"""Execution-lifecycle audit regressions (2026-10-15).

§7 the store's log-line counter sits on its own leaf lock, so the engine's
_log (under _log_lock) and Engine.retry (logging under store.lock) can't
deadlock; the engine's sequence seed replaces undecodable bytes like the
store's; the read-cap hold-back point is itself a guarded boundary; §4.3 the
interval anchor reaches `nextAtMs` and the §4.1 overdue audit as the run's
exact UTC instant; §7 an orphaned live test publishes `execution.deleted`
for its own id.
"""
import os
import sys
import threading
import time
from datetime import datetime, timezone

import pytest
from conftest import make_version

SECRET = "S3cr3t-" + "k" * 33  # 40 characters


# ---------- §7: the log counter's leaf lock (no ABBA deadlock) ----------

def test_append_log_line_never_waits_on_the_store_lock(store):
    a = store.create_automation(make_version(), "Leaf", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="executing")
    holding = threading.Event()
    release = threading.Event()

    def hold_store_lock():
        with store.lock:
            holding.set()
            release.wait(10)

    holder = threading.Thread(target=hold_store_lock, daemon=True)
    holder.start()
    assert holding.wait(5)
    done = []

    def append():
        done.append(store.append_log_line(
            h["id"], store.EXEC_LOG,
            {"sequence": 1, "kind": "out", "text": "x", "timestamp": "2026-10-15T10:00:00Z"}))

    worker = threading.Thread(target=append, daemon=True)
    worker.start()
    worker.join(2)
    try:
        assert not worker.is_alive(), "append_log_line blocked behind store.lock"
        assert done == [True]
    finally:
        release.set()
        holder.join(5)


def test_logging_under_the_store_lock_races_the_step_thread_without_hanging(store):
    from autowright.engine import Engine

    engine = Engine(store)
    a = store.create_automation(make_version(), "Racing", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="executing")
    h["redacted_secrets"] = []
    stop = threading.Event()

    def step_thread():
        while not stop.is_set():
            engine._log(h, "out", "x", {})

    def retry_shape():
        # Engine.retry's shape: a line logged while holding store.lock.
        for _ in range(200):
            with store.lock:
                engine._log(h, "sys", "y", {})

    spinner = threading.Thread(target=step_thread, daemon=True)
    retrier = threading.Thread(target=retry_shape, daemon=True)
    spinner.start()
    retrier.start()
    retrier.join(30)
    stop.set()
    spinner.join(30)
    assert not retrier.is_alive(), "the store.lock logger hung"
    assert not spinner.is_alive(), "the step-thread logger hung"


# ---------- §5: the engine's sequence seed replaces undecodable bytes ----------

def test_engine_log_seed_tolerates_undecodable_bytes(store):
    from autowright.engine import Engine

    engine = Engine(store)
    a = store.create_automation(make_version(), "Bytes", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="executing")
    h["redacted_secrets"] = []
    p = store.log_file(h["id"], store.EXEC_LOG)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xfe\xfd not utf-8\n")

    engine._log(h, "out", "after", {})

    assert h["_log_seq"][store.EXEC_LOG] == 2
    assert [ln["sequence"] for ln in store.read_log(h["id"])][-1] == 2


# ---------- §7: the hold-back point is a guarded boundary ----------

def test_a_secret_ending_at_the_chunk_end_is_held_whole():
    from autowright.engine import hold_read_carry

    carry_len = len(SECRET) - 1
    cap = 100 + len(SECRET)
    now, carry = hold_read_carry("x" * 100 + SECRET, "", carry_len, cap=cap, guards=[SECRET])
    assert now == "x" * 100
    assert carry == SECRET


def test_a_secret_straddling_the_plain_hold_point_is_held_whole():
    from autowright.engine import hold_read_carry

    carry_len = len(SECRET) - 1
    cap = 200
    start = cap - carry_len - 10  # the plain cut lands 10 characters into the secret
    raw = "x" * start + SECRET + "y" * (cap - start - len(SECRET))
    assert len(raw) == cap
    now, carry = hold_read_carry(raw, "", carry_len, cap=cap, guards=[SECRET])
    assert now == "x" * start
    assert carry == SECRET + "y" * (cap - start - len(SECRET))


def test_no_guards_keeps_the_plain_hold_point():
    from autowright.engine import hold_read_carry

    carry_len = len(SECRET) - 1
    cap = 100 + len(SECRET)
    raw = "x" * 100 + SECRET
    assert hold_read_carry(raw, "", carry_len, cap=cap) == (raw[:-carry_len], raw[-carry_len:])


def test_a_guard_at_the_chunk_start_falls_back_to_the_plain_cut():
    from autowright.engine import hold_read_carry

    carry_len = len(SECRET) - 1
    raw = SECRET + "y" * 10
    cap = len(raw)
    assert hold_read_carry(raw, "", carry_len, cap=cap, guards=[SECRET]) == (
        raw[:-carry_len], raw[-carry_len:])


def test_a_chunk_shorter_than_the_cap_is_never_held_with_guards():
    from autowright.engine import hold_read_carry

    carry_len = len(SECRET) - 1
    assert hold_read_carry("short " + SECRET, "", carry_len, cap=1000, guards=[SECRET]) == (
        "short " + SECRET, "")


def test_a_secret_ending_at_the_read_cap_reaches_the_log_whole(monkeypatch, tmp_path):
    import subprocess

    from autowright import engine as engmod
    from autowright import harness

    real = subprocess.Popen
    offset = harness.PIPE_READLINE_CAP - len(SECRET)
    line = "a" * offset + SECRET + "b" * 100

    def fake_popen(argv, **kw):
        code = f"import sys; sys.stdout.write('a' * {offset} + {SECRET!r} + 'b' * 100 + '\\n')"
        return real([sys.executable, "-c", code], **kw)

    monkeypatch.setattr(engmod.subprocess, "Popen", fake_popen)
    script = tmp_path / "01-any.py"
    script.write_text("pass\n")
    logged: list[tuple[str, str]] = []

    engmod.run_step_process(script, {}, {"proc": None, "cancel": False},
                            lambda k, t: logged.append((k, t)),
                            {"status": "ok", "chip": None}, {}, None,
                            carry_len=engmod.redaction_carry_len({SECRET: "api"}),
                            cut_guards=[SECRET])

    texts = [t for _, t in logged]
    assert "".join(texts) == line
    assert sum(SECRET in t for t in texts) == 1


# ---------- §4.3: the interval anchor is the run's exact instant ----------

@pytest.fixture()
def new_york():
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


def _interval(every: str) -> dict:
    return {"id": "iv", "kind": "interval", "enabled": True, "every": every,
            "enabledAt": "2026-10-31T12:00:00-04:00"}


# The run started at the FIRST 01:30 of the fall-back night (EDT, 05:30Z); the
# naive reading alone would resolve to the later 01:30 (EST, 06:30Z).
RUN_INSTANT = datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)


def test_next_at_ms_anchors_at_the_given_instant(new_york):
    from autowright import triggers as triggerlib

    t = _interval("PT2H")
    after = datetime(2026, 11, 1, 2, 0)  # EST, 07:00Z
    expected = triggerlib.interval_instant(t, after.astimezone(timezone.utc), RUN_INSTANT)
    assert expected == datetime(2026, 11, 1, 7, 30, tzinfo=timezone.utc)

    ms = triggerlib.next_at_ms([t], after=after, run_baseline=datetime(2026, 11, 1, 1, 30),
                               interval_baseline=RUN_INSTANT)

    assert ms == int(expected.timestamp() * 1000)


def test_auto_json_passes_the_run_instant_to_next_at_ms(store, monkeypatch):
    from autowright import triggers as triggerlib

    a = store.create_automation(make_version(), "Anchored", None)
    a["_last_exec_at"] = "2026-11-01T05:30:00+00:00"
    seen = {}
    real = triggerlib.next_at_ms

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(triggerlib, "next_at_ms", spy)
    store.auto_json(a)
    assert seen["interval_baseline"] == RUN_INSTANT
    assert seen["interval_baseline"] == store.run_baseline_instant(a)


def test_overdue_flips_exactly_at_anchor_plus_two_intervals(store, new_york):
    a = store.create_automation(make_version(), "Overdue", None)
    a["triggers"] = [_interval("PT2H")]
    a["_last_exec_at"] = "2026-11-01T05:30:00+00:00"
    # anchor 05:30Z: occurrences 07:30Z and 09:30Z (04:30 EST). The later
    # reading (06:30Z) would not flip until 10:30Z.
    assert store.overdue(a, now=datetime(2026, 11, 1, 4, 29)) is False
    assert store.overdue(a, now=datetime(2026, 11, 1, 4, 31)) is True


# ---------- §7: an orphaned live test publishes execution.deleted ----------

def test_an_orphaned_test_publishes_its_own_deleted_event(store, monkeypatch, tmp_path):
    from autowright import testexec

    monkeypatch.setattr(testexec, "store", store)
    published = []
    monkeypatch.setattr(testexec.hub, "publish",
                        lambda ev, **kw: published.append({"event": ev, **kw}))
    a = store.create_automation(make_version(), "Owner", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    h["_draft_settled"] = True

    class _Engine:
        def _execute(self, *args):
            published.append({"event": "execution.finished", "executionId": h["id"]})

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    testexec._run(_Engine(), {}, {}, h, {}, tmp_path / "draft", scratch)

    assert h["id"] not in store.execs
    events = [e["event"] for e in published if e.get("executionId") == h["id"]]
    assert events == ["execution.finished", "execution.deleted"]
