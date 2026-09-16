"""§19 backend-audit regressions (2026-09-16): request-model coverage for the
§4.4 draft content, the one-sentence model 422, the auth and data-path input
guards, the §3 shutdown sweep, and the delete paths' disk-error answers."""
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import make_version


def _draft(**over):
    """A minimal valid §4.4 draft payload — the shape every route below takes."""
    d = {"name": "Typed", "steps": [
        {"file": "01-ok.py", "name": "Ok", "description": "",
         "code": "from autowright import result\nresult.status('ok')\n"}]}
    d.update(over)
    return d


# ---------- §19 request validation: the draft's content is typed ----------

def test_mistyped_draft_spec_is_422_before_anything_is_written(client, home):
    """§19: a wrong draft shape answers 422 before any file is written — no
    half-written automation directory is left behind."""
    from autowright import paths

    r = client.post("/automations", json={"draft": _draft(spec="hello")})
    assert r.status_code == 422
    assert "spec" in r.json()["detail"]
    assert list(paths.automations_dir().iterdir()) == []


def test_mistyped_draft_notes_is_422(client):
    r = client.post("/automations", json={"draft": _draft(notes=5)})
    assert r.status_code == 422
    assert "notes" in r.json()["detail"]


@pytest.mark.parametrize("draft", [{"spec": "hello"}, {"notes": 5},
                                   {"params": "greeting"}, {"packages": "pandas"}])
def test_mistyped_draft_content_is_422_on_every_draft_route(client, draft):
    """The same shapes are refused wherever a §4.4 draft travels."""
    from autowright.storage import store

    a = store.create_automation(make_version(), "Typed", "mock")
    body = {"draft": _draft(**draft)}
    assert client.post("/automations", json=body).status_code == 422
    assert client.post(f"/automations/{a['id']}/versions", json=body).status_code == 422
    assert client.put(f"/draft/{a['id']}", json=body).status_code == 422
    assert client.put("/draft/pending", json=body).status_code == 422


def test_a_valid_draft_still_saves_its_typed_content(client):
    """The typed keys still ride through — and key presence is preserved."""
    from autowright.storage import store

    r = client.post("/automations", json={"draft": _draft(
        spec=[{"kind": "h1", "text": "Typed"}], notes="what I know",
        params=[], packages=[])})
    assert r.status_code == 200
    a = store.autos[r.json()["id"]]
    assert a["versions"][1]["spec"] == [{"kind": "h1", "text": "Typed"}]
    assert a["versions"][1]["notes"] == "what I know"


# ---------- §19: a model 422 reads like a handler 422 ----------

def test_model_422_detail_is_one_sentence(client):
    """§19: pydantic's per-field list is flattened to `<field>: <reason>` —
    every 422 carries a string `detail`."""
    from autowright.storage import store

    a = store.create_automation(make_version(), "Sentence", "mock")
    r = client.patch(f"/automations/{a['id']}", json={"maxParallel": 0})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, str)
    assert detail.startswith("maxParallel: ")


def test_model_422_joins_several_fields_with_semicolons(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Both", "mock")
    r = client.patch(f"/automations/{a['id']}", json={"maxParallel": 0, "maxQueued": -1})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "maxParallel: " in detail and "maxQueued: " in detail
    assert "; " in detail


# ---------- §19 auth: a non-ASCII token never crashes the compare ----------

def test_non_ascii_bearer_token_is_401(client):
    """`secrets.compare_digest` raises TypeError on a non-ASCII str — the
    token is rejected before the compare, not 500."""
    # Sent as bytes: a header travels as bytes on the wire, and starlette hands
    # the handler the latin-1 decoding — a non-ASCII str either way.
    r = client.get("/state", headers={"Authorization": "Bearer é".encode()})
    assert r.status_code == 401


def test_ws_rejects_a_non_ascii_token(client):
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/ws?token=%C3%A9"):
            pass
    assert exc.value.code == 4401


# ---------- §19 POST /settings/data-path: a bad path is a 422 ----------

def test_data_path_with_a_nul_byte_is_422(client):
    """pathlib raises ValueError (not OSError) for an embedded NUL — a bad
    path either way, never a 500."""
    r = client.post("/settings/data-path", json={"path": "/tmp/nope\x00here"})
    assert r.status_code == 422
    assert "can't create that directory" in r.json()["detail"]


# ---------- §3 shutdown: one failing sweep never skips the rest ----------

def test_a_failing_kill_sweep_still_runs_the_later_shutdown_work(monkeypatch, home):
    """§3: `kill_all_live` raising must not keep the drafting harnesses alive
    or leave backend.json behind — every sweep is guarded on its own."""
    from fastapi.testclient import TestClient

    from autowright import api
    from autowright.storage import store

    store.load_all()
    ran = []

    def boom():
        raise RuntimeError("kill failed")

    monkeypatch.setattr(api.engine, "kill_all_live", boom)
    monkeypatch.setattr(api.draft_jobs, "kill_all_building", lambda: ran.append("drafting"))
    monkeypatch.setattr(api, "_kill_pull_procs", lambda: ran.append("pulls"))
    api.register_shutdown(lambda: ran.append("cleanup"))
    try:
        with TestClient(api.app):
            pass
    finally:
        api.hub._loop = None
    assert ran == ["drafting", "pulls", "cleanup"]


# ---------- §19 registered-object guard: a write racing a DELETE is 404 ----------

def test_a_patch_racing_a_delete_answers_404_and_writes_nothing(client, monkeypatch):
    """§19: the record was resolved before the DELETE landed — the store
    refuses the write rather than re-creating the removed directory."""
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Raced", "mock")
    # The DELETE lands between the route's lookup and its store write.
    monkeypatch.setattr(api, "_check_agent_refs",
                        lambda agent_id, step_agents: store.delete_automation(a))
    r = client.patch(f"/automations/{a['id']}", json={"agentId": "mock"})
    assert r.status_code == 404
    assert r.json()["detail"] == "automation not found"
    store.drain_reaper()
    assert not store.auto_dir(a).exists()


# ---------- §19 delete paths: a disk error is a 409, never a silent drop ----------

def _rename_denied(monkeypatch, name: str):
    """Make one directory's aside-rename fail the way a locked or read-only
    volume makes it fail; every other rename runs for real."""
    real = Path.rename

    def rename(self, target):
        if self.name == name:
            raise PermissionError(13, "Permission denied")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", rename)


def test_delete_automation_answers_409_and_keeps_the_record(client, monkeypatch):
    """§19: the directory couldn't go — the automation stays registered, the
    `_deleting` flag goes back, and the client is told why."""
    from autowright.storage import store

    a = store.create_automation(make_version(), "Stuck", "mock")
    _rename_denied(monkeypatch, a["id"])
    r = client.delete(f"/automations/{a['id']}")
    assert r.status_code == 409
    assert r.json()["detail"] == "couldn't remove it from disk: Permission denied"
    assert store.autos.get(a["id"]) is a
    assert "_deleting" not in a  # the record still fires


def test_clear_memory_answers_409_when_the_directory_cannot_go(client, monkeypatch):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Held memory", "mock")
    _rename_denied(monkeypatch, "memory")
    r = client.post(f"/automations/{a['id']}/memory/clear")
    assert r.status_code == 409
    assert r.json()["detail"] == "couldn't remove it from disk: Permission denied"
    assert (store.auto_dir(a) / "memory").exists()


# ---------- §11 tests: admission and the started/deleted event pair ----------

def test_a_test_that_cannot_start_its_thread_publishes_execution_deleted(client, monkeypatch):
    """§19: the row `execution.started` put in the §7 list has to leave it
    again when the launch fails — the record is deleted either way."""
    from autowright import api, testexec
    from autowright.storage import store

    class _BoomThread:
        def __init__(self, *a, **kw):
            pass

        def start(self):
            raise RuntimeError("no thread for you")

    monkeypatch.setattr(testexec, "threading", SimpleNamespace(Thread=_BoomThread))
    published = []
    monkeypatch.setattr(api.hub, "publish", lambda ev, **kw: published.append({"event": ev, **kw}))
    r = client.post("/tests", json={"draft": _draft()})
    assert r.status_code == 409
    started = [e["executionId"] for e in published if e["event"] == "execution.started"]
    assert len(started) == 1  # the row really was announced before the failure
    assert [e["executionId"] for e in published if e["event"] == "execution.deleted"] == started
    assert not [h for h in store.execs.values() if h["kind"] == "test"]
    assert threading.Thread is not _BoomThread  # the real module is untouched


def test_a_test_is_refused_while_the_backend_is_shutting_down(client):
    """§3 drain: past the kill sweep nothing new may start — a test refuses
    exactly like `engine.start` does, so the §19 route answers the same 409."""
    from autowright import api

    api.engine._stopping = True  # reset by the conftest fixture after the test
    r = client.post("/tests", json={"draft": _draft()})
    assert r.status_code == 409
    assert r.json()["detail"] == "the backend is shutting down"


# ---------- §22.7 catalog save: a row that moved mid-save is a 409 ----------

def test_catalog_save_route_answers_409_when_the_catalog_moved(client, tmp_path, monkeypatch):
    """§22.7: the archives went beside the location the save started from, so a
    row pointed somewhere else meanwhile is refused — a 409, not a 422."""
    from autowright import api, marketplace
    from autowright.storage import store

    a = store.create_automation(make_version(), "Watcher", "mock")
    folder = tmp_path / "shelf"
    folder.mkdir()
    source = client.post("/marketplace/catalogs", json={"folder": str(folder)}).json()
    moved = tmp_path / "moved"
    moved.mkdir()
    (folder / marketplace.CATALOG_FILENAME).rename(moved / marketplace.CATALOG_FILENAME)

    def export(automation_id):
        # the user points the row somewhere else while the export runs
        api.marketplace_store.update_settings(
            source["id"], location=str(moved / marketplace.CATALOG_FILENAME))
        return "Watcher", b"archive-bytes"

    monkeypatch.setattr(api, "_marketplace_export", export)
    r = client.put(f"/marketplace/sources/{source['id']}/catalog", json={
        "name": "Shelf", "description": "",
        "entries": [{"title": "Watcher", "description": "", "automationId": a["id"]}]})
    assert r.status_code == 409
    assert r.json()["detail"] == marketplace.CATALOG_MOVED


# ---------- §6 firing: the busy notice is a capacity notice only ----------

def test_a_version_not_found_firing_never_tells_the_sender_it_is_busy(store, monkeypatch):
    """§6: nothing is busy — the firing failed outright, so the §6.1 sender
    gets no "I'm working on something else right now" reply."""
    from autowright import listeners as li_mod
    from autowright.engine import Engine
    from autowright.firing import fire_trigger

    notified = []
    monkeypatch.setattr(li_mod, "notify_busy", notified.append)

    engine = Engine(store)
    a = store.create_automation(make_version(), "Gone version", None)
    a["current_version"] = 9  # §7: the version this firing targets doesn't resolve
    trigger = {"id": "t1", "kind": "discord", "enabled": True, "channel": "42",
               "secret": "TOKEN"}
    payload = {"kind": "discord", "channel": "42", "secret": "TOKEN",
               "sender": "Dave", "text": "go"}

    assert fire_trigger(store, engine, a, trigger, payload=payload) is False
    assert notified == []
    # The skipped record every other never-ran path leaves is still there.
    assert [h["note"] for h in store.execs.values()] == ["version v9 no longer exists"]
