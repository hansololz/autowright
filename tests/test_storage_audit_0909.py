"""Store-layer audit regressions (2026-09-09).

§5/§6 read and delete seams: the background reaper behind "no `rmtree` ever
runs under the lock", the mapping-or-default read seam, the one-pass latest
execution, the cached `startedMs`, best-effort log reads and the log counter's
lifetime — plus the one-shot `at` and queue-wait timestamp readings that share
the same hand-editable-disk rule.
"""
import threading

from conftest import make_version

from autowright import paths


def _write_memory(store, a, name="seen.yaml", text="v: 1\n"):
    d = store.auto_dir(a) / "memory"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")
    return d


def _rmtree_threads(monkeypatch):
    """Record the thread every `shutil.rmtree` runs on, for the §6 rule that
    no tree walk may happen while a caller holds store.lock."""
    import shutil

    from autowright import storage

    seen: list[str] = []
    real = shutil.rmtree

    def spy(path, *args, **kw):
        seen.append(threading.current_thread().name)
        return real(path, *args, **kw)

    monkeypatch.setattr(storage.shutil, "rmtree", spy)
    return seen


# ---------- §6: no rmtree ever runs under the store lock ----------

def test_snapshot_delete_under_the_store_lock_reaps_outside_it(store, monkeypatch):
    """§6: the delete renames the tree aside under the lock (O(1)) and the
    walk happens on the reaper thread — a caller holding the lock is never
    stalled by it."""
    seen = _rmtree_threads(monkeypatch)
    a = store.create_automation(make_version(), "Snapper", None)
    _write_memory(store, a)
    m = store.snapshot_memory(a, "manual")
    root = store.snapshots_dir(a)

    main = threading.current_thread().name
    with store.lock:  # an API caller's hold around the whole request
        assert store.delete_snapshot(a, m["id"]) is True
        # Gone for every reader the moment the lock is released...
        assert not (root / m["id"]).exists()
        assert store.list_snapshots(a) == []
        # ...and the walk never happened on this thread, so the hold stayed O(1)
        # (the reaper is free to be finished with it already).
        assert main not in seen

    store.drain_reaper()
    assert list(root.glob(f"{store.DELETED_PREFIX}*")) == []
    assert seen and main not in seen


def test_clear_memory_and_draft_discard_reap_their_trees(store):
    """§6: clearing memory and discarding a draft both carry a memory tree —
    renamed aside, then reaped."""
    a = store.create_automation(make_version(), "Clearer", None)
    d = _write_memory(store, a)

    store.clear_memory(a)
    assert d.exists() and list(d.iterdir()) == []  # a fresh, empty memory/

    store.save_draft(a, make_version())
    (store.draft_dir(a) / "memory").mkdir(parents=True, exist_ok=True)
    (store.draft_dir(a) / "memory" / "note.txt").write_text("x", encoding="utf-8")
    store.delete_draft(a)
    assert not store.draft_dir(a).exists()
    assert a["draft"] is None

    store.drain_reaper()
    assert list(store.auto_dir(a).glob(f"{store.DELETED_PREFIX}*")) == []


def test_snapshot_prune_and_orphan_sweep_reap_asides(store):
    """§6.3 commit: the crash-orphan sweep and the unnamed-snapshot prune both
    delete whole memory copies — through the reaper, like every other one."""
    a = store.create_automation(make_version(), "Pruner", None)
    _write_memory(store, a)
    root = store.snapshots_dir(a)
    root.mkdir(parents=True, exist_ok=True)
    orphan = root / "orphan"  # a dir with no snapshot.yaml — a crash orphan
    orphan.mkdir()

    made = [store.snapshot_memory(a, "manual") for _ in range(7)]
    assert not orphan.exists()
    kept = {m["id"] for m in store.list_snapshots(a)}
    assert len(kept) == 5 and kept <= {m["id"] for m in made}

    store.drain_reaper()
    assert list(root.glob(f"{store.DELETED_PREFIX}*")) == []
    assert sorted(p.name for p in root.iterdir()) == sorted(kept)


def test_restore_reaps_the_displaced_memory_tree(store):
    """§6.3 restore: the displaced memory/ is renamed aside under the lock and
    removed on the reaper thread."""
    a = store.create_automation(make_version(), "Restorer", None)
    _write_memory(store, a, text="v: 1\n")
    m = store.snapshot_memory(a, "manual")
    _write_memory(store, a, text="v: 2\n")

    assert store.restore_snapshot(a, m["id"])["id"] == m["id"]
    assert (store.auto_dir(a) / "memory" / "seen.yaml").read_text(encoding="utf-8") == "v: 1\n"

    store.drain_reaper()
    assert list(store.auto_dir(a).glob(f"{store.DELETED_PREFIX}*")) == []


def test_one_reaper_thread_serves_every_store(store):
    """§6: ONE process-wide reaper thread removes the aside trees — a second
    Store (the §4.9 data-location reload builds one) reuses it instead of
    leaving a thread behind per instance."""
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Reaped", None)
    _write_memory(store, a)
    store.clear_memory(a)
    store.drain_reaper()
    reapers = [t for t in threading.enumerate() if t.name == "autowright-reaper"]
    assert len(reapers) == 1

    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    _write_memory(s2, b)
    s2.clear_memory(b)
    s2.drain_reaper()
    # the same thread object, not a second one wearing the same name
    assert [t for t in threading.enumerate() if t.name == "autowright-reaper"] == reapers


def test_pending_slot_children_are_reaped_beside_the_slot(store, home, monkeypatch):
    """§6: the pending create-mode slot's children are renamed aside *beside*
    the slot — the emptied slot itself must vanish — and reaped outside the
    lock, so a draft carrying a memory copy never walks under it."""
    store.save_draft(None, make_version(), name="Pending")
    dd = paths.pending_draft_dir()
    mem = dd / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "note.txt").write_text("x", encoding="utf-8")

    seen = _rmtree_threads(monkeypatch)  # only the delete's own walks
    main = threading.current_thread().name
    store.delete_draft(None)
    assert not dd.exists()  # no thread kept → the slot vanishes whole

    assert main not in seen  # the walk never happened under the lock

    store.drain_reaper()
    assert not mem.exists()
    assert list(home.glob(f"{store.DELETED_PREFIX}*")) == []


def test_restore_reaps_stale_swap_leftovers(store, monkeypatch):
    """§6: "its crash leftovers included" — the stale swap dirs a previous
    restore died inside are as big as memory/, so they go aside and are reaped
    outside the lock rather than walked under it."""
    from autowright.storage import MEMORY_SWAP_OLD, MEMORY_SWAP_TMP

    seen = _rmtree_threads(monkeypatch)
    a = store.create_automation(make_version(), "Leftovers", None)
    _write_memory(store, a, text="v: 1\n")
    m = store.snapshot_memory(a, "manual")
    _write_memory(store, a, text="v: 2\n")
    leftovers = [store.auto_dir(a) / MEMORY_SWAP_TMP, store.auto_dir(a) / MEMORY_SWAP_OLD]
    for stale in leftovers:
        stale.mkdir()
        (stale / "seen.yaml").write_text("v: 0\n", encoding="utf-8")

    main = threading.current_thread().name
    assert store.restore_snapshot(a, m["id"])["id"] == m["id"]
    assert (store.auto_dir(a) / "memory" / "seen.yaml").read_text(encoding="utf-8") == "v: 1\n"
    assert not any(stale.exists() for stale in leftovers)
    assert main not in seen

    store.drain_reaper()
    assert list(store.auto_dir(a).glob(f"{store.DELETED_PREFIX}*")) == []


def test_aside_directories_in_the_new_locations_are_swept_at_load(store, home):
    """§6: a crash between a rename and its reap must leave nothing behind —
    the load sweeps the automation dir and the snapshots dir too, not just
    executions/ and automations/."""
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Crashy", None)
    d = store.auto_dir(a)
    mem_aside = d / f"{store.DELETED_PREFIX}mem"        # clear_memory / restore
    snap_aside = d / "memory-snapshots" / f"{store.DELETED_PREFIX}snap"  # delete/prune
    for aside in (mem_aside, snap_aside):
        aside.mkdir(parents=True)
        (aside / "seen.yaml").write_text("v: 1\n", encoding="utf-8")

    s2 = Store()
    s2.load_all()
    assert a["id"] in s2.autos
    assert not mem_aside.exists() and not snap_aside.exists()


def test_an_aside_in_the_app_support_root_is_swept_at_load(store, home):
    """§6: the pending slot's children go aside beside the slot, so the
    app-support root needs the same crash sweep as executions/ and
    automations/."""
    from autowright.storage import Store

    aside = paths.app_support() / f"{store.DELETED_PREFIX}slot"
    aside.mkdir(parents=True)
    (aside / "note.txt").write_text("x", encoding="utf-8")

    s2 = Store()
    s2.load_all()
    assert not aside.exists()


# ---------- §5: the mapping-or-default read seam ----------

def test_non_mapping_draft_test_yaml_reads_as_absent(store, caplog):
    """§5: `draft/test.yaml` holding a bare scalar used to AttributeError out
    of every automation read."""
    a = store.create_automation(make_version(), "Tester", None)
    store.save_draft(a, make_version())
    (store.draft_dir(a) / "test.yaml").write_text("hello\n", encoding="utf-8")

    with caplog.at_level("WARNING"):
        payload = store.auto_json(a, full=True)
    assert "test" not in payload["draft"]
    assert "doesn't hold a mapping" in caplog.text


def test_non_mapping_pending_slot_manifest_reads_as_absent(store, caplog):
    """§5: the same seam on the create-mode slot's identity keys."""
    store.save_draft(None, make_version(), name="Pending")
    manifest = paths.pending_draft_dir() / "automation" / "automation.yaml"
    manifest.write_text("hello\n", encoding="utf-8")

    with caplog.at_level("WARNING"):
        assert store.load_pending_draft() is None
        assert store.pending_draft_summary() == {"name": "New automation", "updatedAt": None}
        assert store.draft_container_json(None) == {"draft": None, "agentId": None}
    assert "doesn't hold a mapping" in caplog.text


def test_non_mapping_snapshot_yaml_reads_as_absent(store, caplog):
    a = store.create_automation(make_version(), "Snapshotter", None)
    _write_memory(store, a)
    m = store.snapshot_memory(a, "manual")
    (store.snapshots_dir(a) / m["id"] / "snapshot.yaml").write_text("hello\n", encoding="utf-8")

    with caplog.at_level("WARNING"):
        assert store.list_snapshots(a) == []
    assert "doesn't hold a mapping" in caplog.text


# ---------- §5: an unreadable version manifest is an absent version ----------

def test_unreadable_current_version_manifest_skips_the_automation(store, home, caplog):
    """§5: never loaded as an empty version that would run zero steps and
    "succeed" — an automation that can't resolve its current version is
    skipped at load, like the empty-versions case."""
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Broken", None)
    assert store.save_new_version(a, make_version(note="v2")) == 2
    (store.auto_dir(a) / "versions" / "v2" / "automation.yaml").write_text(
        "steps: [oops\n", encoding="utf-8")

    s2 = Store()
    with caplog.at_level("WARNING"):
        s2.load_all()
    assert a["id"] not in s2.autos
    assert "unusable automation.yaml" in caplog.text
    assert "can't resolve its current version v2" in caplog.text

    # the sole version unreadable is the empty-versions case, same skip
    caplog.clear()
    (store.auto_dir(a) / "versions" / "v1" / "automation.yaml").write_text(
        "steps: [oops\n", encoding="utf-8")
    s3 = Store()
    with caplog.at_level("WARNING"):
        s3.load_all()
    assert a["id"] not in s3.autos
    assert "has no version folders" in caplog.text


def test_unreadable_old_version_manifest_only_drops_that_version(store, home, caplog):
    a = store.create_automation(make_version(), "Halfway", None)
    assert store.save_new_version(a, make_version(note="v2")) == 2
    (store.auto_dir(a) / "versions" / "v1" / "automation.yaml").write_text(
        "just a string\n", encoding="utf-8")

    from autowright.storage import Store

    s2 = Store()
    with caplog.at_level("WARNING"):
        s2.load_all()
    b = s2.autos[a["id"]]
    assert sorted(b["versions"]) == [2] and b["current_version"] == 2
    assert "unusable automation.yaml" in caplog.text


# ---------- §5: the latest execution is filled by one startup pass ----------

def test_refresh_exec_derived_matches_the_per_automation_computation(store):
    """§5 "filled by one startup query": the single pass must agree with the
    per-automation scan it replaced, record for record."""
    autos = [store.create_automation(make_version(), f"Auto {i}", None) for i in range(3)]
    stamps = ["2026-09-01T10:00:00", "2026-09-03T10:00:00", "2026-09-02T10:00:00"]
    for i, a in enumerate(autos):
        for j, when in enumerate(stamps):
            h = store.create_execution(a, "version", 1, "manual", [],
                                       status=("succeeded", "failed", "skipped")[j])
            h["started_at"] = when
            store.update_execution(h)
        # a queued firing and a draft test — neither may count as the latest
        q = store.create_execution(a, "version", 1, "manual", [], status="queued")
        q["started_at"] = "2026-09-09T10:00:00"
        store.update_execution(q)
        if i == 2:
            t = store.create_execution(a, "test", None, "test", [], status="succeeded")
            t["started_at"] = "2026-09-09T11:00:00"
            store.update_execution(t)

    store._refresh_exec_derived()
    for a in autos:
        brute = store._latest_exec(a["id"])
        assert a["_latest"] == brute
        assert a["_last_status"] == (brute["status"] if brute else "none")
        assert a["_last_exec_at"] == (brute["started_at"] if brute else None)
        assert a["_last_status"] == "failed"  # the newest ran record, not the test

    # an automation with nothing but a queued firing derives "none"
    empty = store.create_automation(make_version(), "Idle", None)
    store._refresh_exec_derived()
    assert empty["_latest"] is None and empty["_last_status"] == "none"


# ---------- §19: startedMs is derived once per header ----------

def test_exec_started_ms_is_cached_beside_its_stored_timestamp(store, monkeypatch):
    from autowright import storage

    calls = []
    real = storage.lenient_local

    def spy(v):
        calls.append(v)
        return real(v)

    monkeypatch.setattr(storage, "lenient_local", spy)
    h = {"started_at": "2026-09-05T10:00:00"}
    first = storage.exec_started_ms(h)
    assert storage.exec_started_ms(h) == first
    assert len(calls) == 1  # parsed once, then read off the header

    h["started_at"] = "2026-09-05T11:00:00"  # a §6 queue promotion re-stamps it
    assert storage.exec_started_ms(h) == first + 3600 * 1000
    assert len(calls) == 2


def test_started_ms_cache_never_reaches_disk_or_the_client(store):
    """The cache rides `_`-prefixed header keys — every serializer names its
    keys explicitly, so they stay in memory like `_pass_start`."""
    from autowright.storage import exec_started_ms

    a = store.create_automation(make_version(), "Serialized", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    exec_started_ms(h)
    assert "_started_ms" in h
    store.update_execution(h)

    assert not any(k.startswith("_") for k in store.exec_json(h, full=True))
    on_disk = store.exec_yaml_path(h["id"]).read_text(encoding="utf-8")
    assert "_started_ms" not in on_disk
    assert not any(k.startswith("_") for k in store.exec_header(h))


# ---------- §5: logs are best-effort ----------

def test_read_log_replaces_undecodable_bytes(store):
    """§5: an undecodable byte run (a crash mid-append) is replaced — the
    parsable lines still come back, never a 500 on a 1 Hz pane."""
    import json

    a = store.create_automation(make_version(), "Logger", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="executing")
    p = store.log_file(h["id"], store.EXEC_LOG)
    p.parent.mkdir(parents=True, exist_ok=True)
    good = json.dumps({"timestamp": "2026-09-05T10:00:00+00:00", "kind": "out",
                       "sequence": 1, "text": "hi"}).encode()
    p.write_bytes(good + b"\n" + b'{"kind": "out", "text": "\xff\xfe"}\n')

    lines = store.read_log(h["id"])
    assert [ln["text"] for ln in lines][0] == "hi"
    assert len(lines) == 2  # the damaged line still parses, with replacements


def test_read_log_answers_empty_on_an_unreadable_file(store, monkeypatch):
    a = store.create_automation(make_version(), "Unreadable", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="executing")
    store.append_log_line(h["id"], store.EXEC_LOG,
                          {"timestamp": "2026-09-05T10:00:00+00:00", "kind": "out",
                           "sequence": 1, "text": "hi"})

    from pathlib import Path

    def boom(self, *args, **kw):
        raise PermissionError("nope")

    monkeypatch.setattr(Path, "read_text", boom)
    assert store.read_log(h["id"]) == []


# ---------- §5: the log counter's lifetime and atomicity ----------

def test_log_counts_are_dropped_at_the_terminal_transition(store):
    """§5: the per-file counter lives in memory only while the execution is
    live, and re-seeds from the file on a later in-place retry."""
    a = store.create_automation(make_version(), "Counter", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="executing")
    key = (h["id"], store.EXEC_LOG)
    for i in range(3):
        store.append_log_line(h["id"], store.EXEC_LOG,
                              {"timestamp": "2026-09-05T10:00:00+00:00", "kind": "out",
                               "sequence": i + 1, "text": "hi"})
    assert store._log_counts[key] == 3

    h["status"] = "succeeded"
    store.update_execution(h)
    assert key not in store._log_counts

    # an in-place retry appends to the same file — the count continues from disk
    store.append_log_line(h["id"], store.EXEC_LOG,
                          {"timestamp": "2026-09-05T10:00:00+00:00", "kind": "out",
                           "sequence": 4, "text": "again"})
    assert store._log_counts[key] == 4
    assert len(store.read_log(h["id"])) == 4


def test_concurrent_appends_count_every_line_exactly_once(store):
    """§5 line cap: the counter's read-modify-write is one step, so parallel
    steps of one execution can't take the same slot."""
    a = store.create_automation(make_version(), "Racer", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="executing")

    def append():
        for i in range(500):
            store.append_log_line(h["id"], store.EXEC_LOG,
                                  {"timestamp": "2026-09-05T10:00:00+00:00", "kind": "out",
                                   "sequence": i + 1, "text": "line"})

    threads = [threading.Thread(target=append) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert store._log_counts[(h["id"], store.EXEC_LOG)] == 1000
    assert len(store.log_file(h["id"], store.EXEC_LOG)
               .read_text(encoding="utf-8").splitlines()) == 1000


# ---------- §5: retention reads `days` leniently ----------

def test_retention_days_falls_back_to_the_default(store):
    """§5: a non-numeric hand-edited `days` sweeps at 90 days rather than
    silently disabling the sweep for the session."""
    from datetime import datetime, timedelta

    a = store.create_automation(make_version(), "Retained", None)
    old = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    old["started_at"] = (datetime.now() - timedelta(days=120)).isoformat(timespec="seconds")
    store.update_execution(old)
    recent = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    recent["started_at"] = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    store.update_execution(recent)

    store.settings["days"] = "ninety"
    assert store.retention_cleanup() == 1
    assert old["id"] not in store.execs and recent["id"] in store.execs


def test_retention_days_zero_is_clamped_to_one_day(store):
    """§4.9: `days` is >= 1 — a hand-edited 0 sweeps at a one-day window
    instead of being read as "no value" and reset to the 90-day default."""
    from datetime import datetime, timedelta

    a = store.create_automation(make_version(), "Clamped", None)
    old = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    old["started_at"] = (datetime.now() - timedelta(days=2)).isoformat(timespec="seconds")
    store.update_execution(old)
    fresh = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    store.update_execution(fresh)

    store.settings["days"] = 0
    assert store.retention_cleanup() == 1
    assert old["id"] not in store.execs and fresh["id"] in store.execs


# ---------- §4.3: an unreadable one-shot `at` is malformed, not spent ----------

def test_unreadable_one_shot_at_is_dropped_with_a_warning(store, home, caplog):
    """§4.3: only a *parsable* past `at` counts as spent. An unquoted
    timestamp loads from YAML as a datetime — dropped with the §5 warning,
    never consumed silently as if it had run."""
    from autowright.storage import Store, load_yaml, save_yaml

    a = store.create_automation(make_version(), "Once", None)
    top_path = store.auto_dir(a) / "automation.yaml"
    top = load_yaml(top_path)
    top["triggers"] = [{"id": "t-1", "kind": "time", "enabled": True,
                        "at": "2020-01-01T09:00:00"}]
    save_yaml(top_path, top)
    # unquoted on disk: YAML hands the loader a datetime, not a string
    top_path.write_text(top_path.read_text(encoding="utf-8")
                        .replace("at: '2020-01-01T09:00:00'", "at: 2020-01-01T09:00:00"),
                        encoding="utf-8")

    s2 = Store()
    with caplog.at_level("WARNING"):
        s2.load_all()
    assert s2.autos[a["id"]]["triggers"] == []
    assert "dropping malformed trigger" in caplog.text


def test_parsable_past_one_shot_is_still_consumed_silently(store, home, caplog):
    from autowright.storage import Store, load_yaml, save_yaml

    a = store.create_automation(make_version(), "Spent", None)
    top_path = store.auto_dir(a) / "automation.yaml"
    top = load_yaml(top_path)
    top["triggers"] = [{"id": "t-1", "kind": "time", "enabled": True,
                        "at": "2020-01-01T09:00:00"}]
    save_yaml(top_path, top)

    s2 = Store()
    with caplog.at_level("WARNING"):
        s2.load_all()
    assert s2.autos[a["id"]]["triggers"] == []
    assert "dropping malformed trigger" not in caplog.text


# ---------- §6: the queue TTL reads a damaged queued_at leniently ----------

def test_waited_seconds_survives_a_naive_or_damaged_queued_at():
    """§6 queue TTL: a hand-edited `queued_at` must not propagate out of
    drain_queue — a naive stamp reads as local time, a non-string as
    just-queued."""
    from datetime import datetime, timedelta

    from autowright import firing

    naive = (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
    waited = firing._waited_s({"queued_at": naive})
    assert 9 * 60 < waited < 11 * 60

    assert firing._waited_s({"queued_at": datetime.now()}) == 0.0
    assert firing._waited_s({"queued_at": "not a time"}) == 0.0
    assert firing._waited_s({}) == 0.0
