"""2026-09-30 runtime audit: step-end group kill (§7), shutdown is not a
cancel (§7), longest-first + substantive-line redaction (§4.8), retention's
latest-real-execution exemption (§5), scheduler baseline seeding and the
enable-stamp clamp (§6/§4.3), retry's DELETE-window refusal (§19), interval
DST math on instants (§4.3), the executor's oversize-line slicing (§7), and
the §8 RECENT EXECUTIONS caps."""
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from conftest import make_version, read_all_logs, wait_done


def _gone(pid: int, timeout: float = 5.0) -> bool:
    """True once `pid` no longer exists (an orphan is reaped by init)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def _wait_pid(path, timeout: float = 20.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            text = path.read_text().strip()
        except OSError:
            text = ""
        if text:
            return int(text)
        time.sleep(0.05)
    raise AssertionError("the step never wrote its child's pid")


# ---------- fix 1: the step group dies at every step end ----------

@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_background_child_holding_the_pipe_never_holds_the_step(store, tmp_path):
    """§7: a step that leaves `sleep 30` behind on the inherited stdout is
    recorded succeeded within the drain grace — not after the watchdog — and
    the sleep dies with the step group."""
    from autowright.engine import Engine

    pidfile = tmp_path / "sleep.pid"
    engine = Engine(store)
    ver = make_version()
    ver["steps"] = [{"file": "01-bg.py", "name": "Background", "description": "",
                     "code": "import subprocess\n"
                             "p = subprocess.Popen(['sleep', '30'])\n"
                             f"open({str(pidfile)!r}, 'w').write(str(p.pid))\n"}]
    a = store.create_automation(ver, "Leaves a child", None)
    t0 = time.time()
    h = engine.start(a, "manual")
    wait_done(engine, h["id"], timeout=15)
    assert time.time() - t0 < 10, "the step must end on exit + grace, not on EOF"
    assert h["status"] == "succeeded"
    assert h["steps"][0]["status"] == "succeeded"
    assert _gone(_wait_pid(pidfile)), "the background sleep must die with the step group"


@pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")
def test_cancel_kills_a_grandchild_that_traps_sigterm(store, tmp_path):
    """§7: a cancel's SIGTERM takes the executor down at once; a grandchild
    that ignores SIGTERM (and holds no pipe) must still be dead after the
    record is finalized — the group kill runs after the executor exit."""
    from autowright.engine import Engine

    pidfile = tmp_path / "stubborn.pid"
    grandchild = ("import os, signal, time\n"
                  "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                  f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
                  "time.sleep(60)\n")
    engine = Engine(store)
    ver = make_version()
    ver["steps"] = [{"file": "01-stubborn.py", "name": "Stubborn", "description": "",
                     "code": "import subprocess, sys, time\n"
                             f"subprocess.Popen([sys.executable, '-c', {grandchild!r}],\n"
                             "                 stdin=subprocess.DEVNULL,\n"
                             "                 stdout=subprocess.DEVNULL,\n"
                             "                 stderr=subprocess.DEVNULL)\n"
                             "time.sleep(60)\n"}]
    a = store.create_automation(ver, "Stubborn grandchild", None)
    h = engine.start(a, "manual")
    pid = _wait_pid(pidfile)
    assert engine.cancel(h["id"]) is True
    wait_done(engine, h["id"], timeout=20)
    assert h["status"] == "cancelled"
    assert _gone(pid), "the SIGTERM-trapping grandchild must not survive the cancel"


# ---------- fix 3/4: redaction ----------

def test_redaction_replaces_the_longer_value_first(store):
    """§4.8: a value that is a prefix of another secret's value must not
    replace the head and leave the longer value's tail in the log."""
    from autowright.engine import Engine, build_redactions

    engine = Engine(store)
    redactions = build_redactions({"a": "hunter2", "b": "hunter2-prod-9f3k"},
                                  {"a": "short", "b": "long"})
    h = {"redacted_secrets": []}
    out = engine._redact(h, "token=hunter2-prod-9f3k and hunter2", redactions)
    assert "-prod-9f3k" not in out
    assert out == "token=••• and •••"
    assert sorted(h["redacted_secrets"]) == ["long", "short"]


_JSON_KEY_LINE = '  "private_key": "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd",'
_JSON_SECRET = "{\n" + _JSON_KEY_LINE + "\n  \"x\": 1\n}"


def test_structural_lines_of_a_multi_line_secret_are_not_probes(store):
    """§4.8: only substantive lines (≥ 8 characters stripped, with a letter or
    digit) of a multi-line value are matched on their own — a `{` line must
    not redact every log line that holds a brace; a real key line still is."""
    from autowright.engine import Engine, build_redactions

    engine = Engine(store)
    redactions = build_redactions({"s": _JSON_SECRET}, {"s": "creds"})
    assert "{" not in redactions and "}" not in redactions
    assert _JSON_KEY_LINE in redactions
    h = {"redacted_secrets": []}
    assert engine._redact(h, '{"a": 1}', redactions) == '{"a": 1}'
    assert h["redacted_secrets"] == []
    assert engine._redact(h, _JSON_KEY_LINE, redactions) == "•••"
    assert h["redacted_secrets"] == ["creds"]


def test_outbound_scan_ignores_structural_lines():
    """§4.8/§6: the agent.ask prompt scan uses the same substantive rule — a
    prompt with a brace passes, one carrying the key line is refused."""
    from autowright.executor import scan_outbound, substantive_probe

    assert not substantive_probe("{")
    assert not substantive_probe("  -----  ")
    assert not substantive_probe("abc1234")  # 7 characters
    assert substantive_probe("abcd1234")
    scan_outbound('Summarize {"a": 1}', "the agent prompt", {"creds": _JSON_SECRET})
    with pytest.raises(RuntimeError):
        scan_outbound("leak: " + _JSON_KEY_LINE, "the agent prompt", {"creds": _JSON_SECRET})


# ---------- fix 5: retention exempts the latest real execution ----------

def test_retention_keeps_the_latest_real_execution_whatever_its_age(store):
    """§5: a monthly cron's only real execution, 45 days old under 30-day
    retention, survives the sweep (it is the §4.1 overdue baseline); an older
    one and a newer never-ran `skipped` record do not."""
    a = store.create_automation(make_version(), "Monthly", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "0 9 1 * *"}])

    def record(days_ago: int, status: str) -> dict:
        h = store.create_execution(a, "version", 1, "cron", [], status=status)
        h["started_at"] = (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")
        store.update_execution(h)
        return h

    older = record(60, "succeeded")
    latest = record(45, "succeeded")
    dropped = record(40, "skipped")
    store.settings["days"] = 30
    store.retention_cleanup()
    assert latest["id"] in store.execs
    assert older["id"] not in store.execs
    assert dropped["id"] not in store.execs


# ---------- fix 6: scheduler baseline seeding + enable-stamp clamp ----------

class _Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def _scheduler(store, local):
    from autowright.engine import Engine
    from autowright.scheduler import Scheduler

    return Scheduler(store, Engine(store), clock=local,
                     utc_clock=lambda: local().astimezone(timezone.utc))


def _record_fires(monkeypatch):
    from autowright import scheduler as sched_mod

    fires = []
    monkeypatch.setattr(sched_mod, "fire_trigger",
                        lambda store, engine, a, t: fires.append(t["id"]) or True)
    return fires


def _stamp(dt: datetime) -> str:
    return dt.astimezone().isoformat(timespec="seconds")


def test_one_shot_saved_seconds_ahead_fires_on_the_next_tick(store, monkeypatch):
    """§6: a first-seen trigger counts from the previous tick (or its enable
    stamp), never from `now` — a one-shot saved seconds ahead whose moment
    passes before the next tick fires instead of being consumed unfired."""
    local = _Clock(datetime(2026, 7, 10, 8, 0, 50))
    sched = _scheduler(store, local)
    fires = _record_fires(monkeypatch)
    sched._tick()  # 08:00:50 — nothing to see yet
    a = store.create_automation(make_version(), "Soon", None, triggers=[
        {"id": "tt", "kind": "time", "enabled": True, "at": "2026-07-10T08:01"}])
    a["triggers"][0]["enabledAt"] = _stamp(datetime(2026, 7, 10, 8, 0, 51))
    local.now = datetime(2026, 7, 10, 8, 1, 5)
    sched._tick()
    assert fires == ["tt"]
    assert a["triggers"] == []  # consumed after firing


def test_occurrence_before_the_enable_stamp_never_fires(store, monkeypatch):
    """§4.3: a cron at 09:00 whose trigger was enabled at 09:00:05 does not
    fire at the 09:00:10 tick — neither when the previous tick saw it off
    (the baseline is clamped up to the stamp) nor when it is first seen."""
    local = _Clock(datetime(2026, 7, 10, 8, 59, 55))
    sched = _scheduler(store, local)
    fires = _record_fires(monkeypatch)
    a = store.create_automation(make_version(), "Nine", None, triggers=[
        {"id": "c1", "kind": "cron", "enabled": False, "expression": "0 9 * * *"}])
    sched._tick()  # 08:59:55 — seen off: baseline = now
    a["triggers"][0]["enabled"] = True
    a["triggers"][0]["enabledAt"] = _stamp(datetime(2026, 7, 10, 9, 0, 5))
    b = store.create_automation(make_version(), "Nine too", None, triggers=[
        {"id": "c2", "kind": "cron", "enabled": True, "expression": "0 9 * * *"}])
    b["triggers"][0]["enabledAt"] = _stamp(datetime(2026, 7, 10, 9, 0, 5))
    local.now = datetime(2026, 7, 10, 9, 0, 10)
    sched._tick()
    assert fires == []
    local.now = datetime(2026, 7, 11, 9, 0, 10)  # the next day's 09:00 fires
    sched._tick()
    assert sorted(fires) == ["c1", "c2"]


# ---------- fix 7: retry refuses in the DELETE window ----------

def test_retry_refuses_while_the_automation_is_being_deleted(store):
    """§19: retry refuses exactly like start during the DELETE window — a
    RuntimeError the route maps to 409."""
    from autowright.engine import Engine

    engine = Engine(store)
    a = store.create_automation(make_version(), "Deleting", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="failed")
    a["_deleting"] = True
    with pytest.raises(RuntimeError, match="being deleted"):
        engine.retry(a, h)
    assert not engine.is_live(h["id"])


# ---------- fix 8: interval DST math on instants ----------

@pytest.mark.skipif(not hasattr(time, "tzset"), reason="POSIX-only tzset")
def test_interval_across_fall_back_fires_at_the_real_instant(store, monkeypatch):
    """§4.3: America/New_York fall-back, PT2H, last run 00:30 EDT (04:30Z) —
    the next occurrence is 06:30Z (the second 01:30), not 05:30Z (the first
    01:30 a naive local comparison would read)."""
    old = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"
    time.tzset()
    try:
        local = _Clock(datetime(2026, 11, 1, 0, 55))
        utc = _Clock(datetime(2026, 11, 1, 4, 55, tzinfo=timezone.utc))
        from autowright.engine import Engine
        from autowright.scheduler import Scheduler

        sched = Scheduler(store, Engine(store), clock=local, utc_clock=utc)
        fires = _record_fires(monkeypatch)
        a = store.create_automation(make_version(), "Two-hourly", None, triggers=[
            {"id": "iv", "kind": "interval", "enabled": True, "every": "PT2H",
             "source": "user"}])
        a["triggers"][0]["enabledAt"] = "2026-10-31T12:00:00-04:00"
        monkeypatch.setattr(store, "run_baseline",
                            lambda auto: datetime(2026, 11, 1, 0, 30))
        sched._tick()  # 00:55 EDT
        assert fires == []
        local.now, utc.now = datetime(2026, 11, 1, 1, 35), \
            datetime(2026, 11, 1, 5, 35, tzinfo=timezone.utc)  # first 01:35 (EDT)
        sched._tick()
        assert fires == [], "05:30Z is not the occurrence — 06:30Z is"
        local.now, utc.now = datetime(2026, 11, 1, 1, 10), \
            datetime(2026, 11, 1, 6, 10, tzinfo=timezone.utc)  # fell back (EST)
        sched._tick()
        assert fires == []
        local.now, utc.now = datetime(2026, 11, 1, 1, 31), \
            datetime(2026, 11, 1, 6, 31, tzinfo=timezone.utc)  # second 01:31 (EST)
        sched._tick()
        assert fires == ["iv"]
    finally:
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()


# ---------- fix 9: oversize lines are sliced, never torn ----------

def test_a_3mb_print_arrives_as_well_formed_bounded_lines(store):
    """§7: a 3 MB single-line print streams as ≤ MAX_LINE log lines — every
    control line parses (none exceeds the engine's size-capped readline) and
    nothing is dropped."""
    from autowright.engine import Engine
    from autowright.executor import _LineWriter

    engine = Engine(store)
    ver = make_version()
    ver["steps"] = [{"file": "01-big.py", "name": "Big", "description": "",
                     "code": "print('y' * 3_000_000)\n"}]
    a = store.create_automation(ver, "Big line", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"], timeout=60)
    assert h["status"] == "succeeded"
    ys = [l["text"] for l in read_all_logs(store, h["id"]) if l["text"].startswith("y")]
    assert sum(len(t) for t in ys) == 3_000_000
    assert all(len(t) <= _LineWriter.MAX_LINE and set(t) == {"y"} for t in ys)
    texts = [l["text"] for l in read_all_logs(store, h["id"])]
    assert not any("@@AD@@" in t for t in texts)  # no torn control line leaked as output


# ---------- fix 10: RECENT EXECUTIONS caps ----------

def test_recent_executions_clip_lines_and_cap_the_section(store, monkeypatch):
    """§8: each log-tail line is clipped to 2,000 characters with
    "… [clipped]", and past 64 K the runs drop oldest-first — the newest
    run's detail is always kept."""
    from autowright import testexec as tr
    from autowright.engine import _step_sha

    monkeypatch.setattr(tr, "store", store)
    step = {"file": "01-s.py", "name": "S", "description": "", "code": "pass\n"}
    a = store.create_automation(make_version(steps=[step]), "Chatty", None)

    for n in range(5):
        steps = [{"name": "S", "file": "01-s.py", "agent": False, "sha": _step_sha(step),
                  "status": "failed", "duration_ms": 1000,
                  "attempts": [{"number": 1, "status": "failed", "started_at": None,
                                "duration_ms": 1000}]}]
        h = store.create_execution(a, "version", 1, "manual", steps, status="failed")
        h["started_at"] = (datetime.now() - timedelta(hours=10 - n)).isoformat(timespec="seconds")
        h["error"] = {"step": "S", "message": f"run{n}:" + "e" * 20_000, "reason": None}
        store.update_execution(h)
        store.append_log_line(h["id"], store.log_name("01-s.py", 0, 1),
                              {"timestamp": h["started_at"], "kind": "out", "sequence": 1,
                               "text": "z" * 5_000})

    section = tr.executions_context(a, [step])
    assert section is not None
    assert len(section) <= tr.SECTION_CAP
    assert "run4:" in section  # the newest run (with its detail) is kept
    assert "log tail (failing step)" in section
    assert "z" * 2_000 + "… [clipped]" in section
    assert "z" * 2_001 not in section
    assert "run0:" not in section  # the oldest run dropped first
