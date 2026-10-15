"""2026-10-14 audit fixes (storage, transfer, marketplace): the YAML error set
covers impossible dates, dot-prefixed execution entries are never touched,
the retention delete re-checks status inside its own hold, a torn log line is
terminated before the next append, a failed version commit removes its
folder, export refuses manifests that can't travel, import types parameter
names, and every marketplace mutation is disk-first."""
import io
import zipfile
from datetime import datetime, timedelta

import pytest
import yaml
from conftest import make_version
from test_marketplace import write_catalog

from autowright import marketplace, transfer
from autowright.marketplace import MarketplaceStore


# ---------- 1. §5 an impossible unquoted date is an unreadable file ----------

@pytest.mark.parametrize("filename", ["settings.yaml", "agents.yaml"])
def test_impossible_date_loads_as_the_default(tmp_path, filename):
    from autowright.yamlio import load_yaml_checked

    path = tmp_path / filename
    path.write_text("a: 2026-13-45\n", encoding="utf-8")
    data, ok = load_yaml_checked(path, {"default": True})
    assert data == {"default": True}
    assert ok is False


def test_too_deep_nesting_loads_as_the_default(tmp_path):
    from autowright.yamlio import load_yaml_checked

    path = tmp_path / "settings.yaml"
    path.write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    data, ok = load_yaml_checked(path, {})
    assert data == {} and ok is False


def _rezip(data, edit):
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for name in src.namelist():
            out.writestr(name, edit(name, src.read(name)) or src.read(name))
    return buf.getvalue()


def test_archive_manifest_with_an_impossible_date_is_a_transfer_error(store):
    a = store.create_automation(make_version(), "Dated", None)
    data = transfer.export_automation(store, a)

    def edit(name, raw):
        if name != "manifest.yaml":
            return None
        lines = [("exported_at: 2026-13-45" if ln.startswith("exported_at:") else ln)
                 for ln in raw.decode("utf-8").splitlines()]
        return ("\n".join(lines) + "\n").encode("utf-8")

    with pytest.raises(transfer.TransferError):
        transfer.preview_archive(store, _rezip(data, edit))


# ---------- 2. §5 dot-prefixed execution entries are never removed ----------

def test_dot_prefixed_entries_in_executions_survive_load(store):
    from autowright.storage import Store

    git = store.executions_dir() / ".git"
    (git / "objects").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    fresh = Store()
    fresh.load_all()
    fresh.load_all()
    assert (git / "HEAD").exists()
    assert (git / "objects").is_dir()
    assert ".git" not in fresh.execs


# ---------- 3. §6 retention re-checks status inside the delete's hold ----------

def _old_execution(store, a):
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    h["started_at"] = (datetime.now() - timedelta(days=60)).isoformat(timespec="seconds")
    store.update_execution(h)
    return h


def test_retention_skips_a_record_flipped_live_after_selection(store, monkeypatch):
    store.settings["days"] = 30
    a = store.create_automation(make_version(), "Retried", None)
    h = _old_execution(store, a)
    store._refresh_exec_derived()
    store.autos.pop(a["id"])  # no latest-execution exemption
    real_delete = store.delete_execution

    def flip_then_delete(execution_id, **kw):
        # Retried into a live state between the sweep's selection and its delete.
        store.execs[execution_id]["status"] = "executing"
        return real_delete(execution_id, **kw)

    monkeypatch.setattr(store, "delete_execution", flip_then_delete)
    assert store.retention_cleanup() == 0
    assert h["id"] in store.execs
    assert store.exec_dir(h["id"]).exists()


def test_finished_only_delete_leaves_a_live_record(store):
    a = store.create_automation(make_version(), "Live", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="executing")
    store.delete_execution(h["id"], finished_only=True)
    assert h["id"] in store.execs
    assert store.exec_dir(h["id"]).exists()


def test_retention_moves_on_after_a_database_error(store, monkeypatch):
    import sqlite3

    store.settings["days"] = 30
    a = store.create_automation(make_version(), "Batch", None)
    first = _old_execution(store, a)
    second = _old_execution(store, a)
    store._refresh_exec_derived()
    store.autos.pop(a["id"])
    real_delete = store.execdb.delete
    failed = []

    def flaky(execution_id):
        if not failed:
            failed.append(execution_id)
            raise sqlite3.OperationalError("database is locked")
        return real_delete(execution_id)

    monkeypatch.setattr(store.execdb, "delete", flaky)
    assert store.retention_cleanup() == 1
    assert first["id"] not in store.execs and second["id"] not in store.execs


# ---------- 4. §5 a torn log line is terminated before the next append ----------

def test_append_after_a_torn_line_keeps_records_apart(store):
    a = store.create_automation(make_version(), "Torn", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")

    def line(n):
        return {"sequence": n, "kind": "out", "text": f"line {n}",
                "timestamp": "2026-10-14T10:00:00Z"}

    store.append_log_line(h["id"], store.EXEC_LOG, line(1))
    p = store.log_file(h["id"], store.EXEC_LOG)
    with open(p, "ab") as f:
        f.write(b'{"sequence": 2, "kind": "out", "te')  # a crash mid-append
    store._log_counts.clear()  # a restart re-seeds from the file
    assert store.append_log_line(h["id"], store.EXEC_LOG, line(3))
    raw = p.read_bytes().split(b"\n")
    assert raw[-1] == b""
    assert len(raw) == 4  # three lines, the torn one standing alone
    got = store.read_log(h["id"])
    assert [ln["sequence"] for ln in got] == [1, 3]
    # the line-number slice still lines up: line 1 carries sequence 1
    assert [ln["sequence"] for ln in store.read_log(h["id"], since=1)] == [3]
    assert p.read_bytes().split(b"\n")[2].startswith(b'{"sequence": 3')


def test_log_count_seed_tolerates_undecodable_bytes(store):
    a = store.create_automation(make_version(), "Bytes", None)
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    p = store.log_file(h["id"], store.EXEC_LOG)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xfe not utf-8\n")
    assert store.append_log_line(h["id"], store.EXEC_LOG,
                                 {"sequence": 2, "kind": "out", "text": "ok",
                                  "timestamp": "2026-10-14T10:00:00Z"})
    assert store._log_counts[(h["id"], store.EXEC_LOG)] == 2


# ---------- 5. §5 a failed version commit removes its folder ----------

@pytest.mark.parametrize("how", ["save", "restore"])
def test_failed_version_commit_leaves_no_folder(store, monkeypatch, how):
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Rollback", None)

    def boom(_a, _mutate):
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as patch, pytest.raises(OSError):
        patch.setattr(store, "_commit_toplevel", boom)
        if how == "save":
            store.save_new_version(a, make_version(note="Second"))
        else:
            store.restore_version(a, 1)
    assert not (store.auto_dir(a) / "versions" / "v2").exists()
    assert sorted(a["versions"]) == [1]
    fresh = Store()
    fresh.load_all()
    assert sorted(fresh.autos[a["id"]]["versions"]) == [1]


# ---------- 6. §5.1 export refuses what can't travel; import types names ----------

def test_export_refuses_a_step_with_no_file_name(store):
    a = store.create_automation(make_version(), "Nameless", None)
    a["versions"][a["current_version"]]["steps"][1]["file"] = None
    with pytest.raises(transfer.TransferError) as e:
        transfer.export_automation(store, a)
    assert "step 2 has no file name" in str(e.value)


@pytest.mark.parametrize("timeout", ["soon", [5], 2.5, True])
def test_export_refuses_a_non_integer_timeout(store, timeout):
    a = store.create_automation(make_version(), "Timed", None)
    a["versions"][a["current_version"]]["steps"][0]["timeout"] = timeout
    with pytest.raises(transfer.TransferError) as e:
        transfer.export_automation(store, a)
    assert "01-say.py" in str(e.value)


def test_export_accepts_a_digit_string_timeout(store):
    a = store.create_automation(make_version(), "Digits", None)
    a["versions"][a["current_version"]]["steps"][0]["timeout"] = "120"
    data = transfer.export_automation(store, a)
    meta = yaml.safe_load(zipfile.ZipFile(io.BytesIO(data)).read("automation/automation.yaml"))
    assert meta["steps"][0]["timeout"] == 120


@pytest.mark.parametrize("name", [["greeting"], "   ", 7, None])
def test_import_rejects_a_non_text_param_name(store, name):
    a = store.create_automation(make_version(), "Params", None)
    data = transfer.export_automation(store, a)

    def edit(member, raw):
        if member != "automation/automation.yaml":
            return None
        meta = yaml.safe_load(raw)
        meta["params"][0]["name"] = name
        return yaml.safe_dump(meta).encode("utf-8")

    with pytest.raises(transfer.TransferError) as e:
        transfer.preview_archive(store, _rezip(data, edit))
    assert "invalid parameter definition" in str(e.value)


def test_export_route_answers_422_for_a_nameless_step(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Route", None)
    a["versions"][a["current_version"]]["steps"][0]["file"] = None
    r = client.get(f"/automations/{a['id']}/export")
    assert r.status_code == 422
    assert "step 1 has no file name" in r.json()["detail"]


# ---------- 7. §22.2 every marketplace mutation is disk-first ----------

@pytest.fixture()
def market(home):
    s = MarketplaceStore()
    s.load()
    return s


def _failing_save():
    raise OSError(28, "No space left on device")


def test_failed_settings_save_reverts_the_row(market, tmp_path, monkeypatch):
    first = write_catalog(tmp_path, [{"title": "One", "path": str(tmp_path / "one.autowright")}],
                          filename="first.yaml")
    second = write_catalog(tmp_path, [{"title": "Two", "path": str(tmp_path / "two.autowright")}],
                           filename="second.yaml")
    source = market.add(path=str(first))
    with market.lock:
        row = market._find(source["id"])
        before = {key: row[key] for key in marketplace.COLUMNS}
    monkeypatch.setattr(market, "_save", _failing_save)
    with pytest.raises(OSError):
        market.update_settings(source["id"], location=str(second), expanded=False)
    with market.lock:
        row = market._find(source["id"])
        assert {key: row[key] for key in marketplace.COLUMNS} == before


def test_failed_remove_save_puts_the_row_back(market, tmp_path, monkeypatch):
    shelves = [write_catalog(tmp_path, [{"title": t, "path": str(tmp_path / f"{t}.autowright")}],
                             filename=f"{t}.yaml") for t in ("a", "b")]
    added = [market.add(path=str(f)) for f in shelves]
    with market.lock:
        order = [s["id"] for s in market.sources]
    directory = market.source_dir(added[0]["id"])
    assert directory.exists()
    monkeypatch.setattr(market, "_save", _failing_save)
    with pytest.raises(OSError):
        market.remove(added[0]["id"])
    with market.lock:
        assert [s["id"] for s in market.sources] == order
    assert directory.exists()


def test_refresh_whose_copy_write_fails_records_the_error(market, tmp_path, monkeypatch):
    f = write_catalog(tmp_path, [{"title": "One", "path": str(tmp_path / "one.autowright")}])
    source = market.add(path=str(f))

    def full(_path, _text, **_kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(marketplace, "atomic_write_text", full)
    after = market.refresh(source["id"])
    assert after["error"].startswith("couldn't save the catalog copy: ")
    assert after["refreshedAt"] == source["refreshedAt"]
    assert [e["title"] for e in after["entries"]] == ["One"]
