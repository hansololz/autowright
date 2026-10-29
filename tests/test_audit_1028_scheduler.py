"""Scheduler audit regressions (2026-10-28).

§6 missed executions: a zone-less cron in the repeated fall-back hour honours
`fold` against a second-pass baseline; "last fired" is the baseline the firing
set; the last-fired hold on a backward step is bounded to 15 min; a stale first
due trigger hands the firing to the next due one, and a tick with nothing
attempted still writes the drop record; `time.tzset()` every tick off macOS.
§6 Discord MESSAGE_CREATE de-duplication across connections. §3 an undeletable
plist is an uninstall result line.
"""
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from conftest import make_version
from test_service import launchd_only, svc  # noqa: F401 - svc is a fixture

from autowright import triggers as triggerlib


class _Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture
def new_york_tz():
    """Pin the process zone to America/New_York (same as test_scheduler.py):
    §4.3 occurrences compare as instants via the process zone."""
    import os
    import time

    if not hasattr(time, "tzset"):
        pytest.skip("POSIX-only tzset")
    old = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


def _clocked_scheduler(store, clock, monkeypatch):
    from autowright import scheduler as sched_mod
    from autowright.engine import Engine
    from autowright.scheduler import Scheduler

    fires = []
    monkeypatch.setattr(sched_mod, "fire_trigger",
                        lambda store, engine, a, t, **_: fires.append(t["id"]) or True)
    sched = Scheduler(store, Engine(store), clock=clock,
                      utc_clock=lambda: clock().astimezone(timezone.utc))
    return sched, fires


def _automation(store, name, triggers):
    a = store.create_automation(make_version(), name, None, triggers=triggers)
    for t in a["triggers"]:
        t.pop("enabledAt", None)  # the real-time enable stamp would postdate the fake clock
    return a


CRON_0130 = {"id": "t1", "kind": "cron", "enabled": True, "expression": "30 1 * * *"}


# ---------- T1: §6 a zone-less cron honours fold in the repeated hour ----------

def test_second_pass_baseline_picks_the_second_pass_reading(new_york_tz):
    base = datetime(2026, 11, 1, 1, 15, fold=1)  # 01:15 EST, second pass
    nxt = triggerlib.trigger_next(CRON_0130, after=base)
    assert nxt == datetime(2026, 11, 1, 1, 30) and nxt.fold == 1
    assert nxt.astimezone().utcoffset() == timedelta(hours=-5)
    assert nxt.astimezone() > base.astimezone()  # not due at 01:15 fold=1


def test_first_pass_baseline_picks_the_first_pass_reading(new_york_tz):
    nxt = triggerlib.trigger_next(CRON_0130, after=datetime(2026, 11, 1, 0, 59))
    assert nxt == datetime(2026, 11, 1, 1, 30) and nxt.fold == 0
    assert nxt.astimezone().utcoffset() == timedelta(hours=-4)


def test_second_pass_baseline_past_both_readings_advances_a_day(new_york_tz):
    nxt = triggerlib.trigger_next(CRON_0130, after=datetime(2026, 11, 1, 1, 45, fold=1))
    assert nxt == datetime(2026, 11, 2, 1, 30)


def test_boot_in_the_second_pass_never_refires_every_tick(store, monkeypatch, new_york_tz):
    clock = _Clock(datetime(2026, 11, 1, 1, 15, fold=1))  # booted in the second pass
    sched, fires = _clocked_scheduler(store, clock, monkeypatch)
    _automation(store, "Nightly", [dict(CRON_0130)])
    sched._tick()
    for minute in (16, 17, 18):
        clock.now = datetime(2026, 11, 1, 1, minute, fold=1)
        sched._tick()
    assert fires == []  # the first-pass 01:30 is long past, never due
    clock.now = datetime(2026, 11, 1, 1, 31, fold=1)
    sched._tick()
    clock.now = datetime(2026, 11, 1, 1, 32, fold=1)
    sched._tick()
    assert fires == ["t1"]  # the second-pass 01:30 fires once


# ---------- T2: "last fired" is the baseline the firing set ----------

def test_wake_catch_up_stays_fired_through_a_post_wake_step_back(store, monkeypatch):
    clock = _Clock(datetime(2026, 7, 10, 8, 30))
    sched, fires = _clocked_scheduler(store, clock, monkeypatch)
    a = _automation(store, "Hourly", [
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "0 * * * *"}])
    sched._tick()  # baseline 08:30
    clock.now = datetime(2026, 7, 10, 12, 0, 5)  # slept through 09:00-12:00
    sched._tick()
    assert fires == ["t1"]  # one catch-up
    assert sched._last_fired[(a["id"], "t1")][0] == clock.now
    clock.now = datetime(2026, 7, 10, 11, 59, 58)  # NTP steps back
    sched._tick()
    clock.now = datetime(2026, 7, 10, 12, 0, 10)
    sched._tick()
    assert fires == ["t1"]  # 12:00 stays fired
    clock.now = datetime(2026, 7, 10, 13, 0, 5)
    sched._tick()
    assert fires == ["t1", "t1"]


# ---------- T4: the last-fired hold is bounded to 15 min ----------

def test_corrected_forward_glitch_resets_the_baseline(store, monkeypatch, caplog):
    clock = _Clock(datetime(2026, 7, 10, 8, 0))
    sched, fires = _clocked_scheduler(store, clock, monkeypatch)
    a = _automation(store, "Daily", [
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "0 9 * * *"}])
    sched._tick()  # baseline 08:00
    clock.now = datetime(2026, 7, 11, 9, 0, 5)  # the clock glitches a day ahead
    sched._tick()
    assert fires == ["t1"]
    caplog.set_level(logging.WARNING, logger="autowright.scheduler")
    clock.now = datetime(2026, 7, 10, 8, 0, 15)  # corrected back by a day
    sched._tick()
    assert sched._baseline[(a["id"], "t1")] == clock.now  # reset to now, not held
    assert "timezone change" not in caplog.text
    clock.now = datetime(2026, 7, 10, 9, 0, 5)
    sched._tick()
    assert fires == ["t1", "t1"]  # today's real 09:00 still fires


# ---------- T5: a stale first due trigger hands the firing on ----------

def test_stale_first_due_trigger_fires_the_next_one(store, monkeypatch):
    clock = _Clock(datetime(2026, 7, 10, 7, 59))
    sched, fires = _clocked_scheduler(store, clock, monkeypatch)
    a = _automation(store, "Pair", [
        {"id": "c1", "kind": "cron", "enabled": True, "expression": "0 8 * * *"},
        {"id": "c2", "kind": "cron", "enabled": True, "expression": "0 8 * * *"}])
    sched._tick()
    real_fire = sched._fire
    raced = []

    def racing_fire(auto, t):
        if not raced:
            raced.append(True)
            # the API disables c1 between the tick's snapshot and the fire
            next(x for x in auto["triggers"] if x["id"] == "c1")["enabled"] = False
        return real_fire(auto, t)

    monkeypatch.setattr(sched, "_fire", racing_fire)
    clock.now = datetime(2026, 7, 10, 8, 0, 10)
    sched._tick()
    assert fires == ["c2"]
    assert sched._last_fired[(a["id"], "c2")][0] == clock.now


def test_nothing_attempted_still_writes_the_drop_record(store, monkeypatch):
    from autowright.scheduler import drop_note

    clock = _Clock(datetime(2026, 7, 10, 7, 0))
    sched, fires = _clocked_scheduler(store, clock, monkeypatch)
    a = _automation(store, "Mixed", [
        {"id": "cron", "kind": "cron", "enabled": True, "expression": "0 8 * * *"},
        {"id": "once", "kind": "time", "enabled": True, "at": "2026-07-10T08:30",
         "runIfMissed": False}])
    sched._tick()  # baseline 07:00
    real_fire = sched._fire

    def racing_fire(auto, t):
        next(x for x in auto["triggers"] if x["id"] == "cron")["enabled"] = False
        return real_fire(auto, t)

    monkeypatch.setattr(sched, "_fire", racing_fire)
    clock.now = datetime(2026, 7, 10, 10, 0)  # slept through 08:00 and 08:30
    sched._tick()
    assert fires == []
    records = [h for h in store.execs.values()
               if h["automation_id"] == a["id"] and h["status"] == "skipped"]
    assert len(records) == 1
    assert records[0]["note"] == drop_note()
    assert records[0]["trigger"] == "time"
    assert [t["id"] for t in a["triggers"]] == ["cron"]  # the dropped one-shot is consumed


# ---------- T8: time.tzset() every tick off macOS ----------

@pytest.mark.parametrize("platform_name,expected", [("linux", 2), ("darwin", 0)])
def test_tick_reloads_the_zone_off_macos(store, monkeypatch, platform_name, expected):
    from autowright import scheduler as sched_mod

    clock = _Clock(datetime(2026, 7, 10, 7, 0))
    sched, _ = _clocked_scheduler(store, clock, monkeypatch)
    calls = []
    monkeypatch.setattr(sched_mod, "sys", SimpleNamespace(platform=platform_name))
    monkeypatch.setattr(sched_mod, "time", SimpleNamespace(tzset=lambda: calls.append(1)))
    sched._tick()
    sched._tick()
    assert len(calls) == expected


# ---------- T6: Discord MESSAGE_CREATE de-duplicated per automation ----------

def _discord_message(**over):
    d = {"channel_id": "42", "content": "deploy", "id": "m1", "guild_id": "g1",
         "author": {"id": "7", "username": "dave"}, "mentions": [],
         "timestamp": "2026-07-27T10:00:00+00:00"}
    d.update(over)
    return d


def _discord_trigger(trigger_id, secret):
    return {"id": trigger_id, "kind": "discord", "enabled": True,
            "channel": "42", "secret": secret}


def _recording_listeners(store, monkeypatch):
    from autowright import listeners as listeners_mod

    fired = []
    monkeypatch.setattr(listeners_mod, "fire_trigger",
                        lambda store, engine, a, t, **_: fired.append((a["id"], t["id"])))
    return listeners_mod.Listeners(store, engine=None), fired


def test_one_message_on_two_tokens_fires_once(store, monkeypatch):
    li, fired = _recording_listeners(store, monkeypatch)
    a = store.create_automation(make_version(), "Chat", None)
    a["triggers"] = [_discord_trigger("ta", "TOKEN_A"), _discord_trigger("tb", "TOKEN_B")]
    other = store.create_automation(make_version(), "Other", None)
    other["triggers"] = [_discord_trigger("tc", "TOKEN_B")]
    li.dispatch("TOKEN_A", _discord_message(), bot_id="bot9")
    li.dispatch("TOKEN_B", _discord_message(), bot_id="bot9")
    assert fired == [(a["id"], "ta"), (other["id"], "tc")]  # once per automation
    li.dispatch("TOKEN_B", _discord_message(id="m2"), bot_id="bot9")
    assert fired[2:] == [(a["id"], "tb"), (other["id"], "tc")]  # a new message fires


def test_seen_messages_are_a_bounded_lru(store, monkeypatch):
    from autowright import listeners as listeners_mod

    monkeypatch.setattr(listeners_mod, "DISCORD_SEEN_LIMIT", 2)
    li, fired = _recording_listeners(store, monkeypatch)
    a = store.create_automation(make_version(), "Chat", None)
    a["triggers"] = [_discord_trigger("ta", "TOKEN_A")]
    for message_id in ("m1", "m2", "m3"):
        li.dispatch("TOKEN_A", _discord_message(id=message_id), bot_id="bot9")
    assert len(li._discord_seen) == 2
    li.dispatch("TOKEN_A", _discord_message(id="m1"), bot_id="bot9")  # evicted: fires again
    li.dispatch("TOKEN_A", _discord_message(id="m3"), bot_id="bot9")  # still remembered
    assert len(fired) == 4


# ---------- T7: §3 an undeletable plist is a result line ----------

@launchd_only
def test_uninstall_reports_an_undeletable_plist(svc, monkeypatch):  # noqa: F811
    import pathlib

    svc.plist.parent.mkdir(parents=True, exist_ok=True)
    svc.plist.write_bytes(b"<plist/>")
    real_unlink = pathlib.Path.unlink

    def unlink(self, *args, **kwargs):
        if self == svc.plist:
            raise PermissionError(13, "Permission denied")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "unlink", unlink)
    out = svc.mod.uninstall()
    assert out.startswith(f"uninstall failed: couldn't delete {svc.plist} (")
    assert "Permission denied" in out
    assert svc.plist.exists()
