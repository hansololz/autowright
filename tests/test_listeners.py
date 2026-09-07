"""§6 listener manager: Discord firing rules, payload shape, §6.1 reply
sending, dispatch, and the engine/executor plumbing around triggerPayload."""
import json
import logging
import threading
import time

import pytest
from conftest import make_version, read_all_logs

from autowright import keychain
from autowright.listeners import (
    Listeners, message_matches, notify_busy, send_reply, trigger_payload,
)


def _msg(**over):
    d = {"channel_id": "42", "content": "please Deploy now",
         "author": {"id": "7", "username": "dave", "global_name": "Dave"},
         "mentions": [], "id": "m1", "guild_id": "g1",
         "timestamp": "2026-07-27T10:00:00+00:00"}
    d.update(over)
    return d


def _trig(**over):
    t = {"id": "t1", "kind": "discord", "enabled": True,
         "channel": "42", "secret": "TOKEN"}
    t.update(over)
    return t


def test_message_matches_rules():
    assert message_matches(_trig(), _msg(), bot_id="bot9")
    # wrong channel
    assert not message_matches(_trig(channel="99"), _msg(), "bot9")
    # bot-authored messages never fire — including the listening bot itself
    assert not message_matches(_trig(), _msg(author={"id": "8", "bot": True}), "bot9")
    assert not message_matches(_trig(), _msg(author={"id": "bot9"}), "bot9")
    # mention: only messages that @-mention the bot
    assert not message_matches(_trig(mention=True), _msg(), "bot9")
    assert message_matches(_trig(mention=True),
                           _msg(mentions=[{"id": "bot9"}]), "bot9")
    # mention: the bot's managed role counts too (typing @BotName often
    # inserts the role mention, not the user mention)
    assert message_matches(_trig(mention=True),
                           _msg(mention_roles=["r5"]), "bot9",
                           bot_roles={"r5"})
    assert not message_matches(_trig(mention=True),
                               _msg(mention_roles=["r6"]), "bot9",
                               bot_roles={"r5"})
    # pattern: case-insensitive substring
    assert message_matches(_trig(pattern="deploy"), _msg(), "bot9")
    assert not message_matches(_trig(pattern="rollback"), _msg(), "bot9")
    # author: only the configured senders' messages fire
    assert message_matches(_trig(author=["7"]), _msg(), "bot9")
    assert message_matches(_trig(author=["6", "7"]), _msg(), "bot9")
    assert not message_matches(_trig(author=["8"]), _msg(), "bot9")
    # filters AND together
    assert message_matches(_trig(author=["7"], pattern="deploy"), _msg(), "bot9")
    assert not message_matches(_trig(author=["8"], pattern="deploy"), _msg(), "bot9")


def test_trigger_payload_shape():
    p = trigger_payload(_trig(), _msg())
    assert p == {"kind": "discord", "text": "please Deploy now", "sender": "Dave",
                 "channel": "42", "channelName": None, "guildName": None,
                 "messageId": "m1", "guildId": "g1",
                 "secret": "TOKEN", "at": "2026-07-27T10:00:00+00:00"}
    # §6 name cache resolved names ride on the payload
    p = trigger_payload(_trig(), _msg(), channel_name="deploys", guild_name="Ops")
    assert p["channelName"] == "deploys" and p["guildName"] == "Ops"


def test_send_reply_needs_discord_payload_and_token():
    assert "message trigger" in send_reply({}, "hi")
    # fake keychain (conftest) holds no TOKEN value
    assert "has no value" in send_reply({"kind": "discord", "channel": "42",
                                         "secret": "TOKEN"}, "hi")


def _busy_recorder(monkeypatch):
    """Capture what the §6 busy notices send. notify_busy hands the payload to
    the shared worker thread, so a test enqueues and then waits on the queue
    (`_drain_busy` below) rather than joining a thread it doesn't own."""
    from autowright import listeners as li_mod

    sent = []
    monkeypatch.setattr(li_mod, "send_reply",
                        lambda payload, text, reply_to=None:
                            sent.append((payload, text, reply_to)))
    return sent


def _drain_busy():
    """Block until the worker has delivered every queued notice."""
    from autowright import listeners as li_mod

    li_mod._busy_q.join()


def test_notify_busy_replies_to_every_dropped_message(monkeypatch):
    from autowright import listeners as li_mod

    sent = _busy_recorder(monkeypatch)
    payload = {"kind": "discord", "channel": "42", "secret": "TOKEN",
               "sender": "Dave", "messageId": "m1"}

    # §6: one notice per dropped message — a burst from one sender is answered
    # message by message, each threaded to the message it answers.
    notify_busy(payload)
    notify_busy({**payload, "messageId": "m2"})
    _drain_busy()
    assert [(t, r) for _, t, r in sent] == [(li_mod.BUSY_TEXT, "m1"),
                                           (li_mod.BUSY_TEXT, "m2")]


def test_notify_busy_burst_uses_one_worker_thread(monkeypatch):
    """§6: N dropped messages produce N notices — delivered by ONE worker
    draining the queue, never one HTTP thread per message (each send is a round
    trip of up to 10 s, so thread-per-notice would keep hundreds alive)."""
    sent = _busy_recorder(monkeypatch)
    payload = {"kind": "discord", "channel": "42", "secret": "TOKEN",
               "sender": "Dave"}

    n = 40
    for i in range(n):
        notify_busy({**payload, "messageId": f"m{i}"})
    _drain_busy()

    # every message answered, in arrival order — queueing is not coalescing
    assert [r for _, _, r in sent] == [f"m{i}" for i in range(n)]
    workers = [t for t in threading.enumerate() if t.name == "ad-busy-reply"]
    assert len(workers) == 1


def test_notify_busy_skips_non_message_firings(monkeypatch):
    sent = _busy_recorder(monkeypatch)
    notify_busy({})  # a skipped cron firing carries no payload
    _drain_busy()
    assert sent == []


def test_notify_busy_reply_failure_never_raises(monkeypatch, caplog):
    from autowright import listeners as li_mod

    _busy_recorder(monkeypatch)
    monkeypatch.setattr(li_mod, "send_reply",
                        lambda payload, text, reply_to=None:
                            "couldn't reach Discord: boom")
    with caplog.at_level(logging.WARNING, logger="autowright.listeners"):
        notify_busy({"kind": "discord", "channel": "42", "secret": "TOKEN",
                     "sender": "Dave"})  # logged, not raised
        _drain_busy()
    assert any("busy notice failed — couldn't reach Discord: boom" in r.getMessage()
               for r in caplog.records)


def test_busy_worker_survives_a_raising_job(caplog):
    """§6: the delivery worker is the only one there is - an unexpected error
    in one job must be logged and stepped over, never end the thread and
    silence every send queued behind it."""
    from autowright import listeners as li_mod

    ran = []

    def raiser():
        raise RuntimeError("send blew up")

    with caplog.at_level(logging.ERROR, logger="autowright.listeners"):
        li_mod._busy_q.put(raiser)
        li_mod._busy_q.put(lambda: ran.append(True))
        li_mod._start_busy_worker()
        _drain_busy()
    assert ran == [True]
    assert sum("outbound send failed" in r.getMessage() for r in caplog.records) == 1


def test_send_reply_posts_with_bot_token(monkeypatch):
    import requests

    keychain.set_secret("TOKEN", "abc123")
    calls = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.update(url=url, headers=headers, body=json)

        class R:
            status_code = 200
        return R()

    monkeypatch.setattr(requests, "post", fake_post)
    payload = {"kind": "discord", "channel": "42", "secret": "TOKEN"}
    assert send_reply(payload, "x" * 3000) is None
    assert calls["url"].endswith("/channels/42/messages")
    assert calls["headers"]["Authorization"] == "Bot abc123"
    assert len(calls["body"]["content"]) == 2000  # Discord cap — truncated
    assert "message_reference" not in calls["body"]  # §6.1 reply(): no threading

    # §6 busy notice threads to the dropped message; fail_if_not_exists False
    # so a since-deleted message degrades to a plain post, not a 400
    assert send_reply(payload, "hi", reply_to="m1") is None
    assert calls["body"]["message_reference"] == {"message_id": "m1",
                                                  "fail_if_not_exists": False}


def test_send_reply_reports_http_failures(monkeypatch):
    # §6.1: a failed send returns an error string, never raises — the engine
    # logs it and the step goes on.
    import requests

    keychain.set_secret("TOKEN", "abc123")
    payload = {"kind": "discord", "channel": "42", "secret": "TOKEN"}

    def boom(*a, **kw):
        raise requests.exceptions.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "post", boom)
    err = send_reply(payload, "hi")
    assert err.startswith("couldn't reach Discord: ")
    assert "connection refused" in err

    class R:
        status_code = 500

    monkeypatch.setattr(requests, "post", lambda *a, **kw: R())
    assert send_reply(payload, "hi") == "Discord answered HTTP 500"


def test_dispatch_fires_matching_triggers_with_payload(store):
    fired = []

    class FakeEngine:
        @staticmethod
        def at_capacity(a):
            return False

        def start(self, a, label, version_label=None, payload=None, adopt=None):
            fired.append((a["id"], label, payload))
            return {"id": "x"}

    a = store.create_automation(make_version(), "Chat", None)
    a["triggers"] = [_trig()]
    b = store.create_automation(make_version(), "Other", None)
    b["triggers"] = [_trig(channel="99"), _trig(id="t2", enabled=False)]
    li = Listeners(store, FakeEngine())
    li.dispatch("TOKEN", _msg(), bot_id="bot9",
                chan_names={"42": "deploys"}, guild_names={"g1": "Ops"})
    assert [f[0] for f in fired] == [a["id"]]  # b: wrong channel / off
    assert fired[0][1] == "discord"
    assert fired[0][2]["text"] == "please Deploy now"
    # §4.5: the §6 name cache stamps channelName/guildName at firing time
    assert fired[0][2]["channelName"] == "deploys"
    assert fired[0][2]["guildName"] == "Ops"
    # a different token secret routes nothing
    fired.clear()
    li.dispatch("OTHER", _msg(), bot_id="bot9")
    assert fired == []


def test_desired_secrets_and_status(store):
    a = store.create_automation(make_version(), "Chat", None)
    a["triggers"] = [_trig(), _trig(id="t2", secret="TOKEN_B", enabled=False)]
    li = Listeners(store, None)
    assert li._desired_secrets() == ({"TOKEN"}, set())  # off triggers don't listen
    li.set_status("TOKEN", "error", "bad token")
    assert store.listener_status["TOKEN"] == {"state": "error", "error": "bad token"}
    # the §4.3 `connection` rides trigger serialization; unknown secrets read connecting
    t_json = store.trigger_json(a["triggers"][0])
    assert t_json["connection"] == {"state": "error", "error": "bad token"}
    assert store.trigger_json(a["triggers"][1])["connection"] == {"state": "connecting"}


def test_set_status_publishes_row(store, monkeypatch):
    from autowright import listeners as li_mod

    events = []
    monkeypatch.setattr(li_mod.hub, "publish",
                        lambda ev, **kw: events.append({"event": ev, **kw}))
    a = store.create_automation(make_version(), "Chat", None)
    a["triggers"] = [_trig()]
    store.create_automation(make_version(), "No triggers", None)
    li = Listeners(store, None)

    li.set_status("TOKEN", "connected")
    # §19: only the automation holding a TOKEN trigger fires, and the event
    # carries its list-shape row with the new connection state riding it
    assert len(events) == 1
    e = events[0]
    assert e["event"] == "automation.changed"
    assert e["automationId"] == a["id"]
    row = e["automation"]
    assert row["id"] == a["id"]
    assert row["triggers"][0]["connection"] == {"state": "connected"}
    assert "steps" not in row

    # unchanged status publishes nothing
    events.clear()
    li.set_status("TOKEN", "connected")
    assert events == []


def _wait_done(engine, execution_id, timeout=30):
    t0 = time.time()
    while engine.is_live(execution_id):
        assert time.time() - t0 < timeout, "execution never finished"
        time.sleep(0.05)


def test_engine_reply_and_payload_end_to_end(store, monkeypatch):
    from autowright import listeners as li_mod
    from autowright.engine import Engine

    sent = []
    monkeypatch.setattr(li_mod, "send_reply",
                        lambda payload, text: sent.append((payload, text)) or None)
    ver = make_version()
    ver["steps"] = [{"file": "01-echo.py", "name": "Echo", "description": "",
                     "code": 'from autowright import execution, reply\nreply(f"got: {execution.trigger_payload[\'text\']}")\n'}]
    a = store.create_automation(ver, "Replier", None)
    engine = Engine(store)
    payload = trigger_payload(_trig(), _msg())
    h = engine.start(a, "discord", payload=payload)
    _wait_done(engine, h["id"])
    assert store.execs[h["id"]]["status"] == "succeeded"
    assert sent == [(payload, "got: please Deploy now")]
    logs = [ln["text"] for ln in read_all_logs(store, h["id"])]
    assert any("reply sent to Discord (42)" in t for t in logs)
    # §4.5: the payload persists on the record
    assert store.read_exec_yaml(h["id"])["trigger_payload"] == payload
    # §4.5 triggerSender rides on the list row; the payload is full-record-only
    row = store.exec_json(store.execs[h["id"]])
    assert row["triggerSender"] == payload["sender"]
    assert "triggerPayload" not in row
    assert store.exec_json(store.execs[h["id"]], full=True)["triggerPayload"] == payload


def test_reply_outside_message_trigger_fails_step(store):
    from autowright.engine import Engine

    ver = make_version()
    ver["steps"] = [{"file": "01-nope.py", "name": "Nope", "description": "",
                     "code": 'from autowright import reply\nreply("hi")\n'}]
    a = store.create_automation(ver, "NoOrigin", None)
    engine = Engine(store)
    h = engine.start(a, "manual")
    _wait_done(engine, h["id"])
    rec = store.execs[h["id"]]
    assert rec["status"] == "failed"
    assert "reply() is only available" in rec["error"]["message"]


# ---------- §6 reconcile lifecycle (fake conn/watcher, real store) ----------

def _pin_imessage_capability(monkeypatch, enabled: bool):
    """§2: pin `capabilities.imessage` so the reconcile tests read the same on
    every host (macOS composes it true, Windows/Linux false)."""
    import dataclasses

    from autowright import platform as platmod

    plat = platmod.current()
    fake = dataclasses.replace(
        plat, capabilities=dataclasses.replace(plat.capabilities, imessage=enabled))
    monkeypatch.setattr(platmod, "current", lambda: fake)


def test_imessage_watcher_is_capability_gated(store, monkeypatch):
    """§2/§6: where the OS has no iMessage the watcher is never constructed —
    nothing touches chat.db or osascript, however many triggers ask for it."""
    from autowright import listeners as li_mod

    built = []
    monkeypatch.setattr(li_mod, "_ImsgWatcher", lambda mgr: built.append(mgr))
    _pin_imessage_capability(monkeypatch, False)
    a = store.create_automation(make_version(), "Chat", None)
    a["triggers"] = [{"id": "t9", "kind": "imessage", "enabled": True,
                      "from": "dave@example.com"}]
    Listeners(store, None)._reconcile()
    assert built == []


def test_reconcile_starts_and_stops_listeners(store, monkeypatch):
    from autowright import listeners as li_mod

    events = []

    class FakeConn:
        def __init__(self, secret, mgr):
            self.secret = secret
            events.append(("conn-new", secret))

        def start(self):
            events.append(("conn-start", self.secret))

        def is_alive(self):
            return True

        def stop(self):
            events.append(("conn-stop", self.secret))

    class FakeWatcher:
        def __init__(self, mgr):
            events.append(("imsg-new",))

        def tick(self, senders):
            events.append(("imsg-tick", tuple(sorted(senders))))

        def close(self):
            events.append(("imsg-close",))

    monkeypatch.setattr(li_mod, "_Conn", FakeConn)
    monkeypatch.setattr(li_mod, "_ImsgWatcher", FakeWatcher)
    # §2: the watcher half is capability-gated. This test is about reconcile's
    # start/stop logic, not the gate (test_imessage_watcher_is_capability_gated
    # covers that), so pin the capability on wherever the suite runs.
    _pin_imessage_capability(monkeypatch, True)
    a = store.create_automation(make_version(), "Chat", None)
    li = Listeners(store, None)

    li._reconcile()  # no enabled triggers → nothing exists
    assert events == []

    # discord trigger + secret appears → one conn, started
    a["triggers"] = [_trig()]
    li._reconcile()
    assert events == [("conn-new", "TOKEN"), ("conn-start", "TOKEN")]

    # trigger toggled off → conn stopped AND its status entry removed
    li.set_status("TOKEN", "connected")
    a["triggers"][0]["enabled"] = False
    events.clear()
    li._reconcile()
    assert events == [("conn-stop", "TOKEN")]
    assert "TOKEN" not in store.listener_status

    # imessage trigger appears → the one watcher is created and ticked
    a["triggers"] = [{"id": "t9", "kind": "imessage", "enabled": True,
                      "from": "dave@example.com"}]
    events.clear()
    li._reconcile()
    assert events == [("imsg-new",), ("imsg-tick", ("dave@example.com",))]

    # last imessage trigger removed → watcher closed, status entry removed
    li.set_status(li_mod.IMSG_KEY, "connected")
    a["triggers"] = []
    events.clear()
    li._reconcile()
    assert events == [("imsg-close",)]
    assert li_mod.IMSG_KEY not in store.listener_status


# ---------- §6 iMessage dispatch ----------

def _imsg(**over):
    m = {"from_me": False, "group": False, "tapback": False,
         "text": "please Deploy now", "sender": "dave@example.com",
         "chat": "iMessage;-;dave@example.com", "guid": "g1", "ts": time.time()}
    m.update(over)
    return m


def _imsg_trig(**over):
    t = {"id": "t1", "kind": "imessage", "enabled": True, "from": "dave@example.com"}
    t.update(over)
    return t


def test_dispatch_imessage_fires_matching_triggers_with_payload(store):
    fired = []

    class FakeEngine:
        @staticmethod
        def at_capacity(a):
            return False

        def start(self, a, label, version_label=None, payload=None, adopt=None):
            fired.append((a["id"], label, payload))
            return {"id": "x"}

    a = store.create_automation(make_version(), "Chat", None)
    a["triggers"] = [_imsg_trig()]
    b = store.create_automation(make_version(), "Other", None)
    b["triggers"] = [_imsg_trig(**{"from": "other@example.com"}),
                     _imsg_trig(id="t2", enabled=False)]
    li = Listeners(store, FakeEngine())
    li.dispatch_imessage(_imsg())
    assert [f[0] for f in fired] == [a["id"]]  # b: wrong sender / off
    assert fired[0][1] == "imessage"
    payload = fired[0][2]
    assert payload["kind"] == "imessage"
    assert payload["text"] == "please Deploy now"
    assert payload["sender"] == "dave@example.com"
    assert payload["chat"] == "iMessage;-;dave@example.com"
    # a non-matching sender routes nothing
    fired.clear()
    li.dispatch_imessage(_imsg(sender="stranger@example.com"))
    assert fired == []


# ---------- §6 _Conn.run: auth failures park at the backoff cap ----------

class _FakeMgr:
    def __init__(self):
        self.statuses = []

    def set_status(self, key, state, error=None):
        self.statuses.append((key, state, error))


def _stub_stop_wait(monkeypatch, conn):
    """Record every _stop.wait duration and break the loop after one park —
    the run loop would otherwise spin forever inside the test."""
    waits = []

    def fake_wait(timeout=None):
        waits.append(timeout)
        conn._stop.set()
        return True

    monkeypatch.setattr(conn._stop, "wait", fake_wait)
    return waits


def test_conn_auth_close_parks_at_backoff_max(monkeypatch):
    from websockets.exceptions import ConnectionClosed
    from websockets.frames import Close

    from autowright import listeners as li_mod

    keychain.set_secret("BOT", "tok")
    mgr = _FakeMgr()
    conn = li_mod._Conn("BOT", mgr)

    def raise_auth_close(token):
        raise ConnectionClosed(Close(4004, "Authentication failed."), None, None)

    monkeypatch.setattr(conn, "_session", raise_auth_close)
    waits = _stub_stop_wait(monkeypatch, conn)
    conn.run()  # inline, no thread — the stubbed wait ends it after one park
    assert waits == [li_mod.BACKOFF_MAX]  # parked at the cap, no hot loop
    assert ("BOT", "error", li_mod._CLOSE_REASONS[4004]) in mgr.statuses
    # the plain-word reason, not a close-code dump
    assert "Discord rejected the bot token" in li_mod._CLOSE_REASONS[4004]


def test_conn_missing_token_parks_with_plain_status(monkeypatch):
    from autowright import listeners as li_mod

    mgr = _FakeMgr()
    conn = li_mod._Conn("NO_VALUE", mgr)  # fake keychain holds nothing for it
    waits = _stub_stop_wait(monkeypatch, conn)
    conn.run()
    assert waits == [li_mod.BACKOFF_MAX]
    key, state, error = mgr.statuses[0]
    assert (key, state) == ("NO_VALUE", "error")
    # §4.8 ids-bind-names-display: no stored record matches the id, so the
    # error copy falls back to the short id prefix, never the raw uuid.
    assert "secret NO_VALUE… has no value yet" in error


def test_conn_unreadable_secret_store_parks_and_retries(monkeypatch):
    """§6: a locked or unavailable OS secret store (routine on a launchd start
    before first unlock) parks at the backoff cap and comes back - it must
    never escape the reconnect loop and leave the listener dead until a
    restart."""
    from autowright import listeners as li_mod

    def unavailable(sid):
        raise RuntimeError("the keychain is locked")

    monkeypatch.setattr(li_mod.keychain, "get_secret", unavailable)
    mgr = _FakeMgr()
    conn = li_mod._Conn("LOCKED", mgr)
    waits = _stub_stop_wait(monkeypatch, conn)
    conn.run()
    # parked at the cap and continued: never a hot loop, never a dead thread
    assert waits == [li_mod.BACKOFF_MAX]
    assert mgr.statuses == [("LOCKED", "error",
                             "couldn't read secret LOCKED… from the OS secret store "
                             "yet — retrying")]


# ---------- §6 _Conn._session: one scripted gateway session, no network ----------

class _GwMgr(_FakeMgr):
    def __init__(self):
        super().__init__()
        self.dispatched = []

    def dispatch(self, secret, d, bot_id, role_ids, chan_names, guild_names):
        self.dispatched.append((secret, d, bot_id, set(role_ids),
                                dict(chan_names), dict(guild_names)))


class _FakeWs:
    """Context-manager websocket replaying scripted frames. A frame is a dict
    (sent as JSON), an Exception (raised from recv), or ("tick", secs) — advance
    the fake clock and raise TimeoutError, like a quiet gateway."""

    def __init__(self, frames, clock=None):
        self.frames = list(frames)
        self.sent = []
        self.clock = clock

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def recv(self, timeout=None):
        assert self.frames, "test script exhausted — _session kept reading"
        f = self.frames.pop(0)
        if isinstance(f, tuple) and f[0] == "tick":
            self.clock["t"] += f[1]
            raise TimeoutError
        if isinstance(f, Exception):
            raise f
        return json.dumps(f)

    def send(self, s):
        self.sent.append(json.loads(s))

    def close(self):
        pass


def _session_over(monkeypatch, frames, clock=None):
    import websockets.sync.client as ws_client

    from autowright import listeners as li_mod

    fake = _FakeWs(frames, clock)
    monkeypatch.setattr(ws_client, "connect",
                        lambda url, max_size=None: fake)
    if clock is not None:
        class _Clock:
            @staticmethod
            def monotonic():
                return clock["t"]
        monkeypatch.setattr(li_mod, "time", _Clock)
    mgr = _GwMgr()
    conn = li_mod._Conn("BOT", mgr)
    return conn, mgr, fake


_HELLO = {"op": 10, "d": {"heartbeat_interval": 45_000}}


def test_session_identify_ready_and_dispatch(monkeypatch):
    from autowright import listeners as li_mod

    conn, mgr, fake = _session_over(monkeypatch, [
        _HELLO,
        {"op": 0, "t": "READY", "s": 1, "d": {"user": {"id": "B1"}}},
        {"op": 0, "t": "GUILD_CREATE", "s": 2, "d": {
            "id": "G1", "name": "My Server",
            "roles": [{"id": "R1", "tags": {"bot_id": "B1"}},
                      {"id": "R2", "tags": {}}],
            "channels": [{"id": "C1", "name": "general"}],
            "threads": [{"id": "T1", "name": "a-thread"}],
        }},
        {"op": 0, "t": "MESSAGE_CREATE", "s": 3,
         "d": {"channel_id": "C1", "content": "hi",
               "author": {"id": "U1", "username": "sam"}}},
        {"op": 7},  # gateway asks to reconnect → session ends
    ])
    assert conn._session("tok-123") is True
    ident = fake.sent[0]
    assert ident["op"] == 2
    assert ident["d"]["token"] == "tok-123"
    assert ident["d"]["intents"] == li_mod.INTENTS
    assert ("BOT", "connected", None) in mgr.statuses
    assert conn.bot_id == "B1"
    assert conn.role_ids == {"R1"}  # only the bot's managed role
    (secret, d, bot_id, roles, chans, guilds) = mgr.dispatched[0]
    assert (secret, bot_id) == ("BOT", "B1")
    assert d["content"] == "hi"
    assert chans == {"C1": "general", "T1": "a-thread"}
    assert guilds == {"G1": "My Server"}
    assert conn._ws is None  # cleared on the way out


def test_session_bad_hello_raises(monkeypatch):
    conn, _, _ = _session_over(monkeypatch, [{"op": 0}])
    with pytest.raises(RuntimeError, match="gateway didn't say hello"):
        conn._session("tok")


def test_session_invalid_session_before_ready_keeps_backoff(monkeypatch):
    conn, mgr, _ = _session_over(monkeypatch, [_HELLO, {"op": 9}])
    assert conn._session("tok") is False  # never READY → run() keeps backing off
    assert ("BOT", "connected", None) not in mgr.statuses


def test_session_heartbeats_on_interval_and_on_request(monkeypatch):
    clock = {"t": 100.0}
    conn, _, fake = _session_over(monkeypatch, [
        _HELLO,                     # interval 45s → next beat at t=145
        {"op": 0, "t": "READY", "s": 5, "d": {"user": {"id": "B1"}}},
        ("tick", 50),               # quiet past the deadline → timed heartbeat
        {"op": 1, "s": 6},          # gateway asks for an immediate one
        {"op": 7},
    ], clock=clock)
    assert conn._session("tok") is True
    beats = [f for f in fake.sent if f["op"] == 1]
    assert [b["d"] for b in beats] == [5, 6]  # latest seq echoed each time


def test_session_reconnects_when_a_heartbeat_is_never_acknowledged(monkeypatch):
    """§6: a half-dead TCP path (sleep/wake, a network switch) answers no op 11
    - the session must end at the next beat rather than sit "connected" while
    dropping every message."""
    clock = {"t": 100.0}
    conn, _, fake = _session_over(monkeypatch, [
        _HELLO,                     # interval 45s → next beat at t=145
        {"op": 0, "t": "READY", "s": 5, "d": {"user": {"id": "B1"}}},
        ("tick", 50),               # quiet past the deadline → first heartbeat
        ("tick", 50),               # still no ack by the next deadline
    ], clock=clock)
    with pytest.raises(RuntimeError, match="gateway heartbeat not acknowledged"):
        conn._session("tok")
    assert len([f for f in fake.sent if f["op"] == 1]) == 1  # never a second beat


def test_session_keeps_running_while_heartbeats_are_acknowledged(monkeypatch):
    """§6: the twin of the reject above - an op 11 clears the wait, so the next
    deadline beats again instead of tearing the session down."""
    clock = {"t": 100.0}
    conn, _, fake = _session_over(monkeypatch, [
        _HELLO,
        {"op": 0, "t": "READY", "s": 5, "d": {"user": {"id": "B1"}}},
        ("tick", 50),               # first heartbeat
        {"op": 11},                 # acknowledged
        ("tick", 50),               # second heartbeat, no reconnect
        {"op": 7},
    ], clock=clock)
    assert conn._session("tok") is True
    assert len([f for f in fake.sent if f["op"] == 1]) == 2


def test_run_generic_connect_failure_reports_connecting(monkeypatch):
    from autowright import listeners as li_mod

    keychain.set_secret("BOT", "tok")
    mgr = _FakeMgr()
    conn = li_mod._Conn("BOT", mgr)
    monkeypatch.setattr(conn, "_session",
                        lambda tok: (_ for _ in ()).throw(OSError("dns down")))
    waits = _stub_stop_wait(monkeypatch, conn)
    conn.run()
    assert waits == [1.0]  # first retry backs off gently, not at the cap
    assert ("BOT", "connecting", "connection failed — dns down") in mgr.statuses


def test_run_healthy_session_resets_backoff(monkeypatch):
    from autowright import listeners as li_mod

    keychain.set_secret("BOT", "tok")
    mgr = _FakeMgr()
    conn = li_mod._Conn("BOT", mgr)
    outcomes = [True]

    def fake_session(tok):
        if outcomes:
            return outcomes.pop()
        raise OSError("dropped")

    monkeypatch.setattr(conn, "_session", fake_session)
    waits = []

    def fake_wait(timeout=None):
        waits.append(timeout)
        if len(waits) >= 2:
            conn._stop.set()
        return conn._stop.is_set()

    monkeypatch.setattr(conn._stop, "wait", fake_wait)
    conn.run()
    # healthy session reset backoff to 1.0 before the failed one doubled it
    assert waits == [1.0, 2.0]


def test_stop_closes_live_socket(monkeypatch):
    from autowright import listeners as li_mod

    mgr = _FakeMgr()
    conn = li_mod._Conn("BOT", mgr)
    closed = []

    class _Ws:
        def close(self):
            closed.append(True)

    conn._ws = _Ws()
    conn.stop()
    assert closed == [True] and conn._stop.is_set()


# ---------- §6 per-item guards / listener liveness ----------

def test_imsg_batch_survives_one_bad_row(store, monkeypatch):
    """§6: the watcher's cursor advances past the whole batch BEFORE dispatch,
    so a row that raises must not take the rows behind it - they can never be
    re-read."""
    from autowright import imessage
    from autowright import listeners as li_mod

    rows = [_imsg(guid="g1", text="one"), _imsg(guid="g2", text="two"),
            _imsg(guid="g3", text="three")]
    monkeypatch.setattr(imessage, "open_db", lambda: object())
    monkeypatch.setattr(imessage, "max_rowid", lambda db: 99)
    monkeypatch.setattr(imessage, "messages_after", lambda db, cur, top, senders: rows)
    monkeypatch.setattr(imessage, "stale", lambda m: False)

    li = li_mod.Listeners(store, None)
    seen = []

    def dispatch(m):
        seen.append(m["guid"])
        if m["guid"] == "g2":
            raise RuntimeError("bad row")

    li.dispatch_imessage = dispatch
    li_mod._ImsgWatcher(li).tick({"dave@example.com"})
    assert seen == ["g1", "g2", "g3"]


def test_imsg_watcher_reports_a_chat_db_yanked_mid_tick(store, monkeypatch):
    """§6: a db rotated or revoked under the watcher closes the handle and
    parks in the `connection` error state, so the next tick re-probes and a
    restored database heals without a restart."""
    from autowright import imessage
    from autowright import listeners as li_mod

    closed = []

    class _Db:
        def close(self):
            closed.append(True)

    def gone(db, cursor, top, senders):
        raise OSError("database disk image is malformed")

    monkeypatch.setattr(imessage, "open_db", lambda: _Db())
    monkeypatch.setattr(imessage, "max_rowid", lambda db: 99)
    monkeypatch.setattr(imessage, "messages_after", gone)

    watcher = li_mod._ImsgWatcher(li_mod.Listeners(store, None))
    watcher.tick({"dave@example.com"})
    assert closed == [True] and watcher._db is None
    assert store.listener_status[li_mod.IMSG_KEY] == {
        "state": "error",
        "error": "couldn't read the Messages database — database disk image is malformed"}


def test_dispatch_survives_one_failing_firing(store, monkeypatch, caplog):
    """§6: one automation's firing must never drop the others' - the Discord
    twin of the iMessage per-hit guard."""
    from autowright import listeners as li_mod

    fired = []

    def boom(store, engine, a, t, payload=None):
        if a["name"] == "Bad":
            raise OSError("disk on fire")
        fired.append(a["name"])
        return True

    monkeypatch.setattr(li_mod, "fire_trigger", boom)
    for name in ("Bad", "Good"):
        a = store.create_automation(make_version(), name, None)
        a["triggers"] = [_trig()]
    with caplog.at_level(logging.ERROR, logger="autowright.listeners"):
        li_mod.Listeners(store, None).dispatch("TOKEN", _msg(), bot_id="bot9")
    assert fired == ["Good"]
    assert any("discord firing on 'Bad' failed" in r.getMessage()
               for r in caplog.records)


def test_dispatch_imessage_survives_one_failing_firing(store, monkeypatch):
    """§6: one automation's firing must never drop the others' (per hit)."""
    from autowright import listeners as li_mod

    fired = []

    def boom(store, engine, a, t, payload=None):
        if a["name"] == "Bad":
            raise OSError("disk on fire")
        fired.append(a["name"])
        return True

    monkeypatch.setattr(li_mod, "fire_trigger", boom)
    for name in ("Bad", "Good"):
        a = store.create_automation(make_version(), name, None)
        a["triggers"] = [_imsg_trig()]
    li_mod.Listeners(store, None).dispatch_imessage(_imsg())
    assert fired == ["Good"]


def test_reconcile_recreates_a_dead_connection(store, monkeypatch):
    """§6: a listener thread that died is no listener at all - the next
    reconcile replaces it instead of leaving the trigger unwatched."""
    from autowright import listeners as li_mod

    made = []

    class FakeConn:
        def __init__(self, secret, mgr):
            self.secret = secret
            self.alive = True
            made.append(self)

        def start(self):
            pass

        def is_alive(self):
            return self.alive

        def stop(self):
            self.alive = False

    monkeypatch.setattr(li_mod, "_Conn", FakeConn)
    a = store.create_automation(make_version(), "Chat", None)
    a["triggers"] = [_trig()]
    li = li_mod.Listeners(store, None)
    li._reconcile()
    assert len(made) == 1 and li._conns["TOKEN"] is made[0]

    li._reconcile()
    assert len(made) == 1  # a live connection is left alone

    made[0].alive = False  # the thread died
    li._reconcile()
    assert len(made) == 2 and li._conns["TOKEN"] is made[1]
