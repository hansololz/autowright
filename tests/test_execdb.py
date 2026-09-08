"""SQLite execution index (§5) — schema wipe on version bump, upsert, ISO-TEXT timestamps."""


def make_header(**over):
    h = {
        "id": "e-1", "automation_id": "a-1", "automation_name": "Job",
        "kind": "version", "version": 1,
        "status": "executing", "trigger": "manual",
        "queued_at": None,
        "started_at": "2026-07-20T08:30:00+00:00", "finished_at": None,
        "duration_ms": None, "note": None, "chip": None, "chip_status": None,
        "error": None,
    }
    h.update(over)
    return h


def _db_path(home):
    return home / "executions" / "executions.db"


def test_schema_version_bump_recreates_empty_table(home, monkeypatch):
    from autowright import execdb

    db = execdb.ExecDB(_db_path(home))
    db.upsert(make_header())
    assert set(db.load_all()) == {"e-1"}
    db.close()

    # same version → rows survive a reopen
    db = execdb.ExecDB(_db_path(home))
    assert set(db.load_all()) == {"e-1"}
    db.close()

    # §5: the DB is only an index — a schema bump drops the table and lets the
    # startup yaml reconcile rebuild it (yaml is the source of truth).
    monkeypatch.setattr(execdb, "SCHEMA_VERSION", execdb.SCHEMA_VERSION + 1)
    db = execdb.ExecDB(_db_path(home))
    assert db.load_all() == {}
    # the wipe is one-time: rows written under the new version persist
    db.upsert(make_header(id="e-2"))
    db.close()
    db = execdb.ExecDB(_db_path(home))
    assert set(db.load_all()) == {"e-2"}
    db.close()

    # §5: any mismatch rebuilds — a DOWNGRADE (stamp newer than the build's
    # version) must also drop rather than run against a newer table shape
    monkeypatch.setattr(execdb, "SCHEMA_VERSION", execdb.SCHEMA_VERSION - 2)
    db = execdb.ExecDB(_db_path(home))
    assert db.load_all() == {}
    db.close()


def test_upsert_updates_mutable_fields_and_roundtrips_error(home):
    from autowright import execdb

    db = execdb.ExecDB(_db_path(home))
    db.upsert(make_header())
    db.upsert(make_header(
        automation_name="Job Renamed", status="failed",
        finished_at="2026-07-20T08:31:05+00:00", duration_ms=65_000,
        note="boom note", chip="2 issues", chip_status="attention",
        error={"step": "01-say.py", "message": "boom", "reason": "crash"},
        # started_at IS mutable on conflict — a §6 queue promotion re-stamps it
        # when the record stops waiting and starts executing
        started_at="2026-07-20T09:00:00+00:00",
        # immutable-on-conflict columns: changed values must NOT stick
        kind="draft", version=9, trigger="cron"))
    h = db.load_all()["e-1"]
    assert h["automation_name"] == "Job Renamed"
    assert h["status"] == "failed"
    assert h["finished_at"] == "2026-07-20T08:31:05+00:00"
    assert h["duration_ms"] == 65_000
    assert h["note"] == "boom note"
    assert h["chip"] == "2 issues" and h["chip_status"] == "attention"
    # the error dict flattens to columns and reconstructs on load
    assert h["error"] == {"step": "01-say.py", "message": "boom", "reason": "crash"}
    # ON CONFLICT updates only the mutable columns — identity fields keep row one's values
    assert h["kind"] == "version" and h["version"] == 1
    assert h["trigger"] == "manual"
    # …but started_at follows the record, so a promoted §6 queue entry sorts by
    # when it actually started rather than when it was admitted
    assert h["started_at"] == "2026-07-20T09:00:00+00:00"

    # row without error_message → error comes back as None (falsy, not a dict)
    db.upsert(make_header(id="e-2"))
    assert db.load_all()["e-2"]["error"] is None
    db.close()


def test_trigger_sender_reads_the_stamped_field_only(home):
    """§4.5/§5 triggerSender: stamped onto the record once at creation — upsert
    reads only trigger_sender, never reaching into the payload (no dual-shape
    fallback)."""
    from autowright import execdb

    db = execdb.ExecDB(_db_path(home))
    db.upsert(make_header(id="e-2", trigger="discord", trigger_sender="Ana"))
    # a payload without the stamped field contributes nothing
    db.upsert(make_header(id="e-3", trigger="discord", trigger_payload={
        "kind": "discord", "text": "hi", "sender": "Dave"}))
    rows = db.load_all()
    assert rows["e-2"]["trigger_sender"] == "Ana"
    assert rows["e-3"]["trigger_sender"] is None
    db.close()


def test_timestamps_store_as_iso_text_and_sort_chronologically(home):
    """§5: started/finished/queued are UTC ISO-8601 microsecond TEXT — stored
    verbatim, and lexicographic order = chronological order, so the same-second
    ordering promise survives a restart."""
    from autowright import execdb

    db = execdb.ExecDB(_db_path(home))
    early = "2026-07-20T08:30:00.000123+00:00"
    late = "2026-07-20T08:30:00.000456+00:00"  # same second — microseconds decide
    db.upsert(make_header(id="e-a", started_at=early, queued_at=early,
                          finished_at="2026-07-20T08:31:05.500000+00:00"))
    db.upsert(make_header(id="e-b", started_at=late))
    row = db.conn.execute(
        "SELECT started_at, finished_at, queued_at FROM executions WHERE id='e-a'").fetchone()
    assert row == (early, "2026-07-20T08:31:05.500000+00:00", early)  # TEXT, verbatim
    order = [r[0] for r in db.conn.execute(
        "SELECT id FROM executions ORDER BY started_at DESC, id")]
    assert order == ["e-b", "e-a"]
    loaded = db.load_all()
    assert loaded["e-a"]["started_at"] == early
    assert loaded["e-b"]["finished_at"] is None
    db.close()
