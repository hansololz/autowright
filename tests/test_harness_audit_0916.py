"""Regression tests for the 2026-09-16 harness audit — one per fixed item.

Each test names the spec rule it pins (§8 agent pipeline in
`spec/agent-pipeline.md`, §19 provider endpoints in `spec/backend-api.md`)."""
import json
import os
import threading
import time

import pytest
from conftest import _reap, fake_cli


# ---------- §8: the timeout kill defuses BOTH pipes ----------

def test_invoke_returns_when_an_escaped_child_holds_stderr(monkeypatch, tmp_path, home):
    """§8: a grandchild in its own session survives the group kill and keeps
    the inherited pipes open. The stderr drain thread then sits in a read that
    holds that pipe's buffer lock, and the cleanup's ordinary `close()` — which
    takes the same lock — used to wedge `_invoke` forever."""
    from autowright import harness

    pidfile = tmp_path / "grandchild.pid"
    script = fake_cli(tmp_path,
                      "import subprocess, sys\n"
                      "child = subprocess.Popen(\n"
                      "    [sys.executable, '-c', 'import time; time.sleep(120)'],\n"
                      "    start_new_session=True)\n"
                      f"open({str(pidfile)!r}, 'w').write(str(child.pid))\n"
                      "sys.exit(7)\n")
    monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))
    before = set(threading.enumerate())
    done = threading.Event()

    def run() -> None:
        try:
            harness.invoke({"harness": "Claude Code"}, "question: hi?", timeout=1)
        except harness.HarnessError:
            pass  # the timeout error itself is pinned elsewhere
        finally:
            done.set()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()

    try:
        # Bounded well under the grandchild's own life: unfixed, the close
        # blocks for as long as that escaped process holds the pipe.
        assert done.wait(20), "_invoke wedged closing the stderr pipe"
    finally:
        _reap(pidfile)
    worker.join(timeout=5)
    # Nothing is left blocked on the defused read end either.
    leftover = set(threading.enumerate()) - before - {worker}
    deadline = time.monotonic() + 5
    while leftover and time.monotonic() < deadline:
        time.sleep(0.05)
        leftover = set(threading.enumerate()) - before - {worker}
    assert not leftover


# ---------- §8: a scratch watcher that never started ----------

def test_scratch_watcher_stop_without_start_is_a_no_op(tmp_path):
    """§8: `stop()` runs from the call's finally, including on a path that
    failed before `start()` — join() on an unstarted thread raises
    RuntimeError and would mask the original error."""
    from autowright import harness

    watcher = harness._ScratchWatcher(tmp_path, harness.ProgressSink())

    watcher.stop()  # must not raise
    watcher.stop()  # and stays idempotent


# ---------- §19: the sign-in poll stays cheap ----------

def test_signin_state_skips_the_ollama_version_lookup(monkeypatch):
    """§19 `GET /agents/signin/{id}`: no version lookups — the poll answered
    installed/signedIn only, but still paid for `/api/version`."""
    from autowright import harness

    def boom():
        raise AssertionError("the sign-in poll must not look up the version")

    monkeypatch.setattr(harness, "_ollama_models", lambda: ["qwen3:8b"])
    monkeypatch.setattr(harness, "ollama_bin", lambda: "/usr/local/bin/ollama")
    monkeypatch.setattr(harness, "_ollama_version", boom)

    assert harness.signin_state("ollama") == {"installed": True, "signedIn": None}


def test_ollama_self_heal_spawns_once_for_concurrent_pollers(monkeypatch):
    """§19: the cooldown check and its stamp are one atomic step — two polls
    landing together used to both read the old stamp and each start their own
    `ollama serve`."""
    from autowright import harness

    calls = []
    calls_lock = threading.Lock()

    def models():
        with calls_lock:
            calls.append(1)
            n = len(calls)
        # Down for both pollers' first look, up by the wait loop's next one.
        return None if n <= 2 else ["qwen3:8b"]

    barrier = threading.Barrier(2, timeout=10)

    def binpath():
        barrier.wait()  # both pollers reach the cooldown check together
        return "/usr/local/bin/ollama"

    spawns = []

    class _FakeProc:
        pass

    monkeypatch.setattr(harness, "_ollama_models", models)
    monkeypatch.setattr(harness, "ollama_bin", binpath)
    monkeypatch.setattr(harness, "_serve_last_spawn", 0.0)
    monkeypatch.setattr(harness.subprocess, "Popen",
                        lambda argv, **kw: (spawns.append(argv), _FakeProc())[1])

    threads = [threading.Thread(target=lambda: harness.ollama_status(want_version=False),
                                daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
        assert not t.is_alive()

    assert len(spawns) == 1


# ---------- §8: tool names are normalized per harness ----------

def test_claude_bash_tool_is_reported_as_shell():
    """§8: tool names are "normalized to WebFetch / WebSearch / Shell where the
    handler knows them" — Claude Code's shell tool is named Bash."""
    from autowright import harness

    tools = []
    sink = harness.ProgressSink(on_tool=tools.append)
    handler = harness.ClaudeCodeHandler({"harness": "Claude Code"}, False)
    line = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
        {"type": "tool_use", "name": "WebSearch", "input": {"query": "weather"}},
    ]}})

    handler.line(line, sink)

    assert [t["name"] for t in tools] == ["Shell", "WebSearch"]
    assert tools[0]["input"] == {"command": "ls"}


# ---------- §8: EOF on stdout disarms the idle window ----------

@pytest.mark.skipif(os.name == "nt",
                    reason="the .cmd shim holds stdout open past the child's close")
def test_invoke_keeps_the_reply_of_a_cli_that_lingers_after_eof(monkeypatch, tmp_path,
                                                                home):
    """§8: "EOF on stdout disarms the window: the exit is then reaped under its
    own 30 s bound … so a complete reply from a CLI that lingers after closing
    stdout is never reported as a timeout". The window used to keep counting
    across the reap and kill a call whose answer had already arrived."""
    from autowright import harness

    script = fake_cli(tmp_path,
                      "import os, sys, time\n"
                      "sys.stdout.write('the whole answer\\n')\n"
                      "sys.stdout.flush()\n"
                      "os.close(1)\n"          # EOF, with the child still alive
                      "time.sleep(3)\n")
    monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))
    monkeypatch.setenv("AUTOWRIGHT_AGENT_TIMEOUT_S", "1")  # §15 idle window
    t0 = time.monotonic()

    out = harness.invoke({"harness": "Claude Code"}, "question: hi?")

    assert "the whole answer" in out
    # It really did outlive the window it would have been killed by.
    assert time.monotonic() - t0 > 1


# ---------- §19: overlapping sign-in polls share one probe ----------

def test_overlapping_signin_polls_share_one_probe(monkeypatch):
    """§19: "The provider's probe result is cached for 2 s behind a
    per-provider lock, so overlapping polls (a CLI cold start can outlast the
    poll interval) share one process instead of stacking"."""
    from autowright import harness

    probed = []
    running = threading.Event()

    def slow_probe(provider_id):
        probed.append(provider_id)
        running.set()
        time.sleep(0.5)
        return True

    monkeypatch.setattr(harness, "_signin_probe", slow_probe)
    answers = []

    def poll():
        answers.append(harness.signed_in("claude"))

    first = threading.Thread(target=poll, daemon=True)
    first.start()
    assert running.wait(5), "the first probe never started"
    second = threading.Thread(target=poll, daemon=True)
    second.start()
    for t in (first, second):
        t.join(timeout=10)

    assert probed == ["claude"]  # the second poll read the first's answer
    assert answers == [True, True]


# ---------- §8: one raising progress callback never kills the watcher ----------

def test_scratch_watcher_survives_a_raising_progress_callback(monkeypatch, tmp_path):
    """§8 live progress: the poll calls the caller's `on_file` callback, and a
    raising one used to take the watcher thread down with it — every document
    that landed afterwards went unreported for the rest of the call."""
    from autowright import harness

    monkeypatch.setattr(harness, "_SCRATCH_POLL_S", 0.05)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    seen: list[str] = []

    def on_file(name, content):
        seen.append(name)
        if len(seen) == 1:
            raise RuntimeError("the progress callback blew up")

    watcher = harness._ScratchWatcher(scratch, harness.ProgressSink(on_file=on_file))
    (scratch / "01-first.py").write_text("one\n", encoding="utf-8")
    watcher.start()
    try:
        deadline = time.monotonic() + 5
        while "01-first.py" not in seen and time.monotonic() < deadline:
            time.sleep(0.02)
        assert seen == ["01-first.py"], "the first document never reached the sink"
        (scratch / "02-second.py").write_text("two\n", encoding="utf-8")
        deadline = time.monotonic() + 5
        while "02-second.py" not in seen and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        watcher.stop()

    assert "02-second.py" in seen  # the thread outlived the raising callback
    assert [name for name, _text in watcher.documents()] == ["01-first.py",
                                                             "02-second.py"]
