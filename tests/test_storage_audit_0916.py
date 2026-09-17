"""§5/§19 store-audit regressions (2026-09-16): the registered-object guard on
every write into an automation directory, the delete paths' disk errors, the
per-automation execution index behind `_latest_exec` / `queued_execs`, and the
per-call work /state used to redo (log slicing, reference scans, yaml parses)."""
import json
import shutil
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

from conftest import _rename_denied


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


# ---------- §19 sinceSequence: a followed log is sliced, not re-parsed ----------

def _exec_with_log(store, count, sequence=lambda i: i):
    """An execution whose execution log holds `count` lines, written straight
    to disk — the sequence of line i is `sequence(i)`."""
    a = store.create_automation(make_version(), "Chatty", None)
    h = store.create_execution(a, "version", 1, "manual", [])
    p = store.log_file(h["id"], store.EXEC_LOG)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(
        json.dumps({"timestamp": "2026-09-16T10:00:00+00:00", "kind": "out",
                    "sequence": sequence(i), "text": f"line {i}"}) + "\n"
        for i in range(1, count + 1)), encoding="utf-8")
    return h


def _count_json_loads(monkeypatch):
    """The lines the read really parsed — the cost sinceSequence is about."""
    real = json.loads
    parsed = []

    def loads(text, *args, **kwargs):
        parsed.append(text)
        return real(text, *args, **kwargs)

    monkeypatch.setattr(json, "loads", loads)
    return parsed


def test_a_followed_log_is_sliced_at_the_sequence_already_served(store, monkeypatch):
    """§19: sequences are gapless per-file line numbers, so a 1 Hz follow of a
    3000-line file parses the ten new lines plus the one probe that proves the
    slice — never the 2990 it already served."""
    h = _exec_with_log(store, 3000)
    parsed = _count_json_loads(monkeypatch)
    lines = store.read_log(h["id"], since=2990)
    assert [l["sequence"] for l in lines] == list(range(2991, 3001))
    assert len(parsed) <= 11


def test_a_log_whose_sequences_dont_line_up_is_parsed_whole(store, monkeypatch):
    """§19: the slice is taken only when the line at that position really
    carries that sequence — a file that doesn't line up is read as before, and
    the caller's own filter decides what to keep."""
    h = _exec_with_log(store, 100, sequence=lambda i: i + 500)
    parsed = _count_json_loads(monkeypatch)
    lines = store.read_log(h["id"], since=50)
    assert len(parsed) == 101  # the probe, then the whole file
    assert [l["sequence"] for l in lines] == list(range(501, 601))


# ---------- §6.3: the restore swap re-checks registration ----------

def test_a_restore_racing_a_delete_never_recreates_the_memory_directory(store, monkeypatch):
    """§6.3: the copy runs outside the lock, so a DELETE can land under it —
    the swap discards the staged copy instead of renaming it into the removed
    automation's directory."""
    from autowright.storage import MEMORY_SWAP_TMP, AutomationGoneError

    a = store.create_automation(make_version(), "Racing", None)
    mem = store.auto_dir(a) / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "notes.md").write_text("remember", encoding="utf-8")
    snap = store.snapshot_memory(a, "manual", name="Keep")
    # §6.3: with the pre-restore toggle off nothing else re-checks registration
    # between the copy and the rename — the swap's own check is the guard.
    a["memory_snapshots"]["pre_restore"] = False

    real = shutil.copytree

    def copytree(src, dst, *args, **kwargs):
        out = real(src, dst, *args, **kwargs)
        if Path(dst).name == MEMORY_SWAP_TMP:
            store.delete_automation(a)  # the DELETE lands between copy and swap
        return out

    monkeypatch.setattr(shutil, "copytree", copytree)
    with pytest.raises(AutomationGoneError):
        store.restore_snapshot(a, snap["id"])
    store.drain_reaper()
    assert not (store.auto_dir(a) / "memory").exists()
    assert not (store.auto_dir(a) / MEMORY_SWAP_TMP).exists()
    assert not store.auto_dir(a).exists()


# ---------- §5: the engine's in-memory header keys are seeded at creation ----------

def test_a_new_execution_header_carries_the_engines_in_memory_keys(store):
    """§5: the engine fills these off-lock as the execution runs — seeded here
    so a registered header never resizes while a reader copies it under the
    lock. They stay in memory: no serializer carries a `_` key."""
    from autowright.yamlio import load_yaml

    a = store.create_automation(make_version(), "Seeded", None)
    h = store.create_execution(a, "version", 1, "manual", [])
    assert h["_cur_step"] is None
    assert h["_cur"] is None
    assert h["_log_seq"] == {}
    assert h["_pass_start"] is None
    assert [k for k in store.exec_json(h, full=True) if k.startswith("_")] == []
    assert [k for k in load_yaml(store.exec_yaml_path(h["id"])) if k.startswith("_")] == []
    assert [k for k in store.exec_header(h) if k.startswith("_")] == []


# ---------- §19 /state: the per-call work is memoized ----------

def test_the_current_versions_references_are_scanned_once_per_version(store, monkeypatch):
    """§4.1: /state derives `problems` for every automation on every call —
    the scan over every step's code runs once per version, not once per call."""
    a = store.create_automation(make_version(), "Refs", None)
    first = store.current_reference_ids(a)

    scans = []
    real = store.effective_reference_ids
    monkeypatch.setattr(type(store), "effective_reference_ids",
                        staticmethod(lambda cur: scans.append(cur) or real(cur)))
    assert store.current_reference_ids(a) == first
    assert scans == []  # the memo answers

    store.save_new_version(a, make_version())
    store.current_reference_ids(a)
    assert len(scans) == 1  # a new current version is scanned once


def _count_yaml_loads(monkeypatch):
    from autowright import storage as storage_mod

    real = storage_mod.load_yaml
    reads = []

    def load_yaml(path, default=None):
        reads.append(path)
        return real(path, default)

    monkeypatch.setattr(storage_mod, "load_yaml", load_yaml)
    return reads


def test_the_draft_test_summary_is_parsed_once_until_the_file_changes(store, monkeypatch):
    """§19: /state serializes a draft per automation — `test.yaml` is parsed
    once and the memo is keyed on the file's stat, so a rewrite invalidates it
    without anyone calling an invalidator."""
    from autowright.yamlio import save_yaml

    a = store.create_automation(make_version(), "Drafting", None)
    container = store.draft_dir(a)
    container.mkdir(parents=True, exist_ok=True)
    save_yaml(container / "test.yaml",
              {"status": "succeeded", "when": "2026-09-16T10:00:00", "execution_id": "e1"})

    reads = _count_yaml_loads(monkeypatch)
    assert store.draft_test_json(container)["status"] == "succeeded"
    assert store.draft_test_json(container)["status"] == "succeeded"
    assert len(reads) == 1

    save_yaml(container / "test.yaml",
              {"status": "failed", "when": "2026-09-16T10:05:00", "execution_id": "e2",
               "steps_fingerprint": "abcdef"})
    assert store.draft_test_json(container)["stepsFingerprint"] == "abcdef"
    assert len(reads) == 2


def test_an_absent_test_summary_is_memoized_too(store, monkeypatch):
    """The absent file is the common case (§11: no test has finished yet) —
    it must not cost a parse attempt per /state either."""
    a = store.create_automation(make_version(), "Untested", None)
    container = store.draft_dir(a)
    assert store.draft_test_json(container) is None
    reads = _count_yaml_loads(monkeypatch)
    assert store.draft_test_json(container) is None
    assert reads == []


def test_the_pending_draft_summary_is_parsed_once_until_the_slot_changes(store, monkeypatch):
    """§19 /state `pendingDraft`: the same rule for the §4.4 pending slot."""
    store.save_draft(None, make_version(), name="Pending", agent_id=None, triggers=[])
    assert store.pending_draft_summary()["name"] == "Pending"

    reads = _count_yaml_loads(monkeypatch)
    assert store.pending_draft_summary()["name"] == "Pending"
    assert reads == []

    store.save_draft(None, make_version(), name="Renamed pending", agent_id=None, triggers=[])
    assert store.pending_draft_summary()["name"] == "Renamed pending"
