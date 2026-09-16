"""Regression tests for the 2026-09-16 engine audit — one per fixed item.

Each test names the spec rule it pins (§1 redaction in `SPEC.md`, §3 shutdown
and §6.3 memory snapshots in `spec/engine.md`, §4.5 agentPgids and §7 kill
semantics in `spec/execution.md`)."""
import sys
import threading

import pytest
from conftest import make_version

from autowright.executor import CTRL


# ---------- §3 shutdown: one failing kill must not strand the other groups ----------

def test_kill_all_live_keeps_going_after_a_failing_kill(store):
    """§3: "their step processes must die with this backend" — a raising
    hard_kill on the first live execution used to abandon every later one."""
    from autowright.engine import Engine

    engine = Engine(store)
    killed = []

    def boom():
        raise RuntimeError("this group's kill blew up")

    engine._live["first"] = {"hard_kill": boom}
    engine._live["second"] = {"hard_kill": lambda: killed.append("second")}

    engine.kill_all_live()

    assert killed == ["second"]
    assert engine._live["first"]["cancel"] and engine._live["second"]["cancel"]


# ---------- §4.5: the agentPgids snapshot never races the read loop ----------

class _NoKill:
    """§2 process-control stand-in — the pgids in this test are made up, so
    nothing may actually be signalled."""

    def kill_group(self, pgid):
        pass

    def signal_group(self, proc, sig=None):
        pass

    def session_kwargs(self):
        from autowright.platform import posixproc

        return posixproc.PosixProcessControl().session_kwargs()


class _CountingLock:
    """A `_pgids_lock` stand-in that records which threads went through it."""

    def __init__(self):
        self._lock = threading.Lock()
        self.threads = set()

    def __enter__(self):
        self._lock.acquire()
        self.threads.add(threading.get_ident())
        return self

    def __exit__(self, *exc):
        self._lock.release()


def test_agent_pgid_snapshot_never_races_the_read_loop(monkeypatch, tmp_path):
    """§4.5/§7: the step read loop adds in-flight agent groups on its own
    thread while a kill path snapshots them on another — both sides must go
    through `_pgids_lock`, or the snapshot iterates a set that is still being
    mutated and the kill signals none of the groups it was about to reach."""
    import subprocess

    from autowright import engine as engmod

    real = subprocess.Popen
    emitter = ("import json, sys\n"
               "sys.stdin.read()\n"
               f"CTRL = {CTRL!r}\n"
               "for pgid in range(4000, 7000):\n"
               "    print(CTRL + json.dumps({'op': 'agent_group', 'pgid': pgid}), flush=True)\n")
    monkeypatch.setattr(engmod.subprocess, "Popen",
                        lambda argv, **kw: real([sys.executable, "-c", emitter], **kw))
    # The kills themselves are out of scope: made-up pgids must never be
    # signalled, and defusing the read end would end the very loop under test.
    monkeypatch.setattr(engmod, "kill_step_group", lambda proc, sig=None: None)
    monkeypatch.setattr(engmod, "_processes", _NoKill)
    monkeypatch.setattr(engmod.harness, "defuse_read_end", lambda f: None)
    lock = _CountingLock()
    monkeypatch.setattr(engmod, "_pgids_lock", lock)

    script = tmp_path / "01-ask.py"
    script.write_text("pass\n", encoding="utf-8")
    state = {"proc": None, "cancel": False}
    stop = threading.Event()
    errors = []

    def hammer():
        while not stop.is_set():
            hard = state.get("hard_kill")
            if hard is None:
                continue
            try:
                hard()
            except Exception as e:  # noqa: BLE001 — the regression is exactly this
                errors.append(e)

    t = threading.Thread(target=hammer, daemon=True)
    t.start()
    try:
        engmod.run_step_process(script, {}, state, lambda kind, text: None,
                                {"status": None, "chip": None}, {}, None)
    finally:
        stop.set()
        t.join(timeout=5)

    assert errors == []
    # Both sides went through the lock: the read loop's adds and the kill
    # path's snapshots ran on different threads.
    assert len(lock.threads) >= 2


# ---------- §1: two secrets sharing a name both get redacted ----------

def test_build_redactions_covers_both_values_of_a_shared_name():
    """§1: redaction must never fail open. Keyed by name, the second
    API_TOKEN's value overwrote the first's entry and leaked into the logs."""
    from autowright.engine import build_redactions

    r = build_redactions({"id-one": "value-one", "id-two": "value-two"},
                         {"id-one": "API_TOKEN", "id-two": "API_TOKEN"})

    assert r == {"value-one": "API_TOKEN", "value-two": "API_TOKEN"}


def test_build_redactions_keeps_multiline_lines_per_id():
    """§4.8: each non-blank line of a multi-line value is redacted too — the
    id keying must not lose that."""
    from autowright.engine import build_redactions

    r = build_redactions({"id-key": "-----BEGIN-----\nbody line\n"},
                         {"id-key": "SSH_KEY"})

    assert r["body line"] == "SSH_KEY"
    assert r["-----BEGIN-----"] == "SSH_KEY"


# ---------- §3 shutdown: a retry is an admission too ----------

def test_retry_refuses_once_the_shutdown_sweep_ran(store):
    """§3: "past the kill sweep nothing new may start" — `start` checked the
    flag under store.lock, `retry` launched its own engine thread regardless."""
    from autowright.engine import Engine

    engine = Engine(store)
    a = store.create_automation(make_version(), "Stopping", None)

    engine.kill_all_live()

    with pytest.raises(RuntimeError, match="shutting down"):
        engine.retry(a, {"id": "gone", "status": "failed", "kind": "version",
                         "version": a["current_version"]})


# ---------- §6.3: the pre-version snapshot serializes with memory operations ----------

def test_pre_version_snapshot_runs_under_memory_ops(store, monkeypatch):
    """§6.3: the stage+commit pair takes `memory_ops`, the lock every API
    memory operation holds — commit's prune must not delete the snapshot a
    concurrent restore is copying from."""
    from autowright.engine import Engine

    engine = Engine(store)
    a = store.create_automation(make_version(), "Snapshotter", None)
    mem = store.auto_dir(a) / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "notes.txt").write_text("remembered", encoding="utf-8")

    held = []
    real_commit = store.commit_snapshot

    def commit(*args, **kwargs):
        free = store.memory_ops.acquire(blocking=False)
        if free:
            store.memory_ops.release()
        held.append(not free)
        return real_commit(*args, **kwargs)

    monkeypatch.setattr(store, "commit_snapshot", commit)
    engine._take_pre_version(a, {"_pre_snapshot": "v1"})

    assert held == [True]
    assert len(store.list_snapshots(a)) == 1
