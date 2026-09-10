"""Harness adapters (§6/§8): query-only invocation flags + CLI detection."""
import io
import json
import os
import threading
import time
from pathlib import Path

import pytest
from conftest import fake_cli


# §8 prompt delivery is per-OS: the prompt is the command's last argv element
# on POSIX, and is absent from argv — piped to the child's stdin — on Windows,
# where the 32,767-char command-line cap can't hold a drafting prompt.
PIPES_PROMPT = os.name == "nt"

PROMPT = "question: hi?"


class _FakeStdin(io.StringIO):
    """The child's stdin pipe. Records what the §8 writer thread sent and
    signals the fake stdout, which — like a real CLI — answers only once it
    has the prompt."""

    def __init__(self, proc):
        super().__init__()
        self._proc = proc

    def write(self, text):
        n = super().write(text)
        self._proc.stdin_text += text
        self._proc.prompt_written.set()
        return n

    def close(self):
        # The writer closes at EOF and invoke()'s finally closes again; neither
        # may lose what was written (the assertions run after both).
        self._proc.stdin_closed = True
        super().close()


class _FakeStdout:
    """Reply stream that yields nothing until the prompt has been delivered,
    so the §8 writer thread can never lose its race with the read loop. Read
    through bounded `readline(limit)` calls, exactly as the real pipe is."""

    def __init__(self, proc, text="ok"):
        self._proc = proc
        self._rest = text

    def readline(self, limit=-1):
        if not self._rest:
            return ""
        assert self._proc.prompt_written.wait(10), \
            "the §8 Windows prompt-writer thread never wrote to stdin"
        nl = self._rest.find("\n")
        end = len(self._rest) if nl < 0 else nl + 1
        if limit is not None and limit >= 0:
            end = min(end, limit)
        out, self._rest = self._rest[:end], self._rest[end:]
        return out

    def close(self):
        pass


class _FakeProc:
    """Streamed-read stand-in (§8): invoke() iterates stdout, drains stderr on
    a thread, then wait()s — no communicate()."""
    returncode = 0

    def __init__(self):
        self.stdin_text = ""
        self.stdin_closed = False
        # POSIX delivers the prompt in argv, so nothing ever waits on it.
        self.prompt_written = threading.Event()
        if not PIPES_PROMPT:
            self.prompt_written.set()
        self.stdout = _FakeStdout(self)
        self.stderr = io.StringIO("")
        self.stdin = _FakeStdin(self)

    def wait(self, timeout=None):
        return 0

    def poll(self):
        return 0

    def kill(self):
        pass


def _assert_prompt_delivered(cmd, proc, prompt=PROMPT):
    """The §8 per-OS delivery rule, asserted against one captured spawn."""
    if PIPES_PROMPT:
        assert prompt not in cmd, "Windows: the prompt must never reach argv (§8)"
        assert "--" not in cmd, "the `--` separator goes with the positional prompt"
        assert proc.stdin_text == prompt
        assert proc.stdin_closed, "stdin must be closed so the child sees EOF"
    else:
        assert cmd[-1] == prompt
        assert proc.stdin_text == ""


def _captured_invoke(monkeypatch, agent, web=False):
    return _captured_invoke_full(monkeypatch, agent, web=web)["cmd"]


def _gemini_signed_in(monkeypatch):
    """§8: the Gemini handler's pre-flight refuses to spawn a signed-out CLI
    (it would block forever on the browser sign-in prompt) — every Gemini
    spawn test has to look signed in first."""
    from autowright import harness

    monkeypatch.setattr(harness, "signed_in", lambda pid: True)


def test_claude_invoked_with_no_tools_flags(monkeypatch):
    cap = _captured_invoke_full(monkeypatch, {"harness": "Claude Code"})
    cmd = cap["cmd"]
    assert cmd[0] == "/usr/local/bin/claude" and "-p" in cmd
    i = cmd.index("--tools")
    assert cmd[i + 1] == ""  # all built-in tools disabled
    assert "--strict-mcp-config" in cmd
    assert "--no-session-persistence" in cmd
    # §8 live progress: partial text streams as stream-json deltas
    j = cmd.index("--output-format")
    assert cmd[j + 1] == "stream-json"
    assert "--include-partial-messages" in cmd
    assert "--verbose" in cmd  # stream-json in print mode requires it
    _assert_prompt_delivered(cmd, cap["proc"])


def test_fake_cli_streams_chunks_and_result():
    # §8 live progress: the fake CLI answers stream-json — on_chunk sees each
    # text delta and the returned text comes from the terminal result event.
    from autowright import harness

    chunks = []
    out = harness.invoke({"harness": "Claude Code"}, "question: hi?",
                         on_chunk=chunks.append)
    assert out == "Mock answer: nothing new."
    assert chunks and "".join(chunks) == out


def test_opencode_local_model_invoked_with_ollama_model_flag(monkeypatch):
    # §4.7: a local-model agent rides in as `--model ollama/<model>` after the
    # §19 opencode.json provider sync.
    from autowright import harness

    synced = []
    monkeypatch.setattr(harness, "sync_opencode_ollama", synced.append)
    cap = _captured_invoke_full(monkeypatch, {"harness": "OpenCode", "mode": "ollama",
                                              "model": "qwen3:8b"})
    cmd = cap["cmd"]
    assert cmd[:2] == ["/usr/local/bin/opencode", "run"]
    i = cmd.index("--model")
    assert cmd[i + 1] == "ollama/qwen3:8b"
    _assert_prompt_delivered(cmd, cap["proc"])
    assert synced == ["qwen3:8b"]

    cmd = _captured_invoke(monkeypatch, {"harness": "OpenCode"})
    assert "--model" not in cmd  # default mode: no model is ever passed
    assert synced == ["qwen3:8b"]  # and no sync either


def _captured_invoke_full(monkeypatch, agent, web=False):
    """Like _captured_invoke but also captures the spawn kwargs (env, stdin)
    and the fake child itself (for the §8 stdin-delivery assertions)."""
    from autowright import harness

    monkeypatch.setattr(harness, "resolve_bin", lambda name: f"/usr/local/bin/{name}")
    captured = {}

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw.get("env")
        captured["stdin"] = kw.get("stdin")
        proc = _FakeProc()
        captured["proc"] = proc
        return proc

    monkeypatch.setattr(harness.subprocess, "Popen", fake_popen)
    out = harness.invoke(agent, PROMPT, web=web)
    assert out == "ok"
    return captured


def test_claude_local_model_invoked_via_ollama_env(monkeypatch):
    # §6: a Claude Code local-model agent rides the CLI's custom-endpoint env
    # vars against Ollama's Anthropic-compatible API — bare `--model` (no
    # ollama/ prefix), bearer auth, and never an inherited ANTHROPIC_API_KEY
    # (Ollama doesn't reliably accept x-api-key).
    from autowright import harness

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-real-key")
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    synced = []
    monkeypatch.setattr(harness, "sync_opencode_ollama", synced.append)
    cap = _captured_invoke_full(monkeypatch, {"harness": "Claude Code",
                                              "mode": "ollama", "model": "qwen3:8b"})
    cmd, env = cap["cmd"], cap["env"]
    i = cmd.index("--model")
    assert cmd[i + 1] == "qwen3:8b"
    assert env["ANTHROPIC_BASE_URL"] == harness.OLLAMA_URL
    assert env["ANTHROPIC_AUTH_TOKEN"] == "ollama"
    assert "ANTHROPIC_API_KEY" not in env
    assert synced == []  # opencode.json sync is OpenCode-only

    # default mode never overrides the endpoint
    cap = _captured_invoke_full(monkeypatch, {"harness": "Claude Code"})
    assert "ANTHROPIC_BASE_URL" not in cap["env"]
    assert cap["env"]["ANTHROPIC_API_KEY"] == "sk-real-key"


def test_codex_local_model_invoked_with_oss_flags(monkeypatch):
    # §6: a Codex local-model agent rides the official top-level flags
    # `--oss --local-provider ollama` before the exec subcommand (exec itself
    # rejects them), with the model as a plain `--model` after it.
    from autowright import harness

    synced = []
    monkeypatch.setattr(harness, "sync_opencode_ollama", synced.append)
    cmd = _captured_invoke(monkeypatch, {"harness": "Codex", "mode": "ollama",
                                         "model": "qwen3:8b"})
    ex = cmd.index("exec")
    assert cmd.index("--oss") < ex
    lp = cmd.index("--local-provider")
    assert lp < ex and cmd[lp + 1] == "ollama"
    i = cmd.index("--model")
    assert i > ex and cmd[i + 1] == "qwen3:8b"
    assert "--sandbox" in cmd and "--skip-git-repo-check" in cmd
    assert synced == []

    # web=True composes: both top-level flags before exec
    cmd = _captured_invoke(monkeypatch, {"harness": "Codex", "mode": "ollama",
                                         "model": "qwen3:8b"}, web=True)
    ex = cmd.index("exec")
    assert cmd.index("--search") < ex and cmd.index("--oss") < ex

    # default mode: no local flags
    cmd = _captured_invoke(monkeypatch, {"harness": "Codex"})
    assert "--oss" not in cmd and "--local-provider" not in cmd


def test_custom_model_invoked_with_verbatim_model_flag(monkeypatch):
    # §4.7: a custom-model agent passes the user-typed string verbatim as
    # `--model` on every harness — no ollama/ prefix, no opencode.json sync.
    from autowright import harness

    synced = []
    monkeypatch.setattr(harness, "sync_opencode_ollama", synced.append)
    _gemini_signed_in(monkeypatch)
    for name, model in (("Claude Code", "claude-opus-4-8"),
                        ("Gemini CLI", "gemini-2.5-pro"),
                        ("Codex", "gpt-5-codex"),
                        ("OpenCode", "anthropic/claude-opus-4-8")):
        cap = _captured_invoke_full(monkeypatch, {"harness": name, "mode": "custom",
                                                  "model": model})
        cmd = cap["cmd"]
        i = cmd.index("--model")
        assert cmd[i + 1] == model
        _assert_prompt_delivered(cmd, cap["proc"])
    assert synced == []


def test_sync_opencode_ollama_merges_config(monkeypatch, tmp_path):
    import json

    from autowright import harness

    cfg = tmp_path / "opencode.json"
    cfg.write_text(json.dumps({"theme": "dark", "provider": {"anthropic": {}}}))
    monkeypatch.setattr(harness, "_OPENCODE_CONFIG", str(cfg))
    harness.sync_opencode_ollama("qwen3:8b")
    out = json.loads(cfg.read_text())
    assert out["theme"] == "dark"                    # untouched keys survive
    assert "anthropic" in out["provider"]
    entry = out["provider"]["ollama"]
    assert entry["npm"] == "@ai-sdk/openai-compatible"
    assert entry["options"]["baseURL"].endswith("/v1")
    assert "qwen3:8b" in entry["models"]
    # idempotent: a second sync writes nothing new
    before = cfg.read_text()
    harness.sync_opencode_ollama("qwen3:8b")
    assert cfg.read_text() == before


def test_codex_invoked_with_read_only_sandbox(monkeypatch):
    # §6/§8: a runtime call (web=False) never writes files — the sandbox stays
    # read-only. The JSONL progress stream and --ephemeral ride every call.
    cap = _captured_invoke_full(monkeypatch, {"harness": "Codex"})
    cmd = cap["cmd"]
    assert cmd[:2] == ["/usr/local/bin/codex", "exec"]
    i = cmd.index("--sandbox")
    assert cmd[i + 1] == "read-only"
    ex = cmd.index("exec")
    assert cmd.index("--json") > ex and cmd.index("--ephemeral") > ex
    assert "--skip-git-repo-check" in cmd
    _assert_prompt_delivered(cmd, cap["proc"])


def test_web_enabled_claude_allows_only_web_read_tools(monkeypatch):
    # §6 drafting calls: web=True swaps the empty --tools list for exactly
    # WebFetch,WebSearch — every other lockdown flag stays.
    cap = _captured_invoke_full(monkeypatch, {"harness": "Claude Code"}, web=True)
    cmd = cap["cmd"]
    i = cmd.index("--tools")
    assert cmd[i + 1] == "WebFetch,WebSearch"
    assert "--strict-mcp-config" in cmd
    assert "--no-session-persistence" in cmd
    _assert_prompt_delivered(cmd, cap["proc"])


def test_web_enabled_codex_adds_top_level_search_flag(monkeypatch):
    # §6: --search must precede the subcommand — `codex exec --search` is
    # rejected by the CLI. §8: web=True is a file-writing drafting call, so the
    # sandbox escalates to workspace-write (confined to the per-call scratch
    # cwd); the JSONL progress stream and the one-shot flag ride `exec`.
    cmd = _captured_invoke(monkeypatch, {"harness": "Codex"}, web=True)
    assert cmd[:3] == ["/usr/local/bin/codex", "--search", "exec"]
    i = cmd.index("--sandbox")
    assert cmd[i + 1] == "workspace-write"
    ex = cmd.index("exec")
    assert cmd.index("--json") > ex and cmd.index("--ephemeral") > ex
    assert "--skip-git-repo-check" in cmd


def test_prompt_delivery_follows_the_per_os_rule(monkeypatch):
    # §8: on POSIX the prompt is the command's last argv element and stdin is
    # /dev/null; on Windows — where the command line caps at 32,767 chars and
    # a drafting prompt is ~38 K — argv carries no prompt at all and the whole
    # of it goes down a stdin pipe instead.
    import subprocess

    from autowright import harness

    long_prompt = "question: " + ("x" * 40_000)
    _gemini_signed_in(monkeypatch)
    for name, head in (("Claude Code", "claude"), ("Gemini CLI", "gemini"),
                       ("Codex", "codex"), ("OpenCode", "opencode")):
        monkeypatch.setattr(harness, "resolve_bin", lambda n: f"/usr/local/bin/{n}")
        monkeypatch.setattr(harness, "sync_opencode_ollama", lambda m: None)
        cap = {}

        def fake_popen(cmd, **kw):
            cap["cmd"], cap["stdin"] = cmd, kw.get("stdin")
            cap["proc"] = proc = _FakeProc()
            return proc

        monkeypatch.setattr(harness.subprocess, "Popen", fake_popen)
        assert harness.invoke({"harness": name}, long_prompt) == "ok"
        cmd, proc = cap["cmd"], cap["proc"]
        assert cmd[0].endswith(head)
        if PIPES_PROMPT:
            assert long_prompt not in cmd
            assert max(len(a) for a in cmd) < 100  # nothing prompt-sized in argv
            assert sum(len(a) + 1 for a in cmd) < 32_767  # under the OS cap
            assert cap["stdin"] is subprocess.PIPE
            assert proc.stdin_text == long_prompt
            assert proc.stdin_closed
            if name == "Gemini CLI":
                assert "-p" not in cmd  # -p <prompt> drops entirely
        else:
            assert cmd[-1] == long_prompt
            assert cap["stdin"] is subprocess.DEVNULL
            assert proc.stdin_text == ""


def test_web_flag_adds_gemini_yolo_and_leaves_opencode_unchanged(monkeypatch):
    # Bare invocations already carry their built-in web tools; web=True must
    # not sprout a stray web flag. §8: the one difference it does make is
    # Gemini's `--approval-mode yolo` — a file-writing drafting call needs the
    # write tools to auto-approve non-interactively; runtime calls stay bare.
    _gemini_signed_in(monkeypatch)
    assert _captured_invoke(monkeypatch, {"harness": "OpenCode"}, web=True) == \
        _captured_invoke(monkeypatch, {"harness": "OpenCode"})

    web = _captured_invoke(monkeypatch, {"harness": "Gemini CLI"}, web=True)
    bare = _captured_invoke(monkeypatch, {"harness": "Gemini CLI"})
    assert "--approval-mode" not in bare
    i = web.index("--approval-mode")
    assert web[i + 1] == "yolo"
    assert [a for j, a in enumerate(web) if j not in (i, i + 1)] == bare
    if not PIPES_PROMPT:
        assert i < web.index("-p")  # the flags precede the prompt argv


def test_detect_reports_all_four_with_sign_in_state(monkeypatch):
    from autowright import harness

    present = {"gemini", "opencode"}
    monkeypatch.setattr(harness, "resolve_bin",
                        lambda name: f"/usr/local/bin/{name}" if name in present else None)

    class _Proc:
        returncode = 0

        def __init__(self, *a, **kw):
            pass

        def communicate(self, timeout=None):
            return "9.9.9\n", ""

    monkeypatch.setattr(harness.subprocess, "Popen", _Proc)
    monkeypatch.setattr(harness, "signed_in", lambda pid: pid == "gemini")
    by_id = {f["id"]: f for f in harness.detect()}
    # §19: one entry per harness, all four always present — Ollama is not a
    # harness and never appears in detection
    assert set(by_id) == {"claude", "codex", "gemini", "opencode"}
    assert by_id["gemini"]["installed"] and by_id["gemini"]["signedIn"] is True
    assert "9.9.9" in by_id["gemini"]["detail"] and "signed in" in by_id["gemini"]["detail"]
    assert by_id["opencode"]["installed"] and by_id["opencode"]["signedIn"] is False
    assert "not signed in yet" in by_id["opencode"]["detail"]
    assert not by_id["claude"]["installed"] and by_id["claude"]["detail"] == ""


def _set_home(monkeypatch, path):
    """Point `expanduser` at `path` on every OS: POSIX reads HOME, Windows
    (ntpath) reads USERPROFILE — setting both is harmless either way."""
    monkeypatch.setenv("HOME", str(path))
    monkeypatch.setenv("USERPROFILE", str(path))


def _isolate_host(monkeypatch, tmp_path):
    """Pin detection/sign-in to the fake CLI alone: the host's real ~/.gemini,
    ~/.local/share/opencode, GEMINI_API_KEY, and CLIs on the fallback bin dirs
    must never leak into the result."""
    from autowright import harness

    fake_home = tmp_path / "home"
    fake_home.mkdir(exist_ok=True)
    _set_home(monkeypatch, fake_home)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("AUTOWRIGHT_TEST_CLAUDE_SIGNED_OUT", raising=False)
    monkeypatch.setattr(harness, "_FALLBACK_BIN_DIRS", ())
    tests_bin = Path(__file__).resolve().parent / "bin"
    # The OS's own bin dir stays on PATH (nothing agent-shaped lives there);
    # everything else is stripped so only the fake CLI can be detected.
    system = ((os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"),)
              if os.name == "nt" else ("/usr/bin", "/bin"))
    monkeypatch.setenv("PATH", os.pathsep.join((str(tests_bin), *system)))
    return fake_home


def test_detect_finds_fake_claude_from_path(monkeypatch, tmp_path):
    """conftest prepends tests/bin, so the real detection path finds the fake CLI."""
    from autowright import harness

    _isolate_host(monkeypatch, tmp_path)
    found = harness.detect()
    by_id = {f["id"]: f for f in found}
    assert by_id["claude"]["installed"]
    assert "autowright test fake" in by_id["claude"]["detail"]
    # the isolated host has no other CLI installed
    assert not by_id["codex"]["installed"]
    assert not by_id["gemini"]["installed"]
    assert not by_id["opencode"]["installed"]


def test_detect_reports_claude_sign_in_from_auth_status_exit(monkeypatch, tmp_path):
    """§19: `signed_in("claude")` shells out to `claude auth status` and reads
    the exit code — AUTOWRIGHT_TEST_CLAUDE_SIGNED_OUT=1 flips the fake CLI to
    exit 1, and the real detect() path must report signed out."""
    from autowright import harness

    _isolate_host(monkeypatch, tmp_path)
    by_id = {f["id"]: f for f in harness.detect()}
    assert by_id["claude"]["installed"] and by_id["claude"]["signedIn"] is True
    assert "signed in" in by_id["claude"]["detail"]

    monkeypatch.setenv("AUTOWRIGHT_TEST_CLAUDE_SIGNED_OUT", "1")
    assert harness.signed_in("claude") is False
    by_id = {f["id"]: f for f in harness.detect()}
    assert by_id["claude"]["installed"] and by_id["claude"]["signedIn"] is False
    assert "not signed in yet" in by_id["claude"]["detail"]


def test_check_ready_requires_sign_in(monkeypatch):
    """§19: every account-backed harness must be signed in to be ready."""
    from autowright import harness

    monkeypatch.setattr(harness, "resolve_bin", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(harness, "signed_in", lambda pid: False)
    for name in ("Claude Code", "Codex", "Gemini CLI", "OpenCode"):
        assert not harness.check_ready(name)
    monkeypatch.setattr(harness, "signed_in", lambda pid: True)
    for name in ("Claude Code", "Codex", "Gemini CLI", "OpenCode"):
        assert harness.check_ready(name)
    monkeypatch.setattr(harness, "resolve_bin", lambda name: None)
    assert not harness.check_ready("Codex")  # not installed → never ready


def test_signin_state_is_cheap_per_provider(monkeypatch):
    from autowright import harness

    monkeypatch.setattr(harness, "resolve_bin", lambda name: "/usr/local/bin/x")
    monkeypatch.setattr(harness, "signed_in", lambda pid: pid == "codex")
    assert harness.signin_state("codex") == {"installed": True, "signedIn": True}
    assert harness.signin_state("gemini") == {"installed": True, "signedIn": False}
    monkeypatch.setattr(harness, "ollama_status",
                        lambda: {"ready": False, "installed": False, "models": []})
    assert harness.signin_state("ollama") == {"installed": False, "signedIn": None}


def test_check_ready_local_model_requires_installed_model(monkeypatch):
    """§4.7: a local-model agent is a local-model harness + Ollama server +
    the model — no sign-in needed."""
    from autowright import harness

    monkeypatch.setattr(harness, "resolve_bin", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(harness, "signed_in", lambda pid: False)
    monkeypatch.setattr(harness, "sync_opencode_ollama", lambda model: None)
    monkeypatch.setattr(harness, "ollama_status",
                        lambda: {"ready": True, "installed": True,
                                 "models": ["qwen3:8b", "llama3.2:latest"],
                                 "version": "0.14.2"})
    # signed out is fine — every local-model harness (§4.7)
    assert harness.check_ready("OpenCode", "qwen3:8b", "ollama")
    assert harness.check_ready("Claude Code", "qwen3:8b", "ollama")
    assert harness.check_ready("Codex", "qwen3:8b", "ollama")
    assert harness.check_ready("OpenCode", "llama3.2", "ollama")  # bare name → :latest
    assert not harness.check_ready("OpenCode", "mistral:7b", "ollama")
    assert not harness.check_ready("Gemini CLI", "qwen3:8b", "ollama")  # never local (§4.7)
    # §4.7 custom mode: model string never validated — sign-in decides, and a
    # signed-out harness is not ready
    assert not harness.check_ready("Claude Code", "made-up-model", "custom")
    monkeypatch.setattr(harness, "signed_in", lambda pid: True)
    assert harness.check_ready("Claude Code", "made-up-model", "custom")
    monkeypatch.setattr(harness, "signed_in", lambda pid: False)

    monkeypatch.setattr(harness, "ollama_status",
                        lambda: {"ready": False, "installed": True, "models": [],
                                 "version": None})
    assert not harness.check_ready("OpenCode", "qwen3:8b", "ollama")

    monkeypatch.setattr(harness, "resolve_bin", lambda name: None)
    assert not harness.check_ready("OpenCode", "qwen3:8b")  # no binary → never ready


def test_check_ready_claude_local_gates_on_ollama_version(monkeypatch):
    # §19: Claude Code talks to Ollama's Anthropic-compatible endpoint, which
    # shipped in 0.14.0 — an older (or unknown-version) Ollama reads
    # needs-setup for Claude Code while the other local harnesses stay ready.
    from autowright import harness

    monkeypatch.setattr(harness, "resolve_bin", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(harness, "signed_in", lambda pid: False)
    monkeypatch.setattr(harness, "sync_opencode_ollama", lambda model: None)
    monkeypatch.setattr(harness, "ollama_status",
                        lambda: {"ready": True, "installed": True,
                                 "models": ["qwen3:8b"], "version": "0.13.9"})
    assert not harness.check_ready("Claude Code", "qwen3:8b", "ollama")
    assert harness.check_ready("Codex", "qwen3:8b", "ollama")
    assert harness.check_ready("OpenCode", "qwen3:8b", "ollama")

    monkeypatch.setattr(harness, "ollama_status",
                        lambda: {"ready": True, "installed": True,
                                 "models": ["qwen3:8b"], "version": None})
    assert not harness.check_ready("Claude Code", "qwen3:8b", "ollama")


def test_version_at_least():
    from autowright import harness

    floor = harness.OLLAMA_MIN_ANTHROPIC
    assert harness.version_at_least("0.14.0", floor)
    assert harness.version_at_least("0.14.2", floor)
    assert harness.version_at_least("1.0.0", floor)
    assert harness.version_at_least("v0.15.0-rc1", floor)
    assert not harness.version_at_least("0.13.9", floor)
    assert not harness.version_at_least(None, floor)
    assert not harness.version_at_least("", floor)
    assert not harness.version_at_least("garbage", floor)


def test_disallowed_imports_matches_drafting_rule():
    from autowright.imports_check import ALLOWED_IMPORTS, disallowed_imports

    code = ("import django\n"
            "import requests\n"
            "from bs4 import BeautifulSoup\n"
            "from dateutil.parser import parse\n"
            "from . import sibling\n"           # relative → ignored
            "import numpy.linalg\n")
    assert disallowed_imports(code) == ["django", "numpy"]
    assert disallowed_imports("x = 1 +\n") == []  # syntax error surfaces at exec
    # rule identical to §8 draft validation — drafting uses the shared module directly
    from autowright import drafting

    assert drafting.disallowed_imports is disallowed_imports
    assert "requests" in ALLOWED_IMPORTS


def test_sync_opencode_ollama_sidesteps_corrupt_config(monkeypatch, tmp_path):
    # §19: corrupt (half-written) opencode.json is preserved as .corrupt and a
    # fresh valid config written — the user's bytes are never silently replaced.
    import json

    from autowright import harness

    cfg = tmp_path / "opencode.json"
    corrupt = '{"theme": "dark", "provider": {'  # truncated mid-write
    cfg.write_text(corrupt)
    monkeypatch.setattr(harness, "_OPENCODE_CONFIG", str(cfg))
    harness.sync_opencode_ollama("qwen3:8b")

    assert (tmp_path / "opencode.json.corrupt").read_text() == corrupt
    out = json.loads(cfg.read_text())  # fresh file parses
    assert "theme" not in out  # started clean, not from the corrupt bytes
    entry = out["provider"]["ollama"]
    assert entry["npm"] == "@ai-sdk/openai-compatible"
    assert "qwen3:8b" in entry["models"]


def test_spawn_env_path_prepend_dedupe_order(monkeypatch):
    # §19 GUI minimal PATH fix. Fallback dirs go in FRONT of the existing PATH
    # (not appended), duplicates collapse to their first occurrence, and the
    # surviving original entries keep their relative order.
    from autowright import harness

    sep = os.pathsep
    monkeypatch.setattr(harness, "_FALLBACK_BIN_DIRS", ("/fb1", "/b"))
    monkeypatch.setenv("PATH", sep.join(("/a", "/b", "/c")))
    env = harness.spawn_env()
    # /b deduped; /a before /c preserved
    assert env["PATH"] == sep.join(("/fb1", "/b", "/a", "/c"))
    assert env["PATH"].split(sep)[-2:] == ["/a", "/c"]


def test_spawn_env_idempotent_and_binpath_dir_first(monkeypatch):
    from autowright import harness

    sep = os.pathsep
    monkeypatch.setattr(harness, "_FALLBACK_BIN_DIRS", ("/fb1", "/b"))
    monkeypatch.setenv("PATH", sep.join(("/a", "/b", "/c")))
    once = harness.spawn_env()["PATH"]
    monkeypatch.setenv("PATH", once)
    assert harness.spawn_env()["PATH"] == once  # already-present dirs: no change

    monkeypatch.setenv("PATH", "/a")
    env = harness.spawn_env("/opt/x/claude")
    # binary's own dir leads
    assert env["PATH"] == sep.join(("/opt/x", "/fb1", "/b", "/a"))


def test_probe_tools_resolves_against_step_path(monkeypatch, tmp_path):
    # §6 installed-tools probe: found tools come back as {name, path}, missing
    # ones are omitted, and resolution uses the §6.1 step PATH — fallback dirs
    # included — so the probe sees exactly what a step subprocess will see.
    from autowright import harness

    fb = tmp_path / "fallback"
    fb.mkdir()
    # §19: what makes a file executable is per-OS — the execute bit on POSIX,
    # a PATHEXT extension on Windows.
    if os.name == "nt":
        exe = fb / "gh.cmd"
        exe.write_text("@echo off\n")
    else:
        exe = fb / "gh"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
    monkeypatch.setattr(harness, "_FALLBACK_BIN_DIRS", (str(fb),))
    monkeypatch.setattr(harness, "_PROBE_TOOLS", ("gh", "definitely-missing-tool"))
    # the Dock launch's stripped PATH — modeled with an empty dir rather than
    # /usr/bin, so a real `gh` installed on the host can never shadow the
    # fallback-dir resolution this test pins.
    stripped = tmp_path / "stripped-path"
    stripped.mkdir()
    monkeypatch.setenv("PATH", str(stripped))
    probed = harness.probe_tools()
    assert [t["name"] for t in probed] == ["gh"]  # the missing one is omitted
    assert Path(probed[0]["path"]).samefile(exe)


# ---------- Ollama runtime (§19 /ollama/status, §10 Free local AI card) ----------

def test_ollama_status_ready_when_server_answers(monkeypatch):
    from autowright import harness

    monkeypatch.setattr(harness, "_ollama_models", lambda: ["qwen3:8b", "gemma4:e4b"])
    monkeypatch.setattr(harness, "_ollama_version", lambda: "0.14.2")
    monkeypatch.setattr(harness, "ollama_bin", lambda: "/fake/ollama")
    spawned = []
    monkeypatch.setattr(harness.subprocess, "Popen",
                        lambda *a, **k: spawned.append(a) or object())
    st = harness.ollama_status()
    assert st == {"ready": True, "installed": True,
                  "models": ["qwen3:8b", "gemma4:e4b"], "version": "0.14.2"}
    assert spawned == []  # server already up — never a spawn


def test_ollama_status_autostarts_serve_once(monkeypatch):
    # §19: installed but not answering → start `ollama serve` and wait
    # briefly; a still-down probe inside the cooldown never spawns again
    # (but the self-heal comes back after the cooldown — no forever-latch).
    from autowright import harness

    monkeypatch.setattr(harness, "OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setattr(harness, "ollama_bin", lambda: "/fake/ollama")
    monkeypatch.setattr(harness, "_ollama_version", lambda: "0.14.2")
    monkeypatch.setattr(harness.time, "sleep", lambda s: None)
    answers = [None, None, ["qwen3:8b"]]  # down, then up after the spawn
    monkeypatch.setattr(harness, "_ollama_models",
                        lambda: answers.pop(0) if answers else None)
    spawned = []
    monkeypatch.setattr(harness.subprocess, "Popen",
                        lambda cmd, **k: spawned.append(cmd) or object())

    st = harness.ollama_status()
    assert spawned == [["/fake/ollama", "serve"]]
    assert st["ready"] is True and st["models"] == ["qwen3:8b"]

    st2 = harness.ollama_status()  # answers exhausted → server down again
    assert len(spawned) == 1      # cooldown guard held
    assert st2["ready"] is False and st2["installed"] is True

    # past the cooldown the self-heal retries the spawn
    monkeypatch.setattr(harness, "_serve_last_spawn",
                        harness.time.time() - harness._SERVE_COOLDOWN_S - 1)
    harness.ollama_status()
    assert len(spawned) == 2


def test_ollama_status_not_installed(monkeypatch):
    from autowright import harness

    monkeypatch.setattr(harness, "_ollama_models", lambda: None)
    monkeypatch.setattr(harness, "ollama_bin", lambda: None)
    assert harness.ollama_status() == {"ready": False, "installed": False,
                                       "models": [], "version": None}


def test_ollama_status_remote_url_never_autostarts(monkeypatch):
    # §19: the autostart is for the local server only — a remote
    # AUTOWRIGHT_OLLAMA_URL must never spawn a local `ollama serve`.
    from autowright import harness

    monkeypatch.setattr(harness, "OLLAMA_URL", "http://gpu-box:11434")
    monkeypatch.setattr(harness, "_ollama_models", lambda: None)
    monkeypatch.setattr(harness, "ollama_bin", lambda: "/fake/ollama")
    spawned = []
    monkeypatch.setattr(harness.subprocess, "Popen",
                        lambda *a, **k: spawned.append(a) or object())
    st = harness.ollama_status()
    assert spawned == []
    assert st == {"ready": False, "installed": True, "models": [], "version": None}


def test_ollama_model_installed_bare_name_matches_latest():
    from autowright import harness

    assert harness.ollama_model_installed("qwen3:8b", ["qwen3:8b"]) is True
    assert harness.ollama_model_installed("qwen3", ["qwen3:latest"]) is True
    assert harness.ollama_model_installed("qwen3", ["qwen3:8b"]) is False
    assert harness.ollama_model_installed("qwen3:8b", ["qwen3:latest"]) is False
    assert harness.ollama_model_installed("qwen3:8b", []) is False


def test_ollama_models_parses_tags_and_none_on_error(monkeypatch):
    import http.server
    import json as _json
    import threading

    from autowright import harness

    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")

    class Tags(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = _json.dumps({"models": [{"name": "qwen3:8b", "size": 1},
                                           {"name": "gemma4:e4b", "size": 2}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Tags)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        monkeypatch.setattr(harness, "OLLAMA_URL", f"http://127.0.0.1:{srv.server_port}")
        assert harness._ollama_models() == ["qwen3:8b", "gemma4:e4b"]
    finally:
        srv.shutdown()
        srv.server_close()
    # dead port → None, never an exception
    assert harness._ollama_models() is None


def test_ollama_bin_falls_back_to_app_bundle(monkeypatch, tmp_path):
    from autowright import harness

    monkeypatch.setattr(harness, "resolve_bin", lambda b: None)
    # §19: the bundle probe applies the per-OS executable check, so the fake
    # has to look executable the way this OS decides that.
    if os.name == "nt":
        fake = tmp_path / "ollama.exe"
        fake.write_bytes(b"MZ")
    else:
        fake = tmp_path / "ollama"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
    monkeypatch.setattr(harness, "_OLLAMA_APP_BINS", (str(tmp_path / "missing"), str(fake)))
    assert harness.ollama_bin() == str(fake)
    monkeypatch.setattr(harness, "_OLLAMA_APP_BINS", (str(tmp_path / "missing"),))
    assert harness.ollama_bin() is None


def test_agent_timeout_env(monkeypatch):
    # §15 AUTOWRIGHT_AGENT_TIMEOUT_S: read per call, default 300, junk ignored.
    from autowright import harness

    monkeypatch.delenv("AUTOWRIGHT_AGENT_TIMEOUT_S", raising=False)
    assert harness.agent_timeout() == 300
    monkeypatch.setenv("AUTOWRIGHT_AGENT_TIMEOUT_S", "600")
    assert harness.agent_timeout() == 600
    monkeypatch.setenv("AUTOWRIGHT_AGENT_TIMEOUT_S", "junk")
    assert harness.agent_timeout() == 300


def test_agent_hard_cap_env(monkeypatch):
    # §15 AUTOWRIGHT_AGENT_HARD_CAP_S: read per call, default 1800, junk ignored.
    from autowright import harness

    monkeypatch.delenv("AUTOWRIGHT_AGENT_HARD_CAP_S", raising=False)
    assert harness.agent_hard_cap() == 1800
    monkeypatch.setenv("AUTOWRIGHT_AGENT_HARD_CAP_S", "60")
    assert harness.agent_hard_cap() == 60
    monkeypatch.setenv("AUTOWRIGHT_AGENT_HARD_CAP_S", "junk")
    assert harness.agent_hard_cap() == 1800


def test_harness_error_retryable_flag():
    from autowright.harness import HarnessError

    assert HarnessError("x").retryable is False
    assert HarnessError("x", retryable=True).retryable is True


# ---------- invoke() child lifecycle (§8: real Popen, no mocks) ----------

def test_invoke_timeout_kills_group_and_is_retryable(monkeypatch, tmp_path, home):
    # §8: the timeout timer kills the whole session group — invoke returns
    # promptly with a retryable timeout error, never after the child's sleep.
    from autowright import harness

    script = fake_cli(tmp_path, "import time\ntime.sleep(60)\n")
    monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))
    t0 = time.monotonic()
    with pytest.raises(harness.HarnessError) as ei:
        harness.invoke({"harness": "Claude Code"}, "question: hi?", timeout=1)
    assert time.monotonic() - t0 < 10  # the group kill worked — no 60 s wait
    assert "timed out after 1s" in str(ei.value)
    assert ei.value.retryable is True


def test_invoke_output_resets_idle_window(monkeypatch, tmp_path, home):
    # §8: the timeout is an idle window — a child that streams a line every
    # 0.4 s outlives a 1 s window because each line resets it. The old fixed
    # timer would have killed this run at 1 s.
    from autowright import harness

    script = fake_cli(tmp_path,
                       "import sys, time\n"
                       "for _ in range(5):\n"
                       "    print('tick', flush=True)\n"
                       "    time.sleep(0.4)\n"
                       "print('done', flush=True)\n")
    monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))
    out = harness.invoke({"harness": "Claude Code"}, "question: hi?", timeout=1)
    assert "done" in out


def test_invoke_hard_cap_ends_endless_streamer(monkeypatch, tmp_path, home):
    # §8: the hard cap bounds total wall clock — a child that streams forever
    # never trips the idle window but dies at the cap, with a retryable error.
    from autowright import harness

    script = fake_cli(tmp_path,
                       "import time\n"
                       "while True:\n"
                       "    print('tick', flush=True)\n"
                       "    time.sleep(0.3)\n")
    monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))
    monkeypatch.setenv("AUTOWRIGHT_AGENT_HARD_CAP_S", "2")
    t0 = time.monotonic()
    with pytest.raises(harness.HarnessError) as ei:
        harness.invoke({"harness": "Claude Code"}, "question: hi?", timeout=1)
    assert time.monotonic() - t0 < 10
    assert "timed out after 2s total" in str(ei.value)
    assert ei.value.retryable is True


def test_invoke_failure_carries_stderr_tail(monkeypatch, tmp_path, home):
    # §8: a nonzero exit raises with the TAIL of stderr (last 3 lines) —
    # banners die first, the decisive last line always survives.
    from autowright import harness

    script = fake_cli(tmp_path,
                       "import sys\n"
                       "for ln in ('banner line one', 'banner line two',\n"
                       "           'banner line three', 'ERROR: the decisive line'):\n"
                       "    print(ln, file=sys.stderr)\n"
                       "sys.exit(2)\n")
    monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))
    with pytest.raises(harness.HarnessError) as ei:
        harness.invoke({"harness": "Claude Code"}, "question: hi?")
    msg = str(ei.value)
    assert msg.startswith("Claude Code failed:")
    assert "ERROR: the decisive line" in msg
    assert "banner line two" in msg and "banner line three" in msg
    assert "banner line one" not in msg  # only the last 3 lines ride along
    assert ei.value.retryable is True


def test_invoke_auth_failure_is_not_retryable(monkeypatch, tmp_path, home):
    # §8 failure policy: a nonzero exit whose stderr names an obvious
    # deterministic failure (auth / model-not-found) is NOT retryable —
    # drafting surfaces it immediately instead of retrying a doomed call.
    from autowright import harness

    for stderr_line in ("Invalid API key · Please run /login",
                        "ERROR: 401 Unauthorized",
                        "You are not logged in",
                        "Authentication failed",
                        "ERROR: model not found: gemini-9.9-ultra",
                        "unknown model gpt-99"):
        script = fake_cli(tmp_path,
                           f"import sys\nprint({stderr_line!r}, file=sys.stderr)\n"
                           "sys.exit(1)\n")
        monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))
        with pytest.raises(harness.HarnessError) as ei:
            harness.invoke({"harness": "Claude Code"}, "question: hi?")
        assert stderr_line in str(ei.value)
        assert ei.value.retryable is False, stderr_line


def test_deterministic_failure_classification():
    # Case-insensitive substrings; a generic crash stays retryable, and the
    # timeout path never goes through this classifier at all.
    from autowright.harness import _deterministic_failure

    assert _deterministic_failure("Please run /login") is True
    assert _deterministic_failure("401 UNAUTHORIZED") is True
    assert _deterministic_failure("Not Logged In") is True
    assert _deterministic_failure("Model Not Found") is True
    assert _deterministic_failure("Unknown Model") is True
    assert _deterministic_failure("invalid api key") is True
    assert _deterministic_failure("segmentation fault") is False
    assert _deterministic_failure("connection reset by peer") is False
    # word-bounded numerics — a byte count or an id must not read as an
    # HTTP status and suppress the §8 one-retry policy
    assert _deterministic_failure("sent 4013 bytes") is False
    assert _deterministic_failure("request id 84032 failed") is False
    assert _deterministic_failure("HTTP 403 Forbidden") is True
    assert _deterministic_failure("") is False


def test_claude_stream_line_parse_table():
    # §8: one stream-json stdout line → (text_chunk, final_result, tool_uses).
    from autowright.harness import _claude_stream_line as parse

    assert parse("not json") == (None, None, [])
    assert parse("[1, 2]") == (None, None, [])  # valid JSON, not an object
    delta = json.dumps({"type": "stream_event",
                        "event": {"type": "content_block_delta",
                                  "delta": {"type": "text_delta", "text": "hi"}}})
    assert parse(delta) == ("hi", None, [])
    other = json.dumps({"type": "stream_event",
                        "event": {"type": "content_block_delta",
                                  "delta": {"type": "input_json_delta",
                                            "partial_json": "{"}}})
    assert parse(other) == (None, None, [])  # non-text delta is noise
    # an assistant message's tool_use blocks surface as {name, input} (§8
    # activity events); its text blocks stay out — text rides the deltas
    tooluse = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "checking the page"},
        {"type": "tool_use", "name": "WebFetch", "input": {"url": "https://x"}},
        {"type": "tool_use", "name": "WebSearch", "input": {"query": "q"}}]}})
    assert parse(tooluse) == (None, None, [
        {"name": "WebFetch", "input": {"url": "https://x"}},
        {"name": "WebSearch", "input": {"query": "q"}}])
    assert parse(json.dumps({"type": "assistant", "message": {}})) == (None, None, [])
    # the terminal result event is authoritative…
    assert parse(json.dumps({"type": "result", "result": "full reply"})) == (None, "full reply", [])
    # …but only when its result is a string
    assert parse(json.dumps({"type": "result", "result": {"nested": 1}})) == (None, None, [])


def test_invoke_aborts_a_child_that_floods_stdout(monkeypatch, tmp_path, home):
    # §8 stream cap: the idle window never fires on a call that keeps
    # streaming, so a harness stuck in a tool loop is ended by the cap instead
    # of pushing its whole output through backend memory and every log sink.
    from autowright import harness

    script = fake_cli(tmp_path,
                       "for _ in range(50):\n"
                       "    print('x' * 100, flush=True)\n")
    monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))
    monkeypatch.setattr(harness, "STDOUT_CAP_CHARS", 200)
    with pytest.raises(harness.HarnessError) as ei:
        harness.invoke({"harness": "Claude Code"}, PROMPT)
    msg = str(ei.value)
    assert msg.startswith("Claude Code produced over ")
    assert msg.endswith(" MB of output — aborting")
    assert ei.value.retryable is False


def test_windows_prompt_is_piped_to_a_real_child(monkeypatch, tmp_path, home):
    # §8 per-OS prompt delivery: on Windows the prompt never reaches argv: a
    # writer thread pipes it to the child's stdin and closes the pipe at EOF.
    # The fake CLI answers only after reading stdin, so a parsed reply is the
    # proof that both halves happened.
    from autowright import harness, paths
    from autowright import platform as platmod

    platmod.current()  # cache the HOST platform: the §2 spawn policy is the host's
    monkeypatch.setattr(paths, "current_os", lambda: "windows")
    real_popen = harness.subprocess.Popen
    captured = {}

    def spy_popen(cmd, **kw):
        captured["cmd"] = cmd
        return real_popen(cmd, **kw)

    monkeypatch.setattr(harness.subprocess, "Popen", spy_popen)
    assert harness.invoke({"harness": "Claude Code"}, PROMPT) == "Mock answer: nothing new."
    assert PROMPT not in captured["cmd"], "Windows: the prompt must never reach argv (§8)"
    assert "--" not in captured["cmd"], "the `--` separator goes with the positional prompt"
    assert "-p" in captured["cmd"]


def test_invoke_rejects_an_unknown_harness_and_a_missing_binary(monkeypatch, home):
    # §8: both pre-spawn refusals name the problem exactly: an unrecognized
    # harness by its name, a missing CLI by the §9 per-OS machine noun.
    from autowright import harness, paths

    with pytest.raises(harness.HarnessError) as ei:
        harness.invoke({"harness": "Nope"}, PROMPT)
    assert str(ei.value) == "unknown harness: Nope"

    monkeypatch.setattr(harness, "resolve_bin", lambda name: None)
    with pytest.raises(harness.HarnessError) as ei:
        harness.invoke({"harness": "Claude Code"}, PROMPT)
    assert str(ei.value) == f"claude is not installed on this {paths.machine_noun()}"


# ---------- §19 per-provider sign-in rules ----------

def test_signed_in_gemini_rules(monkeypatch, tmp_path):
    # §19: oauth_creds.json must PARSE as JSON carrying a refresh token —
    # file existence alone never counts; a stale/empty/garbage file must not
    # fake a working sign-in. GEMINI_API_KEY alone still counts.
    from autowright import harness

    _set_home(monkeypatch, tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    assert harness.signed_in("gemini") is False  # nothing on disk, no API key

    gdir = tmp_path / ".gemini"
    gdir.mkdir()
    creds = gdir / "oauth_creds.json"
    creds.write_text("{}")
    assert harness.signed_in("gemini") is False   # no refresh token
    creds.write_text("not json {")
    assert harness.signed_in("gemini") is False   # unparseable → signed out
    creds.write_text(json.dumps({"refresh_token": ""}))
    assert harness.signed_in("gemini") is False   # empty token → signed out
    creds.write_text(json.dumps([1, 2]))
    assert harness.signed_in("gemini") is False   # JSON but not a dict
    creds.write_text(json.dumps({"access_token": "a", "refresh_token": "1//r"}))
    assert harness.signed_in("gemini") is True    # a real refresh token

    creds.unlink()
    assert harness.signed_in("gemini") is False
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    assert harness.signed_in("gemini") is True    # the API key alone suffices


def test_signed_in_opencode_rules(monkeypatch, tmp_path):
    # §19: auth.json must parse as a dict with at least one provider entry
    # holding a non-empty credential (key / token / access / refresh — the
    # shapes the OpenCode CLI writes). A credential-less, empty, unparseable,
    # or non-dict file reads signed out.
    from autowright import harness

    _set_home(monkeypatch, tmp_path)

    assert harness.signed_in("opencode") is False  # nothing on disk

    ocdir = tmp_path / ".local" / "share" / "opencode"
    ocdir.mkdir(parents=True)
    auth = ocdir / "auth.json"
    auth.write_text("{}")
    assert harness.signed_in("opencode") is False  # empty dict → no account
    auth.write_text("garbage {")
    assert harness.signed_in("opencode") is False  # unparseable → signed out
    auth.write_text(json.dumps([{"key": "k"}]))
    assert harness.signed_in("opencode") is False  # JSON but not a dict
    auth.write_text(json.dumps({"anthropic": {"type": "oauth"}}))
    assert harness.signed_in("opencode") is False  # entry with no credential
    auth.write_text(json.dumps({"anthropic": {"type": "oauth", "refresh": "",
                                              "access": ""}}))
    assert harness.signed_in("opencode") is False  # empty credentials
    auth.write_text(json.dumps({"anthropic": {"type": "oauth", "refresh": "r",
                                              "access": "a", "expires": 1}}))
    assert harness.signed_in("opencode") is True   # oauth entry with tokens
    auth.write_text(json.dumps({"openai": {"type": "api", "key": "sk-x"}}))
    assert harness.signed_in("opencode") is True   # api-key entry
    auth.write_text(json.dumps({"github-copilot": {"type": "wellknown",
                                                   "key": "k", "token": "t"}}))
    assert harness.signed_in("opencode") is True   # wellknown entry

    assert harness.signed_in("ollama") is None  # no account concept at all


def test_check_ready_opencode_sync_failure_is_needs_setup(monkeypatch):
    # §19: check endpoints answer ready/needs-setup, never raise — an
    # unwritable opencode.json surfaces as not-ready.
    from autowright import harness

    monkeypatch.setattr(harness, "resolve_bin", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(harness, "ollama_status",
                        lambda: {"ready": True, "installed": True,
                                 "models": ["qwen3:8b"]})

    def boom(model):
        raise harness.HarnessError("couldn't update opencode.json: disk full")

    monkeypatch.setattr(harness, "sync_opencode_ollama", boom)
    assert harness.check_ready("OpenCode", "qwen3:8b", "ollama") is False


def test_invoke_non_claude_streams_raw_lines(monkeypatch, tmp_path, home):
    # §8: a non-stream-json harness streams each raw stdout line to on_chunk
    # and returns the raw output verbatim.
    from autowright import harness

    script = fake_cli(tmp_path, "print('line one')\nprint('line two')\n",
                       name="gemini")
    monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))
    _gemini_signed_in(monkeypatch)
    chunks = []
    out = harness.invoke({"harness": "Gemini CLI"}, "question: hi?",
                         on_chunk=chunks.append)
    assert out == "line one\nline two\n"
    # §8: Gemini stdout lines are bare activity, never text events — banners
    # and tool chatter must not become "Writing the answer" labels or the
    # captured plan; progress comes entirely from the scratch watcher.
    assert chunks == []


def test_invoke_on_chunk_error_reaps_the_child(monkeypatch, tmp_path, home):
    # §8: an on_chunk callback that raises must not orphan the CLI child —
    # the group dies and the original error surfaces promptly.
    from autowright import harness

    # OpenCode: its JSON text events reach on_chunk (Gemini no longer emits
    # text events — §8 — so it can't drive this path).
    script = fake_cli(tmp_path,
                       "import time\n"
                       "print('{\"type\":\"text\",\"part\":{\"text\":\"first\"}}', flush=True)\n"
                       "time.sleep(60)\n",
                       name="opencode")
    monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))

    def bad_chunk(text):
        raise RuntimeError("renderer went away")

    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="renderer went away"):
        harness.invoke({"harness": "OpenCode"}, "question: hi?",
                       on_chunk=bad_chunk)
    assert time.monotonic() - t0 < 10  # the child never got to sleep out 60 s


def test_sync_opencode_ollama_replaces_non_dict_shapes(monkeypatch, tmp_path):
    # §19: a config whose nodes hold the wrong JSON type (valid JSON, so not
    # the .corrupt path) is normalized in place; a missing file starts clean.
    import json

    from autowright import harness

    cfg = tmp_path / "opencode.json"
    monkeypatch.setattr(harness, "_OPENCODE_CONFIG", str(cfg))

    # missing file → clean config written from scratch
    harness.sync_opencode_ollama("qwen3:8b")
    out = json.loads(cfg.read_text())
    assert "qwen3:8b" in out["provider"]["ollama"]["models"]

    # top level not a dict
    cfg.write_text(json.dumps([1, 2, 3]))
    harness.sync_opencode_ollama("qwen3:8b")
    out = json.loads(cfg.read_text())
    assert "qwen3:8b" in out["provider"]["ollama"]["models"]

    # provider / entry / options / models each the wrong type
    cfg.write_text(json.dumps({"provider": "nope"}))
    harness.sync_opencode_ollama("qwen3:8b")
    assert "qwen3:8b" in json.loads(cfg.read_text())["provider"]["ollama"]["models"]

    cfg.write_text(json.dumps({"provider": {"ollama": 5}}))
    harness.sync_opencode_ollama("qwen3:8b")
    assert "qwen3:8b" in json.loads(cfg.read_text())["provider"]["ollama"]["models"]

    cfg.write_text(json.dumps({"provider": {"ollama": {"options": [], "models": "x"}}}))
    harness.sync_opencode_ollama("qwen3:8b")
    entry = json.loads(cfg.read_text())["provider"]["ollama"]
    assert entry["options"]["baseURL"].endswith("/v1")
    assert "qwen3:8b" in entry["models"]


def test_sync_opencode_ollama_write_failure_raises_harness_error(monkeypatch, tmp_path):
    # §19: a config dir the write can't use surfaces as a HarnessError
    # (check_ready turns it into needs-setup) — never a bare OSError up the
    # stack. The failure is a real filesystem one on every OS: a regular FILE
    # sits where the config's parent directory has to be, so the write's
    # mkdir/open raises (chmod 0500 wouldn't stop a write on Windows).
    from autowright import harness

    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory\n")
    monkeypatch.setattr(harness, "_OPENCODE_CONFIG", str(blocked / "opencode.json"))
    with pytest.raises(harness.HarnessError, match="couldn't update opencode.json"):
        harness.sync_opencode_ollama("qwen3:8b")


def test_ollama_bin_prefers_path_resolution(monkeypatch, tmp_path):
    from autowright import harness

    fake = tmp_path / "ollama"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(harness, "resolve_bin", lambda b: str(fake))
    assert harness.ollama_bin() == str(fake)  # PATH hit wins; no bundle probe


def test_status_check_kills_a_wedged_child_and_reads_signed_out(monkeypatch, tmp_path,
                                                                home):
    # §19: a sign-in probe whose CLI never answers is killed at the status
    # timeout and reads signed out. The child has its own session, so the
    # kill reaches a grandchild holding stdout too.
    from autowright import harness

    script = fake_cli(tmp_path, "import time\ntime.sleep(60)\n")
    monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))
    t0 = time.monotonic()
    assert harness.signed_in("claude") is False
    assert time.monotonic() - t0 < 40  # the group kill worked, no 60 s wait


def test_signed_in_codex_asks_login_status_and_unknown_providers_are_signed_out(
        monkeypatch, tmp_path, home):
    # §19: the Codex probe shells out to `codex login status` and reads only
    # the exit code; a provider the app has no rule for is always signed out.
    from autowright import harness

    argv_log = tmp_path / "argv.txt"
    script = fake_cli(tmp_path,
                       "import sys\n"
                       f"open({str(argv_log)!r}, 'w', encoding='utf-8')"
                       ".write(' '.join(sys.argv[1:]))\n",
                       name="codex")
    monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))
    assert harness.signed_in("codex") is True
    assert argv_log.read_text(encoding="utf-8") == "login status"
    assert harness.signed_in("nope") is False


# ---------- §8 per-harness handlers (Live progress) ----------

class _Events:
    """Capturing ProgressSink: records the typed §8 events a handler or the
    scratch watcher reports, in order."""

    def __init__(self):
        from autowright import harness

        self.text = []
        self.tools = []
        self.files = []
        self.activity = 0
        self.sink = harness.ProgressSink(
            on_chunk=self.text.append,
            on_tool=self.tools.append,
            on_file=lambda name, content: self.files.append((name, content)),
            on_activity=self._bump)

    def _bump(self):
        self.activity += 1


def test_claude_handler_result_wins_over_deltas():
    # §8: the terminal result event is authoritative; the joined deltas cover a
    # CLI that streamed but never sent one, and raw stdout covers neither.
    from autowright import harness

    h = harness.ClaudeCodeHandler({"harness": "Claude Code"}, False)
    ev = _Events()
    h.line(json.dumps({"type": "stream_event",
                       "event": {"type": "content_block_delta",
                                 "delta": {"type": "text_delta", "text": "hi "}}}),
           ev.sink)
    h.line(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "WebSearch", "input": {"query": "q"}}]}}),
        ev.sink)
    assert ev.text == ["hi "]
    assert ev.tools == [{"name": "WebSearch", "input": {"query": "q"}}]
    assert h.reply("raw stdout") == "hi "  # no result event yet — the deltas
    h.line(json.dumps({"type": "result", "result": "the full reply"}), ev.sink)
    assert h.reply("raw stdout") == "the full reply"
    # a CLI that streamed nothing at all falls back to its raw stdout
    fresh = harness.ClaudeCodeHandler({"harness": "Claude Code"}, False)
    assert fresh.reply("raw stdout") == "raw stdout"


def test_codex_handler_parses_jsonl_events():
    # §8 Live progress: `codex exec --json` — each completed agent message is a
    # text event (paragraph-separated so accumulated prose keeps its message
    # boundaries) and the LAST one is the reply; command executions and web
    # searches become tool events under the normalized names.
    from autowright import harness

    h = harness.CodexHandler({"harness": "Codex"}, True)
    ev = _Events()
    h.line(json.dumps({"type": "item.completed",
                       "item": {"type": "agent_message", "text": "first pass"}}),
           ev.sink)
    h.line(json.dumps({"type": "item.started",
                       "item": {"type": "command_execution", "command": "ls -la"}}),
           ev.sink)
    h.line(json.dumps({"type": "item.completed",
                       "item": {"type": "web_search", "query": "manga rss"}}),
           ev.sink)
    h.line(json.dumps({"type": "item.completed",
                       "item": {"type": "agent_message", "text": "the final word"}}),
           ev.sink)
    assert ev.text == ["first pass\n\n", "the final word\n\n"]
    assert ev.tools == [{"name": "Shell", "input": {"command": "ls -la"}},
                        {"name": "WebSearch", "input": {"query": "manga rss"}}]
    assert h.reply("raw stdout") == "the final word"


def test_codex_handler_ignores_unknown_lines_and_falls_back_to_raw():
    # Garbage and unmodeled events are bare activity — never an event, never a
    # raise; with no agent message at all the reply is raw stdout.
    from autowright import harness

    h = harness.CodexHandler({"harness": "Codex"}, False)
    ev = _Events()
    for line in ("not json\n", "[1, 2]\n", "\n",
                 json.dumps({"type": "item.completed",
                             "item": {"type": "file_change"}}),
                 json.dumps({"type": "turn.started"})):
        h.line(line, ev.sink)
    assert ev.text == [] and ev.tools == [] and ev.files == []
    assert h.reply("plain stdout") == "plain stdout"


def test_opencode_handler_parses_jsonl_events():
    # §8 Live progress: `opencode run --format json` — each text part is a text
    # event and the reply is every part joined in order; tool uses become tool
    # events under the normalized names.
    from autowright import harness

    h = harness.OpenCodeHandler({"harness": "OpenCode"}, True)
    ev = _Events()
    h.line(json.dumps({"type": "text", "part": {"text": "planning"}}), ev.sink)
    h.line(json.dumps({"type": "tool_use",
                       "part": {"tool": "bash",
                                "state": {"input": {"command": "ls -la"}}}}),
           ev.sink)
    h.line(json.dumps({"type": "tool_use",
                       "part": {"tool": "webfetch",
                                "state": {"input": {"url": "https://x"}}}}),
           ev.sink)
    h.line(json.dumps({"type": "text", "part": {"text": "done"}}), ev.sink)
    assert ev.text == ["planning\n\n", "done\n\n"]
    assert ev.tools == [{"name": "Shell", "input": {"command": "ls -la"}},
                        {"name": "WebFetch", "input": {"url": "https://x"}}]
    assert h.reply("raw stdout") == "planning\n\ndone"


def test_opencode_handler_file_tools_are_silent_and_reply_falls_back_to_raw():
    # §8: the scratch watcher is the single source of `file` events, so the
    # write/edit tools report as bare activity — never a second tool event.
    from autowright import harness

    h = harness.OpenCodeHandler({"harness": "OpenCode"}, True)
    ev = _Events()
    for tool in ("write", "edit"):
        h.line(json.dumps({"type": "tool_use",
                           "part": {"tool": tool,
                                    "state": {"input": {"filePath": "spec.md"}}}}),
               ev.sink)
    assert ev.tools == [] and ev.files == []
    assert ev.activity == 2  # …but a write is still observed progress
    h.line("garbage {", ev.sink)
    assert h.reply("plain stdout") == "plain stdout"  # no text parts arrived


# ---------- §8 file-writing delivery: the scratch watcher ----------

def test_scratch_watcher_reports_documents_in_first_seen_order(tmp_path):
    # §8: each response document that lands becomes a `file` event carrying its
    # current content, growth re-fires with the new content, and the collected
    # documents keep first-seen order (not the poll's alphabetical one).
    from autowright import harness

    ev = _Events()
    watcher = harness._ScratchWatcher(tmp_path, ev.sink)
    (tmp_path / "manifest.yaml").write_text("steps: []\n", encoding="utf-8")
    watcher._poll()
    (tmp_path / "01-fetch.py").write_text("x = 1\n", encoding="utf-8")
    watcher._poll()
    watcher._poll()  # nothing changed — no repeat event
    (tmp_path / "manifest.yaml").write_text("steps: []\nnote: n\n", encoding="utf-8")
    watcher._poll()
    assert ev.files == [("manifest.yaml", "steps: []\n"),
                        ("01-fetch.py", "x = 1\n"),
                        ("manifest.yaml", "steps: []\nnote: n\n")]
    assert watcher.documents() == [("manifest.yaml", "steps: []\nnote: n\n"),
                                   ("01-fetch.py", "x = 1\n")]


def test_scratch_watcher_ignores_everything_but_response_documents(tmp_path):
    # §8: only the envelope's own file names count, as flat regular files — so
    # build residue (__pycache__, helper scripts), a directory wearing a
    # document name, and a symlink are all ignored.
    from autowright import harness

    ev = _Events()
    watcher = harness._ScratchWatcher(tmp_path, ev.sink)
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "helper.py").write_text("pass\n", encoding="utf-8")
    (tmp_path / "spec.md").mkdir()  # a directory wearing a document name
    real = tmp_path / "elsewhere.md"
    real.write_text("# real\n", encoding="utf-8")
    if os.name != "nt":  # symlinks need extra privileges on Windows
        (tmp_path / "notes.md").symlink_to(real)
    # §21.4/§8: instructions.md is retired, but a written one is COLLECTED, not
    # ignored — it has to reach the validator, which fails the response the same
    # way the fenced envelope does (see the audit test for the whole path).
    (tmp_path / "instructions.md").write_text("- keep it short\n", encoding="utf-8")
    for name in ("actions.yaml", "manifest.yaml", "02-send.py"):
        (tmp_path / name).write_text(f"{name}\n", encoding="utf-8")
    watcher._poll()
    assert [n for n, _ in ev.files] == ["02-send.py", "actions.yaml",
                                        "instructions.md", "manifest.yaml"]
    assert [n for n, _ in watcher.documents()] == ["02-send.py", "actions.yaml",
                                                   "instructions.md", "manifest.yaml"]


def test_scratch_watcher_stop_does_a_final_sweep(tmp_path):
    # §8: a document written in the last poll interval still reaches the feed —
    # stop() sweeps once more after the polling thread has joined.
    from autowright import harness

    ev = _Events()
    watcher = harness._ScratchWatcher(tmp_path, ev.sink)
    watcher.start()
    (tmp_path / "spec.md").write_text("# T\n", encoding="utf-8")
    watcher.stop()
    assert ev.files == [("spec.md", "# T\n")]
    assert watcher.documents() == [("spec.md", "# T\n")]


# ---------- §8 file-writing delivery: recombining the envelope ----------

def test_recombine_builds_the_ordinary_envelope():
    # §8: stdout prose + the collected documents become the envelope that
    # validation, repair, and the §5 audit trail already speak — one block per
    # document in first-seen order, closed once at the end.
    from autowright import drafting, harness

    out = harness._recombine("Here is the plan.\n",
                             [("manifest.yaml", "steps: []\n"),
                              ("01-fetch.py", "x = 1\n")])
    assert out == ("Here is the plan.\n"
                   "===FILE: manifest.yaml===\nsteps: []\n\n"
                   "===FILE: 01-fetch.py===\nx = 1\n\n"
                   "===END===")
    # …and the drafting parser reads back exactly the documents that were written
    assert drafting.parse_envelope(out) == {"manifest.yaml": "steps: []\n",
                                            "01-fetch.py": "x = 1\n"}


def test_recombine_lets_a_blocked_stdout_win():
    # §8: blockers ride stdout by contract — a line-anchored ===BLOCKED=== is
    # the reply as printed, whatever landed in the scratch dir.
    from autowright import harness

    stdout = "===BLOCKED===\nblockers:\n  - reason: r\n    fix: f\n===END===\n"
    assert harness._recombine(stdout, [("spec.md", "# T\n")]) == stdout


def test_recombine_without_documents_returns_stdout_unchanged():
    # §8: an empty scratch dir is an answer-only reply (or an agent that
    # ignored the OUTPUT section and printed the envelope itself).
    from autowright import harness

    assert harness._recombine("just an answer\n", []) == "just an answer\n"


def test_recombine_clips_stdout_blocks_the_agent_also_printed():
    # §8: the files win — stdout is kept only up to its first ===FILE: marker,
    # so a redundantly printed (and possibly stale) copy is dropped.
    from autowright import harness

    stdout = "Here is the plan.\n\n===FILE: spec.md===\n# stale\n===END===\n"
    out = harness._recombine(stdout, [("spec.md", "# fresh\n")])
    assert out == "Here is the plan.\n===FILE: spec.md===\n# fresh\n\n===END==="
    assert "# stale" not in out


# ---------- §8 file-writing delivery: the scratch dir's lifecycle ----------

def test_file_writing_call_runs_in_a_scratch_dir_and_removes_it(monkeypatch, tmp_path, home):
    # §8: a web=True call on a file-writing harness runs in a fresh per-call
    # scratch dir, the documents written there stream as `file` events and land
    # in the recombined reply, and the dir is gone once the call ends.
    from autowright import harness, paths

    script = fake_cli(tmp_path,
                       "import os\n"
                       "open('spec.md', 'w', encoding='utf-8').write('# Written\\n')\n"
                       "print(os.getcwd())\n",
                       name="codex")
    monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))
    files = []
    out = harness.invoke({"harness": "Codex"}, PROMPT, web=True,
                         on_file=lambda name, content: files.append((name, content)))
    scratch_dir = Path(out.splitlines()[0])
    # the child's cwd was <app support>/harness/codex/scratch/<call id>
    assert scratch_dir.parent.name == "scratch"
    assert scratch_dir.parent.parent.name == "codex"
    assert "===FILE: spec.md===\n# Written\n" in out
    assert out.endswith("===END===")
    assert files and all(name == "spec.md" for name, _ in files)
    assert files[-1] == ("spec.md", "# Written\n")
    assert not scratch_dir.exists()  # removed when the call ended
    assert list(paths.harness_scratch("codex").iterdir()) == []


def test_scratch_dir_is_removed_when_the_call_fails(monkeypatch, tmp_path, home):
    # §8: the scratch dir goes on EVERY path — a nonzero exit leaves nothing
    # behind for the startup sweep to find.
    from autowright import harness, paths

    script = fake_cli(tmp_path,
                       "import sys\n"
                       "open('spec.md', 'w', encoding='utf-8').write('# partial\\n')\n"
                       "print('ERROR: boom', file=sys.stderr)\n"
                       "sys.exit(3)\n",
                       name="opencode")
    monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))
    with pytest.raises(harness.HarnessError, match="boom"):
        harness.invoke({"harness": "OpenCode"}, PROMPT, web=True)
    assert list(paths.harness_scratch("opencode").iterdir()) == []


def test_scratch_dir_is_removed_when_the_spawn_fails(monkeypatch, home):
    # §8: the main cleanup only starts after the spawn, so a Popen that raises
    # must take the per-call scratch dir with it instead of leaving it for the
    # next startup sweep.
    from autowright import harness, paths

    monkeypatch.setattr(harness, "resolve_bin", lambda name: "/usr/local/bin/codex")

    def boom(cmd, **kw):
        raise OSError("no processes left")

    monkeypatch.setattr(harness.subprocess, "Popen", boom)
    with pytest.raises(OSError, match="no processes left"):
        harness.invoke({"harness": "Codex"}, PROMPT, web=True)
    assert list(paths.harness_scratch("codex").iterdir()) == []


def test_runtime_call_writes_no_scratch_and_keeps_the_read_only_sandbox(
        monkeypatch, tmp_path, home):
    # §8: file-writing delivery is for drafting calls only — a runtime
    # agent.ask call (web=False) keeps the provider's empty workspace as its
    # cwd, creates no scratch dir, and Codex stays in its read-only sandbox.
    from autowright import harness, paths

    script = fake_cli(tmp_path, "import os\nprint(os.getcwd())\n", name="codex")
    monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))
    real_popen = harness.subprocess.Popen
    captured = {}

    def spy_popen(cmd, **kw):
        captured["cmd"] = cmd
        return real_popen(cmd, **kw)

    monkeypatch.setattr(harness.subprocess, "Popen", spy_popen)
    out = harness.invoke({"harness": "Codex"}, PROMPT)
    assert Path(out.strip()).name == "workspace"
    i = captured["cmd"].index("--sandbox")
    assert captured["cmd"][i + 1] == "read-only"
    assert not paths.harness_scratch("codex").exists()


def test_clear_scratch_removes_leftover_trees_only(home):
    # §5 startup sweep: whole per-provider scratch/ trees a crashed backend left
    # behind go; the providers' workspace dirs are untouched.
    from autowright import harness, paths

    for pid in ("codex", "gemini"):
        leftover = paths.harness_scratch(pid) / "abandoned-call"
        leftover.mkdir(parents=True)
        (leftover / "spec.md").write_text("# half written\n", encoding="utf-8")
        paths.harness_workspace(pid).mkdir(parents=True, exist_ok=True)
    harness.clear_scratch()
    for pid in ("codex", "gemini"):
        assert not paths.harness_scratch(pid).exists()
        assert paths.harness_workspace(pid).is_dir()


def test_gemini_preflight_refuses_a_signed_out_cli(monkeypatch, home):
    # §8 failure policy: a signed-out Gemini CLI blocks forever on its browser
    # sign-in prompt, so the pre-flight fails the call non-retryably BEFORE any
    # spawn — in every mode, drafting and runtime alike.
    from autowright import harness, paths

    monkeypatch.setattr(harness, "resolve_bin", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(harness, "signed_in", lambda pid: False)
    spawned = []
    monkeypatch.setattr(harness.subprocess, "Popen",
                        lambda *a, **kw: spawned.append(a) or None)
    for web in (False, True):
        with pytest.raises(harness.HarnessError) as ei:
            harness.invoke({"harness": "Gemini CLI"}, PROMPT, web=web)
        assert "not signed in" in str(ei.value)
        assert ei.value.retryable is False
    assert spawned == []
    assert not paths.harness_scratch("gemini").exists()
