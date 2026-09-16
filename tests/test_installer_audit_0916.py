"""Regression tests for the 2026-09-16 installer audit — one per fixed item.

§19 install streaming lives in `spec/backend-api.md`; the pipe hygiene it
mirrors is harness._invoke's (§8, `spec/agent-pipeline.md`)."""
import os
import signal
import sys
import threading

import pytest


def _reap(pidfile) -> None:
    """Kill the escaped grandchild the fake installer left behind — nothing
    else will, which is the whole point of the scenario."""
    try:
        os.kill(int(pidfile.read_text()), signal.SIGKILL)
    except (OSError, ValueError):
        pass


@pytest.mark.skipif(os.name == "nt", reason="POSIX session/grandchild semantics")
def test_stream_shell_timeout_unblocks_the_read_loop(monkeypatch, tmp_path, home):
    """§19: the install timer kills the group, but a daemonizing grandchild
    (`curl | bash` leaves those behind) keeps the merged pipe open. Closing the
    read end from the timer thread takes the buffer lock the blocked read loop
    holds — the timer wedged and the install hung until the escapee died."""
    from autowright import installer

    pidfile = tmp_path / "grandchild.pid"
    body = ("import subprocess, sys\n"
            "child = subprocess.Popen(\n"
            "    [sys.executable, '-c', 'import time; time.sleep(120)'],\n"
            "    start_new_session=True)\n"
            f"open({str(pidfile)!r}, 'w').write(str(child.pid))\n"
            "sys.exit(7)\n")
    monkeypatch.setattr(installer, "INSTALL_TIMEOUT_S", 1)
    done = threading.Event()
    outcome = []

    def run() -> None:
        try:
            installer._stream_shell([sys.executable, "-c", body],
                                    lambda **kw: None, "claude")
        except BaseException as e:  # noqa: BLE001 — the outcome is the assertion
            outcome.append(e)
        finally:
            done.set()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        assert done.wait(20), "_stream_shell wedged on the installer's pipe"
    finally:
        _reap(pidfile)
    worker.join(timeout=5)

    # The loop ended and proc.wait() was reached — so the timeout verdict got
    # raised instead of the call hanging for the grandchild's whole life.
    assert outcome and isinstance(outcome[0], RuntimeError)
    assert "timed out" in str(outcome[0])
