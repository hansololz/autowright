"""2026-10-30 second-sweep audit batch (API routes, test execution, request
log): the WebSocket pump's str() fallback and 1011 close on a failed send,
the identical save that reseals a hand edit made after load, a cancel that
stops the test's memory copy, the duplicate/restore gates publishing a
flipped step flag, the retry route's 404 for an unreadable record body, the
/validate-steps stale-id 404 ahead of the request model, a truncated text
body that never logs as binary, and DELETE /secrets sweeping the rows the
load skipped as malformed."""
import datetime
import time

import pytest
import yaml
from conftest import make_version


def _draft():
    return {"steps": [{"file": "01-ok.py", "name": "Ok", "description": "",
                       "code": "from autowright import result\nresult.status('ok')\n"}],
            "params": [], "packages": [], "spec": []}


def _wait_settled(engine, store, execution_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        h = store.execs.get(execution_id)
        if not engine.is_live(execution_id) and (h is None or h["status"] != "executing"):
            return h
        time.sleep(0.02)
    raise AssertionError("the test never settled")


# ---------- A1: the WebSocket pump ----------

def test_ws_sends_a_date_value_as_a_string(client):
    from fastapi.testclient import TestClient

    from autowright import api

    try:
        with TestClient(api.app) as c:
            with c.websocket_connect(f"/ws?token={api.AUTH_TOKEN}") as sock:
                api.hub.publish("test.dated", day=datetime.date(2026, 10, 30))
                assert sock.receive_json() == {"event": "test.dated", "day": "2026-10-30"}
                # The stream is still alive for the next event.
                api.hub.publish("test.ping", n=1)
                assert sock.receive_json() == {"event": "test.ping", "n": 1}
    finally:
        api.hub._loop = None


def test_ws_a_failed_send_closes_the_socket_1011(client, monkeypatch):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocket, WebSocketDisconnect

    from autowright import api

    def failing_send_text(self, data):
        raise RuntimeError("send failed")

    try:
        with TestClient(api.app) as c:
            with c.websocket_connect(f"/ws?token={api.AUTH_TOKEN}") as sock:
                monkeypatch.setattr(WebSocket, "send_text", failing_send_text)
                api.hub.publish("test.ping", n=1)
                with pytest.raises(WebSocketDisconnect) as exc:
                    sock.receive_json()
                assert exc.value.code == 1011
    finally:
        api.hub._loop = None


# ---------- A2: an identical save reseals a hand edit made after load ----------

def test_identical_save_mints_over_a_hand_edit_made_after_load(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Late edit", "mock")
    vd = store.auto_dir(a) / "versions" / "v1"
    # Edited on disk after the load: the loaded flag never learns of it.
    (vd / "02-finish.py").write_text("print('hand')\n", encoding="utf-8")
    assert not a["versions"][1]["steps"][1].get("modifiedOnDisk")
    r = client.post(f"/automations/{a['id']}/versions", json={"draft": make_version()})
    assert r.status_code == 200, r.text
    assert a["current_version"] == 2
    assert not any(s.get("modifiedOnDisk") for s in a["versions"][2]["steps"])


# ---------- A3: a cancel stops the test's setup ----------

def test_a_cancel_during_the_memory_copy_stops_it(client, monkeypatch):
    from autowright import api, engine as engine_module, paths, testexec
    from autowright.storage import store

    memory = paths.pending_draft_dir() / "memory"
    (memory / "sub").mkdir(parents=True)
    (memory / "notes.md").write_text("top", encoding="utf-8")
    (memory / "sub" / "deep.md").write_text("deep", encoding="utf-8")
    spawned = []
    monkeypatch.setattr(engine_module, "run_step_process",
                        lambda *args, **kwargs: spawned.append(1) or 0)
    holder: dict = {}
    visited: list[str] = []
    real_copytree = testexec.shutil.copytree

    def cancelling_copytree(src, dst, *args, ignore=None, **kwargs):
        def hook(directory, names):
            visited.append(directory)
            if len(visited) == 1:
                assert api.engine.cancel(holder["id"]) is True
            return ignore(directory, names) if ignore is not None else []
        return real_copytree(src, dst, *args, ignore=hook, **kwargs)

    monkeypatch.setattr(testexec.shutil, "copytree", cancelling_copytree)
    with store.lock:
        holder["id"] = testexec.start(api.engine, _draft(), None, [], [], {})
    execution_id = holder["id"]
    h = _wait_settled(api.engine, store, execution_id)
    assert h["status"] == "cancelled"
    assert h["note"] == "cancelled before it ran"
    assert len(visited) == 1  # the copy stopped at the first directory
    assert spawned == []
    assert not (paths.tests_scratch_dir() / execution_id).exists()


def test_a_cancel_while_step_files_are_written_skips_the_memory_copy(client, monkeypatch):
    from autowright import api, engine as engine_module, paths, testexec
    from autowright.storage import store

    memory = paths.pending_draft_dir() / "memory"
    memory.mkdir(parents=True)
    (memory / "notes.md").write_text("top", encoding="utf-8")
    spawned = []
    monkeypatch.setattr(engine_module, "run_step_process",
                        lambda *args, **kwargs: spawned.append(1) or 0)
    holder: dict = {}
    copies = []
    real_write = testexec.atomic_write_text

    def write_then_cancel(path, text, *args, **kwargs):
        real_write(path, text, *args, **kwargs)
        api.engine.cancel(holder["id"])

    monkeypatch.setattr(testexec, "atomic_write_text", write_then_cancel)
    monkeypatch.setattr(testexec.shutil, "copytree",
                        lambda *args, **kwargs: copies.append(1))
    with store.lock:
        holder["id"] = testexec.start(api.engine, _draft(), None, [], [], {})
    h = _wait_settled(api.engine, store, holder["id"])
    assert h["status"] == "cancelled"
    assert h["note"] == "cancelled before it ran"
    assert copies == [] and spawned == []


# ---------- A4: the duplicate and restore gates publish a flipped flag ----------

def test_the_duplicate_gate_publishes_the_flag_it_set(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Dup gate", "mock")
    (store.auto_dir(a) / "versions" / "v1" / "02-finish.py").write_text(
        "print('hand')\n", encoding="utf-8")
    published = []
    monkeypatch.setattr(api, "_publish_auto_changed", lambda auto: published.append(auto["id"]))
    assert client.post(f"/automations/{a['id']}/duplicate", json={}).status_code == 409
    assert published == [a["id"]]
    # Already flagged: a second refusal flips nothing and publishes nothing.
    assert client.post(f"/automations/{a['id']}/duplicate", json={}).status_code == 409
    assert published == [a["id"]]


def test_the_restore_gate_publishes_the_flag_it_set(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Restore gate", "mock")
    store.save_new_version(a, make_version(note="v2"))
    (store.auto_dir(a) / "versions" / "v1" / "01-say.py").write_text(
        "print('hand')\n", encoding="utf-8")
    published = []
    monkeypatch.setattr(api, "_publish_auto_changed", lambda auto: published.append(auto["id"]))
    r = client.post(f"/automations/{a['id']}/restore", json={"version": 1})
    assert r.status_code == 422
    assert published == [a["id"]]


# ---------- A5: retry of an unreadable record body is a 404 ----------

def test_retry_maps_an_unreadable_record_body_to_404(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Retry body", "mock")
    h = store.create_execution(a, "version", 1, "manual", [], status="failed")
    store.update_execution(h)
    a["_live"].discard(h["id"])

    def missing_body(auto, record):
        raise LookupError("execution not found")

    monkeypatch.setattr(api.engine, "retry", missing_body)
    r = client.post(f"/executions/{h['id']}/retry")
    assert r.status_code == 404
    assert r.json()["detail"] == "execution not found"

    def other_lookup(auto, record):
        raise LookupError("something else")

    monkeypatch.setattr(api.engine, "retry", other_lookup)
    with pytest.raises(LookupError):
        client.post(f"/executions/{h['id']}/retry")


def test_retry_keeps_the_409_for_a_vanished_version(client, monkeypatch):
    from autowright import api
    from autowright.engine import VersionNotFound
    from autowright.storage import store

    a = store.create_automation(make_version(), "Retry version", "mock")
    h = store.create_execution(a, "version", 1, "manual", [], status="failed")
    store.update_execution(h)
    a["_live"].discard(h["id"])

    def vanished(auto, record):
        raise VersionNotFound("version not found")

    monkeypatch.setattr(api.engine, "retry", vanished)
    r = client.post(f"/executions/{h['id']}/retry")
    assert r.status_code == 409
    assert r.json()["detail"] == "version v1 no longer exists"


# ---------- A6: /validate-steps resolves the id first ----------

def test_validate_steps_stale_id_is_404_even_with_a_mistyped_step(client):
    draft = _draft()
    draft["steps"][0]["timeout"] = "60"
    r = client.post("/validate-steps", json={"draft": draft, "automationId": "no-such-id"})
    assert r.status_code == 404


# ---------- A7: a truncated text body is never logged as binary ----------

def test_a_body_cut_mid_character_is_still_text():
    from autowright import reqlog

    full = ("a" * 10 + "— tail").encode("utf-8")
    cut = full[:11]  # the em dash's lead byte only
    text = reqlog._body_text(cut, len(full), False)
    assert not text.startswith("<binary")
    assert text.startswith("a" * 10)
    assert "truncated" in text
    cut = full[:12]  # two of the three bytes
    assert reqlog._body_text(cut, len(full), False).startswith("a" * 10 + "\n")
    # A complete body that is really not UTF-8 stays binary.
    assert reqlog._body_text(b"\xff\xfe", 2, False) == "<binary, 2 bytes>"


# ---------- S3: DELETE /secrets sweeps the skipped rows too ----------

def test_delete_all_secrets_sweeps_the_rows_the_load_skipped(client):
    from autowright import keychain, paths
    from autowright.storage import store

    client.post("/secrets", json={"name": "KEPT", "value": "v"})
    keychain.set_secret("skipped-id", "orphan")
    store._skipped_secrets = [{"id": "skipped-id"}, {"name": "NO_ID"}, {"id": 5, "name": ""}]
    r = client.delete("/secrets")
    assert r.status_code == 200 and r.json() == {"deleted": 1}
    assert keychain.get_secret("skipped-id") is None
    assert store._skipped_secrets == []
    assert yaml.safe_load(paths.secrets_file().read_text(encoding="utf-8")) == {"secrets": []}
