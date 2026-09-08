"""SQLite index over execution headers (§5).

`<dataPath>/executions/executions.db` is a pure list/filter index: the
authoritative record is `executions/<uuid>/execution.yaml`, and the engine
writes both together (yaml first). The connection is shared across threads and
every call happens under `Store.lock` (check_same_thread=False relies on that).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 8

# §5: timestamps are stored as the canonical UTC ISO-8601 microsecond TEXT —
# a fixed offset keeps lexicographic order equal to chronological order, so
# the indexes below keep sorting correctly and the same-second ordering
# promise survives a restart.
DDL = """
CREATE TABLE IF NOT EXISTS executions (
  id               TEXT PRIMARY KEY,
  automation_id    TEXT,
  automation_name  TEXT NOT NULL,
  kind             TEXT NOT NULL,
  version          INTEGER,
  status           TEXT NOT NULL,
  "trigger"        TEXT NOT NULL,
  trigger_sender   TEXT,
  queued_at        TEXT,
  started_at       TEXT NOT NULL,
  finished_at      TEXT,
  duration_ms           INTEGER,
  note             TEXT,
  chip             TEXT,
  chip_status      TEXT,
  error_step       TEXT,
  error_message    TEXT,
  error_reason     TEXT
);
CREATE INDEX IF NOT EXISTS index_executions_page   ON executions (started_at DESC, id);
CREATE INDEX IF NOT EXISTS index_executions_automation   ON executions (automation_id, started_at DESC);
CREATE INDEX IF NOT EXISTS index_executions_status ON executions (status, started_at DESC);
"""


class ExecDB:
    def __init__(self, path: Path | None) -> None:
        """`path=None` opens an in-memory index — the §5 degraded mode when the
        executions dir is unreachable or the on-disk DB can't be rebuilt; the
        yaml files stay authoritative either way."""
        if path is None:
            self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(path, check_same_thread=False)
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            if self.conn.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                # The DB is only an index (§5): on any schema change, drop and let
                # startup's yaml reconcile rebuild the rows from disk.
                with self.conn:
                    self.conn.execute("DROP TABLE IF EXISTS executions")
                    self.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self.conn.executescript(DDL)
        except BaseException:
            self.conn.close()
            raise

    def close(self) -> None:
        self.conn.close()

    def upsert(self, h: dict) -> None:
        """Write an execution header row (internal shape, ISO timestamps)."""
        err = h.get("error") or {}
        with self.conn:
            self.conn.execute(
                'INSERT INTO executions (id, automation_id, automation_name, kind, version, status,'
                ' "trigger", trigger_sender, queued_at, started_at, finished_at, duration_ms, note, chip, chip_status,'
                " error_step, error_message, error_reason)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(id) DO UPDATE SET"
                " automation_name=excluded.automation_name, status=excluded.status,"
                # §6 queue promotion re-stamps started_at (the record stops
                # measuring the wait and starts measuring the execution), so the
                # index has to follow or the list sorts on a stale timestamp.
                " started_at=excluded.started_at,"
                " finished_at=excluded.finished_at, duration_ms=excluded.duration_ms, note=excluded.note,"
                " chip=excluded.chip, chip_status=excluded.chip_status,"
                " error_step=excluded.error_step, error_message=excluded.error_message,"
                " error_reason=excluded.error_reason",
                (h["id"], h["automation_id"], h["automation_name"], h["kind"], h.get("version"), h["status"],
                 # §4.5 triggerSender: stamped onto the record once at creation —
                 # every reader takes it from this field alone.
                 h["trigger"], h.get("trigger_sender"), h.get("queued_at"),
                 h["started_at"], h.get("finished_at"), h["duration_ms"], h["note"],
                 h.get("chip"), h.get("chip_status"),
                 err.get("step"), err.get("message"), err.get("reason")))

    def load_all(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for row in self.conn.execute(
                'SELECT id, automation_id, automation_name, kind, version, status, "trigger",'
                " trigger_sender, queued_at, started_at, finished_at, duration_ms, note, chip, chip_status,"
                " error_step, error_message, error_reason FROM executions"):
            (eid, automation_id, automation_name, kind, version, status, trigger, trigger_sender,
             queued, started, finished, duration_ms, note, chip, chip_status,
             err_step, err_message, err_reason) = row
            out[eid] = {
                "id": eid, "automation_id": automation_id, "automation_name": automation_name,
                "kind": kind, "version": version,
                "status": status, "trigger": trigger, "trigger_sender": trigger_sender,
                "queued_at": queued,
                "started_at": started, "finished_at": finished,
                "duration_ms": duration_ms, "note": note,
                "chip": chip, "chip_status": chip_status,
                "error": {"step": err_step, "message": err_message, "reason": err_reason}
                         if err_message else None,
            }
        return out

    def delete(self, execution_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM executions WHERE id=?", (execution_id,))
