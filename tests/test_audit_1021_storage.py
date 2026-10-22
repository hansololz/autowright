"""2026-10-21 audit fixes (store side): the index lost-update repair never
bricks startup, the top-level read-only degradation covers wrong shapes and
empty files, NFC/casefold name identity with the at-the-write re-check, a
monotonic run baseline, a duplicate's memory copy abandoned by a live source,
and a restore that refuses a version whose step script is missing."""
import os

import pytest

from conftest import make_version


# ---------- 1. §5 lost-update repair never bricks startup ----------

def _make_yaml_newer_than_index(store, execution_id, **changes):
    from autowright.yamlio import load_yaml, save_yaml

    y = store.exec_dir(execution_id) / "execution.yaml"
    doc = load_yaml(y)
    doc.update(changes)
    save_yaml(y, doc)
    db_mtime = (store.executions_dir() / "executions.db").stat().st_mtime
    os.utime(y, (db_mtime + 10, db_mtime + 10))


@pytest.mark.parametrize("changes", [{"error": "boom"}, {"status": None}])
def test_unusable_newer_yaml_keeps_the_old_index_row(store, home, changes):
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Damaged", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="failed")
    store.update_execution(h)
    store.close_exec_db()
    _make_yaml_newer_than_index(store, h["id"], **changes)
    fresh = Store()
    fresh.load_all()  # never raises
    assert fresh.execs[h["id"]]["status"] == "failed"
    assert a["id"] in fresh.autos
    fresh.close_exec_db()


# ---------- 2. §5 read-only degradation: wrong shapes and empty files ----------

def test_list_root_secrets_yaml_loads_empty_and_is_read_only(home):
    from autowright import paths
    from autowright.storage import Store, StoreUnwritableError

    paths.secrets_file().write_text("- name: A\n  id: x\n", encoding="utf-8")
    s = Store()
    s.load_all()
    assert s.secrets == []
    with pytest.raises(StoreUnwritableError):
        s.save_secrets([])


def test_non_list_agents_key_is_read_only(home):
    from autowright import paths
    from autowright.storage import Store, StoreUnwritableError

    paths.agents_file().write_text("agents: {}\n", encoding="utf-8")
    s = Store()
    s.load_all()
    assert s.agents == []
    with pytest.raises(StoreUnwritableError):
        s.save_agents([])


@pytest.mark.parametrize("content", ["", "   \n\n", "null\n", "~\n"])
def test_empty_settings_yaml_pauses_retention(home, content):
    from datetime import datetime, timedelta

    from autowright import paths
    from autowright.storage import Store, StoreUnwritableError

    s = Store()
    s.load_all()
    a = s.create_automation(make_version(), "Retained", None)
    old = s.create_execution(a, "version", 1, "manual", [], status="succeeded")
    old["started_at"] = (datetime.now() - timedelta(days=400)).isoformat(timespec="seconds")
    s.update_execution(old)
    newer = s.create_execution(a, "version", 1, "manual", [], status="succeeded")
    s.update_execution(newer)
    s.close_exec_db()
    paths.settings_file().write_text(content, encoding="utf-8")
    fresh = Store()
    fresh.load_all()
    assert fresh.retention_cleanup() == 0
    assert old["id"] in fresh.execs
    with pytest.raises(StoreUnwritableError):
        fresh.save_settings()
    assert paths.settings_file().read_text(encoding="utf-8") == content
    fresh.close_exec_db()


def test_list_root_settings_yaml_pauses_retention(home):
    from autowright import paths
    from autowright.storage import Store

    paths.settings_file().write_text("- keepForever\n", encoding="utf-8")
    s = Store()
    s.load_all()
    assert str(paths.settings_file()) in s._unreadable
    assert s.retention_cleanup() == 0


def test_absent_top_level_files_are_a_fresh_install(home):
    from autowright import paths
    from autowright.storage import Store

    for p in (paths.settings_file(), paths.agents_file(), paths.secrets_file()):
        p.unlink(missing_ok=True)
    s = Store()
    s.load_all()
    assert not s._unreadable
    s.save_secrets([])
    s.save_agents([])
    s.save_settings()


# ---------- 3. §4.1 name identity + at-the-write re-check ----------

def test_name_key_folds_nfc_and_case():
    from autowright.storage import name_key

    assert name_key("Café") == name_key("CAFÉ")
    assert name_key("  Report ") == name_key("report")


def test_create_dedupes_nfd_against_nfc(store):
    store.create_automation(make_version(), "Café", None)
    b = store.create_automation(make_version(), "Café", None)
    assert b["name"] == "Café 2"


def test_patch_automation_rechecks_the_name_under_the_lock(store):
    from autowright.storage import NameTakenError

    store.create_automation(make_version(), "Café", None)
    b = store.create_automation(make_version(), "Other", None)
    with pytest.raises(NameTakenError) as e:
        store.patch_automation(b, {"name": "CAFÉ"})
    assert e.value.name == "Café"
    assert b["name"] == "Other"
    # a case-only rename of itself still lands
    store.patch_automation(b, {"name": "OTHER"})
    assert b["name"] == "OTHER"


# ---------- 5. §4.3 run baseline never moves backwards ----------

def test_retry_of_an_older_record_keeps_the_baseline(store):
    from datetime import datetime, timedelta

    a = store.create_automation(make_version(), "Baseline", None)
    older = store.create_execution(a, "version", 1, "manual", [], status="executing")
    older["started_at"] = (datetime.now() - timedelta(hours=2)).isoformat(timespec="seconds")
    older["status"] = "failed"
    store.update_execution(older)
    newer = store.create_execution(a, "version", 1, "manual", [], status="executing")
    newer["status"] = "succeeded"
    store.update_execution(newer)
    assert a["_last_exec_at"] == newer["started_at"]
    # §7 in-place retry: the older record flips back to executing, keeping
    # its original started_at.
    older["status"] = "executing"
    store.update_execution(older)
    assert a["_last_exec_at"] == newer["started_at"]
    assert a["_latest"]["id"] == newer["id"]
    assert older["id"] in a["_live"]


def test_promotion_moves_the_baseline_forward(store):
    a = store.create_automation(make_version(), "Forward", None)
    first = store.create_execution(a, "version", 1, "manual", [], status="executing")
    first["status"] = "succeeded"
    first["started_at"] = "2026-01-01T09:00:00"
    store.update_execution(first)
    second = store.create_execution(a, "version", 1, "manual", [], status="queued")
    store.promote_execution(second, [])
    assert a["_last_exec_at"] == second["started_at"]
    assert a["_latest"]["id"] == second["id"]


# ---------- 6. §5 duplicate's memory copy re-checks the source ----------

def test_copy_memory_abandons_when_the_source_went_live(store, monkeypatch):
    import shutil

    from autowright.storage import LiveExecutionError

    src = store.create_automation(make_version(), "Source", None)
    (store.auto_dir(src) / "memory" / "seen.json").write_text("[1]")
    copy = store.duplicate_automation(src["id"])
    real = shutil.copytree

    def racing_copytree(*args, **kwargs):
        out = real(*args, **kwargs)
        src["_live"].add("racing-execution")  # an execution of the source started
        return out
    monkeypatch.setattr(shutil, "copytree", racing_copytree)
    with pytest.raises(LiveExecutionError):
        store.copy_memory_into(copy, src["id"])
    assert not any((store.auto_dir(copy) / "memory").iterdir())
    assert not (store.auto_dir(copy) / ".ad-tmp-memory").exists()


# ---------- 9. §5 restore refuses a missing step script ----------

def test_restore_of_a_version_missing_its_script_is_refused(store):
    from autowright.storage import MissingStepScriptError

    a = store.create_automation(make_version(), "Missing", None)
    store.save_new_version(a, make_version(note="v2"))
    (store.auto_dir(a) / "versions" / "v1" / "01-say.py").unlink()
    with pytest.raises(MissingStepScriptError) as e:
        store.restore_version(a, 1)
    assert str(e.value) == "step script `01-say.py` is missing on disk"
    assert a["current_version"] == 2
    assert not (store.auto_dir(a) / "versions" / "v3").exists()


def test_restore_with_scripts_present_still_works(store):
    a = store.create_automation(make_version(), "Present", None)
    store.save_new_version(a, make_version(note="v2"))
    assert store.restore_version(a, 1) == 3

