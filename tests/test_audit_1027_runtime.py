"""Runtime audit regressions (2026-10-27).

§4.8 redaction of re-serialized multi-line / JSON secrets; §6 Discord backoff
reset after READY; §7 the line writer's thread safety; §7 a blank step `file`
is the non-retryable missing-script path; §6 a backward clock step never
re-fires an occurrence; §4.3 a coalesced one-shot survives a stale-snapshot
firing; §6 the monotonic retry pause; §4.1/§7 the execution-start seal check
over on-disk bytes; §7 an unreadable Keychain is the secret failure category;
§7 retry keeps the chip; §6 the iMessage cursor survives a transient read
error; §6.1/§19 iMessage NUL replies and the probe timeout.
"""
import hashlib
import json
import threading
import time
import uuid
from datetime import datetime

import pytest
from conftest import make_version, read_all_logs, use_fake_osascript, wait_done

from autowright import keychain

PEM_BODY = ["MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7",
            "q1w2e3r4t5y6u7i8o9p0AaSsDdFfGgHhJjKkLlZzXxCcVvBbNnMm",
            "ZyXwVuTsRqPoNmLkJiHgFeDcBa9876543210zzYYxxWWvvUUttSS"]
PRIVATE_KEY = ("-----BEGIN PRIVATE KEY-----\n" + "\n".join(PEM_BODY)
               + "\n-----END PRIVATE KEY-----\n")
SERVICE_ACCOUNT = json.dumps({
    "type": "service_account",
    "project_id": "my-project-123456",
    "private_key_id": "0123456789abcdef0123456789abcdef01234567",
    "private_key": PRIVATE_KEY,
    "client_email": "robot@my-project-123456.iam.gserviceaccount.com",
}, indent=2)


def add_secret(store, name) -> str:
    secret_id = str(uuid.uuid4())
    store.secrets.append({"id": secret_id, "name": name, "description": "", "set": True})
    return secret_id


# ---------- §4.8: re-serialized multi-line / JSON secrets ----------

def test_json_secret_reserialized_never_reaches_the_log(store):
    from autowright.engine import Engine

    sid = add_secret(store, "SERVICE_ACCOUNT")
    keychain.set_secret(sid, SERVICE_ACCOUNT)
    ver = make_version()
    ver["steps"] = [{"file": "01-leak.py", "name": "Leak", "description": "",
                     "code": ("import json\nfrom autowright import secrets\n"
                              f'v = secrets["{sid}"]\n'
                              "creds = json.loads(v)\n"
                              "print(creds)\n"
                              "print(json.dumps(creds))\n"
                              'print(creds["private_key"])\n')}]
    a = store.create_automation(ver, "Leaky JSON", None)
    store.patch_automation(a, {"allowedSecrets": [sid]})
    engine = Engine(store)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "succeeded"
    text = "\n".join(line["text"] for line in read_all_logs(store, h["id"]))
    for fragment in PEM_BODY + ["BEGIN PRIVATE KEY", "0123456789abcdef0123456789abcdef01234567",
                                "robot@my-project-123456"]:
        assert fragment not in text
    assert "•••" in text
    assert "SERVICE_ACCOUNT" in h["redacted_secrets"]


def test_prompt_scan_refuses_reserialized_json_secret():
    from autowright.executor import scan_outbound

    creds = json.loads(SERVICE_ACCOUNT)
    for text in (json.dumps(creds), str(creds), "key: " + PEM_BODY[1]):
        with pytest.raises(RuntimeError, match="SERVICE_ACCOUNT"):
            scan_outbound(text, "agent prompt", {"SERVICE_ACCOUNT": SERVICE_ACCOUNT})


def test_short_json_leaves_and_literals_never_redact():
    from autowright.engine import build_redactions, redact_text
    from autowright.executor import secret_probes

    redactions = build_redactions({"s1": '{"a": true, "b": "short"}'}, {"s1": "FLAGS"})
    assert "true" not in redactions and "short" not in redactions
    assert redact_text("it is true and short", redactions) == "it is true and short"
    assert "{" not in secret_probes('{\n  "k": "a-long-leaf-value"\n}')
    assert "a-long-leaf-value" in secret_probes('{\n  "k": "a-long-leaf-value"\n}')


def test_multiline_line_stripped_and_escaped_forms_are_probes():
    from autowright.engine import build_redactions

    value = "first-line-secret\n    indented-secret-line   \nshort"
    redactions = build_redactions({"s1": value}, {"s1": "MULTI"})
    assert "indented-secret-line" in redactions  # stripped
    assert "first-line-secret" in redactions
    assert "short" not in redactions  # under 8 characters


# ---------- §6: Discord backoff resets after any READY session ----------

def test_session_that_reached_ready_then_raised_resets_backoff(monkeypatch):
    from autowright import listeners as li_mod

    keychain.set_secret("BOT", "tok")

    class _Mgr:
        def set_status(self, *a, **kw):
            pass

    conn = li_mod._Conn("BOT", _Mgr())
    calls = []

    def fake_session(token):
        calls.append(token)
        if len(calls) == 1:
            raise OSError("first drop")  # never READY → backoff doubles
        conn._reached_ready = True  # READY handled, then the socket died
        raise OSError("dropped after READY")

    monkeypatch.setattr(conn, "_session", fake_session)
    waits = []

    def fake_wait(timeout=None):
        waits.append(timeout)
        if len(waits) >= 3:
            conn._stop.set()
        return conn._stop.is_set()

    monkeypatch.setattr(conn._stop, "wait", fake_wait)
    conn.run()
    # 1 s, then 2 s would follow a non-READY failure; the READY session resets it.
    assert waits[:2] == [1.0, 1.0]


# ---------- §7: _LineWriter is thread-safe ----------

def test_line_writer_threads_never_duplicate_or_merge(monkeypatch):
    from autowright import executor

    emitted = []
    lock = threading.Lock()

    def fake_emit(op, **kw):
        with lock:
            emitted.append(kw["text"])

    monkeypatch.setattr(executor, "emit", fake_emit)
    writer = executor._LineWriter("out")

    def worker(n):
        for i in range(200):
            writer.write(f"thread-{n}-line-{i}\n")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    writer.flush()
    expected = {f"thread-{n}-line-{i}" for n in range(8) for i in range(200)}
    assert len(emitted) == 1600
    assert set(emitted) == expected


# ---------- §7: a blank step `file` is the missing-script path ----------

def test_blank_step_file_fails_once_without_retry(store):
    from autowright.engine import Engine

    ver = make_version()
    a = store.create_automation(ver, "Blank file", None)
    step = a["versions"][1]["steps"][0]
    step["file"] = ""
    step["infinite_retries"] = True
    engine = Engine(store)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "failed"
    assert len(h["steps"][0]["attempts"]) == 1  # never retried, even forever


# ---------- §6: a backward clock step never re-fires a fired occurrence ----------

class _Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def _clocked_scheduler(store, clock, monkeypatch):
    from datetime import timezone

    from autowright import scheduler as sched_mod
    from autowright.engine import Engine
    from autowright.scheduler import Scheduler

    fires = []
    monkeypatch.setattr(sched_mod, "fire_trigger",
                        lambda store, engine, a, t, **_: fires.append(t["id"]) or True)
    sched = Scheduler(store, Engine(store), clock=clock,
                      utc_clock=lambda: clock().astimezone(timezone.utc))
    return sched, fires


def test_backward_step_after_a_firing_never_refires(store, monkeypatch):
    clock = _Clock(datetime(2026, 7, 10, 8, 59, 30))
    sched, fires = _clocked_scheduler(store, clock, monkeypatch)
    store.create_automation(make_version(), "Minutely", None, triggers=[
        {"id": "t1", "kind": "cron", "enabled": True, "expression": "* * * * *"}])
    for t in store.autos[next(iter(store.autos))]["triggers"]:
        t.pop("enabledAt", None)  # the real-time enable stamp would postdate the fake clock
    sched._tick()  # baseline 08:59:30
    clock.now = datetime(2026, 7, 10, 9, 0, 10)
    sched._tick()
    assert fires == ["t1"]  # 09:00 fired
    clock.now = datetime(2026, 7, 10, 8, 59, 50)  # NTP steps back
    sched._tick()
    clock.now = datetime(2026, 7, 10, 9, 0, 5)
    sched._tick()
    assert fires == ["t1"]  # 09:00 stays fired
    clock.now = datetime(2026, 7, 10, 9, 1, 5)
    sched._tick()
    assert fires == ["t1", "t1"]  # 09:01 fires normally


# ---------- §4.3: a stale-snapshot firing consumes no coalesced one-shot ----------

def test_stale_snapshot_firing_keeps_the_coalesced_one_shot(store, monkeypatch):
    clock = _Clock(datetime(2026, 7, 10, 7, 59))
    sched, fires = _clocked_scheduler(store, clock, monkeypatch)
    a = store.create_automation(make_version(), "Race", None, triggers=[
        {"id": "cron", "kind": "cron", "enabled": True, "expression": "0 8 * * *"},
        {"id": "once", "kind": "time", "enabled": True, "at": "2026-07-10T08:00"}])
    for t in a["triggers"]:
        t.pop("enabledAt", None)  # the real-time enable stamp would postdate the fake clock
    sched._tick()
    real_fire = sched._fire
    raced = []

    def racing_fire(auto, t):
        if not raced:
            raced.append(True)
            # the API disables the cron between the tick's snapshot and the fire
            next(x for x in auto["triggers"] if x["id"] == "cron")["enabled"] = False
        return real_fire(auto, t)

    monkeypatch.setattr(sched, "_fire", racing_fire)
    clock.now = datetime(2026, 7, 10, 8, 1)
    sched._tick()
    assert fires == []
    assert [t["id"] for t in a["triggers"]] == ["cron", "once"]  # one-shot survived
    clock.now = datetime(2026, 7, 10, 8, 2)
    sched._tick()
    assert fires == ["once"]
    assert [t["id"] for t in a["triggers"]] == ["cron"]  # consumed once it fired


# ---------- §6: the retry pause runs on the monotonic clock ----------

def test_retry_pause_ignores_a_frozen_wall_clock(monkeypatch):
    from autowright import engine as engine_mod

    monkeypatch.setenv("AUTOWRIGHT_STEP_RETRY_PAUSE_S", "0.1")
    monkeypatch.setattr(engine_mod.time, "time", lambda: 1_000_000.0)
    started = time.monotonic()
    engine_mod.Engine._await_retry({"cancel": False}, 0, True)
    assert time.monotonic() - started < 2


# ---------- §4.1/§7: the execution-start seal check over on-disk bytes ----------

def test_hand_edited_step_is_hashed_flagged_and_scoped_at_start(store, monkeypatch):
    from autowright import engine as engine_mod
    from autowright.engine import Engine

    sid = add_secret(store, "TOKEN")
    keychain.set_secret(sid, "token-value-abcdef")
    a = store.create_automation(make_version(), "Sealed", None)
    store.patch_automation(a, {"allowedSecrets": [sid]})
    assert not any(p["kind"] == "step-modified" for p in store.auto_json(a)["problems"])
    script = store.auto_dir(a) / "versions" / "v1" / "01-say.py"
    edited = ("from autowright import log, secrets\n"
              f'value = secrets["{sid}"]\n'
              'log(f"edited by hand, token length {len(value)}")\n')
    script.write_text(edited, encoding="utf-8")
    published = []
    monkeypatch.setattr(engine_mod.hub, "publish",
                        lambda event, **kw: published.append((event, kw)))
    engine = Engine(store)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "succeeded"  # the edit's secret was scoped in
    assert h["steps"][0]["sha"] == hashlib.sha256(script.read_bytes()).hexdigest()[:16]
    assert any("token length 18" in line["text"] for line in read_all_logs(store, h["id"]))
    problems = store.auto_json(a)["problems"]
    assert [p["kind"] for p in problems if p["kind"] == "step-modified"] == ["step-modified"]
    assert any(event == "automation.changed" and kw.get("automationId") == a["id"]
               and any(p["kind"] == "step-modified"
                       for p in (kw.get("automation") or {}).get("problems") or [])
               for event, kw in published)
    # the seal never reaches the API or the step dicts
    assert "sealedSha" not in json.dumps(store.auto_json(a))
    assert all("sha" not in s for s in a["versions"][1]["steps"])
    # §4.1: the loaded step now holds the bytes that ran — what a restart's
    # load would hold — so the editor / pull / the next save see the hand
    # edit instead of silently sealing the boot-time script over it
    assert a["versions"][1]["steps"][0]["code"] == edited
    # the API's current-version steps (top-level `steps`) carry the edit too
    assert store.auto_json(a, full=True)["steps"][0]["code"] == edited


def test_unedited_version_is_not_flagged(store):
    from autowright.engine import Engine

    a = store.create_automation(make_version(), "Clean", None)
    engine = Engine(store)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "succeeded"
    assert not any(p["kind"] == "step-modified" for p in store.auto_json(a)["problems"])


# ---------- §7: an unreadable Keychain is the secret failure category ----------

def test_keychain_read_error_fails_pre_step_with_the_secret_reason(store, monkeypatch):
    from autowright.engine import Engine

    sid = add_secret(store, "API_KEY")
    ver = make_version()
    ver["steps"][0]["code"] = f'from autowright import secrets\nprint(secrets["{sid}"])\n'
    a = store.create_automation(ver, "Locked", None)
    store.patch_automation(a, {"allowedSecrets": [sid]})

    def locked(secret_id):
        raise RuntimeError("User interaction is not allowed.")

    monkeypatch.setattr(keychain, "get_secret", locked)
    engine = Engine(store)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "failed"
    assert h["error"]["step"] is None
    assert h["error"]["reason"] == ("The Keychain couldn't be read — unlock it or allow "
                                    "access, then execute again.")
    assert "API_KEY" in h["error"]["message"]
    assert "engine error" not in h["error"]["message"]
    assert all(s["attempts"] == [] for s in h["steps"])  # no step ran


# ---------- §7: retry keeps the chip ----------

def test_retry_keeps_the_chip_an_earlier_step_set(store, monkeypatch):
    from autowright.engine import Engine

    store.settings["notifications"] = "attention"
    engine = Engine(store)
    notified = []
    monkeypatch.setattr(engine, "_notify_end",
                        lambda auto, ver, h, result, text: notified.append(dict(h)))
    ver = make_version()
    ver["steps"] = [
        {"file": "01-chip.py", "name": "Chip", "description": "",
         "code": 'from autowright import result\nresult.status("attention")\n'
                 'result.chip("Look at this")\n'},
        {"file": "02-flaky.py", "name": "Flaky", "description": "",
         "code": 'import os\nassert os.path.exists("flag"), "flaky"\n'},
    ]
    a = store.create_automation(ver, "Chip kept", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "failed"
    (store.exec_dir(h["id"]) / "workspace" / "flag").write_text("ok")
    h2 = engine.retry(a, h)
    assert h2["chip"] == "Look at this"  # not cleared by the retry
    wait_done(engine, h2["id"])
    assert h2["status"] == "succeeded"
    assert h2["chip"] == "Look at this" and h2["chip_status"] == "attention"
    deadline = time.time() + 5
    while len(notified) < 2 and time.time() < deadline:
        time.sleep(0.02)
    assert notified[-1]["chip_status"] == "attention"


def test_retry_notification_body_falls_back_to_the_kept_chip(store, monkeypatch):
    from autowright import notify
    from autowright.engine import Engine

    posted = []
    monkeypatch.setattr(notify, "post_targeted",
                        lambda title, body, **kw: posted.append(body))
    engine = Engine(store)
    a = store.create_automation(make_version(), "Body", None)
    h = {"id": "x", "status": "succeeded", "chip": "Look at this", "chip_status": "attention"}
    engine._notify_end(a, a["versions"][1], h, None, None)
    assert posted == ["Look at this"]


# ---------- §6: the iMessage cursor survives a transient read error ----------

def test_imessage_reopen_keeps_the_cursor(store, monkeypatch):
    from autowright import imessage
    from autowright import listeners as li_mod

    class _Db:
        def close(self):
            pass

    top = {"value": 10}
    fail = {"next": False}
    reads = []

    def messages_after(db, cursor, top_rowid, senders):
        reads.append((cursor, top_rowid))
        if fail["next"]:
            fail["next"] = False
            raise OSError("disk I/O error")
        if cursor < 11 <= top_rowid:
            return [{"from_me": False, "group": False, "tapback": False, "text": "hi",
                     "sender": "dave@example.com", "chat": "c", "guid": "g11",
                     "ts": time.time(), "rowid": 11}]
        return []

    monkeypatch.setattr(imessage, "open_db", lambda: _Db())
    monkeypatch.setattr(imessage, "max_rowid", lambda db: top["value"])
    monkeypatch.setattr(imessage, "messages_after", messages_after)
    li = li_mod.Listeners(store, None)
    seen = []
    li.dispatch_imessage = lambda m: seen.append(m["guid"])
    watcher = li_mod._ImsgWatcher(li)
    watcher.tick({"dave@example.com"})  # cursor 10
    fail["next"] = True
    watcher.tick({"dave@example.com"})  # read error → closed
    assert watcher._db is None
    top["value"] = 11  # a message arrives during the outage
    watcher.tick({"dave@example.com"})
    assert seen == ["g11"]
    assert reads[-1] == (10, 11)


def test_imessage_reopen_on_a_shrunk_db_restarts_at_its_max(store, monkeypatch):
    from autowright import imessage
    from autowright import listeners as li_mod

    class _Db:
        def close(self):
            pass

    top = {"value": 50}
    fail = {"next": False}
    reads = []

    def messages_after(db, cursor, top_rowid, senders):
        reads.append(cursor)
        if fail["next"]:
            fail["next"] = False
            raise OSError("gone")
        return []

    monkeypatch.setattr(imessage, "open_db", lambda: _Db())
    monkeypatch.setattr(imessage, "max_rowid", lambda db: top["value"])
    monkeypatch.setattr(imessage, "messages_after", messages_after)
    watcher = li_mod._ImsgWatcher(li_mod.Listeners(store, None))
    watcher.tick(set())
    fail["next"] = True
    watcher.tick(set())
    top["value"] = 5  # replaced database
    watcher.tick(set())
    assert reads[-1] == 5


# ---------- §6.1/§19: iMessage NUL reply + probe timeout ----------

def test_reply_with_nul_byte_is_a_failed_reply(tmp_path, monkeypatch, home):
    from autowright import imessage

    monkeypatch.setenv("AUTOWRIGHT_TEST_OSASCRIPT_LOG", str(tmp_path / "osa.log"))
    use_fake_osascript(monkeypatch, imessage)
    err = imessage.send_message("iMessage;-;+15551234567", "bad\x00reply")
    assert isinstance(err, str) and "NUL" in err


def test_probe_timeout_answers_unknown_and_keeps_the_remembered_state(monkeypatch, client):
    from autowright import imessage
    from autowright.storage import store

    store.settings["imessageAutomation"] = "granted"
    monkeypatch.setattr(imessage, "run_bounded", lambda argv, timeout, **kw: None)
    assert imessage.automation_probe() == "unknown"
    assert store.settings["imessageAutomation"] == "granted"
    r = client.post("/imessage/permissions/automation-probe")
    assert r.status_code == 200 and r.json() == {"automation": "unknown"}
    assert imessage.automation_status() == "granted"
