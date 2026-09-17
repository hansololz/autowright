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

from conftest import _rename_denied


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


# ---------- §19: the polled routes never hold the engine's lock across disk ----------

def test_get_execution_reads_the_body_off_the_store_lock(client, monkeypatch):
    """§19 `GET /executions/{id}`: the header is looked up under the store
    lock, the body read and serialized outside it — the §7 page polls this
    route, so it must never hold the engine's lock across disk."""
    from autowright.storage import store

    a = store.create_automation(make_version(), "Polled", "mock")
    h = store.create_execution(a, "version", 1, "manual", make_version()["steps"],
                               status="succeeded")
    store.update_execution(h)
    before = client.get(f"/executions/{h['id']}").json()

    # A record loaded from disk carries no body (§5 bodies-lazily) — the route
    # merges execution.yaml onto the header, and that read is the disk work.
    h.pop("steps", None)
    held = []
    real = store.read_exec_yaml
    monkeypatch.setattr(store, "read_exec_yaml",
                        lambda eid: held.append(store.lock._is_owned()) or real(eid))

    assert client.get(f"/executions/{h['id']}").json() == before
    assert held == [False]


def test_get_execution_still_404s_an_unknown_id(client):
    assert client.get("/executions/nope").status_code == 404


# ---------- §19: the one-sentence 422 names the parameter, not its source ----------

def test_a_query_parameter_422_reads_as_the_parameter_name(client):
    """§19: pydantic's `loc` leads with the source (`query`, `path`, `header`,
    `body`) — every one of them is dropped alike, so the detail reads
    `limit: …` and not `query.limit: …`."""
    r = client.get("/executions?limit=0")
    assert r.status_code == 422
    assert r.json()["detail"].startswith("limit:")


def test_the_page_cursor_refuses_a_negative_timestamp(client):
    """§19: `beforeStartedMs` is bounded like its `startedFromMs` siblings."""
    assert client.get("/executions?beforeStartedMs=-1&beforeId=x").status_code == 422


# ---------- §19: cancel answers like its siblings ----------

def test_cancel_404s_an_unknown_execution(client):
    """§19 `POST /executions/{id}/cancel`: "404 for an unknown id, like its
    siblings" — never a 200 saying nothing happened."""
    r = client.post("/executions/nope/cancel")
    assert r.status_code == 404
    assert r.json()["detail"] == "execution not found"


# ---------- §19: GET /automations serves the list shape ----------

def test_the_automations_list_never_serializes_version_bodies(client):
    """§19: `GET /automations` is the list shape — the §13 tray poll and the
    §20 reference resolution read it, and the full-record fields (steps, spec,
    versions, draft, …) belong to `GET /automations/{id}`."""
    from autowright.storage import store

    a = store.create_automation(make_version(), "Listed", "mock")
    store.save_new_version(a, make_version(description="v2"))

    row = client.get("/automations").json()[0]
    full = client.get(f"/automations/{a['id']}").json()
    for key in ("steps", "spec", "versions", "draft", "params", "memory",
                "snapshots", "latest", "packages"):
        assert key not in row, key
        assert key in full, key
    # the shared keys are identical — one row, two shapes
    assert all(row[k] == full[k] for k in row)


# ---------- §4.8: the lost create race undoes the Keychain entry off the lock ----------

def test_a_lost_secret_create_race_deletes_off_the_store_lock(client, monkeypatch):
    """§4.8: Keychain IPC can block for seconds — the undo of a lost create
    race is that same IPC, so it must not run under `store.lock` either."""
    from autowright import api
    from autowright.storage import store

    def racing_set(secret_id, value):
        # Another create lands the name while this one's Keychain IPC runs.
        store.secrets.append({"id": "other", "name": "RACER",
                              "description": "", "set": True})

    held = []
    monkeypatch.setattr(api.keychain, "set_secret", racing_set)
    monkeypatch.setattr(api.keychain, "delete_secret",
                        lambda secret_id: held.append(store.lock._is_owned()))

    r = client.post("/secrets", json={"name": "RACER", "value": "x"})
    assert r.status_code == 422
    assert "already exists" in r.json()["detail"]
    assert held == [False]
    # the loser left nothing behind but the winner's entry
    assert [s["id"] for s in store.secrets] == ["other"]


# ---------- §4.9 dataSize: the walk runs on a background thread ----------

def test_data_size_answers_at_once_and_is_keyed_by_the_path(client, tmp_path):
    """§4.9: "the tree walk runs on a background thread, cached 30 s and keyed
    by the path — `/state` and `/settings` answer the last value at once, an
    empty string until the first walk lands, never blocking on the walk"."""
    import time

    from autowright import api

    assert client.get("/settings").json()["dataSize"] == ""  # nothing walked yet
    for _ in range(200):
        label = client.get("/settings").json()["dataSize"]
        if label:
            break
        time.sleep(0.02)
    assert label  # the background walk landed and the cache answers it

    # A cache entry from another location never matches after a data-path
    # switch — it would otherwise still look fresh for the rest of its TTL.
    api._data_size_cache = (time.monotonic(), "/somewhere/else", "9.9 GB")
    assert client.get("/settings").json()["dataSize"] == ""

    target = tmp_path / "elsewhere"
    assert client.post("/settings/data-path",
                       json={"path": str(target)}).json()["dataSize"] == ""


# ---------- §19: the packages routes answer a held pip lock at once ----------

def test_packages_install_and_update_answer_409_while_pip_is_busy(client):
    """§19: "a request that finds the pip lock held by another install answers
    409 … instead of holding a request worker until it frees"."""
    from autowright import packages

    body = {"packages": [{"pip": "leftpad", "import": "leftpad"}]}
    with packages._pip_lock:
        for route in ("/packages/install", "/packages/update"):
            r = client.post(route, json=body)
            assert r.status_code == 409, route
            assert r.json()["detail"] == "a package install is already running"


def test_a_packages_body_is_capped_at_64_entries(client):
    """§19: "Every packages body is capped at 64 entries (422 above)"."""
    entries = [{"pip": f"pkg{i}", "import": f"pkg{i}"} for i in range(65)]
    r = client.post("/packages/check", json={"packages": entries})
    assert r.status_code == 422
    assert r.json()["detail"].startswith("packages:")
    entries.pop()
    assert client.post("/packages/check", json={"packages": entries}).status_code == 200


# ---------- §19: /agents/detect is single-flight ----------

def test_agents_detect_shares_one_sweep_and_a_short_cache(monkeypatch):
    """§19: the sweep probes every supported CLI on the machine — concurrent
    callers share one run, and its answer is served briefly afterwards rather
    than re-probed."""
    from autowright import api

    calls = []
    started, release = threading.Event(), threading.Event()

    def slow_detect():
        calls.append(1)
        started.set()
        release.wait(5)
        return [{"harness": "Claude Code", "installed": True}]

    monkeypatch.setattr(api.harness, "detect", slow_detect)
    answers = []
    threads = [threading.Thread(target=lambda: answers.append(api.detect_agents()))
               for _ in range(4)]
    for t in threads:
        t.start()
    assert started.wait(5), "the sweep never started"
    release.set()
    for t in threads:
        t.join(5)

    assert len(calls) == 1  # one sweep for four concurrent callers
    assert answers == [[{"harness": "Claude Code", "installed": True}]] * 4
    api.detect_agents()
    assert len(calls) == 1  # and served from the result cache afterwards


def test_executions_id_prefix_filters_rows(client, home):
    """§19: `idPrefix` keeps rows whose execution id starts with it — the §20
    reference resolution's bounded lookup; an empty value is ignored."""
    from autowright.storage import store

    a = store.create_automation(make_version(), "Prefixed", "mock")
    ids = []
    for _ in range(3):
        h = store.create_execution(a, "version", 1, "manual", make_version()["steps"],
                                   status="succeeded")
        store.update_execution(h)
        ids.append(h["id"])
    want = ids[0]
    r = client.get(f"/executions?idPrefix={want[:8]}&limit=50")
    assert r.status_code == 200
    got = [e["id"] for e in r.json()["executions"]]
    assert got == [i for i in ids if i.startswith(want[:8])]
    assert r.json()["total"] == len(got)
    assert client.get("/executions?idPrefix=").json()["total"] == 3
