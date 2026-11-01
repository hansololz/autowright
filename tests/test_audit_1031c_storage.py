"""2026-10-31 third sweep (storage): the retention session clock, `kv`
param-value rows coerced at load, trigger timezone / `at` canonicalized at
load, lone surrogates replaced at the YAML read seam, atomic writes keeping
the file's mode and its symlink, memory save temp files skipped, delete
dropping the in-memory memos, the file-safe name strip, a failed create
leaving no folder, and a UTF-8 BOM stripped from every store text read."""
import json
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml
from conftest import make_version

from autowright import paths as paths_mod
from autowright import storage as storage_mod
from autowright.yamlio import atomic_write_text, load_yaml


def _rewrite(path: Path, edit) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _record(store, a, started: datetime) -> str:
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    h["started_at"] = started.isoformat(timespec="seconds")
    store.update_execution(h)
    return h["id"]


def _year_ahead(monkeypatch) -> None:
    real = datetime

    class Ahead(datetime):
        @classmethod
        def now(cls, tz=None):
            return real.now(tz) + timedelta(days=365)

    monkeypatch.setattr(storage_mod, "datetime", Ahead)


# ---------- A §5: the retention session clock ----------

def _history_then_glitch(store) -> dict:
    a = store.create_automation(make_version(), "History", None)
    now = datetime.now()
    ids = {n: _record(store, a, now - timedelta(days=n)) for n in (150, 10, 1)}
    store.load_all()  # the records are now loaded from disk: they feed the base
    a = store.autos[a["id"]]
    # The same tick's catch-up firing under a clock set a year ahead stamps a
    # record at the bogus "now" before the sweep thread runs.
    ids["ahead"] = _record(store, a, now + timedelta(days=365))
    store._refresh_exec_derived()
    store.settings["days"] = 90
    return ids


def test_a_session_record_stamped_a_year_ahead_never_feeds_the_anchor(store):
    ids = _history_then_glitch(store)
    assert store.retention_cleanup() == 1
    assert ids[150] not in store.execs
    assert all(ids[n] in store.execs for n in (10, 1, "ahead"))


def test_the_sweep_clock_a_year_ahead_still_keeps_the_recent_history(store, monkeypatch):
    ids = _history_then_glitch(store)
    _year_ahead(monkeypatch)
    assert store.retention_cleanup() == 1
    assert ids[150] not in store.execs
    assert all(ids[n] in store.execs for n in (10, 1, "ahead"))


def test_the_base_ignores_a_loaded_record_beyond_the_hold(store):
    a = store.create_automation(make_version(), "Hold", None)
    now = datetime.now()
    _record(store, a, now - timedelta(days=3))
    _record(store, a, now + timedelta(days=30))
    store.load_all()
    expected = (now - timedelta(days=3)).replace(microsecond=0).timestamp()
    assert store._retention_base == expected


def test_no_base_anchors_on_the_wall_clock(store):
    a = store.create_automation(make_version(), "Fresh", None)
    now = datetime.now()
    ids = {n: _record(store, a, now - timedelta(days=n)) for n in (10, 0)}
    store._refresh_exec_derived()
    store.settings["days"] = 90
    assert store._retention_base is None
    assert store.retention_cleanup() == 0
    assert all(i in store.execs for i in ids.values())


# ---------- G §21 2026-10-31 (b) third sweep: kv value rows coerced ----------

def test_a_kv_param_value_row_with_an_integer_value_loads_as_text(store):
    a = store.create_automation(make_version(), "Kv value", None)
    d = store.auto_dir(a)

    def version(data):
        data["params"] = [{"name": "headers", "kind": "kv", "label": "Headers", "help": "",
                           "default": []}]
    _rewrite(d / "versions" / "v1" / "automation.yaml", version)

    def top(data):
        data["param_values"] = {"headers": [{"key": "port", "value": 8080},
                                            {"key": "blank", "value": None},
                                            {"key": None, "value": "dropped"}]}
    _rewrite(d / "automation.yaml", top)
    store.load_all()
    b = store.autos[a["id"]]
    assert b["param_values"]["headers"] == [{"key": "port", "value": "8080"},
                                            {"key": "blank", "value": ""}]


def test_a_kv_param_value_row_still_not_text_is_ignored(store):
    a = store.create_automation(make_version(), "Kv bad", None)
    d = store.auto_dir(a)

    def version(data):
        data["params"] = [{"name": "headers", "kind": "kv", "label": "Headers", "help": "",
                           "default": []}]
    _rewrite(d / "versions" / "v1" / "automation.yaml", version)
    _rewrite(d / "automation.yaml",
             lambda data: data.update(param_values={"headers": [{"key": "k", "value": [1]}]}))
    store.load_all()
    assert "headers" not in store.autos[a["id"]]["param_values"]


# ---------- I §21 2026-10-31 third sweep (a): trigger canonical forms ----------

def test_a_lowercase_timezone_and_a_basic_format_at_load_canonical(store):
    a = store.create_automation(make_version(), "Triggers", None)

    def top(data):
        # A far-off one-shot: a spent `at` is consumed at load, never loaded.
        data["triggers"] = [
            {"id": "t1", "kind": "time", "at": "20991120T0900", "timezone": "europe/berlin",
             "enabled": False},
            {"id": "t2", "kind": "cron", "expression": "0 9 * * *", "source": "user",
             "timezone": "europe/berlin", "enabled": False},
            {"id": "t3", "kind": "time", "at": "2099-11-20T09:00:30", "enabled": False},
            {"id": "t4", "kind": "cron", "expression": "0 9 * * *", "source": "user",
             "timezone": "Nowhere/Special", "enabled": False},
        ]
    _rewrite(store.auto_dir(a) / "automation.yaml", top)
    store.load_all()
    triggers = {t["id"]: t for t in store.autos[a["id"]]["triggers"]}
    assert triggers["t1"]["at"] == "2099-11-20T09:00"
    assert triggers["t1"]["timezone"] == "Europe/Berlin"
    assert triggers["t2"]["timezone"] == "Europe/Berlin"
    assert triggers["t3"]["at"] == "2099-11-20T09:00:30"
    assert "t4" not in triggers  # an unresolvable zone is still dropped


# ---------- B-load §5: a lone surrogate at the read seam ----------

def test_a_lone_surrogate_in_automation_yaml_loads_replaced(store):
    a = store.create_automation(make_version(), "Placeholder", None)
    top = store.auto_dir(a) / "automation.yaml"
    text = top.read_text(encoding="utf-8")
    assert "name: Placeholder\n" in text
    top.write_text(text.replace("name: Placeholder\n", 'name: "X\\uD800Y"\n'), encoding="utf-8")
    store.load_all()
    b = store.autos[a["id"]]
    assert b["name"] == "X\ufffdY"
    with store.lock:
        payload = store.auto_json(b)
    json.dumps(payload, ensure_ascii=False).encode("utf-8")


def test_load_yaml_replaces_surrogates_in_keys_and_nested_values(tmp_path):
    f = tmp_path / "x.yaml"
    f.write_text('"k\\uDC80": ["a\\uD800", {n: "\\uDFFF"}]\n', encoding="utf-8")
    assert load_yaml(f) == {"k\ufffd": ["a\ufffd", {"n": "\ufffd"}]}


# ---------- P §5: atomic writes keep the mode and the symlink ----------

def test_an_existing_0644_file_keeps_its_mode(tmp_path):
    f = tmp_path / "catalog.yaml"
    f.write_text("old\n", encoding="utf-8")
    os.chmod(f, 0o644)
    atomic_write_text(f, "new\n")
    assert f.read_text(encoding="utf-8") == "new\n"
    assert stat.S_IMODE(f.stat().st_mode) == 0o644


def test_an_existing_0640_file_keeps_its_mode(tmp_path):
    f = tmp_path / "x.yaml"
    f.write_text("old\n", encoding="utf-8")
    os.chmod(f, 0o640)
    atomic_write_text(f, "new\n")
    assert stat.S_IMODE(f.stat().st_mode) == 0o640


def test_a_new_file_is_0644(tmp_path):
    f = tmp_path / "sub" / "new.yaml"
    atomic_write_text(f, "x\n")
    assert stat.S_IMODE(f.stat().st_mode) == 0o644


def test_an_explicit_mode_is_honoured(tmp_path):
    f = tmp_path / "backend.json"
    f.write_text("{}", encoding="utf-8")
    os.chmod(f, 0o644)
    atomic_write_text(f, "{}", mode=0o600)
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_a_symlinked_target_is_updated_in_place(tmp_path):
    real = tmp_path / "shared" / "catalog.yaml"
    real.parent.mkdir()
    real.write_text("old\n", encoding="utf-8")
    link = tmp_path / "link.yaml"
    link.symlink_to(real)
    atomic_write_text(link, "new\n")
    assert link.is_symlink()
    assert real.read_text(encoding="utf-8") == "new\n"
    assert link.read_text(encoding="utf-8") == "new\n"


# ---------- AJ §6.1: memory save temp files ----------

def test_memory_save_temp_files_are_skipped_by_listing_size_and_copies(store):
    a = store.create_automation(make_version(), "Temps", None)
    mem = store.auto_dir(a) / "memory"
    (mem / "seen.yaml").write_text("v: 1\n", newline="")
    (mem / ".foo.ab12.tmp").write_text("partial", newline="")
    (mem / "nested").mkdir()
    (mem / "nested" / ".state.json.x9.tmp").write_text("partial", newline="")
    names = [e["name"] for e in store.memory_files(a)]
    assert names == ["seen.yaml"]
    assert store._memory_file_stats(mem) == (5, 1)
    snap = store.snapshot_memory(a, "manual")
    copied = store.snapshots_dir(a) / snap["id"] / "memory"
    assert (copied / "seen.yaml").exists()
    assert not (copied / ".foo.ab12.tmp").exists()
    assert not (copied / "nested" / ".state.json.x9.tmp").exists()


def test_a_memory_with_only_temp_files_has_nothing_to_snapshot(store):
    a = store.create_automation(make_version(), "Only temps", None)
    (store.auto_dir(a) / "memory" / ".foo.ab12.tmp").write_text("partial", newline="")
    assert store.memory_files(a) == []
    assert store.snapshot_memory(a, "manual") is None


# ---------- AQ §5: delete drops the in-memory memos ----------

def test_delete_drops_the_seals_and_the_draft_test_summary(store):
    a = store.create_automation(make_version(), "Memos", None)
    directory = store.auto_dir(a)
    store.draft_test_json(store.draft_dir(a))
    assert any(k[0] == directory.name for k in store._sealed_shas)
    assert any(directory in f.parents for f in store._yaml_summaries)
    store.delete_automation(a)
    assert not any(directory.name in k for k in store._sealed_shas)
    assert not any(directory in f.parents for f in store._yaml_summaries)


# ---------- AU §20: the file-safe form of a name ----------

@pytest.mark.parametrize("name, expected", [
    (". .", "automation"),
    ("x .", "x"),
    ("Report?", "Report"),
    (" . x . ", "x"),
    ("...", "automation"),
])
def test_safe_filename_strips_dots_and_whitespace_until_stable(name, expected):
    assert paths_mod.safe_filename(name) == expected


# ---------- AV §5: a failed create leaves no folder ----------

def test_a_failed_create_removes_the_automation_folder(store, monkeypatch):
    import errno

    def partial(vd, ver, *args, **kwargs):
        vd.mkdir(parents=True, exist_ok=True)
        (vd / "01-say.py").write_text("partial", encoding="utf-8")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(store, "_write_version_folder", partial)
    before = set(paths_mod.automations_dir().iterdir())
    with pytest.raises(OSError):
        store.create_automation(make_version(), "Full disk", None)
    assert set(paths_mod.automations_dir().iterdir()) == before
    assert not any(a["name"] == "Full disk" for a in store.autos.values())


# ---------- AS §5: a UTF-8 BOM is stripped ----------

def test_a_bom_in_spec_step_script_and_yaml_is_stripped(store):
    a = store.create_automation(make_version(), "Bom", None)
    vd = store.auto_dir(a) / "versions" / "v1"
    (vd / "spec.md").write_bytes(b"\xef\xbb\xbf# Title\n\nBody text.\n")
    (vd / "notes.md").write_bytes(b"\xef\xbb\xbfA note\n")
    (vd / "01-say.py").write_bytes(b"\xef\xbb\xbfprint('hi')\n")
    top = store.auto_dir(a) / "automation.yaml"
    top.write_bytes(b"\xef\xbb\xbf" + top.read_bytes())
    store.load_all()
    b = store.autos[a["id"]]
    ver = b["versions"][1]
    assert ver["steps"][0]["code"] == "print('hi')\n"
    assert ver["notes"] == "A note"
    assert ver["spec"][0] == {"kind": "h1", "text": "Title"}
    assert b["name"] == "Bom"
