"""Store-layer audit regressions (2026-09-18).

§5 disk-first: a failed `automation.yaml` write rolls the in-memory record
back, the startup scan reclaims the crash leftovers nothing can ever adopt
(an execution directory with no `execution.yaml`, a uuid-named automation
directory with no `automation.yaml`), and a deleted §11 test execution takes
the draft container's `test.yaml` with it.
"""
import pytest
from conftest import make_version

from autowright import paths
from autowright.storage import Store, new_id
from autowright.yamlio import save_yaml


def _boom(*_a, **_kw):
    raise OSError(28, "No space left on device")


# ---------- §5: memory never runs ahead of disk ----------

def test_a_failed_version_write_rolls_the_record_back(store, monkeypatch):
    """§5: the new version's folder is on disk (the next save skips its
    number), but the pointer, the versions map and every other stored field
    stay exactly as the last successful write left them."""
    a = store.create_automation(make_version(), "Rollback", None)
    before = {"current_version": a["current_version"], "versions": set(a["versions"]),
              "name": a["name"], "triggers": list(a["triggers"]),
              "updated_at": a["updated_at"]}

    monkeypatch.setattr(store, "_write_toplevel", _boom)
    with pytest.raises(OSError):
        store.save_new_version(a, make_version(note="Second"))

    assert a["current_version"] == before["current_version"]
    assert set(a["versions"]) == before["versions"]
    assert a["name"] == before["name"] and a["triggers"] == before["triggers"]
    assert a["updated_at"] == before["updated_at"]


def test_a_failed_patch_write_rolls_every_field_back(store, monkeypatch):
    """§5: a PATCH that can't reach disk leaves nothing of itself in memory —
    including the containers it edits in place (paramValues, triggers)."""
    a = store.create_automation(make_version(), "Patched", None)
    store.patch_automation(a, {"paramValues": {"greeting": "hi"}})
    before = {"name": a["name"], "description": a["description"],
              "param_values": dict(a["param_values"]),
              "triggers": list(a["triggers"]), "updated_at": a["updated_at"]}

    monkeypatch.setattr(store, "_write_toplevel", _boom)
    with pytest.raises(OSError):
        store.patch_automation(a, {
            "name": "Renamed", "description": "new", "paramValues": {"greeting": "bye"},
            "triggers": [{"id": new_id(), "kind": "manual", "enabled": True}]})

    assert a["name"] == before["name"] and a["description"] == before["description"]
    assert a["param_values"] == before["param_values"]
    assert a["triggers"] == before["triggers"]
    assert a["updated_at"] == before["updated_at"]


def test_a_failed_restore_write_rolls_the_record_back(store, monkeypatch):
    """§5: the same rule as a save — a restore that can't reach disk leaves
    the pointer and the versions map where the last successful write left
    them (the vN folder on disk is tolerated)."""
    a = store.create_automation(make_version(), "Restored", None)
    store.save_new_version(a, make_version(note="Second"))
    before = {"current_version": a["current_version"], "versions": set(a["versions"]),
              "updated_at": a["updated_at"]}

    monkeypatch.setattr(store, "_write_toplevel", _boom)
    with pytest.raises(OSError):
        store.restore_version(a, 1)

    assert a["current_version"] == before["current_version"]
    assert set(a["versions"]) == before["versions"]
    assert a["updated_at"] == before["updated_at"]


def test_a_failed_trigger_consumption_rolls_the_list_back(store, monkeypatch):
    """§4.3/§5: a one-shot that couldn't be written out stays in the list, so
    the next tick consumes it again rather than the firing being lost."""
    trigger = {"id": new_id(), "kind": "manual", "enabled": True}
    a = store.create_automation(make_version(), "Triggered", None, triggers=[trigger])

    monkeypatch.setattr(store, "_write_toplevel", _boom)
    with pytest.raises(OSError):
        store.consume_trigger(a, trigger["id"])

    assert [t["id"] for t in a["triggers"]] == [trigger["id"]]


def test_a_successful_patch_still_lands(store):
    """The rollback wrapper is invisible on the happy path."""
    a = store.create_automation(make_version(), "Fine", None)
    store.patch_automation(a, {"name": "Renamed", "maxQueued": 3})
    assert a["name"] == "Renamed" and a["max_queued"] == 3
    assert Store.TOPLEVEL_FIELDS  # the snapshot list the rollback restores
    s2 = Store()
    s2.load_all()
    assert s2.autos[a["id"]]["name"] == "Renamed"


# ---------- §5: the startup scan reclaims crash leftovers ----------

def test_an_execution_directory_with_no_yaml_is_removed_at_load(store):
    """§5: the directory is made before the record is written, so one without
    an `execution.yaml` is a crash leftover nothing can ever adopt."""
    leftover = store.executions_dir() / new_id()
    (leftover / "workspace").mkdir(parents=True)

    s2 = Store()
    s2.load_all()
    s2.drain_reaper()
    assert not leftover.exists()
    assert not list(store.executions_dir().glob(f"{Store.DELETED_PREFIX}*"))


def test_an_execution_with_an_unreadable_yaml_stays_out_of_the_index(store):
    """§5: a damaged record is degraded, never destroyed — the directory stays
    on disk, out of the index, like any other unreadable file."""
    damaged = store.executions_dir() / new_id()
    damaged.mkdir(parents=True)
    (damaged / "execution.yaml").write_text("id: [unclosed\n", encoding="utf-8")

    s2 = Store()
    s2.load_all()
    s2.drain_reaper()
    assert damaged.is_dir()
    assert damaged.name not in s2.execs


def test_a_uuid_named_automation_directory_with_no_manifest_goes_at_startup(store, caplog):
    """§5: a create that failed after its version folder landed is logged and
    removed — but only by the backend's own boot load."""
    import logging

    orphan = paths.automations_dir() / new_id()
    (orphan / "versions" / "v1").mkdir(parents=True)

    with caplog.at_level(logging.WARNING, logger="autowright.storage"):
        reload = Store()
        reload.load_all()
        reload.drain_reaper()
    assert orphan.is_dir()  # §4.9 data-location reload: a create may be in flight
    assert "has no automation.yaml" in " | ".join(r.getMessage() for r in caplog.records)

    boot = Store()
    boot.load_all(startup=True)
    boot.drain_reaper()
    assert not orphan.exists()
    assert not list(paths.automations_dir().glob(f"{Store.DELETED_PREFIX}*"))


def test_startup_keeps_a_directory_the_app_never_made(store):
    """§5: only a uuid-named directory is the app's to remove — a hand-made
    folder (and a staging dir with its own sweep) stays put."""
    handmade = paths.automations_dir() / "my notes"
    handmade.mkdir()
    staging = paths.automations_dir() / ".ad-tmp-something"
    staging.mkdir()

    boot = Store()
    boot.load_all(startup=True)
    boot.drain_reaper()
    assert handmade.is_dir() and staging.is_dir()


# ---------- §5: a deleted test execution takes its summary with it ----------

def _test_record(store, a, execution_id=None):
    """A §11 test record plus the draft container's `test.yaml` naming it."""
    h = store.create_execution(a, "test", None, "manual", [], status="succeeded")
    container = store.draft_dir(a)
    container.mkdir(parents=True, exist_ok=True)
    save_yaml(container / "test.yaml",
              {"status": "succeeded", "when": h["started_at"],
               "execution_id": execution_id or h["id"]})
    return h, container


def test_deleting_a_test_execution_drops_the_draft_test_summary(store):
    """§5: the §11 TEST card must not link to a record that is gone."""
    a = store.create_automation(make_version(), "Tested", None)
    h, container = _test_record(store, a)

    store.delete_execution(h["id"])
    assert not (container / "test.yaml").exists()


def test_a_summary_naming_another_execution_stays(store):
    a = store.create_automation(make_version(), "Tested", None)
    h, container = _test_record(store, a, execution_id="another-one")

    store.delete_execution(h["id"])
    assert (container / "test.yaml").exists()


def test_retention_drops_the_summary_of_the_test_it_deletes(store):
    """§5 retention paragraph: the sweep goes through the same delete."""
    a = store.create_automation(make_version(), "Tested", None)
    h, container = _test_record(store, a)
    h["started_at"] = "2020-01-01T00:00:00.000000+00:00"
    store.settings["days"] = 1

    assert store.retention_cleanup() == 1
    store.drain_reaper()
    assert not (container / "test.yaml").exists()
