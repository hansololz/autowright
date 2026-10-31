"""2026-10-31 second sweep audit batch (API routes): the §4.4
one-editor-per-version `baseVersion` gate on the version save and the draft
PUT, the §5 failed crash-orphan set-aside answering a 500 that names the
folder, the §6.3 raced-copy rule on the memory clear and manual snapshot
routes, and the import preview TTL on the monotonic clock."""
import time

import pytest
from conftest import make_version

STALE = "was saved outside this editor — close and reopen Edit to continue from it"


def _edited(text="Edited in the editor"):
    return make_version(spec=[{"kind": "h1", "text": text}])


# ---------- N: §4.4 one editor per version ----------

def test_version_save_with_a_stale_base_version_answers_409_and_writes_nothing(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Base save", "mock")
    store.save_new_version(a, _edited("Pushed from a terminal"))
    assert a["current_version"] == 2
    r = client.put(f"/draft/{a['id']}", json={"draft": _edited()})
    assert r.status_code == 200
    r = client.post(f"/automations/{a['id']}/versions",
                    json={"draft": _edited(), "baseVersion": 1})
    assert r.status_code == 409
    assert r.json()["detail"] == f"version v2 {STALE}"
    assert a["current_version"] == 2 and sorted(a["versions"]) == [1, 2]
    # nothing settled: the editor's draft is still there
    assert client.get(f"/automations/{a['id']}").json()["draft"] is not None


@pytest.mark.parametrize("base", ["equal", "omitted"])
def test_version_save_with_a_current_or_no_base_version_saves(client, base):
    from autowright.storage import store

    a = store.create_automation(make_version(), f"Base ok {base}", "mock")
    body = {"draft": _edited()}
    if base == "equal":
        body["baseVersion"] = 1
    r = client.post(f"/automations/{a['id']}/versions", json=body)
    assert r.status_code == 200
    assert r.json()["version"] == 2 and a["current_version"] == 2


def test_draft_put_with_a_stale_base_version_answers_409_and_stores_nothing(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Base draft", "mock")
    store.save_new_version(a, _edited("Pushed from a terminal"))
    r = client.put(f"/draft/{a['id']}", json={"draft": _edited(), "baseVersion": 1})
    assert r.status_code == 409
    assert r.json()["detail"] == f"version v2 {STALE}"
    assert client.get(f"/automations/{a['id']}").json()["draft"] is None
    assert not store.draft_dir(a).exists()


def test_draft_put_with_a_stale_base_version_leaves_an_existing_draft_unchanged(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Base draft keep", "mock")
    assert client.put(f"/draft/{a['id']}", json={"draft": _edited(),
                                                 "baseVersion": 1}).status_code == 200
    before = client.get(f"/automations/{a['id']}").json()["draft"]
    store.save_new_version(a, _edited("Pushed from a terminal"))
    r = client.put(f"/draft/{a['id']}", json={
        "draft": _edited("Overwrite attempt"), "baseVersion": 1})
    assert r.status_code == 409
    assert client.get(f"/automations/{a['id']}").json()["draft"] == before


@pytest.mark.parametrize("base", ["equal", "omitted"])
def test_draft_put_with_a_current_or_no_base_version_stores(client, base):
    from autowright.storage import store

    a = store.create_automation(make_version(), f"Base draft ok {base}", "mock")
    store.save_new_version(a, _edited("v2"))
    body = {"draft": _edited()}
    if base == "equal":
        body["baseVersion"] = 2
    assert client.put(f"/draft/{a['id']}", json=body).status_code == 200
    assert client.get(f"/automations/{a['id']}").json()["draft"] is not None


def test_pending_draft_put_ignores_base_version(client):
    r = client.put("/draft/pending", json={"draft": _edited(), "baseVersion": 99})
    assert r.status_code == 200
    assert client.get("/draft/pending").json()["draft"] is not None


# ---------- T: §5 failed crash-orphan set-aside ----------

def test_a_failed_orphan_set_aside_answers_500_naming_the_folder(client, monkeypatch):
    from autowright.storage import OrphanSetAsideError, store

    a = store.create_automation(make_version(), "Orphaned", "mock")
    orphan = store.auto_dir(a) / "versions" / "v2"

    def refuse(auto):
        raise OrphanSetAsideError(orphan, "Permission denied")

    monkeypatch.setattr(store, "_set_aside_orphan_versions", refuse)
    r = client.post(f"/automations/{a['id']}/versions", json={"draft": _edited()})
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert str(orphan) in detail and "Permission denied" in detail
    assert a["current_version"] == 1


# ---------- J: §6.3 raced-copy rule on the memory routes ----------

@pytest.mark.parametrize("route", ["clear", "snapshot"])
def test_a_run_that_started_and_finished_inside_the_copy_abandons_it(client, monkeypatch, route):
    from autowright.storage import store

    a = store.create_automation(make_version(), f"Raced {route}", "mock")
    (store.auto_dir(a) / "memory" / "seen.yaml").write_text("v: 1\n")
    real_stage = store.stage_snapshot

    def stage_while_a_run_comes_and_goes(*args, **kwargs):
        out = real_stage(*args, **kwargs)
        with store.lock:
            # A run started and finished during the copy: nothing is live at
            # the commit gate, but the start counter moved.
            a["_starts"] = a.get("_starts", 0) + 1
        return out

    monkeypatch.setattr(store, "stage_snapshot", stage_while_a_run_comes_and_goes)
    url = (f"/automations/{a['id']}/memory/clear" if route == "clear"
           else f"/automations/{a['id']}/memory/snapshots")
    r = client.post(url)
    assert r.status_code == 409
    assert r.json()["detail"] == "an execution is in progress"
    assert store.list_snapshots(a) == []  # nothing committed
    assert (store.auto_dir(a) / "memory" / "seen.yaml").exists()  # nothing cleared


@pytest.mark.parametrize("route", ["clear", "snapshot"])
def test_memory_routes_commit_when_the_counter_held(client, route):
    from autowright.storage import store

    a = store.create_automation(make_version(), f"Steady {route}", "mock")
    (store.auto_dir(a) / "memory" / "seen.yaml").write_text("v: 1\n")
    a["_starts"] = 3  # earlier runs, all before the first gate
    url = (f"/automations/{a['id']}/memory/clear" if route == "clear"
           else f"/automations/{a['id']}/memory/snapshots")
    assert client.post(url).status_code == 200
    if route == "snapshot":
        assert len(store.list_snapshots(a)) == 1
    else:
        assert not (store.auto_dir(a) / "memory" / "seen.yaml").exists()


# ---------- AC: the import TTL reads the monotonic clock ----------

def test_import_ttl_ignores_a_wall_clock_step(client, monkeypatch):
    from autowright import api, transfer

    a = client.post("/automations", json={"draft": make_version(), "agentId": "mock"}).json()
    data = client.get(f"/automations/{a['id']}/export").content
    monkeypatch.setattr(transfer, "preview_archive", lambda store, data: {})
    r = client.post("/automations/import/preview", content=data,
                    headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200
    token = r.json()["token"]
    parked_at, _ = api._import_parked[token]
    assert abs(parked_at - time.monotonic()) < 60
    # A wall clock stepped a day forward never expires the parked archive.
    real_time = time.time
    monkeypatch.setattr(api.time, "time", lambda: real_time() + 86400)
    api._sweep_parked()
    assert token in api._import_parked
