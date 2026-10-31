"""Engine and scheduling audit regressions (2026-10-30 second sweep).

§6.3 the pre-version decision answers from an existing snapshot before it reads
any record; §6 the tick's drain is guarded, so a drain error never silences the
automation's triggers; §6 queue promotion keeps the head waiting only for the
engine's own `AdmissionRefused` refusals (and CapacityError, OSError); §7 a
retry whose version is gone raises VersionNotFound; §6 a far-future enable
stamp never suppresses §4.1 overdue.
"""
from datetime import datetime, timedelta, timezone

import pytest
from conftest import make_version, wait_done
from test_audit_1028_scheduler import _Clock, new_york_tz  # noqa: F401 - fixture


# ---------- E1: §6.3 an existing snapshot answers before any record is read ----------

def test_existing_pre_version_snapshot_skips_the_record_scan(store, monkeypatch):
    from autowright.engine import Engine
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Scan", None)
    version = a["current_version"]
    for _ in range(5):  # failing before step 1: zero attempts each
        steps = [{"name": "Say hello", "file": "01-say.py", "status": "skipped",
                  "duration_ms": None, "attempts": []}]
        h = store.create_execution(a, "version", version, "cron", steps)
        h["status"] = "failed"
        h["finished_at"] = h["started_at"]
        store.update_execution(h)

    fresh = Store()
    fresh.load_all()
    reads = []
    real_full = fresh.exec_full
    monkeypatch.setattr(fresh, "exec_full", lambda eid: reads.append(eid) or real_full(eid))
    monkeypatch.setattr(fresh, "pre_version_snapshot_exists", lambda auto, label: True)
    engine = Engine(fresh)
    assert engine._needs_pre_version(fresh.autos[a["id"]], "version", version) is False
    assert reads == []

    # Without the snapshot the records still decide: none started an attempt.
    monkeypatch.setattr(fresh, "pre_version_snapshot_exists", lambda auto, label: False)
    assert engine._needs_pre_version(fresh.autos[a["id"]], "version", version) is True
    assert len(reads) == 5


# ---------- E2: §6 a drain error never silences the tick's triggers ----------

def test_drain_error_still_fires_the_due_trigger(store, monkeypatch):
    from autowright import scheduler as sched_mod
    from autowright.engine import Engine
    from autowright.scheduler import Scheduler

    from autowright.firing import queue_manual

    clock = _Clock(datetime(2026, 7, 10, 7, 59))
    engine = Engine(store)
    sched = Scheduler(store, engine, clock=clock,
                      utc_clock=lambda: clock().astimezone(timezone.utc))
    a = store.create_automation(make_version(), "Guarded", None, triggers=[
        {"id": "c1", "kind": "cron", "enabled": True, "expression": "0 8 * * *"}])
    for t in a["triggers"]:
        t.pop("enabledAt", None)  # the real-time stamp would postdate the fake clock

    def broken_drain(store, engine, automation_id):
        raise OSError(5, "Input/output error")

    fires = []
    monkeypatch.setattr(sched_mod, "drain_queue", broken_drain)
    monkeypatch.setattr(sched_mod, "fire_trigger",
                        lambda store, engine, a, t, **_: fires.append(t["id"]) or True)
    sched._tick()  # baselines
    # Something queued, so the tick drains this automation (and the drain raises).
    a["max_queued"] = 3
    a["_live"] = {"blocking"}
    queue_manual(store, engine, a, "manual")
    a["_live"] = set()
    clock.now = datetime(2026, 7, 10, 8, 1)
    sched._tick()
    assert fires == ["c1"]


# ---------- E3: §6 only the engine's refusals keep the head waiting ----------

def test_runtime_error_that_is_not_a_refusal_ends_the_head(store, monkeypatch):
    from autowright.engine import Engine
    from autowright.firing import drain_queue, queue_manual

    engine = Engine(store)
    a = store.create_automation(make_version(), "NotARefusal", None)
    a["max_queued"] = 5
    a["_live"] = {"blocking"}
    head, _queued = queue_manual(store, engine, a, "manual")
    second, _queued = queue_manual(store, engine, a, "manual")
    real = store.merged_params
    calls = []

    def broken_once(auto, ver):
        calls.append(1)
        if len(calls) == 1:
            raise RecursionError("maximum recursion depth exceeded")
        return real(auto, ver)

    monkeypatch.setattr(store, "merged_params", broken_once)
    a["_live"] = set()
    drain_queue(store, engine, a["id"])
    assert store.execs[head["id"]]["status"] == "skipped"
    assert store.execs[head["id"]]["note"] == "couldn't be started"
    wait_done(engine, second["id"])
    assert store.execs[second["id"]]["status"] == "succeeded"


def test_admission_refusal_keeps_the_head_queued(store, monkeypatch):
    from autowright.engine import AdmissionRefused, Engine
    from autowright.firing import drain_queue, queue_manual

    engine = Engine(store)
    a = store.create_automation(make_version(), "Refused", None)
    a["max_queued"] = 5
    a["_live"] = {"blocking"}
    head, _queued = queue_manual(store, engine, a, "manual")

    def refused(auto, ver):
        raise AdmissionRefused("this automation is being deleted")

    monkeypatch.setattr(store, "merged_params", refused)
    a["_live"] = set()
    drain_queue(store, engine, a["id"])
    assert store.execs[head["id"]]["status"] == "queued"


def test_engine_refusals_raise_admission_refused(store):
    from autowright.engine import AdmissionRefused, Engine

    engine = Engine(store)
    a = store.create_automation(make_version(), "Deleting", None)
    a["_deleting"] = True
    with pytest.raises(AdmissionRefused):
        engine.start(a, "manual")
    failed = store.create_execution(a, "version", a["current_version"], "manual", [])
    failed["status"] = "failed"
    store.update_execution(failed)
    with pytest.raises(AdmissionRefused):
        engine.retry(a, failed)
    a["_deleting"] = False
    engine._stopping = True
    with pytest.raises(AdmissionRefused):
        engine.start(a, "manual")


# ---------- E5: §7 a retry whose version is gone raises VersionNotFound ----------

def test_retry_of_a_missing_version_raises_version_not_found(store):
    from autowright.engine import Engine, VersionNotFound

    engine = Engine(store)
    a = store.create_automation(make_version(), "Gone", None)
    failed = store.create_execution(a, "version", 99, "manual", [])
    failed["status"] = "failed"
    store.update_execution(failed)
    with pytest.raises(VersionNotFound):
        engine.retry(a, failed)


# ---------- E6: §6 a far-future enable stamp never suppresses overdue ----------

def test_enable_stamp_an_hour_ahead_does_not_suppress_overdue():
    from autowright import triggers as triggerlib

    now = datetime(2026, 7, 10, 12, 0)
    baseline = datetime(2026, 7, 1, 0, 0)
    trigger = {"id": "c1", "kind": "cron", "enabled": True, "expression": "0 8 * * *",
               "enabledAt": (now + timedelta(hours=1)).astimezone().isoformat()}
    assert triggerlib.is_overdue([trigger], baseline, now) is True
    # A stamp within the hold still counts: enabled just now, nothing missed yet.
    trigger["enabledAt"] = (now + timedelta(minutes=10)).astimezone().isoformat()
    assert triggerlib.is_overdue([trigger], baseline, now) is False
