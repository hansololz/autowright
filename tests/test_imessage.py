"""§6 iMessage: typedstream decoding, chat.db reading (fixture db), firing
rules, the watcher's cursor/backlog-fence/permission behavior, osascript
sending (fake in tests/bin), and the §19 permission endpoints."""
import sqlite3
import time

import pytest
from conftest import use_fake_osascript

from autowright import imessage
from autowright.imessage import (
    decode_attributed_body, message_matches, messages_after, trigger_payload,
)

# Apple epoch offset (2001-01-01) — fixture rows store seconds * 1e9 (ns).
EPOCH = 978307200


def _blob(text: str, *, utf16: bool = False, prefix: bytes = b"NSString") -> bytes:
    """Synthesize the decodable core of an attributedBody typedstream blob —
    §6 BER-style lengths: < 0x80 inline, 0x81 + one byte, 0x82 + two LE bytes."""
    raw = (b"\xff\xfe" + text.encode("utf-16-le")) if utf16 else text.encode()
    n = len(raw)
    if n < 0x80:
        ln = bytes([n])
    elif n < 0x100:
        ln = b"\x81" + bytes([n])
    else:
        ln = b"\x82" + n.to_bytes(2, "little")
    return b"streamtyped junk " + prefix + b"\x01\x94\x84\x01+" + ln + raw + b"\x86\x84 tail"


def test_decode_attributed_body():
    assert decode_attributed_body(_blob("hello")) == "hello"
    assert decode_attributed_body(_blob("héllo ✨")) == "héllo ✨"
    assert decode_attributed_body(_blob("y" * 200)) == "y" * 200  # 0x81 + one byte
    assert decode_attributed_body(_blob("x" * 300)) == "x" * 300  # 0x82 + two bytes
    assert decode_attributed_body(_blob("wide", utf16=True)) == "wide"
    assert decode_attributed_body(_blob("mut", prefix=b"NSMutableString")) == "mut"
    # several segments → the longest candidate wins (imsg selection rule)
    multi = _blob("short") + _blob("the much longer real message")
    assert decode_attributed_body(multi) == "the much longer real message"
    # undecodable → "" (the message never fires, §4.3)
    assert decode_attributed_body(None) == ""
    assert decode_attributed_body(b"") == ""
    assert decode_attributed_body(b"no markers here") == ""
    assert decode_attributed_body(b"NSString truncated") == ""


# ---------------------------------------------------------------- fixture db

def _make_db(path):
    """Minimal chat.db shape: the tables/columns the §6 queries touch."""
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE handle (ROWID INTEGER PRIMARY KEY, id TEXT);
        CREATE TABLE chat (ROWID INTEGER PRIMARY KEY, guid TEXT, style INTEGER);
        CREATE TABLE message (ROWID INTEGER PRIMARY KEY, guid TEXT, text TEXT,
            attributedBody BLOB, is_from_me INTEGER DEFAULT 0, date INTEGER,
            associated_message_type INTEGER DEFAULT 0, item_type INTEGER DEFAULT 0,
            handle_id INTEGER);
        CREATE TABLE chat_message_join (chat_id INTEGER, message_id INTEGER);
        INSERT INTO handle VALUES (1, '+15551234567'), (2, 'pal@example.com');
        INSERT INTO chat VALUES (1, 'iMessage;-;+15551234567', 45),
                                (2, 'chat000123', 43);
    """)
    db.commit()
    return db


def _add_msg(db, rowid, *, text=None, body=None, sender=1, chat=1,
             from_me=0, assoc=0, item=0, ts=None):
    ts = time.time() if ts is None else ts
    db.execute("INSERT INTO message VALUES (?,?,?,?,?,?,?,?,?)",
               (rowid, f"g{rowid}", text, body, from_me,
                int((ts - EPOCH) * 1e9), assoc, item, sender))
    db.execute("INSERT INTO chat_message_join VALUES (?,?)", (chat, rowid))
    db.commit()


@pytest.fixture()
def chat_db(tmp_path, monkeypatch):
    path = tmp_path / "chat.db"
    db = _make_db(path)
    monkeypatch.setattr(imessage, "CHAT_DB", str(path))
    yield db
    db.close()


ALL = {"+15551234567", "pal@example.com"}


def test_messages_after_decodes_rows(chat_db):
    _add_msg(chat_db, 1, text="plain", ts=1_800_000_000)
    _add_msg(chat_db, 2, body=_blob("from blob"), sender=2, chat=2)
    conn = imessage.open_db()
    rows = messages_after(conn, 0, 99, ALL)
    assert [r["rowid"] for r in rows] == [1, 2]
    assert rows[0] == {"rowid": 1, "guid": "g1", "text": "plain",
                       "sender": "+15551234567", "chat": "iMessage;-;+15551234567",
                       "group": False, "from_me": False, "tapback": False,
                       "ts": pytest.approx(1_800_000_000, abs=1)}
    assert (rows[1]["text"], rows[1]["sender"], rows[1]["group"]) == (
        "from blob", "pal@example.com", True)
    # cursor semantics: strictly after `rowid`, at most `top`
    assert [r["rowid"] for r in messages_after(conn, 1, 99, ALL)] == [2]
    assert messages_after(conn, 2, 99, ALL) == []
    assert messages_after(conn, 0, 1, ALL) and len(messages_after(conn, 0, 1, ALL)) == 1
    conn.close()


def test_messages_after_reads_only_trigger_relevant_rows(chat_db):
    """§6 data minimization: the query never returns — so never decodes —
    rows outside the configured senders, from-me rows, tapbacks, or
    group-event items."""
    _add_msg(chat_db, 1, text="from watched sender")
    _add_msg(chat_db, 2, text="other conversation", sender=2)   # not configured
    _add_msg(chat_db, 3, text="sent by this Mac", from_me=1)    # loop safety
    _add_msg(chat_db, 4, text="a tapback", assoc=2000)          # reaction
    _add_msg(chat_db, 5, text=None, item=2)                     # group event row
    conn = imessage.open_db()
    rows = messages_after(conn, 0, 99, {"+15551234567"})
    assert [(r["rowid"], r["text"]) for r in rows] == [(1, "from watched sender")]
    # sender matching is case-insensitive
    assert [r["rowid"] for r in messages_after(conn, 0, 99, {"PAL@EXAMPLE.COM"})] == [2]
    # no configured senders → nothing is read at all
    assert messages_after(conn, 0, 99, set()) == []
    conn.close()


def test_open_db_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(imessage, "CHAT_DB", str(tmp_path / "nope" / "chat.db"))
    with pytest.raises(imessage.DbError) as e:
        imessage.open_db()
    assert "Messages database not found" in str(e.value)
    assert imessage.fda_status() is False


def test_message_matches_rules():
    t = {"kind": "imessage", "enabled": True, "from": "+15551234567"}
    m = {"text": "please Deploy now", "sender": "+15551234567", "chat": "c",
         "group": False, "from_me": False, "tapback": False}
    assert message_matches(t, m)
    # sender must equal `from` — case-insensitive exact match
    assert not message_matches({**t, "from": "+15559999999"}, m)
    assert message_matches({"from": "PAL@example.com"}, {**m, "sender": "pal@example.com"})
    # this Mac's own messages never fire (loop safety), groups and tapbacks never fire
    assert not message_matches(t, {**m, "from_me": True})
    assert not message_matches(t, {**m, "group": True})
    assert not message_matches(t, {**m, "tapback": True})
    assert not message_matches(t, {**m, "text": ""})
    # pattern: case-insensitive substring
    assert message_matches({**t, "pattern": "deploy"}, m)
    assert not message_matches({**t, "pattern": "rollback"}, m)


def test_trigger_payload_shape():
    m = {"rowid": 9, "guid": "g9", "text": "hi", "sender": "+15551234567",
         "chat": "iMessage;-;+15551234567", "group": False, "from_me": False,
         "tapback": False, "ts": 1_800_000_000.0}
    p = trigger_payload(m)
    assert p["kind"] == "imessage" and p["text"] == "hi"
    assert p["sender"] == "+15551234567" and p["chat"] == "iMessage;-;+15551234567"
    assert p["messageId"] == "g9" and p["at"].startswith("20")


# ---------------------------------------------------------------- watcher

class _FakeMgr:
    def __init__(self):
        self.status = []
        self.fired = []
        self.renamed = []  # §3 consume_renamed_marker calls (notify_fda flag)

    def set_status(self, key, state, error=None):
        self.status.append((key, state, error))

    def dispatch_imessage(self, m):
        self.fired.append(m)

    def consume_renamed_marker(self, *, notify_fda):
        self.renamed.append(notify_fda)


def test_watcher_cursor_and_fence(chat_db, monkeypatch):
    from autowright.listeners import _ImsgWatcher

    _add_msg(chat_db, 1, text="history")  # present before the watcher opens
    mgr = _FakeMgr()
    w = _ImsgWatcher(mgr)
    w.tick(ALL)
    # §6: cursor starts at MAX(ROWID) — history never fires
    assert mgr.status[-1][:2] == ("imessage", "connected") and mgr.fired == []
    _add_msg(chat_db, 2, text="fresh")
    _add_msg(chat_db, 3, text="slept through", ts=time.time() - 9999)  # backlog fence
    _add_msg(chat_db, 4, text="unwatched sender", sender=2)
    w.tick({"+15551234567"})
    assert [m["text"] for m in mgr.fired] == ["fresh"]
    w.tick(ALL)  # cursor advanced past everything — nothing re-fires, even row 4
    assert len(mgr.fired) == 1
    w.close()


def test_watcher_parks_without_db_and_heals(tmp_path, monkeypatch):
    from autowright.listeners import _ImsgWatcher

    path = tmp_path / "late" / "chat.db"
    monkeypatch.setattr(imessage, "CHAT_DB", str(path))
    mgr = _FakeMgr()
    w = _ImsgWatcher(mgr)
    w.tick({"+15551234567"})
    assert mgr.status[-1][1] == "error"  # §4.3 conn error, re-probed each tick
    path.parent.mkdir()
    db = _make_db(path)
    w.tick({"+15551234567"})  # heals without a restart (§6)
    assert mgr.status[-1][:2] == ("imessage", "connected")
    _add_msg(db, 1, text="now firing")
    w.tick({"+15551234567"})
    assert [m["text"] for m in mgr.fired] == ["now firing"]
    w.close()
    db.close()


# ---------------------------------------------------------------- sending

@pytest.fixture()
def osascript_log(tmp_path, monkeypatch, home):
    log = tmp_path / "osa.log"
    monkeypatch.setenv("AUTOWRIGHT_TEST_OSASCRIPT_LOG", str(log))
    monkeypatch.delenv("AUTOWRIGHT_TEST_OSASCRIPT_FAIL", raising=False)
    use_fake_osascript(monkeypatch, imessage)
    return _Recorded(log)


class _Recorded:
    """The fake's log file. Read as UTF-8 — that is what the recorder writes on
    every OS (the locale codec would be cp1252 on Windows and couldn't even
    hold the reply text these tests send)."""

    def __init__(self, path):
        self.path = path

    def read_text(self):
        return self.path.read_text(encoding="utf-8")


def test_send_message_via_osascript(osascript_log):
    assert imessage.send_message("iMessage;-;+15551234567", "hello there") is None
    argv = osascript_log.read_text().strip().split("\t")
    assert argv[-2:] == ["iMessage;-;+15551234567", "hello there"]
    assert "send msgText to chat id targetChat" in argv[1]
    # a successful Apple Events send is remembered as granted (§19)
    assert imessage.automation_status() == "granted"


def test_send_message_truncates_and_reports_denial(osascript_log, monkeypatch):
    imessage.send_message("c", "x" * 5000)
    assert len(osascript_log.read_text().strip().split("\t")[-1]) == imessage.REPLY_LIMIT
    monkeypatch.setenv("AUTOWRIGHT_TEST_OSASCRIPT_FAIL", "1")
    err = imessage.send_message("c", "hi")
    assert "not allowed to control Messages" in err
    assert imessage.send_message("", "hi") == \
        "the triggering message has no chat to reply to"


def test_send_reply_routes_imessage(osascript_log):
    from autowright.listeners import send_reply

    payload = {"kind": "imessage", "chat": "iMessage;-;+15551234567",
               "sender": "+15551234567"}
    assert send_reply(payload, "done ✔") is None
    assert osascript_log.read_text().strip().split("\t")[-1] == "done ✔"


def test_automation_probe(osascript_log, monkeypatch):
    assert imessage.automation_probe() == "granted"
    assert imessage.automation_status() == "granted"
    monkeypatch.setenv("AUTOWRIGHT_TEST_OSASCRIPT_FAIL", "1")
    assert imessage.automation_probe() == "denied"
    assert imessage.automation_status() == "denied"
