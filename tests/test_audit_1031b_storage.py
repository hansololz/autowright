"""2026-10-31 second sweep (storage): the param-definition and param-value
migrate-on-load extended (§21 2026-10-31 (a)/(b) second sweep: a non-finite
`min`, `kv` text rows, `.inf` numbers), the retention clock-ahead anchor,
symlinks copied as links, absence only on not-found, a typed orphan
set-aside error, the ISO snapshot `created_at`, the execution index check
degrading, a failed version write leaving nothing, the §6.3 execution-start
counter, and the single-flight memory-size walk."""
import errno
import json
import os
import shutil
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml
from conftest import make_version
from test_audit_1030b_pipeline import _validate

from autowright import storage as storage_mod


def _rewrite(path: Path, edit) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _draft_from(ver: dict) -> dict:
    return {
        "description": "Test automation",
        "note": "Edited",
        "params": ver["params"],
        "steps": [{"file": s["file"], "name": s["name"], "description": s.get("description", ""),
                   "code": s["code"]} for s in ver["steps"]],
        "spec": ver["spec"],
    }


# ---------- A/B/F §21 2026-10-31 second sweep: the old-shape fixture ----------

def test_old_shape_min_kv_rows_and_inf_load_serialize_and_save(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Old shape 2", "mock")
    d = store.auto_dir(a)
    manifest = d / "versions" / "v1" / "automation.yaml"

    def version(data):
        # Shapes a released build could save before the §8 validator checked them.
        data["params"] = [
            {"name": "wait", "kind": "number", "label": "Wait", "help": "", "min": "1e3", "default": 5},
            {"name": "low", "kind": "number", "label": "Low", "help": "", "min": None, "default": 2},
            {"name": "headers", "kind": "kv", "label": "Headers", "help": "",
             "default": [{"key": 8080, "value": "port"}, {"key": None, "value": "x"}, "junk"]},
            {"name": "big", "kind": "number", "label": "Big", "help": "", "default": float("inf")},
        ]
    _rewrite(manifest, version)

    def top(data):
        data["param_values"] = {"big": float("inf"), "headers": [{"key": 1, "value": "a"}],
                                "wait": 7, "gone": "kept"}
    _rewrite(d / "automation.yaml", top)
    before = manifest.read_text(encoding="utf-8")

    store.load_all()
    b = store.autos[a["id"]]
    params = {p["name"]: p for p in b["versions"][1]["params"]}
    assert "min" not in params["wait"] and params["wait"]["default"] == 5
    assert "min" not in params["low"]
    assert params["headers"]["default"] == [{"key": "8080", "value": "port"}]
    assert params["big"]["default"] == 0
    # Values not of their definition's kind are ignored; others stay. A `kv`
    # value's rows are coerced like a default's first (third sweep).
    assert b["param_values"] == {"headers": [{"key": "1", "value": "a"}], "wait": 7, "gone": "kept"}
    assert manifest.read_text(encoding="utf-8") == before  # nothing written at load

    json.dumps(store.auto_json(b), allow_nan=False)  # the §19 payload serializes

    _, errors = _validate(extra=yaml.safe_dump({"params": b["versions"][1]["params"]},
                                               sort_keys=False))
    assert not [e for e in errors if "min" in e or "default" in e]

    draft = _draft_from(b["versions"][1])
    draft["steps"][0]["code"] += "# edited\n"
    r = client.post(f"/automations/{b['id']}/versions", json={"draft": draft})
    assert r.status_code == 200, r.text
    assert b["current_version"] == 2


def test_kind_ok_requires_finite_numbers_and_text_kv_rows():
    from autowright.storage import _kind_ok

    assert not _kind_ok("number", float("inf"))
    assert not _kind_ok("number", float("nan"))
    assert _kind_ok("number", 1.5)
    assert _kind_ok("kv", [{"key": "a", "value": "b"}])
    assert not _kind_ok("kv", [{"key": 8080, "value": "port"}])
    assert not _kind_ok("kv", [{"key": "a", "value": None}])
    assert not _kind_ok("kv", [{"key": None, "value": "x"}])


def test_a_bad_kv_default_resolves_to_an_empty_list():
    from autowright.storage import param_default

    assert param_default({"kind": "kv", "default": [{"key": 1, "value": 2}]}) == []
    assert param_default({"kind": "number", "default": float("nan")}) == 0


# ---------- C §5: retention under a clock set ahead ----------

def test_retention_with_the_clock_a_year_ahead_deletes_nothing_newer_than_the_window(store, monkeypatch):
    a = store.create_automation(make_version(), "History", None)
    records = {}
    for days_ago in (0, 10, 20, 45):
        h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
        h["started_at"] = (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")
        store.update_execution(h)
        records[days_ago] = h["id"]
    # Loaded from disk, so the records feed the §5 session clock's base
    # (third sweep: records created this session never do).
    store.load_all()
    store.settings["days"] = 30

    real = datetime

    class Ahead(datetime):
        @classmethod
        def now(cls, tz=None):
            return real.now(tz) + timedelta(days=365)

    monkeypatch.setattr(storage_mod, "datetime", Ahead)
    # Anchored on the newest real record: only the 45-day-old one is past the
    # window, exactly what the plain rule removes under a correct clock.
    assert store.retention_cleanup() == 1
    assert records[45] not in store.execs
    assert all(records[n] in store.execs for n in (0, 10, 20))


def test_retention_with_no_records_uses_the_wall_clock(store):
    store.settings["days"] = 30
    assert store.retention_cleanup() == 0


# ---------- D §6.3: a symlink is copied as a link ----------

def _memory_with_links(store, a) -> Path:
    mem = store.auto_dir(a) / "memory"
    (mem / "venv" / "bin").mkdir(parents=True)
    (mem / "notes.txt").write_text("hello\n", encoding="utf-8")
    os.symlink("/nonexistent/autowright-test/python3", mem / "venv" / "bin" / "python3")
    return mem


def test_a_dangling_symlink_snapshots_and_restores_as_a_link(store):
    a = store.create_automation(make_version(), "Linked", None)
    mem = _memory_with_links(store, a)
    meta = store.snapshot_memory(a, "manual")
    assert meta is not None
    copied = store.snapshots_dir(a) / meta["id"] / "memory" / "venv" / "bin" / "python3"
    assert copied.is_symlink()
    assert os.readlink(copied) == "/nonexistent/autowright-test/python3"

    (mem / "notes.txt").write_text("changed\n", encoding="utf-8")
    store.restore_snapshot(a, meta["id"])
    restored = mem / "venv" / "bin" / "python3"
    assert restored.is_symlink()
    assert (mem / "notes.txt").read_text(encoding="utf-8") == "hello\n"


def test_duplicate_memory_copy_keeps_a_link_to_a_directory_as_a_link(store, tmp_path):
    source = store.create_automation(make_version(), "Source", None)
    mem = _memory_with_links(store, source)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"x" * 10)
    os.symlink(outside, mem / "home")
    copy = store.duplicate_automation(source["id"], name="Copy")
    store.copy_memory_into(copy, source["id"])
    target = store.auto_dir(copy) / "memory"
    assert (target / "home").is_symlink()
    assert (target / "venv" / "bin" / "python3").is_symlink()


# ---------- E §5: absence is only "not found" ----------

def test_absent_answers_true_only_for_not_found(tmp_path):
    from autowright.storage import _absent

    assert _absent(tmp_path / "missing")
    (tmp_path / "file").write_text("x")
    assert not _absent(tmp_path / "file")
    assert _absent(tmp_path / "file" / "below")  # NotADirectoryError


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores modes")
def test_an_unsearchable_automation_dir_is_not_reaped_at_startup(store):
    from autowright.storage import Store, _absent

    a = store.create_automation(make_version(), "Locked", None)
    d = store.auto_dir(a)
    d.chmod(0o000)
    try:
        assert not _absent(d / "automation.yaml")
        s2 = Store()
        s2.load_all(startup=True)
        s2.drain_reaper()
        assert a["id"] not in s2.autos
    finally:
        d.chmod(0o755)
    assert (d / "automation.yaml").exists()


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores modes")
def test_an_unsearchable_execution_dir_is_not_reaped(store):
    from autowright.storage import Store

    ed = store.executions_dir() / str(uuid.uuid4())
    ed.mkdir(parents=True)
    (ed / "execution.yaml").write_text("id: x\n", encoding="utf-8")
    ed.chmod(0o000)
    try:
        s2 = Store()
        s2.load_all(startup=True)
        s2.drain_reaper()
    finally:
        ed.chmod(0o755)
    assert (ed / "execution.yaml").exists()


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores modes")
def test_the_snapshot_sweep_skips_an_unsearchable_snapshot_dir(store):
    a = store.create_automation(make_version(), "Swept", None)
    (store.auto_dir(a) / "memory" / "f.txt").write_text("x", encoding="utf-8")
    first = store.snapshot_memory(a, "manual")
    locked = store.snapshots_dir(a) / first["id"]
    locked.chmod(0o000)
    try:
        store.snapshot_memory(a, "manual")
        store.drain_reaper()
    finally:
        locked.chmod(0o755)
    assert (locked / "snapshot.yaml").exists()


# ---------- T §5: a failed orphan set-aside is typed ----------

def test_a_failing_orphan_set_aside_raises_the_typed_error_with_the_path(store, monkeypatch):
    from autowright.storage import OrphanSetAsideError, Store

    a = store.create_automation(make_version(), "Stuck", "agent-1")
    store.save_new_version(a, make_version(note="v2"))
    versions = store.auto_dir(a) / "versions"
    shutil.copytree(versions / "v2", versions / "v3")
    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    real = Path.rename

    def rename(self, target):
        if self.name == "v3":
            raise PermissionError(13, "Permission denied")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(OrphanSetAsideError) as caught:
        s2.save_new_version(b, make_version(note="Next"))
    assert caught.value.path == versions / "v3"
    assert caught.value.reason == "Permission denied"
    assert str(versions / "v3") in str(caught.value)


# ---------- V §5: snapshot created_at in ISO form ----------

def test_a_datetime_snapshot_created_at_lists_in_iso_form(store):
    a = store.create_automation(make_version(), "Stamps", None)
    root = store.snapshots_dir(a)
    sid = str(uuid.uuid4())
    (root / sid).mkdir(parents=True)
    (root / sid / "snapshot.yaml").write_text(
        f"id: {sid}\nreason: manual\ncreated_at: 2026-10-31 23:00:00\n", encoding="utf-8")
    listed = store.list_snapshots(a)
    assert listed[0]["created_at"] == "2026-10-31T23:00:00"
    assert "T" in listed[0]["created_at"]


# ---------- F5 §5: the execution index check degrades ----------

def test_an_unlistable_executions_dir_never_breaks_load_all(store, monkeypatch):
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Indexed", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    executions = store.executions_dir()
    real = Path.iterdir

    def iterdir(self):
        if self == executions:
            raise PermissionError(13, "Permission denied")
        return real(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    s2 = Store()
    s2.load_all()
    assert h["id"] in s2.execs  # the index rows stand


def test_a_failing_ghost_row_delete_never_breaks_load_all(store, monkeypatch):
    from autowright.execdb import ExecDB
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Ghost", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    store.close_exec_db()
    shutil.rmtree(store.exec_dir(h["id"]))

    def failing(self, execution_id):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(ExecDB, "delete", failing)
    s2 = Store()
    s2.load_all()
    assert h["id"] in s2.execs  # the row stays; the next boot retries


# ---------- F6 §5: a failed version write leaves no partial vN ----------

def test_a_failed_version_write_leaves_no_folder(store, monkeypatch):
    a = store.create_automation(make_version(), "Full disk", None)
    real = store._write_version_folder

    def partial(vd, ver):
        vd.mkdir(parents=True, exist_ok=True)
        (vd / "01-say.py").write_text("partial", encoding="utf-8")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(store, "_write_version_folder", partial)
    versions = store.auto_dir(a) / "versions"
    with pytest.raises(OSError):
        store.save_new_version(a, make_version(note="v2"))
    assert not (versions / "v2").exists()
    with pytest.raises(OSError):
        store.restore_version(a, 1)
    assert not (versions / "v2").exists()
    assert a["current_version"] == 1
    monkeypatch.setattr(store, "_write_version_folder", real)
    assert store.save_new_version(a, make_version(note="v2")) == 2


# ---------- J §6.3: the execution-start counter ----------

def test_create_and_promote_bump_the_start_counter(store):
    a = store.create_automation(make_version(), "Counter", None)
    assert a["_starts"] == 0
    assert store.start_token(a) == 0
    queued = store.create_execution(a, "version", 1, "manual", [], status="queued")
    assert a["_starts"] == 0
    live = store.create_execution(a, "version", 1, "manual", [])
    assert a["_starts"] == 1
    assert store.start_token(a) is None  # something is live
    live["status"] = "succeeded"
    store.update_execution(live)
    assert store.start_token(a) == 1
    store.promote_execution(queued, [])
    # Promote bumps, and update_execution counts the record entering
    # `executing` again — harmless, the token is compared for equality only.
    promoted = a["_starts"]
    assert promoted > 1
    store.create_execution(a, "test", None, "manual", [])
    assert a["_starts"] == promoted  # a §11 test never counts


def test_a_loaded_automation_starts_its_counter_at_zero(store):
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Loaded", None)
    s2 = Store()
    s2.load_all()
    assert s2.autos[a["id"]]["_starts"] == 0


def _run_during_copy(store, monkeypatch, owner, when):
    """A copytree whose `when(dst)` copy lets a run of `owner` start and
    finish inside it — both 'nothing live' checks would pass."""
    real = shutil.copytree

    def copytree(src, dst, *args, **kwargs):
        out = real(src, dst, *args, **kwargs)
        if when(Path(dst)):
            h = store.create_execution(owner, "version", 1, "cron", [])
            (store.auto_dir(owner) / "memory" / "run.txt").write_text("run", encoding="utf-8")
            h["status"] = "succeeded"
            store.update_execution(h)
        return out

    monkeypatch.setattr(storage_mod.shutil, "copytree", copytree)


def test_restore_abandons_when_a_run_started_and_finished_during_the_copy(store, monkeypatch):
    from autowright.storage import MEMORY_SWAP_TMP, LiveExecutionError

    a = store.create_automation(make_version(), "Raced", None)
    mem = store.auto_dir(a) / "memory"
    (mem / "f.txt").write_text("old", encoding="utf-8")
    meta = store.snapshot_memory(a, "manual")
    (mem / "f.txt").write_text("new", encoding="utf-8")
    _run_during_copy(store, monkeypatch, a, lambda dst: dst.name == MEMORY_SWAP_TMP)
    with pytest.raises(LiveExecutionError):
        store.restore_snapshot(a, meta["id"])
    assert (mem / "f.txt").read_text(encoding="utf-8") == "new"
    assert (mem / "run.txt").exists()  # the run's writes were not swapped away
    assert not (mem.parent / MEMORY_SWAP_TMP).exists()


def test_restore_refuses_at_the_first_gate_while_live(store):
    from autowright.storage import LiveExecutionError

    a = store.create_automation(make_version(), "Busy", None)
    (store.auto_dir(a) / "memory" / "f.txt").write_text("x", encoding="utf-8")
    meta = store.snapshot_memory(a, "manual")
    store.create_execution(a, "version", 1, "manual", [])
    with pytest.raises(LiveExecutionError):
        store.restore_snapshot(a, meta["id"])


def test_duplicate_copy_abandons_when_a_source_run_started_and_finished(store, monkeypatch):
    from autowright.storage import MEMORY_SWAP_TMP, LiveExecutionError

    source = store.create_automation(make_version(), "Source", None)
    (store.auto_dir(source) / "memory" / "f.txt").write_text("x", encoding="utf-8")
    copy = store.duplicate_automation(source["id"], name="Copy")
    _run_during_copy(store, monkeypatch, source, lambda dst: dst.name == MEMORY_SWAP_TMP)
    with pytest.raises(LiveExecutionError):
        store.copy_memory_into(copy, source["id"])
    target = store.auto_dir(copy) / "memory"
    assert not (target / "f.txt").exists()
    assert not (target.parent / MEMORY_SWAP_TMP).exists()


# ---------- K4: the memory-size walk is single-flight ----------

def test_invalidations_during_a_walk_start_one_rewalk_not_more_threads(store, monkeypatch):
    a = store.create_automation(make_version(), "Walked", None)
    (store.auto_dir(a) / "memory" / "seen.yaml").write_text("v: 1\n", newline="")
    real_stats = storage_mod.iter_memory_file_stats
    release = threading.Event()
    entered = threading.Event()
    calls = []

    def gated(d):
        calls.append(d)
        if len(calls) == 1:
            entered.set()
            release.wait(5)
        return real_stats(d)

    started = []
    real_thread = threading.Thread

    def recording(*args, **kwargs):
        if kwargs.get("name") == "memorystats":
            started.append(kwargs)
        return real_thread(*args, **kwargs)

    monkeypatch.setattr(storage_mod, "iter_memory_file_stats", gated)
    monkeypatch.setattr(threading, "Thread", recording)
    try:
        store.memory_stats(a)
        assert entered.wait(5)
        store.invalidate_memory_stats(a)
        store.invalidate_memory_stats(a)
        store.memory_stats(a)
        assert len(started) == 1
        assert a["_memory_stats_rewalk"] is True
        release.set()
        deadline = time.monotonic() + 5
        while a.get("_memory_stats_walking") and time.monotonic() < deadline:
            time.sleep(0.01)
        assert a["_memory_stats_walking"] is False
        assert len(calls) == 2  # one walk plus one re-walk
        assert a["_memory_stats_rewalk"] is False
        assert a["_memory_stats"][2]["size"] == "5 B"
    finally:
        release.set()


# ---------- §21 2026-10-31 (c): a stored cron step on a bare number ----------

def test_old_shape_bare_number_step_and_never_firing_cron_load_enabled(store):
    from autowright import triggers as triggerlib
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Old cron", None)
    top = store.auto_dir(a) / "automation.yaml"

    def triggers(data):
        # Shapes a v0.13.x build stored and kept enabled.
        data["triggers"] = [
            {"id": "step", "kind": "cron", "enabled": True, "expression": "5/15 * * * *",
             "source": "user"},
            {"id": "never", "kind": "cron", "enabled": True, "expression": "0 0 30 2 *",
             "source": "user"},
        ]
    _rewrite(top, triggers)
    before = top.read_text(encoding="utf-8")

    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    by_id = {t["id"]: t for t in b["triggers"]}
    assert set(by_id) == {"step", "never"}
    assert by_id["step"]["enabled"] is True and by_id["never"]["enabled"] is True
    assert by_id["step"]["expression"] == "5-59/15 * * * *"
    assert by_id["never"]["expression"] == "0 0 30 2 *"
    start = datetime(2026, 10, 31, 10, 0)
    assert triggerlib.cron_next(by_id["step"]["expression"], start) == datetime(2026, 10, 31, 10, 5)
    fired, at = [], start
    for _ in range(4):
        at = triggerlib.cron_next(by_id["step"]["expression"], at)
        fired.append(at.minute)
    assert fired == [5, 20, 35, 50]
    assert top.read_text(encoding="utf-8") == before  # nothing written at load

    s2.patch_automation(b, {"name": "Old cron renamed"})
    stored = yaml.safe_load(top.read_text(encoding="utf-8"))["triggers"]
    assert {t["id"]: t["expression"] for t in stored} == {
        "step": "5-59/15 * * * *", "never": "0 0 30 2 *"}


def test_the_strict_trigger_check_still_refuses_both_legacy_forms():
    from autowright import triggers as triggerlib

    step = {"kind": "cron", "enabled": True, "expression": "5/15 * * * *", "source": "user"}
    never = {"kind": "cron", "enabled": True, "expression": "0 0 30 2 *", "source": "user"}
    assert triggerlib.validate_trigger(step) is not None
    assert triggerlib.validate_trigger(never) is not None
    assert triggerlib.validate_trigger(step, at_load=True) is not None  # only rewritten passes
    assert triggerlib.validate_trigger(never, at_load=True) is None


# ---------- §6 2026-10-31: a run baseline far ahead falls back to created_at ----------

def _far_ahead_run(store, triggers):
    from autowright import triggers as triggerlib
    from autowright.timefmt import now_iso

    created = datetime.now().astimezone() - timedelta(days=3)
    created_iso = created.isoformat(timespec="microseconds")
    a = store.create_automation(make_version(), f"Ahead {uuid.uuid4().hex[:6]}", None,
                                triggers=triggers)
    a["created_at"] = created_iso
    for t in a["triggers"]:
        # The pre-field shape: no enable stamp, so created_at is the only
        # other baseline the trigger can count from.
        t.pop(triggerlib.ENABLED_AT, None)
    h = store.create_execution(a, "version", 1, "manual", [])
    h["started_at"] = "2027-03-01T09:00:00.000000+00:00"
    h["status"] = "succeeded"
    h["finished_at"] = now_iso()
    store.update_execution(h)
    assert a["_last_exec_at"] == "2027-03-01T09:00:00.000000+00:00"
    return a, created


def test_a_far_ahead_run_still_reports_a_daily_cron_overdue(store):
    a, _ = _far_ahead_run(store, [{"id": "daily", "kind": "cron", "enabled": True,
                                   "expression": "0 9 * * *", "source": "user"}])
    cur = a["versions"][a["current_version"]]
    assert any(p["kind"] == "overdue" for p in store.problems_json(a, cur))


def test_a_far_ahead_run_never_pushes_the_interval_countdown_out(store):
    a, created = _far_ahead_run(store, [{"id": "every", "kind": "interval", "enabled": True,
                                         "every": "PT6H", "source": "user"}])
    next_ms = store.auto_json(a)["nextAtMs"]
    six_hours_ms = 6 * 3600 * 1000
    now_ms = datetime.now().timestamp() * 1000
    assert now_ms < next_ms <= now_ms + six_hours_ms
    # On the six-hour grid anchored at created_at.
    offset = (next_ms - created.timestamp() * 1000) % six_hours_ms
    assert min(offset, six_hours_ms - offset) < 5  # millisecond rounding


# ---------- §5 2026-10-31: a `kv` default row with a null value ----------

def test_a_null_kv_value_reads_as_empty_text():
    rows = storage_mod.normalize_param_definitions([
        {"name": "headers", "kind": "kv", "default": [{"key": "k", "value": None},
                                                      {"key": "x", "value": "y"}]}])
    assert rows[0]["default"] == [{"key": "k", "value": ""}, {"key": "x", "value": "y"}]


def test_a_null_kv_key_still_drops_the_row():
    rows = storage_mod.normalize_param_definitions([
        {"name": "headers", "kind": "kv", "default": [{"key": None, "value": "v"},
                                                      {"key": "k", "value": None}]}])
    assert rows[0]["default"] == [{"key": "k", "value": ""}]


# ---------- §6.3 2026-10-31: an in-place retry counts as a start ----------

def test_an_in_place_retry_moves_the_start_token(store):
    a = store.create_automation(make_version(), "Retry counter", None)
    h = store.create_execution(a, "version", 1, "manual", [])
    h["status"] = "failed"
    store.update_execution(h)
    before = store.start_token(a)
    assert before is not None
    h["status"] = "executing"
    store.update_execution(h)
    assert store.start_token(a) is None  # live again
    h["status"] = "failed"
    store.update_execution(h)
    assert store.start_token(a) not in (None, before)
