"""iMessage support (§6): read the Messages database (chat.db) for the
listener manager's watcher, decode `attributedBody` typedstream blobs, send
messages through Messages.app via osascript, and answer the §19 permission
probes. The Mac's own Messages account is the identity — no bot, no secret.

The typedstream heuristic (segment marker 0x84 0x01 0x2b, BER-style length,
UTF-16 LE / UTF-8) is ported from OpenClaw's MIT-licensed `imsg`."""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .platform.base import run_bounded

log = logging.getLogger("autowright.imessage")

# §15 knobs — configuration only, the code path is identical.
CHAT_DB = os.environ.get("AUTOWRIGHT_CHAT_DB",
                         str(Path.home() / "Library" / "Messages" / "chat.db"))
MAX_AGE_S = float(os.environ.get("AUTOWRIGHT_IMSG_MAX_AGE_S", "120"))
REPLY_LIMIT = 4000  # §6.1: iMessage reply truncation cap

# chat.db timestamps count from 2001-01-01 (Apple epoch); modern macOS stores
# nanoseconds, ancient rows seconds — anything past ~2032 in seconds is ns.
_APPLE_EPOCH = 978307200
_NS_CUTOFF = 10_000_000_000

FDA_ERROR = ("needs Full Disk Access — grant it in System Settings → "
             "Privacy & Security → Full Disk Access")
NO_DB_ERROR = ("Messages database not found — open Messages.app on this Mac "
               "and sign in first")


class DbError(Exception):
    """chat.db can't be opened/read — the message is the §4.3 `connection` error."""


# ---------- typedstream decoding ----------

_SEG_START = b"\x01+"  # typedstream: inline string segment opens


def _decode_candidate(raw: bytes) -> str:
    s = (raw[2:].decode("utf-16-le", errors="ignore")
         if raw[:2] == b"\xff\xfe" else raw.decode("utf-8", errors="ignore"))
    return s.lstrip("".join(chr(c) for c in range(0x20)))


def decode_attributed_body(blob: bytes | None) -> str:
    """Best-effort text from an `attributedBody` typedstream blob (§6 scheme,
    ported from imsg): scan every `0x01 0x2b` segment, read its BER-style
    length — a byte < 0x80, or 0x81 + one byte, or 0x82 + two little-endian
    bytes — decode UTF-16 LE on a BOM else UTF-8, and keep the longest
    candidate. Returns "" when nothing decodes — an undecodable message never
    fires (§4.3)."""
    if not blob:
        return ""
    if blob[:2] == b"\xff\xfe":  # whole-blob UTF-16
        return _decode_candidate(blob)
    best = ""
    i = 0
    while True:
        j = blob.find(_SEG_START, i)
        if j == -1 or j + 2 >= len(blob):
            break
        i = j + 2  # next scan resumes past this marker whatever happens below
        k = i
        n = blob[k]
        k += 1
        if n == 0x81:
            if k + 1 > len(blob):
                continue
            n = blob[k]
            k += 1
        elif n == 0x82:
            if k + 2 > len(blob):
                continue
            n = int.from_bytes(blob[k:k + 2], "little")
            k += 2
        elif n >= 0x80:
            continue  # not a length encoding this scheme knows
        cand = _decode_candidate(blob[k:k + n])
        if len(cand) > len(best):
            best = cand
    return best


# ---------- chat.db reading ----------

def open_db() -> sqlite3.Connection:
    """Open chat.db read-only. Deliberately NOT immutable=1 — the db is WAL
    and an immutable open would miss live updates (§6). Raises DbError with
    the plain-word §4.3 `connection` message."""
    path = Path(CHAT_DB)
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")  # Messages writes while we read
        conn.execute("SELECT 1 FROM message LIMIT 1").fetchall()
        return conn
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "unable to open" in msg or "authorization" in msg:
            raise DbError(FDA_ERROR if path.exists() or not _fda_ok(path.parent)
                          else NO_DB_ERROR) from e
        raise DbError(f"couldn't read the Messages database — {e}") from e


def _fda_ok(messages_dir: Path) -> bool:
    """Whether ~/Library/Messages is listable at all — distinguishes a missing
    chat.db (Messages never signed in) from a TCC denial, where even stat/list
    on the directory fails."""
    try:
        messages_dir.iterdir().__next__()
        return True
    except PermissionError:
        return False
    except (StopIteration, FileNotFoundError, OSError):
        return True


def fda_status() -> bool:
    """§19 GET /imessage/permissions `fullDisk`: can chat.db be opened right
    now? False on a TCC denial or a missing db; never prompts."""
    try:
        open_db().close()
        return True
    except DbError:
        return False


def max_rowid(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(MAX(ROWID), 0) AS m FROM message").fetchone()
    return int(row["m"])


# §6 data minimization: the query itself is scoped to rows the enabled
# triggers could fire on — configured senders only, incoming only, plain
# messages only (no tapbacks/reactions, no group-event rows like renames).
# Conversations no trigger watches are never read or decoded.
_QUERY = """
SELECT m.ROWID AS rowid, m.guid, m.text, m.attributedBody AS body,
       m.is_from_me, m.date, m.associated_message_type AS assoc,
       h.id AS sender, c.guid AS chat_guid, c.style AS chat_style
FROM message m
JOIN handle h ON h.ROWID = m.handle_id
LEFT JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
LEFT JOIN chat c ON c.ROWID = cmj.chat_id
WHERE m.ROWID > ? AND m.ROWID <= ?
  AND m.is_from_me = 0
  AND COALESCE(m.associated_message_type, 0) = 0
  AND COALESCE(m.item_type, 0) = 0
  AND LOWER(h.id) IN ({senders})
GROUP BY m.ROWID
ORDER BY m.ROWID
"""


def messages_after(conn: sqlite3.Connection, rowid: int, top: int,
                   senders: set[str]) -> list[dict]:
    """Rows in (rowid, top] from the configured senders, oldest first, text
    decoded. The caller advances its cursor to `top` afterwards, so filtered
    rows are skipped for good, never re-scanned. Edits rewrite their existing
    row, so the cursor never re-sees them either (§4.3)."""
    want = sorted({s.lower() for s in senders if s})
    if not want:
        return []
    q = _QUERY.format(senders=",".join("?" * len(want)))
    out = []
    for r in conn.execute(q, (rowid, top, *want)):
        raw = r["date"] or 0
        ts = _APPLE_EPOCH + (raw / 1e9 if raw > _NS_CUTOFF else raw)
        out.append({
            "rowid": r["rowid"],
            "guid": r["guid"],
            "text": r["text"] or decode_attributed_body(r["body"]),
            "sender": r["sender"] or "",
            "chat": r["chat_guid"] or "",
            "group": r["chat_style"] == 43,  # 43 = group chat, 45 = 1:1
            "from_me": bool(r["is_from_me"]),
            "tapback": bool(r["assoc"]),  # reactions/tapbacks never fire (§4.3)
            "ts": ts,
        })
    return out


# ---------- firing rules (§4.3) ----------

def message_matches(t: dict, m: dict) -> bool:
    """§4.3 firing rules for one enabled imessage trigger against a decoded
    row. Messages this Mac sent never fire (loop safety); direct chats only;
    sender must equal `from` case-insensitively; pattern is a substring."""
    if m["from_me"] or m["group"] or m["tapback"] or not m["text"]:
        return False
    if m["sender"].lower() != (t.get("from") or "").lower():
        return False
    pat = t.get("pattern")
    if pat and pat.lower() not in m["text"].lower():
        return False
    return True


def trigger_payload(m: dict) -> dict:
    """§4.5 triggerPayload for an iMessage firing."""
    return {
        "kind": "imessage",
        "text": m["text"],
        "sender": m["sender"],
        "chat": m["chat"],
        "messageId": m["guid"],
        "at": datetime.fromtimestamp(m["ts"], tz=timezone.utc).isoformat(),
    }


def stale(m: dict) -> bool:
    """§6 backlog fence: a cursor-passed row older than MAX_AGE_S when
    observed never fires — a sleep/restart backlog cannot fire a burst."""
    return time.time() - m["ts"] > MAX_AGE_S


# ---------- sending (osascript) ----------

_SEND_SCRIPT = """on run {targetChat, msgText}
tell application "Messages" to send msgText to chat id targetChat
end run"""

_PROBE_SCRIPT = 'tell application "Messages" to count chats'


def send_message(chat_guid: str, text: str) -> str | None:
    """§6.1: send `text` to a Messages chat by guid via osascript (resolved
    through PATH — tests shim it). Returns an error message, None on success."""
    if not chat_guid:
        return "the triggering message has no chat to reply to"
    try:
        # Bounded through the shared helper: a grandchild holding osascript's
        # pipe would wedge subprocess.run's own timeout in communicate().
        r = run_bounded(
            ["osascript", "-e", _SEND_SCRIPT, chat_guid, str(text)[:REPLY_LIMIT]],
            timeout=30)
    except FileNotFoundError:
        return "osascript not found"
    if r is None:
        return "Messages didn't answer within 30 s"
    if r.returncode != 0:
        err = (r.stderr or "").strip() or f"osascript exited {r.returncode}"
        if "-1743" in err:
            err = ("not allowed to control Messages — grant it in System "
                   "Settings → Privacy & Security → Automation")
        return err
    _remember_automation("granted")
    return None


# ---------- Automation permission (§19) ----------

def _remember_automation(state: str) -> None:
    """The §19 remembered Automation state: macOS offers no prompt-free read,
    so the backend keeps the result of its most recent Apple Events send,
    persisted in settings storage (§5)."""
    from .storage import StoreUnwritableError, store  # function-level: storage must import freely

    with store.lock:
        if store.settings.get("imessageAutomation") == state:
            return
        store.settings["imessageAutomation"] = state
        try:
            store.save_settings()
        except StoreUnwritableError as e:
            # §5 read-only degradation: a convenience write from the send path
            # must never crash the listener — the state stays in memory only.
            log.warning("not persisting imessageAutomation: %s", e)


def automation_status() -> str:
    from .storage import store

    return store.settings.get("imessageAutomation") or "unknown"


def automation_probe() -> str:
    """§19 POST /imessage/permissions/automation-probe: fire a benign Apple
    Event at Messages.app — macOS shows the consent prompt if never answered
    (and may launch Messages.app). Blocks until the user answers."""
    try:
        r = run_bounded(["osascript", "-e", _PROBE_SCRIPT], timeout=300)
        state = "granted" if r is not None and r.returncode == 0 else "denied"
    except FileNotFoundError:
        state = "denied"
    _remember_automation(state)
    return state
