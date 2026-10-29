"""Per-step subprocess executor + the `autowright` step SDK (§6.1).

Invoked as `python -m autowright.executor <script.py>` with a JSON context on
stdin (never argv/env — secrets travel on the pipe). Registers the SDK as the
importable `autowright` module (steps import what they use; nothing is a
global), executes the script, and reports structured events as `@@AD@@{json}`
control lines on stdout. Plain stdout/stderr lines become out/err log lines.
"""
from __future__ import annotations

import atexit
import io
import json
import sys
import threading
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


# Control lines are emitted from the step thread AND from the harness spawn
# callback thread (§7 agent_group) — an interleaved write loses the op the
# engine needs, so one line goes out at a time. Re-entrant (§7): a signal
# handler that emits while this thread holds it must not deadlock the step.
_emit_lock = threading.RLock()


def emit(op: str, **kw) -> None:
    line = CTRL + json.dumps({"op": op, **kw}, ensure_ascii=False) + "\n"
    with _emit_lock:
        _real_stdout.write(line)
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


# §6: the largest AUTOWRIGHT_TRIGGER_PAYLOAD (UTF-8 bytes of its JSON) the
# executor exports into the environment.
TRIGGER_PAYLOAD_ENV_CAP = 65536


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
    # §7: each call goes out as guarded ≤ MAX_LOG_PIECE pieces — an oversize
    # `log(json.dumps(...))` must never tear the engine's bounded read.
    def __call__(self, text: str) -> None:
        _emit_log("out", str(text))

    def info(self, text: str) -> None:
        _emit_log("out", str(text))

    def warn(self, text: str) -> None:
        _emit_log("wrn", str(text))

    def error(self, text: str) -> None:
        _emit_log("err", str(text))


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
        # blow the engine's size-capped control-line read. §7: a guarded cut,
        # never a fixed slice ahead of the engine's redaction.
        emit("result", field="chip", value=(_guarded_pieces(str(text), 1_000) or [""])[0])


def substantive_probe(part: str) -> bool:
    """§4.8: whether one line of a multi-line secret value is matched on its
    own — at least 8 characters once stripped, with at least one letter or
    digit. A structural line (`{`, `}`, `-----`, a short indent) would
    otherwise redact or block every text that happens to contain it. Shared
    by the outbound scan here and the engine's log redaction
    (`engine.build_redactions`, which also gates §6.1 replies)."""
    stripped = part.strip()
    return len(stripped) >= 8 and any(c.isalnum() for c in stripped)


def _line_probes(text: str) -> list[str]:
    """§4.8: each substantive line of a multi-line text, as written and
    whitespace-stripped, each also JSON-escaped (a line re-serialized inside
    a `json.dumps` or a dict's `repr` keeps its escapes)."""
    forms: list[str] = []
    for line in text.splitlines():
        if not substantive_probe(line):
            continue
        stripped = line.strip()
        forms.extend((line, stripped, json.dumps(line)[1:-1], json.dumps(stripped)[1:-1]))
    return forms


# §4.8: the key words that mark a JSON leaf as secret material. A leaf under
# any other key (`"type": "service_account"`, `"universe_domain":
# "googleapis.com"`) is public and would redact or block every text naming it.
_SECRET_KEY_WORDS = ("secret", "token", "password", "passwd", "private", "key",
                     "credential", "auth", "signature")


def _secret_material_leaf(key: str | None, leaf: str) -> bool:
    """§4.8: whether one JSON string leaf counts as secret material — every
    leaf with no enclosing object key (a top-level array), else a leaf whose
    nearest enclosing key, lowercased, holds a `_SECRET_KEY_WORDS` word, does
    not end in `url`/`uri`, and whose text is not a URL."""
    if key is None:
        return True
    lowered = key.lower()
    if not any(word in lowered for word in _SECRET_KEY_WORDS):
        return False
    if lowered.endswith(("url", "uri")):
        return False
    return "://" not in leaf


def _json_string_leaves(value: str) -> list[str]:
    """§4.8: every secret-material string leaf of `value` parsed as JSON
    (dicts and lists walked iteratively, each leaf gated by its nearest
    enclosing object key — `_secret_material_leaf`), [] when it doesn't
    parse."""
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    leaves: list[str] = []
    pending: list[tuple[str | None, object]] = [(None, parsed)]
    while pending:
        key, node = pending.pop()
        if isinstance(node, str):
            if _secret_material_leaf(key, node):
                leaves.append(node)
        elif isinstance(node, dict):
            pending.extend((str(child_key), child) for child_key, child in node.items())
        elif isinstance(node, list):
            pending.extend((key, child) for child in node)
    return leaves


def secret_probes(value: str) -> list[str]:
    """§4.8 / §6: every text that counts as an occurrence of one secret value
    — the whole value first, then its JSON-escaped (`ensure_ascii` on and
    off), `repr`, and trailing-newline-stripped forms, each line of a
    multi-line value (`_line_probes`), and, when the value parses as JSON,
    every substantive secret-material string leaf (key-gated,
    `_secret_material_leaf`) with its escaped forms and its own lines —
    so a service-account key survives `json.loads` → `print(creds)` or
    `json.dumps`. Every line and leaf probe is at least 8 characters, so a
    lone `{` or `true` never matches. Shared by the engine's log
    redaction (`engine.build_redactions`), the outbound scan, and the cut
    guards, so all three hold the same set."""
    if not value:
        return []
    probes = [value]
    seen = {value}

    def add(probe: str, *, whole: bool = False) -> None:
        # The whole value's own forms match at any length (they are the
        # secret itself); line and leaf probes need at least 8 characters.
        if probe and (whole or len(probe) >= 8) and probe not in seen:
            seen.add(probe)
            probes.append(probe)

    for variant in (json.dumps(value)[1:-1], json.dumps(value, ensure_ascii=False)[1:-1],
                    repr(value)[1:-1], value.rstrip("\r\n")):
        add(variant, whole=True)
    if "\n" in value:
        for form in _line_probes(value):
            add(form)
    for leaf in _json_string_leaves(value):
        if not substantive_probe(leaf):
            continue
        for form in (leaf, json.dumps(leaf)[1:-1], json.dumps(leaf, ensure_ascii=False)[1:-1],
                     repr(leaf)[1:-1]):
            add(form)
        if "\n" in leaf:
            for form in _line_probes(leaf):
                add(form)
    return probes


def scan_outbound(text: str, what: str, scan: dict[str, str]) -> None:
    """§6: refuse to send `text` anywhere off this Mac if it carries a secret
    value. `scan` is every value of the automation, not just the calling step's:
    a value written to workspace/memory by an earlier step must be caught too.
    Each value is probed by its full `secret_probes` set (lines, escaped
    forms, JSON leaves — §4.8), so a partial paste or a re-serialized key is
    caught. Passed in explicitly — a default would let a missed wiring silently
    turn the scan into a no-op."""
    for sname, val in scan.items():
        if not val:
            continue
        if any(p in text for p in secret_probes(val)):
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


# §7: the automation's secret values (plus each substantive line of a
# multi-line value, §4.8) a log-piece cut must never split — set in `main()`.
_cut_guards: list[str] = []
# §7: longest cut guard − 1 (0 with no guards) — how many trailing characters
# the line writer's newline-free overflow flush holds back, so a value
# straddling two `write` calls is never split by the buffer's own end. Set in
# `main()` beside `_cut_guards`.
_GUARD_TAIL = 0

# §7 / §4.5: the structured `error` event's message is clipped here (guarded
# cut, marker below) and again by the engine after redaction.
ERROR_MESSAGE_CAP = 4_000
CLIP_MARKER = "… [clipped]"
# §7: every executor-originated log text goes out as pieces of at most this
# many characters — far under the engine's size-capped readline.
MAX_LOG_PIECE = 131_072


def _build_cut_guards(scan: dict[str, str]) -> list[str]:
    """§7: every `secret_probes` probe of every value of the scan map — the
    same probes `scan_outbound` matches."""
    guards: list[str] = []
    for value in scan.values():
        guards.extend(secret_probes(value))
    return guards


def _guard_tail(guards: list[str]) -> int:
    """§7: the overflow hold-back — longest guard − 1, 0 when there are none."""
    return max((len(guard) for guard in guards), default=1) - 1


def _guarded_boundary(text: str, start: int, end: int, limit: int) -> int:
    """§7: `end` moved back to just before any `_cut_guards` occurrence that
    straddles it (a guard longer than `limit` can't be kept whole and is
    ignored). May return `start` — callers decide what an empty piece means."""
    cut = end
    for guard in _cut_guards:
        if len(guard) > limit:
            continue
        # Any occurrence starting in this window straddles `end`.
        found = text.find(guard, max(start, end - len(guard) + 1), end + len(guard) - 1)
        if found != -1:
            cut = min(cut, found)
    return cut


def _guarded_pieces(text: str, limit: int) -> list[str]:
    """§7: `text` as consecutive pieces of at most `limit` characters. A
    boundary that would split an occurrence of a secret value (`_cut_guards`)
    moves back to just before it, so the engine redacts every piece whole;
    never an empty piece (a boundary with nowhere to move cuts at `limit`).
    Empty text gives no pieces."""
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            cut = _guarded_boundary(text, start, end, limit)
            if cut > start:  # never an empty piece — cut at the limit then
                end = cut
        pieces.append(text[start:end])
        start = end
    return pieces


def _guarded_clip(text: str, limit: int, marker: str = CLIP_MARKER) -> str:
    """§7: the first guarded piece of `text`, with `marker` appended when
    something was cut — never a fixed slice that could leave a secret's head
    behind as a fragment the engine's redaction can't match."""
    if len(text) <= limit:
        return text
    return _guarded_pieces(text, limit)[0] + marker


def _emit_log(kind: str, text: str) -> None:
    """§7: one log text as ≤ MAX_LOG_PIECE guarded pieces, each its own
    control line — an oversize `log()` never tears the engine's bounded read."""
    for piece in _guarded_pieces(text, MAX_LOG_PIECE) or [text]:
        emit("log", kind=kind, text=piece)


def _emit_error(error_type: str, message: str) -> None:
    """§7: the structured `error` event, its message clipped at a guarded cut."""
    emit("error", type=error_type, message=_guarded_clip(message, ERROR_MESSAGE_CAP))


class _LineWriter(io.TextIOBase):
    MAX_LINE = MAX_LOG_PIECE  # a newline-free stream must not grow the buffer unbounded

    def __init__(self, kind: str):
        self.kind = kind
        self.buf = ""
        # A step's threads share sys.stdout — the buffer split must be atomic
        # or two writers duplicate or merge lines. §7: re-entrant — a SIGTERM
        # handler or a `__del__` warning that prints while this thread holds
        # it appends to the buffer (`_writing`) instead of deadlocking.
        self._lock = threading.RLock()
        self._writing = False

    def _emit(self, text: str) -> None:
        """One logical line as ≤ MAX_LINE pieces — every control line stays
        far under the engine's size-capped readline, so an oversize print is
        streamed in bounded chunks instead of arriving torn (and dropped as
        unparsable JSON). §7: a cut never falls inside an occurrence of a
        secret value (`_cut_guards`) — the boundary moves back to just before
        it, so the engine's redaction sees every occurrence whole."""
        for piece in _guarded_pieces(text, self.MAX_LINE):
            if piece.strip():
                emit("log", kind=self.kind, text=piece)

    def write(self, s: str) -> int:  # type: ignore[override]
        with self._lock:
            if self._writing:
                # Re-entered from this thread mid-write: the outer write's
                # loop picks the appended text up.
                self.buf += s
                return len(s)
            self._writing = True
            try:
                self._write_locked(s)
            finally:
                self._writing = False
        return len(s)

    def _write_locked(self, s: str) -> None:
        self.buf += s
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            if line.strip():
                self._emit(line)
        if len(self.buf) > self.MAX_LINE:
            # §7: hold back the last `_GUARD_TAIL` characters — a secret value
            # whose head ends this buffer may finish in the next `write`. The
            # hold-back point itself is a guarded boundary, so a value already
            # whole in the buffer is never split by it either.
            cut = len(self.buf) - _GUARD_TAIL
            if _GUARD_TAIL and cut > 0:
                cut = _guarded_boundary(self.buf, 0, cut, self.MAX_LINE)
            if cut > 0:
                self._emit(self.buf[:cut])
                self.buf = self.buf[cut:]

    def _held_tail(self) -> int:
        """§7: the length of the longest suffix of the buffer that is a proper
        prefix of a cut guard, capped at `_GUARD_TAIL` — the head of a secret
        value whose rest may arrive in the next `write`."""
        cap = min(_GUARD_TAIL, len(self.buf))
        if cap <= 0:
            return 0
        tail = self.buf[-cap:]
        held = 0
        for guard in _cut_guards:
            # Only suffixes longer than the best so far, and shorter than the
            # guard itself (a whole occurrence is emitted, never held).
            start = max(0, len(tail) - len(guard) + 1)
            index = tail.find(guard[0], start) if guard else -1
            while index != -1 and len(tail) - index > held:
                if guard.startswith(tail[index:]):
                    held = len(tail) - index
                    break
                index = tail.find(guard[0], index + 1)
        return held

    def flush(self) -> None:
        """§7: an explicit flush emits the buffer but holds back a trailing
        secret-value head (`_held_tail`), released by the next newline,
        overflow, or `final_flush` — a key echoed chunk by chunk with a flush
        per chunk is never split across two log lines."""
        with self._lock:
            if self._writing:
                return
            held = self._held_tail()
            cut = len(self.buf) - held
            if held and cut > 0:
                cut = _guarded_boundary(self.buf, 0, cut, self.MAX_LINE)
            if cut > 0:
                if self.buf[:cut].strip():
                    self._emit(self.buf[:cut])
                self.buf = self.buf[cut:]

    def final_flush(self) -> None:
        """§7: the process's final flush — emits whatever the buffer still
        holds, held tail included."""
        with self._lock:
            if self.buf.strip():
                self._emit(self.buf)
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
    global _cut_guards, _GUARD_TAIL
    _cut_guards = _build_cut_guards(scan_secrets)
    _GUARD_TAIL = _guard_tail(_cut_guards)
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
    payload_json = (json.dumps(exec_meta["trigger_payload"], ensure_ascii=False)
                    if exec_meta.get("trigger_payload") else None)
    if payload_json is not None and len(payload_json.encode("utf-8")) > TRIGGER_PAYLOAD_ENV_CAP:
        # §6: a larger payload would push every subprocess the step spawns
        # past the OS environment limit (E2BIG) — it stays reachable through
        # `execution.trigger_payload`, which never rides the environment.
        payload_json = None
    for key, value in {
        "AUTOWRIGHT_AUTOMATION_ID": exec_meta.get("automation_id"),
        "AUTOWRIGHT_AUTOMATION_NAME": exec_meta.get("automation_name"),
        "AUTOWRIGHT_EXECUTION_ID": exec_meta.get("id"),
        "AUTOWRIGHT_STEP_INDEX": exec_meta.get("step_index"),
        "AUTOWRIGHT_STEP_NAME": exec_meta.get("step_name"),
        "AUTOWRIGHT_TRIGGER": exec_meta.get("trigger"),
        "AUTOWRIGHT_TRIGGER_PAYLOAD": payload_json,
        "AUTOWRIGHT_WORKSPACE": str(workspace),
        "AUTOWRIGHT_MEMORY_DIR": ctx["memory_dir"],
        "AUTOWRIGHT_RESULT_DIR": ctx["result_dir"],
    }.items():
        if value is not None:
            os.environ[key] = str(value)

    def notify(text: str) -> None:
        # macOS notifications show ~150 chars — cap well above that so an
        # oversize string can't blow the engine's control-line read. §7: a
        # guarded cut, never a fixed slice ahead of the engine's redaction.
        emit("notify", text=(_guarded_pieces(str(text), 10_000) or [""])[0])

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

    out_writer, err_writer = _LineWriter("out"), _LineWriter("err")
    sys.stdout = out_writer  # type: ignore[assignment]
    sys.stderr = err_writer  # type: ignore[assignment]

    def final_flush() -> None:
        # §7: an explicit flush holds back a trailing secret-value head; the
        # exit paths below release it before their own error/log ops, and the
        # atexit hook catches anything a late thread or handler printed.
        out_writer.final_flush()
        err_writer.final_flush()

    atexit.register(final_flush)
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
            _emit_error("DisallowedImport", msg)
            _emit_log("err", msg)
            return 4
        code = compile(source, script, "exec")
        exec(code, g)  # noqa: S102 — this is the engine's job
        final_flush()
        return 0
    except MissingSecret as e:
        # Flush the line-writer shims first — a partial print() pending at
        # failure time is exactly the output tail the user needs in the log.
        final_flush()
        _emit_error("MissingSecret", str(e))
        _emit_log("err", str(e))
        return 3
    except SystemExit as e:
        # A step calling sys.exit() / sys.exit(0) is an ordinary early exit,
        # not a failure; a nonzero or message exit still fails the step —
        # keeping the author's message (sys.exit("why")) as the diagnostic.
        final_flush()
        if e.code is None or e.code == 0:
            return 0
        if isinstance(e.code, int):
            # §6: clamped into 1-255 — POSIX keeps only the low byte, so
            # sys.exit(256) must never come back as a success exit status
            # (a bool rides here too: True exits 1, False took the branch above).
            rc = e.code & 0xFF or 1
            msg = f"step exited with code {rc}"
        else:
            msg = f"SystemExit: {e.code}"
            rc = 1
        _emit_error("SystemExit", msg)
        _emit_log("err", msg)
        return rc
    except BaseException as e:  # noqa: BLE001
        # §7 failure diagnostics: the engine stores this structured event as the
        # execution's error; the traceback still goes to the logs line by line.
        try:
            final_flush()  # keep the partial-line tail (see MissingSecret)
        except Exception:  # noqa: BLE001
            pass
        _emit_error(type(e).__name__,
                    f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)
        for ln in traceback.format_exc().strip().splitlines():
            _emit_log("err", ln)
        return 1


if __name__ == "__main__":
    sys.exit(main())
