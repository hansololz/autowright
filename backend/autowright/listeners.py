"""Message-trigger listener manager (§6): one Discord gateway WebSocket per
distinct bot-token secret referenced by any enabled discord trigger, plus one
chat.db watcher total while any enabled imessage trigger exists. Fires
matching messages as executions (§4.3 rules, §4.5 triggerPayload) and sends
§6.1 reply() messages back — Discord REST API or Messages.app osascript.
Listener state is pushed into `store.listener_status` for the §4.3 `connection`
field, keyed by the token secret's §4.8 id for Discord and by IMSG_KEY for
iMessage. Ids are the binding; every user-facing error resolves the id to the
secret's live name (short id prefix when no record matches)."""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time

from . import imessage, keychain, platform
from .events import hub
from .firing import fire_trigger
from .storage import Store

log = logging.getLogger("autowright.listeners")

LISTEN_TICK_S = float(os.environ.get("AUTOWRIGHT_LISTEN_TICK_S", "3"))  # §15 knob
GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"
API_BASE = "https://discord.com/api/v10"
# GUILDS + GUILD_MESSAGES + DIRECT_MESSAGES + MESSAGE_CONTENT
INTENTS = (1 << 0) | (1 << 9) | (1 << 12) | (1 << 15)
BACKOFF_MAX = 60.0  # cap; also the retry period for parked auth errors (§6)
REPLY_LIMIT = 2000  # Discord message length cap — §6.1 reply() truncates
# §6 busy notice — one per dropped message firing, no rate limit.
BUSY_TEXT = ("I'm working on something else right now and couldn't take this "
             "message — please send it again in a moment.")
# `listener_status` key of the one iMessage watcher (§6) — all imessage
# triggers share it. Not a uuid, so it can never collide with a Discord
# entry's key (§4.8 secret ids are uuids).
IMSG_KEY = "imessage"

# Gateway close codes worth a plain-word `connection` error (§4.3).
_CLOSE_REASONS = {
    4004: "Discord rejected the bot token — check the secret's value",
    4014: "the bot's Message Content intent is off — enable it in the "
          "Discord developer portal",
}


def message_matches(t: dict, d: dict, bot_id: str | None,
                    bot_roles: set[str] | None = None) -> bool:
    """§4.3 firing rules for one enabled discord trigger against a gateway
    MESSAGE_CREATE payload `d`. Bot-authored messages never fire. A mention is
    the bot user in `mentions` or one of the bot's managed roles in
    `mention_roles` — typing @BotName in a server often inserts the role."""
    if d.get("channel_id") != t.get("channel"):
        return False
    author = d.get("author") or {}
    if author.get("bot") or (bot_id and author.get("id") == bot_id):
        return False
    if t.get("author") and author.get("id") not in t["author"]:
        return False
    if t.get("mention"):
        mentioned = any((u or {}).get("id") == bot_id
                        for u in d.get("mentions") or [])
        if not mentioned and bot_roles:
            mentioned = any(r in bot_roles for r in d.get("mention_roles") or [])
        if not mentioned:
            return False
    pat = t.get("pattern")
    if pat and pat.lower() not in (d.get("content") or "").lower():
        return False
    return True


def trigger_payload(t: dict, d: dict, channel_name: str | None = None,
                    guild_name: str | None = None) -> dict:
    """§4.5 triggerPayload for a Discord firing. Names come from the
    connection's §6 GUILD_CREATE name cache — best-effort, None on a miss
    (DMs, channels created after connect)."""
    author = d.get("author") or {}
    return {
        "kind": "discord",
        "text": d.get("content") or "",
        "sender": author.get("global_name") or author.get("username") or "",
        "channel": d.get("channel_id"),
        "channelName": channel_name,
        "guildName": guild_name,
        "messageId": d.get("id"),
        "guildId": d.get("guild_id"),
        "secret": t["secret"],
        "at": d.get("timestamp"),
    }


def _secret_label(sid: str) -> str:
    """§4.8 ids-bind-names-display: error copy names the secret when a stored
    record still matches the id, else a short id prefix — never the raw uuid."""
    from .storage import store
    with store.lock:
        s = next((s for s in store.secrets if s["id"] == sid), None)
    return s["name"] if s else f"{sid[:8]}…"


def send_reply(payload: dict, text: str,
               reply_to: str | None = None) -> str | None:
    """§6.1 reply(): send `text` back to the payload's origin — Discord REST
    with the bot token from the payload's `secret`, or Messages.app osascript
    to the payload's `chat`. Returns an error message, None on success —
    the engine logs either way; a failed send never fails the step.
    `reply_to` (Discord only — iMessage has no reply references) threads the
    send as a reply to that message id; the §6 busy notice uses it to pin
    itself to the dropped message."""
    import requests

    kind = (payload or {}).get("kind")
    if kind == "imessage":
        return imessage.send_message(payload.get("chat") or "", str(text))
    if kind != "discord":
        return "this execution wasn't started by a message trigger"
    token = keychain.get_secret(payload["secret"])  # §4.8: Keychain keyed by id
    if not token:
        return (f"secret {_secret_label(payload['secret'])} has no value — "
                "the bot token is gone")
    body: dict = {"content": str(text)[:REPLY_LIMIT]}
    if reply_to:
        # fail_if_not_exists false: a since-deleted message degrades to a
        # plain channel post instead of a 400.
        body["message_reference"] = {"message_id": reply_to,
                                     "fail_if_not_exists": False}
    try:
        r = requests.post(
            f"{API_BASE}/channels/{payload['channel']}/messages",
            headers={"Authorization": f"Bot {token}"},
            json=body, timeout=10)
    except Exception as e:  # noqa: BLE001
        return f"couldn't reach Discord: {e}"
    if r.status_code >= 400:
        return f"Discord answered HTTP {r.status_code}"
    return None


# §6 busy-notice delivery: ONE long-lived worker draining this queue, started
# on the first notice and never stopped. A dropped-message burst must not spawn
# an OS thread per message (each send is an HTTP round trip up to 10 s, so a
# thread-per-notice design keeps hundreds alive at once). Queueing is not
# coalescing — every entry is still sent, in arrival order.
_busy_q: "queue.Queue[dict]" = queue.Queue()
_busy_worker: threading.Thread | None = None
_busy_lock = threading.Lock()


def notify_busy(payload: dict) -> None:
    """§6: a dropped message firing answers its sender with a short busy
    notice — one per dropped message, never rate-limited or coalesced, so a
    retry that is itself dropped is answered too instead of leaving the sender
    waiting on a silent bot.
    The send is handed to the shared worker below — this is called from the
    gateway read loop, which must never block on an HTTP round trip (a stalled
    read misses heartbeats and drops the connection)."""
    if (payload or {}).get("kind") not in ("discord", "imessage"):
        return  # cron/app-start firings have nobody to answer
    # queued before the worker check, so none is stranded
    _busy_q.put(lambda: _send_busy(payload))
    _start_busy_worker()


def submit_send(job) -> None:
    """§6/§6.1: hand any outbound message send to the shared delivery worker —
    the engine's step-log read loop routes reply() sends here for the same
    reason the gateway routes busy notices: a synchronous send (up to 10 s
    Discord, 30 s osascript) must never stall the thread that called it.
    One FIFO keeps replies and busy notices in arrival order."""
    _busy_q.put(job)
    _start_busy_worker()


def _start_busy_worker() -> None:
    """Lazily start the single delivery thread (never on import — a backend that
    never drops a message never spawns it)."""
    global _busy_worker
    with _busy_lock:
        if _busy_worker is not None and _busy_worker.is_alive():
            return
        _busy_worker = threading.Thread(target=_busy_loop, daemon=True,
                                        name="ad-busy-reply")
        _busy_worker.start()


def _busy_loop() -> None:
    while True:
        job = _busy_q.get()
        try:
            job()
        except Exception:  # noqa: BLE001
            # The worker is the only one there is: an unexpected error in one
            # send must never end the thread and silence every later send.
            log.exception("outbound send failed")
        finally:
            _busy_q.task_done()


def _send_busy(payload: dict) -> None:
    err = send_reply(payload, BUSY_TEXT, reply_to=payload.get("messageId"))
    if err:
        # Same rule as §6.1 reply(): a failed send is logged, never raised — the
        # skipped record already stands and there is nothing to fail here.
        log.warning("busy notice failed — %s", err)


class _Conn(threading.Thread):
    """One gateway connection for one bot-token secret (addressed by its §4.8
    id). Resolves the token at
    every connect attempt (a fixed secret heals without a restart), reconnects
    with exponential backoff, and parks auth failures at the backoff cap."""

    def __init__(self, secret: str, mgr: "Listeners"):
        super().__init__(daemon=True, name=f"ad-discord-{secret[:8]}")
        self.secret = secret  # the token secret's §4.8 id
        self.mgr = mgr
        self.bot_id: str | None = None
        self.role_ids: set[str] = set()  # the bot's managed roles, per §4.3 mention rule
        # §6 name cache from GUILD_CREATE — stamps the payload's
        # channelName/guildName without ever doing REST from the read loop.
        self.chan_names: dict[str, str] = {}   # channel/thread id → name
        self.guild_names: dict[str, str] = {}  # guild id → name
        self._stop = threading.Event()
        self._ws = None

    def stop(self) -> None:
        # §3 shutdown: fire-and-forget — the gateway close can take the
        # library's full close timeout, and the backend's 5 s graceful box
        # must not spend it here. The close runs on a short-lived thread
        # nobody joins (the gateway session is disposable at shutdown); the
        # stop flag is already set, so the read loop exits either way.
        self._stop.set()
        ws = self._ws
        if ws is None:
            return

        def _close() -> None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

        threading.Thread(target=_close, daemon=True,
                         name=f"ad-discord-close-{self.secret[:8]}").start()

    def run(self) -> None:
        # The thread body is guarded whole: anything escaping the reconnect loop
        # (a set_status raise, a failed import) would leave a dead thread the
        # manager still counts as this secret's connection - §6 says the
        # listener reconnects, so nothing here may be fatal to the loop.
        try:
            self._run()
        except BaseException:  # noqa: BLE001
            log.exception("discord listener for %s died", self.secret[:8])

    def _run(self) -> None:
        # Imported at the top of the loop, never inside the except handler: an
        # ImportError there would replace the real failure with its own.
        from websockets.exceptions import ConnectionClosed

        backoff = 1.0
        while not self._stop.is_set():
            try:
                token = keychain.get_secret(self.secret)  # §4.8: Keychain keyed by id
            except Exception as e:  # noqa: BLE001
                # A locked/unavailable OS secret store (routine on a launchd
                # start before first unlock) must park-and-retry, not escape
                # the loop and kill this listener until a restart.
                log.warning("couldn't read secret %s from the OS secret store: %s",
                            self.secret[:8], e)
                self.mgr.set_status(self.secret, "error",
                                    f"couldn't read secret {_secret_label(self.secret)} "
                                    "from the OS secret store yet — retrying")
                self._stop.wait(BACKOFF_MAX)
                continue
            if not token:
                self.mgr.set_status(self.secret, "error",
                                    f"secret {_secret_label(self.secret)} has no value yet — "
                                    "add the bot token on the Secrets page")
                self._stop.wait(BACKOFF_MAX)
                continue
            self.mgr.set_status(self.secret, "connecting")
            try:
                if self._session(token):
                    backoff = 1.0  # a healthy session resets the backoff
            except Exception as e:  # noqa: BLE001 — closes, network errors, DNS, TLS, …
                code = e.rcvd.code if isinstance(e, ConnectionClosed) and e.rcvd else None
                reason = _CLOSE_REASONS.get(code)
                if reason:
                    # §6: an auth-class failure parks at the backoff cap — a
                    # fixed token/intent heals on the next attempt.
                    self.mgr.set_status(self.secret, "error", reason)
                    self._stop.wait(BACKOFF_MAX)
                    continue
                if not self._stop.is_set():
                    self.mgr.set_status(self.secret, "connecting",
                                        f"connection failed — {e}")
            self._stop.wait(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

    def _session(self, token: str) -> bool:
        """One gateway session: identify, heartbeat, dispatch. Returns True
        when the session got as far as READY (resets the backoff)."""
        from websockets.sync.client import connect

        ready = False
        with connect(GATEWAY_URL, max_size=2**23) as ws:
            self._ws = ws
            try:
                hello = json.loads(ws.recv(timeout=30))
                if hello.get("op") != 10:
                    raise RuntimeError("gateway didn't say hello")
                interval = hello["d"]["heartbeat_interval"] / 1000.0
                ws.send(json.dumps({"op": 2, "d": {
                    "token": token, "intents": INTENTS,
                    "properties": {"os": "macos", "browser": "autowright",
                                   "device": "autowright"},
                }}))
                seq = None
                awaiting_ack = False
                next_beat = time.monotonic() + interval
                while not self._stop.is_set():
                    # Deadline checked at the top of every iteration, not only
                    # on a read timeout: a continuously readable socket (a
                    # GUILD_CREATE burst, a busy guild) returns buffered frames
                    # even at timeout=0 and would otherwise starve the
                    # heartbeat until Discord closes the session.
                    if time.monotonic() >= next_beat:
                        if awaiting_ack:
                            # §6: no ack for the previous heartbeat - a
                            # half-dead TCP path (sleep/wake, network
                            # switch) would otherwise sit "connected" while
                            # dropping messages until the OS kills the
                            # socket. Reconnect now.
                            raise RuntimeError("gateway heartbeat not acknowledged")
                        ws.send(json.dumps({"op": 1, "d": seq}))
                        awaiting_ack = True
                        next_beat = time.monotonic() + interval
                    try:
                        raw = ws.recv(timeout=max(0.0, min(next_beat - time.monotonic(), 5.0)))
                    except TimeoutError:
                        continue
                    msg = json.loads(raw)
                    if msg.get("s") is not None:
                        seq = msg["s"]
                    op = msg.get("op")
                    if op == 11:  # heartbeat ack - the path is alive
                        awaiting_ack = False
                    elif op == 1:  # gateway asks for an immediate heartbeat
                        ws.send(json.dumps({"op": 1, "d": seq}))
                        awaiting_ack = True
                        next_beat = time.monotonic() + interval
                    elif op in (7, 9):  # reconnect / invalid session → new session
                        return ready
                    elif op == 0 and msg.get("t") == "READY":
                        self.bot_id = ((msg.get("d") or {}).get("user") or {}).get("id")
                        self.role_ids = set()
                        self.chan_names = {}
                        self.guild_names = {}
                        self.mgr.set_status(self.secret, "connected")
                        ready = True
                    elif op == 0 and msg.get("t") == "GUILD_CREATE":
                        g = msg.get("d") or {}
                        for role in g.get("roles") or []:
                            tags = (role or {}).get("tags") or {}
                            if tags.get("bot_id") == self.bot_id and role.get("id"):
                                self.role_ids.add(role["id"])
                        if g.get("id") and g.get("name"):
                            self.guild_names[g["id"]] = g["name"]
                        for ch in (g.get("channels") or []) + (g.get("threads") or []):
                            if (ch or {}).get("id") and ch.get("name"):
                                self.chan_names[ch["id"]] = ch["name"]
                    elif op == 0 and msg.get("t") == "MESSAGE_CREATE":
                        self.mgr.dispatch(self.secret, msg.get("d") or {},
                                          self.bot_id, self.role_ids,
                                          self.chan_names, self.guild_names)
            finally:
                self._ws = None
        return ready


class _ImsgWatcher:
    """The one §6 chat.db watcher: opened lazily on the first tick (so no
    permission is touched before an enabled imessage trigger exists), polled
    synchronously from the reconcile loop — a ROWID-cursor read is cheap. A
    db that can't be opened parks in the `connection` error state and re-probes
    every tick, so granting Full Disk Access heals it without a restart."""

    def __init__(self, mgr: "Listeners"):
        self.mgr = mgr
        self._db = None
        self._cursor = 0

    def close(self) -> None:
        if self._db is not None:
            try:
                self._db.close()
            except Exception:  # noqa: BLE001
                pass
            self._db = None

    def tick(self, senders: set[str]) -> None:
        if self._db is None:
            db = None
            try:
                db = imessage.open_db()
                # §6: the cursor starts at the db's current MAX(ROWID) — only
                # messages arriving while watching fire, never history. The
                # cursor is set BEFORE self._db, and any failure (not just
                # DbError — max_rowid can raise sqlite3 errors too) leaves
                # self._db None: a half-open watcher keeping its __init__
                # cursor of 0 would replay pre-watch history next tick.
                self._cursor = imessage.max_rowid(db)
                self._db = db
                self.mgr.set_status(IMSG_KEY, "connected")
            except Exception as e:  # noqa: BLE001
                if db is not None:
                    try:
                        db.close()
                    except Exception:  # noqa: BLE001
                        pass
                self.mgr.set_status(IMSG_KEY, "error", str(e))
                return
        try:
            top = imessage.max_rowid(self._db)
            rows = imessage.messages_after(self._db, self._cursor, top, senders)
        except Exception as e:  # noqa: BLE001 — db yanked/rotated under us
            self.close()
            self.mgr.set_status(IMSG_KEY, "error",
                                f"couldn't read the Messages database — {e}")
            return
        # §6: the cursor advances past everything each tick — rows the query
        # filtered out (other senders, from-me, tapbacks) are skipped for
        # good, never re-scanned.
        self._cursor = top
        for m in rows:
            if imessage.stale(m):  # §6 backlog fence
                continue
            try:
                self.mgr.dispatch_imessage(m)
            except Exception:  # noqa: BLE001
                # The cursor already moved past this batch, so one bad row must
                # not take the rows behind it with it - they would never be
                # re-read.
                log.exception("iMessage dispatch failed for row %s", m.get("rowid"))


class Listeners:
    """§6 listener manager: reconciles gateway connections and the iMessage
    watcher against the enabled message triggers every LISTEN_TICK_S seconds."""

    def __init__(self, store: Store, engine):
        self.store = store
        self.engine = engine
        self._conns: dict[str, _Conn] = {}  # token-secret id → connection
        self._imsg: _ImsgWatcher | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        t = threading.Thread(target=self._loop, daemon=True, name="ad-listeners")
        t.start()

    def stop(self) -> None:
        self._stop.set()
        # Snapshot: a _reconcile already in flight on the manager thread keeps
        # mutating _conns until it notices the stop flag — iterating the live
        # dict here can raise mid-shutdown, before backend.json is unlinked.
        for c in list(self._conns.values()):
            c.stop()
        if self._imsg is not None:
            self._imsg.close()

    def _loop(self) -> None:
        while not self._stop.wait(LISTEN_TICK_S):
            try:
                self._reconcile()
            except Exception:  # noqa: BLE001 — same rule as the scheduler tick
                log.exception("listener reconcile failed")

    def _desired_secrets(self) -> tuple[set[str], set[str]]:
        """→ (discord token-secret ids, imessage sender handles), enabled
        triggers only. The senders both gate the watcher's existence and
        scope its §6 minimized read."""
        with self.store.lock:
            secrets = {t["secret"] for a in self.store.autos.values()
                       for t in a["triggers"]
                       if t["kind"] == "discord" and t["enabled"]}
            senders = {t["from"] for a in self.store.autos.values()
                       for t in a["triggers"]
                       if t["kind"] == "imessage" and t["enabled"]}
        return secrets, senders

    def _reconcile(self) -> None:
        # §6 shutdown: stop() has already stopped every connection it could see,
        # so a reconcile running past it must not create new ones — the flag is
        # re-read before each creation below, since the pass outlives the check.
        if self._stop.is_set():
            return
        desired, senders = self._desired_secrets()
        # A connection whose thread is gone is no connection at all - treat it
        # as missing so the loop below recreates it (§6: the listener for an
        # enabled trigger is always running or reconnecting, never absent).
        for secret, conn in list(self._conns.items()):
            if not conn.is_alive():
                log.warning("discord listener for %s died - restarting it", secret[:8])
                self._conns.pop(secret).stop()
        for secret in desired - self._conns.keys():
            if self._stop.is_set():
                return
            conn = _Conn(secret, self)
            self._conns[secret] = conn
            conn.start()
        for secret in list(self._conns.keys() - desired):
            self._conns.pop(secret).stop()
            with self.store.lock:
                self.store.listener_status.pop(secret, None)
        # §6: one iMessage watcher total while any enabled imessage trigger
        # exists — nothing touches chat.db (or any permission) before that,
        # and the watcher closes when the last trigger goes.
        # §2 platform layer: the whole watcher is capability-gated — on an OS
        # without iMessage nothing ever touches chat.db or osascript.
        if (senders and self._imsg is None and not self._stop.is_set()
                and platform.current().capabilities.imessage):
            self._imsg = _ImsgWatcher(self)
        elif not senders and self._imsg is not None:
            self._imsg.close()
            self._imsg = None
            with self.store.lock:
                self.store.listener_status.pop(IMSG_KEY, None)
        if self._imsg is not None:
            self._imsg.tick(senders)

    def set_status(self, key: str, state: str, error: str | None = None) -> None:
        """Push a listener's §4.3 `connection` state; on change,
        `automation.changed` fires for every automation holding a trigger on
        that listener — `key` is a Discord token-secret id, or IMSG_KEY for
        the watcher. §19: each event carries the automation's list row
        (`connection` is list-shape) so clients patch in place, no /state
        refetch per affected automation."""
        status = {"state": state, **({"error": error} if error else {})}
        with self.store.lock:
            if self.store.listener_status.get(key) == status:
                return
            self.store.listener_status[key] = status
            affected = [(a["id"], self.store.auto_json(a, full=False))
                        for a in self.store.autos.values()
                        if any((t["kind"] == "imessage" if key == IMSG_KEY
                                else t["kind"] == "discord" and t.get("secret") == key)
                               for t in a["triggers"])]
        for automation_id, row in affected:
            hub.publish("automation.changed", automationId=automation_id, automation=row)

    def dispatch(self, secret: str, d: dict, bot_id: str | None,
                 bot_roles: set[str] | None = None,
                 chan_names: dict[str, str] | None = None,
                 guild_names: dict[str, str] | None = None) -> None:
        """Route one MESSAGE_CREATE to every matching enabled trigger — at
        most one firing per automation (§6: same-moment occurrences coalesce)."""
        with self.store.lock:
            hits = []
            for a in self.store.autos.values():
                t = next((t for t in a["triggers"]
                          if t["kind"] == "discord" and t["enabled"]
                          and t.get("secret") == secret
                          and message_matches(t, d, bot_id, bot_roles)), None)
                if t:
                    hits.append((a, t))
        for a, t in hits:
            # Per hit: one automation's firing must never drop the others'
            # (§6 - the same rule the §19 app-start fan-out follows).
            try:
                fire_trigger(self.store, self.engine, a, t,
                             payload=trigger_payload(
                                 t, d,
                                 channel_name=(chan_names or {}).get(d.get("channel_id")),
                                 guild_name=(guild_names or {}).get(d.get("guild_id"))))
            except Exception:  # noqa: BLE001
                log.exception("discord firing on %r failed", a["name"])

    def dispatch_imessage(self, m: dict) -> None:
        """Route one decoded chat.db row to every matching enabled imessage
        trigger — at most one firing per automation, like Discord."""
        with self.store.lock:
            hits = []
            for a in self.store.autos.values():
                t = next((t for t in a["triggers"]
                          if t["kind"] == "imessage" and t["enabled"]
                          and imessage.message_matches(t, m)), None)
                if t:
                    hits.append((a, t))
        for a, t in hits:
            try:
                fire_trigger(self.store, self.engine, a, t,
                             payload=imessage.trigger_payload(m))
            except Exception:  # noqa: BLE001 - per hit, as in `dispatch`
                log.exception("iMessage firing on %r failed", a["name"])
