"""Regression tests for the 2026-09-09 engine/harness audit — one per fixed item.

Each test names the spec rule it pins (§6/§6.1/§6.2 in `spec/engine.md`, §7 in
`spec/execution.md`, §8 in `spec/agent-pipeline.md`)."""
import io
import os
import threading
import time

import pytest
from conftest import make_version, read_all_logs

from autowright.executor import CTRL


def wait_done(engine, execution_id, timeout=30):
    t0 = time.time()
    while engine.is_live(execution_id):
        assert time.time() - t0 < timeout, "execution didn't finish in time"
        time.sleep(0.05)


# ---------- §7: engine-level failures are not attempts and never retry ----------

def test_agentless_agent_step_fails_after_one_attempt(store):
    """§7: "Only step failures retry … engine-level failures are not attempts
    and never retry" — with `infinite_retries` this would otherwise loop for as
    long as the backend lives."""
    from autowright.engine import Engine

    engine = Engine(store)
    ver = make_version()
    ver["steps"] = [
        {"file": "01-ask.py", "name": "Ask", "description": "", "agent": True,
         "why": "judgment", "infinite_retries": True,
         "code": 'from autowright import agent\nagent.ask("hi")\n'},
    ]
    a = store.create_automation(ver, "Agentless forever", None, enabled_agents=[])
    h = engine.start(a, "manual")
    wait_done(engine, h["id"], timeout=15)

    assert h["status"] == "failed"
    assert len(h["steps"][0]["attempts"]) == 1
    assert any("needs an agent" in l["text"] for l in read_all_logs(store, h["id"]))


def test_missing_step_script_fails_after_one_attempt(store):
    """§7: the same rule for the other engine-level failure — a step whose
    script isn't on disk."""
    from autowright.engine import Engine

    engine = Engine(store)
    ver = make_version()
    ver["steps"] = [dict(ver["steps"][0], infinite_retries=True)]
    a = store.create_automation(ver, "Scriptless forever", None)
    # the script is written at create time — remove it so the engine's own
    # pre-step check fails, not the step process.
    (store.auto_dir(a) / "versions" / "v1" / "01-say.py").unlink()
    h = engine.start(a, "manual")
    wait_done(engine, h["id"], timeout=15)

    assert h["status"] == "failed"
    assert len(h["steps"][0]["attempts"]) == 1
    assert any("is missing" in l["text"] for l in read_all_logs(store, h["id"]))


# ---------- §8: the stdout cap is enforced on bounded reads ----------

class _BlobStdout:
    """One newline-free stream, served in bounded chunks — and, for the shape
    the fix replaced, as a single line to whoever iterates it."""

    def __init__(self, total: int, served: list):
        self._left = total
        self._served = served

    def readline(self, limit=-1):
        if self._left <= 0:
            return ""
        n = min(limit if limit and limit > 0 else 65536, self._left)
        self._left -= n
        self._served[0] += n
        return "x" * n

    def __iter__(self):
        return self

    def __next__(self):
        if self._left <= 0:
            raise StopIteration
        n, self._left = self._left, 0
        self._served[0] += n
        return "x" * n

    def close(self):
        pass


class _AuditProc:
    """Streamed-read stand-in: invoke() reads stdout, drains stderr on a
    thread, then wait()s."""

    def __init__(self, stdout, stderr=None, returncode=0):
        self.stdout = stdout
        self.stderr = stderr if stderr is not None else io.StringIO("")
        self.stdin = io.StringIO()
        self.returncode = returncode
        self.pid = os.getpid()

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        pass


def _fake_spawn(monkeypatch, proc):
    from autowright import harness

    monkeypatch.setattr(harness, "resolve_bin", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(harness, "kill_group", lambda p: None)
    monkeypatch.setattr(harness.subprocess, "Popen", lambda cmd, **kw: proc)


def test_stdout_cap_trips_on_a_newline_free_blob(monkeypatch, home):
    """§8: "the cap is enforced on bounded reads, never per line, so one
    newline-free blob cannot buffer past it first"."""
    from autowright import harness

    served = [0]
    proc = _AuditProc(_BlobStdout(60_000_000, served))
    _fake_spawn(monkeypatch, proc)

    with pytest.raises(harness.HarnessError) as ei:
        harness.invoke({"harness": "Claude Code"}, "question: hi?")
    assert str(ei.value).endswith(" MB of output — aborting")
    # the read stopped AT the cap: never the whole 60 MB, and never more than
    # one bounded read past it.
    assert served[0] <= harness.STDOUT_CAP_CHARS + 2_000_000


# ---------- §8: the stderr drain and the tail read share one lock ----------

class _ChunkedStderr:
    """Delivers stderr in small chunks, slowly enough that the drain thread is
    still working while the stdout loop runs."""

    def __init__(self, chunks, delay=0.01):
        self._chunks = list(chunks)
        self._delay = delay

    def read(self, size=-1):
        if not self._chunks:
            return ""
        time.sleep(self._delay)
        return self._chunks.pop(0)

    def close(self):
        pass


class _SlowStdout:
    def __init__(self, lines, delay=0.01):
        self._lines = list(lines)
        self._delay = delay

    def readline(self, limit=-1):
        if not self._lines:
            return ""
        time.sleep(self._delay)
        return self._lines.pop(0)

    def close(self):
        pass


def test_stderr_tail_is_read_while_the_drain_is_still_running(monkeypatch, home):
    """§8: the drain thread appends and pops `err_parts` while this thread
    joins it and reads the tail — one lock covers both sides, and the failure
    tail still comes out whole."""
    from autowright import harness

    monkeypatch.setattr(harness, "STDERR_CAP_CHARS", 300)
    stderr = _ChunkedStderr([f"noise {i}\n" + "y" * 90 for i in range(20)]
                            + ["ERROR: the decisive line\n"])
    proc = _AuditProc(_SlowStdout([f"line {i}\n" for i in range(20)]),
                      stderr=stderr, returncode=3)
    _fake_spawn(monkeypatch, proc)

    with pytest.raises(harness.HarnessError) as ei:
        harness.invoke({"harness": "Claude Code"}, "question: hi?")
    assert "ERROR: the decisive line" in str(ei.value)


# ---------- §7: a cancel never waits out another install's pip lock ----------

def test_ensure_cancelled_while_waiting_for_the_pip_lock():
    """§7: "A cancel that arrives while the execution is waiting its turn for
    the process-wide pip lock … is honored at once"."""
    from autowright import packages

    held = threading.Event()
    release = threading.Event()

    def hold_the_lock():
        with packages._pip_lock:
            held.set()
            release.wait(20)

    holder = threading.Thread(target=hold_the_lock, daemon=True)
    holder.start()
    assert held.wait(5), "the lock holder never started"
    try:
        t0 = time.time()
        results = packages.ensure(
            [{"pip": "autowright-not-a-real-distribution", "import": "nope"}],
            should_stop=lambda: True)
        elapsed = time.time() - t0
    finally:
        release.set()
        holder.join(5)
    assert elapsed < 2, "the cancel waited out the other install"
    assert results[0]["status"] == "failed" and results[0]["error"] == "cancelled"


# ---------- §6.1: the reply budget ----------

def test_reply_budget_drops_everything_past_100(store, monkeypatch):
    """§6.1 "Reply budget": at most 100 replies per step attempt; the rest are
    dropped with one err line."""
    from autowright import listeners
    from autowright.engine import Engine

    sent = []
    monkeypatch.setattr(listeners, "send_reply",
                        lambda payload, text: sent.append(text) or None)
    # deliver inline: the shared worker is asynchronous, and this test counts
    # what the engine let through, not the queue's timing.
    monkeypatch.setattr(listeners, "submit_send", lambda fn: fn())

    engine = Engine(store)
    ver = make_version()
    ver["steps"] = [
        {"file": "01-chat.py", "name": "Chat", "description": "",
         "code": "from autowright import reply\n"
                 "for i in range(150):\n"
                 '    reply(f"line {i}")\n'},
    ]
    a = store.create_automation(ver, "Chatterbox", None)
    h = engine.start(a, "discord", payload={"kind": "discord", "channel": "c1",
                                            "secret": "BOT", "sender": "u1"})
    wait_done(engine, h["id"])

    assert h["status"] == "succeeded"
    assert len(sent) == 100
    dropped = [l for l in read_all_logs(store, h["id"]) if "reply dropped" in l["text"]]
    assert len(dropped) == 1
    assert dropped[0]["text"] == "reply dropped — more than 100 replies in one step"


# ---------- §7: a pass that ended in an engine error counts toward the total ----------

def test_engine_error_pass_still_sums_its_duration(store, monkeypatch):
    """§7 retry: "`duration_ms` sums the passes — a pass that ended in an engine
    error counts too"."""
    from autowright import engine as engmod

    def boom(*a, **kw):
        raise RuntimeError("engine blew up")

    monkeypatch.setattr(engmod, "run_step_process", boom)
    engine = engmod.Engine(store)
    a = store.create_automation(make_version(), "Blown", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])

    assert h["status"] == "failed"
    assert isinstance(h["duration_ms"], int)
    assert h["_pass_start"] is None  # §4.5: the pass is over
    assert store.read_exec_yaml(h["id"])["duration_ms"] == h["duration_ms"]


# ---------- §4.5: agentPgids are persisted only when the set gains a group ----------

def test_agent_groups_persist_only_on_a_new_pgid(monkeypatch, tmp_path):
    """§4.5/§3: three agent calls write the record three times — the
    retractions ride the step-end write instead."""
    import subprocess
    import sys

    from autowright import engine as engmod

    real = subprocess.Popen
    emitter = ("import json, sys\n"
               "sys.stdin.read()\n"
               f"CTRL = {CTRL!r}\n"
               "for pgid in (101, 102, 103):\n"
               "    for op in ('agent_group', 'agent_group_done'):\n"
               "        print(CTRL + json.dumps({'op': op, 'pgid': pgid}), flush=True)\n")
    # stand-in for the executor: the six control lines a step with three
    # agent.ask calls emits.
    monkeypatch.setattr(engmod.subprocess, "Popen",
                        lambda argv, **kw: real([sys.executable, "-c", emitter], **kw))
    script = tmp_path / "01-ask.py"
    script.write_text("pass\n", encoding="utf-8")
    persisted = []
    state = {"proc": None, "cancel": False, "on_agent_groups": persisted.append}
    rc = engmod.run_step_process(script, {}, state, lambda kind, text: None,
                                 {"status": None, "chip": None}, {}, None)

    assert rc == 0
    assert persisted == [[101], [102], [103]]
    assert not state.get("agent_pgids")  # every group retracted at call end


# ---------- §6: sys.exit codes are clamped into 1-255 ----------

def _exit_run(store, code_source, name):
    from autowright.engine import Engine

    engine = Engine(store)
    ver = make_version()
    ver["steps"] = [{"file": "01-bye.py", "name": "Bye", "description": "",
                     "code": f"import sys\nsys.exit({code_source})\n"}]
    a = store.create_automation(ver, name, None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    return h


def test_sys_exit_256_fails_the_step_with_code_1(store):
    """§6: "clamped into 1–255 — POSIX keeps only the low byte, so
    `sys.exit(256)` must never read as success"."""
    h = _exit_run(store, "256", "Overflowing exit")
    assert h["status"] == "failed"
    assert h["steps"][0]["attempts"][-1]["error"]["message"] == "step exited with code 1"


def test_sys_exit_booleans_follow_their_int_value(store):
    """§6: `False` is an ordinary early exit, `True` fails with code 1."""
    assert _exit_run(store, "False", "Falsey exit")["status"] == "succeeded"
    h = _exit_run(store, "True", "Truthy exit")
    assert h["status"] == "failed"
    assert h["steps"][0]["attempts"][-1]["error"]["message"] == "step exited with code 1"


# ---------- §8: unknown param keys drop silently ----------

MANIFEST_WITH_A_TYPO = """prose
===FILE: manifest.yaml===
note: Created
params:
  - name: site
    kind: text
    label: Site
    help: the page to read
    default: https://example.com
    placholder: https://example.com
    tone: friendly
steps:
  - { file: 01-a.py, name: A, description: d }
===FILE: 01-a.py===
from autowright import log
log("a")
===END===
"""


def test_manifest_param_keeps_only_the_definition_fields():
    """§8: "a param entry is normalized to the §4.2 definition fields … so a
    misspelled key never reaches a version file or a §5.1 archive"."""
    from autowright.drafting import parse_envelope, validate_steps

    draft, errors = validate_steps(parse_envelope(MANIFEST_WITH_A_TYPO))
    assert errors == []
    assert draft["params"] == [{"name": "site", "kind": "text", "label": "Site",
                                "help": "the page to read",
                                "default": "https://example.com"}]


# ---------- §6: the notification follows the stored chipStatus ----------

def _notify_recorder(monkeypatch):
    from autowright import notify

    calls = []
    monkeypatch.setattr(notify, "post", lambda title, body: calls.append((title, body)))
    return calls


def test_attention_without_a_chip_notifies_nothing(store, monkeypatch):
    """§6: "a `result.status('attention')` … set without a chip stores no
    status … and notifies nothing"."""
    from autowright.engine import Engine

    calls = _notify_recorder(monkeypatch)
    engine = Engine(store)
    ver = make_version()
    ver["steps"] = [{"file": "01-look.py", "name": "Look", "description": "",
                     "code": 'from autowright import result\nresult.status("attention")\n'}]
    a = store.create_automation(ver, "Chipless attention", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])

    assert h["status"] == "succeeded"
    assert h["chip"] is None and h["chip_status"] is None
    assert calls == []

    ver2 = make_version()
    ver2["steps"] = [{"file": "01-look.py", "name": "Look", "description": "",
                      "code": 'from autowright import result\n'
                              'result.status("attention")\nresult.chip("Look at this")\n'}]
    b = store.create_automation(ver2, "Chipped attention", None)
    h2 = engine.start(b, "manual")
    wait_done(engine, h2["id"])

    assert h2["chip_status"] == "attention"
    assert calls == [("Chipped attention", "Look at this")]


# ---------- §8: a scratch-written instructions.md is a validation error ----------

def test_scratch_written_instructions_md_fails_validation(tmp_path):
    """§8: an `instructions.md` rewrite "is a validation error on **both**
    delivery paths — the fenced stdout envelope and the file-writing scratch
    dir alike surface the stray document into the same check"."""
    from autowright import harness
    from autowright.drafting import parse_envelope, validate_chat_files

    (tmp_path / "spec.md").write_text("# Title\n\nDoes things.\n", encoding="utf-8")
    (tmp_path / "instructions.md").write_text("- always be brief\n", encoding="utf-8")
    watcher = harness._ScratchWatcher(tmp_path, harness.ProgressSink())
    watcher._poll()
    documents = watcher.documents()
    assert "instructions.md" in [name for name, _ in documents]

    files = parse_envelope(harness._recombine("Here you go.\n", documents))
    payload, errors, bad = validate_chat_files(files)
    assert payload == {} and bad == {"instructions.md"}
    assert len(errors) == 1 and "instructions.md" in errors[0]
    assert "step files" not in errors[0]  # the stray is a document, not a step


# ---------- §6.2: the trimmed stdlib is rejected ----------

def test_trimmed_stdlib_modules_are_not_importable():
    """§6.2: "Stdlib modules the §3 bundle trim removes … are rejected by the
    same allowlist in every mode"."""
    from autowright.imports_check import disallowed_imports

    assert disallowed_imports("import venv\n") == ["venv"]
    assert disallowed_imports("import tkinter\nfrom turtle import Turtle\n") == \
        ["tkinter", "turtle"]
    assert disallowed_imports("import json\nimport sqlite3\n") == []
