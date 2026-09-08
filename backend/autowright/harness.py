"""Agent harness adapters (§8): send one prompt, receive one text response.

Every adapter is one-shot and non-interactive.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Top-level import (not lazy): the executor subprocess replaces
# sys.modules["autowright"] with the step SDK shim, which breaks late
# `from . import paths` resolution inside _app_log.
from . import paths, platform, reqlog
from .yamlio import atomic_write_text

log = logging.getLogger("autowright.harness")


def _stamp() -> str:
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d %H:%M:%S %Z")


def _app_log(text: str) -> None:
    # §8/§5: the FULL prompt and raw response are the audit trail — never
    # truncated here (the 200k runtime caps already bound the sizes upstream).
    try:
        with open(paths.app_log(), "a", encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass

OLLAMA_URL = os.environ.get("AUTOWRIGHT_OLLAMA_URL", "http://localhost:11434")


class HarnessError(Exception):
    """A harness call failed. `retryable` marks transient failures (timeout,
    nonzero exit that looks transient) the §8 pipeline may retry once;
    environment problems (CLI not installed, unknown harness) and obvious
    deterministic failures (auth / model-not-found stderr — see
    `_deterministic_failure`) are not."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


def agent_timeout() -> int:
    """§8/§15: per-invocation agent-call idle window in seconds — the call is
    killed after this long with no observed progress; every stdout line,
    parsed handler event, and scratch-document change resets it. Read per
    call (like AUTOWRIGHT_STEP_TIMEOUT) so a running backend picks up
    changes."""
    try:
        return int(float(os.environ.get("AUTOWRIGHT_AGENT_TIMEOUT_S") or 300))
    except ValueError:
        return 300


def agent_hard_cap() -> int:
    """§8/§15: per-invocation total wall-clock cap in seconds — ends a call
    that streams forever, which the idle window alone never would. Read per
    call, like `agent_timeout`."""
    try:
        return int(float(os.environ.get("AUTOWRIGHT_AGENT_HARD_CAP_S") or 1800))
    except ValueError:
        return 1800


# §8 stream caps: memory bounds on one invocation's pipes. The idle window
# never fires while a call keeps streaming, so without these a harness stuck
# in a tool loop could push the whole hard cap's worth of output through
# backend memory and every log sink. Both are far beyond any valid response.
STDOUT_CAP_CHARS = 50_000_000
STDERR_CAP_CHARS = 1_000_000


def kill_group(proc: subprocess.Popen, sig: int | None = None) -> None:
    """Signal a harness child's whole session group (see the §2 platform
    session policy in `_invoke`); falls back to the direct child when the
    group is gone."""
    platform.current().processes.signal_group(proc, sig)


def defuse_read_end(f) -> None:
    """Cross-thread escape hatch for a pipe read end another thread may be
    blocked reading (a kill whose EOF never arrives because an escaped child
    still holds the write end). `dup2`s /dev/null over the fd: any further
    read sees EOF, the fd number stays owned (no reuse hazard for the later
    ordinary close), and — unlike TextIOWrapper.close(), which takes the
    buffer lock the blocked reader holds — it can never wedge the calling
    thread."""
    if f is None:
        return
    try:
        devnull = os.open(os.devnull, os.O_RDONLY)
        try:
            os.dup2(devnull, f.fileno())
        finally:
            os.close(devnull)
    except (OSError, ValueError):
        pass


# A backend launched from the Finder/Dock gets a minimal PATH without
# /opt/homebrew/bin or ~/.local/bin, so `shutil.which` alone misses
# normally-installed CLIs (claude installs to ~/.local/bin by default).
# §19: per-OS install locations. On Windows a GUI app inherits the full user
# PATH, so the fallbacks are belt-and-braces there.
_POSIX_FALLBACK_BIN_DIRS = (
    os.path.expanduser("~/.local/bin"),
    os.path.expanduser("~/.opencode/bin"),
    "/opt/homebrew/bin",
    "/usr/local/bin",
    # Linux: distro and channel bin dirs a desktop-launched backend's PATH
    # can miss (harmless probes elsewhere).
    "/usr/bin",
    "/snap/bin",
    os.path.expanduser("~/.nix-profile/bin"),
)
_WINDOWS_FALLBACK_BIN_DIRS = (
    # Claude Code's native installer uses the same ~/.local/bin layout here.
    os.path.join(os.path.expanduser("~"), ".local", "bin"),
    os.path.join(os.path.expanduser("~"), ".opencode", "bin"),
    # npm's global bin — the Gemini CLI / Codex channel on Windows.
    os.path.join(os.environ.get("APPDATA")
                 or os.path.join(os.path.expanduser("~"), "AppData", "Roaming"), "npm"),
)
_FALLBACK_BIN_DIRS = (
    _WINDOWS_FALLBACK_BIN_DIRS if paths.current_os() == "windows"
    else _POSIX_FALLBACK_BIN_DIRS
)


# §19: `os.access(X_OK)` can't detect executables on Windows — the execute bit
# does not exist there; an extension listed in PATHEXT is what makes a file
# runnable.
_DEFAULT_PATHEXT = ".COM;.EXE;.BAT;.CMD"


def _pathext() -> tuple[str, ...]:
    raw = os.environ.get("PATHEXT") or _DEFAULT_PATHEXT
    return tuple(e.lower() for e in raw.split(os.pathsep) if e.strip())


def _is_executable(path: str) -> bool:
    """§19: per-OS executable check — the execute bit on POSIX, existence with
    a PATHEXT extension on Windows."""
    if paths.current_os() != "windows":
        return os.access(path, os.X_OK)
    if not os.path.isfile(path):
        return False
    return os.path.splitext(path)[1].lower() in _pathext()


def _neutral_cwd(provider_id: str) -> str:
    """§6: every provider child runs in its provider's empty workspace dir so
    CLI startup scans never touch TCC-protected folders (no macOS prompts)."""
    d = paths.harness_workspace(provider_id)
    d.mkdir(parents=True, exist_ok=True)
    return str(d)


def resolve_bin(binname: str) -> str | None:
    """Absolute path of `binname`, searching PATH then common install dirs."""
    found = shutil.which(binname)
    if found:
        return found
    for d in _FALLBACK_BIN_DIRS:
        if paths.current_os() == "windows":
            # `which` against a single dir honors PATHEXT, so a bare name
            # resolves to `claude.cmd` / `ollama.exe` there (§19).
            hit = shutil.which(binname, path=d)
            if hit:
                return hit
            continue
        path = os.path.join(d, binname)
        if _is_executable(path):
            return path
    return None


def spawn_env(binpath: str | None = None) -> dict:
    """os.environ with the fallback bin dirs (and `binpath`'s own dir)
    prepended to PATH. Every provider child spawns with this (§19):
    `#!/usr/bin/env node` launchers like npm and gemini can't find `node`
    under the GUI minimal PATH otherwise, even when Node is installed."""
    env = dict(os.environ)
    bindir = os.path.dirname(binpath) if binpath else ""
    dirs = ([bindir] if bindir else []) + list(_FALLBACK_BIN_DIRS)
    current = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []
    env["PATH"] = os.pathsep.join(dict.fromkeys(dirs + current))
    return env


def step_env() -> dict:
    """os.environ with the fallback bin dirs APPENDED to PATH (§6.1). Every
    step subprocess spawns with this, so a step's system-CLI calls and
    `shutil.which` pre-flights resolve normally-installed tools under a Dock
    launch's minimal GUI PATH — appended, unlike `spawn_env`, so the inherited
    PATH order (the user's own resolution) always wins."""
    env = dict(os.environ)
    current = env.get("PATH", "").split(os.pathsep) if env.get("PATH") else []
    env["PATH"] = os.pathsep.join(dict.fromkeys(current + list(_FALLBACK_BIN_DIRS)))
    return env


# §6 installed-tools probe: automation-relevant CLIs. Curated, not exhaustive —
# the §8 SYSTEM TOOLS header tells the agent an unlisted tool may still exist.
_PROBE_TOOLS = ("gh", "git", "brew", "docker", "node", "npm", "ffmpeg",
                "ffprobe", "yt-dlp", "jq", "pandoc", "sqlite3", "osascript",
                "transmission-remote")


def probe_tools() -> list[dict]:
    """§6: which curated CLIs exist on this Mac, as [{name, path}] — resolved
    against the §6.1 step PATH so the answer matches what a step subprocess
    will find at runtime. Presence + path only (pure stat calls, no version
    subprocesses), so it's cheap enough to run at every prompt build."""
    path = step_env().get("PATH", "")
    out = []
    for name in _PROBE_TOOLS:
        found = shutil.which(name, path=path)
        if found:
            out.append({"name": name, "path": found})
    return out


def invoke(agent: dict, prompt: str, timeout: int | None = None,
           proc_holder: dict | None = None, on_chunk=None,
           should_abort=None, web: bool = False, on_tool=None,
           on_file=None, on_spawn=None) -> str:
    """Invoke the harness once with `prompt`, return its text reply.

    web=True enables the harness's web-read tools (§6 drafting calls only);
    the default keeps every tool disabled — runtime agent.ask calls must
    never pass it. On a §8 file-writing harness web=True also switches the
    call to file-writing delivery (scratch cwd + watcher; the caller adds
    the matching OUTPUT prompt section) and the reply is the recombined
    envelope.
    proc_holder, when given, receives {'proc': Popen} so a caller can cancel.
    should_abort, when given, is re-checked right after the spawn lands in
    proc_holder: a cancel racing the spawn (set after the caller's own check,
    before the Popen existed) would otherwise kill nothing and the call would
    run to its full timeout.
    on_chunk, when given, receives each response-prose `text` event as the
    handler reports it (§8 live progress): Claude Code true deltas, Codex
    per completed agent message, OpenCode per text part, Gemini CLI raw
    stdout lines.
    on_tool, when given, receives each {name, input} `tool` event (§8
    activity feed) — names normalized to WebFetch / WebSearch / Shell where
    the handler knows them.
    on_file, when given, receives (name, content) each time a response
    document lands or grows in a file-writing call's scratch dir (§8).
    Every request is framed in app.log (§5): BEGIN header + prompt on send,
    response (or error) + END footer when the request ends.
    """
    if timeout is None:
        timeout = agent_timeout()
    harness = agent.get("harness")
    model = agent.get("model") or "configured default"
    log.info("agent request · harness=%s · model=%s · prompt (%d chars):\n%s",
             harness or "?", model, len(prompt), prompt)
    req_id = str(uuid.uuid4())
    _app_log(f">>>>> BEGIN {_stamp()} {req_id} <<<<<\n"
             f"agent request · harness={harness or '?'} · model={model}"
             f" · prompt ({len(prompt)} chars):\n{prompt}")
    # §5 request-log files: one file per agent request, stamped at send time so
    # its name sorts where the request began; written when the request ends.
    ts = reqlog.stamp()
    t0 = time.monotonic()
    try:
        out = _invoke(harness, agent, prompt, timeout, proc_holder, on_chunk,
                      should_abort, web, on_tool, on_file, on_spawn)
    except Exception as e:  # noqa: BLE001 — log, close the frame, re-raise
        _app_log(f"request failed: {e}\n>>>>> END {_stamp()} {req_id} <<<<<\n")
        reqlog.write_agent(ts, harness or "?", model, prompt, None, str(e),
                           (time.monotonic() - t0) * 1000)
        raise
    _app_log(f"response ({len(out)} chars):\n{out}\n>>>>> END {_stamp()} {req_id} <<<<<\n")
    reqlog.write_agent(ts, harness or "?", model, prompt, out, None,
                       (time.monotonic() - t0) * 1000)
    return out


# §8 failure policy: a nonzero exit whose stderr names an obvious
# DETERMINISTIC failure — authentication/sign-in trouble or a wrong model
# name — is never retried: a retry can't fix a bad credential or a missing
# model, so drafting surfaces the error immediately instead of costing a
# second multi-minute call. Case-insensitive, matched against the stderr
# tail (the decisive last lines; banners never reach it). Word-bounded where
# a bare substring would misfire — "4013 bytes" must not read as a 401, and
# suppressing the one §8 retry on a transient line is the inverse of the
# policy's intent. Sources:
# Claude Code "Invalid API key · Please run /login", Codex "401 Unauthorized"
# / "You must be logged in", Gemini/OpenCode auth and model-not-found lines.
_DETERMINISTIC_STDERR = tuple(re.compile(p, re.I) for p in (
    r"not logged in",
    r"\blogin\b",
    r"logged out",
    r"unauthorized",
    r"\b401\b",
    r"\b403\b",
    r"authentication",
    r"invalid api key",
    r"api key",
    r"model not found",
    r"model_not_found",
    r"unknown model",
    r"no such model",
))


def _deterministic_failure(stderr_tail: str) -> bool:
    """True when the stderr tail matches an obvious auth / model-not-found
    pattern — the §8 non-retryable classification."""
    return any(pat.search(stderr_tail) for pat in _DETERMINISTIC_STDERR)


# §8 envelope shape constants — canonical here (the recombiner below needs
# them and drafting imports harness, never the reverse); drafting aliases them.
FILE_MARK_RE = re.compile(r"^===FILE: (.+?)===\s*$", re.M)
BLOCKED_MARK_RE = re.compile(r"^===BLOCKED===\s*$", re.M)
STEP_FILE_RE = re.compile(r"^(\d{2})-[a-z0-9][a-z0-9-]*\.py$")
FENCE_OPEN_RE = re.compile(r"^```[\w+.-]*$")


def blocked_mark_outside_fences(text: str) -> re.Match | None:
    """§8 shape-aware blocker detection: the first line-anchored ===BLOCKED===
    that does NOT sit inside a markdown code fence - a fenced marker is quoted
    prose (an answer explaining the format), never an envelope. Canonical here
    so `_recombine` and drafting's parse agree — a naive search here would
    treat a chat reply that *quotes* the marker as blocked and silently drop
    the scratch documents from the recombined envelope."""
    fenced = False
    pos = 0
    for line in text.splitlines(keepends=True):
        bare = line.rstrip("\r\n")
        if FENCE_OPEN_RE.match(bare):
            fenced = not fenced
        elif not fenced and BLOCKED_MARK_RE.match(bare):
            return BLOCKED_MARK_RE.match(text, pos)
        pos += len(line)
    return None

# §8 file-writing delivery: the response-document names the scratch watcher
# accepts — exactly the envelope's file names, flat regular files only, so
# build residue an agent leaves behind (__pycache__, helper scripts) never
# reaches the feed or the recombined reply.
_DOCUMENT_NAMES = ("spec.md", "notes.md", "manifest.yaml", "actions.yaml")
_SCRATCH_POLL_S = 0.3


def _is_document_name(name: str) -> bool:
    return name in _DOCUMENT_NAMES or bool(STEP_FILE_RE.match(name))


class ProgressSink:
    """§8 typed progress events from a per-harness handler to the caller.

    `text` is response prose (a Claude delta, a Codex agent message, an
    OpenCode text part); `tool` a tool use ({name, input} with the handler's
    names normalized to WebFetch / WebSearch / Shell where known); `file` a
    response document landing or growing in the call's scratch dir (name +
    current content). Every event also reports activity, which resets the §8
    idle window — the point of the sink for events that carry no callback."""

    def __init__(self, on_chunk=None, on_tool=None, on_file=None,
                 on_activity=None):
        self._on_chunk = on_chunk
        self._on_tool = on_tool
        self._on_file = on_file
        self._on_activity = on_activity

    def activity(self) -> None:
        if self._on_activity:
            self._on_activity()

    def text(self, chunk: str) -> None:
        self.activity()
        if chunk and self._on_chunk:
            self._on_chunk(chunk)

    def tool(self, name: str, tool_input: dict) -> None:
        self.activity()
        if self._on_tool:
            self._on_tool({"name": name, "input": tool_input})

    def file(self, name: str, content: str) -> None:
        self.activity()
        if self._on_file:
            self._on_file(name, content)


class Handler:
    """§8 per-harness handler, one instance per call: builds the command,
    parses stdout into ProgressSink events, and extracts the final reply.
    `writes_files` marks the harnesses whose one-shot mode can't stream text
    deltas — their drafting calls use the §8 file-writing delivery (OUTPUT
    prompt section + scratch watcher) instead."""

    binname: str
    writes_files = False

    def __init__(self, agent: dict, web: bool):
        self.agent = agent
        self.web = web
        self.model = agent.get("model")
        self.local = agent.get("mode", "default") == "ollama" and bool(self.model)

    def model_args(self) -> list[str]:
        return ["--model", self.model] if self.model else []

    def preflight(self) -> None:
        """Raise HarnessError before the spawn for a condition the call could
        never recover from (only Gemini needs one — see there)."""

    def command(self, prompt: str, pipe_prompt: bool, writing: bool) -> list[str]:
        raise NotImplementedError

    def env(self, env: dict) -> dict:
        return env

    def line(self, line: str, sink: ProgressSink) -> None:
        """One stdout line → sink events. The read loop already resets the
        idle window per line; handlers only translate content."""

    def reply(self, raw: str) -> str:
        """The final reply once the child exited 0; `raw` is full stdout."""
        return raw


class ClaudeCodeHandler(Handler):
    binname = "claude"

    def __init__(self, agent: dict, web: bool):
        super().__init__(agent, web)
        self._deltas: list[str] = []
        self._final: str | None = None

    def command(self, prompt: str, pipe_prompt: bool, writing: bool) -> list[str]:
        prompt_argv = [] if pipe_prompt else ["--", prompt]
        return ["claude", "-p", *self.model_args(),
                "--tools", "WebFetch,WebSearch" if self.web else "",
                "--strict-mcp-config",
                "--no-session-persistence", "--output-format", "stream-json",
                "--include-partial-messages", "--verbose", *prompt_argv]

    def env(self, env: dict) -> dict:
        if self.local:
            # §6: Claude Code local mode — point the CLI at Ollama's
            # Anthropic-compatible API. Bearer auth via ANTHROPIC_AUTH_TOKEN:
            # Ollama's /v1/messages does not reliably accept x-api-key, so an
            # inherited ANTHROPIC_API_KEY must not win the auth pick.
            env["ANTHROPIC_BASE_URL"] = OLLAMA_URL
            env["ANTHROPIC_AUTH_TOKEN"] = "ollama"
            env.pop("ANTHROPIC_API_KEY", None)
        return env

    def line(self, line: str, sink: ProgressSink) -> None:
        chunk, result, tools = _claude_stream_line(line)
        if result is not None:
            self._final = result
        if chunk:
            self._deltas.append(chunk)
            sink.text(chunk)
        for tool in tools:
            sink.tool(tool["name"], tool["input"])

    def reply(self, raw: str) -> str:
        # The result event is authoritative; joined deltas cover a CLI that
        # streamed but never sent one; raw stdout covers non-stream output.
        return self._final if self._final is not None else ("".join(self._deltas) or raw)


class CodexHandler(Handler):
    binname = "codex"
    writes_files = True

    def __init__(self, agent: dict, web: bool):
        super().__init__(agent, web)
        self._messages: list[str] = []

    def command(self, prompt: str, pipe_prompt: bool, writing: bool) -> list[str]:
        prompt_argv = [] if pipe_prompt else ["--", prompt]
        codex_local_args = ["--oss", "--local-provider", "ollama"] if self.local else []
        # --json: JSONL events on stdout (the §8 progress stream; verified
        # against codex-cli 0.144.6). --ephemeral keeps the one-shot call off
        # disk — the same intent as Claude Code's --no-session-persistence.
        # A file-writing drafting call escalates the sandbox to
        # workspace-write, confined to the per-call scratch cwd (§6/§8);
        # every other call keeps read-only. --search must precede `exec`
        # (exec rejects it) — same placement rule as the local-model flags.
        return ["codex", *(["--search"] if self.web else []), *codex_local_args,
                "exec", "--json", "--ephemeral", *self.model_args(),
                "--sandbox", "workspace-write" if writing else "read-only",
                "--skip-git-repo-check", *prompt_argv]

    def line(self, line: str, sink: ProgressSink) -> None:
        try:
            obj = json.loads(line)
        except ValueError:
            return
        if not isinstance(obj, dict):
            return
        item = obj.get("item") if isinstance(obj.get("item"), dict) else {}
        etype = obj.get("type")
        itype = item.get("type")
        if etype == "item.completed" and itype == "agent_message":
            text = str(item.get("text") or "")
            if text:
                # A turn can carry several agent messages — preamble prose,
                # then the final one; reply() takes the last. The sink gets a
                # paragraph break so accumulated prose keeps message
                # boundaries (chunks joined feed the §8 plan capture).
                self._messages.append(text)
                sink.text(text + "\n\n")
        elif etype == "item.started" and itype == "command_execution":
            sink.tool("Shell", {"command": str(item.get("command") or "")})
        elif etype == "item.completed" and itype == "web_search":
            sink.tool("WebSearch", {"query": str(item.get("query") or "")})
        # file_change items and every other parsed line: bare activity — the
        # scratch watcher is the single source of `file` events, so a document
        # written via a shell command still reports and nothing double-reports.

    def reply(self, raw: str) -> str:
        return self._messages[-1] if self._messages else raw


class GeminiHandler(Handler):
    binname = "gemini"
    writes_files = True

    def preflight(self) -> None:
        # §8 failure policy: a signed-out Gemini CLI doesn't exit with an auth
        # error — it prints a browser sign-in prompt to stdout and blocks on
        # it forever (no trailing newline, so line reads never even see it).
        # Fail fast and non-retryably instead of burning the idle window twice.
        if signed_in("gemini") is not True:
            raise HarnessError(
                "Gemini CLI is not signed in — sign in from the Agents page, "
                "then try again")

    def command(self, prompt: str, pipe_prompt: bool, writing: bool) -> list[str]:
        gemini_prompt_argv = [] if pipe_prompt else ["-p", prompt]
        # §8: a file-writing drafting call needs the file-write tools to
        # auto-approve non-interactively (the default mode blocks on an
        # approval prompt); its tools were already all-on in every mode (§6),
        # so this widens nothing the app relied on. Runtime calls stay bare.
        approval_args = ["--approval-mode", "yolo"] if writing else []
        return ["gemini", *self.model_args(), *approval_args,
                *gemini_prompt_argv]

    def line(self, line: str, sink: ProgressSink) -> None:
        # Plain text mode (§8): the reply is raw stdout, and progress comes
        # entirely from the scratch watcher's `file` events — stdout lines
        # are bare activity (idle reset only), never `text` events, so CLI
        # banners and tool chatter can't become "Writing the answer" labels
        # or the captured plan.
        sink.activity()


class OpenCodeHandler(Handler):
    binname = "opencode"
    writes_files = True

    # OpenCode tool names → the normalized names drafting labels (§8).
    _TOOL_NAMES = {"bash": "Shell", "webfetch": "WebFetch",
                   "websearch": "WebSearch", "web_search": "WebSearch"}

    def __init__(self, agent: dict, web: bool):
        super().__init__(agent, web)
        self._texts: list[str] = []

    def model_args(self) -> list[str]:
        if not self.model:
            return []
        return ["--model", f"ollama/{self.model}" if self.local else self.model]

    def command(self, prompt: str, pipe_prompt: bool, writing: bool) -> list[str]:
        prompt_argv = [] if pipe_prompt else ["--", prompt]
        # --format json: JSONL events on stdout (the §8 progress stream;
        # verified against opencode 1.18.4 — file writes need no extra flag).
        return ["opencode", "run", "--format", "json", *self.model_args(),
                *prompt_argv]

    def line(self, line: str, sink: ProgressSink) -> None:
        try:
            obj = json.loads(line)
        except ValueError:
            return
        if not isinstance(obj, dict):
            return
        part = obj.get("part") if isinstance(obj.get("part"), dict) else {}
        etype = obj.get("type")
        if etype == "text":
            text = str(part.get("text") or "")
            if text:
                # Paragraph break for the same reason as Codex's messages —
                # reply() joins self._texts itself.
                self._texts.append(text)
                sink.text(text + "\n\n")
        elif etype == "tool_use":
            name = str(part.get("tool") or "")
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            tool_input = state.get("input") if isinstance(state.get("input"), dict) else {}
            if name in ("write", "edit"):
                # The scratch watcher owns file reporting — a write tool event
                # would double-report the same document.
                sink.activity()
            else:
                sink.tool(self._TOOL_NAMES.get(name, name or "a tool"), tool_input)
        # step_start / step_finish and every other parsed line: bare activity
        # (the read loop's per-line idle reset already covers it).

    def reply(self, raw: str) -> str:
        return "\n\n".join(self._texts) if self._texts else raw


HANDLERS: dict[str, type[Handler]] = {
    "Claude Code": ClaudeCodeHandler,
    "Codex": CodexHandler,
    "Gemini CLI": GeminiHandler,
    "OpenCode": OpenCodeHandler,
}


def writes_files(harness_name: str) -> bool:
    """§8: whether this harness's drafting calls use file-writing delivery
    (the OUTPUT prompt section + scratch watcher)."""
    cls = HANDLERS.get(harness_name)
    return bool(cls and cls.writes_files)


class _ScratchWatcher:
    """§8 scratch watcher: polls the call's scratch dir every 0.3 s, emits a
    `file` event when a response document lands or grows (name + current
    content), and keeps first-seen order for the recombined envelope. A final
    `stop()` sweep catches a document written in the last poll interval."""

    def __init__(self, scratch: Path, sink: ProgressSink):
        self._scratch = scratch
        self._sink = sink
        self._order: list[str] = []
        # (size, mtime_ns) — size alone would miss a same-length rewrite.
        self._stamps: dict[str, tuple[int, int]] = {}
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=5)
        if not self._thread.is_alive():
            # Final sweep — only after a successful join, so never concurrent
            # with the poll thread. A timed-out join (a _read stalled on a
            # huge or network-backed file) skips it: the worst case is a lost
            # last-interval `file` feed event — documents() re-reads from
            # disk, so the recombined envelope is complete either way.
            self._poll()

    def _run(self) -> None:
        while not self._stop_event.wait(_SCRATCH_POLL_S):
            self._poll()

    def _read(self, name: str) -> str:
        try:
            return (self._scratch / name).read_text(encoding="utf-8",
                                                    errors="replace")
        except OSError:
            return ""

    def _poll(self) -> None:
        try:
            entries = list(os.scandir(self._scratch))
        except OSError:
            return
        for entry in sorted(entries, key=lambda e: e.name):
            if not _is_document_name(entry.name):
                continue
            try:
                if not entry.is_file(follow_symlinks=False):
                    continue
                st = entry.stat(follow_symlinks=False)
                stamp = (st.st_size, st.st_mtime_ns)
            except OSError:
                continue
            if self._stamps.get(entry.name) == stamp:
                continue
            self._stamps[entry.name] = stamp
            if entry.name not in self._order:
                self._order.append(entry.name)
            self._sink.file(entry.name, self._read(entry.name))

    def documents(self) -> list[tuple[str, str]]:
        """(name, content) in first-seen order — call after stop()."""
        return [(name, self._read(name)) for name in self._order
                if (self._scratch / name).is_file()]


def _recombine(stdout_text: str, documents: list[tuple[str, str]]) -> str:
    """§8 file-writing delivery: stdout prose + collected documents → the
    ordinary envelope, so validation, repair, logging, and the §5 audit
    framing all see one canonical text.

    A line-anchored ===BLOCKED=== on stdout wins outright — blockers ride
    stdout by contract (a fenced quote of the marker alongside written files
    would mispick here; the drafting parser then fails it and a repair round
    corrects — accepted, the shape-aware parse lives in drafting). An empty
    scratch falls back to stdout unchanged (an answer-only chat reply, or an
    agent that ignored the OUTPUT section and printed the envelope)."""
    if blocked_mark_outside_fences(stdout_text):
        return stdout_text
    if not documents:
        return stdout_text
    marker = FILE_MARK_RE.search(stdout_text)
    prose = (stdout_text[:marker.start()] if marker else stdout_text).strip()
    parts = [prose] if prose else []
    for name, content in documents:
        body = content.rstrip("\n")
        parts.append(f"===FILE: {name}===\n{body}\n")
    parts.append("===END===")
    return "\n".join(parts)


def _make_scratch(provider_id: str) -> Path:
    d = paths.harness_scratch(provider_id) / str(uuid.uuid4())
    d.mkdir(parents=True, exist_ok=True)
    return d


def clear_scratch() -> None:
    """§5 startup sweep: remove whole scratch/ trees a crashed backend left
    behind (a live call's dir is removed by its own finally instead)."""
    for provider_id, _name in PROVIDERS:
        d = paths.harness_scratch(provider_id)
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)


def _claude_stream_line(line: str) -> tuple[str | None, str | None, list[dict]]:
    """One `--output-format stream-json` stdout line → (text_chunk, final_result,
    tool_uses).

    Partial text arrives as stream_event/content_block_delta/text_delta chunks
    (`--include-partial-messages`); the terminal `result` event carries the
    complete reply; an `assistant` message's tool_use blocks become
    `[{name, input}, …]` (§8 live-progress events). Anything else — init
    events, tool results, non-JSON — is (None, None, [])."""
    try:
        obj = json.loads(line)
    except ValueError:
        return None, None, []
    if not isinstance(obj, dict):
        return None, None, []
    if obj.get("type") == "stream_event":
        event = obj.get("event") or {}
        delta = event.get("delta") or {}
        if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
            return delta.get("text") or "", None, []
        return None, None, []
    if obj.get("type") == "assistant":
        content = (obj.get("message") or {}).get("content")
        tools = [{"name": b.get("name") or "", "input": b.get("input") or {}}
                 for b in (content if isinstance(content, list) else [])
                 if isinstance(b, dict) and b.get("type") == "tool_use"]
        return None, None, tools
    if obj.get("type") == "result" and isinstance(obj.get("result"), str):
        return None, obj["result"], []
    return None, None, []


def _invoke(harness: str | None, agent: dict, prompt: str, timeout: int,
            proc_holder: dict | None, on_chunk=None, should_abort=None,
            web: bool = False, on_tool=None, on_file=None, on_spawn=None) -> str:
    # §4.7/§6: a local-model agent (mode ollama — Claude Code, Codex, or
    # OpenCode) drives the one local Ollama server through that harness's own
    # supported mechanism: OpenCode rides `--model ollama/<model>` after the
    # §19 opencode.json provider sync; Codex rides its official
    # `--oss --local-provider ollama` top-level flags; Claude Code rides its
    # custom-endpoint env vars against Ollama's Anthropic-compatible API
    # (the handler's env()). A custom-model agent passes the user-typed
    # string verbatim as `--model` (the same flag on all four CLIs), never
    # validated by the app.
    handler_cls = HANDLERS.get(harness or "")
    if not handler_cls:
        raise HarnessError(f"unknown harness: {harness}")
    handler = handler_cls(agent, web)
    if harness == "OpenCode" and handler.local:
        sync_opencode_ollama(handler.model)
    handler.preflight()
    # §8 file-writing delivery: only drafting calls (web=True) on a harness
    # that can't stream text deltas — runtime agent.ask never writes files
    # and Codex stays in its read-only sandbox there.
    writing = web and handler.writes_files
    # §8 prompt delivery is per-OS. POSIX: the prompt rides as the command's
    # last argv element (behind `--`, so an LLM-written prompt starting with
    # "-" never parses as a flag; Gemini's rides `-p`). Windows: the whole
    # command line is capped at 32,767 characters — smaller than any real
    # drafting prompt (a minimal build prompt already measures ~38 K), so
    # every spawn would die with `[WinError 206]`. There the handler omits
    # the argv prompt and pipes it to the child's stdin instead (every §8
    # CLI has a non-interactive piped-stdin mode).
    pipe_prompt = paths.current_os() == "windows"
    cmd = handler.command(prompt, pipe_prompt, writing)
    binpath = resolve_bin(cmd[0])
    if binpath is None:
        raise HarnessError(f"{cmd[0]} is not installed on this {paths.machine_noun()}")
    cmd[0] = binpath
    env = handler.env(spawn_env(binpath))
    # §5/§8: a file-writing call runs in its own per-call scratch dir — the
    # documents the agent writes land where the watcher owns them; everything
    # else keeps the provider's empty workspace (same TCC argument, §6).
    scratch = _make_scratch(HARNESS_ID[harness]) if writing else None
    cwd = str(scratch) if scratch else _neutral_cwd(HARNESS_ID[harness])
    # Own session, like engine steps (§2 platform session policy):
    # timeout/cancel must reach helper processes the CLI spawns — killing
    # only the direct child can leave a helper holding the stdout pipe open
    # (read loop never sees EOF, the §8 idle window silently never fires).
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                # §8 per-OS prompt delivery: a pipe on Windows
                                # (the prompt goes down it), /dev/null everywhere
                                # else (the prompt is already in argv).
                                stdin=subprocess.PIPE if pipe_prompt else subprocess.DEVNULL,
                                # §2 pipe-encoding contract: never the locale
                                # codec. The stdin text write below inherits it.
                                encoding="utf-8", errors="replace",
                                env=env, cwd=cwd,
                                **platform.current().processes.session_kwargs())
    except BaseException:
        # The main cleanup finally starts after the spawn — a failing spawn
        # must not leak the scratch dir until the next startup sweep.
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)
        raise
    if proc_holder is not None:
        proc_holder["proc"] = proc
    if on_spawn is not None:
        # §7 kill semantics: a runtime agent.ask caller reports this child's
        # own-session group to the engine so a step kill can reach it. Called
        # on the invoking thread, right at spawn — the whole point is that
        # the group is known while the call is still in flight.
        try:
            on_spawn(proc)
        except Exception:  # noqa: BLE001 — a reporting failure must not kill the call
            log.exception("on_spawn callback failed")

    def _close_stdin() -> None:
        """Idempotent; safe from any thread (a close racing the writer's own
        close, or the kill path, must never raise)."""
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except (OSError, ValueError):
            pass

    if pipe_prompt:
        # §8: a DEDICATED writer thread, started right after the spawn — never
        # the stdout read loop's thread. A ~40 K prompt overflows the stdin
        # pipe buffer, so the write blocks until the child drains it; a child
        # that fills its own stdout pipe first would then deadlock against a
        # reader that is busy writing.
        def _write_prompt() -> None:
            try:
                proc.stdin.write(prompt)  # type: ignore[union-attr]
                proc.stdin.flush()  # type: ignore[union-attr]
            except (OSError, ValueError):
                # BrokenPipeError (the child exited or was killed before it
                # read the prompt) and ValueError (the kill path closed the
                # pipe underneath us) are both normal ends, not failures —
                # the exit code and stderr carry the real story.
                pass
            finally:
                _close_stdin()  # EOF: every §8 CLI waits for it

        threading.Thread(target=_write_prompt, daemon=True).start()
    # Cancel/spawn race: a cancel that landed after the caller's own check but
    # before this Popen existed killed nothing — re-check now that the proc is
    # visible, so no harness call can outlive a cancel by its full timeout.
    if should_abort is not None and should_abort():
        kill_group(proc)
    # §8 live progress: read stdout as it streams instead of communicate();
    # the timeout is enforced by a watchdog that kills the group (readline
    # then sees EOF), and stderr drains on its own thread so a chatty child
    # can't deadlock on a full pipe.
    timed_out = threading.Event()

    def _kill() -> None:
        timed_out.set()
        kill_group(proc)
        # An escaped child could still hold the pipe — swap our read end for
        # /dev/null so any further read sees EOF. Never `.close()` from this
        # thread: TextIOWrapper.close() takes the buffer lock a blocked
        # readline holds, which would wedge this watchdog instead of freeing
        # the loop.
        defuse_read_end(proc.stdout)
        # §8 Windows delivery: a writer thread blocked on a prompt the dead
        # child will never drain unblocks on the closed pipe (and swallows
        # the resulting error), so the kill leaks no thread and no handle.
        _close_stdin()

    # §8: `timeout` is an idle window — every stdout line pushes the deadline
    # out, so a call still streaming keeps running (a harness that buffers its
    # whole output gets no resets and the window degrades to a fixed timeout).
    # The hard cap bounds total wall clock even when output never stops.
    hard_cap = agent_hard_cap()
    start = time.monotonic()
    idle_deadline = [start + timeout]
    hard_deadline = start + hard_cap
    hard_capped = threading.Event()
    done = threading.Event()

    def _watch() -> None:
        while not done.is_set():
            now = time.monotonic()
            if now >= hard_deadline:
                hard_capped.set()
                _kill()
                return
            if now >= idle_deadline[0]:
                _kill()
                return
            # Sleep to the nearer deadline; a reset moves idle_deadline
            # forward, so waking at the stale deadline just re-checks and
            # sleeps again.
            done.wait(min(idle_deadline[0], hard_deadline) - now)

    watchdog = threading.Thread(target=_watch, daemon=True)
    watchdog.start()
    err_parts: list[str] = []

    def _drain_stderr() -> None:
        # Bounded, tail-keeping: an unbounded read() holds however much a
        # chatty child emits in backend memory. The decisive error lines come
        # LAST (banners first), so overflow drops the oldest chunks.
        total = 0
        try:
            while True:
                chunk = proc.stderr.read(65536)  # type: ignore[union-attr]
                if not chunk:
                    return
                err_parts.append(chunk)
                total += len(chunk)
                while total > STDERR_CAP_CHARS and len(err_parts) > 1:
                    total -= len(err_parts.pop(0))
        except (OSError, ValueError):
            # The cleanup below closed our read end mid-read — normal end.
            pass

    drain = threading.Thread(target=_drain_stderr, daemon=True)
    drain.start()
    raw_parts: list[str] = []

    def _reset_idle() -> None:
        idle_deadline[0] = time.monotonic() + timeout

    # §8 progress sink: handler events → the caller's callbacks. Every event
    # resets the idle window — the scratch watcher's file events are the only
    # progress channel for a harness whose stdout stays silent mid-call.
    sink = ProgressSink(on_chunk=on_chunk, on_tool=on_tool, on_file=on_file,
                        on_activity=_reset_idle)
    scratch_watcher: _ScratchWatcher | None = None
    try:
        if scratch is not None:
            scratch_watcher = _ScratchWatcher(scratch, sink)
            scratch_watcher.start()
        try:
            out_total = 0
            try:
                for line in proc.stdout:
                    _reset_idle()
                    out_total += len(line)
                    if out_total > STDOUT_CAP_CHARS:
                        # §8 stream cap: the idle window never fires on a call
                        # that keeps streaming, so a harness stuck in a tool
                        # loop could push the full hard cap's worth of output
                        # through backend memory and every log sink. No valid
                        # response is anywhere near this large.
                        raise HarnessError(
                            f"{harness} produced over "
                            f"{STDOUT_CAP_CHARS // 1_000_000} MB of output — aborting")
                    raw_parts.append(line)
                    handler.line(line, sink)
            except ValueError:
                # The timeout kill closed our read end — anything else is real.
                if not timed_out.is_set():
                    raise
            proc.wait()
        except BaseException:
            # A raising callback (or any read error) must not orphan the
            # child — the timer gets cancelled below, so nothing else would
            # ever reap it.
            kill_group(proc)
            proc.wait()
            raise
        finally:
            done.set()
            # Closed on EVERY path (the writer normally got here first, and
            # the call is idempotent), so no handle is left open on a
            # long-lived backend — mirrors the engine's step-process pipe
            # hygiene.
            _close_stdin()
            if scratch_watcher is not None:
                # §8: the final sweep — a document written in the last poll
                # interval still reaches the feed and the recombined reply.
                scratch_watcher.stop()
        drain.join(timeout=5)
        if timed_out.is_set() and proc.returncode != 0:
            # returncode guard: a watchdog firing in the instant after a
            # successful exit must not discard a complete valid reply.
            if hard_capped.is_set():
                raise HarnessError(f"{harness} timed out after {hard_cap}s total",
                                   retryable=True)
            raise HarnessError(f"{harness} timed out after {timeout}s without output",
                               retryable=True)
        raw = "".join(raw_parts)
        if proc.returncode != 0:
            err = "".join(err_parts) or raw
            # The TAIL of stderr, not the head: CLIs print banners first and
            # the decisive ERROR line last (verified with Codex).
            tail = "\n".join(err.strip().splitlines()[-3:])
            raise HarnessError(f"{harness} failed: {tail[-400:]}",
                               retryable=not _deterministic_failure(tail))
        out = handler.reply(raw)
        if scratch_watcher is not None:
            # §8: recombine stdout prose + collected documents into the
            # ordinary envelope — validation and the audit trail see one
            # canonical text.
            out = _recombine(out, scratch_watcher.documents())
        return out
    finally:
        # Both pipes close on EVERY path — same hygiene as the engine's step
        # process. The last Popen is retained on the draft job (proc_holder)
        # for its whole ack lifetime, so without this two fds leak per held
        # job on the long-lived backend.
        for pipe in (proc.stdout, proc.stderr):
            try:
                if pipe is not None:
                    pipe.close()
            except (OSError, ValueError):
                pass
        if scratch is not None:
            shutil.rmtree(scratch, ignore_errors=True)


_OPENCODE_CONFIG = os.path.expanduser("~/.config/opencode/opencode.json")
_opencode_cfg_lock = threading.Lock()


def sync_opencode_ollama(model: str) -> None:
    """§19: merge the Ollama provider entry into `~/.config/opencode/opencode.json`
    so `opencode run --model ollama/<model>` resolves. Merge only — the user's
    other config keys are never touched, and nothing is written when the entry
    is already in place."""
    with _opencode_cfg_lock:  # two agent steps must not race the read-modify-write
        _sync_opencode_ollama_locked(model)


def _sync_opencode_ollama_locked(model: str) -> None:
    try:
        with open(_OPENCODE_CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            cfg = {}
    except OSError:
        cfg = {}
    except ValueError:
        # A corrupt (e.g. half-written) config must not be silently replaced
        # with only our entry — that would discard the user's other keys.
        # Preserve the bytes next door and start clean.
        try:
            os.replace(_OPENCODE_CONFIG, _OPENCODE_CONFIG + ".corrupt")
            log.warning("opencode.json was not valid JSON — kept as opencode.json.corrupt")
        except OSError:
            pass
        cfg = {}
    before = json.dumps(cfg, sort_keys=True)
    provider = cfg.setdefault("provider", {})
    if not isinstance(provider, dict):
        provider = cfg["provider"] = {}
    entry = provider.setdefault("ollama", {})
    if not isinstance(entry, dict):
        entry = provider["ollama"] = {}
    entry.setdefault("npm", "@ai-sdk/openai-compatible")
    entry.setdefault("name", "Ollama (local)")
    options = entry.setdefault("options", {})
    if not isinstance(options, dict):
        options = entry["options"] = {}
    options["baseURL"] = f"{OLLAMA_URL}/v1"
    models = entry.setdefault("models", {})
    if not isinstance(models, dict):
        models = entry["models"] = {}
    models.setdefault(model, {"name": model})
    if json.dumps(cfg, sort_keys=True) == before:
        return
    try:
        # Atomic (§5 pattern): a crash mid-write must never truncate the
        # user's config file.
        atomic_write_text(Path(_OPENCODE_CONFIG), json.dumps(cfg, indent=2) + "\n")
    except OSError as e:
        raise HarnessError(f"couldn't update opencode.json: {e}") from e


# §19: on macOS the app may sit in the system or the per-user Applications
# folder; on Windows its per-user installer lands in %LOCALAPPDATA%\Programs.
_POSIX_OLLAMA_APP_BINS = tuple(
    os.path.join(apps, "Ollama.app/Contents/Resources/ollama")
    for apps in ("/Applications", os.path.expanduser("~/Applications")))
_WINDOWS_OLLAMA_APP_BINS = (
    os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local"),
        "Programs", "Ollama", "ollama.exe"),
)
_OLLAMA_APP_BINS = (
    _WINDOWS_OLLAMA_APP_BINS if paths.current_os() == "windows"
    else _POSIX_OLLAMA_APP_BINS
)


def ollama_bin() -> str | None:
    found = resolve_bin("ollama")
    if found:
        return found
    for path in _OLLAMA_APP_BINS:
        if _is_executable(path):
            return path
    return None


def _ollama_models() -> list[str] | None:
    """Model names if the server answers, else None."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/tags", timeout=2) as r:
            tags = json.loads(r.read().decode())
        return [m["name"] for m in tags.get("models", [])]
    except Exception:  # noqa: BLE001
        return None


def _ollama_version() -> str | None:
    """The server's version string if it answers, else None (§19)."""
    try:
        with urllib.request.urlopen(f"{OLLAMA_URL}/api/version", timeout=2) as r:
            return str(json.loads(r.read().decode()).get("version") or "") or None
    except Exception:  # noqa: BLE001
        return None


# §19/§6: Ollama's Anthropic-compatible /v1/messages endpoint (what a Claude
# Code local-model agent talks to) shipped in 0.14.0.
OLLAMA_MIN_ANTHROPIC = (0, 14, 0)


def version_at_least(version: str | None, floor: tuple[int, ...]) -> bool:
    """Compare a dotted version string's leading numeric parts against
    `floor`. Unknown/unparseable versions read False — the §19 check answers
    needs-setup rather than letting the invoke fail later."""
    if not version:
        return False
    parts: list[int] = []
    for piece in version.strip().lstrip("v").split("."):
        digits = ""
        for ch in piece:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        parts.append(int(digits))
    if not parts:
        return False
    return tuple(parts) >= floor


_serve_last_spawn = 0.0
_SERVE_COOLDOWN_S = 30.0


def ollama_status() -> dict:
    global _serve_last_spawn
    models = _ollama_models()
    binpath = ollama_bin()
    local = "localhost" in OLLAMA_URL or "127.0.0.1" in OLLAMA_URL
    if (models is None and binpath and local
            and time.time() - _serve_last_spawn > _SERVE_COOLDOWN_S):
        # Installed but the server isn't up — start it and wait. A cooldown
        # instead of a once-latch: the server dying later (or a failed spawn)
        # must not disable the self-heal for the backend's whole lifetime.
        _serve_last_spawn = time.time()
        try:
            subprocess.Popen([binpath, "serve"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             env=spawn_env(binpath), cwd=_neutral_cwd("ollama"),
                             **platform.current().processes.session_kwargs())
        except Exception:  # noqa: BLE001
            pass
        else:
            for _ in range(10):
                time.sleep(0.3)
                models = _ollama_models()
                if models is not None:
                    break
    return {"ready": models is not None,
            "installed": models is not None or binpath is not None,
            "models": models or [],
            "version": _ollama_version() if models is not None else None}


def ollama_model_installed(model: str, installed: list[str]) -> bool:
    """A bare name without a tag matches its `:latest` variant."""
    if model in installed:
        return True
    return ":" not in model and f"{model}:latest" in installed


def grant_name(agent: dict) -> str:
    """§8 grant name of an agent record — the name steps and grants yaml use
    to refer to it (falls back to the harness name when the agent is unnamed)."""
    return agent.get("name") or agent.get("harness", "")


# §4.7: the harnesses local-model mode (mode ollama) is valid with. Gemini CLI
# is excluded — the stock CLI speaks only the Gemini wire format and has no
# local or OpenAI-compatible endpoint support.
LOCAL_MODEL_HARNESSES = ("Claude Code", "Codex", "OpenCode")

# Provider ids (§19 install/login/signin endpoints, §10 cards) ↔ harness names.
# Ollama is an installable provider but never a harness (§4.7) — it's the
# local-model runtime the §4.7 local-model harnesses drive.
PROVIDERS: tuple[tuple[str, str], ...] = (
    ("claude", "Claude Code"),
    ("ollama", "Ollama"),
    ("codex", "Codex"),
    ("gemini", "Gemini CLI"),
    ("opencode", "OpenCode"),
)
PROVIDER_NAME = dict(PROVIDERS)
PROVIDER_BIN = {"claude": "claude", "codex": "codex", "gemini": "gemini",
                "opencode": "opencode", "ollama": "ollama"}
HARNESS_ID = {name: pid for pid, name in PROVIDERS if pid != "ollama"}


def _status_ok(cmd: list[str], provider_id: str) -> bool:
    try:
        # §2 spawn policy via session_kwargs: on Windows the windowless
        # backend's console children each open a terminal window without it.
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                encoding="utf-8", errors="replace",  # §2 pipe-encoding contract
                                env=spawn_env(cmd[0]), cwd=_neutral_cwd(provider_id),
                                **platform.current().processes.session_kwargs())
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            # The child has its own session: `run`'s kill would reach only the
            # direct child, then block forever on a grandchild holding stdout.
            kill_group(proc)
            try:
                proc.communicate(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            return False
        return proc.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def signed_in(provider_id: str) -> bool | None:
    """§19 per-harness sign-in rule. None means the provider needs no account
    (Ollama); False for an account-backed provider that isn't installed."""
    if provider_id == "ollama":
        return None
    if provider_id == "claude":
        binpath = resolve_bin("claude")
        return bool(binpath) and _status_ok([binpath, "auth", "status"], "claude")
    if provider_id == "codex":
        binpath = resolve_bin("codex")
        return bool(binpath) and _status_ok([binpath, "login", "status"], "codex")
    if provider_id == "gemini":
        # §19: an API key alone counts as signed in; otherwise oauth_creds.json
        # must parse as JSON carrying a refresh token — file existence never
        # counts (a stale/empty/garbage file must not fake a working sign-in).
        if os.environ.get("GEMINI_API_KEY"):
            return True
        try:
            with open(os.path.expanduser("~/.gemini/oauth_creds.json"),
                      encoding="utf-8") as f:
                creds = json.load(f)
        except (OSError, ValueError):
            return False
        return isinstance(creds, dict) and bool(str(creds.get("refresh_token") or "").strip())
    if provider_id == "opencode":
        # §19: auth.json maps provider id → credential entry. The OpenCode CLI
        # writes { type: api, key } / { type: oauth, access, refresh, expires }
        # / { type: wellknown, key, token } shapes — signed in means at least
        # one entry is a dict carrying a non-empty token-like field (key /
        # token / access / refresh); a credential-less or unparseable file
        # reads signed out.
        try:
            with open(os.path.expanduser("~/.local/share/opencode/auth.json"),
                      encoding="utf-8") as f:
                creds = json.load(f)
        except (OSError, ValueError):
            return False
        if not isinstance(creds, dict):
            return False
        return any(isinstance(entry, dict)
                   and any(str(entry.get(k) or "").strip()
                           for k in ("key", "token", "access", "refresh"))
                   for entry in creds.values())
    return False


def signin_state(provider_id: str) -> dict:
    """§19 `GET /agents/signin/{id}` — cheap poll, no version lookups."""
    if provider_id == "ollama":
        st = ollama_status()
        return {"installed": st["installed"], "signedIn": None}
    binpath = resolve_bin(PROVIDER_BIN[provider_id])
    return {"installed": binpath is not None, "signedIn": signed_in(provider_id)}


def check_ready(harness_name: str, model: str | None = None,
                mode: str = "default") -> bool:
    """The single readiness check behind §19 `/agents/{id}/check` and
    `/agents/check-harness`.

    Ready means the harness can take a prompt right now: the binary resolves.
    A local-model agent (mode ollama — Claude Code, Codex, or OpenCode, §4.7)
    additionally needs the Ollama server answering and the model installed —
    and no sign-in, a local model needs no account. Claude Code additionally
    needs Ollama ≥ 0.14.0 (the Anthropic-compatible endpoint it talks to, §6);
    OpenCode additionally needs the opencode.json provider sync to land.
    Default- and custom-mode checks instead
    require the harness to be signed in by the §19 per-harness rule; the
    custom-mode model string is never validated (§4.7) — a wrong name
    surfaces at invoke time.
    """
    pid = HARNESS_ID.get(harness_name)
    if not pid:
        return False
    if resolve_bin(PROVIDER_BIN[pid]) is None:
        return False
    if mode == "ollama":
        if harness_name not in LOCAL_MODEL_HARNESSES or not model:
            return False
        st = ollama_status()
        if not st["ready"] or not ollama_model_installed(model, st["models"]):
            return False
        if (harness_name == "Claude Code"
                and not version_at_least(st["version"], OLLAMA_MIN_ANTHROPIC)):
            return False
        if harness_name == "OpenCode":
            try:
                sync_opencode_ollama(model)
            except HarnessError:
                # §19: check endpoints answer ready/needs-setup, never 500 —
                # an unwritable opencode.json is a needs-setup condition.
                return False
        return True
    return signed_in(pid) is True


def detect() -> list[dict]:
    """§10 step 2 — one entry per harness, all four always present, with real
    installed and sign-in state (§19). Ollama is not part of detection — the
    §10 Free local AI card reads its state from `/ollama/status`."""
    def version_of(binpath: str, pid: str) -> str | None:
        try:
            # §2 spawn policy via session_kwargs (hidden console on Windows).
            proc = subprocess.Popen([binpath, "--version"],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    encoding="utf-8", errors="replace",  # §2 pipe-encoding contract
                                    env=spawn_env(binpath), cwd=_neutral_cwd(pid),
                                    **platform.current().processes.session_kwargs())
            try:
                out, err = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                # The child has its own session: `run`'s kill would reach only
                # the direct child, then block forever on a grandchild holding
                # stdout.
                kill_group(proc)
                try:
                    proc.communicate(timeout=5)
                except Exception:  # noqa: BLE001
                    pass
                return None
            return (out or err).strip().splitlines()[0][:40] if proc.returncode == 0 else None
        except Exception:  # noqa: BLE001
            return None

    out = []
    for pid, name in PROVIDERS:
        if pid == "ollama":
            continue
        binpath = resolve_bin(PROVIDER_BIN[pid])
        if not binpath:
            out.append({"id": pid, "name": name, "installed": False,
                        "signedIn": False, "detail": ""})
            continue
        s = signed_in(pid) is True
        v = version_of(binpath, pid)
        detail = f"{v or 'installed'} · {'signed in' if s else 'not signed in yet'}"
        out.append({"id": pid, "name": name, "installed": True,
                    "signedIn": s, "detail": detail})
    return out
