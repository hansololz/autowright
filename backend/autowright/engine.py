"""Execution engine (§6, §7): executes an automation's steps as subprocesses,
streams status/logs, enforces policies, persists everything file-first."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import harness, keychain, listeners, notify, packages as pkglib, paths, platform, timefmt
from .events import hub
from .executor import CTRL
from .firing import finish_queued
from .storage import (DRAFT_MEM_STAGE_PREFIX, SECRET_REF_RE, Store,
                      clamp_max_parallel, exec_version_label, new_id,
                      resolve_param_value, trigger_label)

log = logging.getLogger("autowright.engine")

STEP_TIMEOUT = 15 * 60  # per-step hard cap (seconds); override via AUTOWRIGHT_STEP_TIMEOUT


def _step_timeout() -> float:
    try:
        return float(os.environ.get("AUTOWRIGHT_STEP_TIMEOUT", "") or STEP_TIMEOUT)
    except ValueError:
        return STEP_TIMEOUT


def step_timeout_for(s: dict) -> float | None:
    """§6: the step's own manifest `timeout` wins; `no_timeout: true` disables
    the watchdog entirely (None); absent falls back to the default above."""
    if s.get("no_timeout"):
        return None
    t = s.get("timeout")
    if isinstance(t, (int, float)) and not isinstance(t, bool) and t > 0:
        return float(t)
    return _step_timeout()


MAX_ATTEMPTS = 20  # §4.5: attempts retained per step — older ones prune with their log files


def step_retries_for(s: dict) -> int:
    """§4.1/§7: the step's automatic retry budget per execution pass — 0 when
    absent (§8 validation caps the manifest field at 10)."""
    r = s.get("retries")
    if isinstance(r, (int, float)) and not isinstance(r, bool) and r > 0:
        return int(r)
    return 0


def step_retries_forever(s: dict) -> bool:
    return bool(s.get("infinite_retries"))


def _retry_pause_s() -> float:
    try:
        return float(os.environ.get("AUTOWRIGHT_STEP_RETRY_PAUSE_S", "") or 1.0)
    except ValueError:
        return 1.0


def _next_attempt_n(step: dict) -> int:
    """§4.5: `number` is monotonic per step — the prune drops old entries, so the
    list length can't number the next attempt."""
    atts = step.get("attempts") or []
    return (atts[-1]["number"] + 1) if atts else 1


# §7 failure diagnostics: exception types that read as "the network failed".
_NET_TYPES = {
    "ConnectionError", "ConnectionRefusedError", "ConnectionResetError",
    "ConnectError", "ConnectTimeout", "ReadTimeout", "Timeout", "TimeoutError",
    "timeout", "gaierror", "URLError", "NewConnectionError", "MaxRetryError",
    "SSLError", "ProxyError", "ChunkedEncodingError", "RemoteDisconnected",
}


def failure_reason(rc: int, err: dict | None) -> str | None:
    """Classify a failed step into a plain-word possible reason (§7) —
    deterministic, from exit code + the executor's structured error event;
    None when the failure fits no known category."""
    t = (err or {}).get("type") or ""
    m = (err or {}).get("message") or ""
    # Exit codes 3/4 only mean secret/import policy when the executor's own
    # structured event says so — a script calling sys.exit(3) is just a script
    # exiting 3, not a secret failure.
    if rc == 4 and t == "DisallowedImport":
        return "The step imports a package outside the allowed list."
    if rc == 3 and t == "MissingSecret":
        if "wasn't injected into this step" in m:
            return "The step reads a secret it doesn't declare — add it to the step's secrets list."
        return "The script references a secret that doesn't exist."
    if t == "AgentCallError":
        return "The step's agent call failed — the agent may be unreachable or misconfigured."
    if (t in ("HTTPError", "HTTPStatusError") or "Client Error" in m or "Server Error" in m
            or "HTTP Error" in m):  # urllib spells 4xx/5xx as "HTTP Error nnn: …"
        code = re.search(r"\b([45]\d\d)\b", m)
        return (f"The site answered with an error (HTTP {code.group(1)})." if code
                else "The site answered with an error.")
    if t in _NET_TYPES or "couldn't fetch" in m or "robots.txt disallows" in m:
        return "A network request failed — the site may be down, blocking, or unreachable."
    if t in ("KeyError", "IndexError", "AttributeError"):
        return "The data didn't have the expected shape — a page or file layout may have changed."
    return None


def _step_sha(s: dict) -> str:
    return hashlib.sha256((s.get("code") or "").encode()).hexdigest()[:16]


def build_redactions(secret_values: dict[str, str]) -> dict[str, str]:
    """value → secret name, plus each non-blank line of a multi-line value
    (§4.8: log lines are redacted one at a time, so a partial paste of a
    multi-line key must match too). Shared by executions and §11 tests."""
    redactions = {v: k for k, v in secret_values.items()}
    for name, v in secret_values.items():
        if "\n" in v:
            for part in v.splitlines():
                if part.strip():
                    redactions.setdefault(part, name)
    return redactions


def agents_for_step(agents: dict[str, dict], enabled: list, s: dict) -> list[dict]:
    """§6: the step's listed agent ids (§4.1 entries) resolved against the
    enabled agents, in the step's order — a rename can never repoint a step.
    The first-enabled-agent fallback
    applies only when the step lists no agents at all — listed agents that all
    fail to resolve (revoked grants, deleted agents) return [] so the caller
    fails the step like an agent step with no enabled agent, never a silent
    hand-off to an agent the step didn't list. Shared by executions and §11
    tests."""
    pool = {a: agents[a] for a in enabled if a in agents}
    entries = s.get("agents") or []
    if not entries:
        return list(pool.values())[:1]
    return [pool[e["id"]] for e in entries if e.get("id") in pool]


def ensure_declared_packages(declared: list, log, should_stop=None) -> str | None:
    """§6.2 preflight shared by executions and §11 tests: fast installed-check,
    install what's missing (with a sys log line), return an error message on
    failure — None when everything is (now) installed. `should_stop` lets a
    cancel land between per-package pip runs (§7: cancel kills processes)."""
    if not declared:
        return None
    missing = [p for p in pkglib.check(declared) if p["status"] != "installed"]
    if not missing:
        return None
    log("sys", "installing packages: " + ", ".join(p["pip"] for p in missing))
    bad = [p for p in pkglib.ensure(declared, should_stop=should_stop)
           if p["status"] != "installed"]
    if bad:
        return "; ".join(f"{p['pip']}: {p.get('error') or 'install failed'}" for p in bad)
    return None


def _close_pipe(f) -> None:
    """Close one of a step process's pipes, tolerating an already-closed or
    already-broken one. Closing is never optional: an fd left to the garbage
    collector accumulates across the steps of a long-lived backend."""
    if f is None:
        return
    try:
        f.close()
    except (OSError, ValueError):
        pass


def _processes():
    """§2 platform layer process-group control — POSIX-shaped, with the
    Windows tree-kill mapping behind the same Protocol (platform/)."""
    return platform.current().processes


def kill_step_group(proc: subprocess.Popen, sig: int | None = None) -> None:
    """Signal a step's whole process group (it runs in its own session).
    `sig=None` means kill hard (SIGKILL, not importable on Windows)."""
    _processes().signal_group(proc, sig)


def kill_orphan_group(pgid: int) -> None:
    """§3 startup recovery: SIGKILL a step process group orphaned by a previous
    backend process (its Popen died with that process, so only the persisted
    pgid remains). Pid-reuse guard: only signals when the group still contains
    an `autowright.executor` process — a recycled pgid must never take down an
    unrelated process."""
    procs = _processes()
    if not procs.group_has_command(pgid, "autowright.executor"):
        return
    procs.kill_group(pgid)


def kill_orphan_agent_group(pgid: int) -> None:
    """§3 startup recovery for a §6.1 runtime agent call's own-session group
    (§4.5 agentPgids) orphaned by a crashed backend. Same pid-reuse rule as
    `kill_orphan_group`, with the harness CLI binaries as the markers — a
    recycled pgid must never take down an unrelated process."""
    procs = _processes()
    if not any(procs.group_has_command(pgid, m)
               for m in ("claude", "codex", "gemini", "opencode")):
        return
    procs.kill_group(pgid)


def run_step_process(script: Path, ctx: dict, state: dict, log, result: dict,
                     holder: dict, timeout_s: float | None,
                     on_reply=None, step_i: int | None = None) -> int:
    """One step as a §6.1 executor subprocess — shared by real executions and
    §11 tests. Streams control lines: `log(k, text)` gets every log line,
    `result` collects §4.5 result ops, `holder` gets error/notify/result_touched,
    `on_reply(text)` gets each §6.1 reply() call (None → replies are dropped).
    `state['proc']` holds the live Popen so a caller can cancel. `timeout_s`
    is the resolved §6 step limit (`step_timeout_for`); None = no watchdog."""
    proc = subprocess.Popen(
        # §2 console-interpreter rule: never pythonw — with the hidden-console
        # spawn policy below, a step's own console children (tool calls)
        # inherit an invisible console instead of each opening a terminal
        # window under the §3 pythonw service.
        [paths.console_python(), "-m", "autowright.executor", str(script)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        # §2 pipe-encoding contract: explicit UTF-8, never the locale codec;
        # errors="replace" so binary garbage on stdout can't kill the read loop.
        encoding="utf-8", errors="replace",
        # §6.1: fallback bin dirs appended to PATH, so a step's system-CLI
        # calls and shutil.which pre-flights resolve under a Dock launch's
        # minimal GUI PATH just like under a terminal launch.
        env=harness.step_env(),
        # Own session (§2 platform session policy): timeout/cancel/skip kill
        # the whole group. Killing only the executor leaves its children
        # (Playwright browsers, step subprocesses) alive — they hold the
        # stdout write end open, the read loop below never sees EOF, and the
        # engine thread hangs forever with the automation stuck "executing".
        **_processes().session_kwargs(),
    )
    state["proc"] = proc
    # §3 orphan recovery: hand the new group id (own session → pgid == pid) to
    # the engine so it lands on the persisted execution record.
    if state.get("on_spawn"):
        state["on_spawn"](proc.pid)
    # Watchdog enforces the per-step timeout even when the step produces no
    # output at all (a bare read loop would block forever on a silent hang).
    # No watchdog at all on a no_timeout step (§6) — cancel/skip still kill it.
    timed_out = threading.Event()
    pipe_closed = threading.Event()

    def _hard_kill() -> None:
        """SIGKILL the group and defuse our read end — §7 kill semantics:
        children can never hold the log pipe open past the kill. Shared by the
        timeout watchdog and the cancel/skip escalation (a step that ignores
        SIGTERM must not strand the execution \"executing\" forever). The read
        end is dup2'd over, never `.close()`d cross-thread: close() takes the
        buffer lock a blocked readline holds and would wedge this thread."""
        pipe_closed.set()
        if proc.poll() is None:
            kill_step_group(proc)
        # §7: any in-flight agent call's own-session group dies with the step —
        # its watchdog lived in the executor this kill just took down.
        procs = _processes()
        for g in list(state.get("agent_pgids") or ()):
            try:
                procs.kill_group(g)
            except Exception:  # noqa: BLE001 — an already-gone group is fine
                pass
        harness.defuse_read_end(proc.stdout)

    state["hard_kill"] = _hard_kill

    # Cancel/skip racing the spawn (mirrors harness._invoke): one that landed
    # after the caller's loop-top check but before this Popen existed killed
    # nothing — with no_timeout the freshly spawned step would then run
    # unbounded while the record shows "executing". Re-check now that the
    # proc is visible. Index-compared like every other skip check: a stale
    # flag armed in the previous step's teardown window (after its pop,
    # before `_cur` cleared) must not kill THIS step and report it failed.
    if state.get("cancel") or (state.get("skip") is not None and state.get("skip") == step_i):
        _hard_kill()

    def _on_timeout() -> None:
        timed_out.set()
        _hard_kill()

    # §6: armed BEFORE the ctx handoff below — the deadline runs from process
    # spawn, so a child that wedges before ever reading its stdin (interpreter
    # hung on startup, pipe never drained) still hits its limit. If the timer
    # fires while we're blocked in stdin.write, _hard_kill SIGKILLs the group
    # and the write surfaces as BrokenPipeError, caught below.
    watchdog = None
    if timeout_s is not None:
        watchdog = threading.Timer(timeout_s, _on_timeout)
        watchdog.daemon = True
        watchdog.start()
    try:
        try:
            proc.stdin.write(json.dumps(ctx))
        except OSError:  # BrokenPipeError: the group died before it read ctx
            pass
        _close_pipe(proc.stdin)  # closed on every path, so no fd is left open
        try:
            while True:
                # Size-capped readline: a step (or an inherited-fd child)
                # streaming newline-free data must not balloon backend memory
                # for the whole step timeout — an over-long line arrives as
                # bounded chunks instead. CTRL lines stay far under the cap
                # (agent prompt/reply are 200k-char capped upstream).
                raw = proc.stdout.readline(2_000_000)  # type: ignore[union-attr]
                if raw == "":
                    break
                if timed_out.is_set():
                    break
                line = raw.rstrip("\n")
                if line.startswith(CTRL):
                    try:
                        msg = json.loads(line[len(CTRL):])
                    except ValueError:
                        continue
                    op = msg.get("op")
                    if op == "log":
                        log(msg.get("kind", "out"), msg.get("text", ""))
                    elif op == "result":
                        holder["result_touched"] = True
                        f, v = msg.get("field"), msg.get("value")
                        if f == "status":
                            result["status"] = v
                        elif f == "chip":
                            result["chip"] = v
                    elif op == "notify":
                        holder["text"] = msg.get("text")
                    elif op == "reply":
                        # §6.1 reply(): routed engine-side so the bot token
                        # never enters the step process; fire-and-forget.
                        if on_reply:
                            on_reply(msg.get("text", ""))
                    elif op == "error":
                        # §7 failure diagnostics — the executor's structured report
                        # of the exception that failed the step.
                        holder["error"] = {"type": msg.get("type"),
                                           "message": msg.get("message")}
                    elif op == "agent_audit":
                        # §6: the FULL redacted prompt/response go to logs for audit
                        # (the 200k prompt/reply size caps already apply upstream).
                        log("sys", f"agent prompt: {msg.get('prompt', '')}")
                        log("sys", f"agent reply: {msg.get('reply', '')}")
                    elif op in ("agent_group", "agent_group_done"):
                        # §7 kill semantics: a runtime agent call's harness CLI
                        # runs in its own session — track its group while the
                        # call is in flight so kill paths and §3 recovery can
                        # reach it (the step-group signal can't).
                        g = msg.get("pgid")
                        if isinstance(g, int) and not isinstance(g, bool):
                            groups = state.setdefault("agent_pgids", set())
                            if op == "agent_group":
                                groups.add(g)
                            else:
                                groups.discard(g)
                            persist = state.get("on_agent_groups")
                            if persist:
                                persist(sorted(groups))
                elif line.strip():
                    log("out", line)
        except ValueError:
            # The watchdog (or the cancel/skip escalation) closed our read end
            # after its kill — the loop is done; anything else is a real error.
            if not timed_out.is_set() and not pipe_closed.is_set():
                raise
        proc.wait()
    finally:
        # Always cancel the timer and drop the proc handle — even if the read
        # loop raises — so the watchdog can't later kill an unrelated process.
        if watchdog is not None:
            watchdog.cancel()
        if proc.poll() is None:
            # The read loop died mid-stream (e.g. a disk-full error while
            # persisting a log line) — never leave the group alive with no
            # handle to cancel it by.
            kill_step_group(proc)
            _close_pipe(proc.stdout)
            try:
                proc.wait(timeout=5)
            except (subprocess.TimeoutExpired, OSError):
                pass
        # Both pipes close on EVERY path, not just the still-alive one: a normal
        # EOF exit used to leave the read end open until the garbage collector
        # got to it, leaking an fd per step on a long-lived backend.
        _close_pipe(proc.stdout)
        _close_pipe(proc.stdin)
        state["proc"] = None
        state.pop("hard_kill", None)
        # §7: the executor is gone, so its agent calls are over — a done event
        # that never arrived (killed mid-call) must not leave stale group ids
        # for a later step's kill to re-signal (pid-reuse hazard).
        if state.pop("agent_pgids", None):
            persist = state.get("on_agent_groups")
            if persist:
                persist([])
    if timed_out.is_set() and proc.returncode != 0:
        msg = f"step timed out after {int(timeout_s)}s"
        log("err", msg)
        holder["error"] = {"type": "StepTimeout", "message": msg,
                           "reason": f"The step hit its {int(timeout_s)} s time limit."}
        return proc.returncode or 1
    return proc.returncode or 0


class Engine:
    def __init__(self, store: Store):
        self.store = store
        self._live: dict[str, dict] = {}  # execution_id → {proc, cancel, thread}
        self._lock = threading.Lock()
        self.drain_queue = None  # set by the scheduler (§6 firing queue)

    # ---------- public ----------
    @staticmethod
    def at_capacity(auto: dict) -> bool:
        """§6: every `maxParallel` slot taken. Lowering the setting below the
        number already running is allowed — it just admits nothing new until the
        automation is back under the limit, and never kills a live execution."""
        return len(auto.get("_live") or ()) >= clamp_max_parallel(auto.get("max_parallel"))

    def start(self, auto: dict, trigger: str, version_label: str | None = None,
              payload: dict | None = None, adopt: dict | None = None) -> dict:
        """Create the execution record and execute it on a worker thread (§7).
        The §6 capacity check and the record creation happen under one lock — two
        concurrent starters can never both pass into the same slot.
        `adopt` promotes an existing §6 queued record instead of creating one, so
        a queued firing produces exactly one record from admission to finish.
        The §6.3 pre-version snapshot is only *decided* here - the copy runs on
        the worker thread, before step 1, because a memory dir can be gigabytes
        and no copytree may run under store.lock."""
        with self.store.lock:
            # §19 delete: an admission racing the DELETE window (the automation
            # is still registered while its live executions are cancelled and
            # awaited) must not start — it would escape the delete's wait set
            # and re-create the tree after the rmtree.
            if auto.get("_deleting"):
                raise RuntimeError("this automation is being deleted")
            # §4.5: the record stores (kind, version) — the §19 label is parsed
            # here at the boundary and never kept. Resolution runs BEFORE the
            # capacity check: §19 says an unresolvable label answers 404
            # either way, and the queued path already resolves first.
            kind, version = self._parse_version_label(auto, version_label)
            ver = self._resolve_version(auto, kind, version)
            if ver is None:
                raise LookupError(f"version {version_label or f'v{version}'} not found")
            if self.at_capacity(auto):
                raise RuntimeError("already executing")
            pre_snapshot = self._needs_pre_version(auto, kind, version)
            # `sha` snapshots each step's script (§4.5) so a Draft retry can
            # detect a re-saved draft whose code changed under the same names.
            steps = [{"name": s["name"], "file": s.get("file"), "agent": bool(s.get("agent")),
                      "sha": _step_sha(s), "status": "queued", "duration_ms": None, "attempts": []}
                     for s in ver["steps"]]
            # §7: snapshot the resolved param values — the execution page shows them as used by this execution.
            params = self.store.merged_params(auto, ver)
            if adopt is not None:
                h = self.store.promote_execution(adopt, steps, params=params)
            else:
                h = self.store.create_execution(auto, kind, version, trigger, steps,
                                                params=params,
                                                trigger_payload=payload)
            if pre_snapshot:
                # In-memory only (like `_test`): write_exec_yaml persists a
                # fixed key set, so this never reaches disk.
                h["_pre_snapshot"] = f"v{version}"
            return self._launch(auto, ver, h)

    @staticmethod
    def _parse_version_label(auto: dict, label: str | None) -> tuple[str, int | None]:
        """§19 `version` body field → the §4.5 stored (kind, version) pair.
        Unparsable labels resolve to a version no automation has, so the
        caller's `_resolve_version` miss answers the 404."""
        if label and label.lower() == "draft":
            return "draft", None
        if not label:
            return "version", auto["current_version"]
        try:
            return "version", int(label.lower().lstrip("v"))
        except ValueError:
            return "version", -1

    def _needs_pre_version(self, auto: dict, kind: str, version: int | None) -> bool:
        """§6.3 pre-version snapshot: first execution of a real version with no
        recorded execution yet — memory as the previous version left it,
        restorable after rollback. Records that never ran (§4.1) must not
        suppress the snapshot. Decided under the admission lock, in the same
        critical section that creates the `executing` record, so two parallel
        first-executions of a version can never both answer True; the copy
        itself happens on the worker thread (`_take_pre_version`)."""
        if kind != "version":
            return False
        if any(x["automation_id"] == auto["id"] and x.get("kind") == "version"
               and x.get("version") == version
               and not self.store.never_ran(x)
               for x in self.store.execs.values()):
            return False
        # Belt and braces after a retention sweep removed the version's records.
        return not self.store.pre_version_snapshot_exists(auto, f"v{version}")

    def _seed_draft_memory(self, auto: dict) -> bool:
        """§4.4: the first Draft execution seeds draft/memory as a copy of the
        live memory; later Draft executions (and draft re-saves) reuse it.
        "Never written" is the test (exists-and-empty counts — §19 /draft/open
        pre-creates an empty memory/). Staged outside store.lock and swapped in
        by rename under it, so two first executions racing under maxParallel > 1
        still produce exactly one seeded dir. True when a copy was made."""
        dmem = self._memory_dir(auto, "draft")
        live_mem = self.store.auto_dir(auto) / "memory"

        def _seeded() -> bool:
            return dmem.exists() and any(dmem.iterdir())

        with self.store.lock:
            if _seeded():
                return False
            if not (live_mem.exists() and any(live_mem.iterdir())):
                dmem.mkdir(parents=True, exist_ok=True)
                return False
            staging = dmem.parent / f"{DRAFT_MEM_STAGE_PREFIX}{new_id()}"
        try:
            shutil.copytree(live_mem, staging)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        with self.store.lock:
            if _seeded():
                shutil.rmtree(staging, ignore_errors=True)  # lost the race
                return False
            if dmem.exists():
                dmem.rmdir()  # empty by the check above
            staging.rename(dmem)
        return True

    def _take_pre_version(self, auto: dict, h: dict) -> None:
        """§6.3: the snapshot `start` decided on, taken before step 1 - staged
        outside store.lock (a memory dir can be gigabytes) and renamed into
        place under it."""
        label = h.pop("_pre_snapshot", None)
        if not label:
            return
        staged = self.store.stage_snapshot(auto, "pre-version")
        if staged is not None:
            self.store.commit_snapshot(auto, staged, "pre-version", version=label)

    def retry(self, auto: dict, h: dict) -> dict:
        """§7 in-place retry: the same execution record re-executes from the
        failed step as a new attempt; succeeded/skipped steps are untouched."""
        with self.store.lock:
            # Re-read under the lock: the caller's header may be the stale
            # object a first retry already replaced in store.execs — checking
            # its status would let two concurrent retries both pass and launch
            # two engine threads on the same record.
            h = self.store.execs.get(h["id"]) or h
            if self.is_live(h["id"]):
                raise RuntimeError("already executing")
            if self.at_capacity(auto):
                raise RuntimeError("already executing")
            if h["status"] != "failed":
                raise RuntimeError("only failed executions can be retried")
            ver = self._resolve_version(auto, h["kind"], h.get("version"))
            if ver is None:
                raise LookupError(f"version {exec_version_label(h)} not found")
            full = self.store.exec_full(h["id"])
            if full is None:
                raise LookupError("execution not found")
            h = full
            # A Draft is mutable: a re-saved draft may no longer match the failed
            # record's steps — re-entering the loop would pair old statuses with
            # new scripts. Real versions are immutable, so only Draft can drift.
            # Compare code hashes too: an edit can keep the same names/files.
            if [(s["name"], s.get("file"), s.get("sha")) for s in h["steps"]] != \
                    [(s["name"], s.get("file"), _step_sha(s)) for s in ver["steps"]]:
                raise RuntimeError("the draft's steps changed since this execution — execute it fresh instead")
            self.store.execs[h["id"]] = h  # the live in-memory record is the full one
            for s in h["steps"]:
                if s["status"] == "failed":
                    s["status"] = "queued"
            h["status"] = "executing"
            h["finished_at"] = None
            h["error"] = None
            h["chip"] = None
            h["chip_status"] = None
            idx = next((i for i, s in enumerate(h["steps"]) if s["status"] == "queued"), None)
            if idx is not None:
                n = _next_attempt_n(h["steps"][idx])
                self._log(h, "sys", f"retrying from step {idx + 1} — attempt {n}", {})
            self.store.update_execution(h)
            return self._launch(auto, ver, h)

    def _launch(self, auto: dict, ver: dict, h: dict) -> dict:
        def _on_spawn(pgid: int) -> None:
            # §3 orphan recovery: persist the live step's group id so a
            # restarted backend can kill whatever this process leaves behind.
            with self.store.lock:
                h["pgid"] = pgid
                self.store.update_execution(h)

        def _on_agent_groups(groups: list[int]) -> None:
            # §4.5 agentPgids: persist the in-flight agent-call groups so §3
            # recovery can sweep one a crashed backend orphaned.
            with self.store.lock:
                h["agent_pgids"] = groups
                self.store.update_execution(h)

        state = {"proc": None, "cancel": False, "on_spawn": _on_spawn,
                 "on_agent_groups": _on_agent_groups}
        t = threading.Thread(target=self._execute, args=(auto, ver, h, state), daemon=True)
        state["thread"] = t
        with self._lock:
            self._live[h["id"]] = state
        with self.store.lock:
            # §19: the started event carries both rows so clients patch in
            # place (list entry + the automation's live/lastStatus chip)
            # instead of re-fetching /state.
            hub.publish("execution.started", executionId=h["id"], automationId=auto["id"],
                        execution=self.store.exec_json(h),
                        automation=self.store.auto_json(auto, full=False))
        try:
            t.start()
        except BaseException as e:
            # Thread exhaustion is rare but must not strand the record
            # "executing" with no thread — it would pin a §6 slot until a
            # backend restart and 409 every later firing.
            with self._lock:
                self._live.pop(h["id"], None)
            with self.store.lock:
                h["status"] = "failed"
                h["finished_at"] = timefmt.now_iso()
                if not h.get("error"):
                    h["error"] = {"step": None,
                                  "message": f"the execution thread couldn't start: {e}",
                                  "reason": None}
                self.store.update_execution(h)
                a = self.store.autos.get(h["automation_id"])
                if a:
                    a["_live"].discard(h["id"])
                hub.publish("execution.finished", executionId=h["id"],
                            automationId=h["automation_id"],
                            execution=self.store.exec_json(h),
                            automation=self.store.auto_json(a, full=False) if a else None)
            raise
        return h

    KILL_GRACE = 5.0  # seconds between the polite SIGTERM and the group SIGKILL

    @classmethod
    def _term_then_kill(cls, proc: subprocess.Popen, hard_kill) -> None:
        """§7 kill semantics for cancel/skip: SIGTERM first (steps get a chance
        to clean up), then the step's own hard-kill after a grace period — a
        step that traps SIGTERM must not strand the execution \"executing\"
        forever (`hard_kill` also closes the log pipe, so even an escaped
        child can't hold the read loop open)."""
        kill_step_group(proc, signal.SIGTERM)
        if hard_kill is None:
            return
        t = threading.Timer(cls.KILL_GRACE, hard_kill)
        t.daemon = True
        t.start()

    def cancel(self, execution_id: str) -> bool:
        """§7 cancel for a running execution; §6 queue-leave for a waiting one.
        An entry promoted between the user's click and this call is cancelled
        by exactly one of the two branches — never both, never neither:
        `finish_queued` reports whether it won the transition, and a loss means
        the entry is executing now (promotion registers `_live` under
        `store.lock` before releasing it), so the loop retries as live."""
        while True:
            with self.store.lock:
                h = self.store.execs.get(execution_id)
                queued = h is not None and h["status"] == "queued"
                if not queued:
                    with self._lock:
                        state = self._live.get(execution_id)
            if queued:
                # Outside store.lock: finish_queued replies to the §6.1 sender,
                # and a network send must never run under the store lock.
                if finish_queued(self.store, h, "cancelled before it ran"):
                    return True
                continue  # promoted in the gap — cancel it as a live execution
            break
        if not state:
            return False
        state["cancel"] = True
        proc = state.get("proc")
        if proc and proc.poll() is None:
            self._term_then_kill(proc, state.get("hard_kill"))
        return True

    def skip_step(self, execution_id: str, index: int) -> bool:
        """§7 skip: kill the currently executing step and continue with the
        next one. False unless `index` is the step executing right now."""
        with self._lock:
            state = self._live.get(execution_id)
            if not state:
                return False
            h = self.store.execs.get(execution_id)
            cur = (h or {}).get("_cur")
            if not cur or cur["i"] != index:
                return False
            state["skip"] = index
            proc = state.get("proc")
            hard = state.get("hard_kill")
        if proc and proc.poll() is None:
            self._term_then_kill(proc, hard)
        return True

    def is_live(self, execution_id: str) -> bool:
        with self._lock:
            return execution_id in self._live

    def wait_finished(self, exec_ids, timeout: float = KILL_GRACE + 6.0) -> bool:
        """Block until each execution's engine thread has exited — its step
        group is dead and the record finalized. §19 delete waits on this so
        the rmtree can't race a step still dying in the SIGTERM grace window.
        True when every thread is gone within `timeout` (shared deadline)."""
        deadline = time.monotonic() + timeout
        for eid in exec_ids:
            with self._lock:
                t = (self._live.get(eid) or {}).get("thread")
            if t is None:
                continue
            t.join(max(0.0, deadline - time.monotonic()))
            if t.is_alive():
                return False
        return True

    def kill_all_live(self) -> None:
        """§3 backend shutdown: hard-kill every live step group. The records
        get marked interrupted by the next startup's recovery either way — but
        their step processes must die with this backend, or an orphan keeps
        writing `memory/` while the successor starts a second copy."""
        with self._lock:
            states = list(self._live.values())
        for state in states:
            state["cancel"] = True
            proc = state.get("proc")
            hard = state.get("hard_kill")
            if hard:
                hard()
            elif proc is not None and proc.poll() is None:
                kill_step_group(proc)

    # ---------- internals ----------
    def _resolve_version(self, auto: dict, kind: str, version: int | None) -> dict | None:
        if kind == "draft":
            return auto.get("draft")
        return auto["versions"].get(version)

    def _version_dir(self, auto: dict, kind: str, version: int | None) -> Path:
        base = self.store.auto_dir(auto)
        return base / "draft" / "automation" if kind == "draft" else base / "versions" / f"v{version}"

    def _memory_dir(self, auto: dict, kind: str) -> Path:
        """§4.4: Draft executions get the draft's own memory — the live dir is
        never handed to a draft step."""
        base = self.store.auto_dir(auto)
        return base / "draft" / "memory" if kind == "draft" else base / "memory"

    def _redact(self, h: dict, text: str, redactions: dict[str, str]) -> str:
        for val, name in redactions.items():
            if val and val in text:
                text = text.replace(val, "•••")
                if name not in h["redacted_secrets"]:
                    h["redacted_secrets"].append(name)
        return text

    def _log(self, h: dict, kind: str, text: str, redactions: dict[str, str]) -> None:
        text = self._redact(h, text, redactions)
        cur = h.get("_cur")
        name = cur["log"] if cur else self.store.EXEC_LOG
        # Per-file monotonic seq (§5) — resumed by counting existing lines, so a
        # retried execution's execution.ndjson keeps a gapless sequence.
        seqs = h.setdefault("_log_seq", {})
        if name not in seqs:
            p = self.store.log_file(h["id"], name)
            seqs[name] = sum(1 for _ in p.open(encoding="utf-8")) if p.exists() else 0
        seqs[name] += 1
        # On-disk shape (§5): {timestamp, kind, sequence, text} — the owning step/attempt is
        # implicit in the filename. The serialized/UI shape adds the derived
        # local clock label `time` (read_log for files, here for the live event).
        line = {"timestamp": timefmt.now_iso(), "kind": kind, "sequence": seqs[name], "text": text}
        # Publish only lines the store accepted — past the §5 cap the live
        # pane must match the stored log, and a runaway step must not keep
        # queuing loop callbacks for lines that exist nowhere.
        if self.store.append_log_line(h["id"], name, line):
            hub.publish("execution.log", executionId=h["id"], automationId=h["automation_id"],
                        stepIndex=cur["i"] if cur else None,
                        attempt=cur["number"] if cur else None,
                        line={"time": datetime.now().strftime("%H:%M:%S"), "kind": kind,
                              "sequence": line["sequence"], "text": text})

    def _prune_attempts(self, h: dict, step: dict, i: int) -> None:
        """§4.5: keep the newest MAX_ATTEMPTS attempts — an infiniteRetries step
        must not grow the record and the logs dir without bound. The pruned
        attempt's log file goes with it."""
        atts = step["attempts"]
        while len(atts) > MAX_ATTEMPTS:
            old = atts.pop(0)
            name = self.store.log_name(step.get("file"), i, old["number"])
            try:
                self.store.log_file(h["id"], name).unlink(missing_ok=True)
            except OSError:
                pass
            h.get("_log_seq", {}).pop(name, None)
            # The store's per-file line count is keyed the same way — an
            # infiniteRetries step would otherwise leak one key per prune.
            with self.store.lock:
                self.store._log_counts.pop((h["id"], name), None)

    @staticmethod
    def _await_retry(state: dict, i: int, forever: bool) -> None:
        """§7: consecutive attempts of an infiniteRetries step are spaced
        ≥ AUTOWRIGHT_STEP_RETRY_PAUSE_S apart (default 1 s) — a script that
        crashes on startup must not hot-loop process spawns. Finite retries run
        back-to-back. Cancel and skip cut the wait short."""
        if not forever:
            return
        end = time.time() + _retry_pause_s()
        while time.time() < end:
            if state["cancel"] or state.get("skip") == i:
                return
            time.sleep(0.05)

    def _step_event(self, h: dict, i: int) -> None:
        self.store.update_execution(h)
        s = h["steps"][i]
        from .timefmt import dur_label

        hub.publish("execution.step", executionId=h["id"], automationId=h["automation_id"], index=i,
                    step={"name": s["name"], "status": s["status"],
                          "duration": dur_label(s["duration_ms"]) if s.get("duration_ms") else "",
                          "attempts": self.store.step_attempts_json(s)})

    def _execute(self, auto: dict, ver: dict, h: dict, state: dict) -> None:
        # §4.4: a draft carries its own grant selections — a Draft execution
        # honors them instead of the automation's live grants. Shadow copy only;
        # the stored automation is never touched.
        if ver.get("step_agents") is not None or ver.get("allowed_secrets") is not None:
            auto = {**auto,
                    "enabled_agents": ver["step_agents"] if ver.get("step_agents") is not None
                    else auto["enabled_agents"],
                    "allowed_secrets": ver["allowed_secrets"] if ver.get("allowed_secrets") is not None
                    else auto["allowed_secrets"]}
        state["pass_start"] = time.time()  # §7: duration_ms accumulates across retry passes
        result: dict[str, Any] = {"status": "ok", "chip": None}
        result_touched = False
        notify_text: str | None = None
        # §3 per-execution idle-sleep hold, through the §2 platform layer —
        # the release is called in the finally below. Never raises; a platform
        # with no mechanism holds nothing.
        release_power = platform.current().power.hold_execution()
        redactions: dict[str, str] = {}
        failed = False
        try:
            # §6.3: the pre-version snapshot `start` decided on stands before
            # anything else this execution does.
            self._take_pre_version(auto, h)
            # §6: a missing secret stops the execution before any step —
            # declared (`secrets` entry ids in the manifest) and the
            # code-referenced secrets["<id>"] literals alike. Ids are the
            # binding (§4.8); every message resolves the id to the secret's
            # name — names are the display, never the identity.
            needed: set[str] = set()
            for s in ver["steps"]:
                needed |= set(SECRET_REF_RE.findall(s.get("code", "")))
                needed |= {e["id"] for e in s.get("secrets") or [] if e.get("id")}
            by_id = {x["id"]: x for x in self.store.secrets}
            secret_values: dict[str, str] = {}   # id → value (§6 step scoping keys on ids)
            secret_names: dict[str, str] = {}    # id → name (error copy + redaction labels)
            for sid in sorted(needed):
                sec = by_id.get(sid)
                if sec is None:
                    msg = (f"a step references a secret that no longer exists "
                           f"({sid[:8]}…) — the execution can't start")
                    self._log(h, "err", msg, {})
                    if not h.get("error"):
                        h["error"] = {"step": None, "message": msg,
                                      "reason": f"A step references a secret that no longer exists ({sid[:8]}…)."}
                    failed = True
                elif sid not in auto["allowed_secrets"]:
                    msg = f"secret {sec['name']} isn't allowed for this automation — the execution can't start"
                    self._log(h, "err", msg, {})
                    if not h.get("error"):
                        h["error"] = {"step": None, "message": msg,
                                      "reason": "A step references a secret this automation isn't allowed to use."}
                    failed = True
                else:
                    # §4.8: the Keychain entry is keyed by the secret's id.
                    v = keychain.get_secret(sid)
                    if v is None:
                        # §4.8: a placeholder (set: False) gets the clearer message —
                        # the secret exists, only its value was never added.
                        if not sec.get("set", True):
                            msg = f"secret {sec['name']} has no value yet — add it on the Secrets page"
                            reason = "A step references a secret whose value hasn't been added yet."
                        else:
                            store_name = paths.secret_store_name()  # §9 per-OS copy rule
                            msg = (f"secret {sec['name']} isn't in your {store_name} — "
                                   "the execution can't start")
                            reason = f"A step references a secret that isn't in your {store_name}."
                        self._log(h, "err", msg, {})
                        if not h.get("error"):
                            h["error"] = {"step": None, "message": msg, "reason": reason}
                        failed = True
                    else:
                        secret_values[sid] = v
                        secret_names[sid] = sec["name"]
            # Redaction labels are names (§4.5 redactedSecrets), never ids.
            redactions = build_redactions({secret_names[i]: v for i, v in secret_values.items()})

            # §7: ensure the version's declared packages (§6.2) before step 1 —
            # the fast check costs milliseconds when everything is present;
            # self-heals after an app update or a cleared site-packages dir.
            if not failed:
                msg = ensure_declared_packages(
                    ver.get("packages") or [],
                    lambda k, text: self._log(h, k, text, redactions),
                    should_stop=lambda: state["cancel"])
                if msg and not state["cancel"]:
                    self._log(h, "err", f"package install failed — {msg}", redactions)
                    h["error"] = {"step": None, "message": self._redact(h, msg, redactions),
                                  "reason": "A required package couldn't be installed — check "
                                            "your connection, then execute again or retry from "
                                            "the edit page."}
                    failed = True

            warns: list[str] = []
            params = {p["name"]: resolve_param_value(p, auto["param_values"], warns)
                      for p in ver.get("params", [])}
            for w in warns:
                self._log(h, "wrn", w, redactions)

            if h["kind"] == "draft" and not failed:
                if self._seed_draft_memory(auto):
                    self._log(h, "sys", "draft memory created — copied from the automation's memory", redactions)

            # §11 test executions carry their own script/memory dirs (`_test` is
            # in-memory only — write_exec_yaml persists a fixed key set).
            vdir = (Path(h["_test"]["vdir"]) if h.get("_test")
                    else self._version_dir(auto, h["kind"], h.get("version")))
            for i, s in enumerate(ver["steps"]):
                step = h["steps"][i]
                if step["status"] in ("succeeded", "skipped"):
                    continue  # §7 retry: terminal steps from an earlier pass never re-execute
                if failed or state["cancel"]:
                    step["status"] = "cancelled" if state["cancel"] else "queued"
                    self._step_event(h, i)
                    continue
                forever = step_retries_forever(s)
                budget = step_retries_for(s)
                pass_tries = 0  # §7: automatic re-attempts count per execution pass
                while True:
                    n = _next_attempt_n(step)
                    attempt = {"number": n, "status": "executing",
                               "started_at": timefmt.now_iso(),
                               "duration_ms": None}
                    step["attempts"].append(attempt)
                    self._prune_attempts(h, step, i)
                    step["status"] = "executing"
                    step["duration_ms"] = None
                    h["_cur_step"] = s["name"]  # engine-error fallback for §4.5 error.step
                    h["_cur"] = {"i": i, "number": n,
                                 "log": self.store.log_name(step.get("file"), i, n)}
                    self._step_event(h, i)
                    if pass_tries:
                        self._log(h, "sys",
                                  f"attempt {n - 1} failed — retrying (attempt {n})" if forever
                                  else f"attempt {n - 1} failed — retrying ({pass_tries} of {budget})",
                                  redactions)
                    t0 = time.time()
                    agent_cfgs: list[dict] = []
                    rc = 1
                    notify_holder: dict = {}
                    if s.get("agent"):
                        agent_cfgs = self._agents_for_step(auto, s)
                        if not agent_cfgs:
                            msg = f"Step {i + 1} needs an agent, but none is enabled — the execution fails here."
                            self._log(h, "err", msg, redactions)
                            notify_holder["error"] = {
                                "message": msg,
                                "reason": "No enabled agent can serve this step — enable one for this automation."}
                    if not (s.get("agent") and not agent_cfgs):
                        rc = self._execute_step(auto, ver, h, s, i + 1, vdir, params, secret_values,
                                                secret_names, agent_cfgs, state, redactions,
                                                result, notify_holder)
                    if notify_holder.get("text"):
                        notify_text = notify_holder["text"]
                    if notify_holder.get("result_touched"):
                        result_touched = True
                    dur = int((time.time() - t0) * 1000)
                    step["duration_ms"] = dur
                    attempt["duration_ms"] = dur
                    skip = state.pop("skip", None)
                    if state["cancel"]:
                        status = "cancelled"
                        self._log(h, "sys", "execution cancelled by you — nothing else will happen", redactions)
                    elif rc == 0:
                        status = "succeeded"
                        if skip == i:
                            self._log(h, "sys", "skip arrived after the step finished", redactions)
                    elif skip == i:
                        status = "skipped"
                        self._log(h, "sys", "step skipped by you — continuing with the next step", redactions)
                    else:
                        status = "failed"
                        # §7 failure diagnostics: the executor's structured error
                        # event (or the engine's own, e.g. a timeout) becomes the
                        # execution's error — message redacted like any log line.
                        err = notify_holder.get("error")
                        message = self._redact(h, (err or {}).get("message")
                                               or f"step failed (exit code {rc})", redactions)
                        reason = (err or {}).get("reason") or failure_reason(rc, err)
                        attempt["error"] = {"message": message, "reason": reason}
                        if forever or pass_tries < budget:
                            # §7 step retry: budget left — only the attempt keeps
                            # the error; the execution stays `executing` and never
                            # flickers terminal between attempts.
                            step["status"] = status
                            attempt["status"] = status
                            self._step_event(h, i)
                            pass_tries += 1
                            self._await_retry(state, i, forever)
                            skip = state.pop("skip", None)
                            if state["cancel"]:
                                # §7: cancel wins over the pending retry exactly
                                # as over a running attempt — the step lands
                                # cancelled (the failed attempt keeps its error).
                                # Left `failed`, a last-step cancel would slip
                                # past finalize's cancelled-detection and the
                                # record would finish `succeeded`.
                                step["status"] = "cancelled"
                                self._log(h, "sys",
                                          "execution cancelled by you — nothing else will happen",
                                          redactions)
                                self._step_event(h, i)
                                break
                            if skip == i:
                                # §7: skip wins over a pending retry — the step is
                                # skipped, spending nothing further.
                                step["status"] = "skipped"
                                self._log(h, "sys",
                                          "step skipped by you — continuing with the next step",
                                          redactions)
                                self._step_event(h, i)
                                break
                            continue
                        failed = True
                        h["error"] = {"step": s["name"], "message": message, "reason": reason}
                    step["status"] = status
                    attempt["status"] = status
                    self._step_event(h, i)
                    break
                h["_cur_step"] = None
                h["_cur"] = None
            # ---- finalize ----
            h["_cur_step"] = None
            h["_cur"] = None
            h["duration_ms"] = (h["duration_ms"] or 0) + int((time.time() - state["pass_start"]) * 1000)
            # §7: the cancel flag marks the record cancelled only when it
            # actually reached a step — at least one cancelled or left
            # non-terminal. A cancel landing after the last step already
            # succeeded changes nothing: the status reports what happened to
            # the steps, not that a button was pressed too late.
            cancelled = state["cancel"] and any(
                s["status"] in ("cancelled", "queued", "executing") for s in h["steps"])
            if cancelled:
                h["status"] = "cancelled"
            elif failed:
                h["status"] = "failed"
                self._log(h, "sys", f"execution failed — see the step above", redactions)
            else:
                h["status"] = "succeeded"
            h["finished_at"] = timefmt.now_iso()
            h["pgid"] = None  # §3: no live step group to recover anymore
            h["agent_pgids"] = []
            if result_touched and not cancelled:
                # The chip is optional (§4.5): it lives on the execution header,
                # tinted by the execution's result status. It is persisted and
                # published, so it gets the same redaction a log line gets (§5:
                # secret values never appear in any file). Redacted in place —
                # _notify_end falls back to the chip for the notification body.
                if result["chip"]:
                    result["chip"] = self._redact(h, result["chip"], redactions)
                h["chip"] = result["chip"]
                h["chip_status"] = result["status"] if result["chip"] else None
            self.store.update_execution(h)
            # No OS notification for a cancelled execution (the user did it
            # themselves, seconds ago) or a §11 draft test (editor-scoped —
            # its outcome shows on the Test card).
            if h["status"] != "cancelled" and h.get("kind") != "test" and not h.get("_test"):
                # §6.1: the notification body leaves the app's storage (osascript
                # argv is world-readable, the text persists in Notification
                # Center) — redact it like a log line.
                if notify_text:
                    notify_text = self._redact(h, notify_text, redactions)
                self._notify_end(auto, ver, h, result if result_touched else None, notify_text)
        except Exception as e:  # noqa: BLE001
            # This path must always complete — if the original failure was a
            # disk error, logging/persisting can raise again; swallow those so
            # the finally block still clears the live state (a stuck `_live`
            # would 409 every later start until a backend restart).
            h["status"] = "failed"
            h["finished_at"] = timefmt.now_iso()
            h["pgid"] = None
            h["agent_pgids"] = []
            h["_cur"] = None
            try:
                self._log(h, "err", f"engine error: {e}", redactions)
            except Exception:  # noqa: BLE001
                pass
            if not h.get("error"):
                h["error"] = {"step": h.get("_cur_step"),
                              "message": self._redact(h, f"engine error: {e}", redactions),
                              "reason": None}
            try:
                self.store.update_execution(h)
            except Exception:  # noqa: BLE001
                pass
        finally:
            release_power()
            with self._lock:
                self._live.pop(h["id"], None)
            with self.store.lock:
                # Belt and braces: even if update_execution failed above, the
                # automation must never stay pinned "executing" in memory. A
                # BaseException escape (interpreter shutdown aside) can reach
                # here with the status still "executing" — force it terminal
                # rather than pin the §6 slot until a backend restart.
                if h["status"] == "executing":
                    h["status"] = "interrupted"
                    h["finished_at"] = h.get("finished_at") or timefmt.now_iso()
                    try:
                        self.store.update_execution(h)
                    except Exception:  # noqa: BLE001
                        pass
                a = self.store.autos.get(h["automation_id"])
                if a:
                    a["_live"].discard(h["id"])
                hub.publish("execution.finished", executionId=h["id"], automationId=h["automation_id"],
                            execution=self.store.exec_json(h),
                            automation=self.store.auto_json(a, full=False) if a else None)
            # §6: a slot just freed — hand it to the longest-waiting firing.
            if self.drain_queue:
                try:
                    self.drain_queue(h["automation_id"])
                except Exception:  # noqa: BLE001
                    log.exception("queue drain failed")

    def _agents_for_step(self, auto: dict, s: dict) -> list[dict]:
        agents = {a["id"]: a for a in self.store.agents}
        return agents_for_step(agents, auto["enabled_agents"], s)

    def _execute_step(self, auto: dict, ver: dict, h: dict, s: dict, step_index: int, vdir: Path,
                  params: dict, secret_values: dict, secret_names: dict, agent_cfgs: list[dict],
                  state: dict, redactions: dict, result: dict, notify_holder: dict) -> int:
        script = vdir / (s.get("file") or "")
        if not script.exists():
            msg = f"step script {s.get('file')} is missing"
            self._log(h, "err", msg, redactions)
            notify_holder["error"] = {"type": "MissingScript", "message": msg}
            return 1
        # §6 secret scoping: a step only receives the secrets it declares in the
        # manifest plus those its own source references — all keyed by §4.8 id.
        # The full value map stays
        # engine-side for log redaction; reading an uninjected secret raises in
        # the executor and fails the execution.
        step_refs = set(SECRET_REF_RE.findall(s.get("code", ""))) | {e["id"] for e in s.get("secrets") or [] if e.get("id")}
        step_secrets = {k: v for k, v in secret_values.items() if k in step_refs}
        ctx = {
            "params": params,
            "secrets": step_secrets,
            # §6 prompt scan: ANY of the automation's secret values must fail
            # an agent prompt — a value can reach a later step through
            # workspace/memory, so scanning only the step's own secrets would
            # miss it. Scan-only; secrets["<id>"] access stays step-scoped.
            "scan_secrets": secret_values,
            "allowed_secrets": auto["allowed_secrets"],
            # id → name, for the executor's error copy and log labels — names
            # are the display, ids the identity (§4.8).
            "secret_names": secret_names,
            "site_packages": str(pkglib.site_packages_dir()),
            "package_imports": [p["import"] for p in ver.get("packages") or []],
            "memory_dir": h["_test"]["mem"] if h.get("_test")
            else str(self._memory_dir(auto, h["kind"])),
            "workspace": str(self.store.exec_dir(h["id"]) / "workspace"),
            "result_dir": str(self.store.exec_dir(h["id"]) / "result"),
            "agents": agent_cfgs,
            "is_agent_step": bool(s.get("agent")),
            "agent_timeout": 120,
            "can_reply": (h.get("trigger_payload") or {}).get("kind")
            in ("discord", "imessage"),
            "execution": {
                "automation_id": auto["id"],
                "automation_name": auto["name"],
                "id": h["id"],
                "step_index": step_index,
                "step_name": s["name"],
                # §6.1 SDK contract: the display label ("Manual" / "Cron"),
                # derived from the stored §4.5 kind.
                "trigger": trigger_label(h["trigger"]),
                "trigger_payload": h.get("trigger_payload"),
            },
        }

        def on_reply(text: str) -> None:
            # §6.1: send back through the listener module — a failed send logs
            # an err line and never fails the step.
            payload = h.get("trigger_payload") or {}
            # §6.1 last gate before the network: the executor already refuses a
            # reply carrying a secret value, but this is the point of no return
            # (the text goes to a third party), so re-check here and drop the
            # send rather than leak a Keychain value if that scan is bypassed.
            hit = next((name for val, name in redactions.items() if val and val in text), None)
            if hit:
                self._log(h, "err",
                          f"reply blocked — it contains the value of secret {hit}", redactions)
                return
            # §6.1 fire-and-forget, delivered off-thread: this callback runs on
            # the step's log read loop, and a synchronous send (up to 10 s
            # Discord, 30 s osascript) would stall log streaming while the
            # step's own stdout pipe fills behind it. The shared §6 delivery
            # worker keeps replies and busy notices in one ordered FIFO.
            def deliver() -> None:
                err = listeners.send_reply(payload, text)
                if err:
                    self._log(h, "err", f"reply failed — {err}", redactions)
                else:
                    where = (f"iMessage ({payload.get('chat')})" if payload.get("kind") == "imessage"
                             else f"Discord ({payload.get('channel')})")
                    self._log(h, "sys", f"reply sent to {where}", redactions)

            listeners.submit_send(deliver)

        return run_step_process(script, ctx, state,
                                lambda k, text: self._log(h, k, text, redactions),
                                result, notify_holder, step_timeout_for(s),
                                on_reply=on_reply, step_i=step_index - 1)

    def _notify_end(self, auto: dict, ver: dict, h: dict, result: dict | None,
                    notify_text: str | None) -> None:
        """§6: at most one notification, at the end, per the §4.9 setting."""
        setting = self.store.settings.get("notifications", "attention")
        status = h["status"]
        interesting = (
            status == "failed"
            or (result or {}).get("status") in ("changes", "attention")
        )
        if setting == "all" or interesting:
            body = notify_text or (result or {}).get("chip") or \
                ("Execution failed" if status == "failed" else "Execution finished")
            title_param = None
            for p in ver.get("params", []):
                if p["name"] == "notification_title":
                    title_param = resolve_param_value(p, auto["param_values"]) or None
            notify.post(title_param or auto["name"], body)
