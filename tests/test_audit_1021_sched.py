"""Scheduler, trigger, listener, service, platform and draft-job audit
regressions (2026-10-21).

§6 the overdue notified mark is set only when a notification posts; §19 a
non-boolean `enabled` is a 422; §4.3 zoned occurrences compare as instants
(the fall-back fold kept) and a gap-shifted occurrence passes the §6
runIfMissed fresh check; §6 the iMessage watcher is created, ticked and
closed under the listener lock; §3 an unwritable LaunchAgents answers a
result line, the CLI shim single-quotes the interpreter, and the Windows
literal doubles every single-quote character; §19 a held draft outcome is
never dropped by the finished-record bound.
"""
import sys
import threading
import time
from datetime import datetime, timezone

import pytest
from conftest import make_version
from test_listeners import _pin_imessage_capability
from test_scheduler import (_Clock, _drop_records, _enable_stamps_before_the_fake_clocks,  # noqa: F401
                            _mk_clocked, _record_fires, _wait_for, new_york_tz)
from test_service import svc  # noqa: F401 - fixture


# ---------- §6 overdue: marked only when a notification posts ----------

def test_overdue_under_never_is_notified_once_the_setting_turns(store, monkeypatch):
    from autowright import notify

    clock = _Clock(datetime(2026, 7, 10, 9, 0))
    engine, sched = _mk_clocked(store, clock)
    posted = []
    monkeypatch.setattr(notify, "post_targeted",
                        lambda *args, **kwargs: posted.append((args, kwargs)))
    a = store.create_automation(make_version(), "Quiet Job", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "0 8 * * *",
         "source": "user"}])
    store.patch_automation(a, {"notifications": "never"})
    a["created_at"] = "2026-07-01T08:00:00"
    a["triggers"][0]["enabledAt"] = datetime(2026, 7, 1, 8, 0).astimezone().isoformat()

    sched._overdue_sweep([a], datetime(2026, 7, 10, 10, 1))
    sched._overdue_sweep([a], datetime(2026, 7, 10, 11, 2))
    assert posted == []
    assert a["id"] not in sched._overdue_notified

    store.patch_automation(a, {"notifications": "attention"})
    sched._overdue_sweep([a], datetime(2026, 7, 10, 12, 3))
    _wait_for(lambda: posted)
    assert len(posted) == 1
    assert a["id"] in sched._overdue_notified
    sched._overdue_sweep([a], datetime(2026, 7, 10, 13, 4))
    time.sleep(0.05)
    assert len(posted) == 1  # once per overdue stretch


# ---------- §19 a non-boolean `enabled` is a 422 ----------

@pytest.mark.parametrize("trigger", [
    {"kind": "cron", "expression": "0 9 * * *", "source": "user"},
    {"kind": "interval", "every": "PT1H", "source": "user"},
    {"kind": "app_start"},
])
@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_a_non_boolean_enabled_is_rejected(trigger, value):
    from autowright import triggers as triggerlib

    assert triggerlib.validate_trigger({**trigger, "enabled": value}) == triggerlib.ENABLED_ERROR
    out, err = triggerlib.normalize_triggers([{**trigger, "enabled": value}])
    assert err == triggerlib.ENABLED_ERROR and out == []


def test_a_boolean_or_absent_enabled_is_accepted():
    from autowright import triggers as triggerlib

    for extra in ({}, {"enabled": True}, {"enabled": False}):
        assert triggerlib.validate_trigger(
            {"kind": "cron", "expression": "0 9 * * *", "source": "user", **extra}) is None


# ---------- §4.3 zoned occurrences compare as instants ----------

def _local(dt_utc):
    """The local naive reading of a UTC instant, fold kept (what datetime.now()
    gives in the second pass of a fall-back hour)."""
    return datetime.fromtimestamp(dt_utc.timestamp())


@pytest.mark.parametrize("trigger", [
    {"id": "t1", "kind": "cron", "enabled": True, "expression": "30 6 * * *",
     "source": "user", "timezone": "UTC"},
    {"id": "t1", "kind": "time", "enabled": True, "at": "2026-11-01T06:30:00",
     "timezone": "UTC"},
])
def test_zoned_occurrence_in_the_fold_is_not_due_an_hour_early(store, monkeypatch, new_york_tz,
                                                              trigger):
    """06:30Z on 2026-11-01 is 01:30 EST (the second pass); at 01:30:05 EDT
    (05:30:05Z, the first pass) it must not fire, at 06:30:05Z it does."""
    baseline_utc = datetime(2026, 11, 1, 5, 0, tzinfo=timezone.utc)
    local = _Clock(_local(baseline_utc))
    utc = _Clock(baseline_utc)
    from autowright.engine import Engine
    from autowright.scheduler import Scheduler

    sched = Scheduler(store, Engine(store), clock=local, utc_clock=utc)
    fires = _record_fires(monkeypatch)
    store.create_automation(make_version(), "Zoned", None, triggers=[dict(trigger)])
    sched._tick()  # baseline 01:00 EDT
    first_pass = datetime(2026, 11, 1, 5, 30, 5, tzinfo=timezone.utc)
    local.now, utc.now = _local(first_pass), first_pass
    assert local.now == datetime(2026, 11, 1, 1, 30, 5) and local.now.fold == 0
    sched._tick()
    assert fires == [], "fired an hour early in the first pass of the fold"
    second_pass = datetime(2026, 11, 1, 6, 30, 5, tzinfo=timezone.utc)
    local.now, utc.now = _local(second_pass), second_pass
    assert local.now == datetime(2026, 11, 1, 1, 30, 5) and local.now.fold == 1
    sched._tick()
    assert len(fires) == 1


def test_zoned_cron_next_reads_the_second_pass_after_the_first(new_york_tz):
    from autowright import triggers as triggerlib

    t = {"kind": "cron", "enabled": True, "expression": "30 6 * * *", "timezone": "UTC"}
    after = _local(datetime(2026, 11, 1, 5, 30, 5, tzinfo=timezone.utc))  # 01:30:05 EDT
    nxt = triggerlib.trigger_next(t, after=after)
    assert nxt.astimezone() == datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)
    ms = triggerlib.next_at_ms([t], after=after)
    assert ms == int(datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc).timestamp() * 1000)


def test_zoned_one_shot_in_the_fold_is_not_elapsed_an_hour_early(new_york_tz):
    from autowright import triggers as triggerlib

    t = {"kind": "time", "enabled": True, "at": "2026-11-01T06:30:00", "timezone": "UTC"}
    first = _local(datetime(2026, 11, 1, 5, 30, 5, tzinfo=timezone.utc))
    second = _local(datetime(2026, 11, 1, 6, 30, 5, tzinfo=timezone.utc))
    assert not triggerlib.time_elapsed(t, first)
    assert triggerlib.time_elapsed(t, second)


# ---------- §6/§4.3 the gap-shifted occurrence passes the fresh check ----------

def test_spring_forward_gap_occurrence_fires_with_run_if_missed_off(store, monkeypatch,
                                                                    new_york_tz):
    """`30 2 * * *` on 2026-03-08 New York: 02:30 is erased, the occurrence
    fires at 03:30 EDT (§4.3). With runIfMissed off and the Mac awake, the
    tick at 03:30:05 fires it — never a "missed while asleep" drop."""
    clock = _Clock(datetime(2026, 3, 8, 1, 59))
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    a = store.create_automation(make_version(), "Gap", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "30 2 * * *",
         "source": "user", "runIfMissed": False}])
    sched._tick()  # baseline 01:59 EST
    clock.now = datetime(2026, 3, 8, 3, 29)
    sched._tick()
    assert fires == []
    clock.now = datetime(2026, 3, 8, 3, 30, 5)
    sched._tick()
    assert len(fires) == 1
    assert _drop_records(store, a["id"]) == []


# ---------- §6 the iMessage watcher under the listener lock ----------

def test_stop_never_misses_a_watcher_created_by_an_in_flight_reconcile(monkeypatch):
    from autowright import listeners

    entered = threading.Event()
    gate = threading.Event()
    log: list[str] = []

    class FakeWatcher:
        def __init__(self, manager):
            entered.set()
            gate.wait(5)
            log.append("created")

        def tick(self, senders):
            log.append("tick")

        def close(self):
            log.append("close")

    monkeypatch.setattr(listeners, "_ImsgWatcher", FakeWatcher)
    _pin_imessage_capability(monkeypatch, True)
    manager = listeners.Listeners(store=None, engine=None)
    monkeypatch.setattr(manager, "_desired_secrets", lambda: (set(), {"dave@example.com"}))

    reconcile = threading.Thread(target=manager._reconcile, daemon=True)
    reconcile.start()
    assert entered.wait(5)
    stopper = threading.Thread(target=manager.stop, daemon=True)
    stopper.start()
    stopper.join(0.2)
    assert stopper.is_alive(), "stop() must wait for the watcher being created"
    gate.set()
    reconcile.join(5)
    stopper.join(5)
    assert not stopper.is_alive()
    # The watcher the reconcile made was closed by stop(), after its tick.
    assert log == ["created", "tick", "close"]


def test_no_tick_runs_after_stop(monkeypatch):
    from autowright import listeners

    log: list[str] = []

    class FakeWatcher:
        def __init__(self, manager):
            pass

        def tick(self, senders):
            log.append("tick")

        def close(self):
            log.append("close")

    monkeypatch.setattr(listeners, "_ImsgWatcher", FakeWatcher)
    _pin_imessage_capability(monkeypatch, True)
    manager = listeners.Listeners(store=None, engine=None)
    monkeypatch.setattr(manager, "_desired_secrets", lambda: (set(), {"dave@example.com"}))
    manager._reconcile()
    manager.stop()
    manager._reconcile()
    assert log == ["tick", "close"]


# ---------- §3 macOS install: an unwritable LaunchAgents is a result line ----------

def test_install_with_an_unwritable_launch_agents_answers_a_result_line(svc, monkeypatch):  # noqa: F811
    def refuse(*args, **kwargs):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(svc.mod, "atomic_write_text", refuse)
    out = svc.mod.install()
    assert out.startswith(f"install failed: couldn't write {svc.plist}")
    assert "Permission denied" in out
    assert svc.mod.result_code(out) == 1
    assert svc.actions() == []  # nothing loaded


# ---------- §3 the CLI shim single-quotes the interpreter ----------

def test_shim_text_single_quotes_a_hostile_interpreter_path(monkeypatch, tmp_path):
    import subprocess

    from autowright import service

    weird = "/tmp/it's $HOME `x` \"q\" \\b/python3"
    monkeypatch.setattr(sys, "executable", weird)
    text = service.shim_text()
    assert text == ("#!/bin/sh\n# autowright CLI shim\n"
                    "exec '/tmp/it'\\''s $HOME `x` \"q\" \\b/python3' -m autowright.cli \"$@\"\n")
    # The shell reads the path back byte for byte, nothing expanded.
    probe = tmp_path / "probe.sh"
    probe.write_text(text.replace("exec ", "printf '%s' ", 1).replace(
        ' -m autowright.cli "$@"', ""))
    out = subprocess.run(["/bin/sh", str(probe)], capture_output=True, text=True, check=True)
    assert out.stdout == weird


# ---------- §3 Windows: every single-quote character is doubled ----------

_PS_QUOTES = "'\u2018\u2019\u201a\u201b"


def _literals(script: str) -> list[str]:
    """Every single-quoted literal in `script`, by the PowerShell tokenizer's
    rule: any of the five quote characters opens/closes, a doubled one inside
    is an escaped quote."""
    out, i = [], 0
    while i < len(script):
        if script[i] in _PS_QUOTES:
            j, body = i + 1, []
            while True:
                if script[j] in _PS_QUOTES:
                    if j + 1 < len(script) and script[j + 1] in _PS_QUOTES:
                        body.append(script[j])
                        j += 2
                        continue
                    break
                body.append(script[j])
                j += 1
            out.append("".join(body))
            i = j + 1
        else:
            i += 1
    return out


def test_toast_literals_double_curly_quotes():
    from autowright.platform import windows

    title = "Hero\u2019s Journey"
    body = "done\u2019; Start-Process calc; \u2019"
    script = windows._toast_script(title, body)
    assert "'Hero\u2019\u2019s Journey'" in script
    assert "'done\u2019\u2019; Start-Process calc; \u2019\u2019'" in script
    literals = _literals(script)
    assert title in literals and body in literals
    # No literal ended early: the injected command is never outside a literal.
    outside = script
    for lit in ("'Hero\u2019\u2019s Journey'", "'done\u2019\u2019; Start-Process calc; \u2019\u2019'"):
        outside = outside.replace(lit, "")
    assert "Start-Process" not in outside


@pytest.mark.parametrize("quote", list(_PS_QUOTES))
def test_ps_literal_doubles_every_quote_character(quote):
    from autowright.platform import windows

    assert windows._ps_literal(f"a{quote}b") == f"'a{quote}{quote}b'"


# ---------- §19 a held draft outcome survives the finished-record bound ----------

def test_held_outcome_survives_twenty_five_cancelled_jobs(monkeypatch):
    from autowright import drafting

    monkeypatch.setattr(drafting.DraftJobs, "_run", lambda self, *args: None)
    jobs = drafting.DraftJobs()
    held_id = jobs.start("sync", {}, None, None, {}, owner_id="held-owner")
    with jobs._lock:
        jobs.jobs[held_id]["status"] = "done"
    cancelled_ids = []
    for i in range(25):
        job_id = jobs.start("sync", {}, None, None, {}, owner_id=f"owner-{i}")
        with jobs._lock:
            jobs.jobs[job_id]["status"] = "cancelled"
        cancelled_ids.append(job_id)
    jobs.start("sync", {}, None, None, {}, owner_id="last-owner")
    assert held_id in jobs.jobs
    assert jobs.jobs[held_id]["status"] == "done"
    kept = [k for k in cancelled_ids if k in jobs.jobs]
    assert kept == cancelled_ids[-20:]
