"""Engine, executor and harness audit regressions (2026-10-31, third sweep).

§3 interpreter isolation rides as `-s -P` flags, never inherited variables;
§6.1 the reply budget is per execution and a queued send for a terminal
record is dropped; §7 the manual-retry log line follows the disk write; §8
the result.md excerpt reads only the head; §7 non-object control lines are
dropped; §6 the robots read is a spaced site hit under the page's deadline;
§6.3 an idle start sweeps memory temp files; §4.8 whitespace-stripped secret
forms; §7 first-piece-only cuts guard long secrets; §19 a JSONC opencode.json
is left untouched and Ollama requests skip proxies; §8 step file names are
capped at 120 characters; §6 a BOM-prefixed step compiles.
"""
import http.server
import os
import threading
import time
import urllib.request
import uuid

import pytest
from conftest import make_version, read_all_logs, wait_done

_PAYLOAD = {"kind": "discord", "channel": "c1", "secret": "BOT", "sender": "u1"}


def _one_step(code: str, **step):
    ver = make_version()
    ver["steps"] = [{"file": "01-step.py", "name": "Step", "description": "",
                     "code": code, **step}]
    return ver


def _texts(store, execution_id):
    return [line["text"] for line in read_all_logs(store, execution_id)]


# ---------- F: §3 isolation as interpreter flags ----------

def test_the_executor_spawn_carries_the_isolation_flags(store, monkeypatch):
    from autowright import engine as engine_module
    from autowright.engine import Engine

    real_popen = engine_module.subprocess.Popen
    commands = []

    def recording_popen(cmd, *args, **kwargs):
        commands.append(list(cmd))
        return real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(engine_module.subprocess, "Popen", recording_popen)
    engine = Engine(store)
    a = store.create_automation(_one_step("from autowright import log\nlog('hi')\n"),
                                "Flags", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "succeeded"
    spawn = next(c for c in commands if "autowright.executor" in c)
    assert spawn[1:5] == ["-s", "-P", "-m", "autowright.executor"]


def test_the_executor_drops_the_isolation_variables_before_step_code(store, monkeypatch):
    """A v0.14.0 service definition still sets the pair: even when the spawn
    env carries it, neither the step nor its own subprocesses see it."""
    from autowright import engine as engine_module
    from autowright.engine import Engine

    def leaky_env():
        env = dict(os.environ)
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONSAFEPATH"] = "1"
        return env

    monkeypatch.setattr(engine_module.harness, "step_env", leaky_env)
    engine = Engine(store)
    a = store.create_automation(_one_step(
        "import os, subprocess, sys\nfrom autowright import log\n"
        "log('step ' + repr(os.environ.get('PYTHONSAFEPATH')) + "
        "repr(os.environ.get('PYTHONNOUSERSITE')))\n"
        "out = subprocess.run([sys.executable, '-c', 'import os; "
        "print(os.environ.get(\"PYTHONSAFEPATH\"), os.environ.get(\"PYTHONNOUSERSITE\"))'], "
        "capture_output=True, text=True).stdout.strip()\n"
        "log('child ' + out)\n"), "Leaky", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "succeeded"
    texts = _texts(store, h["id"])
    assert "step NoneNone" in texts
    assert "child None None" in texts


# ---------- J: §6.1 reply budget per execution ----------

def test_the_reply_budget_spans_the_attempts_of_an_execution(store, monkeypatch):
    from autowright import listeners
    from autowright.engine import Engine

    sent = []
    monkeypatch.setattr(listeners, "send_reply",
                        lambda payload, text: sent.append(text) or None)
    monkeypatch.setattr(listeners, "submit_send", lambda fn: fn())
    engine = Engine(store)
    code = ("import os\nfrom autowright import reply\n"
            "first = not os.path.exists('marker')\n"
            "open('marker', 'w').close()\n"
            "for i in range(60):\n"
            "    reply(f'line {i}')\n"
            "if first:\n"
            "    raise RuntimeError('try again')\n")
    a = store.create_automation(_one_step(code, retries=1), "Two attempts", None)
    h = engine.start(a, "discord", payload=dict(_PAYLOAD))
    wait_done(engine, h["id"])
    assert h["status"] == "succeeded"
    assert len(h["steps"][0]["attempts"]) == 2
    assert len(sent) == 100
    dropped = [t for t in _texts(store, h["id"]) if "reply dropped" in t]
    assert dropped == ["reply dropped — more than 100 replies in one execution"]


def test_a_reply_queued_for_a_cancelled_record_is_never_sent(store, monkeypatch):
    from autowright import listeners
    from autowright.engine import Engine

    sent, held = [], []
    monkeypatch.setattr(listeners, "send_reply",
                        lambda payload, text: sent.append(text) or None)
    monkeypatch.setattr(listeners, "submit_send", held.append)
    engine = Engine(store)
    a = store.create_automation(_one_step(
        "import time\nfrom autowright import reply\nreply('hello')\ntime.sleep(30)\n"),
        "Cancelled reply", None)
    h = engine.start(a, "discord", payload=dict(_PAYLOAD))
    deadline = time.monotonic() + 10
    while not held and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(held) == 1
    engine.cancel(h["id"])
    wait_done(engine, h["id"])
    assert h["status"] == "cancelled"
    held[0]()
    assert sent == []
    assert not any("reply sent" in t for t in _texts(store, h["id"]))


def test_a_queued_reply_holds_only_the_clipped_text(store, monkeypatch):
    from autowright import listeners
    from autowright.engine import Engine

    sent = []
    monkeypatch.setattr(listeners, "send_reply",
                        lambda payload, text: sent.append(text) or None)
    monkeypatch.setattr(listeners, "submit_send", lambda fn: fn())
    engine = Engine(store)
    a = store.create_automation(_one_step(
        "from autowright import reply\nreply('x' * 5000)\n"), "Long reply", None)
    h = engine.start(a, "discord", payload=dict(_PAYLOAD))
    wait_done(engine, h["id"])
    assert sent == ["x" * listeners.REPLY_LIMIT]


# ---------- Q: §7 the retry line follows the disk write ----------

def test_a_failed_retry_write_leaves_no_retry_log_line(store, monkeypatch):
    from autowright.engine import Engine

    engine = Engine(store)
    a = store.create_automation(_one_step("raise RuntimeError('boom')\n"), "Retry", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "failed"

    def failing_write(record):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store, "update_execution", failing_write)
    with pytest.raises(OSError):
        engine.retry(a, h)
    assert not any(t.startswith("retrying from step") for t in _texts(store, h["id"]))
    assert store.execs[h["id"]]["status"] == "failed"


# ---------- T: §8 only the head of result.md is read ----------

def test_the_result_excerpt_reads_only_the_head_of_result_md(store, monkeypatch):
    from pathlib import Path

    from autowright import keychain, testexec
    from autowright.engine import Engine

    monkeypatch.setattr(testexec, "store", store)
    secret_id = str(uuid.uuid4())
    store.secrets.append({"id": secret_id, "name": "KEY", "description": "", "set": True})
    keychain.set_secret(secret_id, "sk-result-secret-1234")
    engine = Engine(store)
    a = store.create_automation(_one_step(
        "from autowright import result\n"
        "(result / 'result.md').write_text('y' * (5 * 1024 * 1024))\n"), "Big result", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "succeeded"

    reads = []
    real_open = Path.open

    def recording_open(self, *args, **kwargs):
        handle = real_open(self, *args, **kwargs)
        if self.name != "result.md":
            return handle
        real_read = handle.read

        def bounded_read(size=-1):
            reads.append(size)
            return real_read(size)

        handle.read = bounded_read
        return handle

    def no_full_read(self, *args, **kwargs):
        raise AssertionError("result.md must never be read whole")

    monkeypatch.setattr(Path, "open", recording_open)
    real_read_text = Path.read_text
    monkeypatch.setattr(Path, "read_text", lambda self, *args, **kwargs: (
        no_full_read(self) if self.name == "result.md" else real_read_text(self, *args, **kwargs)))
    block = testexec.executions_context(a, [])
    assert block is not None
    assert "result.md:\n" + "y" * testexec.RESULT_EXCERPT + "\n… [result.md truncated]" in block
    probe = len("sk-result-secret-1234")
    assert reads == [testexec.RESULT_EXCERPT + probe + 1]


# ---------- U: §7 a non-object control line is dropped ----------

def test_control_lines_that_are_not_objects_never_crash_the_step(store):
    from autowright.engine import Engine

    engine = Engine(store)
    a = store.create_automation(_one_step(
        "import os\nfrom autowright import log\n"
        "os.write(1, b'@@AD@@null\\n')\n"
        "os.write(1, b'@@AD@@[]\\n')\n"
        "os.write(1, b'@@AD@@{\"op\": \"log\", \"text\": null}\\n')\n"
        "os.write(1, b'@@AD@@{\"op\": \"log\", \"kind\": 7, \"text\": 3}\\n')\n"
        "os.write(1, b'@@AD@@{\"op\": \"error\", \"type\": \"X\", \"message\": null}\\n')\n"
        "log('still here')\n"), "Control", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "succeeded"
    texts = _texts(store, h["id"])
    assert "still here" in texts
    assert "3" in texts


# ---------- W: §6 the robots read is a site hit ----------

class _Trickle(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802 - http.server API
        if self.path == "/robots.txt":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            try:
                for _ in range(40):
                    self.wfile.write(b"# x\n")
                    self.wfile.flush()
                    threading.Event().wait(0.05)  # time.sleep is patched
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        body = b"page"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def trickle(monkeypatch):
    from autowright import executor

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Trickle)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(executor, "_robots", {})
    monkeypatch.setattr(executor, "_site_last", {})
    monkeypatch.setattr(executor, "_sites_file", None)
    sleeps = []
    monkeypatch.setattr(executor.time, "sleep", sleeps.append)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", sleeps
    finally:
        server.shutdown()
        server.server_close()


def test_a_trickling_robots_read_hits_the_fetch_deadline(trickle, monkeypatch):
    from autowright import executor

    base, _ = trickle
    monkeypatch.setattr(executor, "FETCH_DEADLINE_S", 0.3)
    errors = []
    real_read_page = executor._read_page

    def recording_read_page(r, deadline):
        try:
            return real_read_page(r, deadline)
        except Exception as e:
            errors.append(e)
            raise

    monkeypatch.setattr(executor, "_read_page", recording_read_page)
    started = time.monotonic()
    assert executor.fetch_page(f"{base}/page") == "page"  # a failed robots read allows
    assert time.monotonic() - started < 1.5
    assert len(errors) == 1 and isinstance(errors[0], TimeoutError)
    assert "the fetch took over" in str(errors[0])


def test_the_page_request_is_spaced_after_the_robots_hit(trickle, monkeypatch):
    from autowright import executor

    base, sleeps = trickle
    hits = []
    real_stamp = executor._write_site_hit

    def recording_stamp(host, at):
        hits.append(time.monotonic())
        real_stamp(host, at)

    monkeypatch.setattr(executor, "_write_site_hit", recording_stamp)
    monkeypatch.setattr(executor, "FETCH_DEADLINE_S", 0.3)
    executor.fetch_page(f"{base}/page")
    assert len(hits) == 2  # robots, then the page
    assert len(sleeps) == 1
    # The page waited out the spacing counted from the robots hit.
    assert hits[1] - hits[0] + sleeps[0] >= executor.SITE_SPACING_S - 0.05


# ---------- AJ: §6.3 an idle start sweeps memory temp files ----------

def test_a_start_with_nothing_live_sweeps_memory_temp_files(store):
    from autowright.engine import Engine

    engine = Engine(store)
    a = store.create_automation(make_version(), "Sweep", None)
    memory = store.auto_dir(a) / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    (memory / ".foo.ab12.tmp").write_text("half", encoding="utf-8")
    (memory / "keep.yaml").write_text("a: 1\n", encoding="utf-8")
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert not (memory / ".foo.ab12.tmp").exists()
    assert (memory / "keep.yaml").exists()


# ---------- AL: §4.8 the whitespace-stripped form is a probe ----------

def test_a_whitespace_padded_secret_redacts_its_stripped_form():
    from autowright.engine import build_redactions, redact_text
    from autowright.executor import secret_probes

    secret_id = str(uuid.uuid4())
    assert "ghp_abcdef123" in secret_probes(" ghp_abcdef123 ")
    redactions = build_redactions({secret_id: " ghp_abcdef123 "}, {secret_id: "TOKEN"})
    assert redact_text("token=ghp_abcdef123;", redactions) == "token=•••;"
    # The 8-character minimum still holds for the stripped form.
    assert "abc" not in secret_probes(" abc ")


# ---------- AM: §7 a first-piece-only cut guards a long secret ----------

def test_the_chip_holds_no_prefix_of_a_secret_straddling_its_cut(monkeypatch, tmp_path):
    from autowright import executor

    secret = "S" + "k" * 1498 + "Z"
    monkeypatch.setattr(executor, "_cut_guards", executor.secret_probes(secret))
    emitted = []
    monkeypatch.setattr(executor, "emit", lambda op, **kw: emitted.append((op, kw)))
    executor.Result(str(tmp_path)).chip("a" * 500 + secret + "b")
    assert emitted == [("result", {"field": "chip", "value": "a" * 500})]
    assert executor._guarded_clip("a" * 3000 + secret, 4000).startswith("a" * 3000 + "…")


# ---------- O: §19 a JSONC opencode.json stays untouched ----------

def test_a_jsonc_opencode_config_is_left_byte_identical(monkeypatch, tmp_path):
    from autowright import harness

    cfg = tmp_path / "opencode.json"
    original = b'{\n  // my providers\n  "provider": {"x": {}},\n}\n'
    cfg.write_bytes(original)
    monkeypatch.setattr(harness, "_OPENCODE_CONFIG", str(cfg))
    with pytest.raises(harness.HarnessError,
                       match="opencode.json isn't plain JSON — add the ollama provider by hand"):
        harness.sync_opencode_ollama("qwen3:8b")
    assert cfg.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == [".opencode.json.lock", "opencode.json"]


# ---------- V: §19 Ollama probes skip proxies ----------

def test_the_ollama_probes_go_through_the_no_proxy_opener(monkeypatch):
    from autowright import harness

    # ProxyHandler({}) registers no proxy_open methods, so the opener holds
    # no ProxyHandler at all (the default one would read the environment).
    assert not any(isinstance(h, urllib.request.ProxyHandler)
                   for h in harness._OLLAMA_OPENER.handlers)

    urls = []

    def fake_open(url, timeout):
        urls.append((url, timeout))
        raise OSError("down")

    monkeypatch.setattr(harness, "ollama_urlopen", fake_open)
    monkeypatch.setattr(harness.urllib.request, "urlopen",
                        lambda *a, **k: pytest.fail("plain urlopen used"))
    assert harness._ollama_models() is None
    assert harness._ollama_version() is None
    assert urls == [(f"{harness.OLLAMA_URL}/api/tags", 2),
                    (f"{harness.OLLAMA_URL}/api/version", 2)]


# ---------- AV: §8 step file names are capped at 120 characters ----------

def test_a_300_character_step_file_name_fails_validation():
    from autowright.drafting import parse_envelope, validate_steps
    from autowright.harness import STEP_FILE_RE

    long_name = "01-" + "a" * 294 + ".py"
    assert len(long_name) == 300
    assert STEP_FILE_RE.match(long_name) is None
    assert STEP_FILE_RE.match("01-" + "a" * 114 + ".py")  # exactly 120
    text = "\n".join([
        "===FILE: manifest.yaml===", "note: n", "steps:",
        f"  - {{ file: {long_name}, name: Long, description: d }}",
        f"===FILE: {long_name}===", 'from autowright import log\nlog("x")',
        "===END===", ""])
    _, errors = validate_steps(parse_envelope(text), {"agents": [], "secrets": []})
    assert any("doesn't follow NN-name.py" in e for e in errors)


# ---------- AS: §6 a BOM-prefixed step compiles ----------

def test_a_bom_prefixed_step_script_runs(store):
    from autowright.engine import Engine

    engine = Engine(store)
    a = store.create_automation(_one_step(
        "﻿from autowright import log\nlog('bom ok')\n"), "Bom", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "succeeded"
    assert "bom ok" in _texts(store, h["id"])
