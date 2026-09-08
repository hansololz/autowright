"""Per-step subprocess executor + the `autowright` step SDK (§6.1).

Invoked as `python -m autowright.executor <script.py>` with a JSON context on
stdin (never argv/env — secrets travel on the pipe). Registers the SDK as the
importable `autowright` module (steps import what they use; nothing is a
global), executes the script, and reports structured events as `@@AD@@{json}`
control lines on stdout. Plain stdout/stderr lines become out/err log lines.
"""
from __future__ import annotations

import io
import json
import sys
import time
import traceback
import urllib.error
import urllib.robotparser
import urllib.request
from pathlib import Path, PurePath

# Import before main() replaces sys.modules["autowright"] with the SDK shim.
from . import harness as _harness
from .imports_check import disallowed_imports

CTRL = "@@AD@@"
USER_AGENT = "Autowright/1.0"

# §2 pipe-encoding contract: the engine decodes these pipes as UTF-8; the
# locale codec (cp1252 on Windows) can't encode this module's own log text.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

_real_stdout = sys.stdout


def emit(op: str, **kw) -> None:
    _real_stdout.write(CTRL + json.dumps({"op": op, **kw}, ensure_ascii=False) + "\n")
    _real_stdout.flush()


class MissingSecret(Exception):
    pass


class AgentCallError(Exception):
    """A runtime agent.ask call failed — kept distinct so the engine can name
    the likely cause in the execution's failure diagnostics (§7)."""


class Secrets:
    """§6.1: `secrets["<secret id>"]` — subscript by the §4.8 secret id (a
    literal quoted string). Errors label secrets by NAME via the id→name map —
    ids are the identity, names the display."""

    def __init__(self, values: dict[str, str], allowed: list[str], names: dict[str, str]):
        self._values = values
        self._allowed = set(allowed)
        self._names = names

    def _label(self, secret_id: str) -> str:
        name = self._names.get(secret_id)
        return name if name else f"{secret_id[:8]}…"

    def __getitem__(self, secret_id: str) -> str:
        if secret_id not in self._allowed:
            raise MissingSecret(
                f"secret {self._label(secret_id)} is not allowed for this automation")
        if secret_id not in self._values:
            # Allowed but not injected: the step never declared or referenced
            # it, so §6 scoping withheld it — the Keychain may well hold it.
            # Saying "not in your Keychain" here would send the user to the
            # wrong fix.
            raise MissingSecret(
                f"secret {self._label(secret_id)} wasn't injected into this step — steps "
                f"only receive the secrets they reference in code or declare in the "
                f"step's secrets list; add it there")
        return self._values[secret_id]

    def __getattr__(self, name: str) -> str:
        if name.startswith("_"):
            raise AttributeError(name)
        # §6.1: attribute access does not exist — secrets are addressed by id.
        raise MissingSecret(
            f"secrets.{name} isn't how secrets are read — use secrets[\"<secret id>\"] "
            f"with the secret's granted id")


class Memory:
    """Path-like handle on the automation's memory dir + YAML helpers."""

    def __init__(self, root: str):
        self.path = Path(root)
        self.path.mkdir(parents=True, exist_ok=True)

    def __fspath__(self) -> str:
        return str(self.path)

    def __truediv__(self, other: str) -> Path:
        return self.path / other

    def _file(self, name: str) -> Path:
        """§6.1: a memory key is a plain file name confined to the memory dir —
        snapshots and "Clear memory" (§4.4) operate on that dir, so a key that
        escapes it would silently outlive both."""
        name = str(name)
        if not name or name.startswith("/") or "/" in name or "\\" in name or name == "..":
            raise ValueError(
                f"memory name {name!r} must be a plain file name, without path separators")
        # A drive-relative or rooted name ("C:x.yaml") replaces the whole path
        # under pathlib's join semantics — it would escape the memory dir.
        if PurePath(name).drive or PurePath(name).is_absolute():
            raise ValueError(
                f"memory name {name!r} must be a plain file name, without a drive or root")
        return self.path / (name if "." in name else name + ".yaml")

    def load(self, name: str, default=None):
        import yaml

        f = self._file(name)
        if not f.exists():
            return default
        return yaml.safe_load(f.read_text(encoding="utf-8"))

    def save(self, name: str, obj) -> None:
        """§6: commit atomically (temp file in the same dir, then rename), so a
        concurrent execution of the same automation — `maxParallel > 1` shares one
        memory dir — can never read a half-written file, and a crash mid-write
        leaves the previous version intact rather than a truncated one. This does
        not make read-modify-write safe: two executions doing
        `save(k, load(k) + 1)` still race and one increment is lost."""
        import os
        import tempfile

        import yaml

        f = self._file(name)
        f.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump(obj, sort_keys=False, allow_unicode=True)
        # Same directory as the target: os.replace is only atomic within a
        # filesystem, and /tmp may well be a different one.
        fd, tmp = tempfile.mkstemp(dir=f.parent, prefix=f".{f.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, f)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


class Execution:
    """§6.1 read-only execution metadata: which automation/execution/step this is."""

    _FIELDS = ("automation_id", "automation_name", "id",
               "step_index", "step_name", "trigger", "trigger_payload")

    def __init__(self, meta: dict):
        for f in self._FIELDS:
            object.__setattr__(self, f, meta.get(f))

    def __setattr__(self, name: str, value) -> None:
        raise AttributeError("execution metadata is read-only")


class Log:
    def __call__(self, text: str) -> None:
        emit("log", kind="out", text=str(text))

    def info(self, text: str) -> None:
        emit("log", kind="out", text=str(text))

    def warn(self, text: str) -> None:
        emit("log", kind="wrn", text=str(text))

    def error(self, text: str) -> None:
        emit("log", kind="err", text=str(text))


class Result:
    """§6.1 result builder. Chip + status go to the execution record via the
    engine; output files are written directly into `result.path` (result.md, images, …)."""

    def __init__(self, root: str):
        self.path = Path(root)
        self.path.mkdir(parents=True, exist_ok=True)

    def __fspath__(self) -> str:
        return str(self.path)

    def __truediv__(self, other: str) -> Path:
        return self.path / other

    def status(self, s: str) -> None:
        if s not in ("changes", "ok", "attention"):
            raise ValueError("result.status must be changes | ok | attention")
        emit("result", field="status", value=s)

    def chip(self, text: str) -> None:
        # A chip is a short §4.5 badge — cap it so a runaway string can never
        # blow the engine's size-capped control-line read.
        emit("result", field="chip", value=str(text)[:1_000])


def scan_outbound(text: str, what: str, scan: dict[str, str]) -> None:
    """§6: refuse to send `text` anywhere off this Mac if it carries a secret
    value. `scan` is every value of the automation, not just the calling step's:
    a value written to workspace/memory by an earlier step must be caught too.
    Multi-line values are probed line by line, so a partial paste of a key is
    caught. Passed in explicitly — a default would let a missed wiring silently
    turn the scan into a no-op."""
    for sname, val in scan.items():
        if not val:
            continue
        probes = [val] + [p for p in val.splitlines() if p.strip()] if "\n" in val else [val]
        if any(p in text for p in probes):
            raise RuntimeError(f"{what} contains the value of secret {sname} — refusing to send")


class Agent:
    """§6.1 agent handle — bound to ONE of the step's agents. The bare `agent`
    global is the handle for the step's first `agents:` entry; `agents["<id>"]`
    returns the handle for another declared entry."""

    def __init__(self, ctx: dict, scan: dict[str, str], cfg: dict | None):
        self._ctx = ctx
        # §6: held apart from _ctx so a step that declared no secrets can't read
        # another step's value off this object. Required, not defaulted — a
        # fallback would let a missed wiring quietly narrow the outbound scan.
        # Scoping hygiene, not a boundary: a step is arbitrary in-process Python
        # (§6.2 — the engine is not a sandbox).
        self._scan = scan
        self._cfg = cfg

    def ask(self, prompt: str, data=None) -> str:
        return self._ask(prompt, data)

    # prototype scripts use agent.read(page, q) / agent.write(rows, q)
    def read(self, data, prompt: str) -> str:
        return self._ask(prompt, data)

    def write(self, data, prompt: str) -> str:
        return self._ask(prompt, data)

    def _ask(self, prompt: str, data) -> str:
        if not self._ctx.get("is_agent_step"):
            raise RuntimeError("agent calls are only available in steps marked as agent steps")
        cfg = self._cfg
        if cfg is None:
            raise RuntimeError("no enabled agent for this step")
        full = str(prompt) if data is None else f"question: {prompt}\n\ndata:\n{data}"
        # §6: secret values must never enter a prompt — scan before sending,
        # against ALL of the automation's secret values, not just this step's
        # own: a value written to workspace/memory by an earlier step must be
        # caught too.
        scan_outbound(full, "prompt", self._scan)
        emit("log", kind="sys",
             text=f"agent query → {_harness.grant_name(cfg)} ({cfg.get('harness')}, {len(full)} chars)")
        if len(full) > 200_000:
            raise RuntimeError("agent prompt too large (200k char cap)")
        # §7 kill semantics: the harness CLI spawns in its OWN session (its
        # watchdog kills it without killing this step), so a step-group kill
        # can't reach it. Report its group to the engine at spawn and retract
        # it when the call ends — cancel/timeout/skip then kill it too, and
        # §3 recovery sweeps one a crashed backend orphaned.
        spawned: dict = {}

        def _on_spawn(p) -> None:
            spawned["pgid"] = p.pid  # own session → pgid == pid (§2)
            emit("agent_group", pgid=p.pid)

        try:
            reply = _harness.invoke(cfg, full, timeout=self._ctx.get("agent_timeout", 120),
                                    on_spawn=_on_spawn)
        except Exception as e:  # noqa: BLE001
            raise AgentCallError(f"agent call failed ({cfg.get('harness')}): {e}") from e
        finally:
            if "pgid" in spawned:
                emit("agent_group_done", pgid=spawned["pgid"])
        if len(reply) > 200_000:
            raise RuntimeError("agent reply too large (200k char cap)")
        # §6: the FULL prompt/reply go to logs for audit (already size-capped above).
        emit("agent_audit", prompt=full, reply=reply)
        return reply.strip()


class Agents:
    """§6.1: `agents["<agent id>"]` — the handle for one of the step's declared
    `agents:` entries, addressed by the §4.7 agent id (a literal quoted string,
    like `secrets["<id>"]`). Subscripting an id the step doesn't carry raises."""

    def __init__(self, ctx: dict, scan: dict[str, str]):
        self._ctx = ctx
        self._scan = scan

    def __getitem__(self, agent_id: str) -> Agent:
        cfgs = self._ctx.get("agents") or []
        cfg = next((c for c in cfgs if c.get("id") == agent_id), None)
        if cfg is None:
            avail = ", ".join(f"{_harness.grant_name(c)} ({c.get('id')})" for c in cfgs) or "none"
            raise RuntimeError(f"agents[{agent_id!r}] isn't among this step's declared agents — "
                               f"it can call: {avail}")
        return Agent(self._ctx, self._scan, cfg)


_site_last: dict[str, float] = {}
_robots: dict[str, urllib.robotparser.RobotFileParser] = {}


def fetch_page(url: str) -> str:
    """§6 web policies: 10s timeout, ≥2s per-site spacing, retry twice, robots.txt, UA."""
    from urllib.parse import urlparse

    host = urlparse(url).netloc
    rp = _robots.get(host)
    if rp is None:
        rp = urllib.robotparser.RobotFileParser()
        try:
            # Fetch robots.txt ourselves: RobotFileParser.read() has no timeout,
            # so a black-holing server would hang the step until the watchdog.
            req = urllib.request.Request(f"{urlparse(url).scheme}://{host}/robots.txt",
                                         headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as r:
                rp.parse(r.read().decode("utf-8", errors="replace").splitlines())
        except urllib.error.HTTPError as e:
            # Same semantics as RobotFileParser.read(): 401/403 mean "crawlers
            # not welcome" (disallow all); other 4xx (no robots.txt) allow all.
            if e.code in (401, 403):
                rp.disallow_all = True  # type: ignore[attr-defined]
            else:
                rp.allow_all = True  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            rp.allow_all = True  # type: ignore[attr-defined]
        _robots[host] = rp
    try:
        allowed = rp.can_fetch(USER_AGENT, url)
    except Exception:  # noqa: BLE001
        allowed = True
    if not allowed:
        raise RuntimeError(f"robots.txt disallows fetching {url}")
    wait = _site_last.get(host, 0) + 2.0 - time.time()
    if wait > 0:
        time.sleep(wait)
    last_err: Exception | None = None
    for attempt in range(3):  # first try + retry twice
        _site_last[host] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < 2:  # no pointless sleep after the final failure
                time.sleep(2)
    raise RuntimeError(f"couldn't fetch {url}: {last_err}")


class _LineWriter(io.TextIOBase):
    MAX_LINE = 131_072  # a newline-free stream must not grow the buffer unbounded

    def __init__(self, kind: str):
        self.kind = kind
        self.buf = ""

    def write(self, s: str) -> int:  # type: ignore[override]
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                emit("log", kind=self.kind, text=line)
        if len(self.buf) > self.MAX_LINE:
            emit("log", kind=self.kind, text=self.buf)
            self.buf = ""
        return len(s)

    def flush(self) -> None:
        if self.buf.strip():
            emit("log", kind=self.kind, text=self.buf)
        self.buf = ""


def main() -> int:
    script = sys.argv[1]
    ctx = json.load(sys.stdin)
    # §6: take the automation-wide scan map off ctx — Agent keeps a ctx
    # reference, so leaving it would hand every secret of the automation
    # to a step that declared none of them. §6.1: passed, never defaulted —
    # a wiring mistake that drops it must fail loudly here, not silently
    # narrow the outbound scan to the step's own secrets (fail closed).
    scan_secrets = ctx.pop("scan_secrets")
    # §6.2: declared packages live in the user-writable site-packages dir — the
    # bundled interpreter never has them installed directly.
    if ctx.get("site_packages"):
        sys.path.insert(0, str(ctx["site_packages"]))
    workspace = Path(ctx["workspace"])
    workspace.mkdir(parents=True, exist_ok=True)
    import os

    os.chdir(workspace)

    # §6.1: non-secret execution metadata + paths go into the environment too, so
    # child processes a step spawns can self-identify without plumbing. The
    # executor itself never reads these back — stdin JSON stays the only input.
    exec_meta = ctx.get("execution", {})
    for key, value in {
        "AUTOWRIGHT_AUTOMATION_ID": exec_meta.get("automation_id"),
        "AUTOWRIGHT_AUTOMATION_NAME": exec_meta.get("automation_name"),
        "AUTOWRIGHT_EXECUTION_ID": exec_meta.get("id"),
        "AUTOWRIGHT_STEP_INDEX": exec_meta.get("step_index"),
        "AUTOWRIGHT_STEP_NAME": exec_meta.get("step_name"),
        "AUTOWRIGHT_TRIGGER": exec_meta.get("trigger"),
        "AUTOWRIGHT_TRIGGER_PAYLOAD": (json.dumps(exec_meta["trigger_payload"], ensure_ascii=False)
                                       if exec_meta.get("trigger_payload") else None),
        "AUTOWRIGHT_WORKSPACE": str(workspace),
        "AUTOWRIGHT_MEMORY_DIR": ctx["memory_dir"],
        "AUTOWRIGHT_RESULT_DIR": ctx["result_dir"],
    }.items():
        if value is not None:
            os.environ[key] = str(value)

    def notify(text: str) -> None:
        # macOS notifications show ~150 chars — cap well above that so an
        # oversize string can't blow the engine's control-line read.
        emit("notify", text=str(text)[:10_000])

    def reply(text: str) -> None:
        # §6.1: only message-trigger executions have an origin to answer; the
        # send itself happens engine-side (the bot token never reaches here).
        if not ctx.get("can_reply"):
            raise RuntimeError("reply() is only available in executions started "
                               "by a message trigger (e.g. Discord)")
        # §6.1: a reply goes to a third party — same refusal an agent prompt
        # gets. Hard raise, not a redaction: posting "•••" to a channel would
        # hide that the automation tried to send a Keychain value.
        text = str(text)
        # Same 200k cap as agent prompts/replies — an oversize control line
        # would blow the engine's size-capped readline and the reply would be
        # silently lost mid-pipe; a hard raise tells the step instead (§6.1
        # failed sends are never silent).
        if len(text) > 200_000:
            raise RuntimeError("reply too large (200k char cap)")
        scan_outbound(text, "reply", scan_secrets)
        emit("reply", text=text)

    sdk = {
        "params": ctx.get("params", {}),
        "secrets": Secrets(ctx.get("secrets", {}), ctx.get("allowed_secrets", []),
                           ctx.get("secret_names", {})),
        "memory": Memory(ctx["memory_dir"]),
        "workspace": workspace,
        "execution": Execution(exec_meta),
        "log": Log(),
        "result": Result(ctx["result_dir"]),
        "notify": notify,
        "reply": reply,
        # §6.1: `agent` is the ready-made handle for the step's FIRST agents:
        # entry (or the engine's first-enabled fallback when the step lists
        # none); `agents["<id>"]` addresses the other declared entries.
        "agent": Agent(ctx, scan_secrets, (ctx.get("agents") or [None])[0]),
        "agents": Agents(ctx, scan_secrets),
        "fetch_page": fetch_page,
    }
    # §6.1: the SDK reaches a step only through `import autowright` — nothing is
    # injected into the script's globals, so an unimported name is a NameError.
    import types

    sdk_mod = types.ModuleType("autowright")
    for k, v in sdk.items():
        setattr(sdk_mod, k, v)
    sys.modules["autowright"] = sdk_mod

    g = {"__name__": "__main__", "__file__": script}

    sys.stdout = _LineWriter("out")  # type: ignore[assignment]
    sys.stderr = _LineWriter("err")  # type: ignore[assignment]
    try:
        source = Path(script).read_text(encoding="utf-8")
        # §6.2: re-validate the import allowlist at runtime — the draft-time
        # check alone doesn't cover hand-edited or stale scripts. The version's
        # declared packages extend the allowlist.
        bad = disallowed_imports(source, ctx.get("package_imports") or [])
        if bad:
            msg = (f"import {', '.join(bad)} isn't allowed — steps may only import "
                   f"the Python stdlib, the curated packages, and this automation's "
                   f"declared packages (§6.2)")
            emit("error", type="DisallowedImport", message=msg)
            emit("log", kind="err", text=msg)
            return 4
        code = compile(source, script, "exec")
        exec(code, g)  # noqa: S102 — this is the engine's job
        sys.stdout.flush()
        sys.stderr.flush()
        return 0
    except MissingSecret as e:
        # Flush the line-writer shims first — a partial print() pending at
        # failure time is exactly the output tail the user needs in the log.
        sys.stdout.flush()
        sys.stderr.flush()
        emit("error", type="MissingSecret", message=str(e))
        emit("log", kind="err", text=str(e))
        return 3
    except SystemExit as e:
        # A step calling sys.exit() / sys.exit(0) is an ordinary early exit,
        # not a failure; a nonzero or message exit still fails the step —
        # keeping the author's message (sys.exit("why")) as the diagnostic.
        sys.stdout.flush()
        sys.stderr.flush()
        if e.code is None or e.code == 0:
            return 0
        if isinstance(e.code, int):
            msg = f"step exited with code {e.code}"
            rc = e.code
        else:
            msg = f"SystemExit: {e.code}"
            rc = 1
        emit("error", type="SystemExit", message=msg)
        emit("log", kind="err", text=msg)
        return rc
    except BaseException as e:  # noqa: BLE001
        # §7 failure diagnostics: the engine stores this structured event as the
        # execution's error; the traceback still goes to the logs line by line.
        try:
            sys.stdout.flush()  # keep the partial-line tail (see MissingSecret)
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
        emit("error", type=type(e).__name__,
             message=f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)
        for ln in traceback.format_exc().strip().splitlines():
            emit("log", kind="err", text=ln)
        return 1


if __name__ == "__main__":
    sys.exit(main())
