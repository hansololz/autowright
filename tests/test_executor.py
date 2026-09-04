"""Step-SDK surface of the executor (§6.1) — pure parts, no real subprocess."""
import io
import json

import pytest


@pytest.fixture()
def ctrl(monkeypatch):
    """Capture @@AD@@ control lines emit() writes to the real stdout."""
    from autowright import executor

    buf = io.StringIO()
    monkeypatch.setattr(executor, "_real_stdout", buf)

    def lines():
        out = []
        for ln in buf.getvalue().splitlines():
            assert ln.startswith(executor.CTRL)
            out.append(json.loads(ln[len(executor.CTRL):]))
        return out

    return lines


# ---------- Secrets ----------

# §4.8/§6.1: secrets are addressed by id subscript; errors label by NAME via
# the id→name map. Fixed test uuids keep the assertions readable.
TOKEN_ID = "11111111-1111-1111-1111-111111111111"
OTHER_ID = "22222222-2222-2222-2222-222222222222"
NOPE_ID = "33333333-3333-3333-3333-333333333333"
SECRET_NAMES = {TOKEN_ID: "TOKEN", OTHER_ID: "OTHER"}


def test_secrets_allowed_and_injected():
    from autowright.executor import Secrets

    s = Secrets({TOKEN_ID: "abc"}, [TOKEN_ID, OTHER_ID], SECRET_NAMES)
    assert s[TOKEN_ID] == "abc"


def test_secrets_allowed_but_not_in_keychain():
    from autowright.executor import MissingSecret, Secrets

    s = Secrets({TOKEN_ID: "abc"}, [TOKEN_ID, OTHER_ID], SECRET_NAMES)
    with pytest.raises(MissingSecret, match="OTHER wasn't injected into this step"):
        s[OTHER_ID]


def test_secrets_not_allowed():
    from autowright.executor import MissingSecret, Secrets

    s = Secrets({TOKEN_ID: "abc"}, [TOKEN_ID], SECRET_NAMES)
    # an unknown id has no name to label with — the short id prefix stands in
    with pytest.raises(MissingSecret, match="33333333… is not allowed for this automation"):
        s[NOPE_ID]


def test_secrets_attribute_access_is_retired():
    from autowright.executor import MissingSecret, Secrets

    s = Secrets({TOKEN_ID: "abc"}, [TOKEN_ID], SECRET_NAMES)
    # §6.1: secrets.NAME does not exist anymore — the message points at ids
    with pytest.raises(MissingSecret, match=r'use secrets\["<secret id>"\]'):
        s.TOKEN


def test_secrets_underscore_attrs_raise_attribute_error():
    from autowright.executor import Secrets

    s = Secrets({}, [], {})
    with pytest.raises(AttributeError):
        s._missing


def test_secrets_container_holds_only_the_step_subset():
    """§6/§6.1: the container holds the step's injected values only — every
    other automation-wide grant raises, allowed or not."""
    from autowright.executor import MissingSecret, Secrets

    s = Secrets({TOKEN_ID: "abc"}, [TOKEN_ID, OTHER_ID, NOPE_ID],
                {**SECRET_NAMES, NOPE_ID: "NOPE"})
    assert s[TOKEN_ID] == "abc"
    for other in (OTHER_ID, NOPE_ID):
        with pytest.raises(MissingSecret, match="wasn't injected into this step"):
            s[other]


# ---------- Result ----------

def test_result_status_validation(tmp_path, ctrl):
    from autowright.executor import Result

    r = Result(str(tmp_path / "res"))
    with pytest.raises(ValueError, match="result.status must be"):
        r.status("junk")
    r.status("ok")
    assert ctrl() == [{"op": "result", "field": "status", "value": "ok"}]


def test_result_chip_coerces_to_str(tmp_path, ctrl):
    from autowright.executor import Result

    r = Result(str(tmp_path / "res"))
    r.chip(42)
    assert ctrl() == [{"op": "result", "field": "chip", "value": "42"}]


# ---------- Agent._ask guard rails ----------

HELPER_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LOCAL_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def make_ctx(**over):
    ctx = {
        "is_agent_step": True,
        "agents": [{"id": HELPER_ID, "name": "helper", "harness": "claude"}],
        "secrets": {"TOKEN": "s3cr3t-value"},
        "secret_names_with_values": ["TOKEN"],
        "agent_timeout": 5,
    }
    ctx.update(over)
    return ctx


def make_agent(ctx=None, **over):
    """Build the bare `agent` handle the way main() does: bound to the step's
    first agents entry, with the automation-wide scan map handed over
    explicitly (§6)."""
    from autowright.executor import Agent

    ctx = make_ctx(**over) if ctx is None else ctx
    scan = ctx.pop("scan_secrets", None) or ctx.get("secrets", {})
    return Agent(ctx, scan, (ctx.get("agents") or [None])[0])


def make_agents(ctx=None, **over):
    """Build the `agents["<id>"]` container the way main() does."""
    from autowright.executor import Agents

    ctx = make_ctx(**over) if ctx is None else ctx
    scan = ctx.pop("scan_secrets", None) or ctx.get("secrets", {})
    return Agents(ctx, scan)


@pytest.fixture()
def invoke_spy(monkeypatch):
    from autowright import executor

    calls = []

    # Strict signature on purpose: §6 runtime agent.ask calls must never pass
    # web= (drafting-only) — if the executor ever did, this spy TypeErrors.
    # on_spawn is the §7 agent-group report the executor DOES pass.
    def fake_invoke(cfg, prompt, timeout=120, on_spawn=None):
        calls.append((cfg, prompt, timeout))
        return "  the reply  "

    monkeypatch.setattr(executor._harness, "invoke", fake_invoke)
    return calls


def test_ask_happy_path_emits_audit(ctrl, invoke_spy):
    a = make_agent()
    assert a.ask("summarize", data="rows") == "the reply"
    assert len(invoke_spy) == 1
    assert invoke_spy[0][1] == "question: summarize\n\ndata:\nrows"
    audit = [e for e in ctrl() if e["op"] == "agent_audit"]
    # §6: the full prompt/reply are logged for audit
    assert audit == [{"op": "agent_audit",
                      "prompt": "question: summarize\n\ndata:\nrows",
                      "reply": "  the reply  "}]


def test_ask_reports_agent_group_start_and_done(ctrl, monkeypatch):
    """§7 kill semantics: the harness CLI spawns in its own session, so the
    executor reports its group at spawn and retracts it when the call ends —
    on failure too (the finally), or a killed call would leave a stale group
    id for a later step's kill to re-signal."""
    from autowright import executor

    class FakeProc:
        pid = 4242

    def fake_invoke(cfg, prompt, timeout=120, on_spawn=None):
        on_spawn(FakeProc())
        return "ok"

    monkeypatch.setattr(executor._harness, "invoke", fake_invoke)
    a = make_agent()
    assert a.ask("hi") == "ok"
    events = [e for e in ctrl() if e["op"] in ("agent_group", "agent_group_done")]
    assert events == [{"op": "agent_group", "pgid": 4242},
                      {"op": "agent_group_done", "pgid": 4242}]

    def failing_invoke(cfg, prompt, timeout=120, on_spawn=None):
        on_spawn(FakeProc())
        raise ValueError("cli died")

    monkeypatch.setattr(executor._harness, "invoke", failing_invoke)
    with pytest.raises(executor.AgentCallError):
        make_agent().ask("hi")
    events = [e for e in ctrl() if e["op"] in ("agent_group", "agent_group_done")]
    assert events[-1] == {"op": "agent_group_done", "pgid": 4242}


def test_ask_refuses_secret_value_in_prompt(ctrl, invoke_spy):
    a = make_agent()
    with pytest.raises(RuntimeError, match="contains the value of secret TOKEN"):
        a.ask("please use s3cr3t-value to log in")
    # the secret may hide in the data payload too — same refusal
    with pytest.raises(RuntimeError, match="contains the value of secret TOKEN"):
        a.ask("harmless question", data={"auth": "s3cr3t-value"})
    assert invoke_spy == []


def test_ask_refuses_single_line_of_multiline_secret(ctrl, invoke_spy):
    ctx = make_ctx(secrets={"PEM": "AAA-first\nBBB-second\nCCC-third"},
                   secret_names_with_values=["PEM"])
    a = make_agent(ctx)
    with pytest.raises(RuntimeError, match="contains the value of secret PEM"):
        a.ask("header BBB-second footer")  # one pasted line is enough
    assert invoke_spy == []


def test_ask_caps_prompt_at_200k(ctrl, invoke_spy):
    a = make_agent()
    with pytest.raises(RuntimeError, match="agent prompt too large"):
        a.ask("x" * 200_001)
    assert invoke_spy == []
    assert a.ask("x" * 200_000) == "the reply"  # exactly at the cap still goes


def test_agents_subscript_picks_declared_entry_by_id(ctrl, invoke_spy):
    ctx = make_ctx(agents=[{"id": HELPER_ID, "name": "helper", "harness": "claude"},
                           {"id": LOCAL_ID, "name": None, "harness": "opencode"}])
    ags = make_agents(ctx)
    assert ags[LOCAL_ID].ask("hi") == "the reply"
    assert invoke_spy[0][0]["id"] == LOCAL_ID


def test_agents_unknown_id_lists_available(ctrl, invoke_spy):
    ctx = make_ctx(agents=[{"id": HELPER_ID, "name": "helper", "harness": "claude"},
                           {"id": LOCAL_ID, "name": None, "harness": "opencode"}])
    ags = make_agents(ctx)
    # §6.1: the error lists the step's agents as `Name (id)` — grant names,
    # falling back to the harness name for unnamed agents
    with pytest.raises(RuntimeError,
                       match=rf"isn't among this step's declared agents.*helper \({HELPER_ID}\), "
                             rf"opencode \({LOCAL_ID}\)"):
        ags["44444444-4444-4444-4444-444444444444"]
    assert invoke_spy == []


def test_ask_outside_agent_step_or_without_agents(ctrl, invoke_spy):
    with pytest.raises(RuntimeError, match="only available in steps marked as agent steps"):
        make_agent(is_agent_step=False).ask("hi")
    with pytest.raises(RuntimeError, match="no enabled agent for this step"):
        make_agent(agents=[]).ask("hi")
    assert invoke_spy == []


def test_agents_container_holds_only_declared_entries(ctrl, invoke_spy):
    """§6/§6.1: the container is built from the step's declared `agents:`
    entries alone — an agent the automation enables but the step doesn't list
    is not in it."""
    ags = make_agents(make_ctx())
    with pytest.raises(RuntimeError, match=rf"it can call: helper \({HELPER_ID}\)$"):
        ags[LOCAL_ID]
    with pytest.raises(RuntimeError, match="it can call: none$"):
        make_agents(make_ctx(agents=[]))[HELPER_ID]
    assert invoke_spy == []


def test_ask_wraps_invoke_failure_naming_harness(ctrl, monkeypatch):
    """§7: a failing harness invoke surfaces as AgentCallError naming the
    harness, so the engine can classify it as an agent failure."""
    from autowright import executor

    def boom(cfg, prompt, timeout=120, on_spawn=None):
        raise ValueError("cli went away")

    monkeypatch.setattr(executor._harness, "invoke", boom)
    a = make_agent()
    with pytest.raises(executor.AgentCallError,
                       match=r"agent call failed \(claude\): cli went away"):
        a.ask("hi")


def test_ask_caps_reply_at_200k(ctrl, monkeypatch):
    from autowright import executor

    monkeypatch.setattr(executor._harness, "invoke",
                        lambda cfg, prompt, timeout=120, on_spawn=None: "y" * 200_001)
    a = make_agent()
    with pytest.raises(RuntimeError, match="agent reply too large"):
        a.ask("hi")
    # exactly at the cap still goes through
    monkeypatch.setattr(executor._harness, "invoke",
                        lambda cfg, prompt, timeout=120, on_spawn=None: "y" * 200_000)
    assert a.ask("hi") == "y" * 200_000


# ---------- _LineWriter (stdout/stderr shim) ----------

def test_line_writer_splits_lines_and_suppresses_blanks(ctrl):
    from autowright.executor import _LineWriter

    w = _LineWriter("out")
    assert w.write("a\nb\npartial") == len("a\nb\npartial")
    assert [e["text"] for e in ctrl()] == ["a", "b"]  # partial stays buffered
    w.write("-rest\n\n   \n x\n")
    events = ctrl()
    # blank and whitespace-only lines are suppressed; leading spaces survive
    assert [e["text"] for e in events] == ["a", "b", "partial-rest", " x"]
    assert all(e["op"] == "log" and e["kind"] == "out" for e in events)
    assert w.buf == ""


def test_line_writer_overflow_flushes_without_newline(ctrl):
    from autowright.executor import _LineWriter

    w = _LineWriter("out")
    w.write("x" * w.MAX_LINE)  # exactly at the cap: still buffered
    assert ctrl() == []
    w.write("x")  # over the cap: the whole buffer flushes as one line
    events = ctrl()
    assert [e["text"] for e in events] == ["x" * (w.MAX_LINE + 1)]
    assert w.buf == ""


def test_line_writer_flush_emits_partial_line(ctrl):
    from autowright.executor import _LineWriter

    w = _LineWriter("err")
    w.write("tail no newline")
    assert ctrl() == []
    w.flush()
    assert ctrl() == [{"op": "log", "kind": "err", "text": "tail no newline"}]
    w.flush()  # empty buffer: nothing new
    assert len(ctrl()) == 1
    w.write("   ")
    w.flush()  # whitespace-only partial: suppressed, buffer still cleared
    assert len(ctrl()) == 1
    assert w.buf == ""


# ---------- Memory.save cleanup ----------

def test_memory_save_failed_replace_removes_tmp_keeps_old_value(tmp_path, monkeypatch):
    """§6: a failure at the final os.replace (past the YAML dump — the temp
    file exists and holds the new content) removes the temp file and leaves
    the previous value intact."""
    import os

    from autowright.executor import Memory

    m = Memory(str(tmp_path / "memory"))
    m.save("notes", {"a": 1})

    calls = []

    def boom(src, dst):
        calls.append((src, dst))
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        m.save("notes", {"a": 2})
    assert calls, "the dump must succeed and reach os.replace"
    assert m.load("notes") == {"a": 1}  # previous version intact
    assert [p.name for p in (tmp_path / "memory").iterdir()] == ["notes.yaml"]  # no tmp litter


# ---------- emit framing ----------

def test_emit_control_line_roundtrip(ctrl):
    from autowright import executor

    executor.emit("log", kind="out", text="héllo @@AD@@ world")
    executor.emit("notify", text="done")
    assert executor.CTRL == "@@AD@@"
    assert ctrl() == [
        {"op": "log", "kind": "out", "text": "héllo @@AD@@ world"},
        {"op": "notify", "text": "done"},
    ]


def test_emit_keeps_unicode_unescaped(monkeypatch):
    from autowright import executor

    buf = io.StringIO()
    monkeypatch.setattr(executor, "_real_stdout", buf)
    executor.emit("log", kind="out", text="héllo")
    line = buf.getvalue()
    assert line.startswith(executor.CTRL) and line.endswith("\n")
    assert "héllo" in line  # ensure_ascii=False: no é escaping


# ---------- Execution metadata ----------

def test_execution_metadata_read_only():
    from autowright.executor import Execution

    e = Execution({"automation_id": "a-1", "automation_name": "Job", "id": "e-1",
                   "step_index": 2, "step_name": "Say hello", "trigger": "Manual"})
    assert e.automation_id == "a-1"
    assert e.automation_name == "Job"
    assert e.id == "e-1"
    assert e.step_index == 2
    assert e.step_name == "Say hello"
    assert e.trigger == "Manual"
    with pytest.raises(AttributeError, match="read-only"):
        e.id = "other"
    # absent meta keys read as None rather than raising
    assert Execution({}).trigger is None


# ---------- fetch_page (§6 web policies) — urllib and clock monkeypatched ----------

class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def clean_fetch_state(monkeypatch):
    """Isolated robots/spacing caches and a controllable clock, no real sleeps."""
    from autowright import executor

    monkeypatch.setattr(executor, "_robots", {})
    monkeypatch.setattr(executor, "_site_last", {})
    clock = {"t": 1000.0}
    sleeps: list[float] = []

    def fake_sleep(s: float) -> None:
        sleeps.append(s)
        clock["t"] += s

    monkeypatch.setattr(executor.time, "time", lambda: clock["t"])
    monkeypatch.setattr(executor.time, "sleep", fake_sleep)
    return clock, sleeps


def _urlopen_router(monkeypatch, robots, page):
    """Route urlopen: /robots.txt → `robots` (bytes | Exception), else `page`
    (bytes | Exception | list of per-attempt values). Returns the call log."""
    import urllib.request

    from autowright import executor

    calls: list[str] = []
    pages = list(page) if isinstance(page, list) else None

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        calls.append(url)
        assert timeout == 10
        out = robots if url.endswith("/robots.txt") else (pages.pop(0) if pages else page)
        if isinstance(out, Exception):
            raise out
        return _FakeResp(out)

    monkeypatch.setattr(executor.urllib.request, "urlopen", fake_urlopen)
    return calls


def test_fetch_page_robots_403_disallows_all(monkeypatch, clean_fetch_state):
    import urllib.error

    from autowright.executor import fetch_page

    err = urllib.error.HTTPError("u", 403, "forbidden", None, None)
    _urlopen_router(monkeypatch, err, b"body")
    with pytest.raises(RuntimeError, match="robots.txt disallows"):
        fetch_page("https://example.com/a")


def test_fetch_page_robots_404_allows_and_returns_body(monkeypatch, clean_fetch_state):
    import urllib.error

    from autowright.executor import fetch_page

    err = urllib.error.HTTPError("u", 404, "not found", None, None)
    calls = _urlopen_router(monkeypatch, err, "héllo".encode())
    assert fetch_page("https://example.com/a") == "héllo"
    # robots fetched once, then cached for the host
    assert fetch_page("https://example.com/b") == "héllo"
    assert calls.count("https://example.com/robots.txt") == 1


def test_fetch_page_robots_rule_blocks_matching_path(monkeypatch, clean_fetch_state):
    from autowright.executor import fetch_page

    robots = b"User-agent: *\nDisallow: /private/\n"
    _urlopen_router(monkeypatch, robots, b"body")
    assert fetch_page("https://example.com/open") == "body"
    with pytest.raises(RuntimeError, match="robots.txt disallows"):
        fetch_page("https://example.com/private/x")


def test_fetch_page_robots_network_error_allows(monkeypatch, clean_fetch_state):
    from autowright.executor import fetch_page

    _urlopen_router(monkeypatch, OSError("robots black hole"), b"body")
    assert fetch_page("https://example.com/a") == "body"


def test_fetch_page_spaces_same_host_by_two_seconds(monkeypatch, clean_fetch_state):
    from autowright.executor import fetch_page

    clock, sleeps = clean_fetch_state
    _urlopen_router(monkeypatch, b"", b"body")
    fetch_page("https://example.com/a")
    assert sleeps == []  # first hit: no wait
    clock["t"] += 0.5
    fetch_page("https://example.com/b")
    assert len(sleeps) == 1 and sleeps[0] == pytest.approx(1.5)  # tops up to 2s
    clock["t"] += 10
    fetch_page("https://example.com/c")
    assert len(sleeps) == 1  # ≥2s already elapsed: no extra wait


def test_fetch_page_retries_twice_then_raises(monkeypatch, clean_fetch_state):
    from autowright.executor import fetch_page

    _, sleeps = clean_fetch_state
    calls = _urlopen_router(monkeypatch, b"", OSError("conn reset"))
    with pytest.raises(RuntimeError, match="couldn't fetch .*conn reset"):
        fetch_page("https://example.com/a")
    assert calls.count("https://example.com/a") == 3  # first try + two retries
    assert sleeps == [2, 2]  # no sleep after the final failure


def test_fetch_page_second_attempt_succeeds(monkeypatch, clean_fetch_state):
    from autowright.executor import fetch_page

    _urlopen_router(monkeypatch, b"", [OSError("flaky"), b"recovered"])
    assert fetch_page("https://example.com/a") == "recovered"


# ---------- main() in-process (§6.1 subprocess entry, run without a subprocess) ----------

@pytest.fixture()
def run_main(tmp_path, monkeypatch, ctrl):
    """Run executor.main() in-process with a scripted step + ctx, restoring every
    global main() mutates: the sys.modules["autowright"] SDK shim, stdout/stderr,
    sys.path, AUTOWRIGHT_* env, and the cwd. Returns (rc, control_lines)."""
    import os
    import sys as _sys

    def run(source: str, **ctx_overrides):
        from autowright import executor

        script = tmp_path / "step.py"
        script.write_text(source, encoding="utf-8")
        ctx = {
            "scan_secrets": {},
            "workspace": str(tmp_path / "ws"),
            "memory_dir": str(tmp_path / "memory"),
            "result_dir": str(tmp_path / "result"),
        }
        ctx.update(ctx_overrides)
        monkeypatch.setattr(_sys, "argv", ["executor", str(script)])
        monkeypatch.setattr(_sys, "stdin", io.StringIO(json.dumps(ctx)))
        saved_mod = _sys.modules["autowright"]
        saved_out, saved_err = _sys.stdout, _sys.stderr
        saved_path = list(_sys.path)
        saved_env = {k: v for k, v in os.environ.items() if k.startswith("AUTOWRIGHT_")}
        saved_cwd = os.getcwd()
        try:
            rc = executor.main()
        finally:
            _sys.modules["autowright"] = saved_mod
            _sys.stdout, _sys.stderr = saved_out, saved_err
            _sys.path[:] = saved_path
            for k in [k for k in os.environ if k.startswith("AUTOWRIGHT_")]:
                del os.environ[k]
            os.environ.update(saved_env)
            os.chdir(saved_cwd)
        return rc, ctrl()

    return run


def test_main_happy_path_sdk_env_and_logs(run_main, tmp_path):
    rc, lines = run_main(
        "import os\n"
        "from autowright import params, log, workspace\n"
        "print('plain', params['n'])\n"
        "log.warn('careful')\n"
        "print('exec id', os.environ['AUTOWRIGHT_EXECUTION_ID'])\n"
        "print('step', os.environ['AUTOWRIGHT_STEP_NAME'])\n"
        "open('made.txt', 'w').write('x')\n",
        params={"n": 7},
        execution={"id": "e1", "automation_id": "a1", "automation_name": "A",
                   "step_index": 0, "step_name": "fetch", "trigger": "cron"},
    )
    assert rc == 0
    logs = [(l["kind"], l["text"]) for l in lines if l["op"] == "log"]
    assert ("out", "plain 7") in logs
    assert ("wrn", "careful") in logs
    assert ("out", "exec id e1") in logs
    assert ("out", "step fetch") in logs
    # cwd was the workspace: the relative write landed there
    assert (tmp_path / "ws" / "made.txt").read_text() == "x"


def test_main_disallowed_import_fails_closed(run_main):
    rc, lines = run_main("import definitely_not_a_real_pkg\n")
    assert rc == 4
    err = next(l for l in lines if l["op"] == "error")
    assert err["type"] == "DisallowedImport"
    assert "definitely_not_a_real_pkg" in err["message"]


def test_main_declared_package_extends_allowlist(run_main):
    # the module only has to pass the AST allowlist check — the script never
    # actually imports it at runtime
    rc, _ = run_main("if False:\n    import declared_pkg\nprint('ok')\n",
                     package_imports=["declared_pkg"])
    assert rc == 0


def test_main_missing_secret_exit_code(run_main):
    rc, lines = run_main(f'from autowright import secrets\nsecrets["{NOPE_ID}"]\n',
                         secrets={}, allowed_secrets=[])
    assert rc == 3
    err = next(l for l in lines if l["op"] == "error")
    assert err["type"] == "MissingSecret"
    assert NOPE_ID[:8] in err["message"]


def test_main_sys_exit_zero_is_ordinary_early_exit(run_main):
    rc, lines = run_main("import sys\nprint('before')\nsys.exit(0)\nprint('after')\n")
    assert rc == 0
    assert not any(l["op"] == "error" for l in lines)
    texts = [l["text"] for l in lines if l["op"] == "log"]
    assert "before" in texts and "after" not in texts


def test_main_sys_exit_nonzero_fails_with_code(run_main):
    rc, lines = run_main("import sys\nsys.exit(5)\n")
    assert rc == 5
    err = next(l for l in lines if l["op"] == "error")
    assert err["type"] == "SystemExit"
    assert err["message"] == "step exited with code 5"


def test_main_sys_exit_message_keeps_author_diagnostic(run_main):
    rc, lines = run_main("import sys\nsys.exit('config file gone')\n")
    assert rc == 1
    err = next(l for l in lines if l["op"] == "error")
    assert err["message"] == "SystemExit: config file gone"


def test_main_exception_reports_type_and_traceback(run_main):
    rc, lines = run_main("print('partial tail', end='')\nraise ValueError('bad input')\n")
    assert rc == 1
    err = next(l for l in lines if l["op"] == "error")
    assert err["type"] == "ValueError"
    assert err["message"] == "ValueError: bad input"
    logs = [l for l in lines if l["op"] == "log"]
    # the pending partial print() line was flushed before the error
    assert any(l["kind"] == "out" and l["text"] == "partial tail" for l in logs)
    assert any(l["kind"] == "err" and "ValueError: bad input" in l["text"] for l in logs)


def test_main_reply_outside_message_trigger_raises(run_main):
    rc, lines = run_main("from autowright import reply\nreply('hi')\n")
    assert rc == 1
    err = next(l for l in lines if l["op"] == "error")
    assert "message trigger" in err["message"]


def test_main_reply_scans_outbound_for_secret_values(run_main):
    rc, lines = run_main(
        f'from autowright import reply, secrets\nreply(\'key is \' + secrets["{TOKEN_ID}"])\n',
        can_reply=True, secrets={TOKEN_ID: "s3cr3t"}, allowed_secrets=[TOKEN_ID],
        secret_names={TOKEN_ID: "TOKEN"},
        scan_secrets={"TOKEN": "s3cr3t"})
    assert rc == 1
    err = next(l for l in lines if l["op"] == "error")
    assert "secret TOKEN" in err["message"]
    assert not any(l["op"] == "reply" for l in lines)


def test_main_reply_emits_control_line(run_main):
    rc, lines = run_main("from autowright import reply\nreply('all done')\n",
                         can_reply=True)
    assert rc == 0
    assert [l["text"] for l in lines if l["op"] == "reply"] == ["all done"]


def test_main_notify_caps_text(run_main):
    rc, lines = run_main("from autowright import notify\nnotify('x' * 20_000)\n")
    assert rc == 0
    n = next(l for l in lines if l["op"] == "notify")
    assert len(n["text"]) == 10_000


def test_main_site_packages_joins_sys_path(run_main, tmp_path):
    sp = tmp_path / "site"
    sp.mkdir()
    (sp / "declared_pkg.py").write_text("VALUE = 41\n", encoding="utf-8")
    rc, lines = run_main(
        "import declared_pkg\nprint('got', declared_pkg.VALUE + 1)\n",
        site_packages=str(sp), package_imports=["declared_pkg"])
    assert rc == 0
    assert any(l.get("text") == "got 42" for l in lines)


# ---------- §6.1 containment: outbound secrets, scan map, memory keys ----------


def test_reply_and_prompt_refuse_secret_values():
    """§6.1: text bound for a third party (agent prompt, message reply) is
    refused outright when it carries a secret value."""
    from autowright.executor import scan_outbound

    scan = {"API_TOKEN": "s3cret-value", "PEM": "line-one\nline-two"}
    for what in ("prompt", "reply"):
        with pytest.raises(RuntimeError, match="API_TOKEN"):
            scan_outbound("here you go: s3cret-value", what, scan)
        # a partial paste of a multi-line value is caught line by line
        with pytest.raises(RuntimeError, match="PEM"):
            scan_outbound("line-two", what, scan)
    scan_outbound("nothing sensitive here", "reply", scan)  # clean text passes


def test_scan_map_is_not_reachable_from_the_step_sdk():
    """§6: a step that declared no secrets must not read another step's value
    off the agent object."""
    from autowright.executor import Agent

    ctx = {"secrets": {}, "scan_secrets": {"OTHER": "v"}, "is_agent_step": True}
    scan = ctx.pop("scan_secrets")
    agent = Agent(ctx, scan, None)
    assert "scan_secrets" not in agent._ctx
    assert "OTHER" not in (agent._ctx.get("secrets") or {})


def test_memory_names_cannot_escape_the_memory_dir(tmp_path):
    """§6.1: snapshots and Clear memory operate on the memory dir — a key must
    never address a file outside it."""
    from autowright.executor import Memory

    m = Memory(str(tmp_path / "mem"))
    for bad in ("../escape", "sub/dir", "/abs", ".."):
        with pytest.raises(ValueError):
            m.save(bad, {"a": 1})
        with pytest.raises(ValueError):
            m.load(bad)
    m.save("fine", {"a": 1})
    assert m.load("fine") == {"a": 1}
    assert not (tmp_path / "escape.yaml").exists()
