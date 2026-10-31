"""§11 Test (§19 POST /tests): executes the sent draft's steps as a §4.5 test
execution record (kind `test`, trigger kind `test`) through the exact
engine path a real execution takes — record, workspace/result/logs, and the
`exec.*` events all ordinary. Test-specific pieces live here: the sent draft's
scripts land in the record's steps/ dir, memory is a scratch copy discarded at
the end, one test record per draft container (409 while one is live, previous
record deleted at start), and the last-test summary lands in the container's
test.yaml. This module also builds the §8 chat call's RECENT EXECUTIONS context —
failure analysis is an ordinary chat job reading it (there is no separate
analysis call)."""
from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path

from . import keychain, paths, timefmt
from .engine import Engine, _crlf_step_sha, _step_sha, build_redactions, redact_text
from .firing import DATA_PATH_UNREACHABLE
from .events import hub
from .storage import exec_version_label, is_test, safe_step_filename, store
from .yamlio import atomic_write_text, save_yaml

log = logging.getLogger("autowright.testexec")

LOG_TAIL = 40      # lines per step handed to the §8 RECENT EXECUTIONS section
EXECUTIONS_CAP = 5  # §8: newest settled executions included, across all §4.5 kinds
RESULT_EXCERPT = 2000  # chars of result.md shown for a detailed successful execution
# §8 RECENT EXECUTIONS bounds: a log-tail line is clipped to LOG_LINE_CLIP
# characters and the whole section to SECTION_CAP — a step that logs one JSON
# document per line (§6 lines run to 2 MB) must never push the prompt past
# the §8 clip budget.
LOG_LINE_CLIP = 2000
LOG_LINE_CLIP_MARKER = "… [clipped]"
SECTION_CAP = 64 * 1024
BLOCK_CAP = 24_000  # §8: one run's block — its log tails shrink until it fits


def start(engine: Engine, draft: dict, auto: dict | None,
          enabled_agents: list, allowed_secrets: list, param_values: dict,
          trigger_payload: dict | None = None,
          steps_fingerprint: str | None = None) -> str:
    """Create and launch the test execution record; returns its exec id.
    Raises RuntimeError while the container already has a live test (§19 409).

    §19: returns as soon as the record and its live-engine entry exist — both
    created in ONE store.lock hold, so a cancel, draft settle or automation
    delete landing during setup finds the entry (`engine.cancel` marks it)
    instead of missing a test that then runs every step after the draft was
    gone. The step-file writes and the scratch memory copy run on the test
    thread (`_set_up_and_run`), so a large memory folder never stalls the
    request past the client's 30 s timeout."""
    container_id = auto["id"] if auto else None  # §4.5: null automationId on create-mode tests
    with store.lock:
        # §3 shutdown: past the kill sweep nothing new may start — the same
        # refusal `engine.start` makes, so a test lands on the §19 409 instead
        # of spawning a step group with nobody left to collect it.
        if engine._stopping:
            raise RuntimeError("the backend is shutting down")
        # §4.9 degraded session: no record may be created while the relocated
        # data path is unreachable — the same 409 a manual execute gets.
        if store.exec_dir_unreachable:
            raise RuntimeError(DATA_PATH_UNREACHABLE)
        # §19 critical-section rule: a test admitted while the automation is
        # being deleted would escape the delete's wait set and re-create the
        # tree after the rmtree — re-checked here, under the lock.
        if auto is not None and auto.get("_deleting"):
            raise RuntimeError("this automation is being deleted")
        if any(is_test(h) and h["automation_id"] == container_id
               and h["status"] == "executing" for h in store.execs.values()):
            raise RuntimeError("a test is already executing — cancel it or wait for it to finish")
        # §11 keep-latest: one test record per draft container.
        superseded = store.delete_test_execs(container_id) or []

        # Same trust boundary as version writes: client `file` names are
        # sanitized before they hit the record's steps/ directory.
        taken: set[str] = set()
        steps = []
        for i, s in enumerate(draft.get("steps", []), 1):
            f = safe_step_filename(s.get("file"), i, s.get("name"), taken)
            taken.add(f)
            steps.append({**s, "file": f})
        ver = {"steps": steps, "params": draft.get("params", []) or [],
               "packages": draft.get("packages", []) or [],
               "spec": draft.get("spec") or []}
        # §11: in-editor grants and values, never the stored automation's — the
        # engine reads them off this shadow record; the real one is untouched.
        shadow = {
            "id": container_id,
            "name": (auto["name"] if auto else draft.get("name")) or "New automation",
            "enabled_agents": enabled_agents,
            "allowed_secrets": allowed_secrets,
            "param_values": {**(auto["param_values"] if auto else {}), **(param_values or {})},
        }
        rec_steps = [{"name": s.get("name", ""), "file": s["file"],
                      "agent": bool(s.get("agent")), "sha": _step_sha(s),
                      "status": "queued", "duration_ms": None, "attempts": []}
                     for s in steps]
        # §19 triggerMock: the mocked §4.5 payload rides the record like a real
        # firing's — the trigger kind stays `test`, so labels still say "Test".
        h = store.create_execution(shadow, "test", None, "test", rec_steps,
                                   params=store.merged_params(shadow, ver),
                                   trigger_payload=trigger_payload)

        def _on_spawn(pgid: int) -> None:
            # §7: a test run persists `pgid` exactly like a scheduled execution,
            # so §3 orphan recovery can kill its step group after a crash.
            with store.lock:
                h["pgid"] = pgid
                store.update_execution(h)

        def _on_agent_groups(groups: list[int]) -> None:
            # §4.5 agentPgids: the same for an in-flight agent call's own-session
            # group — recovery sweeps a test's agent groups too.
            with store.lock:
                h["agent_pgids"] = groups
                store.update_execution(h)

        state = {"proc": None, "cancel": False, "on_spawn": _on_spawn,
                 "on_agent_groups": _on_agent_groups}
        dbase = (store.auto_dir(auto) / "draft") if auto is not None else paths.pending_draft_dir()
        t = threading.Thread(target=_set_up_and_run,
                             args=(engine, auto, shadow, ver, h, state, dbase, steps_fingerprint),
                             daemon=True)
        # §19 delete waits on `engine.wait_finished`, which finds the thread
        # here - an unregistered one makes that wait a no-op and the rmtree can
        # race a step still dying.
        state["thread"] = t
        # §7/§19: the live-engine entry lands in the SAME lock hold that
        # created the record (and beneath the lock `kill_all_live` flips the
        # stopping flag under), so every cancel path and the shutdown sweep
        # see it from the first instant the record exists.
        with engine._lock:
            engine._live[h["id"]] = state
        # §4.5: test executions never change display state — no automation row.
        hub.publish("execution.started", executionId=h["id"], automationId=container_id,
                    execution=store.exec_json(h), automation=None)
        try:
            # Started under the lock: the thread's first act is a store.lock
            # re-check, so it simply waits for this hold to end.
            t.start()
        except BaseException:
            # A permanent "executing" test record would trip the 409 above
            # forever (delete_test_execs skips executing records).
            with engine._lock:
                engine._live.pop(h["id"], None)
            store.delete_execution(h["id"])
            # §19 execution.deleted: the row the `execution.started` above put
            # in the §7 list has to leave it again.
            hub.publish("execution.deleted", executionId=h["id"])
            for superseded_id in superseded:
                hub.publish("execution.deleted", executionId=superseded_id)
            raise

    # §19 execution.deleted: the superseded test rows leave the §7 list live —
    # published outside the lock.
    for superseded_id in superseded:
        hub.publish("execution.deleted", executionId=superseded_id)
    return h["id"]


def _stopped_before_run(engine: Engine, auto: dict | None, h: dict, state: dict) -> str | None:
    """§19 registration re-check (caller holds store.lock): why a test must
    end before running anything — `"shutdown"` past the §3 kill sweep,
    `"cancelled"` for a pending cancel, a settled draft (`_draft_settled`) or
    an automation being deleted or gone — else None."""
    if engine._stopping or state.get("shutdown"):
        return "shutdown"
    if (state.get("cancel") or h.get("_draft_settled")
            or (auto is not None and (auto.get("_deleting")
                                      or auto["id"] not in store.autos))):
        return "cancelled"
    return None


def _finish_before_run(engine: Engine, h: dict, reason: str) -> None:
    """End a test record that never ran a step — no step files written, no
    memory copied. A cancel finishes `cancelled` ("cancelled before it ran");
    the §3 shutdown sweep finishes `interrupted` with the shutdown line, as
    the engine's own shutdown arm would (§7: a shutdown is not a cancel). A
    record whose draft settled or whose automation is gone is then deleted,
    exactly as `_run` deletes an orphaned test."""
    with store.lock:
        with engine._lock:
            engine._live.pop(h["id"], None)
        status = "interrupted" if reason == "shutdown" else "cancelled"
        h["status"] = status
        h["note"] = ("backend stopped mid-execution" if status == "interrupted"
                     else "cancelled before it ran")
        for step in h["steps"]:
            if step["status"] in ("queued", "executing"):
                step["status"] = status
        h["duration_ms"] = 0
        h["finished_at"] = timefmt.now_iso()
        h["pgid"] = None
        h["agent_pgids"] = []
        store.update_execution(h)
        hub.publish("execution.finished", executionId=h["id"], automationId=h["automation_id"],
                    execution=store.exec_json(h), automation=None)
        orphaned = (h.get("_draft_settled") or h["id"] not in store.execs
                    or (h["automation_id"] is not None
                        and h["automation_id"] not in store.autos))
    if orphaned:
        store.delete_execution(h["id"])
        hub.publish("execution.deleted", executionId=h["id"])


class _CopyAborted(Exception):
    """Raised from the memory copy's `ignore` hook when the test was stopped
    (a cancel or the shutdown sweep) while the copy ran."""


def _copy_stop_check(state: dict):
    """A `shutil.copytree` ignore callback that aborts the copy once
    `state` carries a cancel or shutdown; otherwise it ignores nothing."""
    def check(directory: str, names: list[str]) -> list[str]:
        if state.get("cancel") or state.get("shutdown"):
            raise _CopyAborted()
        return []
    return check


def _set_up_and_run(engine: Engine, auto: dict | None, shadow: dict, ver: dict, h: dict,
                    state: dict, dbase: Path, steps_fingerprint: str | None) -> None:
    """The test thread: re-check at registration, write the sent draft's
    scripts and the scratch memory copy, then execute through `_run`."""
    scratch: Path | None = None
    try:
        with store.lock:
            reason = _stopped_before_run(engine, auto, h, state)
        if reason is not None:
            _finish_before_run(engine, h, reason)
            return

        # The sent draft's scripts, as executed (§5 steps/) — a real version
        # folder serves this role for ordinary executions.
        steps_dir = store.exec_dir(h["id"]) / "steps"
        steps_dir.mkdir(parents=True, exist_ok=True)
        for s in ver["steps"]:
            # §5: written atomically like every other file the app stores — a
            # crash mid-write must never leave a half-written step script the
            # engine would then execute.
            atomic_write_text(steps_dir / s["file"], s.get("code", ""))

        # §19: a cancel, settle or shutdown that landed while the step files
        # were written ends the test here, before the memory copy starts.
        with store.lock:
            reason = _stopped_before_run(engine, auto, h, state)
        if reason is not None:
            _finish_before_run(engine, h, reason)
            return

        # §11 scratch memory: draft container's memory/ when present (edit mode
        # falls back to the automation's), create mode the pending slot's — copied
        # to a temp dir and discarded when the test ends.
        # §11: under the app's own storage, never the OS temp dir — the
        # startup sweep (`clear_scratch`) can then clear what a crash left.
        scratch = paths.tests_scratch_dir() / h["id"]
        scratch.mkdir(parents=True, exist_ok=True)
        mem_dir = scratch / "memory"
        src = dbase / "memory"
        if auto is not None and not src.exists():
            src = store.auto_dir(auto) / "memory"
        if src.exists():
            # §19: the copy re-checks for a stop at each directory it enters,
            # so a cancel during a large memory copy stops it promptly.
            shutil.copytree(src, mem_dir, ignore=_copy_stop_check(state))
        mem_dir.mkdir(parents=True, exist_ok=True)
        (dbase / "test.yaml").unlink(missing_ok=True)  # wiped at each test start (§5)

        # §11 stale-outcome rule: the renderer's opaque steps fingerprint rides
        # the record to the summary write below (`steps_fingerprint`, §5).
        h["_test"] = {"vdir": str(steps_dir), "mem": str(mem_dir), "fp": steps_fingerprint}
        # §7: a setup that straddled the §3 stopping flip runs nothing — the
        # flag is re-read under the lock it flips beneath, and the engine's
        # shutdown arm then finishes the record `interrupted`.
        with store.lock:
            if engine._stopping:
                state["shutdown"] = True
    except _CopyAborted:
        # §19: stopped mid-copy, finished like a stop before the run: the
        # partial scratch copy is discarded.
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
        with store.lock:
            reason = _stopped_before_run(engine, auto, h, state)
        _finish_before_run(engine, h, reason or "cancelled")
        return
    except BaseException:
        # Any setup failure takes the record with it: a permanent "executing"
        # test record trips the §19 409 forever (and delete_test_execs skips
        # executing records), so only a backend restart would ever unblock
        # testing again.
        log.exception("test %s: setup failed", h["id"])
        with engine._lock:
            engine._live.pop(h["id"], None)
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
        store.delete_execution(h["id"])
        # §19 execution.deleted: the row `execution.started` announced leaves
        # the §7 list again.
        hub.publish("execution.deleted", executionId=h["id"])
        return
    # A cancel, settle or shutdown landing during setup is the engine's to
    # observe from here: its step loop never starts a step once the entry is
    # marked, and the §7 shutdown arm finishes the record `interrupted`.
    _run(engine, shadow, ver, h, state, dbase, scratch)


def _run(engine: Engine, shadow: dict, ver: dict, h: dict, state: dict,
         dbase: Path, scratch: Path) -> None:
    try:
        engine._execute(shadow, ver, h, state)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)  # §11: the memory copy is discarded
    # §11 test lifetime: a draft settle that raced this test cancelled and
    # marked it (§19 `_draft_settled`) — or, if the test settled first, already
    # deleted the record. Either way the test leaves nothing behind: no record,
    # and no test.yaml written into the settled container. Checked and written
    # under the lock so a settle can't land between the check and the write.
    orphaned = False
    with store.lock:
        if (h.get("_draft_settled") or h["id"] not in store.execs
                or (h["automation_id"] is not None
                    and h["automation_id"] not in store.autos)):
            # The owner-gone arm is the §19 delete backstop: a test thread that
            # somehow outlived delete's kill grace must not re-create the
            # removed automation directory by writing test.yaml into it.
            orphaned = True
        elif h["status"] in ("succeeded", "failed"):
            # §5 test.yaml — the last-test summary a resumed draft's Test card
            # shows; deleted with the draft. A failed test is NOT analyzed here —
            # analysis is a user-sent §8 chat message reading executions_context.
            summary = {
                "status": h["status"],
                "when": timefmt.now_iso(),
                "execution_id": h["id"],
            }
            fp = (h.get("_test") or {}).get("fp")
            if fp:  # additive, written only when the client sent one (§21)
                summary["steps_fingerprint"] = fp
            save_yaml(dbase / "test.yaml", summary)
    if orphaned:
        store.delete_execution(h["id"])
        # §19 execution.deleted: the orphaned test's row leaves the §7 list
        # live, published for its own id after its execution.finished.
        hub.publish("execution.deleted", executionId=h["id"])


def clear_scratch() -> None:
    """§11 startup sweep: remove whole scratch trees a crashed backend left
    behind (a live test's dir is removed by its own `_run` finally instead) —
    the §8 harness sweep's twin."""
    for d in paths.tests_scratch_dir().iterdir():
        shutil.rmtree(d, ignore_errors=True)


def _log_tail(h: dict, idx: int) -> list[str]:
    # §4.5: attempt numbers are monotonic and old attempts prune — the latest
    # attempt's `n` names the newest log file, never the list length.
    atts = h["steps"][idx].get("attempts") or []
    attempt = atts[-1]["number"] if atts else 1
    # §5: tailed by the reader — parsing a whole multi-megabyte step log to
    # keep its last LOG_TAIL lines is the cost this argument exists to avoid.
    lines = store.read_log(h["id"], idx, attempt, tail=LOG_TAIL)
    out = []
    for l in lines:
        text = l.get("text", "")
        if len(text) > LOG_LINE_CLIP:
            text = text[:LOG_LINE_CLIP] + LOG_LINE_CLIP_MARKER
        out.append(text)
    return out


def executions_context(auto: dict | None, current_steps: list[dict],
                       execution_id: str | None = None) -> str | None:
    """§8 RECENT EXECUTIONS: the newest settled executions of this automation/draft
    (all §4.5 kinds — the draft's test record, Draft executions, version
    executions), newest first, capped at EXECUTIONS_CAP. The newest execution — and the
    `execution_id` one (§19, the Fix-with-AI entry), however old — carries full
    detail: per-step outcomes, the error, log tails, the result. Every execution is
    marked against the in-editor step code (§4.5 per-step sha) so the agent
    never reads stale output as current behavior. None when no execution exists."""
    container_id = auto["id"] if auto else None
    with store.lock:
        hs = [h for h in store.execs.values()
              if h["automation_id"] == container_id
              and h["status"] not in ("executing", "queued")]
        hs.sort(key=lambda h: h.get("started_at") or "", reverse=True)
        picked = hs[:EXECUTIONS_CAP]
        if execution_id and all(h["id"] != execution_id for h in picked):
            f = store.execs.get(execution_id)
            if (f and f["automation_id"] == container_id
                    and f["status"] not in ("executing", "queued")):
                # §8: the forced-in run takes the last of the EXECUTIONS_CAP
                # slots rather than adding a sixth block, and the list is
                # re-sorted so the section stays newest-first.
                picked = hs[:EXECUTIONS_CAP - 1] + [f]
                picked.sort(key=lambda h: h.get("started_at") or "", reverse=True)
        # §8: the result.md excerpt is redacted like a log line — against
        # EVERY stored secret, not only the automation's grants: a draft test
        # runs with the draft's own grants, which this context never sees, and
        # a value that reached result.md must never reach the model whichever
        # grant let it in (§1 — redaction must never fail open).
        names_by_id = {s["id"]: s["name"] for s in store.secrets}
    if not picked:
        return None
    redaction_cache: dict[str, dict[str, str] | None] = {}

    def redact(text: str) -> str | None:
        """The text with the granted secret values replaced the way a log line
        is; None when a value could not be read (fail closed: the caller then
        leaves the text out). Keychain reads run here, never under store.lock,
        and only once a result excerpt actually needs them."""
        if "map" not in redaction_cache:
            try:
                values_by_id = {}
                for secret_id in sorted(names_by_id):
                    value = keychain.get_secret(secret_id)
                    if value is not None:
                        values_by_id[secret_id] = value
                redaction_cache["map"] = build_redactions(values_by_id, names_by_id)
            except Exception:  # noqa: BLE001 — a locked/denied keychain read
                redaction_cache["map"] = None
        redactions = redaction_cache["map"]
        return None if redactions is None else redact_text(text, redactions)
    # §4.1: a record's sha hashes the raw bytes that ran while the draft holds
    # LF text — a CRLF-written step file counts as the same step.
    cur_shas = [(_step_sha(s), _crlf_step_sha(s)) for s in current_steps or []]
    blocks = []
    kept: set[int] = set()  # block indexes the section cap never drops
    for i, h in enumerate(picked):
        full = store.exec_full(h["id"]) or h
        detail = i == 0 or (execution_id is not None and h["id"] == execution_id)
        if detail:
            kept.add(i)
        blocks.append(_bounded_execution_block(full, cur_shas, detail, redact))
    # §8: past SECTION_CAP the runs are dropped oldest-first — but never the
    # newest run (block 0) nor the request's `executionId` run, wherever it
    # sorted; with only those left the per-block cap already bounds them.
    order = list(range(len(blocks)))
    while len("\n\n".join(blocks[i] for i in order)) > SECTION_CAP:
        droppable = [i for i in order if i not in kept]
        if not droppable:
            break
        order.remove(droppable[-1])
    return "\n\n".join(blocks[i] for i in order)


def _bounded_execution_block(h: dict, cur_shas: list[tuple[str, str]], detail: bool,
                             redact=None) -> str:
    """§8: one run's block bounded to BLOCK_CAP — while it exceeds the cap,
    the per-step log tails shrink (fewer lines, the last ones kept; the step
    at fault keeps the most) until it fits or no tail line is left."""
    tails: dict[int, list[str]] = {}
    tail_lines = LOG_TAIL
    while True:
        block = _execution_block(h, cur_shas, detail, tail_lines, tails, redact)
        if len(block) <= BLOCK_CAP or tail_lines == 0:
            return block
        tail_lines //= 2


def _last_lines(lines: list[str], count: int) -> list[str]:
    return lines[-count:] if count > 0 else []


def _execution_block(h: dict, cur_shas: list[tuple[str, str]], detail: bool,
                     tail_lines: int = LOG_TAIL,
                     tails: dict[int, list[str]] | None = None,
                     redact=None) -> str:
    """`tail_lines` bounds each earlier step's log tail; the failing step
    keeps twice that (up to LOG_TAIL). `tails` caches the read tails across
    the shrink passes of `_bounded_execution_block`. `redact` is applied to
    the result.md excerpt (a file the step wrote, never rewritten by the
    engine); a None from it leaves the excerpt out."""
    if tails is None:
        tails = {}

    def tail_of(idx: int, count: int) -> list[str]:
        if idx not in tails:
            tails[idx] = _log_tail(h, idx)
        return _last_lines(tails[idx], count)

    steps = h.get("steps") or []
    shas = [s.get("sha") for s in steps]
    same = len(shas) == len(cur_shas) and all(
        sha in forms for sha, forms in zip(shas, cur_shas))
    stale = ("steps match the current draft" if same
             else "ran older steps — treat its behavior as historical")
    started = ""
    if h.get("started_at"):
        started = timefmt.started_label(timefmt.parse_local(h["started_at"]))
    head = (f"--- {exec_version_label(h)} execution · {h.get('status')} · started {started} · "
            f"trigger {h.get('trigger')} · {stale} ---")
    lines = [head]
    err = h.get("error") or {}
    if not detail:
        if err.get("message"):
            lines.append(f"failed at step {err.get('step')}: {err['message']}")
        return "\n".join(lines)
    for i, s in enumerate(steps, 1):
        dur = f" · {s['duration_ms'] // 1000}s" if s.get("duration_ms") else ""
        lines.append(f"step {i}: {s.get('name')} — {s.get('status')}{dur}")
    if err.get("message"):
        lines.append(f"error at step {err.get('step')}: {err['message']}"
                     + (f" (possible reason: {err['reason']})" if err.get("reason") else ""))
    failed_at = next((i for i, s in enumerate(steps) if s.get("status") == "failed"), None)
    if failed_at is not None:
        # The failing step's tail plus the earlier steps' — the cause is often upstream.
        for j in range(failed_at):
            if tail := tail_of(j, tail_lines):
                lines.append(f"log tail (step {j + 1}):\n" + "\n".join(tail))
        if tail := tail_of(failed_at, min(LOG_TAIL, tail_lines * 2)):
            lines.append("log tail (failing step):\n" + "\n".join(tail))
    if h.get("chip"):
        lines.append(f"result chip: {h['chip']}")
    files = store.result_files(h["id"])
    if files:
        lines.append("result files: " + ", ".join(f["name"] for f in files))
    rmd = store.exec_dir(h["id"]) / "result" / "result.md"
    if rmd.is_file():
        try:
            # A step writes result.md itself — any encoding, and it can vanish or
            # be unreadable between the check and the read. Never fail the §8
            # context over it: the run just contributes no result text.
            text = rmd.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            text = ""
        # §8: redacted before the clip, so a value straddling the cut can't
        # leave its head in the prompt.
        if text:
            text = (redact(text) if redact is not None else text) or ""
        if len(text) > RESULT_EXCERPT:
            text = text[:RESULT_EXCERPT] + "\n… [result.md truncated]"
        if text:
            lines.append("result.md:\n" + text)
    return "\n".join(lines)
