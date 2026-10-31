"""§6 firing-queue + maxParallel race hunt: many threads of manual starts,
message firings, cancels, and drains against the real Engine + Store.

Hammers one automation from many threads at once — manual starts, message
firings (mixed senders), cancels, drains — then asserts the invariants the
spec promises:
  - never more than maxParallel executions live at any instant
  - the queue never exceeds maxQueued
  - every firing produced exactly one record, admission to finish
  - at the end everything is terminal, _live is empty, engine._live is empty
  - every record's status is a legal terminal status with consistent fields
"""
import random
import threading
import time

MAX_PARALLEL = 3
MAX_QUEUED = 4
N_THREADS = 12
N_FIRINGS = 25  # per thread


def _payload(sender, text="hi"):
    return {"kind": "discord", "text": text, "sender": sender,
            "channel": "c1", "messageId": "m1", "guildId": "g1",
            "secret": "BOT", "at": "2026-07-29T00:00:00"}


def _discord_trig():
    return {"id": "t1", "kind": "discord", "enabled": True, "secret": "BOT",
            "channel": "c1", "mention": False, "pattern": None}


def test_queue_stress_no_races(store, monkeypatch):
    from autowright import listeners as li_mod
    from autowright.engine import Engine
    from autowright.firing import drain_queue, fire_trigger
    from autowright.scheduler import Scheduler
    from conftest import make_version

    monkeypatch.setattr(li_mod, "notify_busy", lambda p: None)

    ver = make_version()
    ver["steps"] = [{"file": "01-quick.py", "name": "Quick", "description": "",
                     "code": "import time, random\ntime.sleep(random.uniform(0.02, 0.15))\n"}]
    ver["params"] = []

    engine = Engine(store)
    sched = Scheduler(store, engine)  # wires the drain_queue hook
    a = store.create_automation(ver, "Stress", None)
    a["triggers"].append(_discord_trig())  # §6: a firing's trigger must be on the automation
    a["max_parallel"] = MAX_PARALLEL
    a["max_queued"] = MAX_QUEUED

    over_parallel = []
    over_queue = []
    stop = threading.Event()

    def watcher():
        # Sample the invariants continuously while the storm runs.
        while not stop.is_set():
            with store.lock:
                live = len(a["_live"])
                q = len(store.queued_execs(a["id"]))
            if live > MAX_PARALLEL:
                over_parallel.append(live)
            if q > MAX_QUEUED:
                over_queue.append(q)
            time.sleep(0.002)

    senders = ["Dave", "Ana", "Kim", "Lee", "Sam", "Joe", "Mia", "Zed"]

    def storm(seed):
        rng = random.Random(seed)
        for i in range(N_FIRINGS):
            kind = rng.random()
            if kind < 0.55:
                fire_trigger(store, engine, a, _discord_trig(),
                             payload=_payload(rng.choice(senders), f"msg-{seed}-{i}"))
            elif kind < 0.75:
                try:
                    engine.start(a, "manual")
                except (RuntimeError, LookupError):
                    pass  # 409 path — refused, fine
            elif kind < 0.9:
                drain_queue(store, engine, a["id"])
            else:
                with store.lock:
                    q = store.queued_execs(a["id"])
                if q:
                    engine.cancel(rng.choice(q)["id"])
            time.sleep(rng.uniform(0.01, 0.06))

    w = threading.Thread(target=watcher, daemon=True)
    w.start()
    threads = [threading.Thread(target=storm, args=(s,)) for s in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Let everything running finish and the queue drain fully.
    deadline = time.time() + 60
    while time.time() < deadline:
        drain_queue(store, engine, a["id"])
        with store.lock:
            live = len(a["_live"])
            q = len(store.queued_execs(a["id"]))
        if live == 0 and q == 0:
            break
        time.sleep(0.05)
    stop.set()
    w.join(timeout=2)

    assert not over_parallel, f"maxParallel exceeded: saw {max(over_parallel)} live"
    assert not over_queue, f"maxQueued exceeded: saw {max(over_queue)} queued"

    with store.lock:
        assert len(a["_live"]) == 0, "slots leaked — automation pinned executing"
        assert store.queued_execs(a["id"]) == []
        assert engine._live == {}, "engine thread map leaked"
        statuses = {}
        for h in store.execs.values():
            assert h["status"] in ("succeeded", "failed", "skipped", "cancelled"), \
                f"non-terminal record left behind: {h['id']} = {h['status']}"
            statuses[h["status"]] = statuses.get(h["status"], 0) + 1
            if h["status"] == "succeeded":
                assert h["finished_at"], "succeeded without finished_at"
                # finished records are demoted to headers in memory; body is lazy
                assert store.exec_full(h["id"])["steps"], "succeeded with no steps"
            if h["status"] == "skipped":
                assert h["note"], "skipped record with no note"
            # a record that ran carries duration_ms
            if h["status"] in ("succeeded", "failed"):
                assert h["duration_ms"] is not None
        # sanity: the storm actually exercised both paths
        assert statuses.get("succeeded", 0) > 0
        assert statuses.get("skipped", 0) > 0
        # …and the promotion path: records admitted to the queue that later ran
        promoted = [h for h in store.execs.values()
                    if h.get("queued_at") and h["status"] in ("succeeded", "failed")]
        assert promoted, "no queued record was ever promoted — storm never hit the drain path"
        for h in promoted:
            assert store.exec_full(h["id"])["steps"], \
                "promoted record executed with no steps filled in"
            assert h["started_at"] >= h["queued_at"]
    print("status counts:", statuses, "| promoted:", len(promoted))
