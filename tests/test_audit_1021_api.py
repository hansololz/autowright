"""2026-10-21 audit fixes (API side): unreadable-shape top-level files answer
the 409, NFC name identity and the at-the-write rename re-check, a second
DELETE, the duplicate's memory-copy guard and warning, a failed snapshot
commit discards its staged copy, whitespace-only secret values are blank, and
a restore refuses a version whose step script is missing on disk."""
import shutil

import pytest

from conftest import make_version


# ---------- 2. §5 read-only degradation through the routes ----------

def test_list_root_secrets_yaml_answers_409_and_survives(client):
    from autowright import paths
    from autowright.storage import store

    content = "- name: A\n  id: x\n"
    paths.secrets_file().write_text(content, encoding="utf-8")
    store.load_all()
    assert store.secrets == []
    r = client.post("/secrets", json={"name": "MY_TOKEN", "value": "abc"})
    assert r.status_code == 409
    assert "unreadable on disk" in r.json()["detail"]
    assert paths.secrets_file().read_text(encoding="utf-8") == content


def test_non_list_agents_key_answers_409_and_survives(client):
    from autowright import paths
    from autowright.storage import store

    content = "agents: {}\n"
    paths.agents_file().write_text(content, encoding="utf-8")
    store.load_all()
    assert store.agents == []
    r = client.post("/agents", json={"harness": "Claude Code", "mode": "default"})
    assert r.status_code == 409
    assert "unreadable on disk" in r.json()["detail"]
    assert paths.agents_file().read_text(encoding="utf-8") == content


def test_empty_settings_yaml_answers_409(client):
    from autowright import paths
    from autowright.storage import store

    paths.settings_file().write_text("", encoding="utf-8")
    store.load_all()
    assert client.patch("/settings", json={"days": 5}).status_code == 409
    assert paths.settings_file().read_text(encoding="utf-8") == ""


# ---------- 3. §4.1 name identity + §19 at-the-write re-check ----------

def test_nfd_rename_collides_with_nfc_name(client):
    from autowright.storage import store

    store.create_automation(make_version(), "Café", "mock")
    b = store.create_automation(make_version(), "Other", "mock")
    r = client.patch(f"/automations/{b['id']}", json={"name": "café"})
    assert r.status_code == 422
    assert r.json()["detail"] == ("an automation named 'Café' already exists - "
                                  "automation names must be unique")
    assert b["name"] == "Other"


def _take_name_after_precheck(monkeypatch):
    """The up-front check passes, then another automation takes the name
    before the write."""
    from autowright import api
    from autowright.storage import store

    real = api._check_automation_name_free

    def racing(name, exclude_id=None):
        real(name, exclude_id)
        store.create_automation(make_version(), name, "mock")
    monkeypatch.setattr(api, "_check_automation_name_free", racing)


def test_patch_rename_raced_after_precheck_answers_422(client, monkeypatch):
    from autowright.storage import store
    from autowright.yamlio import load_yaml

    b = store.create_automation(make_version(), "Other", "mock")
    _take_name_after_precheck(monkeypatch)
    r = client.patch(f"/automations/{b['id']}", json={"name": "Weekly"})
    assert r.status_code == 422
    assert r.json()["detail"] == ("an automation named 'Weekly' already exists - "
                                  "automation names must be unique")
    assert b["name"] == "Other"
    assert load_yaml(store.auto_dir(b) / "automation.yaml")["name"] == "Other"


def test_version_save_rename_raced_after_precheck_stores_nothing(client, monkeypatch):
    from autowright.storage import store

    b = store.create_automation(make_version(), "Other", "mock")
    _take_name_after_precheck(monkeypatch)
    draft = make_version(note="changed")
    draft["steps"][0]["code"] += "x = 1\n"
    r = client.post(f"/automations/{b['id']}/versions", json={"draft": draft, "name": "Weekly"})
    assert r.status_code == 422
    assert "already exists - automation names must be unique" in r.json()["detail"]
    assert b["name"] == "Other"
    assert b["current_version"] == 1
    assert not (store.auto_dir(b) / "versions" / "v2").exists()


# ---------- 4. §19 a second DELETE ----------

def test_second_delete_while_deleting_answers_409(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Going", "mock")
    with store.lock:
        a["_deleting"] = True
    r = client.delete(f"/automations/{a['id']}")
    assert r.status_code == 409
    assert r.json()["detail"] == "already being deleted"
    assert a["_deleting"] is True and a["id"] in store.autos
    a.pop("_deleting")
    assert client.delete(f"/automations/{a['id']}").status_code == 200


# ---------- 6. §19 duplicate's memory copy ----------

def test_duplicate_with_memory_while_source_live_is_409(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Busy", "mock")
    a["_live"].add("live-execution")
    before = set(store.autos)
    r = client.post(f"/automations/{a['id']}/duplicate", json={"copyMemory": True})
    assert r.status_code == 409
    assert r.json()["detail"] == "an execution is in progress"
    assert set(store.autos) == before
    # without the memory copy a live source still duplicates
    r = client.post(f"/automations/{a['id']}/duplicate", json={})
    assert r.status_code == 201 and "warning" not in r.json()
    a["_live"].discard("live-execution")


def test_duplicate_raced_by_a_source_execution_answers_201_with_warning(client, monkeypatch):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Racy", "mock")
    (store.auto_dir(a) / "memory" / "seen.json").write_text("[1]")
    real = shutil.copytree

    def racing_copytree(src, dst, *args, **kwargs):
        out = real(src, dst, *args, **kwargs)
        if str(src) == str(store.auto_dir(a) / "memory"):
            a["_live"].add("racing-execution")
        return out
    monkeypatch.setattr(shutil, "copytree", racing_copytree)
    r = client.post(f"/automations/{a['id']}/duplicate", json={"copyMemory": True})
    assert r.status_code == 201
    body = r.json()
    assert body["warning"] == "Its memory was not copied - an execution started meanwhile"
    copy = store.autos[body["id"]]
    assert not any((store.auto_dir(copy) / "memory").iterdir())
    # the warning is this response's alone
    assert "warning" not in client.get(f"/automations/{copy['id']}").json()
    a["_live"].discard("racing-execution")


def test_duplicate_memory_copy_failure_answers_201_with_warning(client, monkeypatch):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Full", "mock")

    def broken(target, source_id):
        raise OSError("disk full")
    monkeypatch.setattr(store, "copy_memory_into", broken)
    r = client.post(f"/automations/{a['id']}/duplicate", json={"copyMemory": True})
    assert r.status_code == 201
    assert r.json()["warning"] == "Its memory could not be copied: disk full"
    assert r.json()["id"] in store.autos


# ---------- 7. §6.3 a failed snapshot commit discards its staged copy ----------

def test_failed_snapshot_commit_discards_the_staged_copy(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Snap", "mock")
    (store.auto_dir(a) / "memory" / "seen.json").write_text("[1]")
    staged_paths = []
    real_stage = store.stage_snapshot

    def recording_stage(*args, **kwargs):
        staged = real_stage(*args, **kwargs)
        staged_paths.append(staged[0])
        return staged

    def failing_commit(*_args, **_kwargs):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(store, "stage_snapshot", recording_stage)
    monkeypatch.setattr(store, "commit_snapshot", failing_commit)
    with pytest.raises(OSError):
        api.create_snapshot(a["id"], None)
    assert staged_paths and not staged_paths[0].exists()


# ---------- 8. §4.8 whitespace-only secret values are blank ----------

def test_whitespace_only_value_creates_a_placeholder(client):
    from autowright import keychain

    r = client.post("/secrets", json={"name": "BLANK_TOKEN", "value": "  \t "})
    assert r.status_code == 200
    assert r.json()["set"] is False
    assert keychain.get_secret(r.json()["id"]) is None


def test_whitespace_only_value_on_update_keeps_the_state(client):
    from autowright import keychain

    placeholder = client.post("/secrets", json={"name": "EMPTY_ONE"}).json()
    r = client.put(f"/secrets/{placeholder['id']}", json={"value": "   "})
    assert r.status_code == 200 and r.json()["set"] is False
    assert keychain.get_secret(placeholder["id"]) is None

    stored = client.post("/secrets", json={"name": "REAL_ONE", "value": "abc"}).json()
    r = client.put(f"/secrets/{stored['id']}", json={"value": " \n "})
    assert r.status_code == 200 and r.json()["set"] is True
    assert keychain.get_secret(stored["id"]) == "abc"


# ---------- 9. §5 restore refuses a missing step script ----------

def test_restore_with_a_missing_step_script_is_409(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Restore", "mock")
    store.save_new_version(a, make_version(note="v2"))
    (store.auto_dir(a) / "versions" / "v1" / "02-finish.py").unlink()
    r = client.post(f"/automations/{a['id']}/restore", json={"version": 1})
    assert r.status_code == 409
    assert r.json()["detail"] == "step script `02-finish.py` is missing on disk"
    assert a["current_version"] == 2
