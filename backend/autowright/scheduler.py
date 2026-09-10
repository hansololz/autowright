"""Scheduler (§6): the trigger tick loop — fires due triggers, coalesces
same-moment occurrences, consumes one-shot `time` triggers, applies the
missed-execution policy, and handles retention. The queue/firing mechanics
live in firing.py. There is no automatic execution-level retry (§6) —
transient failures are the engine's §7 step retry."""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import paths, timefmt, triggers as triggerlib
from .engine import Engine
from .events import hub
from .firing import drain_queue, finish_never_ran, fire_trigger
from .storage import Store

log = logging.getLogger("autowright.scheduler")

TICK_S = float(os.environ.get("AUTOWRIGHT_TICK_S", "15"))  # §15 knob, config only
# §6 run-if-missed grace window: an occurrence older than this when the tick
# notices it was slept through, not merely seen a tick late. Derived from the
# tick period; no knob of its own (§15).
GRACE_S = max(60.0, 4 * TICK_S)
def drop_note() -> str:
    """§6 drop-record note. "Mac" is the §9 per-OS machine noun, resolved when
    the record is written (a Windows or Linux record says "this PC")."""
    return f"missed while this {paths.machine_noun()} was asleep (run if missed is off for this trigger)"


class Scheduler:
    def __init__(self, store: Store, engine: Engine,
                 clock: Callable[[], datetime] = datetime.now,
                 utc_clock: Callable[[], datetime] | None = None):
        self.store = store
        self.engine = engine
        self._clock = clock
        # Baselines are compared against the local wall clock, which is not
        # monotonic: a DST fall-back rewinds it by an hour with no clock step at
        # all. The UTC reading tells the two apart (§4.3: an hour repeated by
        # fall-back fires once) — only a genuine backward step moves it.
        self._utc_clock = utc_clock or (lambda: datetime.now(timezone.utc))
        # (automation id, trigger id) → last position; occurrences at or before
        # it never fire (startup baseline = now, §6 no catch-up queue).
        self._baseline: dict[tuple[str, str], datetime] = {}
        self._baseline_utc: dict[tuple[str, str], datetime] = {}
        # Keys already warned about a wall-clock rewind too large for DST (a
        # system-timezone change) — one warning per rewind, not one per tick.
        self._warned_rewind: set[tuple[str, str]] = set()
        self._stop = threading.Event()
        self._last_retention = clock()
        # §6: the live retention worker, single-flight — the tick never starts
        # a second sweep while one is still deleting.
        self._retention_thread: threading.Thread | None = None
        # §6 overdue sweep: hourly observations, in-memory only (§4.1
        # derived-not-stored). streak counts consecutive overdue sightings per
        # automation; notified re-arms when the automation recovers.
        self._last_overdue = self._last_retention
        self._overdue_streak: dict[str, int] = {}
        self._overdue_notified: set[str] = set()
        engine.drain_queue = lambda automation_id: drain_queue(self.store, self.engine, automation_id)  # type: ignore[attr-defined]

    def start(self) -> None:
        t = threading.Thread(target=self._loop, daemon=True, name="ad-scheduler")
        t.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(TICK_S):
            try:
                self._tick()
            except Exception:  # noqa: BLE001
                # Never let one bad tick kill the thread — but never hide it
                # either: a persistent failure here silently turns triggers off.
                log.exception("scheduler tick failed")

    def _set_baseline(self, key: tuple[str, str], now: datetime, now_utc: datetime) -> None:
        self._baseline[key] = now
        self._baseline_utc[key] = now_utc

    def _tick(self) -> None:
        now = self._clock()
        now_utc = self._utc_clock()
        with self.store.lock:
            autos = list(self.store.autos.values())
            # One O(all executions) pass per tick, not one per automation:
            # drain_queue walks store.execs in full, so with nothing queued
            # (the overwhelmingly common tick) the per-automation drain below
            # is skipped outright.
            queued_autos = {h.get("automation_id")
                            for h in self.store.execs.values()
                            if h.get("status") == "queued"}
        # Collected before the per-automation work, so a raising automation can
        # never cost another one its baselines in the prune below.
        live_keys = {(a["id"], t["id"]) for a in autos for t in list(a["triggers"])}
        for a in autos:
            try:
                self._tick_automation(a, now, now_utc, drain=a["id"] in queued_autos)
            except Exception:  # noqa: BLE001
                # §6: one automation's firing must never silence every
                # automation after it in the list - same rule as the tick loop.
                log.exception("scheduler tick failed for automation %r", a.get("name"))
        # Forget baselines of deleted automations / removed triggers.
        self._baseline = {k: v for k, v in self._baseline.items() if k in live_keys}
        self._baseline_utc = {k: v for k, v in self._baseline_utc.items() if k in live_keys}
        self._warned_rewind &= live_keys
        # Both hourly sweeps advance their timestamp first (a failing sweep
        # retries next hour, never hot-loops) and are guarded so neither can
        # abort the tick or the other sweep. The wall clock is not monotonic
        # (DST fall-back, NTP steps): a negative delta counts as due — a
        # rewound clock must never stall a sweep for up to an hour.
        # §6: the retention sweep only *decides* here — its deletes run on
        # their own worker thread (single-flight): a first sweep after a
        # retention change can rmtree a huge backlog, and a tick stalled past
        # GRACE_S would make the runIfMissed drop rule misread scheduler lag
        # as sleep.
        retention_wait = (now - self._last_retention).total_seconds()
        if ((retention_wait > 3600 or retention_wait < 0)
                and not (self._retention_thread and self._retention_thread.is_alive())):
            self._last_retention = now
            self._retention_thread = threading.Thread(
                target=self._retention_sweep, daemon=True, name="ad-retention")
            self._retention_thread.start()
        overdue_wait = (now - self._last_overdue).total_seconds()
        if overdue_wait > 3600 or overdue_wait < 0:
            self._last_overdue = now
            try:
                self._overdue_sweep(autos, now)
            except Exception:  # noqa: BLE001
                log.exception("overdue sweep failed")

    def _tick_automation(self, a: dict, now: datetime, now_utc: datetime,
                         drain: bool = True) -> None:
        """One automation's share of a tick - its own unit of failure (§6): the
        caller logs and moves on, so a broken automation can't stop the rest.
        `drain=False` skips the queue drain (the caller saw nothing queued for
        this automation at tick start; anything queued mid-tick is picked up
        by the finish-time drain or the next tick — the same one-tick
        granularity the safety net always had)."""
        due: list[tuple[datetime, dict]] = []
        dropped: list[dict] = []  # §6 runIfMissed-off triggers whose span was slept through
        # §4.3 interval anchor: the automation's run baseline, read once per
        # tick (the same baseline §4.1 overdue uses) — never stored.
        run_baseline = self.store.run_baseline(a)
        for t in list(a["triggers"]):  # consume_trigger below mutates the list
            key = (a["id"], t["id"])
            if key not in self._baseline:
                self._set_baseline(key, now, now_utc)
            base = self._baseline[key]
            if base > now and now_utc < self._baseline_utc[key]:
                # A backward clock step (NTP) must not silence every
                # trigger until the wall clock re-reaches the old baseline.
                # UTC moved back too, so this is a real step — not a DST
                # fall-back, where holding the baseline is what makes the
                # repeated hour fire once (§4.3).
                base = now
                self._set_baseline(key, now, now_utc)
            if t["enabled"] and base > now and (base - now).total_seconds() > 3900:
                # §4.3: the wall clock rewound with UTC steady, by more
                # than any DST fall-back shifts (> 65 min) — almost
                # certainly a system-timezone change. Deliberately handled
                # like fall-back (occurrences in the rewound span never
                # re-fire; a pending one-shot there is consumed unfired) —
                # this line exists so a reported miss is diagnosable.
                if key not in self._warned_rewind:
                    self._warned_rewind.add(key)
                    log.warning(
                        "wall clock rewound %d min with UTC steady — looks like a "
                        "system timezone change; occurrences of trigger %s on %r "
                        "before the old baseline will not fire",
                        int((base - now).total_seconds() // 60), t["id"], a["name"])
            else:
                self._warned_rewind.discard(key)
            if not t["enabled"]:
                # Occurrences passing while off never fire, even after a re-enable.
                self._set_baseline(key, now, now_utc)
                if triggerlib.time_elapsed(t, now):
                    # §4.3: a spent one-shot never lingers — consumed even
                    # when its moment passed while the trigger was off.
                    self.store.consume_trigger(a, t["id"])
                    self._publish_changed(a)
                continue
            occ = triggerlib.trigger_next(t, after=base, run_baseline=run_baseline)
            if occ and occ <= now:
                # §6: at most one catch-up per wake — swallow every older occurrence.
                self._set_baseline(key, now, now_utc)
                if not triggerlib.run_if_missed(t):
                    # §6 opt-out: fire only when an occurrence landed within
                    # the grace window; anything older was slept through and
                    # is dropped, never fired late.
                    fresh = triggerlib.trigger_next(t, after=now - timedelta(seconds=GRACE_S),
                                                    run_baseline=run_baseline)
                    if fresh is None or fresh > now:
                        dropped.append(t)
                        if t["kind"] == "time":
                            # §4.3 spent rule: a dropped one-shot is consumed unfired.
                            self.store.consume_trigger(a, t["id"])
                            self._publish_changed(a)
                        continue
                    occ = fresh
                due.append((occ, t))
            elif occ is None and triggerlib.time_elapsed(t, now):
                # §4.3: the one-shot's moment passed before the baseline
                # (backend down when it passed) — consumed, never fired.
                self.store.consume_trigger(a, t["id"])
                self._publish_changed(a)
        if due:
            # §6: same-moment (and same-wake) occurrences coalesce into one
            # execution — "at most one catch-up execution fires per wake
            # regardless of how many occurrences — across all triggers — were
            # slept through"; the fired execution covers the rest, a coalesced
            # one-shot included.
            due.sort(key=lambda p: p[0])
            self._fire(a, due[0][1])
            consumed = False
            for _, t in due:
                if t["kind"] == "time":
                    self.store.consume_trigger(a, t["id"])
                    consumed = True
            if consumed:
                self._publish_changed(a)
        elif dropped:
            # §6 drop record: nothing fired for this automation in this tick,
            # so the drop is the only trace the user gets (for a one-shot, the
            # trigger itself is gone): one skipped record, naming the first
            # dropped trigger's kind. A catch-up by another trigger in the same
            # tick covers the drop silently (the one-per-wake rule).
            self._record_drop(a, dropped[0])
        # §6: drain here as well as on every execution finish — a raised
        # maxParallel, or a finish whose drain lost a race, is picked up
        # within a tick rather than waiting for the next firing.
        if drain:
            drain_queue(self.store, self.engine, a["id"])

    def _retention_sweep(self) -> None:
        """§5 hourly retention pass, off the tick thread (§6): deletes expired
        records and publishes automation.changed only when something was
        actually removed."""
        try:
            removed = self.store.retention_cleanup()
            if removed:
                hub.publish("automation.changed")
        except Exception:  # noqa: BLE001
            log.exception("retention sweep failed")

    def _record_drop(self, a: dict, t: dict) -> None:
        with self.store.lock:
            if self.store.autos.get(a["id"]) is not a:
                return  # deleted mid-tick, no record for a gone automation
            try:
                h = self.store.create_execution(a, "version", a["current_version"], t["kind"],
                                                steps=[], status="skipped", note=drop_note())
                finish_never_ran(self.store, h, drop_note(), finished_at=timefmt.now_iso())
            except Exception:  # noqa: BLE001
                log.exception("recording a dropped occurrence on %r failed", a.get("name"))

    def _fire(self, a: dict, t: dict) -> None:
        # The tick evaluated a snapshot taken outside the lock - a PATCH landing
        # mid-tick may have removed or disabled this trigger, and an occurrence
        # the API already confirmed off must not fire from the stale dict.
        with self.store.lock:
            # A DELETE landing mid-tick removed the automation and its tree —
            # firing against the stale snapshot would create a spurious record
            # (and the engine would re-create a ghost directory).
            if self.store.autos.get(a["id"]) is not a:
                return
            cur = next((x for x in a["triggers"] if x["id"] == t["id"]), None)
            if cur is None or not cur["enabled"]:
                return
        # §6: a cron/one-shot/app-start firing with no free slot is skipped, never
        # queued — only message firings queue (a due one-shot is still consumed by
        # the caller).
        fire_trigger(self.store, self.engine, a, t)

    def _overdue_sweep(self, autos: list[dict], now: datetime) -> None:
        """§6 overdue sweep: notify an automation observed §4.1 overdue at two
        consecutive hourly sweeps — two, not one, so a backend booting into a
        stale morning doesn't cry about an automation its next occurrence is
        about to clear. Once per overdue stretch; recovery re-arms it."""
        from . import notify

        live_ids = {a["id"] for a in autos}
        self._overdue_streak = {k: v for k, v in self._overdue_streak.items() if k in live_ids}
        self._overdue_notified &= live_ids
        for a in autos:
            aid = a["id"]
            # Per-automation unit of failure, same rule as _tick_automation:
            # one automation's damaged triggers must not silence the sweep
            # for every automation after it.
            try:
                is_overdue = self.store.overdue(a, now)
            except Exception:  # noqa: BLE001
                log.exception("overdue check failed for automation %r", a.get("name"))
                continue
            if is_overdue:
                self._overdue_streak[aid] = self._overdue_streak.get(aid, 0) + 1
                if self._overdue_streak[aid] >= 2 and aid not in self._overdue_notified:
                    self._overdue_notified.add(aid)
                    # §6: attention-class — posts under both §4.9 notifications
                    # values, exactly like a failed execution's end notification.
                    # On its own thread: the osascript post can block up to
                    # 10 s, and several automations going overdue in one sweep
                    # would stall the scheduler tick for the sum of them.
                    threading.Thread(
                        target=notify.post,
                        args=(a["name"], "Scheduled executions are being missed."),
                        daemon=True).start()
                    self._publish_changed(a)
            else:
                self._overdue_streak.pop(aid, None)
                self._overdue_notified.discard(aid)

    def _publish_changed(self, a: dict) -> None:
        # §19: single-automation change events carry the changed row so clients
        # patch in place instead of re-fetching /state.
        with self.store.lock:
            payload = self.store.auto_json(a, full=False)
        hub.publish("automation.changed", automationId=a["id"], automation=payload)
