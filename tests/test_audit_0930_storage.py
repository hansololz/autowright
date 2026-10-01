"""2026-09-30 audit fixes: disk-first top-level stores and data-path switch,
rmtree / memory walk / log reads off the store lock, step field typing with
lenient on-disk readers, lenient version-folder load, the index lost-update
repair, publish-inside-the-lock, and `POST /automations` `settlePending`."""
import json
import os
import shutil
import threading
import time
from types import SimpleNamespace

import pytest

from conftest import make_version


def _fail(*_a, **_k):
    raise OSError(28, "No space left on device")


# ---------- 1. §5 data-location change: absolute-only, disk-first ----------

def test_data_path_relative_is_422(client):
    r = client.post("/settings/data-path", json={"path": "relative/folder"})
    assert r.status_code == 422
    assert r.json()["detail"] == "the data folder must be an absolute path"


def test_data_path_failed_settings_write_changes_nothing(client, home, monkeypatch):
    from autowright import api, models
    from autowright.storage import store

    before_path = store.settings.get("dataPath")
    db = store.execdb
    monkeypatch.setattr(store, "save_settings", _fail)
    with pytest.raises(OSError):
        api.set_data_path(models.DataPath(path=str(home / "elsewhere")))
    # The old index is still open and the old path still in memory.
    assert store.execdb is db
    db.load_all()  # an open connection answers
    assert store.settings.get("dataPath") == before_path


def test_data_path_failed_reload_restores_the_old_location(client, home, monkeypatch):
    from autowright import api, models, paths
    from autowright.storage import store
    from autowright.yamlio import load_yaml

    old_dir = store.executions_dir()
    real_load_all = store.load_all
    calls = []

    def flaky_load_all(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("reload blew up")
        return real_load_all(*a, **k)

    monkeypatch.setattr(store, "load_all", flaky_load_all)
    with pytest.raises(RuntimeError):
        api.set_data_path(models.DataPath(path=str(home / "elsewhere")))
    # No half-switched store: the old path in memory and on disk, an index open.
    assert store.executions_dir() == old_dir
    assert (load_yaml(paths.settings_file()) or {}).get("dataPath") in (None, str(old_dir))
    assert store.execdb is not None
    store.execdb.load_all()


# ---------- 2. §5 disk-first agents / secrets / settings ----------

def test_patch_settings_failed_write_keeps_memory(client, monkeypatch):
    from autowright.storage import store

    before = store.settings.get("days")
    monkeypatch.setattr(store, "save_settings", _fail)
    with pytest.raises(OSError):
        client.patch("/settings", json={"days": 3})
    assert store.settings.get("days") == before


def test_add_and_patch_agent_failed_write_keeps_memory(client, monkeypatch):
    from autowright.storage import store

    agents_before = [dict(g) for g in store.agents]
    default_before = store.default_agent_id
    monkeypatch.setattr(store, "save_agents", _fail)
    with pytest.raises(OSError):
        client.post("/agents", json={"name": "Second", "harness": "Codex", "mode": "default"})
    with pytest.raises(OSError):
        client.patch("/agents/mock", json={"name": "Renamed", "default": True})
    assert store.agents == agents_before
    assert store.default_agent_id == default_before


def test_delete_agent_failed_write_repoints_nothing(client, monkeypatch):
    from autowright.storage import store

    second = client.post("/agents", json={"name": "Second", "harness": "Codex",
                                          "mode": "default"}).json()
    auto = client.post("/automations", json={"draft": make_version(), "agentId": second["id"],
                                             "stepAgents": [second["id"]]}).json()
    agents_before = [dict(g) for g in store.agents]
    monkeypatch.setattr(store, "save_agents", _fail)
    with pytest.raises(OSError):
        client.delete(f"/agents/{second['id']}")
    assert store.agents == agents_before
    a = store.autos[auto["id"]]
    assert a["agent_id"] == second["id"]
    assert a["enabled_agents"] == [second["id"]]


def test_delete_agent_repoints_after_the_write(client):
    from autowright.storage import store

    second = client.post("/agents", json={"name": "Second", "harness": "Codex",
                                          "mode": "default"}).json()
    auto = client.post("/automations", json={"draft": make_version(), "agentId": second["id"],
                                             "stepAgents": ["mock", second["id"]]}).json()
    assert client.delete(f"/agents/{second['id']}").json() == {"ok": True}
    a = store.autos[auto["id"]]
    assert a["agent_id"] == "mock"
    assert a["enabled_agents"] == ["mock"]
    assert all(g["id"] != second["id"] for g in store.agents)


def test_create_secret_failed_write_leaves_no_row_and_no_keychain_value(client, monkeypatch):
    from autowright import keychain
    from autowright.storage import store

    written = []
    real_set = keychain.set_secret

    def recording_set(secret_id, value):
        written.append(secret_id)
        real_set(secret_id, value)

    monkeypatch.setattr(keychain, "set_secret", recording_set)
    monkeypatch.setattr(store, "save_secrets", _fail)
    with pytest.raises(OSError):
        client.post("/secrets", json={"name": "MY_TOKEN", "value": "abc"})
    assert not any(s["name"] == "MY_TOKEN" for s in store.secrets)
    assert written and keychain.get_secret(written[0]) is None


def test_put_and_delete_secret_failed_write_keeps_memory(client, monkeypatch):
    from autowright.storage import store

    sid = client.post("/secrets", json={"name": "MY_TOKEN", "value": "abc",
                                        "description": "old"}).json()["id"]
    before = [dict(s) for s in store.secrets]
    monkeypatch.setattr(store, "save_secrets", _fail)
    with pytest.raises(OSError):
        client.put(f"/secrets/{sid}", json={"description": "new"})
    with pytest.raises(OSError):
        client.delete(f"/secrets/{sid}")
    with pytest.raises(OSError):
        client.delete("/secrets")
    assert store.secrets == before


# ---------- 3. §6 no rmtree under the lock ----------

def _rmtree_asserting_unlocked(store, monkeypatch):
    real = shutil.rmtree
    seen = []

    def checked(path, *a, **k):
        # `_is_owned` is per thread: whichever thread deletes (the route's
        # worker, the reaper) must not be the one holding the lock.
        seen.append(path)
        assert not store.lock._is_owned(), f"rmtree({path}) under store.lock"
        return real(path, *a, **k)

    monkeypatch.setattr(shutil, "rmtree", checked)
    return seen


def test_restore_snapshot_abandoned_by_a_live_execution_deletes_off_lock(store, monkeypatch):
    from autowright import storage as storage_mod
    from autowright.storage import LiveExecutionError

    a = store.create_automation(make_version(), "Restorer", None)
    (store.auto_dir(a) / "memory" / "seen.yaml").write_text("v: 1\n")
    meta = store.snapshot_memory(a, "manual")
    real_copy = storage_mod.shutil.copytree

    def copy_then_go_live(src, dst, *args, **kwargs):
        out = real_copy(src, dst, *args, **kwargs)
        a["_live"] = {"racing-execution"}
        return out

    seen = _rmtree_asserting_unlocked(store, monkeypatch)
    monkeypatch.setattr(storage_mod.shutil, "copytree", copy_then_go_live)
    with pytest.raises(LiveExecutionError):
        store.restore_snapshot(a, meta["id"])
    assert seen  # the staged copies were deleted — after the lock hold


@pytest.mark.parametrize("route", ["clear", "snapshot"])
def test_memory_routes_discard_a_raced_copy_off_lock(client, monkeypatch, route):
    from autowright.storage import store

    auto = client.post("/automations", json={"draft": make_version(), "agentId": "mock"}).json()
    a = store.autos[auto["id"]]
    (store.auto_dir(a) / "memory" / "seen.yaml").write_text("v: 1\n")
    real_stage = store.stage_snapshot

    def stage_then_go_live(*args, **kwargs):
        out = real_stage(*args, **kwargs)
        a["_live"] = {"racing-execution"}
        return out

    seen = _rmtree_asserting_unlocked(store, monkeypatch)
    monkeypatch.setattr(store, "stage_snapshot", stage_then_go_live)
    try:
        url = (f"/automations/{a['id']}/memory/clear" if route == "clear"
               else f"/automations/{a['id']}/memory/snapshots")
        assert client.post(url).status_code == 409
    finally:
        a["_live"] = set()
    assert seen
    assert (store.auto_dir(a) / "memory" / "seen.yaml").exists()  # nothing cleared


# ---------- 4. §19 memory_stats walk off the lock ----------

def test_memory_stats_walk_never_runs_under_the_lock(store, monkeypatch):
    from autowright import storage as storage_mod

    a = store.create_automation(make_version(), "Walker", None)
    (store.auto_dir(a) / "memory" / "seen.yaml").write_text("v: 1\n", newline="")
    walks = []
    real = storage_mod.iter_file_stats

    def recording(d):
        walks.append((threading.current_thread(), store.lock._is_owned()))
        return real(d)

    monkeypatch.setattr(storage_mod, "iter_file_stats", recording)
    with store.lock:  # the /state and detail serializers hold it
        full = store.auto_json(a)
    assert full["memory"]["size"] == store.MEMORY_STATS_COMPUTING
    deadline = time.monotonic() + 5
    while (store.memory_stats(a)["size"] == store.MEMORY_STATS_COMPUTING
           and time.monotonic() < deadline):
        time.sleep(0.01)
    assert store.memory_stats(a)["size"] == "5 B"
    assert walks
    for thread, owned in walks:
        assert thread is not threading.current_thread()
        assert owned is False


def test_memory_file_413_names_the_directory_without_a_walk(client, monkeypatch):
    from autowright import storage as storage_mod
    from autowright.storage import store

    auto = client.post("/automations", json={"draft": make_version(), "agentId": "mock"}).json()
    a = store.autos[auto["id"]]
    (store.auto_dir(a) / "memory" / "blob.bin").write_bytes(b"\xff\xfe\x00")
    monkeypatch.setattr(storage_mod, "iter_file_stats", lambda d: pytest.fail("walked"))
    r = client.get(f"/automations/{a['id']}/memory/files/blob.bin")
    assert r.status_code == 422
    assert str(store.auto_dir(a) / "memory") in r.json()["detail"]


# ---------- 5. §19 log reads: header under the lock, tail from the end ----------

def _naive_read(p, tail, since):
    raw = p.read_text(encoding="utf-8", errors="replace").splitlines()
    if since is not None and since >= 1 and len(raw) >= since:
        try:
            probe = json.loads(raw[since - 1])
        except ValueError:
            probe = None
        if isinstance(probe, dict) and probe.get("sequence") == since:
            raw = raw[since:]
    if tail is not None:
        raw = raw[-tail:]
    return [json.loads(ln) for ln in raw]


def _strip_time(lines):
    return [{k: v for k, v in ln.items() if k != "time"} for ln in lines]


def test_read_log_fast_paths_equal_the_naive_read(store):
    a = store.create_automation(make_version(), "Logger", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    p = store.log_file(h["id"], store.EXEC_LOG)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        for n in range(1, 30_001):
            f.write(json.dumps({"sequence": n, "kind": "out", "text": f"line {n} " + "x" * 40,
                                "timestamp": "2026-09-30T10:00:00Z"}) + "\n")
    for tail, since in [(1, None), (50, None), (5000, None), (40_000, None),
                        (None, 1), (None, 29_990), (100, 25_000), (100, 29_999),
                        (10, 30_000), (None, 30_000), (None, 40_000), (10, 40_000)]:
        got = store.read_log(h["id"], tail=tail, since=since)
        assert _strip_time(got) == _naive_read(p, tail, since), (tail, since)


def test_read_log_since_with_misnumbered_file_falls_back(store):
    a = store.create_automation(make_version(), "Misnumbered", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    p = store.log_file(h["id"], store.EXEC_LOG)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Sequences start at 100 — the line at position `since` doesn't carry it.
    p.write_text("".join(json.dumps({"sequence": n + 100, "text": str(n)}) + "\n"
                         for n in range(1, 501)))
    for tail, since in [(None, 10), (5, 10), (5, None)]:
        got = store.read_log(h["id"], tail=tail, since=since)
        assert got == _naive_read(p, tail, since)


def test_read_log_step_file_from_the_yaml_outside_the_lock(store, home, monkeypatch):
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Stepper", None)
    h = store.create_execution(a, "version", 1, "manual",
                               [{"file": "01-say.py", "name": "Say hello"}], status="succeeded")
    store.update_execution(h)
    p = store.log_file(h["id"], store.log_name("01-say.py", 0, 1))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"sequence": 1, "text": "hi"}) + "\n")
    store.close_exec_db()
    fresh = Store()
    fresh.load_all()
    assert "steps" not in fresh.execs[h["id"]]  # a finished header — the body is lazy
    real_read = fresh.read_exec_yaml

    def read_checked(execution_id):
        assert not fresh.lock._is_owned(), "execution.yaml read under store.lock"
        return real_read(execution_id)

    monkeypatch.setattr(fresh, "read_exec_yaml", read_checked)
    assert [ln["text"] for ln in fresh.read_log(h["id"], 0, 1)] == ["hi"]
    assert fresh.read_log(h["id"], 3, 1) == []


# ---------- 6. §19 step field typing + lenient stored readers ----------

def test_draft_step_wrong_type_is_422(client):
    auto = client.post("/automations", json={"draft": make_version(), "agentId": "mock"}).json()
    for bad in ({"timeout": "abc"}, {"retries": "3"}, {"name": 5}, {"code": ["x"]},
                {"description": {"a": 1}}, {"file": 7}):
        ver = make_version()
        ver["steps"][0] = {**ver["steps"][0], **bad}
        r = client.put(f"/draft/{auto['id']}", json={"draft": ver})
        assert r.status_code == 422, bad
    ver = make_version()
    ver["steps"] = ["not a mapping"]
    assert client.put(f"/draft/{auto['id']}", json={"draft": ver}).status_code == 422


def test_stored_version_with_bad_timeout_diffs_and_restores(client):
    from autowright.storage import store
    from autowright.yamlio import load_yaml, save_yaml

    auto = client.post("/automations", json={"draft": make_version(), "agentId": "mock"}).json()
    aid = auto["id"]
    v2 = make_version(notes="second")
    assert client.post(f"/automations/{aid}/versions", json={"draft": v2}).status_code == 200
    manifest = store.auto_dir(store.autos[aid]) / "versions" / "v1" / "automation.yaml"
    doc = load_yaml(manifest)
    doc["steps"][0]["timeout"] = "abc"
    doc["steps"][0]["retries"] = "lots"
    save_yaml(manifest, doc)
    store.load_all()
    assert store.autos[aid]["versions"][1]["steps"][0]["timeout"] == "abc"
    r = client.get(f"/automations/{aid}/diff", params={"from": "v1", "to": "v2"})
    assert r.status_code == 200
    r = client.post(f"/automations/{aid}/restore", json={"version": 1})
    assert r.status_code == 200
    n = r.json()["version"]
    restored = load_yaml(store.auto_dir(store.autos[aid]) / "versions" / f"v{n}" / "automation.yaml")
    assert "timeout" not in restored["steps"][0]  # degraded to the default
    assert "retries" not in restored["steps"][0]


def test_manifest_step_entry_is_lenient():
    from autowright.storage import manifest_step_entry

    entry = manifest_step_entry({"timeout": "abc", "retries": None}, "01-x.py")
    assert entry == {"file": "01-x.py", "name": "", "description": ""}
    assert manifest_step_entry({"name": "a", "timeout": "60", "retries": 2},
                               "01-a.py")["timeout"] == 60


def test_version_writer_coerces_non_string_code(store):
    ver = make_version()
    ver["steps"][0]["code"] = None
    a = store.create_automation(ver, "Codeless", None)
    vd = store.auto_dir(a) / "versions" / "v1"
    assert (vd / "01-say.py").read_text() == ""


# ---------- 7. §5 lenient version-folder load ----------

def test_non_utf8_notes_and_spec_load(store, home):
    from autowright.storage import Store

    a = store.create_automation(make_version(notes="fine"), "Encoded", None)
    vd = store.auto_dir(a) / "versions" / "v1"
    (vd / "notes.md").write_bytes(b"caf\xe9 notes\n")
    (vd / "spec.md").write_bytes(b"# Sp\xe9c\n")
    fresh = Store()
    fresh.load_all()
    loaded = fresh.autos[a["id"]]["versions"][1]
    assert "notes" in loaded["notes"]
    assert loaded["spec"]


def test_non_mapping_step_entry_is_skipped(store, caplog):
    import logging

    from autowright.storage import Store
    from autowright.yamlio import load_yaml, save_yaml

    a = store.create_automation(make_version(), "Skipper", None)
    manifest = store.auto_dir(a) / "versions" / "v1" / "automation.yaml"
    doc = load_yaml(manifest)
    doc["steps"].append("just a string")
    save_yaml(manifest, doc)
    fresh = Store()
    with caplog.at_level(logging.WARNING, logger="autowright.storage"):
        fresh.load_all()
    assert len(fresh.autos[a["id"]]["versions"][1]["steps"]) == 2
    assert any("isn't a mapping" in r.message for r in caplog.records)


@pytest.mark.parametrize("broken", [
    "steps: [unclosed\n",                                     # unparsable
    "steps:\n  - file: [1, 2]\n    name: odd\n",              # parses, then crashes the reader
])
def test_broken_draft_loads_the_automation_with_no_draft(store, broken):
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Drafty", None)
    dd = store.auto_dir(a) / "draft" / "automation"
    dd.mkdir(parents=True, exist_ok=True)
    (dd / "automation.yaml").write_text(broken)
    fresh = Store()
    fresh.load_all()
    assert a["id"] in fresh.autos
    assert fresh.autos[a["id"]]["draft"] is None


# ---------- 8. §5 index lost-update repair ----------

def _index_row(store, execution_id):
    from autowright.execdb import ExecDB

    db = ExecDB(store.executions_dir() / "executions.db")
    try:
        return db.load_all().get(execution_id)
    finally:
        db.close()


def test_newer_yaml_refreshes_a_stale_index_row(store, home):
    from autowright.storage import Store
    from autowright.yamlio import load_yaml, save_yaml

    a = store.create_automation(make_version(), "Retried", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="failed")
    store.update_execution(h)
    store.close_exec_db()
    # A power loss dropped the index update: the yaml says succeeded.
    y = store.exec_dir(h["id"]) / "execution.yaml"
    doc = load_yaml(y)
    doc["status"] = "succeeded"
    save_yaml(y, doc)
    db_mtime = (store.executions_dir() / "executions.db").stat().st_mtime
    os.utime(y, (db_mtime + 10, db_mtime + 10))
    fresh = Store()
    fresh.load_all()
    assert fresh.execs[h["id"]]["status"] == "succeeded"
    fresh.close_exec_db()
    assert _index_row(fresh, h["id"])["status"] == "succeeded"


def test_older_yaml_is_not_reread(store, home, monkeypatch):
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Settled", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    store.update_execution(h)
    store.close_exec_db()
    y = store.exec_dir(h["id"]) / "execution.yaml"
    db_mtime = (store.executions_dir() / "executions.db").stat().st_mtime
    os.utime(y, (db_mtime - 10, db_mtime - 10))
    fresh = Store()
    reads = []
    real = fresh.read_exec_yaml
    monkeypatch.setattr(fresh, "read_exec_yaml", lambda eid: reads.append(eid) or real(eid))
    fresh.load_all()
    assert h["id"] in fresh.execs and reads == []


# ---------- 9. §19 single-row automation.changed inside the lock ----------

def test_single_row_automation_changed_publishes_inside_the_lock(client, monkeypatch):
    from autowright.events import hub
    from autowright.scheduler import Scheduler
    from autowright.storage import store

    auto = client.post("/automations", json={"draft": make_version(), "agentId": "mock"}).json()
    seen = []

    def recording(event, **payload):
        if event == "automation.changed" and payload.get("automationId"):
            seen.append(store.lock._is_owned())

    monkeypatch.setattr(hub, "publish", recording)
    assert client.patch(f"/automations/{auto['id']}",
                        json={"description": "changed"}).status_code == 200
    Scheduler(store, SimpleNamespace())._publish_changed(store.autos[auto["id"]])
    assert seen and all(seen)


# ---------- 11. §19 POST /automations settlePending ----------

def _stage_pending(client):
    client.post("/draft/pending/open")
    client.put("/chat/pending", json={"chat": [{"id": "c1", "kind": "user", "text": "hello"}]})
    client.put("/draft/pending", json={"draft": {**make_version(), "name": "Pending"},
                                       "agentId": "mock"})


def test_create_with_settle_pending_false_leaves_the_slot(client, home):
    _stage_pending(client)
    auto = client.post("/automations", json={"draft": make_version(), "agentId": "mock",
                                             "settlePending": False}).json()
    assert client.get("/draft/pending").json()["draft"] is not None
    assert [e["id"] for e in client.get("/chat/pending").json()["chat"]] == ["c1"]
    chat = client.get(f"/chat/{auto['id']}").json()["chat"]
    assert not any(e.get("boundary") for e in chat)


def test_create_settles_the_slot_by_default(client, home):
    _stage_pending(client)
    auto = client.post("/automations", json={"draft": make_version(), "agentId": "mock"}).json()
    assert client.get("/draft/pending").json()["draft"] is None
    assert client.get("/chat/pending").json() == {"chat": []}
    chat = client.get(f"/chat/{auto['id']}").json()["chat"]
    assert chat[-1]["boundary"] is True and chat[-1]["text"] == "Created as v1."


# ---------- follow-ups: memory walk landing, _latest_exec, post_draft ----------

def _wait_walk(store, a, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not a.get("_memory_stats_walking") and a.get("_memory_stats") \
                and a["_memory_stats"][0] != float("-inf"):
            return
        time.sleep(0.01)
    raise AssertionError("the memory_stats walk never landed")


def test_memory_walk_landing_publishes_only_on_a_change(client, monkeypatch):
    from autowright.events import hub
    from autowright.storage import store

    # Created in the store directly: no serializer has read its memory yet.
    a = store.create_automation(make_version(), "Landing", None)
    (store.auto_dir(a) / "memory" / "seen.yaml").write_text("v: 1\n", newline="")
    published = []

    def recording(event, **payload):
        if event == "automation.changed" and payload.get("automationId") == a["id"]:
            published.append(payload)

    monkeypatch.setattr(hub, "publish", recording)
    # First-ever read: the placeholder, then the landing announces the change.
    assert store.memory_stats(a)["size"] == store.MEMORY_STATS_COMPUTING
    _wait_walk(store, a)
    assert len(published) == 1
    assert store.memory_stats(a)["size"] == "5 B"
    # Expired, unchanged on disk: last known served, the landing is silent.
    store.invalidate_memory_stats(a)
    assert store.memory_stats(a)["size"] == "5 B"
    _wait_walk(store, a)
    assert len(published) == 1
    # Changed on disk: last known served at once (unless the walk the
    # invalidation started already landed), the landing announces it.
    (store.auto_dir(a) / "memory" / "more.yaml").write_text("v: 12\n", newline="")
    store.invalidate_memory_stats(a)
    assert store.memory_stats(a)["size"] in ("5 B", "11 B")
    _wait_walk(store, a)
    assert len(published) == 2
    assert store.memory_stats(a)["size"] == "11 B"


def _wait_published(published, count, timeout=5.0):
    deadline = time.monotonic() + timeout
    while len(published) < count and time.monotonic() < deadline:
        time.sleep(0.01)
    return len(published)


def test_invalidate_with_a_fresh_memo_walks_and_publishes(store):
    # §19: a reader that arrived while the pre-finish memo was fresh started
    # no walk; the invalidation itself walks and announces the new size.
    a = store.create_automation(make_version(), "FreshMemo", None)
    (store.auto_dir(a) / "memory" / "seen.yaml").write_text("v: 1\n", newline="")
    published = []
    store.on_memory_stats_changed = published.append
    try:
        store.memory_stats(a)
        _wait_walk(store, a)
        assert _wait_published(published, 1) == 1
        assert store.memory_stats(a)["size"] == "5 B"  # memo fresh, no walk
        (store.auto_dir(a) / "memory" / "more.yaml").write_text("v: 12\n", newline="")
        store.invalidate_memory_stats(a)
        # Nobody reads again: the invalidation's own walk lands and publishes.
        assert _wait_published(published, 2) == 2
        _wait_walk(store, a)
        time.sleep(0.05)
        assert len(published) == 2
        assert a["_memory_stats"][2]["size"] == "11 B"
    finally:
        store.on_memory_stats_changed = None


def test_invalidate_voids_an_in_flight_walk_and_the_new_walk_lands(store, monkeypatch):
    from autowright import storage as storage_mod

    a = store.create_automation(make_version(), "InFlight", None)
    (store.auto_dir(a) / "memory" / "seen.yaml").write_text("v: 1\n", newline="")
    real = storage_mod.iter_file_stats
    release = threading.Event()
    entered = threading.Event()
    calls = []

    def gated(d):
        calls.append(d)
        if len(calls) == 1:  # the old-generation walk blocks until released
            entered.set()
            release.wait(5)
        return real(d)

    monkeypatch.setattr(storage_mod, "iter_file_stats", gated)
    published = []
    store.on_memory_stats_changed = published.append
    try:
        assert store.memory_stats(a)["size"] == store.MEMORY_STATS_COMPUTING
        assert entered.wait(5)
        (store.auto_dir(a) / "memory" / "more.yaml").write_text("v: 12\n", newline="")
        store.invalidate_memory_stats(a)
        # The new-generation walk lands while the old one is still blocked.
        assert _wait_published(published, 1) == 1
        assert a["_memory_stats"][2]["size"] == "11 B"
        release.set()
        time.sleep(0.1)
        # The voided walk landed nothing and published nothing.
        assert len(published) == 1
        assert a["_memory_stats"][2]["size"] == "11 B"
        assert a["_memory_stats_walking"] is False
    finally:
        release.set()
        store.on_memory_stats_changed = None


def test_invalidate_for_a_never_asked_record_starts_no_walk(store, monkeypatch):
    a = store.create_automation(make_version(), "Unwatched", None)
    started = []
    real_thread = threading.Thread

    def recording(*args, **kwargs):
        if kwargs.get("name") == "memorystats":
            started.append(kwargs)
        return real_thread(*args, **kwargs)

    monkeypatch.setattr(threading, "Thread", recording)
    store.invalidate_memory_stats(a)
    assert started == []
    assert not a.get("_memory_stats_walking")
    assert a.get("_memory_stats") is None

def test_latest_exec_orders_by_parsed_stamp(store):
    from datetime import datetime, timedelta

    a = store.create_automation(make_version(), "Latest", None)
    real = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    real["started_at"] = (datetime.now() - timedelta(days=1)).isoformat(timespec="seconds")
    corrupt = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    corrupt["started_at"] = "not-a-timestamp"  # sorts above every ISO date as a string
    assert store._latest_exec(a["id"])["id"] == real["id"]
    real["started_at"] = "garbage too"
    assert store._latest_exec(a["id"]) is None


def test_post_draft_for_an_automation_mid_delete_is_404(client):
    from autowright.storage import store

    auto = client.post("/automations", json={"draft": make_version(), "agentId": "mock"}).json()
    store.autos[auto["id"]]["_deleting"] = True
    try:
        r = client.post("/drafts", json={"mode": "chat", "text": "x", "agentId": "mock",
                                         "automationId": auto["id"]})
    finally:
        store.autos[auto["id"]].pop("_deleting", None)
    assert r.status_code == 404
