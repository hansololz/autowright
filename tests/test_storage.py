from conftest import make_version

from autowright import paths


def test_create_and_reload_roundtrip(store, home):
    from autowright.storage import Store

    trig = {"id": "t-1", "kind": "cron", "enabled": True, "expression": "30 7 * * *",
            "source": "user"}
    a = store.create_automation(make_version(), "My Test Job", "agent-1", triggers=[trig])
    assert (home / "automations" / a["id"] / "versions" / "v1" / "01-say.py").exists()
    assert (home / "automations" / a["id"] / "versions" / "v1" / "spec.md").exists()

    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    assert b["name"] == "My Test Job"
    # §4.3: created enabled → stamped, and the stamp survives the reload.
    assert b["triggers"] == [{**trig, "enabledAt": a["triggers"][0]["enabledAt"]}]
    assert s2.auto_json(b)["triggerChip"] == "Daily 7:30"
    ver = b["versions"][1]
    assert ver["steps"][0]["name"] == "Say hello"
    assert "params['greeting']" in ver["steps"][0]["code"]
    assert ver["spec"][0] == {"kind": "h1", "text": "Test automation"}


def test_concurrency_defaults_and_floors(store, home):
    """§4.1/§6: new automations run one at a time and skip on busy
    (maxParallel 1, maxQueued 0) — parallel runs and queueing are opt-in.
    The floors hold everywhere: never below 1 / below 0, even for
    hand-edited or missing automation.yaml values (clamped at load, never
    dropping the automation)."""
    from autowright.storage import (DEFAULT_MAX_PARALLEL, DEFAULT_MAX_QUEUED,
                                    Store, clamp_max_parallel, clamp_max_queued,
                                    load_yaml, save_yaml)

    assert DEFAULT_MAX_PARALLEL == 1 and DEFAULT_MAX_QUEUED == 0
    a = store.create_automation(make_version(), "Defaults", "agent-1")
    assert a["max_parallel"] == 1 and a["max_queued"] == 0
    assert store.auto_json(a)["maxParallel"] == 1
    assert store.auto_json(a)["maxQueued"] == 0

    # absent keys (pre-concurrency data) load as the defaults
    top_path = home / "automations" / a["id"] / "automation.yaml"
    top = load_yaml(top_path)
    del top["max_parallel"], top["max_queued"]
    save_yaml(top_path, top)
    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    assert b["max_parallel"] == 1 and b["max_queued"] == 0

    # hand-edited below-floor / garbage values clamp at load
    top = load_yaml(top_path)
    top["max_parallel"], top["max_queued"] = 0, -5
    save_yaml(top_path, top)
    s3 = Store()
    s3.load_all()
    c = s3.autos[a["id"]]
    assert c["max_parallel"] == 1 and c["max_queued"] == 0

    # the clamps themselves: floors 1 and 0, unusable values → the defaults
    assert clamp_max_parallel(0) == 1 and clamp_max_parallel(-3) == 1
    assert clamp_max_parallel(4) == 4
    assert clamp_max_queued(-1) == 0 and clamp_max_queued(0) == 0
    assert clamp_max_queued(7) == 7
    assert clamp_max_parallel("junk") == 1 and clamp_max_parallel(None) == 1
    assert clamp_max_queued("junk") == 0 and clamp_max_queued(None) == 0


def test_no_triggers_roundtrip(store, home):
    from autowright.storage import Store

    # No triggers given -> manual / menu bar only.
    a = store.create_automation(make_version(), "No Trigger Job", "agent-1")
    assert a["triggers"] == []

    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    assert b["triggers"] == []
    j = s2.auto_json(b)
    assert j["triggerChip"] == "No triggers"
    assert j["allTriggersOff"] is False
    assert j["nextAtMs"] is None


def test_step_timeout_roundtrip(store, home):
    from autowright.storage import Store

    # §4.1: the internal shape is snake_case only — the API boundary already
    # normalized any client `noTimeout` before storage sees it.
    ver = make_version()
    ver["steps"] = [
        {"file": "01-a.py", "name": "A", "description": "", "code": "from autowright import log\nlog('a')\n", "timeout": 60},
        {"file": "02-b.py", "name": "B", "description": "", "code": "from autowright import log\nlog('b')\n", "no_timeout": True},
    ]
    a = store.create_automation(ver, "Timed", None)

    s2 = Store()
    s2.load_all()
    steps = s2.autos[a["id"]]["versions"][1]["steps"]
    assert steps[0]["timeout"] == 60 and "no_timeout" not in steps[0]
    assert steps[1]["no_timeout"] is True and "timeout" not in steps[1]
    js = [s2.step_json(s) for s in steps]
    assert js[0]["timeout"] == 60 and "noTimeout" not in js[0]
    assert js[1]["noTimeout"] is True and "timeout" not in js[1]


def test_versioning_and_restore(store):
    a = store.create_automation(make_version(), "Versioned", None)
    n = store.save_new_version(a, make_version(description="v2 desc", note="Second"))
    assert n == 2 and a["current_version"] == 2
    # restore v1 → becomes v3; v1/v2 untouched
    n = store.restore_version(a, 1)
    assert n == 3 and a["current_version"] == 3
    assert a["versions"][3]["note"] == "Restored from v1"
    assert set(a["versions"]) == {1, 2, 3}


def test_draft_save_and_discard(store):
    a = store.create_automation(make_version(), "Drafty", None)
    store.save_draft(a, make_version(note="draft note"))
    assert a["draft"]["note"] == "draft note"
    assert (store.auto_dir(a) / "draft" / "automation" / "automation.yaml").exists()
    # §4.4: draft/memory survives a re-save of the working copy…
    dmem = store.auto_dir(a) / "draft" / "memory"
    dmem.mkdir()
    (dmem / "seen.yaml").write_text("x: 1")
    store.save_draft(a, make_version(note="draft note 2"))
    assert (dmem / "seen.yaml").exists()
    # …and dies with the draft.
    store.delete_draft(a)
    assert a["draft"] is None
    assert not (store.auto_dir(a) / "draft").exists()


def test_rename_keeps_directory(store):
    a = store.create_automation(make_version(), "Old Name", None)
    old_dir = store.auto_dir(a)
    store.patch_automation(a, {"name": "Brand New Name"})
    # §5: directories are named by id — a rename never moves them
    assert store.auto_dir(a) == old_dir and old_dir.exists()
    assert store.auto_dir(a).name == a["id"]
    assert store.autos[a["id"]]["name"] == "Brand New Name"


def test_param_value_resolution(store):
    from autowright.storage import resolve_param_value

    d = {"name": "count", "kind": "number", "min": 1, "default": 3}
    assert resolve_param_value(d, {}) == 3
    assert resolve_param_value(d, {"count": 9}) == 9
    warns = []
    assert resolve_param_value(d, {"count": "nine"}, warns) == 3  # kind mismatch → default + warning
    assert warns


def test_permissions_never_versioned(store):
    """§5: enabled_agents / allowed_secrets live only in the top-level file."""
    a = store.create_automation(make_version(), "Perms", None)
    store.patch_automation(a, {"allowedSecrets": ["TOKEN_A"]})
    store.save_new_version(a, make_version(note="v2"))
    import yaml

    vy = yaml.safe_load((store.auto_dir(a) / "versions" / "v2" / "automation.yaml").read_text())
    assert "allowed_secrets" not in vy and "enabled_agents" not in vy
    top = yaml.safe_load((store.auto_dir(a) / "automation.yaml").read_text())
    assert top["allowed_secrets"] == ["TOKEN_A"]


def test_retention_cleanup(store):
    from datetime import datetime, timedelta

    a = store.create_automation(make_version(), "Retained", None)
    h_old = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    h_old["started_at"] = (datetime.now() - timedelta(days=120)).isoformat(timespec="seconds")
    store.update_execution(h_old)
    h_new = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    store.update_execution(h_new)
    store.settings["days"] = 90
    assert store.retention_cleanup() == 1
    assert h_old["id"] not in store.execs and h_new["id"] in store.execs
    store.settings["keepForever"] = True
    assert store.retention_cleanup() == 0


def test_packages_persist_as_bare_names(store):
    from autowright.storage import Store

    # §6.2: manifests carry bare distribution names — no version anywhere.
    pkgs = [{"pip": "pandas", "import": "pandas"}]
    a = store.create_automation(make_version(packages=pkgs), "Uses Pandas", None)
    s2 = Store()
    s2.load_all()
    assert s2.autos[a["id"]]["versions"][1]["packages"] == pkgs


# ---------- §6.3 memory snapshots ----------

def _write_memory(store, a, name="seen.yaml", text="items: [1]\n"):
    d = store.auto_dir(a) / "memory"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)


def test_snapshot_create_layout_and_list(store, home):
    a = store.create_automation(make_version(), "Snappy", None)
    # empty memory is never snapshotted (§6.3)
    assert store.snapshot_memory(a, "pre-clear") is None
    assert store.list_snapshots(a) == []

    _write_memory(store, a)
    m = store.snapshot_memory(a, "manual", name="first")
    d = home / "automations" / a["id"] / "memory-snapshots" / m["id"]
    assert (d / "snapshot.yaml").exists()
    assert (d / "memory" / "seen.yaml").read_text() == "items: [1]\n"
    assert m["reason"] == "manual" and m["name"] == "first"
    assert m["version"] == "v1" and m["files"] == 1 and m["size"] > 0

    snaps = store.list_snapshots(a)
    assert [s["id"] for s in snaps] == [m["id"]]
    j = store.auto_json(a)["snapshots"][0]
    assert j["id"] == m["id"] and j["name"] == "first" and j["files"] == 1
    assert j["version"] == "v1" and j["reason"] == "manual"


def test_snapshot_restore_takes_pre_restore_copy(store):
    a = store.create_automation(make_version(), "Restorer", None)
    _write_memory(store, a, text="v: old\n")
    m = store.snapshot_memory(a, "manual")
    _write_memory(store, a, text="v: new\n")

    assert store.restore_snapshot(a, m["id"]) is not None
    mem = store.auto_dir(a) / "memory"
    assert (mem / "seen.yaml").read_text() == "v: old\n"
    reasons = [s["reason"] for s in store.list_snapshots(a)]
    # restored snapshot stays; current memory was saved as pre-restore first
    assert sorted(reasons) == ["manual", "pre-restore"]
    pre = next(s for s in store.list_snapshots(a) if s["reason"] == "pre-restore")
    pre_dir = store.snapshots_dir(a) / pre["id"] / "memory"
    assert (pre_dir / "seen.yaml").read_text() == "v: new\n"


def test_snapshot_rename_and_delete(store):
    a = store.create_automation(make_version(), "Renamer", None)
    _write_memory(store, a)
    m = store.snapshot_memory(a, "manual")
    assert store.rename_snapshot(a, m["id"], "  pinned  ")["name"] == "pinned"
    assert store.rename_snapshot(a, m["id"], "")["name"] is None
    assert store.rename_snapshot(a, "0" * 36, "x") is None
    # sid is validated before any path join — traversal shapes are rejected
    assert store.get_snapshot(a, "../../../etc/passwd") is None
    assert store.delete_snapshot(a, "../memory") is False
    assert store.delete_snapshot(a, m["id"]) is True
    assert store.list_snapshots(a) == []
    assert store.delete_snapshot(a, m["id"]) is False


def test_snapshot_toggles_gate_automatic_reasons(store):
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Toggler", None)
    # defaults: every automatic reason on
    assert a["memory_snapshots"] == {"pre_version": True, "pre_clear": True, "pre_restore": True}
    _write_memory(store, a)

    # off → the automatic reason skips silently; manual is never gated
    store.patch_automation(a, {"snapshotSettings": {"preClear": False}})
    assert store.snapshot_memory(a, "pre-clear") is None
    m = store.snapshot_memory(a, "manual")
    assert m is not None

    # pre-restore off → restore replaces memory without the undo copy
    store.patch_automation(a, {"snapshotSettings": {"preRestore": False}})
    _write_memory(store, a, text="v: new\n")
    assert store.restore_snapshot(a, m["id"]) is not None
    assert [s["reason"] for s in store.list_snapshots(a)] == ["manual"]

    # partial merge touched only the sent keys; persisted and reloaded as-is
    assert a["memory_snapshots"] == {"pre_version": True, "pre_clear": False, "pre_restore": False}
    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    assert b["memory_snapshots"] == {"pre_version": True, "pre_clear": False, "pre_restore": False}
    assert s2.auto_json(b)["snapshotSettings"] == {
        "preVersion": True, "preClear": False, "preRestore": False}


def test_snapshot_toggles_absent_keys_default_on(store):
    from autowright.storage import Store
    from autowright.yamlio import load_yaml, save_yaml

    a = store.create_automation(make_version(), "Legacyless", None)
    # hand-edited automation.yaml without the memory_snapshots key → all on
    top = store.auto_dir(a) / "automation.yaml"
    data = load_yaml(top)
    del data["memory_snapshots"]
    save_yaml(top, data)
    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    assert b["memory_snapshots"] == {"pre_version": True, "pre_clear": True, "pre_restore": True}


def test_snapshot_retention_prunes_unnamed_keeps_named(store):
    a = store.create_automation(make_version(), "Pruner", None)
    _write_memory(store, a)
    named = store.snapshot_memory(a, "manual", name="keep me")
    for _ in range(8):
        store.snapshot_memory(a, "manual")
    snaps = store.list_snapshots(a)
    unnamed = [s for s in snaps if not s["name"]]
    assert len(unnamed) == 5  # §6.3: newest 5 unnamed survive
    assert any(s["id"] == named["id"] for s in snaps)  # named never auto-deleted


def test_snapshot_orphan_dirs_skipped_and_swept(store):
    a = store.create_automation(make_version(), "Orphan", None)
    _write_memory(store, a)
    orphan = store.snapshots_dir(a) / "deadbeef-dead-dead-dead-deadbeefdead"
    (orphan / "memory").mkdir(parents=True)
    assert store.list_snapshots(a) == []  # no snapshot.yaml → skipped
    store.snapshot_memory(a, "manual")
    assert not orphan.exists()  # swept at the next creation
    assert len(store.list_snapshots(a)) == 1


def test_pending_draft_slot_roundtrip(store):
    """§4.4 pending create-mode slot: save → load/json → delete."""
    from autowright import paths
    from conftest import make_version

    assert store.draft_container_json(None) == {"draft": None, "agentId": None}
    ver = make_version()
    ver["step_agents"] = ["ag1"]
    ver["allowed_secrets"] = ["TOKEN"]
    store.save_draft(None, ver, name="Pending One", agent_id="ag1",
                     triggers=[{"kind": "cron", "expression": "0 9 * * *"}])
    assert (paths.pending_draft_dir() / "automation" / "automation.yaml").exists()

    j = store.draft_container_json(None)
    assert j["agentId"] == "ag1"
    d = j["draft"]
    assert d["name"] == "Pending One"
    assert d["stepAgents"] == ["ag1"] and d["allowedSecrets"] == ["TOKEN"]
    assert d["triggers"] == [{"kind": "cron", "expression": "0 9 * * *"}]
    assert [s["name"] for s in d["steps"]] == [s["name"] for s in ver["steps"]]
    assert d["steps"][0]["code"] == ver["steps"][0]["code"]

    # re-keep preserves created_at, bumps updated_at metadata on disk
    store.save_draft(None, ver, name="Pending Two", agent_id=None)
    assert store.draft_container_json(None)["draft"]["name"] == "Pending Two"

    store.delete_draft(None)
    assert store.draft_container_json(None) == {"draft": None, "agentId": None}
    assert not paths.pending_draft_dir().exists()


def test_open_draft_makes_pending_container(store):
    """§4.4: opening the create flow makes the slot's container (memory/ only —
    §11 tests execute as execution records) exist, without touching contents
    already there."""
    from autowright import paths
    from conftest import make_version

    store.open_draft(None)
    assert (paths.pending_draft_dir() / "memory").is_dir()
    assert store.draft_container_json(None) == {"draft": None, "agentId": None}

    # re-open never clobbers a kept draft or memory contents
    store.save_draft(None, make_version(), name="Kept")
    marker = paths.pending_draft_dir() / "memory" / "notes.txt"
    marker.write_text("kept", encoding="utf-8")
    store.open_draft(None)
    assert marker.read_text(encoding="utf-8") == "kept"
    assert store.draft_container_json(None)["draft"]["name"] == "Kept"


def test_pending_draft_summary(store):
    """§19 /state pendingDraft: None while the slot holds no draft (even with
    the container dirs present), the identity summary once one is kept."""
    from conftest import make_version

    assert store.pending_draft_summary() is None
    store.open_draft(None)
    assert store.pending_draft_summary() is None

    store.save_draft(None, make_version(), name="Kept One")
    s = store.pending_draft_summary()
    assert s["name"] == "Kept One" and s["updatedAt"]

    store.delete_draft(None)
    assert store.pending_draft_summary() is None


# ---------- appended coverage: load resilience, index self-heal, logs, retention ----------

def test_crashed_version_write_ignored_on_load(store):
    """§5 crash-safe write order: a vN+1 folder holding a step script but no
    manifest (the commit point) is skipped at load — the automation still
    loads with its last complete version, without raising."""
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Crashy", None)
    partial = store.auto_dir(a) / "versions" / "v2"
    partial.mkdir()
    (partial / "01-say.py").write_text('log("half-written")\n', encoding="utf-8")

    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    assert set(b["versions"]) == {1}
    assert b["current_version"] == 1
    assert b["versions"][1]["steps"][0]["name"] == "Say hello"


def test_reconcile_exec_index_restores_missing_row(store, caplog):
    """§5: execution.yaml is authoritative — a row deleted from the sqlite
    index is rebuilt from the yaml at the next load."""
    import logging
    import sqlite3

    from autowright.storage import Store

    a = store.create_automation(make_version(), "Indexed", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    store.update_execution(h)

    db = store.executions_dir() / "executions.db"
    store.close_exec_db()
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("DELETE FROM executions WHERE id=?", (h["id"],))
    conn.close()

    with caplog.at_level(logging.WARNING, logger="autowright.storage"):
        s2 = Store()
        s2.load_all()
    assert h["id"] in s2.execs
    r = s2.execs[h["id"]]
    assert r["status"] == "succeeded"
    assert r["automation_id"] == a["id"] and r["automation_name"] == "Indexed"
    assert r["started_at"] == h["started_at"]
    assert any("restored from its execution.yaml" in rec.message for rec in caplog.records)


def test_corrupt_settings_and_agents_yaml_fall_back(home, caplog):
    """§5: hand-editable disk — corrupt YAML never bricks startup; defaults
    plus a warning instead."""
    import logging

    from autowright import paths
    from autowright.storage import DEFAULT_SETTINGS, Store

    paths.settings_file().write_text("{{{:::\nnot: [valid", encoding="utf-8")
    paths.agents_file().write_text("[unclosed", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="autowright.yamlio"):
        s = Store()
        s.load_all()
    assert s.settings == dict(DEFAULT_SETTINGS)
    assert s.agents == []
    assert sum("unreadable YAML" in rec.message for rec in caplog.records) >= 2


def test_unreadable_toplevel_yaml_is_readonly_for_session(home):
    """§5 read-only degradation: a top-level file that failed to load is never
    saved back — the corrupt bytes survive every mutation attempt."""
    import pytest

    from autowright import paths
    from autowright.storage import Store, StoreUnwritableError

    corrupt = "{{{:::\nnot: [valid"
    for p in (paths.settings_file(), paths.agents_file(), paths.secrets_file()):
        p.write_text(corrupt, encoding="utf-8")
    s = Store()
    s.load_all()
    s.secrets.append({"id": "11111111-2222-4333-8444-555555555555",
                      "name": "NEW_ONE", "description": "", "set": True})
    with pytest.raises(StoreUnwritableError):
        s.save_secrets()
    with pytest.raises(StoreUnwritableError):
        s.save_agents()
    with pytest.raises(StoreUnwritableError):
        s.save_settings()
    for p in (paths.settings_file(), paths.agents_file(), paths.secrets_file()):
        assert p.read_text(encoding="utf-8") == corrupt


def test_unreadable_yaml_guard_resets_on_reload(home):
    """§5: the read-only flags belong to one load — fixing the file and
    reloading makes it writable again; an absent file was never flagged."""
    from autowright import paths
    from autowright.storage import Store
    from autowright.yamlio import load_yaml

    paths.secrets_file().write_text("[unclosed", encoding="utf-8")
    s = Store()
    s.load_all()
    # repaired on disk → the next load drops the flag and saves work again
    paths.secrets_file().unlink()
    s2 = Store()
    s2.load_all()
    s2.secrets.append({"id": "11111111-2222-4333-8444-555555555555",
                      "name": "BACK_ONE", "description": "", "set": True})
    s2.save_secrets()
    raw = load_yaml(paths.secrets_file(), {})["secrets"]
    assert [x["name"] for x in raw] == ["BACK_ONE"]


def test_load_triggers_consumes_past_oneshot_and_dedupes_app_start(store, caplog):
    import logging

    from autowright.storage import Store
    from autowright.yamlio import load_yaml, save_yaml

    trig = {"id": "keep", "kind": "cron", "enabled": True, "expression": "0 8 * * *",
            "source": "user"}
    a = store.create_automation(make_version(), "Triggered", None, triggers=[trig])
    top = store.auto_dir(a) / "automation.yaml"
    data = load_yaml(top)
    data["triggers"] = [
        trig,
        {"id": "gone", "kind": "time", "enabled": True, "at": "2020-01-01T08:00"},
        {"id": "as1", "kind": "app_start", "enabled": True},
        {"id": "as2", "kind": "app_start", "enabled": True},
        # §4.3: cron `source` is required — a source-less stored cron is
        # malformed and dropped with a warning, never read as "spec"
        {"id": "srcless", "kind": "cron", "enabled": True, "expression": "0 9 * * *"},
    ]
    save_yaml(top, data)

    with caplog.at_level(logging.WARNING, logger="autowright.storage"):
        s2 = Store()
        s2.load_all()
    trigs = s2.autos[a["id"]]["triggers"]
    # the past one-shot was consumed (§4.3) — silently, no warning names it
    assert [t["kind"] for t in trigs] == ["cron", "app_start"]
    assert not any("gone" in rec.message for rec in caplog.records)
    # §4.3: at most one app_start — the duplicate dropped with a warning
    assert [t["id"] for t in trigs if t["kind"] == "app_start"] == ["as1"]
    assert any("duplicate app-start" in rec.message for rec in caplog.records)
    # the source-less cron dropped as malformed — the valid one survived
    assert [t["id"] for t in trigs if t["kind"] == "cron"] == ["keep"]
    assert any("malformed trigger" in rec.message and "srcless" in rec.message
               for rec in caplog.records)


def test_load_triggers_discord_roundtrip(store):
    from autowright.storage import Store

    trig = {"id": "d1", "kind": "discord", "enabled": True, "channel": "42",
            "secret": "9b2f4e12-8c3d-4f6a-9e01-2b7c5d8a1f34",  # §4.3: the token secret's id
            "pattern": "go", "mention": True}
    a = store.create_automation(make_version(), "Chatty", None, triggers=[trig])
    s2 = Store()
    s2.load_all()
    loaded = s2.autos[a["id"]]["triggers"]
    assert loaded == [{**trig, "enabledAt": a["triggers"][0]["enabledAt"]}]


def test_load_skips_unresolvable_secret_entries(store, caplog):
    """§4.8/§5 lenient load: a secrets.yaml entry without an id (or name)
    can't be referenced — skipped with a warning, never healed (no id minted,
    no write-back), never fatal."""
    import logging

    from autowright import paths
    from autowright.storage import Store
    from autowright.yamlio import load_yaml, save_yaml

    good = {"id": "9b2f4e12-8c3d-4f6a-9e01-2b7c5d8a1f34", "name": "GOOD_ONE",
            "description": "", "set": True}
    save_yaml(paths.secrets_file(), {"secrets": [
        good,
        {"name": "NO_ID_ONE", "description": "", "set": True},
        {"id": "11111111-2222-4333-8444-555555555555", "description": "", "set": True},
    ]})
    with caplog.at_level(logging.WARNING, logger="autowright.storage"):
        s2 = Store()
        s2.load_all()
    assert [s["name"] for s in s2.secrets] == ["GOOD_ONE"]
    assert sum("can't be referenced" in rec.message for rec in caplog.records) == 2
    # never healed: the file on disk is untouched — no minted ids, no rewrite
    raw = load_yaml(paths.secrets_file(), {})["secrets"]
    assert [s.get("id") for s in raw] == [good["id"], None,
                                          "11111111-2222-4333-8444-555555555555"]


def test_load_skips_unresolvable_agent_entries(store, caplog):
    """§4.7/§5 lenient load: an agents.yaml entry without an id can't be
    referenced — skipped with a warning, never fatal."""
    import logging

    from autowright import paths
    from autowright.storage import Store
    from autowright.yamlio import load_yaml, save_yaml

    data = load_yaml(paths.agents_file(), {}) or {}
    data["agents"] = [{"name": "No Id One", "harness": "Claude Code",
                       "mode": "default", "model": None},
                      *(data.get("agents") or [])]
    save_yaml(paths.agents_file(), data)
    with caplog.at_level(logging.WARNING, logger="autowright.storage"):
        s2 = Store()
        s2.load_all()
    assert all(g.get("id") for g in s2.agents)
    assert not any(g.get("name") == "No Id One" for g in s2.agents)
    assert any("can't be referenced" in rec.message for rec in caplog.records)


def test_read_log_bounds_and_bad_lines(store):
    a = store.create_automation(make_version(), "Loggy", None)
    steps = [{"name": "Say hello", "file": "01-say.py"}]
    h = store.create_execution(a, "version", 1, "manual", steps, status="succeeded")
    name = store.log_name("01-say.py", 0, 1)
    store.append_log_line(h["id"], name, {"ts": "x", "t": "0:00", "k": "out", "sequence": 1, "text": "one"})
    with open(store.log_file(h["id"], name), "a", encoding="utf-8") as f:
        f.write("this line is not json\n")
    store.append_log_line(h["id"], name, {"ts": "x", "t": "0:01", "k": "out", "sequence": 2, "text": "two"})

    # interleaved non-JSON line skipped, the rest returned in order
    assert [l["text"] for l in store.read_log(h["id"], 0, 1)] == ["one", "two"]
    # out-of-range step index / attempt → empty, never an exception
    assert store.read_log(h["id"], 5, 1) == []
    assert store.read_log(h["id"], -1, 1) == []
    assert store.read_log(h["id"], 0, 99) == []


def test_size_label_boundaries():
    from autowright.storage import size_label

    assert size_label(0) == "0 B"
    assert size_label(1023) == "1023 B"
    assert size_label(1024) == "1.0 KB"
    assert size_label(1024 * 1024 - 1) == "1024.0 KB"
    assert size_label(1024 * 1024) == "1.0 MB"
    assert size_label(1024 ** 3 - 1) == "1024.0 MB"
    assert size_label(1024 ** 3) == "1.0 GB"


def test_retention_skips_executing_missing_and_corrupt_rows(store, caplog):
    import logging
    from datetime import datetime, timedelta

    a = store.create_automation(make_version(), "Sweepy", None)
    old_iso = (datetime.now() - timedelta(days=120)).isoformat(timespec="seconds")

    h_exec = store.create_execution(a, "version", 1, "manual", [])          # still executing
    h_exec["started_at"] = old_iso                                  # ancient, but live wins
    h_missing = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    h_missing["started_at"] = None
    h_corrupt = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    h_corrupt["started_at"] = "not-a-timestamp"
    h_old = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    h_old["started_at"] = old_iso
    store.update_execution(h_old)

    store.settings["days"] = 90
    with caplog.at_level(logging.WARNING, logger="autowright.storage"):
        assert store.retention_cleanup() == 1                       # only the honest old row
    assert h_old["id"] not in store.execs
    for h in (h_exec, h_missing, h_corrupt):                        # all skipped, sweep not aborted
        assert h["id"] in store.execs
    assert any("unparsable started_at" in rec.message for rec in caplog.records)


def test_notes_md_write_reload_and_serialize(store):
    """§4.1/§5 notes lifecycle: nonempty notes land as the version folder's
    notes.md; a disk reload carries them into version_json and auto_json."""
    from autowright.storage import Store

    ver = make_version(notes="Remember: the feed paginates.")
    a = store.create_automation(ver, "Notey", None)
    nf = store.auto_dir(a) / "versions" / "v1" / "notes.md"
    assert nf.read_text(encoding="utf-8") == "Remember: the feed paginates.\n"

    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    assert b["versions"][1]["notes"] == "Remember: the feed paginates."
    assert s2.version_json(b, 1, b["versions"][1])["notes"] == "Remember: the feed paginates."
    assert s2.auto_json(b)["notes"] == "Remember: the feed paginates."


def test_notes_md_cleared_notes_unlink_the_file(store):
    """§5: an empty (or whitespace-only) notes value on a re-save of the same
    folder unlinks notes.md — absent when empty."""
    a = store.create_automation(make_version(), "Notey2", None)
    store.save_draft(a, make_version(notes="working thoughts"))
    nf = store.auto_dir(a) / "draft" / "automation" / "notes.md"
    assert nf.read_text(encoding="utf-8") == "working thoughts\n"

    store.save_draft(a, make_version(notes=""))
    assert not nf.exists()

    store.save_draft(a, make_version(notes="back again"))
    assert nf.exists()
    store.save_draft(a, make_version(notes="   \n"))
    assert not nf.exists()


def test_step_file_named_notes_md_gets_renamed(store):
    """safe_step_filename's keep set: a step claiming the reserved `notes.md`
    name (not a plain NN-slug.py shape anyway) falls back to the generated
    name — the real notes document survives untouched."""
    import yaml

    from autowright.storage import safe_step_filename

    assert safe_step_filename("notes.md", 1, "Sneaky", {"notes.md"}) == "01-sneaky.py"

    ver = make_version(notes="real notes")
    ver["steps"] = [{"file": "notes.md", "name": "Sneaky", "description": "",
                     "code": "print('s')\n"}]
    a = store.create_automation(ver, "Sneak", None)
    vd = store.auto_dir(a) / "versions" / "v1"
    assert (vd / "notes.md").read_text(encoding="utf-8") == "real notes\n"
    meta = yaml.safe_load((vd / "automation.yaml").read_text(encoding="utf-8"))
    assert meta["steps"][0]["file"] == "01-sneaky.py"
    assert (vd / "01-sneaky.py").read_text(encoding="utf-8") == "print('s')\n"


def test_read_log_derives_time_from_timestamp(store):
    """§5: stored lines carry only the UTC `ts` — the local `t` label is
    derived at serialization; an unparsable `ts` serves `t == ""`."""
    from autowright import timefmt

    a = store.create_automation(make_version(), "Derive", None)
    steps = [{"name": "Say hello", "file": "01-say.py"}]
    h = store.create_execution(a, "version", 1, "manual", steps, status="succeeded")
    name = store.log_name("01-say.py", 0, 1)
    ts = "2026-07-01T12:00:00.000000+00:00"
    store.append_log_line(h["id"], name, {"timestamp": ts, "kind": "out", "sequence": 1, "text": "one"})
    store.append_log_line(h["id"], name, {"timestamp": "not-a-timestamp", "kind": "out",
                                          "sequence": 2, "text": "two"})
    lines = store.read_log(h["id"], 0, 1)
    assert [l["text"] for l in lines] == ["one", "two"]
    assert lines[0]["time"] == timefmt.parse_local(ts).strftime("%H:%M:%S")
    assert lines[1]["time"] == ""  # bad timestamp → empty label, line still served


def test_chat_json_skips_bad_lines_and_unreadable_file(tmp_path):
    """§11 thread reads mirror read_log's resilience: malformed JSON, non-dict
    lines, and kind-less dicts are skipped; an unreadable file is []."""
    import json as jsonlib

    from autowright.storage import Store

    d = tmp_path / "container"
    d.mkdir()
    good = {"id": "c1", "kind": "user", "text": "hi"}
    (d / "chat.jsonl").write_text(
        jsonlib.dumps(good) + "\n"
        + "this line is not json\n"
        + jsonlib.dumps(["a", "list"]) + "\n"
        + jsonlib.dumps({"text": "kindless"}) + "\n"
        + "\n"
        + jsonlib.dumps({"kind": "agent", "text": "yo"}) + "\n",
        encoding="utf-8")
    assert Store.chat_json(d) == [good, {"kind": "agent", "text": "yo"}]
    assert Store.chat_json(tmp_path / "nowhere") == []       # missing file
    d2 = tmp_path / "bad"
    d2.mkdir()
    (d2 / "chat.jsonl").write_bytes(b"\xff\xfe\x00garbage\x80")
    assert Store.chat_json(d2) == []                          # undecodable file


def test_save_chat_keeps_activity_title(store):
    """§4.4: activity entries persist their stage-label `title` (and the
    outcome/icon/boundary fields) through save_chat's key filter; unknown keys
    are still dropped. The thread lives at the owner's container root (§4.4
    thread lifetime) — here the pending slot."""
    store.save_chat(None, [{"id": "c1", "kind": "activity", "at": "t",
                            "title": "Generating the steps…", "outcome": "done",
                            "text": "Writing 01-check.py…", "junk": "dropped"},
                           {"id": "c2", "kind": "system", "icon": "fa-flag-checkered",
                            "boundary": True, "text": "Draft discarded."}])
    from autowright.storage import Store
    e, m = Store.chat_json(store.chat_dir(None))
    assert e["title"] == "Generating the steps…"
    assert e["outcome"] == "done"
    assert "junk" not in e
    assert m["boundary"] is True and m["icon"] == "fa-flag-checkered"


def test_save_chat_keeps_activity_durations(store):
    """§4.4 per-step durations: an activity entry's `eventDurationsMs` (nulls
    included — a line no stamp bounds) survives save_chat's key filter and the
    chat.jsonl round trip."""
    store.save_chat(None, [{"id": "c1", "kind": "activity", "at": "t",
                            "title": "Updating the documents…", "outcome": "done",
                            "text": "Writing the answer\nWriting the spec\nWriting the notes",
                            "eventDurationsMs": [1400, None, 2200]}])
    from autowright.storage import Store
    (e,) = Store.chat_json(store.chat_dir(None))
    assert e["eventDurationsMs"] == [1400, None, 2200]


def test_activity_chat_entry_without_durations_round_trips(store):
    """§21.4 (2026-08-26) fixture: `eventDurationsMs` is additive - an activity
    entry written to chat.jsonl before it existed loads intact, renders
    without stamps (no key to invent), and re-saves keyless."""
    import json as jsonlib

    from autowright.storage import Store

    old = {"id": "c1", "kind": "activity", "title": "Syncing the workflow…",
           "outcome": "done", "text": "Writing 01-check.py…", "at": "t"}
    container = store.chat_dir(None)
    container.mkdir(parents=True, exist_ok=True)
    (container / "chat.jsonl").write_text(jsonlib.dumps(old) + "\n", encoding="utf-8")

    loaded = Store.chat_json(container)
    assert loaded == [old]
    assert "eventDurationsMs" not in loaded[0]
    # re-saving invents no duration key - no data rewrite exists
    store.save_chat(None, loaded)
    assert Store.chat_json(container) == [old]


def test_default_agent_pointer_self_heals_on_load(home):
    """§4.7: a dangling or absent `default_agent` in agents.yaml falls back to
    the first agent at load; save_agents round-trips the pointer."""
    import yaml

    from autowright import paths
    from autowright.storage import Store
    from autowright.yamlio import save_yaml

    agents = [{"id": "a1", "name": "One", "description": "", "harness": "Claude Code",
               "mode": "default", "model": None},
              {"id": "a2", "name": "Two", "description": "", "harness": "Claude Code",
               "mode": "default", "model": None}]
    save_yaml(paths.agents_file(), {"agents": agents, "default_agent": "ghost"})
    s = Store()
    s.load_all()
    assert s.default_agent_id == "a1"                        # dangling → first

    save_yaml(paths.agents_file(), {"agents": agents})
    s = Store()
    s.load_all()
    assert s.default_agent_id == "a1"                        # missing key → first

    s.default_agent_id = "a2"
    s.save_agents()
    saved = yaml.safe_load(paths.agents_file().read_text(encoding="utf-8"))
    assert saved["default_agent"] == "a2"
    s2 = Store()
    s2.load_all()
    assert s2.default_agent_id == "a2"                       # round-trips

    save_yaml(paths.agents_file(), {"agents": [], "default_agent": "a1"})
    s3 = Store()
    s3.load_all()
    assert s3.default_agent_id is None                       # no agents → None


def test_binary_corrupt_yaml_falls_back_like_bad_yaml(home, caplog):
    """§5: an invalid text encoding is as hand-editable-corrupt as bad YAML —
    defaults plus a warning, never a startup crash."""
    import logging

    from autowright import paths
    from autowright.storage import DEFAULT_SETTINGS, Store

    paths.settings_file().write_bytes(b"\xff\xfe\x00garbage\x80")
    with caplog.at_level(logging.WARNING, logger="autowright.yamlio"):
        s = Store()
        s.load_all()
    assert s.settings == dict(DEFAULT_SETTINGS)
    assert any("unreadable YAML" in rec.message for rec in caplog.records)


def test_terminal_transition_demotes_record_to_header(store):
    """§5 slim-on-finish: on a terminal transition the in-memory record demotes
    to the DB-index header projection; the body stays lazy behind
    execution.yaml and full bodies are never pinned for the backend's life."""
    from autowright import timefmt
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Slim", None)
    h = store.create_execution(a, "version", 1, "discord",
                               [{"name": "Say hello", "file": "01-say.py"}],
                               trigger_payload={"kind": "discord", "sender": "Dave", "text": "hi"})
    assert store.execs[h["id"]] is h and "steps" in h  # live record is the full one
    h["status"] = "succeeded"
    h["finished_at"] = timefmt.now_iso()
    h["duration_ms"] = 1234
    store.update_execution(h)
    r = store.execs[h["id"]]
    assert r is not h
    assert set(r) == set(Store.EXEC_HEADER_KEYS)       # header-only shape
    assert "steps" not in r and "trigger_payload" not in r
    assert r["status"] == "succeeded" and r["trigger_sender"] == "Dave"
    # `_latest` now points at the header — auto_json reads header fields only
    assert a["_latest"] is r
    j = store.auto_json(a)
    assert j["lastStatus"] == "succeeded"
    # list row serializes off the header; the body re-reads from the yaml
    assert store.exec_json(r)["triggerSender"] == "Dave"
    full = store.exec_full(h["id"])
    assert full["steps"][0]["name"] == "Say hello"
    assert store.exec_json(r, full=True)["triggerPayload"]["sender"] == "Dave"


def test_trigger_sender_stamped_once_at_creation(store):
    """§5: trigger_sender is stamped onto the record at creation from the
    trigger payload; every shape (live record, yaml, DB row, reconcile) carries
    the field itself — no reader lifts it from the payload."""
    import sqlite3

    import yaml as pyyaml

    from autowright.storage import Store

    a = store.create_automation(make_version(), "Sender", None)
    h = store.create_execution(a, "version", 1, "imessage", [], status="succeeded",
                               trigger_payload={"kind": "imessage", "sender": "+15551234567"})
    assert h["trigger_sender"] == "+15551234567"
    assert store.create_execution(a, "version", 1, "manual", [],
                                  status="succeeded")["trigger_sender"] is None
    store.update_execution(h)
    y = pyyaml.safe_load((store.exec_dir(h["id"]) / "execution.yaml").read_text())
    assert y["trigger_sender"] == "+15551234567"
    # reconcile path: delete the DB row — the header rebuilt from the yaml
    # carries the stamped field (no payload reach-in)
    db = store.executions_dir() / "executions.db"
    store.close_exec_db()
    conn = sqlite3.connect(db)
    with conn:
        conn.execute("DELETE FROM executions WHERE id=?", (h["id"],))
    conn.close()
    s2 = Store()
    s2.load_all()
    assert s2.execs[h["id"]]["trigger_sender"] == "+15551234567"
    assert s2.exec_json(s2.execs[h["id"]])["triggerSender"] == "+15551234567"


def test_restore_writes_through_the_version_writer(store):
    """§5: restore rebuilds vN+1 from vX's loaded content through
    _write_version_folder (manifest last as the commit point) — never a tree
    copy — and the content round-trips exactly, step code included."""
    import yaml as pyyaml

    a = store.create_automation(make_version(), "Restorable", None)
    store.save_new_version(a, make_version(description="v2 desc", note="Second"))
    n = store.restore_version(a, 1)
    assert n == 3
    v1 = store.auto_dir(a) / "versions" / "v1"
    v3 = store.auto_dir(a) / "versions" / "v3"
    for f in ("01-say.py", "02-finish.py", "spec.md"):
        assert (v3 / f).read_text() == (v1 / f).read_text()
    m1 = pyyaml.safe_load((v1 / "automation.yaml").read_text())
    m3 = pyyaml.safe_load((v3 / "automation.yaml").read_text())
    assert m3["note"] == "Restored from v1" and m3["when"] != m1["when"]
    assert {k: v for k, v in m3.items() if k not in ("when", "note")} == \
        {k: v for k, v in m1.items() if k not in ("when", "note")}
    assert a["versions"][3]["steps"][0]["code"] == a["versions"][1]["steps"][0]["code"]


def test_log_line_cap_marker_then_silence(store, monkeypatch):
    """§5 line cap: a log file stops at MAX_LOG_LINES appended lines, one final
    sys marker records the truncation, then nothing more lands — for attempt
    files and execution.ndjson alike."""
    import json

    from autowright.storage import Store

    monkeypatch.setattr(Store, "MAX_LOG_LINES", 5)
    a = store.create_automation(make_version(), "Cappy", None)
    h = store.create_execution(a, "version", 1, "manual",
                               [{"name": "Say hello", "file": "01-say.py"}])
    name = store.log_name("01-say.py", 0, 1)
    for i in range(1, 10):
        store.append_log_line(h["id"], name, {"timestamp": "2026-08-08T00:00:00+00:00",
                                              "kind": "out", "sequence": i, "text": f"line {i}"})
    lines = [json.loads(ln) for ln in
             store.log_file(h["id"], name).read_text().splitlines()]
    assert len(lines) == 6                       # 5 content lines + the marker
    assert [ln["text"] for ln in lines[:5]] == [f"line {i}" for i in range(1, 6)]
    marker = lines[-1]
    assert marker["kind"] == "sys" and "truncated" in marker["text"]
    assert marker["sequence"] == 6
    # a restart mid-execution re-seeds the count from disk — still capped, and
    # no second marker ever lands
    store._log_counts.clear()
    store.append_log_line(h["id"], name, {"timestamp": "2026-08-08T00:00:01+00:00",
                                          "kind": "out", "sequence": 11, "text": "late"})
    assert len(store.log_file(h["id"], name).read_text().splitlines()) == 6
    # execution.ndjson has the same cap
    for i in range(1, 10):
        store.append_log_line(h["id"], store.EXEC_LOG,
                              {"timestamp": "2026-08-08T00:00:02+00:00",
                               "kind": "sys", "sequence": i, "text": f"e{i}"})
    elines = store.log_file(h["id"], store.EXEC_LOG).read_text().splitlines()
    assert len(elines) == 6
    assert "truncated" in json.loads(elines[-1])["text"]
    # deleting the execution drops the in-memory counters
    store.delete_execution(h["id"])
    assert not any(k[0] == h["id"] for k in store._log_counts)


def test_atomic_write_failure_unlinks_tmp_and_keeps_original(tmp_path):
    """§5 atomic IO: a write that dies mid-stream (here: text the utf-8 codec
    refuses) re-raises, removes its temp file, and never touches the existing
    file — a crash can't leave a half-written or truncated target."""
    import pytest

    from autowright.yamlio import atomic_write_text

    target = tmp_path / "settings.yaml"
    atomic_write_text(target, "keep: me\n")
    with pytest.raises(UnicodeEncodeError):
        atomic_write_text(target, "broken \ud800 surrogate")
    assert target.read_text(encoding="utf-8") == "keep: me\n"  # original intact
    assert not list(tmp_path.glob(".ad-tmp-*"))               # temp cleaned up


def test_problems_audit_kinds_order_and_precedence(store):
    """§4.1 `problems`: mirrors the §7 pre-step gates from stored facts only —
    at most one entry per record (missing > ungranted > unset), kinds in
    serialized order, names sorted within a kind."""
    from autowright.storage import new_id

    sid_ok, sid_unset, sid_ungranted, sid_both = new_id(), new_id(), new_id(), new_id()
    sid_gone, trig_unset = new_id(), new_id()
    store.secrets = [
        {"id": sid_ok, "name": "OK_ONE", "description": "", "set": True},
        {"id": sid_unset, "name": "EMPTY_ONE", "description": "", "set": False},
        {"id": sid_ungranted, "name": "LOCKED_ONE", "description": "", "set": True},
        # ungranted AND unset — precedence keeps only the ungranted row
        {"id": sid_both, "name": "BOTH_BAD", "description": "", "set": False},
        {"id": trig_unset, "name": "BOT_TOKEN", "description": "", "set": False},
    ]
    aid_ok, aid_off, aid_gone = new_id(), new_id(), new_id()
    store.agents = [
        {"id": aid_ok, "name": "Researcher", "description": "",
         "harness": "Claude Code", "mode": "default", "model": None},
        {"id": aid_off, "name": None, "description": "",
         "harness": "OpenCode", "mode": "default", "model": None},
    ]
    ver = make_version(packages=[{"pip": "zzz-nonexistent-dist", "import": "zzz", "why": "w"},
                                 {"pip": "aaa-nonexistent-dist", "import": "aaa", "why": "w"}])
    # manifest entries and code subscripts both count (§4.1 effective refs)
    ver["steps"][0]["secrets"] = [{"id": sid_ok, "why": "w"}, {"id": sid_unset, "why": "w"},
                                  {"id": sid_ungranted, "why": "w"}, {"id": sid_both, "why": "w"}]
    ver["steps"][0]["code"] += f'x = secrets["{sid_gone}"]  # GONE\n'
    ver["steps"][1]["agent"] = True
    ver["steps"][1]["why"] = "judgment"
    ver["steps"][1]["agents"] = [{"id": aid_ok}, {"id": aid_off, "why": "w"}]
    ver["steps"][1]["code"] += f'y = agents["{aid_gone}"]\n'
    a = store.create_automation(
        ver, "Audit me", aid_ok,
        triggers=[{"id": new_id(), "kind": "discord", "enabled": False,
                   "channel": "42", "secret": trig_unset}],
        enabled_agents=[aid_ok],
        allowed_secrets=[sid_ok, sid_unset])
    # An origin OS that differs from the host, whichever the host is — the
    # §4.1 mismatch must fire on macOS, Windows, and Linux runs alike.
    origin = "windows" if paths.current_os() != "windows" else "macos"
    a["origin_os"] = origin
    probs = store.auto_json(a)["problems"]
    assert [(p["kind"], p["label"]) for p in probs] == [
        ("secret-missing", "A step references a deleted secret."),
        ("secret-ungranted", "Secret BOTH_BAD isn't allowed for this automation yet — "
                             "grant it on the edit page."),
        ("secret-ungranted", "Secret LOCKED_ONE isn't allowed for this automation yet — "
                             "grant it on the edit page."),
        ("secret-unset", "Secret BOT_TOKEN has no value yet — add it on the Secrets page."),
        ("secret-unset", "Secret EMPTY_ONE has no value yet — add it on the Secrets page."),
        ("agent-missing", "A step references a deleted agent."),
        # §8 grant name: an unnamed agent shows its harness name
        ("agent-ungranted", "Agent OpenCode isn't enabled for this automation yet — "
                            "enable it on the edit page."),
        ("package-missing", "Package aaa-nonexistent-dist isn't installed yet — "
                            "it installs on the first execution."),
        ("package-missing", "Package zzz-nonexistent-dist isn't installed yet — "
                            "it installs on the first execution."),
        ("os-mismatch", f"Built on {paths.os_display_name(origin)} — its steps may need "
                        f"rewriting before they run on this {paths.machine_noun()}."),
    ]


def test_problems_empty_when_everything_is_wired(store):
    """A fully wired automation audits clean — and discord trigger secrets are
    checked for value, not for grants (§4.3: triggers aren't grant-gated)."""
    from autowright.storage import new_id

    sid = new_id()
    store.secrets = [{"id": sid, "name": "TOKEN_ONE", "description": "", "set": True}]
    ver = make_version()
    ver["steps"][0]["secrets"] = [{"id": sid, "why": "w"}]
    a = store.create_automation(
        ver, "Clean", None,
        triggers=[{"id": new_id(), "kind": "discord", "enabled": False,
                   "channel": "42", "secret": sid}],
        allowed_secrets=[sid])
    assert store.auto_json(a)["problems"] == []


def test_execution_serializers_are_lenient_about_timestamps(store):
    """§5 lenient serialization: hand-damaged execution timestamps drop their
    labels instead of 500ing every /state."""
    a = store.create_automation(make_version(), "Damaged Clock", None)
    h = store.create_execution(a, "version", a["current_version"], "manual",
                               steps=[{"name": "One"}], status="queued")
    h["queued_at"] = "not-a-timestamp"
    h["started_at"] = "also-broken"
    h["finished_at"] = "still-broken"
    h["steps"][0]["attempts"] = [{"number": 1, "status": "failed",
                                  "started_at": "nope", "duration_ms": None}]
    j = store.exec_json(h, full=True)
    assert (j["started"], j["startedMs"], j["endedMs"], j["queuedMs"]) == ("", 0, 0, 0)
    assert j["steps"][0]["attempts"][0]["startedMs"] == 0

    # the automation payload rides the same damaged record: `latest` still
    # serializes, just without the "from <when>" label
    h["status"] = "succeeded"
    h["chip"] = "All good"
    a["_last_exec_at"] = "not-a-timestamp"
    aj = store.auto_json(a)
    assert aj["lastExecutionLabel"] == ""
    assert aj["latest"]["chip"] == "All good" and aj["latest"]["when"] == ""


# ---------- damage tolerance & crash recovery: loads, reconcile, swaps ----------


def test_restore_survives_unnamed_prune(store):
    """Restoring the oldest of 5 unnamed snapshots must not prune the restore
    source mid-restore: the pre-restore snapshot taken inside restore is the
    6th unnamed, so the §6.3 prune targets exactly the snapshot being restored
    (it used to rmtree the target, then crash after wiping live memory)."""
    a = store.create_automation(make_version(), "Pruney", None)
    _write_memory(store, a, text="items: [0]\n")
    oldest = store.snapshot_memory(a, "manual")  # unnamed
    for i in range(1, 5):
        _write_memory(store, a, text=f"items: [{i}]\n")
        store.snapshot_memory(a, "manual")
    _write_memory(store, a, text="items: [99]\n")

    meta = store.restore_snapshot(a, oldest["id"])
    assert meta is not None and meta["id"] == oldest["id"]
    mem = store.auto_dir(a) / "memory" / "seen.yaml"
    assert mem.read_text() == "items: [0]\n"
    # §6.3: restore is repeatable — the source snapshot still exists
    assert store.get_snapshot(a, oldest["id"]) is not None
    assert store.restore_snapshot(a, oldest["id"]) is not None


def test_offset_aware_trigger_on_disk_does_not_brick_load(store, home):
    from autowright.storage import Store
    from autowright.yamlio import load_yaml, save_yaml

    a = store.create_automation(make_version(), "Diskey", None)
    y = home / "automations" / a["id"] / "automation.yaml"
    data = load_yaml(y)
    data["triggers"] = [{"id": "t-1", "kind": "time", "at": "2030-01-01T10:00+02:00"}]
    save_yaml(y, data)

    s2 = Store()
    s2.load_all()  # used to raise TypeError out of validate_trigger
    assert s2.autos[a["id"]]["triggers"] == []


def test_step_without_file_does_not_brick_load(store, home):
    from autowright.storage import Store
    from autowright.yamlio import load_yaml, save_yaml

    a = store.create_automation(make_version(), "NoFile", None)
    y = home / "automations" / a["id"] / "versions" / "v1" / "automation.yaml"
    data = load_yaml(y)
    del data["steps"][0]["file"]
    save_yaml(y, data)

    s2 = Store()
    s2.load_all()  # used to raise IsADirectoryError
    steps = s2.autos[a["id"]]["versions"][1]["steps"]
    assert steps[0]["code"] == "" and steps[1]["code"]


def test_reconcile_skips_unparsable_started_at(store, home):
    """§5: a hand-damaged started_at must stay out of the index — it used to
    be restored by the reconcile and 500 the whole executions list (and, via
    _latest_exec, the automations list) on every request."""
    from autowright.storage import Store
    from autowright.yamlio import load_yaml, save_yaml

    a = store.create_automation(make_version(), "Recon", None)
    h = store.create_execution(a, "version", 1, "manual", steps=[])
    h["status"] = "succeeded"
    store.update_execution(h)
    y = store.exec_dir(h["id"]) / "execution.yaml"
    data = load_yaml(y)
    data["started_at"] = "banana"
    save_yaml(y, data)
    store.close_exec_db()
    for suffix in ("", "-wal", "-shm"):
        (store.executions_dir() / ("executions.db" + suffix)).unlink(missing_ok=True)

    s2 = Store()
    s2.load_all()
    assert h["id"] not in s2.execs  # left out of the index, not restored broken
    # the serializers that used to 500 still work for the rest of the store
    assert isinstance(s2.auto_json(s2.autos[a["id"]]), dict)


def test_retention_never_deletes_queued(store, monkeypatch):
    """§5/§6: queued records ARE the firing queue — retention used to delete
    one older than the cutoff, silently dropping a waiting firing."""
    from datetime import datetime, timedelta, timezone

    a = store.create_automation(make_version(), "Queuey", None)
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    q = store.create_execution(a, "version", 1, "discord", steps=[], status="queued")
    q["queued_at"] = q["started_at"] = old
    store.update_execution(q)
    done = store.create_execution(a, "version", 1, "manual", steps=[])
    done["status"] = "succeeded"
    done["started_at"] = old
    store.update_execution(done)
    store.settings["days"] = 7

    deleted = store.retention_cleanup()
    assert deleted == 1
    assert q["id"] in store.execs          # the queued firing survives
    assert done["id"] not in store.execs   # ordinary old records still go


def test_damaged_snapshot_yaml_never_500s_detail(store):
    """§5: a hand-damaged snapshot.yaml is skipped, never fatal to the
    automation detail that serializes it."""
    a = store.create_automation(make_version(), "Snappy", None)
    d = store.auto_dir(a) / "memory"
    d.mkdir(parents=True, exist_ok=True)
    (d / "seen.yaml").write_text("x: 1\n")
    good = store.snapshot_memory(a, "manual")
    bad = store.snapshot_memory(a, "manual")
    (store.snapshots_dir(a) / bad["id"] / "snapshot.yaml").write_text("just a string\n")

    metas = store.list_snapshots(a)
    assert [m["id"] for m in metas] == [good["id"]]
    assert store.get_snapshot(a, bad["id"]) is None
    assert isinstance(store.auto_json(a), dict)  # used to AttributeError/KeyError


def test_load_yaml_survives_permission_error(home, monkeypatch):
    """§5: an unreadable top-level yaml (permissions) must fall back to the
    default, not crash startup into a launchd crash loop."""
    import builtins

    from autowright.yamlio import load_yaml

    real_open = builtins.open

    def deny(path, *a, **kw):
        if str(path).endswith("settings.yaml"):
            raise PermissionError(13, "Permission denied", str(path))
        return real_open(path, *a, **kw)

    monkeypatch.setattr(builtins, "open", deny)
    assert load_yaml(home / "settings.yaml", {"d": 1}) == {"d": 1}


def test_save_draft_swap_recovers_from_crash(store):
    """§5 staged-dir swap: a save crashing between the two renames leaves the
    old copy renamed aside — loads and the next save put it back, so a draft
    is never a mix of old manifest and new step files."""
    a = store.create_automation(make_version(), "Swappy", None)
    store.save_draft(a, {**make_version(), "note": "draft v1"})
    container = store.draft_dir(a)

    # crash point: old renamed aside, new never renamed in
    (container / "automation").rename(container / ".ad-old-automation")
    from autowright.storage import Store
    s2 = Store()
    s2.load_all()
    recovered = s2.autos[a["id"]]["draft"]
    assert recovered is not None and recovered["note"] == "draft v1"
    assert not (container / ".ad-old-automation").exists()

    # crash point: staged copy written, swap never started — stale temp goes
    (container / ".ad-new-automation").mkdir()
    store.save_draft(a, {**make_version(), "note": "draft v2"})
    assert a["draft"]["note"] == "draft v2"
    assert not (container / ".ad-new-automation").exists()
    assert not (container / ".ad-old-automation").exists()


def test_pending_draft_swap_recovers_from_crash(store):
    """Same swap recovery for the create-mode pending slot."""
    from autowright import paths

    store.save_draft(None, {**make_version(), "note": "pending v1"}, name="Pendy")
    slot = paths.pending_draft_dir()
    (slot / "automation").rename(slot / ".ad-old-automation")
    d = store.load_pending_draft()
    assert d is not None and d["note"] == "pending v1" and d["name"] == "Pendy"
    assert not (slot / ".ad-old-automation").exists()


def test_problems_overdue_first_and_labels(store):
    """§4.1 overdue: two missed cron occurrences since the baseline put an
    `overdue` entry first; a recent real run clears it."""
    from autowright import timefmt

    trig = {"id": "t1", "kind": "cron", "enabled": True,
            "expression": "0 8 * * *", "source": "user"}
    a = store.create_automation(make_version(), "Sleepy", None, triggers=[trig])
    cur = a["versions"][a["current_version"]]
    # freshly created — nothing missed yet, no entry
    assert store.problems_json(a, cur) == []
    # never ran, created long ago (and enabled back then) → overdue, never-run label
    a["created_at"] = "2020-01-01T08:00:00"
    a["triggers"][0]["enabledAt"] = "2020-01-01T08:00:00+00:00"
    probs = store.problems_json(a, cur)
    assert probs[0] == {"kind": "overdue", "label":
                        "Scheduled executions are being missed — it has never run."}
    # ran long ago → overdue with the last-ran date label
    a["_last_exec_at"] = "2020-01-05T09:00:00"
    p0 = store.problems_json(a, cur)[0]
    assert p0["kind"] == "overdue"
    assert p0["label"].startswith("Scheduled executions are being missed — it last ran ")
    # a recent real run clears it
    a["_last_exec_at"] = timefmt.now_iso()
    assert all(p["kind"] != "overdue" for p in store.problems_json(a, cur))
    # disabled trigger → never overdue, however old the baseline
    a["_last_exec_at"] = "2020-01-05T09:00:00"
    a["triggers"][0]["enabled"] = False
    assert all(p["kind"] != "overdue" for p in store.problems_json(a, cur))


def test_overdue_ignores_occurrences_that_passed_while_a_trigger_was_off(store):
    """§4.1/§4.3: overdue counts from the enable stamp, so the moments that
    passed while a cron sat off are ignored when it comes back on — a
    re-enable after a week away must not fire the audit the instant it lands.
    Genuine misses after the stamp still flag."""
    from datetime import datetime, timedelta

    trig = {"id": "t1", "kind": "cron", "enabled": False,
            "expression": "0 8 * * *", "source": "user"}
    a = store.create_automation(make_version(), "Napper", None, triggers=[trig])
    a["created_at"] = "2020-01-01T08:00:00"  # old automation that never ran
    assert not store.overdue(a)  # off: nothing to miss
    assert "enabledAt" not in a["triggers"][0]  # a disabled entry is never stamped

    store.patch_automation(a, {"triggers": [{**trig, "enabled": True}]})
    assert a["triggers"][0]["enabledAt"]  # the write path stamped the transition
    assert not store.overdue(a)  # years of 8:00s passed while off — ignored
    # two 8:00s after the stamp with no run → genuinely overdue
    assert store.overdue(a, datetime.now() + timedelta(days=3))


def test_overdue_ignores_a_cron_added_to_an_old_automation(store):
    """§4.1/§4.3: a cron minted enabled is stamped at that moment, so adding
    one to an automation created (or last run) long ago starts the count from
    the add, not from the stale baseline."""
    from datetime import datetime, timedelta

    a = store.create_automation(make_version(), "Ancient", None, triggers=[])
    a["created_at"] = "2020-01-01T08:00:00"
    a["_last_exec_at"] = "2020-01-05T09:00:00"
    store.patch_automation(a, {"triggers": [
        {"id": "new", "kind": "cron", "enabled": True,
         "expression": "0 8 * * *", "source": "user"}]})
    assert not store.overdue(a)
    assert store.overdue(a, datetime.now() + timedelta(days=3))


def test_enable_stamp_is_kept_across_edits_and_never_healed(store):
    """§4.3: an already-on trigger keeps its stamp across an unrelated edit of
    the list (a schedule change is not a re-enable), and a trigger stored
    without one is never healed — it keeps counting from the run baseline."""
    trig = {"id": "t1", "kind": "cron", "enabled": True,
            "expression": "0 8 * * *", "source": "user"}
    a = store.create_automation(make_version(), "Steady", None, triggers=[trig])
    stamp = a["triggers"][0]["enabledAt"]
    store.patch_automation(a, {"triggers": [{**trig, "expression": "0 9 * * *"}]})
    assert a["triggers"][0]["enabledAt"] == stamp  # not re-stamped
    # a client can't set it either — the stored value wins
    store.patch_automation(a, {"triggers": [{**trig, "enabledAt": "2999-01-01T00:00:00+00:00"}]})
    assert a["triggers"][0]["enabledAt"] == stamp

    # legacy: stored without the stamp, edited while on → still no stamp, and
    # overdue keeps counting from the run baseline alone (§4.3 lenient load).
    a["triggers"][0].pop("enabledAt")
    a["created_at"] = "2020-01-01T08:00:00"
    store.patch_automation(a, {"triggers": [dict(trig)]})
    assert "enabledAt" not in a["triggers"][0]
    assert store.overdue(a)


# ---------- §4.1 unresolvedReferences (§5.1 import's no-match map) ----------

def _imported(store, *, secret_in_code=None, secret_in_entry=None, agent_in_entry=None,
              trigger_secret=None, unresolved=None, name="Imported"):
    """An automation shaped like a §5.1 import landing: minted ids that match
    no stored record, carried by the stored unresolved_references map."""
    from autowright.storage import new_id

    ver = make_version()
    if secret_in_code:
        ver["steps"][0]["code"] += f'x = secrets["{secret_in_code}"]  # WANTED\n'
    if secret_in_entry:
        ver["steps"][0]["secrets"] = [{"id": secret_in_entry, "why": "w"}]
    if agent_in_entry:
        ver["steps"][1]["agent"] = True
        ver["steps"][1]["why"] = "judgment"
        ver["steps"][1]["agents"] = [{"id": agent_in_entry}]
    triggers = ([{"id": new_id(), "kind": "discord", "enabled": False,
                  "channel": "42", "secret": trigger_secret}] if trigger_secret else [])
    return store.create_automation(ver, name, None, triggers=triggers,
                                   unresolved_references=unresolved)


def test_unresolved_references_load_is_lenient(store, home):
    """§4.1/§5: entries that aren't a uuid-keyed mapping with a valid kind and
    a string name are dropped with a warning on load, never fatal."""
    from autowright.storage import Store, load_yaml, new_id, save_yaml

    good, odd_desc = new_id(), new_id()
    a = _imported(store, secret_in_code=good,
                  unresolved={good: {"kind": "secret", "name": "MAIL_PASS",
                                     "description": "mail"}})
    top_file = store.auto_dir(a) / "automation.yaml"
    top = load_yaml(top_file)
    top["unresolved_references"] = {
        good: {"kind": "secret", "name": "MAIL_PASS", "description": "mail"},
        "not-a-uuid": {"kind": "secret", "name": "NOPE"},
        new_id(): {"kind": "bogus", "name": "NOPE"},          # unknown kind
        new_id(): {"kind": "agent", "name": ""},              # empty name
        new_id(): {"kind": "agent", "name": 7},               # non-string name
        new_id(): "just a string",                            # not a mapping
        # kind + name are the structural rules; a non-string description is
        # normalized rather than dropped
        odd_desc: {"kind": "secret", "name": "ODD", "description": 5},
    }
    save_yaml(top_file, top)

    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    assert b["unresolved_references"] == {
        good: {"kind": "secret", "name": "MAIL_PASS", "description": "mail"},
        odd_desc: {"kind": "secret", "name": "ODD", "description": ""}}
    # a whole map that isn't a mapping degrades to empty, not a lost automation
    top["unresolved_references"] = ["MAIL_PASS"]
    save_yaml(top_file, top)
    s3 = Store()
    s3.load_all()
    assert "unresolved_references" not in s3.autos[a["id"]]


def test_save_new_version_prunes_unresolved_references(store):
    """§4.1: an edit save prunes entries the new version no longer references -
    a fixed reference stops carrying its label while the rest keep theirs."""
    from autowright.storage import load_yaml, new_id

    sid, aid = new_id(), new_id()
    a = _imported(store, secret_in_code=sid, agent_in_entry=aid,
                  unresolved={sid: {"kind": "secret", "name": "MAIL_PASS", "description": ""},
                              aid: {"kind": "agent", "name": "Ghost", "description": ""}})
    assert set(a["unresolved_references"]) == {sid, aid}

    # v2 drops the secret subscript, keeps the agent entry
    ver2 = {k: v for k, v in a["versions"][1].items() if k != "when"}
    ver2["steps"] = [dict(s) for s in ver2["steps"]]
    ver2["steps"][0]["code"] = "from autowright import log\nlog('clean')\n"
    store.save_new_version(a, ver2)
    assert list(a["unresolved_references"]) == [aid]
    assert list(load_yaml(store.auto_dir(a) / "automation.yaml")
                ["unresolved_references"]) == [aid]

    # v3 drops the agent too → the key leaves automation.yaml entirely
    ver3 = {k: v for k, v in a["versions"][a["current_version"]].items() if k != "when"}
    ver3["steps"] = [dict(s) for s in ver3["steps"]]
    ver3["steps"][1].pop("agents")
    store.save_new_version(a, ver3)
    assert "unresolved_references" not in a
    assert "unresolved_references" not in load_yaml(store.auto_dir(a) / "automation.yaml")


def test_restore_version_keeps_unresolved_references(store):
    """§4.1: a restore is not a rework (the `originOs` rule) - the map rides
    through untouched even when the restored version references nothing."""
    from autowright.storage import new_id

    sid = new_id()
    a = _imported(store, secret_in_code=sid,
                  unresolved={sid: {"kind": "secret", "name": "MAIL_PASS", "description": ""}})
    clean = {k: v for k, v in a["versions"][1].items() if k != "when"}
    clean["steps"] = [dict(s) for s in clean["steps"]]
    clean["steps"][0]["code"] = "from autowright import log\nlog('clean')\n"
    store.save_new_version(a, clean)
    assert "unresolved_references" not in a          # the save pruned it
    a["unresolved_references"] = {sid: {"kind": "secret", "name": "MAIL_PASS",
                                        "description": ""}}
    store.restore_version(a, 1)
    assert a["unresolved_references"] == {sid: {"kind": "secret", "name": "MAIL_PASS",
                                                "description": ""}}


def test_trigger_replace_prunes_unresolved_references(store):
    """§4.1: a trigger replace prunes the entries a dropped discord trigger was
    the last holder of."""
    from autowright.storage import new_id

    sid, other = new_id(), new_id()
    a = _imported(store, secret_in_code=other, trigger_secret=sid,
                  unresolved={sid: {"kind": "secret", "name": "BOT_TOKEN", "description": ""},
                              other: {"kind": "secret", "name": "MAIL_PASS", "description": ""}})
    assert set(a["unresolved_references"]) == {sid, other}
    store.patch_automation(a, {"triggers": []})
    assert list(a["unresolved_references"]) == [other]


def test_auto_json_unresolved_references_always_present_and_filtered(store):
    """§4.1: serialized as `unresolvedReferences` - {} when none, and filtered
    to ids the current version (or a discord trigger) still references."""
    from autowright.storage import new_id

    plain = store.create_automation(make_version(), "Plain", None)
    assert store.auto_json(plain)["unresolvedReferences"] == {}

    sid, stale = new_id(), new_id()
    a = _imported(store, secret_in_code=sid,
                  unresolved={sid: {"kind": "secret", "name": "MAIL_PASS", "description": "mail"},
                              stale: {"kind": "agent", "name": "Ghost", "description": ""}})
    assert store.auto_json(a)["unresolvedReferences"] == {
        sid: {"kind": "secret", "name": "MAIL_PASS", "description": "mail"}}
    # the stored map is untouched by the filter - only the serialization narrows
    assert set(a["unresolved_references"]) == {sid, stale}


def test_problems_unresolved_kinds_precedence_and_order(store):
    """§4.1: `secret-unresolved` / `agent-unresolved` name the archive record,
    take the missing slot (mutually exclusive per id), carry the trigger copy
    for a trigger's token secret, and serialize before the missing rows."""
    from autowright.storage import new_id

    step_sid, trig_sid, gone_sid = new_id(), new_id(), new_id()
    agent_id, gone_agent = new_id(), new_id()
    ver = make_version()
    ver["steps"][0]["code"] += (f'x = secrets["{step_sid}"]\n'
                                f'y = secrets["{gone_sid}"]\n')
    ver["steps"][1]["agent"] = True
    ver["steps"][1]["why"] = "judgment"
    ver["steps"][1]["agents"] = [{"id": agent_id}, {"id": gone_agent}]
    a = store.create_automation(
        ver, "Needs fixing", None,
        triggers=[{"id": new_id(), "kind": "discord", "enabled": False,
                   "channel": "42", "secret": trig_sid}],
        unresolved_references={
            step_sid: {"kind": "secret", "name": "MAIL_PASS", "description": ""},
            trig_sid: {"kind": "secret", "name": "BOT_TOKEN", "description": ""},
            agent_id: {"kind": "agent", "name": "Ghost", "description": ""},
            # kind mismatch: this id is referenced as an agent, so the entry
            # doesn't claim it - it stays the ordinary missing row
            gone_agent: {"kind": "secret", "name": "WRONG_KIND", "description": ""}})
    noun = paths.machine_noun()
    assert [(p["kind"], p["label"]) for p in store.auto_json(a)["problems"]] == [
        ("secret-unresolved", f"Imported secret MAIL_PASS has no match on this {noun}. "
                              "Pick one of your secrets on the edit page."),
        ("secret-unresolved", f"A trigger needs the imported secret BOT_TOKEN, "
                              f"which has no match on this {noun}."),
        ("secret-missing", "A step references a deleted secret."),
        ("agent-unresolved", f"Imported agent Ghost has no match on this {noun}. "
                             "Choose one of your agents on the edit page."),
        ("agent-missing", "A step references a deleted agent."),
    ]


def test_automation_yaml_without_unresolved_references_loads(store, home):
    """§21.4 (2026-08-24) fixture: `unresolved_references` is additive - an
    automation.yaml written before the key existed loads as an empty map,
    serializes as {}, and stays keyless through later top-level writes."""
    from autowright.storage import Store, load_yaml, save_yaml

    a = store.create_automation(make_version(), "Pre-change", None)
    top_file = store.auto_dir(a) / "automation.yaml"
    top = load_yaml(top_file)
    top.pop("unresolved_references", None)          # the pre-change shape
    save_yaml(top_file, top)
    assert "unresolved_references" not in load_yaml(top_file)

    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    assert "unresolved_references" not in b
    assert s2.auto_json(b)["unresolvedReferences"] == {}
    # a later top-level write keeps the key absent - no data rewrite exists
    s2.patch_automation(b, {"name": "Pre-change renamed"})
    assert "unresolved_references" not in load_yaml(top_file)


def test_trigger_without_run_if_missed_loads_true(store, home):
    """§21.4 (2026-08-30) fixture: `runIfMissed` is additive - a cron written
    before the key existed loads without it, serializes as true, and stays
    keyless through later top-level writes."""
    from autowright.storage import Store, load_yaml

    trig = {"id": "t-1", "kind": "cron", "enabled": True, "expression": "0 8 * * *",
            "source": "user"}
    a = store.create_automation(make_version(), "Pre-field", None, triggers=[trig])
    top_file = store.auto_dir(a) / "automation.yaml"
    assert "runIfMissed" not in load_yaml(top_file)["triggers"][0]  # the pre-change shape

    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    assert "runIfMissed" not in b["triggers"][0]
    assert s2.trigger_json(b["triggers"][0])["runIfMissed"] is True
    # a later top-level write keeps the key absent - no data rewrite exists
    s2.patch_automation(b, {"name": "Pre-field renamed"})
    assert "runIfMissed" not in load_yaml(top_file)["triggers"][0]


def test_trigger_run_if_missed_false_round_trips(store, home, caplog):
    """§4.3: `runIfMissed` loads only when the stored value is exactly false -
    a true on disk reads as the absent default - and `trigger_json` serializes
    it explicitly either way. A non-boolean is malformed trigger data: the
    trigger drops with a warning (§5 lenient load), never the automation."""
    import logging

    from autowright.storage import Store, load_yaml, save_yaml

    trig = {"id": "t-1", "kind": "cron", "enabled": True, "expression": "0 8 * * *",
            "source": "user", "runIfMissed": False}
    a = store.create_automation(make_version(), "No catch-up", None, triggers=[trig])
    top_file = store.auto_dir(a) / "automation.yaml"

    s2 = Store()
    s2.load_all()
    loaded = s2.autos[a["id"]]["triggers"][0]
    assert loaded["runIfMissed"] is False
    assert s2.trigger_json(loaded)["runIfMissed"] is False

    def _rewrite(value):
        top = load_yaml(top_file)
        top["triggers"][0]["runIfMissed"] = value
        save_yaml(top_file, top)

    _rewrite(True)  # stored only when false: a true on disk loads keyless
    s3 = Store()
    s3.load_all()
    assert "runIfMissed" not in s3.autos[a["id"]]["triggers"][0]
    assert s3.trigger_json(s3.autos[a["id"]]["triggers"][0])["runIfMissed"] is True

    _rewrite("no")
    with caplog.at_level(logging.WARNING, logger="autowright.storage"):
        s4 = Store()
        s4.load_all()
    assert s4.autos[a["id"]]["triggers"] == []
    assert any("malformed trigger" in rec.message for rec in caplog.records)


def test_draft_test_summary_without_fingerprint_loads(store):
    """§21 (2026-09-03): a test.yaml written before `steps_fingerprint` existed is the
    old shape — it loads with `stepsFingerprint: null`; a summary carrying the key echoes
    it verbatim (§19: opaque to the backend)."""
    from autowright.yamlio import save_yaml
    a = store.create_automation(make_version(), "Fp", None)
    container = store.auto_dir(a) / "draft"
    container.mkdir(parents=True, exist_ok=True)
    save_yaml(container / "test.yaml", {
        "status": "succeeded", "when": "2026-09-01T10:00:00.000000+00:00", "execution_id": "e1",
    })
    j = store.draft_test_json(container)
    assert j["status"] == "succeeded" and j["executionId"] == "e1"
    assert j["stepsFingerprint"] is None
    save_yaml(container / "test.yaml", {
        "status": "failed", "when": "2026-09-01T10:00:00.000000+00:00", "execution_id": "e2",
        "steps_fingerprint": "2:deadbeef",
    })
    assert store.draft_test_json(container)["stepsFingerprint"] == "2:deadbeef"


# ---------- §5 hand-edited execution.yaml / delete-aside / data-path guards ----------

def test_exec_yaml_with_unquoted_timestamps_loads(store, home):
    """§5: an UNQUOTED timestamp in a hand-edited execution.yaml parses as a
    YAML datetime, and one datetime among the string-typed timestamp fields
    made `max` over started_at raise — bricking the automations list."""
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Hand Edited", None)
    old = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    store.update_execution(old)
    hand = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    store.update_execution(hand)
    (store.exec_dir(hand["id"]) / "execution.yaml").write_text(
        f"id: {hand['id']}\n"
        f"automation_id: {a['id']}\n"
        "automation_name: Hand Edited\n"
        "kind: version\nversion: 1\nstatus: succeeded\ntrigger: manual\n"
        "started_at: 2026-09-05T12:00:00+00:00\n"   # unquoted → a YAML datetime
        "finished_at: 2026-09-05T12:00:05+00:00\n"
        "duration_ms: 5000\n", encoding="utf-8")

    s2 = Store()
    s2.load_all()
    y = s2.read_exec_yaml(hand["id"])
    assert isinstance(y["id"], str) and isinstance(y["automation_id"], str)
    assert y["started_at"] == "2026-09-05 12:00:00+00:00"
    assert y["finished_at"] == "2026-09-05 12:00:05+00:00"

    # the crash path: the full record (yaml-merged) lands back in the index,
    # and _latest_exec then compares it against the string-timestamped rows
    s2.execs[hand["id"]] = s2.exec_full(hand["id"])
    assert s2._latest_exec(a["id"])["id"] in (old["id"], hand["id"])


def test_retention_skips_an_unreadable_settings_file(home):
    """§5 read-only degradation: an unreadable settings.yaml loaded as the
    DEFAULTS, and the default is a 90-day sweep — running it would delete
    executions the user's real settings keep forever."""
    from datetime import datetime, timedelta

    from autowright import paths
    from autowright.storage import Store

    paths.settings_file().write_text("{{{:::\nnot: [valid", encoding="utf-8")
    s = Store()
    s.load_all()
    assert str(paths.settings_file()) in s._unreadable

    a = s.create_automation(make_version(), "Kept", None)
    h = s.create_execution(a, "version", 1, "manual", [], status="succeeded")
    h["started_at"] = (datetime.now() - timedelta(days=400)).isoformat(timespec="seconds")
    s.update_execution(h)

    assert s.retention_cleanup() == 0
    assert h["id"] in s.execs and s.exec_dir(h["id"]).is_dir()


def test_delete_execution_leaves_no_aside_directory(store, home):
    # §6 retention: the dir is renamed aside under the lock and rmtree'd after
    # it — by the time the call returns nothing is left of either.
    a = store.create_automation(make_version(), "Deleter", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    store.update_execution(h)
    store.append_log_line(h["id"], store.EXEC_LOG,
                          {"timestamp": "2026-09-05T10:00:00+00:00", "kind": "out",
                           "sequence": 1, "text": "hi"})
    assert (h["id"], store.EXEC_LOG) in store._log_counts

    store.delete_execution(h["id"])
    assert not store.exec_dir(h["id"]).exists()
    assert list(store.executions_dir().glob(f"{store.DELETED_PREFIX}*")) == []
    assert h["id"] not in store.execs
    assert not any(k[0] == h["id"] for k in store._log_counts)


def test_delete_automation_leaves_no_aside_directory(store, home):
    a = store.create_automation(make_version(), "Goner", None)
    d = store.auto_dir(a)
    (d / "memory" / "seen.yaml").write_text("v: 1\n")

    store.delete_automation(a)
    assert not d.exists()
    assert list(paths.automations_dir().glob(f"{store.DELETED_PREFIX}*")) == []
    assert a["id"] not in store.autos


def test_leftover_deleted_directories_are_swept_at_load(store, home):
    """§5/§6: a crash between the aside-rename and the rmtree leaves the dir
    behind — the next load finishes the delete instead of keeping dead bytes."""
    from autowright.storage import Store

    exec_leftover = store.executions_dir() / f"{store.DELETED_PREFIX}x"
    exec_leftover.mkdir(parents=True)
    (exec_leftover / "execution.yaml").write_text("id: x\n", encoding="utf-8")
    auto_leftover = paths.automations_dir() / f"{store.DELETED_PREFIX}y"
    auto_leftover.mkdir(parents=True)
    (auto_leftover / "automation.yaml").write_text("id: y\n", encoding="utf-8")

    s2 = Store()
    s2.load_all()
    assert not exec_leftover.exists() and not auto_leftover.exists()
    assert "y" not in s2.autos and "x" not in s2.execs


def test_unreachable_relocated_data_path_is_never_created(home, tmp_path):
    """§5: only the DEFAULT data path is created on first run. A relocated
    dataPath whose executions dir isn't there is an unmounted volume — the
    tree must not be recreated on the boot disk, hiding the real one."""
    from autowright import paths
    from autowright.storage import Store
    from autowright.yamlio import save_yaml

    gone = tmp_path / "unmounted" / "Autowright"
    save_yaml(paths.settings_file(), {"dataPath": str(gone)})
    s = Store()
    s.load_all()

    assert not gone.exists()
    assert s.execs == {}
    # the in-memory index still works for the session
    a = s.create_automation(make_version(), "Homeless", None)
    assert s.create_execution(a, "version", 1, "manual", [])["id"] in s.execs


def test_default_data_path_is_still_created_on_first_run(home):
    from autowright.storage import Store

    shutil_rm = home / "executions"
    if shutil_rm.exists():
        import shutil as sh

        sh.rmtree(shutil_rm)
    s = Store()
    s.load_all()
    assert (home / "executions" / "executions.db").exists()


# ---------- §6.3 memory swap recovery + §19 memory_stats memo ----------

def test_memory_swap_aside_is_restored_at_load(store, home):
    """§6.3: a restore that died between the two renames left memory/ absent
    and the aside dir the sole surviving copy — the load puts it back."""
    from autowright.storage import MEMORY_SWAP_OLD, Store

    a = store.create_automation(make_version(), "Crashed Restore", None)
    d = store.auto_dir(a)
    import shutil as sh

    sh.rmtree(d / "memory")
    aside = d / MEMORY_SWAP_OLD
    aside.mkdir()
    (aside / "seen.yaml").write_text("v: survived\n", encoding="utf-8")

    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    assert (s2.auto_dir(b) / "memory" / "seen.yaml").read_text() == "v: survived\n"
    assert not aside.exists()


def test_clear_memory_repairs_a_half_finished_swap_first(store, home):
    """§6.3: without the repair the clear recreates an empty memory/ beside a
    live aside dir, and the NEXT restore's own recovery then rmtrees the only
    copy of the pre-crash memory with nobody asking."""
    from autowright.storage import MEMORY_SWAP_OLD

    a = store.create_automation(make_version(), "Clear After Crash", None)
    d = store.auto_dir(a)
    import shutil as sh

    sh.rmtree(d / "memory")
    aside = d / MEMORY_SWAP_OLD
    aside.mkdir()
    (aside / "seen.yaml").write_text("v: old\n", encoding="utf-8")

    store.clear_memory(a)
    assert (d / "memory").is_dir() and not any((d / "memory").iterdir())
    assert not aside.exists()  # the clear is explicit; nothing lingers to surprise a restore


def test_memory_stats_memo_skips_the_walk_and_clear_invalidates(store, home, monkeypatch):
    # §19 /state cost: the tree walk is memoized per automation with a short
    # TTL, and every wholesale replacement of the dir drops the memo.
    from autowright import storage as storage_mod

    a = store.create_automation(make_version(), "Memoized", None)
    (store.auto_dir(a) / "memory" / "seen.yaml").write_text("v: 1\n", newline="")

    walks = []
    real = storage_mod.iter_file_stats

    def counting(d):
        walks.append(str(d))
        return real(d)

    monkeypatch.setattr(storage_mod, "iter_file_stats", counting)
    first = store.memory_stats(a)
    assert len(walks) == 1 and first["size"] == "5 B"
    assert store.memory_stats(a) == first
    assert len(walks) == 1  # memo hit — no second walk

    store.clear_memory(a)
    assert store.memory_stats(a)["size"] == "empty"
    assert len(walks) == 2

    store.invalidate_memory_stats(a)
    store.memory_stats(a)
    assert len(walks) == 3


def test_latest_result_memo_hit_skips_the_scan(store, home, monkeypatch):
    # §19 /state cost: the memo key comes from one linear pass over the
    # headers, so a hit costs neither the sort nor the per-execution
    # result-dir walk — /state pays this per automation under store.lock.
    a = store.create_automation(make_version(), "Results", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    h["chip"] = "All good"
    store.update_execution(h)

    first = store.latest_result_json(a)
    assert first["executionId"] == h["id"] and first["chip"] == "All good"

    scanned = []
    monkeypatch.setattr(store, "result_json", lambda x: scanned.append(x["id"]))
    assert store.latest_result_json(a) == first
    assert scanned == []  # memo hit — the walk never ran

    # a newer terminal record moves the key, so the answer is recomputed
    later = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    store.update_execution(later)
    store.latest_result_json(a)
    assert scanned == [later["id"], h["id"]]


# ---------- §21.4 stored-shape fixtures ----------

def test_version_params_with_value_keys_load_stripped(store, home):
    """§21.4 (2026-09-05): versions written by v0.6.0-v0.8.3 carry resolved
    value keys inside their definitions — stripped at the read seam, and the
    frozen file on disk is never rewritten."""
    from autowright.storage import Store
    from autowright.yamlio import load_yaml, save_yaml

    a = store.create_automation(make_version(), "Old Shape", None)
    vfile = store.auto_dir(a) / "versions" / "v1" / "automation.yaml"
    meta = load_yaml(vfile)
    meta["params"] = [
        {"name": "greeting", "kind": "text", "label": "Greeting", "help": "",
         "default": "hello", "value": "bonjour"},
        {"name": "loud", "kind": "toggle", "label": "Loud", "help": "",
         "default": False, "on": True},
        {"name": "names", "kind": "list", "label": "Names", "help": "",
         "default": [], "lines": ["ana"]},
        {"name": "rows", "kind": "table", "label": "Rows", "help": "",
         "default": [], "rows": [{"a": 1}]},
    ]
    save_yaml(vfile, meta)
    before = vfile.read_text(encoding="utf-8")

    s2 = Store()
    s2.load_all()
    params = s2.autos[a["id"]]["versions"][1]["params"]
    assert [p["name"] for p in params] == ["greeting", "loud", "names", "rows"]
    for p in params:
        assert not {"value", "on", "lines", "rows"} & set(p)
        assert {"name", "kind", "label", "help", "default"} <= set(p)
    # a read-seam strip, never a rewrite: the frozen version is untouched
    assert vfile.read_text(encoding="utf-8") == before


def test_exec_yaml_without_agent_pgids_loads(store, home):
    """§21.4: records written before the §4.5 agentPgids key existed load with
    an empty list, and the key stays sparse — absent whenever there are none."""
    from autowright.yamlio import load_yaml

    a = store.create_automation(make_version(), "No Pgids", None)
    h = store.create_execution(a, "version", 1, "manual",
                               [{"name": "Say hello", "file": "01-say.py",
                                 "status": "succeeded", "attempts": []}])
    store.update_execution(h)
    yfile = store.exec_yaml_path(h["id"])
    meta = load_yaml(yfile)
    assert "agent_pgids" not in meta  # sparse from the start

    meta.pop("agent_pgids", None)
    from autowright.yamlio import save_yaml

    save_yaml(yfile, meta)
    assert store.read_exec_yaml(h["id"])["agent_pgids"] == []

    # a later write with no live groups keeps the key absent
    full = store.exec_full(h["id"])
    full["agent_pgids"] = []
    full["status"] = "succeeded"
    store.update_execution(full)
    assert "agent_pgids" not in load_yaml(yfile)


def test_exec_json_carries_duration_ms_and_pass_start(store):
    """§4.5 durationMs / passStartedMs: the raw ms behind `duration` and the live
    pass's clock — what the §7 LOGS-rail total timer ticks from. The pass
    marker is in-memory only and reads 0 on anything not executing."""
    import yaml
    a = store.create_automation(make_version(), "Timed", None)
    h = store.create_execution(a, "version", a["current_version"], "manual",
                               steps=[{"name": "One"}], status="executing")
    j = store.exec_json(h)
    assert (j["durationMs"], j["passStartedMs"]) == (None, 0)  # no pass stamped yet
    h["_pass_start"] = 1_700_000_000.25
    h["duration_ms"] = 60_000  # a failed first pass, being retried
    j = store.exec_json(h)
    assert (j["durationMs"], j["passStartedMs"]) == (60_000, 1_700_000_000_250)
    # the stamp never reaches disk, and a settled record has no live pass
    store.write_exec_yaml(h)
    assert "_pass_start" not in yaml.safe_load(store.exec_yaml_path(h["id"]).read_text())
    h["status"] = "succeeded"
    assert store.exec_json(h, full=True)["passStartedMs"] == 0
