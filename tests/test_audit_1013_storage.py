"""2026-10-13 audit fixes (storage): LF-only line splitting, lenient nested
version lists, derived execution state agreeing across paths, the delete
bookkeeping in `finally`, the every-column index upsert, retention for
deleted automations, agents replaced never mutated, and the data-location
rollback when the rollback write fails too."""
import json
import shutil
from datetime import datetime, timedelta

import pytest

from conftest import make_version


# ---------- 1. §5 line-delimited files split on LF only ----------

def test_chat_entry_with_raw_separators_round_trips(store):
    a = store.create_automation(make_version(), "Separators", None)
    text = "first paragraph second third\x85fourth"
    store.save_chat(a, [{"id": "e1", "kind": "user", "text": text}])
    chat = store.chat_json(store.chat_dir(a))
    assert [e["text"] for e in chat] == [text]
    store.append_chat_marker(a, "Draft saved as v2.")
    chat = store.chat_json(store.chat_dir(a))
    assert chat[0]["text"] == text
    assert len(chat) == 2 and chat[1].get("boundary")


def test_log_line_with_a_line_separator_is_read_whole(store):
    a = store.create_automation(make_version(), "Logger", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    for n, text in enumerate(["before", "one two", "after"], 1):
        store.append_log_line(h["id"], store.EXEC_LOG,
                              {"sequence": n, "kind": "out", "text": text,
                               "timestamp": "2026-10-13T10:00:00Z"})
    got = store.read_log(h["id"])  # no tail, no since: the whole-file path
    assert [ln["text"] for ln in got] == ["before", "one two", "after"]


# ---------- 2. §5 lenient version read covers nested lists ----------

def test_non_mapping_nested_entries_and_non_string_file_load(client):
    from autowright.storage import Store, store
    from autowright.yamlio import load_yaml, save_yaml

    a = store.create_automation(make_version(), "Lenient", None)
    manifest = store.auto_dir(a) / "versions" / "v1" / "automation.yaml"
    doc = load_yaml(manifest)
    doc["packages"] = ["requests", {"pip": "rich", "import": "rich"}]
    doc["steps"][0]["secrets"] = ["MY_TOKEN"]
    doc["steps"][0]["agents"] = ["an-agent"]
    doc["steps"][1]["file"] = 7
    save_yaml(manifest, doc)
    fresh = Store()
    fresh.load_all()
    loaded = fresh.autos[a["id"]]
    ver = loaded["versions"][1]
    assert ver["packages"] == [{"pip": "rich", "import": "rich"}]
    assert ver["steps"][0]["secrets"] == []
    assert ver["steps"][0]["agents"] == []
    assert ver["steps"][1]["file"] is None
    assert ver["steps"][1]["code"] == ""
    fresh.problems_json(loaded, ver)  # never raises
    store.load_all()
    r = client.get("/state")
    assert r.status_code == 200
    assert any(x["id"] == a["id"] for x in r.json()["automations"])


@pytest.mark.parametrize("damage", ["unparsable", "raises"])
def test_a_broken_version_folder_skips_only_that_version(store, damage):
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Two versions", None)
    versions = store.auto_dir(a) / "versions"
    shutil.copytree(versions / "v1", versions / "v2")
    if damage == "unparsable":
        (versions / "v2" / "automation.yaml").write_text("steps: [unclosed\n")
    else:
        # A spec.md that is a directory makes the reader itself raise.
        (versions / "v2" / "spec.md").unlink(missing_ok=True)
        (versions / "v2" / "spec.md").mkdir()
    fresh = Store()
    fresh.load_all()
    assert a["id"] in fresh.autos
    assert set(fresh.autos[a["id"]]["versions"]) == {1}


# ---------- 3. §5 derived execution state agrees across paths ----------

def test_boot_pass_picks_the_same_latest_as_latest_exec(store):
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Mixed stamps", None)
    early = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    late = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    # Stamps where the raw string compare and the parsed compare disagree.
    early["started_at"] = "2026-09-02T23:00:00+05:00"   # 18:00Z
    late["started_at"] = "2026-09-02 19:00:00+00:00"    # 19:00Z
    assert early["started_at"] > late["started_at"]     # the raw compare is wrong
    store.update_execution(early)
    store.update_execution(late)
    fresh = Store()
    fresh.load_all()
    assert fresh.autos[a["id"]]["_latest"]["id"] == late["id"]
    assert fresh._latest_exec(a["id"])["id"] == late["id"]


def test_boot_pass_space_form_and_z_form_agree_with_latest_exec(store):
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Space form", None)
    one = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    two = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    one["started_at"] = "2026-09-01 23:00:00"
    two["started_at"] = "2026-09-02T01:00:00Z"
    bad = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    bad["started_at"] = "not-a-timestamp"
    for h in (one, two, bad):
        store.update_execution(h)
    fresh = Store()
    fresh.load_all()
    expected = fresh._latest_exec(a["id"])
    assert expected is not None and expected["id"] != bad["id"]
    assert fresh.autos[a["id"]]["_latest"]["id"] == expected["id"]


# ---------- 4. §5 delete bookkeeping in finally ----------

def test_delete_execution_with_a_failing_index_delete_forgets_the_record(store, monkeypatch):
    a = store.create_automation(make_version(), "Deleter", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    store.update_execution(h)
    assert a["_latest"]["id"] == h["id"]
    exec_dir = store.exec_dir(h["id"])
    assert exec_dir.exists()
    reaped = []
    monkeypatch.setattr(store, "_reap_later", lambda p: reaped.append(p))

    def boom(_execution_id):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(store.execdb, "delete", boom)
    with pytest.raises(RuntimeError):
        store.delete_execution(h["id"])
    assert h["id"] not in store.execs
    assert h["id"] not in a["_exec_ids"]
    assert a["_latest"] is None
    assert a["_last_status"] == "none"
    assert not exec_dir.exists()
    assert len(reaped) == 1 and reaped[0].name.endswith(h["id"])


# ---------- 5. §5 the header upsert updates every column ----------

def test_upsert_on_conflict_updates_every_column(store):
    from autowright.execdb import ExecDB

    a = store.create_automation(make_version(), "Upsert", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    db = ExecDB(None)
    try:
        db.upsert(h)
        changed = {**h, "automation_id": "other-automation", "kind": "draft", "version": None,
                   "trigger": "cron", "trigger_sender": "someone",
                   "queued_at": "2026-10-13T09:00:00Z"}
        db.upsert(changed)
        row = db.load_all()[h["id"]]
        assert row["kind"] == "draft"
        assert row["automation_id"] == "other-automation"
        assert row["version"] is None
        assert row["trigger"] == "cron"
        assert row["trigger_sender"] == "someone"
        assert row["queued_at"] == "2026-10-13T09:00:00Z"
    finally:
        db.close()


# ---------- 6. §5 retention exempts only existing automations' latest ----------

def test_retention_sweeps_a_deleted_automations_latest(store):
    store.settings["days"] = 30
    old = (datetime.now() - timedelta(days=60)).isoformat(timespec="seconds")
    live = store.create_automation(make_version(), "Live", None)
    gone = store.create_automation(make_version(), "Gone", None)
    live_h = store.create_execution(live, "version", 1, "manual", [], status="succeeded")
    gone_old = store.create_execution(gone, "version", 1, "manual", [], status="succeeded")
    gone_latest = store.create_execution(gone, "version", 1, "manual", [], status="succeeded")
    for h, stamp in ((live_h, old), (gone_old, old),
                     (gone_latest, (datetime.now() - timedelta(days=45)).isoformat(timespec="seconds"))):
        h["started_at"] = stamp
        store.update_execution(h)
    store._refresh_exec_derived()
    store.autos.pop(gone["id"])  # its executions outlive it
    store.retention_cleanup()
    assert live_h["id"] in store.execs
    assert gone_old["id"] not in store.execs
    assert gone_latest["id"] not in store.execs


# ---------- 7. §5 agents replaced, never mutated in place ----------

def test_patch_agent_never_empties_the_shared_dict(client):
    from autowright.storage import store

    r = client.post("/agents", json={"harness": "Codex", "name": "Original"})
    assert r.status_code in (200, 201), r.text
    agent_id = r.json()["id"]
    held = next(g for g in store.agents if g["id"] == agent_id)
    r = client.patch(f"/agents/{agent_id}", json={"name": "Renamed"})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Renamed"
    assert held.get("harness") == "Codex"
    assert held.get("name") == "Original"
    current = next(g for g in store.agents if g["id"] == agent_id)
    assert current is not held and current["name"] == "Renamed"


# ---------- 8. §4.9 data-location rollback when the rollback write fails ----------

def test_data_path_rollback_write_failure_keeps_the_old_path(client, home, monkeypatch):
    from autowright import api, models
    from autowright.storage import store

    old_dir = store.executions_dir()
    before_path = store.settings.get("dataPath")
    real_load_all = store.load_all
    real_save_settings = store.save_settings
    loads, saves = [], []

    def flaky_load_all(*a, **k):
        loads.append(1)
        if len(loads) == 1:
            raise RuntimeError("reload blew up")
        return real_load_all(*a, **k)

    def flaky_save_settings(*a, **k):
        saves.append(1)
        if len(saves) == 2:  # the rollback write
            raise OSError(28, "No space left on device")
        return real_save_settings(*a, **k)

    monkeypatch.setattr(store, "load_all", flaky_load_all)
    monkeypatch.setattr(store, "save_settings", flaky_save_settings)
    with pytest.raises(OSError):
        api.set_data_path(models.DataPath(path=str(home / "elsewhere")))
    assert len(loads) == 1  # never reloaded from disk (it names the new path)
    assert store.settings.get("dataPath") == before_path
    assert store.executions_dir() == old_dir
    assert store.execdb is not None
    assert store.execdb.conn.execute("PRAGMA database_list").fetchone()[2] == \
        str(old_dir / "executions.db")
    store.execdb.load_all()  # an open connection answers
