"""Queue/firing mechanics (§6): starting trigger-fired executions, admitting
message firings to the queue, draining free slots, and finishing entries that
will never run. The scheduler's tick loop lives in scheduler.py and calls in
here; the API and listeners fire through here directly."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from . import timefmt
from .events import hub
from .storage import Store, clamp_max_queued

if TYPE_CHECKING:  # runtime import would cycle: engine → listeners → firing
    from .engine import Engine

log = logging.getLogger("autowright.firing")

QUEUE_TTL_S = float(os.environ.get("AUTOWRIGHT_QUEUE_TTL_S", "120"))  # §15 knob


def finish_never_ran(store: Store, h: dict, note: str, *,
                     finished_at: str | None = None,
                     with_automation: bool = False) -> None:
    """§4.6 shared never-ran finisher: an occurrence that will never execute —
    skipped at firing, cancelled/stale in the queue, or orphaned by a restart —
    ends `skipped` with the note saying why, zero duration, and the ordinary
    `execution.finished` event so no reader ever waits on it. `finished_at`
    defaults to `started_at` (nothing ran, no time passed that matters);
    `with_automation` attaches the automation summary when the finish changes
    it (a queue exit moves the §9.2 "N waiting" count). Caller holds
    store.lock."""
    h["status"] = "skipped"
    h["note"] = note
    h["duration_ms"] = 0
    h["finished_at"] = finished_at or h["started_at"]
    store.update_execution(h)
    auto = store.autos.get(h["automation_id"]) if with_automation else None
    hub.publish("execution.finished", executionId=h["id"], automationId=h["automation_id"],
                execution=store.exec_json(h),
                automation=store.auto_json(auto, full=False) if auto else None)


def finish_queued(store: Store, h: dict, note: str) -> bool:
    """§6: end a queue entry that will never execute — cancelled, too stale, or
    orphaned by a restart. `skipped` (§4.6) is the status for "this occurrence
    never ran", so a cancelled entry uses it too and can never be mistaken for
    the automation's latest execution (§4.1). False when the entry was promoted
    or finished under us — the §19 cancel path then retries on the live record
    instead of reporting a cancel that cancelled nothing."""
    with store.lock:
        if h["status"] != "queued":
            return False  # promoted or already finished under us
        finish_never_ran(store, h, note, finished_at=timefmt.now_iso(),
                         with_automation=True)
        payload = h.get("trigger_payload")
    if payload:
        # Outside store.lock: notify_busy replies to the §6.1 sender, and a
        # network send must never run under the store lock.
        from .listeners import notify_busy  # deferred: listeners imports fire_trigger

        notify_busy(payload)
    return True


def fire_trigger(store: Store, engine: Engine, a: dict, t: dict,
                 payload: dict | None = None) -> bool:
    """Start a trigger-fired execution. With every §6 slot taken, a message
    firing is queued when `maxQueued` allows and skipped otherwise; every other
    kind is always skipped (a late cron occurrence is worse than none).
    True when an execution actually started — a queued firing returns False,
    since nothing is executing yet. The capacity check and the start (or the
    queued/skipped record) happen under one lock, so a firing can never race a
    concurrent manual start into a slot that isn't there, or lose its record.
    `payload` is the §4.5 triggerPayload of a message-trigger firing."""
    from .engine import VersionNotFound  # deferred: engine imports firing

    # §4.5: the record stores the trigger's machine kind — §4.3 kinds are the
    # §4.5 kinds verbatim; labels are derived at serialization.
    kind = t["kind"]
    skipped = False
    started = False
    with store.lock:
        # §6: a firing landing in the §19 DELETE window is refused without a
        # record of any kind — the automation is gone a moment later, and a
        # queued or skipped record minted now would sit in the §7 lists as a
        # phantom until the next restart's repair. (`start` refuses too, but
        # the capacity branch below never reaches it.)
        if a.get("_deleting"):
            return False
        if engine.at_capacity(a):
            queued = store.queued_execs(a["id"]) if payload else []
            cap = clamp_max_queued(a.get("max_queued"))
            if payload and len(queued) < cap:
                h = store.create_execution(a, "version", a["current_version"], kind,
                                           steps=[], status="queued",
                                           trigger_payload=payload)
                # §6: admission is visible immediately — the §7 Waiting section
                # and the §9.2 "N waiting" line update off this, and promotion
                # publishes the ordinary exec.started for the same record.
                hub.publish("execution.queued", executionId=h["id"], automationId=a["id"],
                            execution=store.exec_json(h),
                            automation=store.auto_json(a, full=False))
                skipped = False
            else:
                # §6: past maxQueued the *newest* firing is refused — never an
                # entry already admitted and answered. A full queue is a
                # capacity limit the user fixes by raising maxQueued, so it says
                # so; skip-on-busy (maxQueued 0, or a trigger that can't queue at
                # all) keeps the plain note — nothing was queued to be full.
                note = (f"the queue was full ({cap} waiting)" if payload and cap
                        else "previous execution still in progress")
                h = store.create_execution(a, "version", a["current_version"], kind,
                                           steps=[], status="skipped",
                                           note=note,
                                           trigger_payload=payload)
                finish_never_ran(store, h, note)
                skipped = True
        else:
            live_before = set(a.get("_live") or ())
            try:
                engine.start(a, kind, payload=payload)
                started = True
            except Exception as e:  # noqa: BLE001 - a disk error, a bad step folder, …
                started = False
                if isinstance(e, VersionNotFound):
                    # §7: the version this firing targets doesn't resolve
                    # (corrupt current_version) — a silent swallow would fire
                    # and vanish every tick forever, with §4.1 overdue never
                    # flagging it. Leave the skipped record every other
                    # never-ran path leaves. The class is narrow on purpose:
                    # KeyError and IndexError are LookupErrors too, and a bug
                    # inside `start` must be logged, not dressed up as a
                    # missing version.
                    try:
                        h = store.create_execution(
                            a, "version", a["current_version"], kind,
                            steps=[], status="skipped",
                            note=f"version v{a['current_version']} no longer exists",
                            trigger_payload=payload)
                        finish_never_ran(store, h,
                                         f"version v{a['current_version']} no longer exists",
                                         finished_at=timefmt.now_iso())
                        skipped = True
                    except Exception:  # noqa: BLE001
                        log.exception("recording a failed firing on %r", a.get("name"))
                elif not isinstance(e, RuntimeError):
                    log.exception("firing %s on %r failed", kind, a.get("name"))
                # §4.6: `start` may already have created the record before it
                # failed — any exception class, not only unexpected ones. Left
                # `executing` it would hold a §6 slot forever and 409 every
                # later firing, so the never-ran finisher ends it.
                for eid in set(a.get("_live") or ()) - live_before:
                    h = store.execs.get(eid)
                    if h is not None:
                        finish_never_ran(store, h, "the execution couldn't be started",
                                         finished_at=timefmt.now_iso(),
                                         with_automation=True)
    if skipped and payload:
        # §6: a dropped message firing answers its sender. Outside the store
        # lock — a network send must never run under it.
        from .listeners import notify_busy  # deferred: listeners imports fire_trigger

        notify_busy(payload)
    return started


def queue_manual(store: Store, engine: Engine, a: dict, trigger: str,
                 version_label: str | None = None) -> tuple[dict, bool]:
    """§6 manual admission (§19 `queue: true` — the §9.2 popup's Queue action):
    with a free slot the execution simply starts — queueing beside a free slot
    would promote on the next drain anyway. At capacity the entry is admitted
    pinned to the resolved version, with no `triggerPayload` and so no sender
    to notify and no TTL. A Draft is never queued (the draft could change under
    the waiting entry) and a full queue refuses with **no record** — the user is
    present to decide, unlike a message sender. Returns (record, queued).
    Raises VersionNotFound (unknown version → the API's 404) and RuntimeError
    (Draft / full queue / a deletion in progress → 409). The capacity check and
    the start/admission happen under one lock, exactly as in fire_trigger."""
    from .engine import VersionNotFound  # deferred: engine imports firing

    with store.lock:
        # §6: same DELETE-window refusal as a message firing — an entry admitted
        # while the automation's live executions are being cancelled and awaited
        # would outlive the automation itself.
        if a.get("_deleting"):
            raise RuntimeError("this automation is being deleted")
        if not engine.at_capacity(a):
            return engine.start(a, trigger, version_label=version_label), False
        # Same label parse/resolve as engine.start — a queue admission must
        # 404 the same labels a start would.
        kind, version = engine._parse_version_label(a, version_label)
        if kind != "version":
            raise RuntimeError("a Draft execution can't be queued — execute it "
                               "again when a slot frees up")
        if engine._resolve_version(a, kind, version) is None:
            raise VersionNotFound(f"version {version_label or f'v{version}'} not found")
        cap = clamp_max_queued(a.get("max_queued"))
        if len(store.queued_execs(a["id"])) >= cap:
            raise RuntimeError(f"the queue is full ({cap} waiting)")
        h = store.create_execution(a, "version", version, trigger,
                                   steps=[], status="queued")
        # §6: admission is visible immediately, same event as a message firing.
        hub.publish("execution.queued", executionId=h["id"], automationId=a["id"],
                    execution=store.exec_json(h),
                    automation=store.auto_json(a, full=False))
        return h, True


def cancel_unmatched_queue(store: Store, engine: Engine, automation_id: str) -> None:
    """§6: turning a message trigger off (or removing it) cancels the waiting
    entries it admitted — an entry whose payload no longer matches any enabled
    message trigger (same secret + channel for discord; sender matching `from`
    for imessage) can never be re-admitted, and
    promoting it would execute a firing the user just switched off. Called
    after any change to the automation's trigger list."""
    def _key(payload: dict) -> tuple:
        if payload.get("kind") == "imessage":
            return ("imessage", (payload.get("sender") or "").lower())
        return ("discord", payload.get("secret"), payload.get("channel"))

    with store.lock:
        a = store.autos.get(automation_id)
        if a is None:
            return
        live = {("discord", t.get("secret"), t.get("channel"))
                for t in a["triggers"]
                if t["kind"] == "discord" and t["enabled"]}
        live |= {("imessage", (t.get("from") or "").lower())
                 for t in a["triggers"]
                 if t["kind"] == "imessage" and t["enabled"]}
        # §6: only payload-carrying (message-admitted) entries are matched —
        # a manual entry was not admitted by any trigger, so trigger edits
        # never cancel it.
        doomed = [h["id"] for h in store.queued_execs(automation_id)
                  if h.get("trigger_payload")
                  and _key(h["trigger_payload"]) not in live]
    for eid in doomed:
        engine.cancel(eid)  # queued → finish_queued: skipped, sender told


def drain_queue(store: Store, engine: Engine, automation_id: str) -> None:
    """§6: hand every free slot to the longest-waiting firing. Entries that
    waited past the TTL are finished instead of executed — answering a stale
    question is noise. Called whenever a slot frees (an execution finishing or
    being cancelled) and on every scheduler tick, so a raised `maxParallel` or a
    missed wake-up still gets picked up."""
    from .engine import VersionNotFound  # deferred: engine imports firing

    while True:
        with store.lock:
            a = store.autos.get(automation_id)
            if a is None or engine.at_capacity(a):
                return
            queue = store.queued_execs(automation_id)
            if not queue:
                return
            head = queue[0]
            # §6: the TTL applies to message firings only — a manual entry the
            # user chose to queue waits until promoted or cancelled.
            stale = bool(head.get("trigger_payload")) and _waited_s(head) > QUEUE_TTL_S
            if not stale:
                try:
                    engine.start(a, head["trigger"],
                                 version_label=f"v{head['version']}",
                                 payload=head.get("trigger_payload"), adopt=head)
                    continue
                except VersionNotFound:
                    # The version this firing was admitted against is gone (§7
                    # restore/rollback). The entry can never execute — end it
                    # rather than blocking the queue behind it forever.
                    pass
                except RuntimeError:
                    # Slot taken under us — but if the failure landed after
                    # the entry was already promoted (a §7 launch failure),
                    # the record must not stay `executing` with no thread.
                    if head["status"] == "executing" and not engine.is_live(head["id"]):
                        finish_never_ran(store, head, "the execution couldn't be started",
                                         finished_at=timefmt.now_iso(),
                                         with_automation=True)
                    return  # the next finish drains again
        finish_queued(store, head,
                      "waited too long in the queue" if stale
                      else f"version v{head['version']} no longer exists")


def _waited_s(h: dict) -> float:
    q = h.get("queued_at")
    if not q:
        return 0.0
    try:
        queued = datetime.fromisoformat(q)
    except ValueError:
        # A damaged stored timestamp reads as just-queued rather than
        # propagating out of drain_queue and wedging this queue forever.
        return 0.0
    return (datetime.now(timezone.utc) - queued).total_seconds()
