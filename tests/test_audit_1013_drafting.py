"""Regression tests for the 2026-10-13 drafting / harness / installer /
packages fixes — one block per fixed item.

Each test names the spec rule it pins (§8 agent pipeline in
`spec/agent-pipeline.md`, §19 installers in `spec/backend-api.md`, §6.2
packages in `spec/engine.md`)."""
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from conftest import fake_cli

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX session groups")


def _kill_pid_file(path):
    try:
        os.kill(int(path.read_text().strip()), signal.SIGKILL)
    except (OSError, ValueError):
        pass


# ---------- §8: a job cancel releases the pipes ----------

@posix_only
def test_job_cancel_releases_pipes_an_escapee_holds(monkeypatch, tmp_path, home):
    """§8: cancelling the job kills the harness process AND releases its
    pipes (the release travels with the held process) — an escapee in its own
    session holding stdout can't keep the call alive until its idle window."""
    from autowright import drafting, harness

    pid_file = tmp_path / "escapee.pid"
    script = fake_cli(tmp_path,
                       "import subprocess, sys, time\n"
                       "p = subprocess.Popen([sys.executable, '-c',\n"
                       "                      'import time; time.sleep(60)'],\n"
                       "                     start_new_session=True)\n"
                       f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
                       "print('started', flush=True)\n"
                       "time.sleep(60)\n")
    monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))
    holder: dict = {}
    outcome: dict = {}

    def _call():
        try:
            outcome["out"] = harness.invoke({"harness": "Claude Code"}, "question: hi?",
                                            timeout=60, proc_holder=holder)
        except Exception as e:  # noqa: BLE001
            outcome["error"] = e

    thread = threading.Thread(target=_call, daemon=True)
    try:
        thread.start()
        deadline = time.monotonic() + 15
        while not pid_file.exists() or not pid_file.read_text().strip():
            assert time.monotonic() < deadline, "fake CLI never started"
            time.sleep(0.05)
        assert callable(holder.get("release"))
        t0 = time.monotonic()
        drafting.DraftJobs._kill_harness({"_proc": holder})
        thread.join(timeout=15)
        assert not thread.is_alive(), "the cancel left the call blocked on the escapee"
        assert time.monotonic() - t0 < 15
        assert "error" in outcome
    finally:
        _kill_pid_file(pid_file)


@posix_only
def test_shutdown_kill_releases_pipes_an_escapee_holds(monkeypatch, tmp_path, home):
    """§3/§8: the shutdown SIGKILL of every building job also releases its
    pipes, so a drafting thread never outlives the shutdown box blocked on an
    escapee holding stdout."""
    from autowright import drafting, harness

    pid_file = tmp_path / "escapee.pid"
    script = fake_cli(tmp_path,
                       "import subprocess, sys, time\n"
                       "p = subprocess.Popen([sys.executable, '-c',\n"
                       "                      'import time; time.sleep(60)'],\n"
                       "                     start_new_session=True)\n"
                       f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
                       "print('started', flush=True)\n"
                       "time.sleep(60)\n")
    monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))
    holder: dict = {}
    outcome: dict = {}

    def _call():
        try:
            outcome["out"] = harness.invoke({"harness": "Claude Code"}, "question: hi?",
                                            timeout=60, proc_holder=holder)
        except Exception as e:  # noqa: BLE001
            outcome["error"] = e

    jobs = drafting.DraftJobs()
    jobs.jobs["job-1"] = {"status": "building", "_proc": holder}
    thread = threading.Thread(target=_call, daemon=True)
    try:
        thread.start()
        deadline = time.monotonic() + 15
        while not pid_file.exists() or not pid_file.read_text().strip():
            assert time.monotonic() < deadline, "fake CLI never started"
            time.sleep(0.05)
        t0 = time.monotonic()
        jobs.kill_all_building()
        thread.join(timeout=15)
        assert not thread.is_alive(), "shutdown left the call blocked on the escapee"
        assert time.monotonic() - t0 < 15
        assert "error" in outcome
        assert jobs.jobs["job-1"]["status"] == "cancelled"
    finally:
        _kill_pid_file(pid_file)


def test_holder_release_makes_a_blocked_read_see_eof(monkeypatch, tmp_path, home):
    """§8: the held release is the watchdog kill's pipe half — calling it
    alone (no kill) ends a blocked stdout read with EOF."""
    from autowright import harness

    script = fake_cli(tmp_path,
                       "import time\nprint('one', flush=True)\ntime.sleep(60)\n")
    monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))
    monkeypatch.setattr(harness, "REAP_TIMEOUT_S", 0.5)
    holder: dict = {}
    outcome: dict = {}

    def _call():
        try:
            outcome["out"] = harness.invoke({"harness": "Claude Code"}, "question: hi?",
                                            timeout=60, proc_holder=holder)
        except Exception as e:  # noqa: BLE001
            outcome["error"] = e

    thread = threading.Thread(target=_call, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while "release" not in holder:
        assert time.monotonic() < deadline
        time.sleep(0.02)
    time.sleep(0.5)  # let the read loop block on the silent child
    holder["release"]()
    # EOF ends the loop; the lingering child is killed after the (short) reap.
    thread.join(timeout=15)
    assert not thread.is_alive()
    assert holder["proc"].returncode is not None


# ---------- §8: setup failures after the spawn never leave the child ----------

def test_a_reader_construction_failure_kills_and_reaps_the_child(monkeypatch, tmp_path,
                                                                 home):
    """§8: everything built after the spawn is inside the call's own try — a
    failure there (fd exhaustion) kills the group and reaps, then re-raises."""
    from autowright import harness

    script = fake_cli(tmp_path, "import time\ntime.sleep(60)\n")
    monkeypatch.setattr(harness, "resolve_bin", lambda name: str(script))

    def _boom(self, f, cap=2_000_000):
        raise OSError("Too many open files")

    monkeypatch.setattr(harness.PipeReader, "__init__", _boom)
    killed = []
    real_kill = harness.kill_group

    def _recording_kill(proc, sig=None):
        killed.append(proc)
        real_kill(proc, sig)

    monkeypatch.setattr(harness, "kill_group", _recording_kill)
    holder: dict = {}
    t0 = time.monotonic()
    with pytest.raises(OSError, match="Too many open files"):
        harness.invoke({"harness": "Claude Code"}, "question: hi?", timeout=60,
                       proc_holder=holder)
    assert time.monotonic() - t0 < 15
    assert killed == [holder["proc"]]
    assert holder["proc"].returncode is not None


# ---------- §8: documents() lists accepted names the watcher never polled ----------

def test_documents_includes_an_unpolled_accepted_document(tmp_path):
    from autowright import harness

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    watcher = harness._ScratchWatcher(scratch, harness.ProgressSink(), "Codex")
    (scratch / "notes.md").write_text("- polled\n", encoding="utf-8")
    watcher._poll()
    # Written after the last poll (a stop whose join timed out skips the
    # final sweep): still on disk, so still in the recombined envelope.
    (scratch / "spec.md").write_text("# spec\n", encoding="utf-8")
    (scratch / "01-fetch.py").write_text("print(1)\n", encoding="utf-8")
    (scratch / "stray.txt").write_text("not a document\n", encoding="utf-8")
    (scratch / "actions.yaml").mkdir()  # never a directory
    assert watcher.documents() == [("notes.md", "- polled\n"),
                                   ("01-fetch.py", "print(1)\n"),
                                   ("spec.md", "# spec\n")]


# ---------- §19: `curl … | sh` installers run from a file ----------

def test_a_failed_installer_download_never_runs_the_script(monkeypatch):
    from autowright import installer

    calls = []

    def fake_stream(cmd, emit, provider_id, env_extra=None):
        calls.append(list(cmd))
        raise RuntimeError("curl: (6) Could not resolve host: claude.ai")

    monkeypatch.setattr(installer, "_stream_shell", fake_stream)
    monkeypatch.setattr(installer, "_require",
                        lambda b: pytest.fail("a failed download must not pass"))
    with pytest.raises(RuntimeError) as ei:
        installer._install_claude(lambda **k: None)
    assert str(ei.value) == ("couldn't download the installer: "
                             "curl: (6) Could not resolve host: claude.ai")
    assert len(calls) == 1 and "curl -fsSL -o" in calls[0][2]


def test_a_downloaded_installer_runs_from_the_file_and_is_removed(monkeypatch):
    from autowright import installer

    seen = {}

    def fake_stream(cmd, emit, provider_id, env_extra=None):
        if cmd[0] == "/bin/bash" and cmd[1] == "-c":
            # the download: curl writes the script to the temp file
            script = cmd[2].split(" -o ", 1)[1].split(" ", 1)[0]
            with open(script, "w", encoding="utf-8") as f:
                f.write("echo installed\n")
            seen["download"] = script
            return
        seen["run"], seen["env"] = list(cmd), env_extra
        with open(cmd[1], encoding="utf-8") as f:
            seen["content"] = f.read()

    monkeypatch.setattr(installer, "_stream_shell", fake_stream)
    monkeypatch.setattr(installer, "_require", lambda b: None)
    monkeypatch.setattr(installer, "_ensure_login_path", lambda emit: None)
    installer._install_codex(lambda **k: None)
    assert seen["run"] == ["/bin/sh", seen["download"]]
    assert seen["content"] == "echo installed\n"
    assert seen["env"] == {"CODEX_NON_INTERACTIVE": "1"}
    assert not os.path.exists(seen["download"])  # removed in the finally


# ---------- §6.2: pip's exit, not its pipes' EOF, ends the wait ----------

@posix_only
def test_a_pip_that_exits_while_a_helper_holds_its_pipes_succeeds(monkeypatch, home,
                                                                  tmp_path):
    from autowright import packages

    real_popen = subprocess.Popen
    pid_file = tmp_path / "helper.pid"
    body = ("import subprocess, sys\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],\n"
            "                     start_new_session=True)\n"
            f"open({str(pid_file)!r}, 'w').write(str(p.pid))\n"
            "print('Successfully installed leftpad', flush=True)\n")

    def fake_pip(cmd, **kwargs):
        return real_popen([sys.executable, "-c", body], **kwargs)

    monkeypatch.setattr(packages.subprocess, "Popen", fake_pip)
    try:
        t0 = time.monotonic()
        assert packages._pip_install("leftpad") is None
        assert time.monotonic() - t0 < 10
    finally:
        _kill_pid_file(pid_file)
