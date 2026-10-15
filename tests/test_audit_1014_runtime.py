"""Execution-lifecycle audit regressions (2026-10-14).

§7 the guarded cut governs every executor-originated text (the `log()`
family, the `error` message, traceback lines, `result.chip`, `notify`, and
the line writer's newline-free overflow flush); the engine's bounded read
holds back a cap-cut chunk's tail so a secret straddling the read cap is
redacted whole; log lines land in sequence order; §4.3 a naive run baseline
inside a fall-back fold anchors an interval at the same instant the
scheduler uses; §4.9 a degraded data path creates no execution record.
"""
import io
import json
import os
import sys
import time
from datetime import datetime, timezone

import pytest
from conftest import make_version

SECRET = "S3cr3t-" + "k" * 33  # 40 characters


class _Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


@pytest.fixture()
def captured(monkeypatch):
    """Every control line emit() writes, as parsed dicts."""
    from autowright import executor

    buf = io.StringIO()
    monkeypatch.setattr(executor, "_real_stdout", buf)

    def lines():
        return [json.loads(ln[len(executor.CTRL):]) for ln in buf.getvalue().splitlines()]

    return lines


@pytest.fixture()
def guarded(monkeypatch):
    """The executor's cut guards set to SECRET, as `main()` would."""
    from autowright import executor

    monkeypatch.setattr(executor, "_cut_guards", [SECRET])
    monkeypatch.setattr(executor, "_GUARD_TAIL", len(SECRET) - 1)
    return executor


# ---------- §7: guarded cuts on every executor-originated text ----------

def test_guarded_pieces_never_split_a_guard_and_never_go_empty(guarded):
    text = "a" * 95 + SECRET + "b" * 100
    pieces = guarded._guarded_pieces(text, 100)
    assert "".join(pieces) == text
    assert all(0 < len(p) <= 100 for p in pieces)
    assert sum(SECRET in p for p in pieces) == 1
    assert pieces[0] == "a" * 95
    assert guarded._guarded_pieces("", 100) == []


def test_an_oversize_log_call_goes_out_as_guarded_pieces(guarded, captured):
    limit = guarded.MAX_LOG_PIECE
    offset = limit - 20
    text = "a" * offset + SECRET + "b" * (300_000 - offset - len(SECRET))

    guarded.Log()(text)

    logs = [l for l in captured() if l["op"] == "log"]
    assert all(l["kind"] == "out" for l in logs)
    texts = [l["text"] for l in logs]
    assert "".join(texts) == text
    assert all(len(t) <= limit for t in texts)
    assert sum(SECRET in t for t in texts) == 1
    assert texts[0] == "a" * offset


def test_log_warn_and_error_keep_their_kind_on_every_piece(guarded, captured):
    text = "x" * (guarded.MAX_LOG_PIECE + 5)
    guarded.Log().warn(text)
    guarded.Log().error(text)
    kinds = [l["kind"] for l in captured() if l["op"] == "log"]
    assert kinds == ["wrn", "wrn", "err", "err"]


def test_the_error_message_is_clipped_at_a_guarded_cut(guarded, captured):
    offset = guarded.ERROR_MESSAGE_CAP - 10
    guarded._emit_error("Boom", "a" * offset + SECRET + "b" * 5_000)
    (event,) = [l for l in captured() if l["op"] == "error"]
    assert event["type"] == "Boom"
    assert event["message"] == "a" * offset + "… [clipped]"


def test_a_short_error_message_is_untouched(guarded, captured):
    guarded._emit_error("Boom", "short")
    assert [l["message"] for l in captured() if l["op"] == "error"] == ["short"]


def test_the_chip_is_a_guarded_cut_without_a_marker(guarded, captured, tmp_path):
    offset = 1_000 - 15
    guarded.Result(str(tmp_path / "result")).chip("a" * offset + SECRET + "b" * 50)
    (event,) = [l for l in captured() if l["op"] == "result"]
    assert event["value"] == "a" * offset


def test_the_line_writer_overflow_holds_back_a_value_straddling_two_writes(guarded, captured):
    max_line = guarded._LineWriter.MAX_LINE
    writer = guarded._LineWriter("out")
    head = "a" * (max_line - 3)
    writer.write(head + SECRET[:10])  # over the cap: the overflow flush runs
    writer.write(SECRET[10:] + "b" * 10)
    writer.flush()
    texts = [l["text"] for l in captured() if l["op"] == "log"]
    assert "".join(texts) == head + SECRET + "b" * 10
    assert all(len(t) <= max_line for t in texts)
    assert sum(SECRET in t for t in texts) == 1


def test_the_overflow_without_guards_still_flushes_everything(monkeypatch, captured):
    from autowright import executor

    monkeypatch.setattr(executor, "_cut_guards", [])
    monkeypatch.setattr(executor, "_GUARD_TAIL", 0)
    writer = executor._LineWriter("out")
    writer.write("x" * (writer.MAX_LINE + 1))
    assert [len(l["text"]) for l in captured()] == [writer.MAX_LINE, 1]
    assert writer.buf == ""


def test_guard_tail_is_the_longest_guard_minus_one():
    from autowright import executor

    assert executor._guard_tail([]) == 0
    assert executor._guard_tail(["abc", SECRET]) == len(SECRET) - 1


@pytest.fixture()
def run_main(tmp_path, monkeypatch, captured):
    """executor.main() in-process (as tests/test_executor.py drives it),
    restoring every global main() mutates. Returns (rc, control_lines)."""
    from autowright import executor

    def run(source: str, **ctx_overrides):
        script = tmp_path / "step.py"
        script.write_text(source, encoding="utf-8")
        ctx = {"scan_secrets": {}, "workspace": str(tmp_path / "ws"),
               "memory_dir": str(tmp_path / "memory"), "result_dir": str(tmp_path / "result")}
        ctx.update(ctx_overrides)
        monkeypatch.setattr(sys, "argv", ["executor", str(script)])
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(ctx)))
        monkeypatch.setattr(executor, "_cut_guards", [])
        monkeypatch.setattr(executor, "_GUARD_TAIL", 0)
        saved_mod = sys.modules["autowright"]
        saved_out, saved_err = sys.stdout, sys.stderr
        saved_path = list(sys.path)
        saved_env = {k: v for k, v in os.environ.items() if k.startswith("AUTOWRIGHT_")}
        saved_cwd = os.getcwd()
        try:
            rc = executor.main()
        finally:
            sys.modules["autowright"] = saved_mod
            sys.stdout, sys.stderr = saved_out, saved_err
            sys.path[:] = saved_path
            for k in [k for k in os.environ if k.startswith("AUTOWRIGHT_")]:
                del os.environ[k]
            os.environ.update(saved_env)
            os.chdir(saved_cwd)
        return rc, captured()

    return run


def test_main_sets_the_guard_tail_and_clips_notify_at_a_guarded_cut(run_main):
    from autowright import executor

    offset = 10_000 - 5
    rc, lines = run_main(
        "from autowright import notify\n"
        f"notify('a' * {offset} + {SECRET!r} + 'b' * 100)\n",
        scan_secrets={"api": SECRET})
    assert rc == 0
    assert executor._GUARD_TAIL == len(SECRET) - 1
    (event,) = [l for l in lines if l["op"] == "notify"]
    assert event["text"] == "a" * offset


def test_main_an_exception_carrying_a_document_tears_nothing(run_main):
    from autowright import executor

    offset = executor.MAX_LOG_PIECE - 50
    rc, lines = run_main(
        f"raise RuntimeError('a' * {offset} + {SECRET!r} + 'b' * 200_000)\n",
        scan_secrets={"api": SECRET})
    assert rc == 1
    (error,) = [l for l in lines if l["op"] == "error"]
    assert error["message"].endswith("… [clipped]")
    assert len(error["message"]) <= executor.ERROR_MESSAGE_CAP + len("… [clipped]")
    texts = [l["text"] for l in lines if l["op"] == "log"]
    assert all(len(t) <= executor.MAX_LOG_PIECE for t in texts)
    # The message line (and the quoted source line) carry the value whole —
    # never a piece holding only its head.
    assert any(t.startswith("RuntimeError: ") for t in texts)
    assert sum(SECRET in t for t in texts) >= 1
    assert not any(SECRET[:20] in t and SECRET not in t for t in texts)


# ---------- §7: the engine's read-cap carry ----------

def test_hold_read_carry_holds_the_tail_of_a_cap_cut_chunk():
    from autowright.engine import hold_read_carry, redaction_carry_len

    assert redaction_carry_len({}) == 0
    assert redaction_carry_len({SECRET: "api", "": "empty"}) == len(SECRET) - 1
    carry_len = len(SECRET) - 1
    cap = 100 + 15  # a chunk exactly this long with no newline was cut by the cap
    first, carry = hold_read_carry("x" * 100 + SECRET[:15], "", carry_len, cap=cap)
    assert first == ("x" * 100 + SECRET[:15])[:-carry_len]
    second, carry = hold_read_carry(SECRET[15:] + " tail\n", carry, carry_len, cap=cap)
    assert carry == ""
    assert SECRET in second
    # A newline-free chunk SHORTER than the cap is the stream's final fragment
    # (EOF follows): never held back — holding it would split that line.
    assert hold_read_carry("short " + SECRET, "", carry_len, cap=cap) == ("short " + SECRET, "")
    assert hold_read_carry("no secrets", "", 0, cap=cap) == ("no secrets", "")
    # The carry prepends to a final fragment too, and nothing is held after it.
    assert hold_read_carry("end", "held", carry_len, cap=cap) == ("heldend", "")


def test_a_secret_straddling_the_read_cap_reaches_the_log_whole(monkeypatch, tmp_path):
    import subprocess

    from autowright import engine as engmod

    real = subprocess.Popen
    cap = 2_000_000
    offset = cap - 20
    line = "a" * offset + SECRET + "b" * 100

    def fake_popen(argv, **kw):
        code = f"import sys; sys.stdout.write('a' * {offset} + {SECRET!r} + 'b' * 100 + '\\n')"
        return real([sys.executable, "-c", code], **kw)

    monkeypatch.setattr(engmod.subprocess, "Popen", fake_popen)
    script = tmp_path / "01-any.py"
    script.write_text("pass\n")
    logged: list[tuple[str, str]] = []
    state = {"proc": None, "cancel": False}

    engmod.run_step_process(script, {}, state, lambda k, t: logged.append((k, t)),
                            {"status": "ok", "chip": None}, {}, None,
                            carry_len=engmod.redaction_carry_len({SECRET: "api"}))

    texts = [t for _, t in logged]
    assert "".join(texts) == line
    assert sum(SECRET in t for t in texts) == 1


def test_a_final_fragment_without_a_newline_is_logged_whole(monkeypatch, tmp_path):
    # §7: a child's last write with no trailing newline arrives as a short
    # newline-free chunk; the carry must not split it (the secret stays whole
    # in ONE logged line, then redaction can match it).
    import subprocess

    from autowright import engine as engmod

    real = subprocess.Popen

    def fake_popen(argv, **kw):
        code = f"import sys; sys.stdout.write('tail ' + {SECRET!r})"
        return real([sys.executable, "-c", code], **kw)

    monkeypatch.setattr(engmod.subprocess, "Popen", fake_popen)
    script = tmp_path / "01-any.py"
    script.write_text("pass\n")
    logged: list[tuple[str, str]] = []
    engmod.run_step_process(script, {}, {"proc": None, "cancel": False},
                            lambda k, t: logged.append((k, t)),
                            {"status": "ok", "chip": None}, {}, None,
                            carry_len=engmod.redaction_carry_len({SECRET: "api"}))
    texts = [t for _, t in logged]
    assert texts == ["tail " + SECRET]


def test_the_engine_clips_the_error_message_after_redaction(store, monkeypatch):
    from autowright import engine as engmod
    from autowright.engine import Engine

    def failing(*args, **kwargs):
        holder = args[5]
        holder["error"] = {"type": "Boom", "message": "Boom: " + "z" * 10_000}
        return 1

    monkeypatch.setattr(engmod, "run_step_process", failing)
    engine = Engine(store)
    a = store.create_automation(make_version(), "Loud Failure", None)
    h = engine.start(a, "manual")
    from conftest import wait_done

    wait_done(engine, h["id"])
    error = store.exec_full(h["id"])["error"]
    assert error["message"] == ("Boom: " + "z" * 10_000)[:4_000] + "… [clipped]"


# ---------- §7: log lines land in sequence order ----------

def test_the_sequence_is_taken_and_the_line_appended_under_one_lock(store, monkeypatch):
    from autowright.engine import Engine

    engine = Engine(store)
    a = store.create_automation(make_version(), "Ordered", None)
    h = store.create_execution(a, "version", 1, "manual", [])
    h["redacted_secrets"] = []
    held = []
    real_append = store.append_log_line

    def recording_append(*args, **kwargs):
        held.append(engine._log_lock.locked())
        return real_append(*args, **kwargs)

    monkeypatch.setattr(store, "append_log_line", recording_append)
    engine._log(h, "sys", "one", {})
    engine._log(h, "sys", "two", {})
    assert held == [True, True]


# ---------- §4.3: interval anchor as an instant inside a fall-back fold ----------

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


def test_next_at_ms_reads_a_fold_baseline_as_the_scheduler_does(new_york):
    """The run started at the second 01:30 (EST, 06:30Z). The scheduler's
    instant anchors there; the naive baseline must read the same reading."""
    from zoneinfo import ZoneInfo

    from autowright import triggers as triggerlib

    zone = ZoneInfo("America/New_York")
    run_instant = datetime(2026, 11, 1, 1, 30, tzinfo=zone).replace(fold=1).astimezone(timezone.utc)
    assert run_instant == datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)
    t = _interval("PT2H")
    after = datetime(2026, 11, 1, 2, 0)  # EST, 07:00Z
    scheduler_next = triggerlib.interval_instant(t, after.astimezone(timezone.utc), run_instant)

    ms = triggerlib.next_at_ms([t], after=after, run_baseline=datetime(2026, 11, 1, 1, 30))

    assert ms == int(scheduler_next.timestamp() * 1000)
    assert scheduler_next == datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc)


def test_is_overdue_reads_a_fold_baseline_as_the_later_reading(new_york):
    """PT1H from the second 01:30 (06:30Z): occurrences 07:30Z and 08:30Z —
    at 03:00 EST (08:00Z) only one has passed, so not overdue. The earlier
    reading (05:30Z) would have counted two."""
    from autowright import triggers as triggerlib

    assert triggerlib.is_overdue([_interval("PT1H")], datetime(2026, 11, 1, 1, 30),
                                 now=datetime(2026, 11, 1, 3, 0)) is False


# ---------- §4.9: a degraded data path creates no execution record ----------

def test_engine_start_refuses_on_an_unreachable_data_path(store, monkeypatch):
    from autowright.engine import Engine

    monkeypatch.setattr(store, "exec_dir_unreachable", True, raising=False)
    engine = Engine(store)
    a = store.create_automation(make_version(), "Offline", None)
    with pytest.raises(RuntimeError, match="data path isn't reachable"):
        engine.start(a, "manual")
    assert store.execs == {}


def test_firing_refuses_on_an_unreachable_data_path(store, monkeypatch):
    from autowright.engine import Engine
    from autowright.firing import fire_trigger, queue_manual

    monkeypatch.setattr(store, "exec_dir_unreachable", True, raising=False)
    engine = Engine(store)
    a = store.create_automation(make_version(), "Offline Firing", None)
    payload = {"kind": "discord", "secret": "s", "channel": "c"}
    assert fire_trigger(store, engine, a, {"id": "d", "kind": "discord"}, payload=payload) is False
    a["_live"] = {"x"}  # at capacity: the queued/skipped record branch must refuse too
    a["max_queued"] = 5
    assert fire_trigger(store, engine, a, {"id": "d", "kind": "discord"}, payload=payload) is False
    a["_live"] = set()
    with pytest.raises(RuntimeError, match="data path isn't reachable"):
        queue_manual(store, engine, a, "manual")
    assert store.execs == {}


def test_testexec_refuses_on_an_unreachable_data_path_and_a_deleting_automation(store, monkeypatch):
    from autowright import testexec as tr
    from autowright.engine import Engine

    monkeypatch.setattr(tr, "store", store)
    engine = Engine(store)
    ver = make_version()
    a = store.create_automation(ver, "Offline Test", None)
    store.save_draft(a, ver)

    monkeypatch.setattr(store, "exec_dir_unreachable", True, raising=False)
    with pytest.raises(RuntimeError, match="data path isn't reachable"):
        tr.start(engine, ver, a, [], [], {})
    monkeypatch.setattr(store, "exec_dir_unreachable", False, raising=False)
    a["_deleting"] = True
    with pytest.raises(RuntimeError, match="being deleted"):
        tr.start(engine, ver, a, [], [], {})
    assert store.execs == {}


def test_the_scheduler_fires_nothing_and_consumes_nothing_while_degraded(store, monkeypatch):
    from autowright import scheduler as sched_mod, triggers as triggerlib
    from autowright.engine import Engine
    from autowright.scheduler import Scheduler

    real = triggerlib.stamp_enabled
    monkeypatch.setattr(triggerlib, "stamp_enabled",
                        lambda new, old=None, now_iso=None: real(new, old, now_iso or "2026-01-01T00:00:00"))
    fires = []
    real_fire = sched_mod.fire_trigger
    monkeypatch.setattr(sched_mod, "fire_trigger",
                        lambda *args, **kwargs: fires.append(args[3]["id"]) or real_fire(*args, **kwargs))
    clock = _Clock(datetime(2026, 7, 10, 7, 59))
    sched = Scheduler(store, Engine(store), clock=clock,
                      utc_clock=lambda: clock().astimezone(timezone.utc))
    a = store.create_automation(make_version(), "Offline Schedule", None, triggers=[
        {"id": "cr", "kind": "cron", "enabled": True, "expression": "0 8 * * *"},
        {"id": "tt", "kind": "time", "enabled": True, "at": "2026-07-10T08:00"}])
    sched._tick()

    monkeypatch.setattr(store, "exec_dir_unreachable", True, raising=False)
    clock.now = datetime(2026, 7, 10, 8, 1)
    sched._tick()
    assert fires == []
    assert store.execs == {}
    assert {t["id"] for t in a["triggers"]} == {"cr", "tt"}  # one-shot not consumed

    # Reachable again: nothing that passed meanwhile catches up.
    monkeypatch.setattr(store, "exec_dir_unreachable", False, raising=False)
    clock.now = datetime(2026, 7, 10, 8, 2)
    sched._tick()
    assert fires == []
    assert store.execs == {}
