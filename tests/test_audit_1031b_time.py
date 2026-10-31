"""Trigger/scheduler time audit regressions (2026-10-31, second sweep).

§4.3 the scheduler baseline is an instant, re-read in the current local zone
each tick (a system-timezone change neither re-fires nor skips); §6 a run
baseline more than 15 min ahead is ignored by the interval anchor and the
overdue audit; `time.at` and `timezone` store canonical; the cron dialect
rejects `5/15` and never-firing expressions; a `*`-led field is unrestricted
under the Vixie rule.
"""
import logging
import os
import time as time_mod
from datetime import UTC, datetime, timedelta

import pytest
from conftest import make_version

from autowright import triggers as triggerlib


class _Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def _set_zone(name):
    os.environ["TZ"] = name
    time_mod.tzset()


@pytest.fixture
def zone():
    """Switch the process zone mid-test (§4.3 occurrences compare as instants
    via the process zone); restores the original zone afterwards."""
    if not hasattr(time_mod, "tzset"):
        pytest.skip("POSIX-only tzset")
    old = os.environ.get("TZ")
    try:
        yield _set_zone
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time_mod.tzset()


def _scheduler(store, local, utc, monkeypatch):
    from autowright import scheduler as sched_mod
    from autowright.engine import Engine
    from autowright.scheduler import Scheduler

    fires = []
    monkeypatch.setattr(sched_mod, "fire_trigger",
                        lambda store, engine, a, t, **_: fires.append(t["id"]) or True)
    return Scheduler(store, Engine(store), clock=local, utc_clock=utc), fires


def _automation(store, name, triggers):
    a = store.create_automation(make_version(), name, None, triggers=triggers)
    for t in a["triggers"]:
        t.pop("enabledAt", None)  # the real-time enable stamp would postdate the fake clock
    return a


# ---------- H (T2): a system-timezone change re-reads the baseline instant ----------

@pytest.mark.parametrize("run_if_missed", [True, False])
def test_eastward_timezone_change_does_not_fire_an_occurrence_before_the_baseline(
        store, monkeypatch, zone, caplog, run_if_missed):
    zone("America/Los_Angeles")
    local = _Clock(datetime(2026, 10, 31, 10, 0))  # 17:00Z
    utc = _Clock(datetime(2026, 10, 31, 17, 0, tzinfo=UTC))
    sched, fires = _scheduler(store, local, utc, monkeypatch)
    trigger = {"id": "t1", "kind": "cron", "enabled": True, "expression": "0 20 * * *"}
    if not run_if_missed:
        trigger["runIfMissed"] = False
    a = _automation(store, "Evening", [trigger])
    sched._tick()  # baseline LA 10:00
    zone("Asia/Tokyo")
    utc.now = datetime(2026, 10, 31, 18, 0, tzinfo=UTC)
    local.now = datetime(2026, 11, 1, 3, 0)  # the same instant in Tokyo
    caplog.set_level(logging.WARNING, logger="autowright.scheduler")
    sched._tick()
    # 20:00 JST Oct 31 = 11:00Z precedes the 17:00Z baseline instant
    assert fires == []
    assert [h for h in store.execs.values() if h["automation_id"] == a["id"]] == []
    # the 17:00Z baseline instant re-read in Tokyo
    assert sched._baseline[(a["id"], "t1")] == datetime(2026, 11, 1, 2, 0)
    assert sum("timezone change" in r.message for r in caplog.records) == 1
    sched._tick()
    assert sum("timezone change" in r.message for r in caplog.records) == 1  # once per change


def test_westward_timezone_change_fires_the_next_occurrence_after_the_baseline(
        store, monkeypatch, zone):
    zone("Asia/Tokyo")
    local = _Clock(datetime(2026, 10, 31, 10, 0))  # 01:00Z
    utc = _Clock(datetime(2026, 10, 31, 1, 0, tzinfo=UTC))
    sched, fires = _scheduler(store, local, utc, monkeypatch)
    _automation(store, "Morning", [
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "0 9 * * *"}])
    sched._tick()  # baseline Tokyo 10:00
    zone("America/Los_Angeles")
    utc.now = datetime(2026, 10, 31, 16, 30, tzinfo=UTC)
    local.now = datetime(2026, 10, 31, 9, 30)  # PDT
    sched._tick()
    assert fires == ["t1"]  # the 09:00 LA occurrence (16:00Z) fires


# ---------- I (T3): a run baseline far ahead is ignored ----------

NOW = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)
FUTURE_RECORD = datetime(2027, 3, 1, 10, 0, tzinfo=UTC)


def test_interval_instant_ignores_a_run_baseline_far_ahead():
    t = {"kind": "interval", "enabled": True, "every": "PT6H"}
    assert triggerlib.interval_instant(t, NOW, FUTURE_RECORD, now=NOW) == NOW + timedelta(hours=6)


def test_interval_anchor_keeps_a_run_baseline_within_the_hold():
    t = {"kind": "interval", "enabled": True, "every": "PT6H"}
    near = NOW + timedelta(minutes=10)
    assert triggerlib.interval_anchor(t, near, NOW, now=NOW) == near


@pytest.mark.parametrize("trigger", [
    {"kind": "interval", "enabled": True, "every": "PT6H"},
    {"kind": "cron", "enabled": True, "expression": "0 * * * *"},
])
def test_is_overdue_ignores_a_run_baseline_far_ahead(trigger):
    stamped = {**trigger, "enabledAt": "2026-10-30T00:00:00+00:00"}
    future_naive = FUTURE_RECORD.astimezone().replace(tzinfo=None)
    now_naive = NOW.astimezone().replace(tzinfo=None)
    assert triggerlib.is_overdue([stamped], future_naive, now_naive,
                                 interval_baseline=FUTURE_RECORD) is True
    # neither a credible baseline nor a stamp: nothing to count from
    assert triggerlib.is_overdue([trigger], future_naive, now_naive,
                                 interval_baseline=FUTURE_RECORD) is False


# ---------- Z (B4): `time.at` stored canonical ----------

@pytest.mark.parametrize("sent,stored", [
    ("20361120T0900", "2036-11-20T09:00"),
    ("2036-07-20T15:00:15", "2036-07-20T15:00:15"),
    ("2036-07-20T15:00:00", "2036-07-20T15:00"),
])
def test_time_at_stored_canonical(sent, stored):
    out, err = triggerlib.normalize_triggers([{"kind": "time", "at": sent}])
    assert err is None
    assert out[0]["at"] == stored


# ---------- AA (B5): timezone matched case-insensitively, stored canonical ----------

def test_timezone_matched_case_insensitively_and_stored_canonical():
    assert triggerlib.timezone_error("europe/berlin") is None
    assert triggerlib.canonical_timezone("europe/berlin") == "Europe/Berlin"
    out, err = triggerlib.normalize_triggers([
        {"kind": "cron", "expression": "0 9 * * *", "source": "user", "timezone": "europe/berlin"},
        {"kind": "time", "at": "2036-07-20T15:00", "timezone": "ASIA/TOKYO"}])
    assert err is None
    assert [t["timezone"] for t in out] == ["Europe/Berlin", "Asia/Tokyo"]


def test_unknown_timezone_still_errors():
    assert "unknown timezone" in triggerlib.timezone_error("Nowhere/City")


# ---------- AB (B6): `5/15` and never-firing expressions rejected ----------

def _cron(expression):
    return {"kind": "cron", "expression": expression, "source": "user"}


def test_step_on_a_bare_number_is_rejected():
    err = triggerlib.validate_trigger(_cron("5/15 * * * *"))
    assert err == "minute: a step applies to `*` or a range (`*/n`, `a-b/n`)"
    out, err = triggerlib.normalize_triggers([_cron("5/15 * * * *")])
    assert out == [] and "a step applies" in err
    assert triggerlib.validate_trigger(_cron("*/15 * * * *")) is None
    assert triggerlib.validate_trigger(_cron("5-50/15 * * * *")) is None


def test_never_firing_expression_is_rejected():
    assert "never fires" in triggerlib.validate_trigger(_cron("0 0 30 2 *"))


def test_february_29_validates():
    # the 5-year search window always holds a Feb 29 for the next decades
    assert triggerlib.validate_trigger(_cron("0 0 29 2 *")) is None


# ---------- AD (T5): a `*`-led field is unrestricted (Vixie) ----------

def test_star_step_day_of_month_ands_with_day_of_week():
    expression = "0 9 */2 * 1"
    assert triggerlib.cron_next(expression, datetime(2026, 11, 2, 0, 0)) == datetime(2026, 11, 9, 9, 0)
    # Tue Nov 3 is an odd day but not a Monday — no occurrence
    nxt = triggerlib.cron_next(expression, datetime(2026, 11, 2, 23, 59))
    assert nxt == datetime(2026, 11, 9, 9, 0)


def test_restricted_day_of_month_still_ors_with_day_of_week():
    # `1-31/2` does not start with `*`: the classic OR rule applies
    assert triggerlib.cron_next("0 9 1-31/2 * 1", datetime(2026, 11, 2, 23, 59)) == \
        datetime(2026, 11, 3, 9, 0)


# ---------- §21 2026-10-31 (c): the load-time cron rewrite ----------

def test_rewrite_legacy_cron_rewrites_only_a_step_on_a_bare_number():
    rw = triggerlib.rewrite_legacy_cron
    assert rw("5/15 * * * *") == "5-59/15 * * * *"
    assert rw("0 3/6 * * *") == "0 3-23/6 * * *"
    assert rw("0 0 2/5 * *") == "0 0 2-31/5 * *"
    assert rw("0 0 1 2/3 *") == "0 0 1 2-12/3 *"
    assert rw("0 0 * * 1/2") == "0 0 * * 1-6/2"
    assert rw("1,5/15,20-30/5 * * * *") == "1,5-59/15,20-30/5 * * * *"
    for untouched in ("*/15 * * * *", "0-30/10 9 * * 1-5", "0 9 * * *", "5/15 * *", "  0 9 * * * "):
        assert rw(untouched) == untouched
