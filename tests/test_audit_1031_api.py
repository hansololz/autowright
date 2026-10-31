"""2026-10-31 sweep audit batch (API routes, test execution, entry point):
the WebSocket pump ending quietly when the client went away mid-send, the
duplicate gate publishing a flag it cleared, the test memory copy stopping
per file on a cancel, and the backend importing with an unknown
OTEL_PROPAGATORS name in its environment."""
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import make_version


# ---------- A2: the WebSocket pump and a client that went away ----------

@pytest.mark.parametrize("disconnect", ["fastapi", "starlette"])
def test_ws_a_send_to_a_gone_client_ends_the_pump_without_an_error(
        client, monkeypatch, caplog, disconnect):
    from fastapi import WebSocketDisconnect
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocket, WebSocketDisconnected

    from autowright import api

    sends = []

    def gone_send_text(self, data):
        sends.append(data)
        if disconnect == "fastapi":
            raise WebSocketDisconnect(code=1001)
        raise WebSocketDisconnected("the client went away")

    caplog.set_level(logging.DEBUG, logger="autowright.api")
    try:
        with TestClient(api.app) as c:
            with c.websocket_connect(f"/ws?token={api.AUTH_TOKEN}"):
                monkeypatch.setattr(WebSocket, "send_text", gone_send_text)
                api.hub.publish("test.ping", n=1)
                deadline = time.time() + 5
                while not sends and time.time() < deadline:
                    time.sleep(0.01)
                assert sends
                time.sleep(0.05)
    finally:
        api.hub._loop = None
    assert not [r for r in caplog.records
                if r.name == "autowright.api" and r.levelno >= logging.ERROR]


# ---------- A4: the duplicate gate publishes a cleared flag ----------

def test_a_duplicate_that_proceeds_after_a_cleared_flag_publishes_the_source(
        client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Dup clear", "mock")
    script = store.auto_dir(a) / "versions" / "v1" / "02-finish.py"
    original = script.read_text(encoding="utf-8")
    script.write_text("print('hand')\n", encoding="utf-8")
    assert client.post(f"/automations/{a['id']}/duplicate", json={}).status_code == 409
    assert a["versions"][1]["steps"][1].get("modifiedOnDisk")

    script.write_text(original, encoding="utf-8")  # the edit reverted: the flag clears
    published = []
    monkeypatch.setattr(api, "_publish_auto_changed", lambda auto: published.append(auto["id"]))
    r = client.post(f"/automations/{a['id']}/duplicate", json={})
    assert r.status_code == 201, r.text
    assert a["id"] in published
    assert r.json()["id"] in published


# ---------- A3: the test memory copy stops per file ----------

def test_the_copy_function_aborts_before_a_file_lands(tmp_path):
    from autowright import testexec

    source = tmp_path / "memory"
    source.mkdir()
    for n in range(3):
        (source / f"f{n}.md").write_text(str(n), encoding="utf-8")
    state = {"cancel": True}
    destination = tmp_path / "copy"
    with pytest.raises(testexec._CopyAborted):
        testexec.shutil.copytree(source, destination,
                                 copy_function=testexec._copy_file_stop_check(state))
    assert not any(destination.glob("*.md"))


def test_the_copy_function_copies_when_not_stopped(tmp_path):
    from autowright import testexec

    source = tmp_path / "memory"
    source.mkdir()
    (source / "notes.md").write_text("top", encoding="utf-8")
    destination = tmp_path / "copy"
    testexec.shutil.copytree(source, destination,
                             copy_function=testexec._copy_file_stop_check({}))
    assert (destination / "notes.md").read_text(encoding="utf-8") == "top"


# ---------- A5: an unknown OTEL_PROPAGATORS name ----------

def test_the_entry_point_imports_with_an_unknown_otel_propagator():
    backend = Path(__file__).resolve().parent.parent / "backend"
    env = {**os.environ, "OTEL_PROPAGATORS": "b3",
           "PYTHONPATH": os.pathsep.join(
               [str(backend), *filter(None, [os.environ.get("PYTHONPATH")])])}
    r = subprocess.run([sys.executable, "-c", "import autowright.main"],
                       env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
