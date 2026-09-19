"""Regression tests for the 2026-09-18 engine audit — one per fixed item.

Each test names the spec rule it pins (§6.1 notification threading in
`spec/engine.md`, §7 cancel and the capacity 409 in `spec/execution.md`)."""
import threading
import time

import pytest
from conftest import make_version, wait_done


# ---------- §6.1: the end-of-execution notification never holds the slot ----------

def test_notification_never_blocks_the_slot_release(store, monkeypatch):
    """§6.1: "The engine posts it on its own thread: the OS notifier can block
    for its full timeout, and the §6 slot release, the `execution.finished`
    event and the queue drain must not wait behind it"."""
    from autowright import notify
    from autowright.engine import Engine

    posting = threading.Event()
    release = threading.Event()
    posted: list[tuple[str, str]] = []

    def blocking_post(title, body):
        posting.set()
        assert release.wait(10), "the test never released the notifier"
        posted.append((title, body))

    monkeypatch.setattr(notify, "post", blocking_post)
    engine = Engine(store)
    ver = make_version()
    ver["steps"][0]["code"] = 'raise RuntimeError("boom")\n'  # a failure always notifies
    a = store.create_automation(ver, "Loud Fail", None)

    h = engine.start(a, "manual")
    wait_done(engine, h["id"])

    # The record is terminal and the slot is free while the notifier still blocks.
    assert posting.wait(5), "the notification never started"
    assert h["status"] == "failed"
    assert not engine.is_live(h["id"])
    assert a["_live"] == set()
    assert posted == []  # still inside the OS notifier

    release.set()
    deadline = time.time() + 5
    while not posted:
        assert time.time() < deadline, "the notification never landed"
        time.sleep(0.02)
    assert posted == [("Loud Fail", "Execution failed")]


def test_notification_reads_the_record_as_it_was_at_the_end(store, monkeypatch):
    """§6.1: the notifier thread works off a snapshot — a record still being
    written after the hand-off can never change what it announces."""
    from autowright import notify
    from autowright.engine import Engine

    seen: list[tuple[str, str]] = []
    release = threading.Event()

    def blocking_post(title, body):
        assert release.wait(10)
        seen.append((title, body))

    monkeypatch.setattr(notify, "post", blocking_post)
    engine = Engine(store)
    ver = make_version()
    ver["steps"][-1]["code"] = ('from autowright import result\n'
                                'result.status("attention")\nresult.chip("Look at this")\n')
    a = store.create_automation(ver, "Chipped", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])

    h["chip"] = "rewritten after the hand-off"  # a later write must not leak in
    release.set()
    deadline = time.time() + 5
    while not seen:
        assert time.time() < deadline, "the notification never landed"
        time.sleep(0.02)
    assert seen == [("Chipped", "Look at this")]


# ---------- §7: a cancel already flagged skips the pre-execution memory work ----------

def test_cancel_before_step_one_skips_the_pre_version_snapshot(store, monkeypatch):
    """§7: "the §6.3 pre-version snapshot and the draft-memory seed are
    skipped when the cancel flag is already set, so a multi-gigabyte copy
    never runs for an execution nobody wants"."""
    from autowright.engine import Engine

    def never(*a, **kw):
        raise AssertionError("§7: a cancelled execution takes no snapshot")

    monkeypatch.setattr(store, "stage_snapshot", never)
    engine = Engine(store)
    a = store.create_automation(make_version(), "Cancelled early", None)
    ver = a["versions"][a["current_version"]]

    h = store.create_execution(a, "version", a["current_version"], "manual",
                               [{"name": s["name"], "file": s["file"], "agent": False,
                                 "sha": "x", "status": "queued", "duration_ms": None,
                                 "attempts": []} for s in ver["steps"]])
    h["_pre_snapshot"] = f"v{a['current_version']}"  # what `start` would have decided
    state = {"proc": None, "cancel": True}

    engine._execute(a, ver, h, state)

    assert h["status"] == "cancelled"
    assert all(s["status"] == "cancelled" for s in h["steps"])


def test_cancel_before_step_one_skips_the_draft_memory_seed(store, monkeypatch):
    """§7: the draft seed is the same gigabyte-scale copy — skipped too."""
    from autowright.engine import Engine

    engine = Engine(store)
    seeded: list[str] = []
    monkeypatch.setattr(engine, "_seed_draft_memory",
                        lambda auto: seeded.append(auto["id"]) or True)
    a = store.create_automation(make_version(), "Draft cancelled", None)
    ver = a["versions"][a["current_version"]]
    h = store.create_execution(a, "draft", None, "manual",
                               [{"name": s["name"], "file": s["file"], "agent": False,
                                 "sha": "x", "status": "queued", "duration_ms": None,
                                 "attempts": []} for s in ver["steps"]])
    state = {"proc": None, "cancel": True}

    engine._execute(a, ver, h, state)

    assert seeded == []
    assert h["status"] == "cancelled"


def test_a_live_execution_still_takes_its_pre_version_snapshot(store):
    """The skip is the cancel path only — an ordinary first version execution
    still snapshots memory before step 1 (§6.3)."""
    from autowright.engine import Engine

    engine = Engine(store)
    a = store.create_automation(make_version(), "Snapshotting", None)
    (store.auto_dir(a) / "memory").mkdir(parents=True, exist_ok=True)
    (store.auto_dir(a) / "memory" / "notes.md").write_text("before")

    h = engine.start(a, "manual")
    wait_done(engine, h["id"])

    assert h["status"] == "succeeded"
    assert store.pre_version_snapshot_exists(a, f"v{a['current_version']}")


# ---------- §6/§19: the no-free-slot refusal is its own error class ----------

def test_at_capacity_raises_capacity_error(store):
    """§19: the execute route answers that one 409 with `reason: "capacity"`,
    so the engine names it with its own class — every other refusal stays a
    plain RuntimeError."""
    from autowright.engine import CapacityError, Engine

    engine = Engine(store)
    a = store.create_automation(make_version(), "Busy", None)
    a["_live"] = {"blocking"}
    with pytest.raises(CapacityError):
        engine.start(a, "manual")
    a["_live"] = set()


def test_other_start_refusals_are_not_capacity(store):
    """§19: shutting down and being deleted are plain RuntimeErrors — their
    409s carry `detail` alone."""
    from autowright.engine import CapacityError, Engine

    engine = Engine(store)
    a = store.create_automation(make_version(), "Refused", None)

    a["_deleting"] = True
    with pytest.raises(RuntimeError) as ei:
        engine.start(a, "manual")
    assert not isinstance(ei.value, CapacityError)
    assert "being deleted" in str(ei.value)
    a.pop("_deleting")

    engine._stopping = True
    with pytest.raises(RuntimeError) as ei:
        engine.start(a, "manual")
    assert not isinstance(ei.value, CapacityError)
    assert "shutting down" in str(ei.value)


def test_capacity_error_is_a_runtime_error(store):
    """Every existing 409 mapping catches RuntimeError — the new class must
    keep landing in it."""
    from autowright.engine import CapacityError

    assert issubclass(CapacityError, RuntimeError)


def test_an_expired_pip_lock_wait_is_a_package_failure(monkeypatch):
    """§19: the bounded wait on the process-wide pip lock expiring is reported
    like any other package failure (the execution fails before step 1 with the
    package category), never escaping as an engine error."""
    from autowright import engine as eng, packages as pkglib

    monkeypatch.setattr(pkglib, "check", lambda entries: [{"pip": "pandas", "status": "missing"}])

    def busy(*a, **k):
        raise pkglib.PackagesBusy()

    monkeypatch.setattr(pkglib, "ensure", busy)
    lines = []
    msg = eng.ensure_declared_packages([{"pip": "pandas", "import": "pandas"}],
                                       lambda k, t: lines.append((k, t)))
    assert msg and "another package install" in msg
    assert lines == [("sys", "installing packages: pandas")]
