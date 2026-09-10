"""§6 scheduler policy: tick/firing rules (no automatic execution-level retry —
§7 step retry is the engine's job) and the firing queue — admission, depth cap,
drain, staleness, and cancel."""
from conftest import make_version


def _mk(store):
    from autowright.engine import Engine
    from autowright.scheduler import Scheduler

    engine = Engine(store)
    sched = Scheduler(store, engine)  # loop not started — we drive ticks directly
    return engine, sched


def test_failed_scheduled_run_stays_failed(store):
    """§6: a failed trigger-fired execution is never retried automatically —
    a later tick leaves the record exactly as it failed."""
    import time
    from datetime import datetime, timedelta

    engine, sched = _mk(store)
    ver = make_version()
    ver["steps"][0]["code"] = 'raise RuntimeError("boom")\n'
    a = store.create_automation(ver, "Sched Fail", None)
    h = engine.start(a, "cron")
    t0 = time.time()
    while engine.is_live(h["id"]):
        assert time.time() - t0 < 30
        time.sleep(0.1)
    assert h["status"] == "failed"
    attempts = len(h["steps"][0]["attempts"])
    sched._clock = lambda now=datetime.now() + timedelta(minutes=6): now
    sched._tick()
    time.sleep(0.2)
    assert h["status"] == "failed"  # still failed — nothing re-ran it
    assert len(h["steps"][0]["attempts"]) == attempts
    assert not engine.is_live(h["id"])


# ---------- §6 tick behavior, driven directly with an injected fake clock ----------

class _Clock:
    """Mutable fake clock for Scheduler(clock=...) — call returns .now."""

    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def _mk_clocked(store, clock):
    from autowright.engine import Engine
    from autowright.scheduler import Scheduler

    engine = Engine(store)
    sched = Scheduler(store, engine, clock=clock)  # loop never started
    return engine, sched


def _record_fires(monkeypatch):
    """Replace the module-level fire_trigger with a recorder."""
    from autowright import scheduler as sched_mod

    fires = []
    monkeypatch.setattr(sched_mod, "fire_trigger",
                        lambda store, engine, a, t: fires.append((a["id"], t["id"])) or True)
    return fires


def test_same_moment_triggers_coalesce_into_one_fire(store, monkeypatch):
    from datetime import datetime
    from conftest import make_version

    clock = _Clock(datetime(2026, 7, 10, 7, 59))
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    trigs = [{"id": "t1", "kind": "cron", "enabled": True, "expression": "0 8 * * *"},
             {"id": "t2", "kind": "cron", "enabled": True, "expression": "0 8 * * *"}]
    store.create_automation(make_version(), "Coalesce", None, triggers=trigs)
    sched._tick()  # establishes baselines at 7:59
    assert fires == []
    clock.now = datetime(2026, 7, 10, 8, 1)
    sched._tick()
    assert len(fires) == 1  # both due at 8:00 → one execution
    sched._tick()
    assert len(fires) == 1  # both baselines advanced — nothing re-fires


def test_at_most_one_catch_up_per_wake(store, monkeypatch):
    from datetime import datetime
    from conftest import make_version

    clock = _Clock(datetime(2026, 7, 10, 10, 30))
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    a = store.create_automation(make_version(), "Catchup", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "0 * * * *"}])
    sched._tick()  # baseline 10:30
    clock.now = datetime(2026, 7, 10, 13, 31)  # slept through 11:00, 12:00, 13:00
    sched._tick()
    assert len(fires) == 1  # single catch-up, older occurrences swallowed
    assert sched._baseline[(a["id"], "t1")] == clock.now  # baseline advanced to now
    sched._tick()
    assert len(fires) == 1
    clock.now = datetime(2026, 7, 10, 14, 1)
    sched._tick()
    assert len(fires) == 2  # normal next occurrence still fires


def test_occurrence_missed_while_off_never_fires(store, monkeypatch):
    from datetime import datetime
    from conftest import make_version

    clock = _Clock(datetime(2026, 7, 10, 7, 59))
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    a = store.create_automation(make_version(), "OffMiss", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": False, "expression": "0 8 * * *"}])
    sched._tick()
    clock.now = datetime(2026, 7, 10, 8, 5)  # 8:00 passes while off
    sched._tick()
    assert fires == []
    a["triggers"][0]["enabled"] = True  # re-enable after the occurrence
    clock.now = datetime(2026, 7, 10, 8, 6)
    sched._tick()
    assert fires == []  # the missed 8:00 never fires
    clock.now = datetime(2026, 7, 11, 8, 1)  # NEXT occurrence
    sched._tick()
    assert len(fires) == 1


def test_one_shot_time_trigger_consumed_after_fire(store, monkeypatch):
    from datetime import datetime
    from conftest import make_version

    clock = _Clock(datetime(2026, 7, 10, 7, 59))
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    a = store.create_automation(make_version(), "OneShot", None, triggers=[
        {"id": "tt", "kind": "time", "enabled": True, "at": "2026-07-10T08:00"}])
    sched._tick()
    clock.now = datetime(2026, 7, 10, 8, 1)
    sched._tick()
    assert len(fires) == 1
    assert a["triggers"] == []  # consumed — removed from the automation
    clock.now = datetime(2026, 7, 10, 8, 2)
    sched._tick()
    assert len(fires) == 1


def test_fire_trigger_mid_execution_writes_skipped_record(store):
    from autowright.firing import fire_trigger
    from conftest import make_version

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Busy", None)
    a["_live"] = {"some-live-exec"}
    t = {"id": "t1", "kind": "cron", "enabled": True, "expression": "0 8 * * *"}
    assert fire_trigger(store, engine, a, t) is False
    recs = [h for h in store.execs.values() if h["automation_id"] == a["id"]]
    assert len(recs) == 1
    h = recs[0]
    assert h["status"] == "skipped"
    assert h["note"] == "previous execution still in progress"
    assert h["trigger"] == "cron"  # stored kind; the label is derived (§4.5)
    assert h["duration_ms"] == 0 and h["finished_at"] == h["started_at"]


def _discord_trig():
    return {"id": "t1", "kind": "discord", "enabled": True, "secret": "TOKEN",
            "channel": "42"}


def _payload(sender="Dave", text="hi"):
    return {"kind": "discord", "channel": "42", "secret": "TOKEN",
            "sender": sender, "text": text}


def test_fire_trigger_answers_a_refused_message_firing(store, monkeypatch):
    """§6: a message firing refused for lack of queue room replies to its sender;
    a cron firing with no free slot is skipped silently — nobody to answer."""
    from autowright import listeners as li_mod
    from autowright.firing import fire_trigger
    from conftest import make_version

    notified = []
    monkeypatch.setattr(li_mod, "notify_busy", notified.append)

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Busy", None)
    a["_live"] = {"some-live-exec"}
    a["max_queued"] = 0  # queue disabled → straight to skip-on-busy
    payload = _payload()

    assert fire_trigger(store, engine, a, _discord_trig(), payload=payload) is False
    assert notified == [payload]

    # §7: nothing was queued, so nothing was full — skip-on-busy keeps the plain
    # note whether or not the firing could have queued.
    assert [h["note"] for h in store.execs.values()] == ["previous execution still in progress"]

    cron = {"id": "t2", "kind": "cron", "enabled": True, "expression": "0 8 * * *"}
    assert fire_trigger(store, engine, a, cron) is False
    assert notified == [payload]  # unchanged — no sender to answer
    assert {h["note"] for h in store.execs.values()} == {"previous execution still in progress"}


def test_message_firing_queues_instead_of_being_dropped(store):
    """§6: with room in the queue the firing waits as a `queued` record — not a
    skip, and not something that shadows the automation's real latest status."""
    from autowright.firing import fire_trigger
    from conftest import make_version

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Chat", None)
    a["max_queued"] = 10  # §4.1: queueing is opt-in — the default 0 would skip
    a["_live"] = {"some-live-exec"}

    assert fire_trigger(store, engine, a, _discord_trig(), payload=_payload()) is False
    q = store.queued_execs(a["id"])
    assert len(q) == 1
    assert q[0]["status"] == "queued" and q[0]["queued_at"]
    assert q[0]["trigger_payload"]["sender"] == "Dave"
    # a queued record never counts as the automation's latest (§4.1)
    assert store._latest_exec(a["id"]) is None
    assert a["_last_status"] == "none"


def test_admission_publishes_exec_queued(store, monkeypatch):
    """§6/§19: admission publishes `exec.queued` carrying the new record — the
    §7 Waiting section and the §9.2 "N waiting" line update off it, and without
    it a waiting firing stays invisible until something else forces a refetch."""
    from autowright import scheduler as sch
    from autowright.firing import fire_trigger
    from conftest import make_version

    events = []
    monkeypatch.setattr(sch.hub, "publish", lambda ev, **kw: events.append((ev, kw)))

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Chat", None)
    a["max_queued"] = 10  # §4.1: queueing is opt-in — the default 0 would skip
    a["_live"] = {"some-live-exec"}

    assert fire_trigger(store, engine, a, _discord_trig(), payload=_payload()) is False
    assert [ev for ev, _ in events] == ["execution.queued"]
    kw = events[0][1]
    assert kw["executionId"] == store.queued_execs(a["id"])[0]["id"]
    assert kw["execution"]["status"] == "queued"
    assert kw["execution"]["queuedMs"] > 0


def test_queue_admits_every_firing_and_caps_at_max_queued(store, monkeypatch):
    """§6: every admitted firing is its own entry — same-sender messages never
    coalesce; past the cap the newest firing is refused, not admitted."""
    from autowright import listeners as li_mod
    from autowright.firing import fire_trigger
    from conftest import make_version

    notified = []
    monkeypatch.setattr(li_mod, "notify_busy", notified.append)

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Chat", None)
    a["_live"] = {"some-live-exec"}
    a["max_queued"] = 2

    fire_trigger(store, engine, a, _discord_trig(), payload=_payload("Dave", "one"))
    fire_trigger(store, engine, a, _discord_trig(), payload=_payload("Dave", "two"))
    q = store.queued_execs(a["id"])
    assert len(q) == 2  # one entry per firing, even from the same sender
    assert len({e["id"] for e in q}) == 2
    assert [e["trigger_payload"]["text"] for e in q] == ["one", "two"]  # FIFO
    assert notified == []

    fire_trigger(store, engine, a, _discord_trig(), payload=_payload("Ana"))
    q = store.queued_execs(a["id"])
    assert len(q) == 2  # the newest is refused — admitted entries are never evicted
    assert {e["trigger_payload"]["text"] for e in q} == {"one", "two"}
    assert [p["sender"] for p in notified] == ["Ana"]
    # §7: a full queue names the cap — the row must say which knob to turn, not
    # read the same as configured skip-on-busy.
    ana = next(h for h in store.execs.values()
               if h.get("trigger_sender") == "Ana")
    assert ana["status"] == "skipped"
    assert ana["note"] == "the queue was full (2 waiting)"


def test_queue_drains_into_a_freed_slot_and_promotes_in_place(store):
    """§6: a freed slot goes to the longest-waiting firing, and promotion reuses
    the record — one firing produces exactly one execution from admission to
    finish, carrying its payload and its queued_at."""
    import time

    from autowright.firing import drain_queue, fire_trigger
    from conftest import make_version

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Chat", None)
    a["max_queued"] = 10  # §4.1: queueing is opt-in — the default 0 would skip
    a["_live"] = {"blocking"}

    fire_trigger(store, engine, a, _discord_trig(), payload=_payload("Dave"))
    q = store.queued_execs(a["id"])
    assert len(q) == 1
    entry_id, queued_at = q[0]["id"], q[0]["queued_at"]

    a["_live"] = set()  # the blocking execution finished
    drain_queue(store, engine, a["id"])

    t0 = time.time()
    while engine.is_live(entry_id):
        assert time.time() - t0 < 30
        time.sleep(0.05)

    h = store.exec_full(entry_id)  # finished records are demoted to headers in memory
    assert store.queued_execs(a["id"]) == []  # left the queue
    assert h["status"] == "succeeded"  # same record, now executed
    assert h["steps"] and h["trigger"] == "discord"
    assert h["trigger_sender"] == "Dave"  # sender survived promotion
    assert h["queued_at"] == queued_at  # the wait is still on the record
    assert h["started_at"] >= queued_at  # …but the duration measures execution


def test_stale_queue_entry_is_answered_not_executed(store, monkeypatch):
    """§6: an entry that reaches the head past the TTL is finished, not run —
    answering a stale question is noise. It ends `skipped`, so it can never
    become the automation's latest (§4.1)."""
    from autowright import firing as firing_mod
    from autowright import listeners as li_mod
    from autowright.firing import drain_queue, fire_trigger
    from conftest import make_version

    notified = []
    monkeypatch.setattr(li_mod, "notify_busy", notified.append)
    monkeypatch.setattr(firing_mod, "QUEUE_TTL_S", -1.0)  # everything is stale

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Chat", None)
    a["max_queued"] = 10  # §4.1: queueing is opt-in — the default 0 would skip
    a["_live"] = {"blocking"}
    fire_trigger(store, engine, a, _discord_trig(), payload=_payload("Dave"))
    entry_id = store.queued_execs(a["id"])[0]["id"]

    a["_live"] = set()
    drain_queue(store, engine, a["id"])

    h = store.execs[entry_id]
    assert h["status"] == "skipped" and h["note"] == "waited too long in the queue"
    assert not engine.is_live(entry_id)  # never executed
    assert store._latest_exec(a["id"]) is None  # never shadows the real latest
    assert [p["sender"] for p in notified] == ["Dave"]


def test_cancelling_a_queued_entry_answers_its_sender(store, monkeypatch):
    """§6/§19: the ordinary cancel endpoint covers a waiting entry. It ends
    `skipped` — it never ran, and §4.6 reserves that status for exactly this."""
    from autowright import listeners as li_mod
    from autowright.firing import fire_trigger
    from conftest import make_version

    notified = []
    monkeypatch.setattr(li_mod, "notify_busy", notified.append)

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Chat", None)
    a["max_queued"] = 10  # §4.1: queueing is opt-in — the default 0 would skip
    a["_live"] = {"blocking"}
    fire_trigger(store, engine, a, _discord_trig(), payload=_payload("Dave"))
    entry_id = store.queued_execs(a["id"])[0]["id"]

    assert engine.cancel(entry_id) is True
    h = store.execs[entry_id]
    assert h["status"] == "skipped" and h["note"] == "cancelled before it ran"
    assert store.queued_execs(a["id"]) == []
    assert [p["sender"] for p in notified] == ["Dave"]
    assert store._latest_exec(a["id"]) is None


def test_max_parallel_admits_several_and_then_queues(store):
    """§6: maxParallel slots run concurrently; the firing that finds them all
    taken waits rather than being dropped."""
    import time

    from autowright.firing import fire_trigger
    from conftest import make_version

    engine, sched = _mk(store)
    ver = make_version()
    ver["steps"][0]["code"] = "import time\ntime.sleep(1.5)\n"
    a = store.create_automation(ver, "Parallel", None)
    a["max_parallel"] = 2
    a["max_queued"] = 10  # §4.1: queueing is opt-in — default 0 would skip Kim

    assert fire_trigger(store, engine, a, _discord_trig(), payload=_payload("Dave")) is True
    assert fire_trigger(store, engine, a, _discord_trig(), payload=_payload("Ana")) is True
    assert len(a["_live"]) == 2  # both running at once
    # the third finds no slot and waits
    assert fire_trigger(store, engine, a, _discord_trig(), payload=_payload("Kim")) is False
    assert len(store.queued_execs(a["id"])) == 1

    t0 = time.time()
    while True:
        # under store.lock: a §6 promotion flips queued→executing and registers
        # `_live` atomically only to lock-holding readers — a bare read can
        # catch the record between the two and see nothing pending
        with store.lock:
            if not (store.queued_execs(a["id"]) or a["_live"]):
                break
        assert time.time() - t0 < 60
        time.sleep(0.1)
    # the queued firing was drained into the slot the first finisher freed
    kim = [h for h in store.execs.values()
           if h.get("trigger_sender") == "Kim"]
    assert len(kim) == 1 and kim[0]["status"] == "succeeded"


def test_trigger_off_cancels_its_waiting_entries(store, monkeypatch):
    """§6: turning the trigger off (or removing it) cancels every waiting entry
    it admitted — the entry could never be re-admitted, and promoting it would
    execute a firing the user just switched off. Sender is told."""
    from autowright import listeners as li_mod
    from autowright.firing import cancel_unmatched_queue, fire_trigger
    from conftest import make_version

    notified = []
    monkeypatch.setattr(li_mod, "notify_busy", notified.append)

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Chat", None)
    a["max_queued"] = 10  # §4.1: queueing is opt-in — the default 0 would skip
    a["triggers"] = [_discord_trig()]
    a["_live"] = {"blocking"}
    fire_trigger(store, engine, a, _discord_trig(), payload=_payload("Dave"))
    fire_trigger(store, engine, a, _discord_trig(), payload=_payload("Ana"))
    assert len(store.queued_execs(a["id"])) == 2

    # trigger still enabled: nothing is cancelled
    cancel_unmatched_queue(store, engine, a["id"])
    assert len(store.queued_execs(a["id"])) == 2

    a["triggers"][0]["enabled"] = False
    cancel_unmatched_queue(store, engine, a["id"])
    assert store.queued_execs(a["id"]) == []
    notes = [h["note"] for h in store.execs.values() if h["status"] == "skipped"]
    assert notes == ["cancelled before it ran"] * 2
    assert {p["sender"] for p in notified} == {"Dave", "Ana"}
    assert store._latest_exec(a["id"]) is None  # cancelled entries never shadow


def test_trigger_off_keeps_entries_of_other_enabled_triggers(store):
    """§6: with two discord triggers, turning one off cancels only the entries
    whose payload matches it — the other trigger's waiter keeps its place."""
    from autowright.firing import cancel_unmatched_queue, fire_trigger
    from conftest import make_version

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Chat", None)
    a["max_queued"] = 10  # §4.1: queueing is opt-in — the default 0 would skip
    other = {"id": "t2", "kind": "discord", "enabled": True, "secret": "TOKEN",
             "channel": "99"}
    a["triggers"] = [_discord_trig(), other]
    a["_live"] = {"blocking"}
    fire_trigger(store, engine, a, _discord_trig(), payload=_payload("Dave"))
    p2 = {**_payload("Ana"), "channel": "99"}
    fire_trigger(store, engine, a, other, payload=p2)
    assert len(store.queued_execs(a["id"])) == 2

    a["triggers"][0]["enabled"] = False
    cancel_unmatched_queue(store, engine, a["id"])
    q = store.queued_execs(a["id"])
    assert len(q) == 1
    assert q[0]["trigger_payload"]["sender"] == "Ana"


def test_each_admission_publishes_its_own_exec_queued(store, monkeypatch):
    """§6: every admitted firing publishes exec.queued for its own record —
    same-sender messages are distinct entries, so the Waiting list shows both."""
    from autowright import scheduler as sch
    from autowright.firing import fire_trigger
    from conftest import make_version

    events = []
    monkeypatch.setattr(sch.hub, "publish", lambda ev, **kw: events.append((ev, kw)))

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Chat", None)
    a["max_queued"] = 10  # §4.1: queueing is opt-in — the default 0 would skip
    a["_live"] = {"blocking"}
    fire_trigger(store, engine, a, _discord_trig(), payload=_payload("Dave", "one"))
    fire_trigger(store, engine, a, _discord_trig(), payload=_payload("Dave", "two"))

    queued_events = [kw for ev, kw in events if ev == "execution.queued"]
    assert len(queued_events) == 2  # one admission event per firing
    assert queued_events[0]["executionId"] != queued_events[1]["executionId"]  # distinct records
    assert len(store.queued_execs(a["id"])) == 2


def test_dst_fall_back_hour_fires_once(store, monkeypatch):
    """§4.3: an hour repeated by a DST fall-back fires once. The local wall
    clock rewinds an hour with no clock step at all, so the backward-step guard
    must not mistake it for one and re-evaluate the whole repeated hour."""
    from datetime import datetime, timedelta, timezone
    from conftest import make_version

    # 2026-11-01 America/New_York: 01:00-01:59 EDT runs, then repeats as EST.
    local = _Clock(datetime(2026, 11, 1, 0, 55))
    utc = _Clock(datetime(2026, 11, 1, 4, 55, tzinfo=timezone.utc))

    from autowright.engine import Engine
    from autowright.scheduler import Scheduler

    sched = Scheduler(store, Engine(store), clock=local, utc_clock=utc)
    fires = _record_fires(monkeypatch)
    store.create_automation(make_version(), "Nightly", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "30 1 * * *"}])

    # walk local time through the repeated hour; UTC only ever moves forward
    for local_now in [datetime(2026, 11, 1, 0, 55),   # baseline, EDT
                      datetime(2026, 11, 1, 1, 35),   # 01:30 EDT — the firing
                      datetime(2026, 11, 1, 1, 5),    # clock fell back to EST
                      datetime(2026, 11, 1, 1, 35),   # 01:30 again, now EST
                      datetime(2026, 11, 1, 2, 5)]:
        local.now = local_now
        sched._tick()
        utc.now += timedelta(minutes=30)

    assert len(fires) == 1, f"the repeated hour must fire once, fired {len(fires)}"


def test_backward_clock_step_still_recovers(store, monkeypatch):
    """§6: a genuine backward step (NTP) must not silence a trigger until the
    wall clock re-reaches the old baseline — UTC moves back too, unlike DST."""
    from datetime import datetime, timedelta, timezone
    from conftest import make_version

    local = _Clock(datetime(2026, 7, 10, 9, 55))
    utc = _Clock(datetime(2026, 7, 10, 13, 55, tzinfo=timezone.utc))

    from autowright.engine import Engine
    from autowright.scheduler import Scheduler

    sched = Scheduler(store, Engine(store), clock=local, utc_clock=utc)
    fires = _record_fires(monkeypatch)
    store.create_automation(make_version(), "Hourly", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "0 * * * *"}])

    sched._tick()  # baseline at 09:55
    # NTP yanks the clock back an hour — both readings move back together
    local.now = datetime(2026, 7, 10, 8, 55)
    utc.now -= timedelta(hours=1)
    sched._tick()
    assert fires == []
    # the next real occurrence still fires rather than being swallowed
    local.now = datetime(2026, 7, 10, 9, 5)
    utc.now += timedelta(minutes=10)
    sched._tick()
    assert len(fires) == 1


def test_deleted_version_queue_entry_finishes_with_note(store, monkeypatch):
    """§6 drain LookupError path: a queued entry whose admitted version was
    deleted (§7 restore/rollback) can never execute — it finishes `skipped`
    with the version-gone note instead of blocking the queue forever."""
    from autowright import listeners as li_mod
    from autowright.firing import drain_queue, fire_trigger
    from conftest import make_version

    notified = []
    monkeypatch.setattr(li_mod, "notify_busy", notified.append)

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Chat", None)
    a["max_queued"] = 10  # §4.1: queueing is opt-in — the default 0 would skip
    a["_live"] = {"blocking"}
    fire_trigger(store, engine, a, _discord_trig(), payload=_payload("Dave"))
    entry = store.queued_execs(a["id"])[0]
    entry["version"] = 99  # the version this firing was admitted against is gone

    a["_live"] = set()
    drain_queue(store, engine, a["id"])

    h = store.execs[entry["id"]]
    assert h["status"] == "skipped"
    assert h["note"] == "version v99 no longer exists"
    assert not engine.is_live(entry["id"])  # never executed
    assert store.queued_execs(a["id"]) == []  # the queue isn't blocked behind it
    assert [p["sender"] for p in notified] == ["Dave"]  # sender still answered


# ---------- the real tick loop thread (§6) ----------

def test_loop_thread_survives_a_failing_tick(store, monkeypatch, caplog):
    """§6: one bad tick is logged and never kills the scheduler thread —
    a persistent failure here would silently turn every trigger off."""
    import logging
    import threading
    import time
    from datetime import datetime

    from autowright import scheduler as sched_mod
    from autowright.engine import Engine
    from autowright.scheduler import Scheduler

    monkeypatch.setattr(sched_mod, "TICK_S", 0.01)
    calls = {"n": 0}

    def clock():
        calls["n"] += 1
        if calls["n"] == 2:  # the ctor takes call 1; the first tick raises
            raise RuntimeError("bad tick")
        return datetime.now()

    engine = Engine(store)
    sched = Scheduler(store, engine, clock=clock)
    with caplog.at_level(logging.ERROR, logger="autowright.scheduler"):
        sched.start()
        t0 = time.time()
        while calls["n"] < 4:  # ticks keep coming after the failing one
            assert time.time() - t0 < 30
            time.sleep(0.01)
        sched.stop()
    for t in threading.enumerate():
        if t.name == "ad-scheduler":
            t.join(timeout=10)
            assert not t.is_alive()
    assert any("scheduler tick failed" in r.message for r in caplog.records)


def test_disabled_one_shot_elapsed_is_consumed_unfired(store, monkeypatch):
    """§4.3: a one-shot whose moment passes while the trigger is off is
    consumed without firing — a spent one-shot never lingers."""
    from datetime import datetime
    from conftest import make_version

    clock = _Clock(datetime(2026, 7, 10, 8, 5))
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    a = store.create_automation(make_version(), "OffShot", None, triggers=[
        {"id": "tt", "kind": "time", "enabled": False, "at": "2026-07-10T08:00"}])
    sched._tick()
    assert fires == []
    assert a["triggers"] == []  # consumed even though it was off


def test_one_shot_missed_before_baseline_consumed_unfired(store, monkeypatch):
    """§4.3: a one-shot whose moment passed while the backend was down (before
    the startup baseline) is consumed, never fired — no catch-up queue."""
    from datetime import datetime
    from conftest import make_version

    clock = _Clock(datetime(2026, 7, 10, 9, 0))  # backend wakes past the moment
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    a = store.create_automation(make_version(), "MissedShot", None, triggers=[
        {"id": "tt", "kind": "time", "enabled": True, "at": "2026-07-10T08:00"}])
    sched._tick()
    assert fires == []
    assert a["triggers"] == []


def test_retention_sweep_runs_hourly_and_publishes(store, monkeypatch):
    """§5: the hourly retention pass deletes expired records and publishes
    automation.changed only when something was actually removed."""
    from datetime import datetime, timedelta
    from autowright import scheduler as sched_mod
    from conftest import make_version

    clock = _Clock(datetime(2026, 7, 10, 9, 0))
    engine, sched = _mk_clocked(store, clock)
    a = store.create_automation(make_version(), "Sweep", None)
    h_old = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    h_old["started_at"] = (datetime(2026, 7, 10) - timedelta(days=120)).isoformat(timespec="seconds")
    store.update_execution(h_old)
    store.settings["days"] = 90
    events = []
    monkeypatch.setattr(sched_mod.hub, "publish",
                        lambda ev, **kw: events.append(ev))
    def sweep_done():
        # §6: the sweep's deletes run on their own worker thread — the tick
        # only starts it; tests join so the assertions see the finished pass.
        t = sched._retention_thread
        if t is not None:
            t.join(timeout=10)

    sched._tick()
    sweep_done()
    assert h_old["id"] in store.execs  # not an hour yet — nothing swept
    clock.now += timedelta(hours=2)
    sched._tick()
    sweep_done()
    assert h_old["id"] not in store.execs
    assert "automation.changed" in events
    events.clear()
    clock.now += timedelta(hours=2)
    sched._tick()  # nothing left to remove → no publish
    sweep_done()
    assert "automation.changed" not in events


def test_fire_trigger_version_gone_reports_not_started(store):
    """§6: a firing whose current version no longer resolves starts nothing
    and returns False — the tick must survive it, not crash the loop."""
    from autowright.firing import fire_trigger
    from conftest import make_version

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "GoneVersion", None)
    a["versions"].clear()  # the version the firing would resolve is gone
    before = len(store.execs)
    t = {"id": "t1", "kind": "cron", "enabled": True, "expression": "0 8 * * *"}
    assert fire_trigger(store, engine, a, t) is False
    # §7: nothing started, but the occurrence leaves its skipped record — a
    # silent swallow would fire and vanish every occurrence forever.
    assert len(store.execs) == before + 1
    (h,) = [x for x in store.execs.values()]
    assert h["status"] == "skipped"
    assert h["note"] == f"version v{a['current_version']} no longer exists"
    assert a["_live"] == set()


def test_trigger_off_cancels_unmatched_imessage_entries(store, monkeypatch):
    """§6: an imessage queue entry matches by sender ↔ the trigger's `from`
    (case-insensitive) — turning that trigger off cancels only its entries;
    an unknown automation id is a silent no-op."""
    from autowright import listeners as li_mod
    from autowright.firing import cancel_unmatched_queue, fire_trigger
    from conftest import make_version

    notified = []
    monkeypatch.setattr(li_mod, "notify_busy", notified.append)

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Texts", None)
    a["max_queued"] = 10  # §4.1: queueing is opt-in — the default 0 would skip
    keep = {"id": "tk", "kind": "imessage", "enabled": True, "from": "+15551234567"}
    drop = {"id": "td", "kind": "imessage", "enabled": True, "from": "+19998887777"}
    a["triggers"] = [keep, drop]
    a["_live"] = {"blocking"}
    fire_trigger(store, engine, a, keep,
                 payload={"kind": "imessage", "sender": "+15551234567", "text": "run"})
    fire_trigger(store, engine, a, drop,
                 payload={"kind": "imessage", "sender": "+19998887777", "text": "run"})
    assert len(store.queued_execs(a["id"])) == 2

    a["triggers"][1]["enabled"] = False
    cancel_unmatched_queue(store, engine, a["id"])
    q = store.queued_execs(a["id"])
    assert [h["trigger_payload"]["sender"] for h in q] == ["+15551234567"]
    assert [p["sender"] for p in notified] == ["+19998887777"]

    cancel_unmatched_queue(store, engine, "no-such-automation")  # silent no-op
    assert len(store.queued_execs(a["id"])) == 1


def test_drain_queue_slot_race_leaves_entry_queued(store, monkeypatch):
    """§6 drain race: at_capacity said free but start answers RuntimeError (a
    concurrent starter took the slot) — drain returns and the entry stays
    queued for the next finish; an entry with no queued_at counts as fresh,
    never stale."""
    from autowright.firing import drain_queue, fire_trigger
    from conftest import make_version

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Race", None)
    a["max_queued"] = 10  # §4.1: queueing is opt-in — the default 0 would skip
    a["_live"] = {"blocking"}
    fire_trigger(store, engine, a, _discord_trig(), payload=_payload("Dave"))
    head = store.queued_execs(a["id"])[0]
    head["queued_at"] = None  # §6: no timestamp reads as freshly queued

    a["_live"] = set()  # capacity looks free…
    monkeypatch.setattr(engine, "start",
                        lambda *args, **kw: (_ for _ in ()).throw(RuntimeError("already executing")))
    drain_queue(store, engine, a["id"])  # …but the slot is taken under us
    assert store.execs[head["id"]]["status"] == "queued"  # still waiting


# ---------- §6 manual queue admission (§19 queue: true) ----------

def test_queue_manual_starts_when_a_slot_is_free(store):
    """§6 manual admission: `queue: true` with a free slot simply starts —
    queueing beside a free slot would promote on the next drain anyway."""
    import time

    from autowright.firing import queue_manual

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Manual Q", None)
    h, queued = queue_manual(store, engine, a, "manual")
    assert queued is False
    t0 = time.time()
    while engine.is_live(h["id"]):
        assert time.time() - t0 < 30
        time.sleep(0.05)
    assert store.exec_full(h["id"])["status"] == "succeeded"


def test_queue_manual_admits_at_capacity_and_drains(store):
    """§6 manual admission: at capacity the entry queues pinned to the current
    version with no payload (trigger `manual`); a freed slot promotes it like
    any firing."""
    import time

    from autowright.firing import drain_queue, queue_manual

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Manual Q", None)
    a["max_queued"] = 10  # §4.1: queueing is opt-in — the default 0 would skip
    a["_live"] = {"blocking"}
    h, queued = queue_manual(store, engine, a, "manual")
    assert queued is True
    assert h["status"] == "queued" and h["trigger"] == "manual"
    assert h["trigger_payload"] is None and h["queued_at"]
    assert h["kind"] == "version" and h["version"] == a["current_version"]

    a["_live"] = set()  # the blocking execution finished
    drain_queue(store, engine, a["id"])
    t0 = time.time()
    while engine.is_live(h["id"]):
        assert time.time() - t0 < 30
        time.sleep(0.05)
    assert store.exec_full(h["id"])["status"] == "succeeded"


def test_queue_manual_has_no_ttl(store, monkeypatch):
    """§6 staleness is message-firings-only: a manual entry the user chose to
    queue is promoted no matter how long it waited — evaporating that choice
    would be a silent no-op."""
    import time

    from autowright import firing as firing_mod
    from autowright.firing import drain_queue, queue_manual

    monkeypatch.setattr(firing_mod, "QUEUE_TTL_S", -1.0)  # everything is stale
    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Manual Q", None)
    a["max_queued"] = 10  # §4.1: queueing is opt-in — the default 0 would skip
    a["_live"] = {"blocking"}
    h, _queued = queue_manual(store, engine, a, "manual")

    a["_live"] = set()
    drain_queue(store, engine, a["id"])
    t0 = time.time()
    while engine.is_live(h["id"]):
        assert time.time() - t0 < 30
        time.sleep(0.05)
    assert store.exec_full(h["id"])["status"] == "succeeded"  # ran, never skipped


def test_queue_manual_full_queue_and_draft_refuse_with_no_record(store):
    """§6: a manual queue request past the cap — or for a Draft — raises (the
    API's 409) and writes no record; the user is present to decide, unlike a
    message sender."""
    import pytest

    from autowright.firing import fire_trigger, queue_manual

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Manual Q", None)
    a["max_queued"] = 1
    a["_live"] = {"blocking"}
    fire_trigger(store, engine, a, _discord_trig(), payload=_payload())  # fills the queue
    before = set(store.execs)
    with pytest.raises(RuntimeError, match=r"the queue is full \(1 waiting\)"):
        queue_manual(store, engine, a, "manual")
    with pytest.raises(RuntimeError, match="Draft"):
        queue_manual(store, engine, a, "manual", version_label="draft")
    with pytest.raises(LookupError):
        queue_manual(store, engine, a, "manual", version_label="v99")
    assert set(store.execs) == before  # no record any which way


def test_trigger_edits_never_cancel_a_manual_entry(store):
    """§6: cancel_unmatched_queue matches payload-carrying entries only — a
    manual entry was not admitted by any trigger and survives trigger edits."""
    from autowright.firing import cancel_unmatched_queue, queue_manual

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Manual Q", None)
    a["max_queued"] = 10  # §4.1: queueing is opt-in — the default 0 would skip
    a["_live"] = {"blocking"}
    h, _queued = queue_manual(store, engine, a, "manual")
    cancel_unmatched_queue(store, engine, a["id"])  # no message triggers enabled at all
    assert store.execs[h["id"]]["status"] == "queued"  # still waiting


def test_one_automations_firing_failure_never_stops_the_tick(store, monkeypatch):
    """§6: a per-automation guard - one broken automation must not silence
    every automation after it in the tick's list."""
    from datetime import datetime

    from autowright import scheduler as sched_mod
    from conftest import make_version

    clock = _Clock(datetime(2026, 7, 10, 7, 59))
    engine, sched = _mk_clocked(store, clock)
    fired = []

    def boom(store, engine, a, t):
        if a["name"] == "Bad":
            raise OSError("disk on fire")
        fired.append(a["name"])
        return True

    monkeypatch.setattr(sched_mod, "fire_trigger", boom)
    trig = [{"id": "t1", "kind": "cron", "enabled": True, "expression": "0 8 * * *"}]
    for name in ("Bad", "Good"):
        store.create_automation(make_version(), name, None,
                                triggers=[dict(t) for t in trig])
    sched._tick()  # baselines
    clock.now = datetime(2026, 7, 10, 8, 1)
    sched._tick()
    assert fired == ["Good"]
    # the failing automation keeps its baseline - the prune never dropped it
    assert len(sched._baseline) == 2


def test_fire_trigger_finishes_a_record_left_behind_by_a_failed_start(store):
    """§4.6: `start` may raise after the record exists - left `executing` it
    would hold the §6 slot and 409 every later firing."""
    from autowright.firing import fire_trigger

    class Boom:
        @staticmethod
        def at_capacity(a):
            return False

        def start(self, a, trigger, version_label=None, payload=None, adopt=None):
            store.create_execution(a, "version", a["current_version"], trigger, steps=[])
            raise OSError("disk on fire")

    a = store.create_automation(make_version(), "Half Started", None)
    assert fire_trigger(store, Boom(), a, {"id": "t1", "kind": "cron"}) is False
    assert a["_live"] == set()
    h = next(iter(store.execs.values()))
    assert h["status"] == "skipped"
    assert h["note"] == "the execution couldn't be started"
    # and the automation can fire again
    assert fire_trigger(store, Boom(), a, {"id": "t1", "kind": "cron"}) is False
    assert a["_live"] == set()


def test_scheduler_warns_once_on_timezone_rewind(store, monkeypatch, caplog):
    """§4.3: a wall rewind too large for DST (system-timezone change) is
    handled like fall-back but logs one diagnosable warning — and does not
    double-fire the rewound span."""
    import logging
    from datetime import datetime, timedelta, timezone

    from autowright.engine import Engine
    from autowright.scheduler import Scheduler

    class Clock:
        def __init__(self, now):
            self.now = now

        def __call__(self):
            return self.now

    local = Clock(datetime(2026, 7, 10, 15, 0))
    utc = Clock(datetime(2026, 7, 10, 19, 0, tzinfo=timezone.utc))
    sched = Scheduler(store, Engine(store), clock=local, utc_clock=utc)
    fires = []
    from autowright import scheduler as sched_mod
    monkeypatch.setattr(sched_mod, "fire_trigger",
                        lambda store, engine, a, t: fires.append(t["id"]) or True)
    store.create_automation(make_version(), "Zoney", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "0 13 * * *"},
        # a disabled trigger could never fire — it must not add rewind noise
        {"id": "t2", "kind": "cron", "enabled": False, "expression": "0 13 * * *"}])

    sched._tick()  # baseline at 15:00
    # user moves the Mac three zones west: wall rewinds, UTC keeps advancing
    local.now = datetime(2026, 7, 10, 12, 1)
    utc.now += timedelta(minutes=1)
    with caplog.at_level(logging.WARNING, logger="autowright.scheduler"):
        sched._tick()
        sched._tick()
    warnings = [r for r in caplog.records if "timezone change" in r.message]
    assert len(warnings) == 1          # once per rewind, not once per tick
    assert fires == []                 # the 13:00 in the rewound span stays unfired
    # the clock catching back up clears the state and normal firing resumes
    local.now = datetime(2026, 7, 11, 13, 1)
    utc.now += timedelta(days=1, hours=1)
    sched._tick()
    assert fires == ["t1"]


def test_overdue_sweep_notifies_after_two_consecutive_sweeps(store, monkeypatch):
    """§6 overdue sweep: hourly; one notification per overdue stretch, only
    after two consecutive sweeps observe it — a boot into a stale morning
    (one sweep's worth of overdue) never cries."""
    from datetime import datetime

    from autowright import notify
    from conftest import make_version

    clock = _Clock(datetime(2026, 7, 10, 9, 0))
    engine, sched = _mk_clocked(store, clock)
    _record_fires(monkeypatch)  # keep ticks from actually executing anything
    posted = []
    monkeypatch.setattr(notify, "post", lambda title, body: posted.append((title, body)))
    a = store.create_automation(make_version(), "Dead Job", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "0 8 * * *",
         "source": "user"}])
    a["created_at"] = "2026-07-01T08:00:00"  # never ran; many 8:00s already missed
    # §4.3: on since then too — a fresh enable stamp would (rightly) clear it.
    a["triggers"][0]["enabledAt"] = datetime(2026, 7, 1, 8, 0).astimezone().isoformat()

    sched._tick()  # within the first hour — no sweep yet
    assert posted == []
    clock.now = datetime(2026, 7, 10, 10, 1)
    sched._tick()  # sweep 1: overdue observed — streak 1, still silent
    assert posted == []
    clock.now = datetime(2026, 7, 10, 11, 2)
    sched._tick()  # sweep 2: two consecutive observations → one notification
    assert posted == [("Dead Job", "Scheduled executions are being missed.")]
    clock.now = datetime(2026, 7, 10, 12, 3)
    sched._tick()  # still overdue — never a second notification this stretch
    assert len(posted) == 1
    # a real run clears the state and re-arms the notification
    a["_last_exec_at"] = "2026-07-10T12:00:00"
    clock.now = datetime(2026, 7, 10, 13, 4)
    sched._tick()
    assert len(posted) == 1
    assert a["id"] not in sched._overdue_streak
    assert a["id"] not in sched._overdue_notified


# ---------- §6 runIfMissed: the wake catch-up opt-out (§4.3) ----------

def _drop_records(store, automation_id):
    return [h for h in store.execs.values()
            if h["automation_id"] == automation_id and h["status"] == "skipped"]


def test_run_if_missed_off_drops_slept_through_cron(store, monkeypatch):
    """§6: a cron with runIfMissed false never fires late: the slept-through
    span is dropped, the baseline advances to now, one skipped record with the
    drop note is written, and the next natural occurrence fires normally."""
    from datetime import datetime
    from autowright.scheduler import drop_note
    from conftest import make_version

    clock = _Clock(datetime(2026, 7, 10, 10, 30))
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    a = store.create_automation(make_version(), "NoCatchup", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "0 * * * *",
         "source": "user", "runIfMissed": False}])
    sched._tick()  # baseline 10:30
    clock.now = datetime(2026, 7, 10, 13, 31)  # slept through 11:00, 12:00, 13:00
    sched._tick()
    assert fires == []  # nothing fired late
    assert sched._baseline[(a["id"], "t1")] == clock.now
    recs = _drop_records(store, a["id"])
    assert len(recs) == 1
    assert recs[0]["note"] == drop_note()
    assert recs[0]["trigger"] == "cron"
    assert recs[0]["duration_ms"] == 0
    sched._tick()
    assert fires == [] and len(_drop_records(store, a["id"])) == 1  # no re-drop
    clock.now = datetime(2026, 7, 10, 14, 0, 5)  # the next occurrence, seen 5 s late
    sched._tick()
    assert len(fires) == 1  # normal firing resumes


def test_run_if_missed_off_still_fires_within_grace(store, monkeypatch):
    """§6 grace window: an occurrence noticed a tick or two late is not a
    miss, it fires; only a span older than the grace window is dropped."""
    from datetime import datetime, timedelta
    from autowright import scheduler as sched_mod
    from conftest import make_version

    clock = _Clock(datetime(2026, 7, 10, 8, 59, 50))
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    a = store.create_automation(make_version(), "Grace", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "0 9 * * *",
         "source": "user", "runIfMissed": False}])
    sched._tick()
    clock.now = datetime(2026, 7, 10, 9, 0, 40)  # 40 s late: inside the 60 s grace
    sched._tick()
    assert len(fires) == 1
    assert _drop_records(store, a["id"]) == []
    # a wake just past the grace window drops instead
    clock.now = datetime(2026, 7, 11, 9, 0) + timedelta(seconds=sched_mod.GRACE_S + 5)
    sched._tick()
    assert len(fires) == 1
    assert len(_drop_records(store, a["id"])) == 1


def test_run_if_missed_off_wake_with_fresh_occurrence_fires_once(store, monkeypatch):
    """§6: waking seconds after a fresh occurrence with older ones slept
    through fires exactly once (the fresh one) and writes no drop record."""
    from datetime import datetime
    from conftest import make_version

    clock = _Clock(datetime(2026, 7, 10, 8, 30))
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    a = store.create_automation(make_version(), "FreshWake", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "0 * * * *",
         "source": "user", "runIfMissed": False}])
    sched._tick()
    clock.now = datetime(2026, 7, 10, 12, 0, 10)  # slept through 9, 10, 11; 12:00 is fresh
    sched._tick()
    assert len(fires) == 1
    assert _drop_records(store, a["id"]) == []
    assert sched._baseline[(a["id"], "t1")] == clock.now


def test_run_if_missed_off_one_shot_consumed_unfired_with_record(store, monkeypatch):
    """§4.3/§6: a slept-through one-shot with runIfMissed false is consumed
    unfired, leaving the skipped drop record as its only trace."""
    from datetime import datetime
    from conftest import make_version

    clock = _Clock(datetime(2026, 7, 10, 9, 0))
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    a = store.create_automation(make_version(), "ShotDrop", None, triggers=[
        {"id": "tt", "kind": "time", "enabled": True, "at": "2026-07-10T10:00",
         "runIfMissed": False}])
    sched._tick()
    clock.now = datetime(2026, 7, 10, 14, 0)
    sched._tick()
    assert fires == []
    assert a["triggers"] == []  # consumed
    recs = _drop_records(store, a["id"])
    assert len(recs) == 1 and recs[0]["trigger"] == "time"


def test_run_if_missed_default_true_keeps_catch_up(store, monkeypatch):
    """§4.3 default: a trigger without the field (the pre-field shape) and one
    with runIfMissed true both keep the §6 one-catch-up-per-wake behavior."""
    from datetime import datetime
    from conftest import make_version

    clock = _Clock(datetime(2026, 7, 10, 10, 30))
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    a = store.create_automation(make_version(), "Default", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "0 * * * *", "source": "user"},
        {"id": "t2", "kind": "cron", "enabled": True, "expression": "30 * * * *", "source": "user",
         "runIfMissed": True}])
    sched._tick()
    clock.now = datetime(2026, 7, 10, 13, 31)
    sched._tick()
    assert len(fires) == 1  # one catch-up for the automation
    assert _drop_records(store, a["id"]) == []


def test_run_if_missed_drop_is_silent_when_another_trigger_catches_up(store, monkeypatch):
    """§6: when a runIfMissed-true trigger of the same automation catches up
    in the same tick, the opted-out trigger's drop writes no record; the
    execution covers it and the one-per-wake rule holds."""
    from datetime import datetime
    from conftest import make_version

    clock = _Clock(datetime(2026, 7, 10, 10, 30))
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    a = store.create_automation(make_version(), "Mixed", None, triggers=[
        {"id": "keep", "kind": "cron", "enabled": True, "expression": "0 * * * *", "source": "user"},
        {"id": "drop", "kind": "cron", "enabled": True, "expression": "15 * * * *", "source": "user",
         "runIfMissed": False}])
    sched._tick()
    clock.now = datetime(2026, 7, 10, 13, 31)
    sched._tick()
    assert fires == [(a["id"], "keep")]
    assert _drop_records(store, a["id"]) == []
    assert sched._baseline[(a["id"], "drop")] == clock.now


def test_run_if_missed_off_cron_never_flags_overdue(store):
    """§4.1: a cron that opted out of the wake catch-up never makes the
    automation overdue; its misses are chosen, not a problem."""
    from datetime import datetime
    from autowright import triggers as triggerlib

    base = datetime(2026, 7, 10, 8, 0)
    now = datetime(2026, 7, 12, 8, 0)  # two daily occurrences passed unrun
    on = [{"kind": "cron", "enabled": True, "expression": "0 9 * * *", "source": "user"}]
    off = [{**on[0], "runIfMissed": False}]
    assert triggerlib.is_overdue(on, base, now) is True
    assert triggerlib.is_overdue(off, base, now) is False


def test_run_if_missed_off_disabled_trigger_never_drops(store, monkeypatch):
    """§6: the enabled gate runs before the opt-out check, so a trigger that is
    off writes no drop record; its occurrences simply never fire, and
    re-enabling resumes normal firing with no late catch-up."""
    from datetime import datetime
    from conftest import make_version

    clock = _Clock(datetime(2026, 7, 10, 10, 30))
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    a = store.create_automation(make_version(), "OffOptOut", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": False, "expression": "0 * * * *",
         "source": "user", "runIfMissed": False}])
    # §4.3 stamp_enabled only adds `enabledAt`; an entry created off stays off.
    assert a["triggers"][0]["enabled"] is False
    sched._tick()  # baseline 10:30
    clock.now = datetime(2026, 7, 10, 13, 31)  # 11:00, 12:00, 13:00 passed while off
    sched._tick()
    assert fires == [] and _drop_records(store, a["id"]) == []
    assert sched._baseline[(a["id"], "t1")] == clock.now
    a["triggers"][0]["enabled"] = True
    clock.now = datetime(2026, 7, 10, 13, 45)
    sched._tick()
    assert fires == [] and _drop_records(store, a["id"]) == []  # no late catch-up
    clock.now = datetime(2026, 7, 10, 14, 0, 5)
    sched._tick()
    assert len(fires) == 1  # normal firing resumes after re-enable


def test_run_if_missed_drop_records_are_per_automation(store, monkeypatch):
    """§6: two automations slept through in the same tick each get their own
    single drop record; the one-record rule is per automation, not global."""
    from datetime import datetime
    from autowright.scheduler import drop_note
    from conftest import make_version

    clock = _Clock(datetime(2026, 7, 10, 10, 40))
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    a = store.create_automation(make_version(), "DropA", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "0 * * * *",
         "source": "user", "runIfMissed": False}])
    b = store.create_automation(make_version(), "DropB", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "30 * * * *",
         "source": "user", "runIfMissed": False}])
    sched._tick()
    clock.now = datetime(2026, 7, 10, 13, 41)
    sched._tick()
    assert fires == []
    for auto in (a, b):
        recs = _drop_records(store, auto["id"])
        assert len(recs) == 1
        assert recs[0]["note"] == drop_note()
        assert recs[0]["trigger"] == "cron"


def test_record_drop_bails_for_automation_deleted_mid_tick(store):
    """§6: the tick evaluates a snapshot taken outside the lock, so a DELETE
    landing mid-tick can leave _record_drop holding a stale dict; the identity
    guard writes no record for a gone automation."""
    from datetime import datetime
    from conftest import make_version

    engine, sched = _mk_clocked(store, _Clock(datetime(2026, 7, 10, 10, 30)))
    a = store.create_automation(make_version(), "GoneMidTick", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "0 * * * *",
         "source": "user", "runIfMissed": False}])
    t = a["triggers"][0]
    store.delete_automation(a)
    sched._record_drop(a, t)  # the mid-tick race, called directly
    assert _drop_records(store, a["id"]) == [] and store.execs == {}


def test_fire_trigger_logs_a_start_bug_instead_of_a_missing_version(store, caplog):
    """§6/§7: KeyError and IndexError are LookupErrors too — a bug inside
    `start` is logged, never dressed up as the "version no longer exists"
    record a genuinely unresolvable version leaves."""
    import logging

    from autowright.firing import fire_trigger

    class Boom:
        @staticmethod
        def at_capacity(a):
            return False

        def start(self, a, trigger, version_label=None, payload=None, adopt=None):
            raise KeyError("steps")

    a = store.create_automation(make_version(), "StartBug", None)
    with caplog.at_level(logging.ERROR):
        assert fire_trigger(store, Boom(), a, {"id": "t1", "kind": "cron"}) is False
    assert store.execs == {}  # no phantom "no longer exists" record
    assert "firing cron" in caplog.text and "KeyError" in caplog.text


def test_firing_in_the_delete_window_leaves_no_record(store):
    """§6: a message firing or a manual queue request landing while the §19
    DELETE cancels the automation's live executions is refused without a record
    — the automation is gone a moment later, and the entry would outlive it."""
    import pytest

    from autowright.firing import fire_trigger, queue_manual

    engine, sched = _mk(store)
    a = store.create_automation(make_version(), "Deleting", None)
    a["max_queued"] = 5        # queueing would otherwise admit the firing
    a["_live"] = {"blocking"}  # at capacity: the branch that mints the records
    a["_deleting"] = True
    assert fire_trigger(store, engine, a, _discord_trig(), payload=_payload()) is False
    with pytest.raises(RuntimeError, match="being deleted"):
        queue_manual(store, engine, a, "manual")
    assert store.execs == {}


# ---------- §4.3 interval triggers: every `every` since the last run ----------

def _interval_auto(store, name, *, every="PT1H", created="2026-07-10T12:00:00", **fields):
    """An automation with one enabled interval trigger anchored at `created` —
    both the §4.1 run baseline and the §4.3 enable stamp, pinned to the fake
    clock's timeline rather than the wall clock the write paths stamp."""
    from conftest import make_version

    a = store.create_automation(make_version(), name, None, triggers=[
        {"id": "t1", "kind": "interval", "enabled": True, "every": every,
         "source": "user", **fields}])
    a["created_at"] = created
    a["triggers"][0]["enabledAt"] = created
    return a


def test_interval_fires_every_span_since_the_last_run(store, monkeypatch):
    """§4.3 interval semantics: an automation that never ran anchors at its
    created_at, fires one `every` later, and then re-anchors on that run's
    start — never back on the created_at grid."""
    from datetime import datetime

    clock = _Clock(datetime(2026, 7, 10, 12, 0))
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    a = _interval_auto(store, "Hourly")

    sched._tick()  # baseline 12:00 — created_at, nothing due
    assert fires == []
    clock.now = datetime(2026, 7, 10, 12, 59, 59)
    sched._tick()
    assert fires == []  # nothing before created_at + every
    clock.now = datetime(2026, 7, 10, 13, 0, 20)  # the 13:00 occurrence
    sched._tick()
    assert len(fires) == 1
    sched._tick()
    assert len(fires) == 1  # the baseline advanced — it never re-fires

    # that firing started a run: the anchor moves to its start, so the next
    # occurrence is one `every` after S — not created_at + 2 × every
    a["_last_exec_at"] = "2026-07-10T13:00:20"
    clock.now = datetime(2026, 7, 10, 14, 0)  # created_at + 2 × every
    sched._tick()
    assert len(fires) == 1
    clock.now = datetime(2026, 7, 10, 14, 0, 20)  # S + every
    sched._tick()
    assert len(fires) == 2


def test_a_manual_run_pushes_the_next_interval_occurrence_back(store, monkeypatch):
    """§4.3: the run baseline is the latest real execution whatever started it
    — a manual Execute now pushes the next interval occurrence back a full
    `every`."""
    from datetime import datetime

    clock = _Clock(datetime(2026, 7, 10, 12, 0))
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    a = _interval_auto(store, "Manual push")
    sched._tick()  # baseline 12:00

    h = store.create_execution(a, "version", a["current_version"], "manual", steps=[])
    # the record is stamped from the wall clock — re-stamp it onto the fake
    # clock's timeline, the one the tick reasons about
    h["started_at"] = "2026-07-10T12:30:00"
    a["_last_exec_at"] = h["started_at"]

    clock.now = datetime(2026, 7, 10, 13, 0)  # created_at + every, but S + 30 min
    sched._tick()
    assert fires == []
    clock.now = datetime(2026, 7, 10, 13, 30)  # S + every
    sched._tick()
    assert len(fires) == 1


def test_interval_occurrence_during_a_long_run_is_skipped_until_the_next(store):
    """§4.3/§6: a run longer than `every` has its next occurrence fall while it
    is still executing — the one-execution-at-a-time skip writes the usual
    record, and the trigger waits for anchor + 2 × every."""
    from datetime import datetime

    clock = _Clock(datetime(2026, 7, 10, 12, 0))
    engine, sched = _mk_clocked(store, clock)
    a = _interval_auto(store, "Long run")
    # a run in progress since 12:00 — the anchor is its start (§4.3)
    h = store.create_execution(a, "version", a["current_version"], "manual", steps=[])
    h["started_at"] = "2026-07-10T12:00:00"
    a["_last_exec_at"] = h["started_at"]
    assert a["_live"] == {h["id"]}

    sched._tick()  # baseline 12:00
    assert _drop_records(store, a["id"]) == []
    clock.now = datetime(2026, 7, 10, 13, 0)  # anchor + every, still executing
    sched._tick()
    skips = _drop_records(store, a["id"])
    assert len(skips) == 1
    assert skips[0]["note"] == "previous execution still in progress"
    assert skips[0]["trigger"] == "interval"  # §4.5: the stored machine kind

    clock.now = datetime(2026, 7, 10, 13, 59)
    sched._tick()
    assert len(_drop_records(store, a["id"])) == 1  # nothing between the grid points
    clock.now = datetime(2026, 7, 10, 14, 0)  # anchor + 2 × every
    sched._tick()
    assert len(_drop_records(store, a["id"])) == 2


def test_run_if_missed_off_drops_a_slept_through_interval_span(store, monkeypatch):
    """§6/§4.3: an interval with runIfMissed false never fires late — the
    slept-through span is dropped with the usual record, and the trigger waits
    for the next grid point after the wake."""
    from datetime import datetime

    from autowright.scheduler import drop_note

    clock = _Clock(datetime(2026, 7, 10, 12, 0))
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    a = _interval_auto(store, "No catch-up", every="PT3H", runIfMissed=False)

    sched._tick()  # baseline 12:00
    clock.now = datetime(2026, 7, 10, 17, 0)  # slept through 15:00, woke two hours late
    sched._tick()
    assert fires == []
    recs = _drop_records(store, a["id"])
    assert len(recs) == 1
    assert recs[0]["note"] == drop_note() and recs[0]["trigger"] == "interval"

    clock.now = datetime(2026, 7, 10, 18, 0)  # anchor + 2 × every — a live moment
    sched._tick()
    assert len(fires) == 1
    assert len(_drop_records(store, a["id"])) == 1  # no second drop


def test_run_if_missed_true_catches_a_slept_through_interval_up_once(store, monkeypatch):
    """§6: the default keeps the one-catch-up-per-wake behavior for an interval
    exactly as for a cron — one execution, no drop record."""
    from datetime import datetime

    clock = _Clock(datetime(2026, 7, 10, 12, 0))
    engine, sched = _mk_clocked(store, clock)
    fires = _record_fires(monkeypatch)
    a = _interval_auto(store, "Catch-up", every="PT3H")

    sched._tick()  # baseline 12:00
    clock.now = datetime(2026, 7, 10, 17, 0)  # the same slept-through span
    sched._tick()
    assert len(fires) == 1
    assert _drop_records(store, a["id"]) == []
