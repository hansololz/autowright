"""Regression tests for the 2026-09-16 installer audit — one per fixed item.

§19 install streaming lives in `spec/backend-api.md`; the pipe hygiene it
mirrors is harness._invoke's (§8, `spec/agent-pipeline.md`)."""
import os
import sys
import threading
import time

import pytest
from conftest import _reap


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


# ---------- §19: an abandoned phase's late progress never reaches a later job ----------

def _await_state(provider_id: str, state: str) -> dict:
    """Poll `status` until the background job reaches `state`."""
    from autowright import installer

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        snap = installer.status(provider_id)
        if snap["state"] == state:
            return snap
        time.sleep(0.01)
    raise AssertionError(f"the {provider_id} job never reached {state}")


def test_late_progress_from_an_abandoned_phase_is_discarded(monkeypatch):
    """§19: "the abandoned phase's late progress is discarded (each job carries
    a generation token its emitter checks), so it can never write into a later
    install's snapshot". A timed-out phase keeps running — a Python thread
    can't be killed — and its emitter used to overwrite the retry's state and
    stream its lines to the UI."""
    from autowright import installer

    monkeypatch.setattr(installer, "_jobs", {})
    emitters = []

    def recipe(emit) -> None:
        emitters.append(emit)

    monkeypatch.setitem(installer._INSTALLERS, "claude", recipe)

    assert installer.start("claude", lambda **kw: None) is True
    _await_state("claude", "done")
    events = []
    assert installer.start("claude", lambda **kw: events.append(kw)) is True
    _await_state("claude", "done")
    events.clear()
    snapshot = installer.status("claude")
    assert "_gen" not in snapshot  # the token is never part of the §19 shape

    emitters[0](line="the abandoned phase, minutes late", percent=99)

    assert installer.status("claude") == snapshot
    assert events == []

    # The live job's own emitter still works — only the stale one is muted.
    emitters[1](line="still the current job", percent=50)
    assert installer.status("claude")["line"] == "still the current job"
    assert events == [{"line": "still the current job", "percent": 50,
                       "done": False}]
