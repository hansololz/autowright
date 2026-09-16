"""§5/§19 store-audit regressions (2026-09-16): the registered-object guard on
every write into an automation directory, the delete paths' disk errors, and
the per-automation execution index behind `_latest_exec` / `queued_execs`."""
from pathlib import Path

import pytest

from conftest import make_version


# ---------- §19 registered-object guard ----------

def _deleted(store):
    """An automation record that a DELETE already removed — still in hand, no
    longer the registered one (exactly what a request resolved mid-delete holds)."""
    a = store.create_automation(make_version(), "Gone", None)
    store.delete_automation(a)
    store.drain_reaper()
    assert not store.auto_dir(a).exists()
    return a


@pytest.mark.parametrize("write", [
    lambda store, a: store.save_new_version(a, make_version()),
    lambda store, a: store.restore_version(a, 1),
    lambda store, a: store.patch_automation(a, {"description": "back from the dead"}),
    lambda store, a: store.save_draft(a, make_version()),
    lambda store, a: store.save_chat(a, [{"kind": "user", "text": "hi"}]),
    lambda store, a: store.open_draft(a),
    lambda store, a: store.clear_memory(a),
    lambda store, a: store.migrate_pending_chat(a),
    lambda store, a: store.stage_snapshot(a, "manual"),
    lambda store, a: store.commit_snapshot(a, (store.auto_dir(a) / "staged", 0, 0), "manual"),
    lambda store, a: store.restore_snapshot(a, "11111111-1111-1111-1111-111111111111"),
    lambda store, a: store.delete_snapshot(a, "11111111-1111-1111-1111-111111111111"),
    lambda store, a: store.rename_snapshot(a, "11111111-1111-1111-1111-111111111111", "Kept"),
])
def test_a_write_on_a_deleted_record_refuses_instead_of_recreating_the_tree(store, write):
    """§19: a mutation racing the DELETE would otherwise re-create the removed
    directory, and the automation would come back at the next boot."""
    from autowright.storage import AutomationGoneError

    a = _deleted(store)
    with pytest.raises(AutomationGoneError):
        write(store, a)
    assert not store.auto_dir(a).exists()


def test_the_pending_draft_slot_is_not_an_automation(store):
    """§4.4: the `pending` owner has no record — its writes always work."""
    store.open_draft(None)
    store.save_draft(None, make_version(), name="Pending", agent_id=None, triggers=[])
    store.save_chat(None, [{"kind": "user", "text": "hi"}])
    assert store.load_pending_draft()["name"] == "Pending"
    assert store.chat_json(store.chat_dir(None))[0]["text"] == "hi"


# ---------- §19 delete paths: "nothing there" only means FileNotFoundError ----------

def _rename_denied(monkeypatch, name: str):
    real = Path.rename

    def rename(self, target):
        if self.name == name:
            raise PermissionError(13, "Permission denied")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", rename)


def test_delete_execution_keeps_the_record_when_the_directory_cannot_go(store, monkeypatch):
    """§19: the header must never be dropped from the index while its
    directory survives to be re-adopted at the next startup."""
    a = store.create_automation(make_version(), "Kept", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    _rename_denied(monkeypatch, h["id"])
    with pytest.raises(PermissionError):
        store.delete_execution(h["id"])
    assert h["id"] in store.execs
    assert h["id"] in store.execdb.load_all()
    assert h["id"] in a["_exec_ids"]


def test_delete_automation_keeps_the_record_when_the_directory_cannot_go(store, monkeypatch):
    a = store.create_automation(make_version(), "Kept", None)
    _rename_denied(monkeypatch, a["id"])
    with pytest.raises(PermissionError):
        store.delete_automation(a)
    assert store.autos.get(a["id"]) is a
    assert store.auto_dir(a).exists()


def test_a_header_only_execution_still_deletes(store):
    """The FileNotFoundError case is unchanged: nothing on disk, record gone."""
    a = store.create_automation(make_version(), "Header only", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    store._remove_tree(store.exec_dir(h["id"]))
    store.drain_reaper()
    store.delete_execution(h["id"])
    assert h["id"] not in store.execs


def test_removing_a_missing_tree_is_still_a_no_op(store):
    store._remove_tree(store.executions_dir() / "not-here")  # FileNotFoundError arm


# ---------- §5: the per-automation execution index, not a full scan ----------

def test_latest_and_queued_read_the_automations_own_index(store):
    """§5 `_exec_ids`: another automation's history costs nothing here, and the
    index — not a scan over every header — is what decides the answer."""
    a = store.create_automation(make_version(), "Mine", None)
    other = store.create_automation(make_version(), "Theirs", None)
    store.create_execution(other, "version", 1, "manual", [], status="succeeded")
    store.create_execution(other, "version", 1, "discord", [], status="queued")
    older = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    newer = store.create_execution(a, "version", 1, "manual", [], status="failed")
    waiting = store.create_execution(a, "version", 1, "discord", [], status="queued")

    assert store._latest_exec(a["id"])["id"] == newer["id"]
    assert [h["id"] for h in store.queued_execs(a["id"])] == [waiting["id"]]

    # A header the index doesn't carry is not this automation's: the index is
    # the source, not a walk over store.execs.
    a["_exec_ids"].discard(newer["id"])
    assert store._latest_exec(a["id"])["id"] == older["id"]
    a["_exec_ids"].discard(waiting["id"])
    assert store.queued_execs(a["id"]) == []


# ---------- §19: the sweeps log and move on ----------

def test_the_retention_sweep_moves_on_past_a_record_it_cannot_remove(store, monkeypatch):
    """§19: one stuck directory must not keep the rest of the batch on disk."""
    a = store.create_automation(make_version(), "Old", None)
    stuck = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    other = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    for h in (stuck, other):
        h["started_at"] = "2020-01-01T00:00:00"  # well past any retention window
    store.settings["days"] = 1
    _rename_denied(monkeypatch, stuck["id"])

    assert store.retention_cleanup() == 1
    assert other["id"] not in store.execs
    assert stuck["id"] in store.execs  # still registered, never silently dropped


def test_settling_a_draft_moves_on_past_a_test_record_it_cannot_remove(store, monkeypatch):
    """§11 settle: the ids it answers with are the ones it really deleted —
    a record still on disk is not announced as deleted."""
    shadow = {"id": None, "name": "Draft"}
    stuck = store.create_execution(shadow, "test", None, "test", [], status="succeeded")
    other = store.create_execution(shadow, "test", None, "test", [], status="succeeded")
    _rename_denied(monkeypatch, stuck["id"])

    assert store.delete_test_execs(None) == [other["id"]]
    assert stuck["id"] in store.execs


def test_executions_of_a_deleted_automation_still_resolve(store):
    """§4.5: a deleted automation's real records stay — there is no index left
    to read, so the scan answers for them."""
    a = store.create_automation(make_version(), "Gone", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    store.autos.pop(a["id"])
    assert store._latest_exec(a["id"])["id"] == h["id"]
