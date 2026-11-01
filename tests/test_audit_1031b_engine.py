"""Engine, executor and process audit regressions (2026-10-31, second sweep).

§6.3 memory copies keep symlinks as links; §3 interpreter isolation in every
spawn and service definition; §6.2 pip runs with PIP_USER=0 /
PIP_REQUIRE_VIRTUALENV=0 and the PyPI lookup is https-only; §6 trigger edits
finish waiting entries directly (never a live cancel); §7 a manual retry is
disk-first; §6 a firing against a replaced automation dict is refused; §5 a
late reply delivery never re-creates a deleted record; §6 monotonic clocks for
in-memory durations; §19 opencode.json sync under a cross-process lock and
through symlinks; §7 executor policy verdicts never retry; §6.1 scan and
redaction cover every allowed secret; §6 fetch_page cap, charset, deadline and
cross-step site spacing.
"""
import http.server
import json
import os
import plistlib
import threading
import time
import uuid

import pytest
from conftest import make_version, read_all_logs, wait_done
from test_service import launchd_only, svc  # noqa: F401 - fixture


def _payload(sender="Dave"):
    return {"kind": "discord", "channel": "42", "secret": "TOKEN",
            "sender": sender, "text": "hi"}


def _one_step(code: str, **step):
    ver = make_version()
    ver["steps"] = [{"file": "01-step.py", "name": "Step", "description": "",
                     "code": code, **step}]
    return ver


def _add_secret(store, name, value):
    from autowright import keychain

    secret_id = str(uuid.uuid4())
    store.secrets.append({"id": secret_id, "name": name, "description": "", "set": True})
    keychain.set_secret(secret_id, value)
    return secret_id


# ---------- D: §6.3 memory copies keep a symlink as a link ----------

def test_draft_memory_seed_copies_a_dangling_symlink_as_a_link(store):
    from autowright.engine import Engine

    a = store.create_automation(make_version(), "Links", None)
    live = store.auto_dir(a) / "memory"
    live.mkdir(parents=True, exist_ok=True)
    (live / "notes.md").write_text("kept", encoding="utf-8")
    os.symlink(str(live / "gone" / "python3"), live / "python3")  # dangling
    engine = Engine(store)
    assert engine._seed_draft_memory(a) is True
    seeded = engine._memory_dir(a, "draft")
    assert (seeded / "notes.md").read_text(encoding="utf-8") == "kept"
    assert (seeded / "python3").is_symlink()
    assert os.readlink(seeded / "python3") == str(live / "gone" / "python3")


def test_a_test_runs_memory_copy_keeps_a_directory_link_as_a_link(client, monkeypatch, tmp_path):
    from autowright import api, engine as engine_module, paths, testexec
    from autowright.storage import store
    from test_audit_1030b_api import _draft, _wait_settled

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"x" * 10)
    memory = paths.pending_draft_dir() / "memory"
    memory.mkdir(parents=True)
    (memory / "notes.md").write_text("top", encoding="utf-8")
    os.symlink(str(outside), memory / "home")
    os.symlink(str(tmp_path / "nowhere"), memory / "dangling")
    seen = {}

    def fake_run(script, ctx, *args, **kwargs):
        mem = ctx["memory_dir"]
        seen["home_is_link"] = os.path.islink(os.path.join(mem, "home"))
        seen["dangling_is_link"] = os.path.islink(os.path.join(mem, "dangling"))
        return 0

    monkeypatch.setattr(engine_module, "run_step_process", fake_run)
    with store.lock:
        execution_id = testexec.start(api.engine, _draft(), None, [], [], {})
    h = _wait_settled(api.engine, store, execution_id)
    assert h["status"] == "succeeded"
    assert seen == {"home_is_link": True, "dangling_is_link": True}


# ---------- R / S: §3 interpreter isolation, §6.2 pip environment ----------

def test_the_executor_spawn_env_carries_no_isolation_variables(monkeypatch):
    """§3 (third sweep): the isolation is the spawn's `-s -P` flags; the env
    pair would reach every subprocess a step spawns."""
    from autowright import harness

    monkeypatch.setenv("PYTHONNOUSERSITE", "1")
    monkeypatch.setenv("PYTHONSAFEPATH", "1")
    env = harness.step_env()
    assert "PYTHONNOUSERSITE" not in env
    assert "PYTHONSAFEPATH" not in env


def test_a_step_never_imports_a_module_from_its_cwd(store):
    """§3: with `-P` the workspace (the spawn cwd) is off sys.path, so a
    stray email.py there can't shadow the stdlib."""
    from autowright.engine import Engine

    engine = Engine(store)
    a = store.create_automation(_one_step(
        "import sys\nfrom autowright import log\n"
        "log('user-site ' + str(sys.flags.no_user_site))\n"
        "log('safe-path ' + str(sys.flags.safe_path))\n"), "Isolated", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "succeeded"
    texts = [line["text"] for line in read_all_logs(store, h["id"])]
    assert "user-site 1" in texts and "safe-path True" in texts


def test_pip_runs_with_the_isolation_and_pip_config_overrides(home, monkeypatch):
    from types import SimpleNamespace

    from autowright import packages

    seen = {}

    class FakeProc:
        def __init__(self, cmd, **kw):
            seen["cmd"] = cmd
            seen["env"] = kw.get("env")
            self.returncode = 0
            self.stdout = SimpleNamespace(read=lambda *a: "", close=lambda: None)
            self.stderr = SimpleNamespace(read=lambda *a: "", close=lambda: None)

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def communicate(self, timeout=None):
            return "", ""

    monkeypatch.setenv("PIP_USER", "1")
    monkeypatch.setenv("PIP_REQUIRE_VIRTUALENV", "1")
    monkeypatch.setenv("PIP_NO_INDEX", "1")
    monkeypatch.setattr(packages.subprocess, "Popen", FakeProc)
    packages._pip_install("pkg")
    env = seen["env"]
    assert env["PIP_USER"] == "0" and env["PIP_REQUIRE_VIRTUALENV"] == "0"
    # §3 (third sweep): flags on the pip command line, never the variables.
    assert "PYTHONNOUSERSITE" not in env and "PYTHONSAFEPATH" not in env
    assert seen["cmd"][1:5] == ["-s", "-P", "-m", "pip"]
    assert env["PIP_NO_INDEX"] == "1"  # §15: the suites' pip variables survive
    assert "--isolated" not in seen["cmd"]


@launchd_only
def test_the_launchd_plist_carries_the_isolation_flags(svc):  # noqa: F811
    # §3 (third sweep): flags, never an EnvironmentVariables pair.
    svc.mod.install()
    with open(svc.plist, "rb") as f:
        plist = plistlib.load(f)
    assert plist["ProgramArguments"][1:] == ["-s", "-P", "-m", "autowright.main"]
    assert "EnvironmentVariables" not in plist


def test_the_systemd_unit_carries_the_isolation_flags():
    from autowright.platform import linux

    text = linux.unit_text()
    assert " -s -P -m autowright.main\n" in text
    assert "PYTHONNOUSERSITE" not in text and "PYTHONSAFEPATH" not in text


# ---------- AF: §5.2 the PyPI lookup refuses a hop off https ----------

def test_the_pypi_lookup_goes_through_the_https_only_opener(monkeypatch):
    import io

    from autowright import packages, transfer

    seen = {}

    def opener(req, *, timeout, refuse):
        seen["timeout"] = timeout
        seen["refused"] = refuse()
        return io.BytesIO(json.dumps({"releases": {}}).encode("utf-8"))

    monkeypatch.setattr(transfer, "open_https_only", opener)
    assert packages._latest_compatible("pkg") is None
    assert seen["timeout"] == packages.PYPI_TIMEOUT
    assert isinstance(seen["refused"], ConnectionError)


# ---------- K: §6 trigger edits finish waiting entries, never a live cancel ----------

def test_a_trigger_edit_never_cancels_an_entry_promoted_after_the_snapshot(store, monkeypatch):
    from autowright import listeners
    from autowright.engine import Engine
    from autowright.firing import cancel_unmatched_queue

    monkeypatch.setattr(listeners, "notify_busy", lambda payload: None)
    engine = Engine(store)
    a = store.create_automation(make_version(), "Promoted", None)
    promoted = store.create_execution(a, "version", a["current_version"], "discord",
                                      steps=[], status="queued", trigger_payload=_payload("P"))
    doomed = store.create_execution(a, "version", a["current_version"], "discord",
                                    steps=[], status="queued", trigger_payload=_payload("D"))
    cancels = []
    monkeypatch.setattr(engine, "cancel", lambda eid: cancels.append(eid) or True)
    real = store.queued_execs

    def snapshot_then_promote(automation_id):
        heads = list(real(automation_id))
        promoted["status"] = "executing"  # a finish's drain promoted it in the gap
        return heads

    monkeypatch.setattr(store, "queued_execs", snapshot_then_promote)
    cancel_unmatched_queue(store, engine, a["id"])
    assert cancels == []  # never engine.cancel
    assert promoted["status"] == "executing"  # left running
    assert doomed["status"] == "skipped"
    assert doomed["note"] == "cancelled before it ran"


# ---------- L: §7 a manual retry is disk-first ----------

def test_a_retry_whose_write_fails_leaves_the_record_failed(store, monkeypatch):
    from autowright.engine import Engine

    engine = Engine(store)
    a = store.create_automation(_one_step("raise SystemExit(1)\n"), "Retry", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "failed"

    real = store.update_execution

    def full_disk(record):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store, "update_execution", full_disk)
    with pytest.raises(OSError, match="No space left"):
        engine.retry(a, store.execs[h["id"]])
    assert store.execs[h["id"]]["status"] == "failed"
    assert not engine.is_live(h["id"])
    assert h["id"] not in a["_live"]

    monkeypatch.setattr(store, "update_execution", real)
    again = engine.retry(a, store.execs[h["id"]])
    assert again["status"] == "executing"
    wait_done(engine, h["id"])
    assert store.execs[h["id"]]["status"] == "failed"  # the script still exits 1


# ---------- K5: §6 a firing against a replaced automation dict ----------

def test_a_firing_against_a_stale_automation_dict_is_refused_with_no_record(store):
    from autowright.engine import Engine
    from autowright.firing import fire_trigger

    engine = Engine(store)
    a = store.create_automation(make_version(), "Reloaded", None)
    trigger = {"id": "t1", "kind": "cron", "enabled": True}
    a["triggers"] = [trigger]
    stale = dict(a)  # what a data-path reload left behind in a caller's hand
    before = set(store.execs)
    assert fire_trigger(store, engine, stale, trigger) is False
    assert set(store.execs) == before
    assert not a["_live"]



def _collected_hit(store):
    """An automation with an enabled message trigger, as a listener's earlier
    hold collected it (a, t) — the PATCH then lands before the firing."""
    from autowright.engine import Engine

    engine = Engine(store)
    a = store.create_automation(make_version(), "Edited mid-dispatch", None)
    a["max_queued"] = 3
    trigger = {"id": "t1", "kind": "discord", "enabled": True, "secret": "TOKEN",
               "channel": "42"}
    a["triggers"] = [trigger]
    return engine, a, dict(trigger)


def test_a_trigger_disabled_between_hit_and_fire_is_refused_with_no_record(store):
    from autowright.firing import fire_trigger

    engine, a, hit = _collected_hit(store)
    a["triggers"][0]["enabled"] = False
    before = set(store.execs)
    assert fire_trigger(store, engine, a, hit, payload=_payload()) is False
    a["_live"] = {"blocking"}  # the queue path refuses the same way
    assert fire_trigger(store, engine, a, hit, payload=_payload()) is False
    assert set(store.execs) == before
    assert store.queued_execs(a["id"]) == []


def test_a_trigger_removed_between_hit_and_fire_is_refused_with_no_record(store):
    from autowright.firing import fire_trigger

    engine, a, hit = _collected_hit(store)
    a["triggers"] = []
    before = set(store.execs)
    assert fire_trigger(store, engine, a, hit, payload=_payload()) is False
    assert set(store.execs) == before
    assert not a["_live"]

# ---------- K6: §5 a late reply never re-creates a deleted record ----------

def test_a_reply_delivered_after_the_record_was_deleted_logs_nothing(store, monkeypatch):
    from autowright import listeners
    from autowright.engine import Engine

    held = []
    monkeypatch.setattr(listeners, "send_reply", lambda payload, text: None)
    monkeypatch.setattr(listeners, "submit_send", held.append)
    engine = Engine(store)
    a = store.create_automation(_one_step("from autowright import reply\nreply('hello')\n"),
                                "Late reply", None)
    h = engine.start(a, "discord", payload={"kind": "discord", "channel": "c1",
                                            "secret": "BOT", "sender": "u1"})
    wait_done(engine, h["id"])
    assert len(held) == 1
    store.delete_execution(h["id"])
    logs = store.exec_dir(h["id"]) / "logs"
    assert not logs.exists()
    held[0]()  # the queued delivery lands now
    assert not logs.exists()
    assert not store.exec_dir(h["id"]).exists()


# ---------- AC: §6 monotonic clocks for in-memory durations ----------

def test_the_ollama_self_heal_cooldown_runs_on_the_monotonic_clock(monkeypatch):
    from autowright import harness

    monkeypatch.setattr(harness, "_serve_last_spawn", float("-inf"))
    assert harness._claim_serve_spawn() is True
    # A wall clock stepped an hour back changes nothing: still cooling down.
    real = time.time
    monkeypatch.setattr(harness.time, "time", lambda: real() - 3600)
    assert harness._claim_serve_spawn() is False
    monkeypatch.setattr(harness, "_serve_last_spawn",
                        time.monotonic() - harness._SERVE_COOLDOWN_S - 1)
    assert harness._claim_serve_spawn() is True


def test_the_queue_ttl_runs_on_the_monotonic_clock(store, monkeypatch):
    from autowright import firing
    from autowright.engine import Engine

    monkeypatch.setattr(firing, "admission_blocked", lambda *args: True)
    engine = Engine(store)
    a = store.create_automation(make_version(), "Ttl", None)
    a["max_queued"] = 3
    trigger = {"id": "t1", "kind": "discord", "enabled": True, "secret": "TOKEN",
               "channel": "42"}
    a["triggers"] = [trigger]
    firing.fire_trigger(store, engine, a, trigger, payload=_payload())
    (h,) = store.queued_execs(a["id"])
    assert not firing._is_stale(h)
    # The wall-clock queued_at still reads fresh; the monotonic stamp is old.
    h["_queued_mono"] = time.monotonic() - firing.QUEUE_TTL_S - 1
    assert firing._is_stale(h)
    # A hand-edited (or reloaded) queued_at falls back to the wall clock.
    h["queued_at"] = "2020-01-01T00:00:00+00:00"
    h["_queued_mono"] = time.monotonic()
    assert firing._is_stale(h)


# ---------- X3: §19 opencode.json merge under a cross-process lock ----------

def test_parallel_opencode_syncs_for_different_models_both_land(tmp_path, monkeypatch):
    from autowright import harness

    config = tmp_path / "opencode" / "opencode.json"
    monkeypatch.setattr(harness, "_OPENCODE_CONFIG", str(config))
    models = [f"model-{i}" for i in range(8)]
    threads = [threading.Thread(target=harness.sync_opencode_ollama, args=(m,))
               for m in models]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    cfg = json.loads(config.read_text(encoding="utf-8"))
    assert set(cfg["provider"]["ollama"]["models"]) == set(models)
    assert (config.parent / ".opencode.json.lock").exists()


def test_a_symlinked_opencode_config_stays_a_symlink(tmp_path, monkeypatch):
    from autowright import harness

    target = tmp_path / "dotfiles" / "opencode.json"
    target.parent.mkdir()
    target.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")
    link = tmp_path / "config" / "opencode.json"
    link.parent.mkdir()
    os.symlink(str(target), link)
    monkeypatch.setattr(harness, "_OPENCODE_CONFIG", str(link))
    harness.sync_opencode_ollama("llama3")
    assert link.is_symlink()
    cfg = json.loads(target.read_text(encoding="utf-8"))
    assert cfg["theme"] == "dark"
    assert "llama3" in cfg["provider"]["ollama"]["models"]


# ---------- X4: §7 executor policy verdicts never retry ----------

@pytest.mark.parametrize("code", [
    "import numpy\n",                                       # DisallowedImport, exit 4
    "from autowright import secrets\nkey = 'X' * 3\nsecrets[key]\n",  # MissingSecret, exit 3
])
def test_a_policy_verdict_fails_an_infinite_retries_step_once(store, code):
    from autowright.engine import Engine

    engine = Engine(store)
    a = store.create_automation(_one_step(code, infinite_retries=True), "Verdict", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"], timeout=30)
    full = store.exec_full(h["id"])
    assert full["status"] == "failed"
    assert len(full["steps"][0]["attempts"]) == 1


# ---------- X5: §6.1 every allowed secret is redacted ----------

def test_an_allowed_secret_no_step_references_is_redacted_in_step_output(store):
    from autowright.engine import Engine

    secret_id = _add_secret(store, "OLD_TOKEN", "hunter2-old-token")
    engine = Engine(store)
    a = store.create_automation(_one_step(
        "import os\n"
        "path = os.path.join(os.environ['AUTOWRIGHT_MEMORY_DIR'], 'saved.txt')\n"
        "print(open(path).read())\n"), "Leaky memory", None,
        allowed_secrets=[secret_id])
    memory = store.auto_dir(a) / "memory"
    memory.mkdir(parents=True, exist_ok=True)
    (memory / "saved.txt").write_text("token=hunter2-old-token", encoding="utf-8")
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "succeeded"
    texts = [line["text"] for line in read_all_logs(store, h["id"])]
    assert not any("hunter2-old-token" in t for t in texts)
    assert any(t.startswith("token=") for t in texts)


def test_an_allowed_secret_that_was_deleted_fails_nothing(store):
    from autowright.engine import Engine

    engine = Engine(store)
    a = store.create_automation(_one_step("print('fine')\n"), "Gone secret", None,
                                allowed_secrets=[str(uuid.uuid4())])
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "succeeded"


# ---------- X6: §6 fetch_page cap, charset, deadline, shared spacing ----------

class _Pages(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):  # noqa: N802 - http.server API
        if self.path == "/robots.txt":
            self.send_response(404)
            self.end_headers()
            return
        if self.path == "/latin":
            body = "café".encode("windows-1252")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=windows-1252")
        elif self.path == "/plain":
            body = "naïve".encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
        else:  # /huge
            body = b"x" * (8 * 1024 * 1024 + 1)
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass


@pytest.fixture()
def pages(monkeypatch):
    from autowright import executor

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
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


def test_fetch_page_decodes_with_the_declared_charset(pages):
    from autowright.executor import fetch_page

    base, _ = pages
    assert fetch_page(f"{base}/latin") == "café"
    assert fetch_page(f"{base}/plain") == "naïve"  # no charset declared → UTF-8


def test_fetch_page_refuses_a_page_over_8_mb_without_retrying(pages):
    from autowright.executor import fetch_page

    base, sleeps = pages
    with pytest.raises(RuntimeError, match="page larger than 8 MB"):
        fetch_page(f"{base}/huge")
    assert 2 not in sleeps  # never retried


def test_fetch_page_spacing_is_shared_through_the_sites_file(pages, tmp_path, monkeypatch):
    from autowright import executor

    base, sleeps = pages
    sites = tmp_path / ".sites.json"
    monkeypatch.setattr(executor, "_sites_file", str(sites))
    executor.fetch_page(f"{base}/plain")
    assert len(sleeps) == 1  # §6 third sweep: the page is spaced after the robots hit
    recorded = json.loads(sites.read_text(encoding="utf-8"))
    host = f"127.0.0.1:{base.rsplit(':', 1)[1]}"
    assert set(recorded) == {host}
    # The next step is a fresh executor process: empty in-process caches.
    monkeypatch.setattr(executor, "_site_last", {})
    monkeypatch.setattr(executor, "_robots", {})
    executor.fetch_page(f"{base}/plain")
    # The next step's robots read waits on the shared record, then its page.
    assert len(sleeps) == 3 and all(1.5 < s <= 2.0 for s in sleeps[1:])


def test_fetch_page_tolerates_a_corrupt_sites_file(pages, tmp_path, monkeypatch):
    from autowright import executor

    base, sleeps = pages
    sites = tmp_path / ".sites.json"
    sites.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(executor, "_sites_file", str(sites))
    assert executor.fetch_page(f"{base}/plain") == "naïve"
    assert len(sleeps) == 1  # only the page's spacing after the robots hit
    assert json.loads(sites.read_text(encoding="utf-8"))


def test_the_engine_names_the_sites_file_in_the_step_context(store, monkeypatch):
    from autowright import engine as engine_module
    from autowright.engine import Engine

    captured = []
    monkeypatch.setattr(engine_module, "run_step_process",
                        lambda script, ctx, *a, **k: captured.append(ctx) or 0)
    engine = Engine(store)
    a = store.create_automation(make_version(), "Sites", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert captured
    assert {c["sites_file"] for c in captured} == {
        str(store.exec_dir(h["id"]) / ".sites.json")}

