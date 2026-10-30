"""Execution-lifecycle audit regressions (2026-10-13).

§7 a log-piece cut never splits a secret value; a §11 test registering during
the shutdown sweep finishes interrupted and spawns nothing; §4.3 a due
one-shot the shutting-down engine refused is not consumed; §7 a step whose
pipe reader fails to build is still killed; §7 the lifespan waits for swept
live records and startup recovery interrupts queued steps too; §8 RECENT
EXECUTIONS bounds each run block and never drops the newest or the
`executionId` run.
"""
import asyncio
import threading
import time
from datetime import datetime, timedelta

import pytest
from conftest import make_version


# ---------- §7: a cut never falls inside a secret value ----------

def test_a_secret_straddling_a_cut_lands_whole_in_one_piece(monkeypatch):
    from autowright import executor

    secret = "S3cr3t-" + "k" * 33  # 40 characters
    assert len(secret) == 40
    max_line = executor._LineWriter.MAX_LINE
    offset = max_line - 20  # straddles offset 131072
    text = "a" * offset + secret + "b" * (300_000 - offset - len(secret))
    pieces: list[str] = []
    monkeypatch.setattr(executor, "emit", lambda op, **kw: pieces.append(kw["text"]))
    monkeypatch.setattr(executor, "_cut_guards", [secret])

    executor._LineWriter("out")._emit(text)

    assert "".join(pieces) == text
    assert all(0 < len(p) <= max_line for p in pieces)
    assert sum(secret in p for p in pieces) == 1
    assert pieces[0] == "a" * offset  # the cut moved back to just before it


def test_cut_guards_cover_the_substantive_lines_of_a_multi_line_value():
    from autowright import executor

    value = "-----BEGIN-----\nabcdefgh12345678\n}\n"
    guards = executor._build_cut_guards({"key": value, "empty": ""})
    assert value in guards and "abcdefgh12345678" in guards
    assert "}" not in guards and "" not in guards


# ---------- §7: a test registering during the shutdown sweep ----------

def test_a_test_meeting_the_shutdown_flag_ends_interrupted_and_spawns_nothing(store, monkeypatch):
    from autowright import engine as engmod, testexec as tr
    from autowright.engine import Engine

    monkeypatch.setattr(tr, "store", store)
    engine = Engine(store)
    ver = make_version()
    a = store.create_automation(ver, "Late Test", None)
    store.save_draft(a, ver)
    # the draft needs a memory/ dir so the copytree step runs
    (store.auto_dir(a) / "draft" / "memory").mkdir(parents=True, exist_ok=True)

    real_copytree = tr.shutil.copytree

    def copytree_then_stop(src, dst, *args, **kwargs):
        result = real_copytree(src, dst, *args, **kwargs)
        engine._stopping = True  # the sweep lands mid-setup
        return result

    monkeypatch.setattr(tr.shutil, "copytree", copytree_then_stop)
    spawned = []
    monkeypatch.setattr(engmod, "run_step_process",
                        lambda *args, **kwargs: spawned.append(1) or 0)

    eid = tr.start(engine, ver, a, [], [], {})
    # §19 (2026-10-30): setup runs on the test thread — wait for it to land.
    deadline = time.time() + 10
    while time.time() < deadline and (eid in engine._live
                                      or store.execs[eid]["status"] == "executing"):
        time.sleep(0.02)

    assert spawned == []
    assert eid not in engine._live
    h = store.execs[eid]
    assert h["status"] == "interrupted"
    assert all(s["status"] == "interrupted" for s in store.exec_full(eid)["steps"])
    from conftest import read_all_logs
    assert any(l["text"] == "backend stopped mid-execution" for l in read_all_logs(store, eid))


# ---------- §4.3: a refused one-shot is not consumed ----------

def test_a_due_one_shot_survives_a_tick_that_meets_the_shutting_down_engine(store, monkeypatch):
    from autowright import triggers as triggerlib
    from autowright.engine import Engine
    from autowright.scheduler import Scheduler

    real = triggerlib.stamp_enabled
    monkeypatch.setattr(triggerlib, "stamp_enabled",
                        lambda new, old=None, now_iso=None: real(new, old, now_iso or "2026-01-01T00:00:00"))

    class Clock:
        def __init__(self, now):
            self.now = now

        def __call__(self):
            return self.now

    from datetime import timezone

    clock = Clock(datetime(2026, 7, 10, 7, 59))
    engine = Engine(store)
    sched = Scheduler(store, engine, clock=clock,
                      utc_clock=lambda: clock().astimezone(timezone.utc))
    a = store.create_automation(make_version(), "Late One-shot", None, triggers=[
        {"id": "tt", "kind": "time", "enabled": True, "at": "2026-07-10T08:00"}])
    sched._tick()
    engine._stopping = True
    clock.now = datetime(2026, 7, 10, 8, 1)
    sched._tick()

    assert [t["id"] for t in a["triggers"]] == ["tt"]  # not consumed
    assert store.execs == {}  # and nothing recorded


# ---------- §7: a failed reader construction still kills the group ----------

def test_a_failing_pipe_reader_still_kills_the_step_group(monkeypatch, tmp_path):
    import subprocess
    import sys

    from autowright import engine as engmod, harness

    real = subprocess.Popen
    spawned = {}

    def fake_popen(argv, **kw):
        p = real([sys.executable, "-c", "import time; time.sleep(60)"], **kw)
        spawned["proc"] = p
        return p

    def broken_reader(self, *args, **kwargs):
        raise OSError("too many open files")

    killed = []
    real_kill = engmod.kill_step_group

    def recording_kill(proc, *args, **kwargs):
        killed.append(proc)
        return real_kill(proc, *args, **kwargs)

    monkeypatch.setattr(engmod.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(harness.PipeReader, "__init__", broken_reader)
    monkeypatch.setattr(engmod, "kill_step_group", recording_kill)
    script = tmp_path / "01-any.py"
    script.write_text("pass\n")
    state = {"proc": None, "cancel": False}

    with pytest.raises(OSError, match="too many open files"):
        engmod.run_step_process(script, {}, state, lambda k, t: None,
                                {"status": "ok", "chip": None}, {}, None)
    assert killed and killed[0] is spawned["proc"]
    assert state["proc"] is None
    spawned["proc"].wait(timeout=10)


# ---------- §7: startup recovery interrupts queued steps too ----------

def test_repair_interrupts_executing_and_queued_steps(client):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Crash Left", "mock")
    steps = [{"name": "One", "file": "01-one.py", "status": "executing",
              "attempts": [{"number": 1, "status": "executing",
                            "started_at": None, "duration_ms": None}]},
             {"name": "Two", "file": "02-two.py", "status": "queued", "attempts": []}]
    h = store.create_execution(a, "version", 1, "manual", steps)

    api._repair_stale_executing()

    full = store.exec_full(h["id"])
    assert full["status"] == "interrupted"
    assert full["note"] == "backend restarted mid-execution"
    assert [s["status"] for s in full["steps"]] == ["interrupted", "interrupted"]


# ---------- §7: the lifespan waits for swept live records ----------

def test_lifespan_shutdown_waits_for_live_records_to_settle(home, monkeypatch):
    from autowright import api
    from autowright.storage import store

    store.load_all()
    monkeypatch.setattr(api, "_startup_callbacks", [])
    monkeypatch.setattr(api, "_quiesce_callbacks", [])
    monkeypatch.setattr(api, "_shutdown_callbacks", [])
    monkeypatch.setattr(api.engine, "kill_all_live", lambda: None)
    monkeypatch.setattr(api.draft_jobs, "kill_all_building", lambda: None)

    settled = threading.Event()

    def settle():
        time.sleep(0.4)
        settled.set()
        with api.engine._lock:
            api.engine._live.pop("stub-live", None)

    async def drive() -> None:
        lifespan = api._lifespan(api.app)
        await lifespan.__aenter__()
        thread = threading.Thread(target=settle, daemon=True)
        with api.engine._lock:
            api.engine._live["stub-live"] = {"thread": thread}
        thread.start()
        await lifespan.__aexit__(None, None, None)
        assert settled.is_set(), "the lifespan returned before the live record settled"

    try:
        asyncio.run(drive())
    finally:
        with api.engine._lock:
            api.engine._live.pop("stub-live", None)


# ---------- §8: RECENT EXECUTIONS block and section caps ----------

def _failed_run(store, a, step, started, files, line_len, lines, message):
    from autowright.engine import _step_sha

    steps = [{"name": f"S{i}", "file": f, "agent": False, "sha": _step_sha(step),
              "status": "succeeded" if i < len(files) - 1 else "failed", "duration_ms": 1000,
              "attempts": [{"number": 1, "status": "failed", "started_at": None,
                            "duration_ms": 1000}]}
             for i, f in enumerate(files)]
    h = store.create_execution(a, "version", 1, "manual", steps, status="failed")
    h["started_at"] = started.isoformat(timespec="seconds")
    h["error"] = {"step": f"S{len(files) - 1}", "message": message, "reason": None}
    store.update_execution(h)
    for i, f in enumerate(files):
        for n in range(lines):
            store.append_log_line(h["id"], store.log_name(f, i, 1),
                                  {"timestamp": h["started_at"], "kind": "out",
                                   "sequence": n + 1, "text": f"{i}-{n}-" + "z" * line_len})
    return h


def test_section_cap_keeps_the_newest_and_the_execution_id_run(store, monkeypatch):
    from autowright import testexec as tr

    monkeypatch.setattr(tr, "store", store)
    step = {"file": "01-s.py", "name": "S", "description": "", "code": "pass\n"}
    a = store.create_automation(make_version(steps=[step]), "Chatty", None)
    runs = []
    for n in range(4):
        runs.append(_failed_run(store, a, step, datetime.now() - timedelta(hours=10 - n),
                                ["01-s.py"], 100, 5, f"run{n}:" + "e" * 22_000))

    section = tr.executions_context(a, [step], execution_id=runs[0]["id"])

    assert "run3:" in section  # the newest
    assert "run0:" in section  # the executionId run, the oldest
    assert "run1:" not in section and "run2:" not in section
    assert section.count("log tail (failing step)") == 2


def test_a_chatty_run_block_shrinks_under_the_block_cap(store, monkeypatch):
    from autowright import testexec as tr

    monkeypatch.setattr(tr, "store", store)
    step = {"file": "01-s.py", "name": "S", "description": "", "code": "pass\n"}
    a = store.create_automation(make_version(steps=[step]), "Very chatty", None)
    files = ["01-a.py", "02-b.py", "03-c.py"]
    _failed_run(store, a, step, datetime.now(), files, 2_000, 40, "boom")

    section = tr.executions_context(a, [step])

    assert len(section) <= tr.BLOCK_CAP + 1_000
    assert "log tail (failing step)" in section
    assert "2-39-" in section  # the failing step keeps its last line
    early, tail = section.split("log tail (failing step):\n", 1)
    # the step at fault keeps more lines than each earlier step
    early_lines = [l for l in early.splitlines() if l.startswith("0-")]
    fault_lines = [l for l in tail.splitlines() if l.startswith("2-")]
    assert early_lines and len(fault_lines) > len(early_lines)
