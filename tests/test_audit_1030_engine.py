"""Engine and scheduling audit regressions (2026-10-30).

§6.3 only a record with a step attempt counts as the version's recorded
execution, across a restart too; §6 nothing jumps the queue (waiting entries
count as capacity, the tick drains before it fires); §6 stale message entries
expire every tick whatever the capacity; §6 runIfMissed's grace probe keeps
`fold`; §7 a retried failed attempt's result ops are dropped; §7 skip wins
over a pending finite retry; §6 a head that can never start ends instead of
wedging the queue.
"""
import time
from datetime import datetime, timezone

import pytest
from conftest import make_version, read_all_logs, wait_done
from test_audit_1028_scheduler import _Clock, new_york_tz  # noqa: F401 - fixture


def _discord_trigger():
    return {"id": "t1", "kind": "discord", "enabled": True, "secret": "TOKEN",
            "channel": "42"}


def _payload(sender="Dave", text="hi"):
    return {"kind": "discord", "channel": "42", "secret": "TOKEN",
            "sender": sender, "text": text}


def _record_busy(monkeypatch):
    from autowright import listeners

    told = []
    monkeypatch.setattr(listeners, "notify_busy", told.append)
    return told


def _wait_until(predicate, timeout=30):
    t0 = time.time()
    while not predicate():
        assert time.time() - t0 < timeout, "condition never held"
        time.sleep(0.05)


# ---------- E1: §6.3 a zero-attempt record never counts, across a restart ----------

def test_zero_attempt_record_leaves_the_pre_version_snapshot_owed_after_restart(store):
    from autowright.engine import Engine
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Owed", None)
    version = a["current_version"]
    steps = [{"name": "Say hello", "file": "01-say.py", "status": "interrupted",
              "duration_ms": None, "attempts": []}]
    h = store.create_execution(a, "version", version, "manual", steps)
    h["status"] = "interrupted"
    h["finished_at"] = h["started_at"]
    store.update_execution(h)

    fresh = Store()
    fresh.load_all()
    loaded = fresh.autos[a["id"]]
    assert "steps" not in fresh.execs[h["id"]]  # a header row: the body decides
    assert Engine(fresh)._needs_pre_version(loaded, "version", version) is True

    # Control: a record that started an attempt is the recorded execution.
    steps[0]["attempts"] = [{"number": 1, "status": "failed", "started_at": h["started_at"],
                             "duration_ms": 1}]
    h2 = store.create_execution(a, "version", version, "manual", steps)
    h2["status"] = "failed"
    h2["finished_at"] = h2["started_at"]
    store.update_execution(h2)
    again = Store()
    again.load_all()
    assert Engine(again)._needs_pre_version(again.autos[a["id"]], "version", version) is False


# ---------- E2: §6 nothing jumps the queue ----------

def test_firing_between_finish_and_drain_joins_the_tail(store, monkeypatch):
    from autowright.engine import Engine
    from autowright.firing import drain_queue, fire_trigger
    from autowright.scheduler import Scheduler

    _record_busy(monkeypatch)
    engine = Engine(store)
    Scheduler(store, engine)  # wires the finish-time drain hook
    ver = make_version()
    ver["steps"] = [{"file": "01-wait.py", "name": "Wait", "description": "",
                     "code": "import time\ntime.sleep(0.6)\n"}]
    a = store.create_automation(ver, "Fifo", None)
    a["max_queued"] = 3
    trigger = _discord_trigger()

    seen = {}
    real_drain = engine.drain_queue

    def drain_with_a_late_arrival(automation_id):
        # The slot is free, the drain has not run yet: message B lands now.
        if "b" not in seen:
            fire_trigger(store, engine, a, trigger, payload=_payload("B"))
            (b_entry,) = [x for x in store.queued_execs(a["id"])
                          if x["trigger_payload"]["sender"] == "B"]
            seen["b"] = b_entry["id"]
            seen["queue_before_drain"] = [x["trigger_payload"]["sender"]
                                          for x in store.queued_execs(a["id"])]
            real_drain(automation_id)
            seen["a_after_drain"] = store.execs[seen["a"]]["status"]
            seen["b_after_drain"] = store.execs[seen["b"]]["status"]
            return
        real_drain(automation_id)

    engine.drain_queue = drain_with_a_late_arrival
    x = engine.start(a, "manual")
    fire_trigger(store, engine, a, trigger, payload=_payload("A"))
    (a_entry,) = store.queued_execs(a["id"])
    seen["a"] = a_entry["id"]
    wait_done(engine, x["id"])
    # The finish-time drain runs after the slot is released: wait for the hook.
    _wait_until(lambda: "b_after_drain" in seen)
    assert seen["queue_before_drain"] == ["A", "B"]  # B queued behind A, never started
    assert seen["a_after_drain"] == "executing"
    assert seen["b_after_drain"] == "queued"
    _wait_until(lambda: store.execs[seen["b"]]["status"] not in ("queued", "executing"))
    wait_done(engine, seen["a"])
    assert store.execs[seen["a"]]["status"] == "succeeded"
    assert store.execs[seen["b"]]["status"] == "succeeded"
    drain_queue(store, engine, a["id"])  # nothing left


def test_waiting_entries_count_as_capacity_for_every_admission(store, monkeypatch):
    from autowright.engine import CapacityError, Engine
    from autowright.firing import fire_trigger, queue_manual

    _record_busy(monkeypatch)
    engine = Engine(store)
    a = store.create_automation(make_version(), "Waiting", None)
    a["max_queued"] = 5
    a["_live"] = {"blocking"}
    fire_trigger(store, engine, a, _discord_trigger(), payload=_payload("A"))
    a["_live"] = set()  # the slot freed; its drain has not run yet
    with pytest.raises(CapacityError):
        engine.start(a, "manual")
    entry, queued = queue_manual(store, engine, a, "manual")
    assert queued is True and entry["status"] == "queued"
    cron = {"id": "c1", "kind": "cron", "enabled": True, "expression": "0 8 * * *"}
    assert fire_trigger(store, engine, a, cron) is False
    skipped = [x for x in store.execs.values() if x["trigger"] == "cron"]
    assert [x["status"] for x in skipped] == ["skipped"]
    failed = store.create_execution(a, "version", a["current_version"], "manual", [])
    failed["status"] = "failed"
    store.update_execution(failed)
    with pytest.raises(CapacityError):
        engine.retry(a, failed)
    assert a["_live"] == set()  # nothing jumped the two waiting entries


def test_tick_drains_before_it_fires(store, monkeypatch):
    from autowright import scheduler as sched_mod
    from autowright.engine import Engine
    from autowright.firing import fire_trigger
    from autowright.scheduler import Scheduler

    _record_busy(monkeypatch)
    clock = _Clock(datetime(2026, 7, 10, 7, 59))
    engine = Engine(store)
    sched = Scheduler(store, engine, clock=clock,
                      utc_clock=lambda: clock().astimezone(timezone.utc))
    a = store.create_automation(make_version(), "Order", None, triggers=[
        {"id": "c1", "kind": "cron", "enabled": True, "expression": "0 8 * * *"}])
    for t in a["triggers"]:
        t.pop("enabledAt", None)  # the real-time stamp would postdate the fake clock
    a["max_queued"] = 3
    order = []
    monkeypatch.setattr(sched_mod, "drain_queue",
                        lambda store, engine, automation_id: order.append("drain"))
    monkeypatch.setattr(sched_mod, "fire_trigger",
                        lambda store, engine, a, t, **_: order.append("fire") or True)
    sched._tick()  # baselines
    a["_live"] = {"blocking"}
    fire_trigger(store, engine, a, _discord_trigger(), payload=_payload())
    a["_live"] = set()
    clock.now = datetime(2026, 7, 10, 8, 1)
    sched._tick()
    assert order == ["drain", "fire"]


# ---------- E3: §6 stale message entries expire every tick, whatever the capacity ----------

def test_stale_entries_expire_under_full_capacity_and_senders_are_told(store, monkeypatch):
    from autowright import firing as firing_mod
    from autowright.engine import Engine
    from autowright.firing import fire_trigger, queue_manual
    from autowright.scheduler import Scheduler

    told = _record_busy(monkeypatch)
    engine = Engine(store)
    sched = Scheduler(store, engine)
    a = store.create_automation(make_version(), "Stale", None)
    a["max_queued"] = 5
    a["_live"] = {"blocking"}  # every slot stays busy throughout
    manual, _queued = queue_manual(store, engine, a, "manual")  # the head: no TTL
    fire_trigger(store, engine, a, _discord_trigger(), payload=_payload("Old"))
    fire_trigger(store, engine, a, _discord_trigger(), payload=_payload("New"))
    entries = {x["trigger_payload"]["sender"]: x
               for x in store.queued_execs(a["id"]) if x["trigger_payload"]}
    manual["queued_at"] = "2020-01-01T00:00:00+00:00"
    entries["Old"]["queued_at"] = "2020-01-01T00:00:00+00:00"
    monkeypatch.setattr(firing_mod, "QUEUE_TTL_S", 120.0)

    sched._tick()
    old = store.execs[entries["Old"]["id"]]
    assert old["status"] == "skipped" and old["note"] == "waited too long in the queue"
    assert [p["sender"] for p in told] == ["Old"]
    assert store.execs[entries["New"]["id"]]["status"] == "queued"
    assert store.execs[manual["id"]]["status"] == "queued"  # manual entries never expire
    assert a["_live"] == {"blocking"}


# ---------- E5: §6 the runIfMissed grace probe keeps fold ----------

def test_run_if_missed_off_drops_a_first_pass_occurrence_seen_in_the_second_pass(
        store, monkeypatch, new_york_tz):  # noqa: F811
    from autowright import scheduler as sched_mod
    from autowright.engine import Engine
    from autowright.scheduler import Scheduler

    def local(dt_utc):
        return datetime.fromtimestamp(dt_utc.timestamp())

    utc = _Clock(datetime(2026, 11, 1, 5, 20, tzinfo=timezone.utc))  # 01:20 EDT
    clock = _Clock(local(utc.now))
    sched = Scheduler(store, Engine(store), clock=clock, utc_clock=utc)
    fires = []
    monkeypatch.setattr(sched_mod, "fire_trigger",
                        lambda store, engine, a, t, **_: fires.append(t["id"]) or True)
    a = store.create_automation(make_version(), "Fold", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "30 1 * * *",
         "source": "user", "runIfMissed": False}])
    for t in a["triggers"]:
        t.pop("enabledAt", None)
    sched._tick()  # baseline 01:20 EDT, first pass
    # The Mac slept through 01:30 EDT and wakes at 01:15 EST (second pass).
    utc.now = datetime(2026, 11, 1, 6, 15, tzinfo=timezone.utc)
    clock.now = local(utc.now)
    assert clock.now == datetime(2026, 11, 1, 1, 15) and clock.now.fold == 1
    sched._tick()
    assert fires == [], "a slept-through occurrence fired 45 min late"
    drops = [x for x in store.execs.values()
             if x["automation_id"] == a["id"] and x["status"] == "skipped"]
    assert len(drops) == 1


# ---------- E6: §7 a retried failed attempt's result ops are dropped ----------

def test_failed_attempt_chip_is_dropped_after_a_successful_retry(store, monkeypatch):
    from autowright.engine import Engine

    store.settings["notifications"] = "all"
    engine = Engine(store)
    ended = []
    monkeypatch.setattr(engine, "_notify_end",
                        lambda auto, ver, h, result, text: ended.append((result, text)))
    ver = make_version()
    ver["steps"] = [{
        "file": "01-flaky.py", "name": "Flaky", "description": "", "retries": 1,
        "code": ("from autowright import notify, result\nimport pathlib\n"
                 "p = pathlib.Path('tried')\n"
                 "if not p.exists():\n"
                 "    p.write_text('1')\n"
                 "    result.status('attention')\n"
                 "    result.chip('3 errors')\n"
                 "    notify('it broke')\n"
                 "    raise RuntimeError('first attempt fails')\n")}]
    a = store.create_automation(ver, "Dropped chip", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "succeeded"
    assert [x["status"] for x in h["steps"][0]["attempts"]] == ["failed", "succeeded"]
    assert h["chip"] is None and h["chip_status"] is None
    _wait_until(lambda: ended)
    result, text = ended[0]
    assert text is None
    assert result is None or (result.get("chip") is None and result.get("status") == "ok")


def test_final_failed_attempt_keeps_what_it_reported(store):
    from autowright.engine import Engine

    engine = Engine(store)
    ver = make_version()
    ver["steps"] = [{
        "file": "01-broken.py", "name": "Broken", "description": "", "retries": 1,
        "code": ("from autowright import result\n"
                 "result.status('attention')\nresult.chip('3 errors')\n"
                 "raise RuntimeError('always fails')\n")}]
    a = store.create_automation(ver, "Kept chip", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "failed"
    assert h["chip"] == "3 errors" and h["chip_status"] == "attention"


# ---------- E7: §7 skip wins over a pending finite retry ----------

def test_skip_before_a_finite_retry_appends_no_attempt(store, monkeypatch):
    from autowright.engine import Engine

    engine = Engine(store)

    def skip_lands(state, i, forever):
        state["skip"] = i  # the user's Skip lands as the retry is about to start

    monkeypatch.setattr(Engine, "_await_retry", staticmethod(skip_lands))
    ver = make_version()
    ver["steps"] = [{"file": "01-fails.py", "name": "Fails", "description": "", "retries": 3,
                     "code": "raise RuntimeError('fails fast')\n"},
                    {"file": "02-after.py", "name": "After", "description": "",
                     "code": "from autowright import log\nlog('after ran')\n"}]
    a = store.create_automation(ver, "Skip retry", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert [x["status"] for x in h["steps"][0]["attempts"]] == ["skipped"]
    assert h["steps"][0]["status"] == "skipped"
    assert h["steps"][1]["status"] == "succeeded"
    logs = read_all_logs(store, h["id"])
    assert not any("retrying" in line["text"] for line in logs)


# ---------- E10: §6 a head that can never start ends instead of wedging ----------

def test_unstartable_head_finishes_skipped_and_the_drain_moves_on(store, monkeypatch):
    from autowright.engine import Engine
    from autowright.firing import drain_queue, queue_manual

    engine = Engine(store)
    a = store.create_automation(make_version(), "Wedge", None)
    a["max_queued"] = 5
    a["_live"] = {"blocking"}
    head, _queued = queue_manual(store, engine, a, "manual")
    second, _queued = queue_manual(store, engine, a, "manual")
    real = store.merged_params
    calls = []

    def damaged_once(auto, ver):
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("damaged param definition")
        return real(auto, ver)

    monkeypatch.setattr(store, "merged_params", damaged_once)
    a["_live"] = set()
    drain_queue(store, engine, a["id"])
    assert store.execs[head["id"]]["status"] == "skipped"
    assert store.execs[head["id"]]["note"] == "couldn't be started"
    wait_done(engine, second["id"])
    assert store.execs[second["id"]]["status"] == "succeeded"


def test_write_error_on_promotion_keeps_the_head_queued(store, monkeypatch):
    from autowright.engine import Engine
    from autowright.firing import drain_queue, queue_manual

    engine = Engine(store)
    a = store.create_automation(make_version(), "Transient", None)
    a["max_queued"] = 5
    a["_live"] = {"blocking"}
    head, _queued = queue_manual(store, engine, a, "manual")

    def disk_full(auto, ver):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store, "merged_params", disk_full)
    a["_live"] = set()
    drain_queue(store, engine, a["id"])
    assert store.execs[head["id"]]["status"] == "queued"  # the next drain retries it



# ---------- E4: §6 an enable stamp far ahead of now is ignored ----------

@pytest.mark.parametrize("trigger, later", [
    ({"id": "t1", "kind": "cron", "enabled": True, "expression": "0 8 * * *"},
     datetime(2026, 7, 10, 8, 1)),
    ({"id": "t1", "kind": "interval", "enabled": True, "every": "PT1H"},
     datetime(2026, 7, 10, 8, 31)),
])
def test_enable_stamp_an_hour_ahead_never_silences_the_trigger(store, monkeypatch,
                                                                trigger, later):
    from autowright import scheduler as sched_mod
    from autowright.engine import Engine
    from autowright.scheduler import Scheduler

    clock = _Clock(datetime(2026, 7, 10, 7, 59))
    sched = Scheduler(store, Engine(store), clock=clock,
                      utc_clock=lambda: clock().astimezone(timezone.utc))
    fires = []
    monkeypatch.setattr(sched_mod, "fire_trigger",
                        lambda store, engine, a, t, **_: fires.append(t["id"]) or True)
    a = store.create_automation(make_version(), "Ahead", None, triggers=[dict(trigger)])
    # The interval anchors on the run baseline: created 06:30, so 07:30, 08:30, …
    a["created_at"] = datetime(2026, 7, 10, 6, 30).astimezone().isoformat()
    # Stamped while the clock was an hour ahead, then corrected.
    a["triggers"][0]["enabledAt"] = datetime(2026, 7, 10, 8, 59).astimezone().isoformat()
    sched._tick()  # baselines
    assert fires == []
    clock.now = later
    sched._tick()
    assert fires == ["t1"]
