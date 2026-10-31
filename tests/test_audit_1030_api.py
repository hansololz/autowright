"""2026-10-30 audit batch (API routes, test execution, transfer): the test's
live-engine entry created with its record, setup moved onto the test thread,
guarded startup sweeps, narrowed 404 mappings and the `vN` label grammar, the
unreadable-current-version answers, lenient /validate-steps, the retry 409,
the secret-update Keychain rollback, the two-way on-disk step check behind
duplicate and restore, LF step code, alias-free untrusted YAML with capped
error excerpts, export running the importer's checks, and `/health` `pid`."""
import io
import logging
import os
import threading
import time
import zipfile

import pytest
import yaml
from conftest import make_version

from autowright import transfer


@pytest.fixture(autouse=True)
def stub_check_ready(monkeypatch):
    """No test spawns a real harness status subprocess."""
    monkeypatch.setattr(transfer.harness, "check_ready",
                        lambda name, model=None, mode="default": False)


def _draft():
    return {"steps": [{"file": "01-ok.py", "name": "Ok", "description": "",
                       "code": "from autowright import result\nresult.status('ok')\n"}],
            "params": [], "packages": [], "spec": []}


def _archive(*, params=(), steps=None, manifest_text=None, meta_text=None):
    """A hand-built §5.1 format-2 archive."""
    steps = list(steps or [{"file": "01-a.py", "name": "A", "description": "",
                            "code": "print('a')\n"}])
    manifest = {"format_version": 2, "exported_at": "2026-10-30T09:00:00",
                "app_version": "0.0.0", "name": "Ported", "triggers": []}
    meta = {"description": "", "params": list(params),
            "steps": [{k: v for k, v in s.items() if k != "code"} for s in steps]}
    files = [("manifest.yaml", manifest_text or yaml.safe_dump(manifest, sort_keys=False)),
             ("automation/automation.yaml", meta_text or yaml.safe_dump(meta, sort_keys=False)),
             ("automation/spec.md", "# T\n\nBody.\n"),
             ("agents.yaml", yaml.safe_dump({"agents": []})),
             ("secrets.yaml", yaml.safe_dump({"secrets": []}))]
    files += [(f"automation/{s['file']}", s["code"]) for s in steps]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, text in files:
            z.writestr(path, text)
    return buf.getvalue()


def _wait_settled(engine, store, execution_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        h = store.execs.get(execution_id)
        if not engine.is_live(execution_id) and (h is None or h["status"] != "executing"):
            return h
        time.sleep(0.02)
    raise AssertionError("the test never settled")


# ---------- A1: a cancel during setup finds the entry ----------

def test_a_cancel_during_setup_finishes_the_test_cancelled_without_running(client, monkeypatch):
    from autowright import api, engine as engine_module, testexec
    from autowright.storage import store

    spawned = []
    monkeypatch.setattr(engine_module, "run_step_process",
                        lambda *args, **kwargs: spawned.append(1) or 0)
    with store.lock:
        # The test thread's registration re-check waits for this hold, so
        # the cancel lands inside the old setup gap.
        execution_id = testexec.start(api.engine, _draft(), None, [], [], {})
        assert api.engine.cancel(execution_id) is True  # the placeholder is found
    h = _wait_settled(api.engine, store, execution_id)
    assert h["status"] == "cancelled"
    assert h["note"] == "cancelled before it ran"
    assert all(s["status"] == "cancelled" for s in store.exec_full(execution_id)["steps"])
    assert spawned == []
    assert not (store.exec_dir(execution_id) / "steps").exists()  # no step file written


def test_a_settle_during_setup_leaves_no_test_behind(client, monkeypatch):
    from autowright import api, engine as engine_module, testexec
    from autowright.storage import store

    spawned = []
    monkeypatch.setattr(engine_module, "run_step_process",
                        lambda *args, **kwargs: spawned.append(1) or 0)
    with store.lock:
        execution_id = testexec.start(api.engine, _draft(), None, [], [], {})
        assert api._cancel_live_draft_work(None) == [execution_id]
    _wait_settled(api.engine, store, execution_id)
    assert execution_id not in store.execs  # the settled draft's test is deleted
    assert spawned == []


# ---------- A2: the route answers before the memory copy ----------

def test_post_tests_answers_before_the_memory_copy(client, monkeypatch):
    from autowright import api, paths, testexec
    from autowright.storage import store

    memory = paths.pending_draft_dir() / "memory"
    memory.mkdir(parents=True)
    (memory / "notes.md").write_text("big", encoding="utf-8")
    release = threading.Event()
    copy_started = threading.Event()
    real_copytree = testexec.shutil.copytree

    def slow_copytree(src, dst, *args, **kwargs):
        copy_started.set()
        assert release.wait(10)
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(testexec.shutil, "copytree", slow_copytree)
    r = client.post("/tests", json={"draft": _draft()})
    assert r.status_code == 200, r.text
    execution_id = r.json()["executionId"]
    assert copy_started.wait(10)  # the copy runs on the test thread, still blocked
    assert store.execs[execution_id]["status"] == "executing"
    release.set()
    h = _wait_settled(api.engine, store, execution_id, timeout=30)
    assert h["status"] == "succeeded"


# ---------- A3: a failing startup sweep never fails startup ----------

def test_a_startup_sweep_raising_oserror_does_not_fail_startup(home, monkeypatch, caplog):
    from fastapi.testclient import TestClient

    from autowright import api, testexec
    from autowright.storage import store

    store.load_all()

    def boom():
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(api, "_repair_stale_executing", boom)
    # The lifespan's shutdown flips the drafting stopping flag; restored after.
    monkeypatch.setattr(api.draft_jobs, "_stopping", False)
    monkeypatch.setattr(testexec, "clear_scratch", boom)
    with caplog.at_level(logging.WARNING, logger="autowright.api"):
        with TestClient(api.app) as started:
            assert started.get("/health").status_code == 200
    assert "the stale-execution repair failed" in caplog.text
    assert "the test scratch sweep failed" in caplog.text


# ---------- A6/A8: execute's 404 is the version label alone ----------

def test_execute_maps_only_version_not_found_to_404(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Narrow", "mock")

    def broken_start(*args, **kwargs):
        raise KeyError("steps")

    monkeypatch.setattr(api.engine, "start", broken_start)
    with pytest.raises(KeyError):
        client.post(f"/automations/{a['id']}/execute", json={})


@pytest.mark.parametrize("label", ["1", "vv1", "v+1", "v-1", " 1"])
def test_execute_rejects_labels_outside_the_vn_grammar(client, label):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Labels", "mock")
    r = client.post(f"/automations/{a['id']}/execute", json={"version": label})
    assert r.status_code == 404, r.text
    assert not store.execs


def test_engine_label_parser_is_the_diff_routes():
    from autowright.engine import Engine

    auto = {"current_version": 4}
    assert Engine._parse_version_label(auto, "V3") == ("version", 3)
    assert Engine._parse_version_label(auto, None) == ("version", 4)
    assert Engine._parse_version_label(auto, "Draft") == ("draft", None)
    assert Engine._parse_version_label(auto, "3") == ("version", -1)
    assert Engine._parse_version_label(auto, "vv3") == ("version", -1)


# ---------- E9: retry of a gone version is a 409 ----------

def test_retry_of_an_execution_whose_version_is_gone_is_409(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Gone", "mock")
    h = store.create_execution(a, "version", 7, "manual", [], status="failed")
    store.update_execution(h)
    a["_live"].discard(h["id"])
    r = client.post(f"/executions/{h['id']}/retry")
    assert r.status_code == 409
    assert r.json()["detail"] == "version v7 no longer exists"


# ---------- A7: an unreadable current version ----------

UNREADABLE = "the current version can't be read — restore an earlier version"


def test_an_unreadable_current_version_answers_422_and_409(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Unreadable", "mock")
    a["versions"].pop(a["current_version"])  # the folder failed to load (§5)
    r = client.get(f"/automations/{a['id']}/export")
    assert r.status_code == 422 and r.json()["detail"] == UNREADABLE
    r = client.post("/drafts", json={"mode": "sync", "automationId": a["id"],
                                     "spec": "# T\n"})
    assert r.status_code == 409 and r.json()["detail"] == UNREADABLE
    r = client.post(f"/automations/{a['id']}/duplicate", json={})
    assert r.status_code == 409 and r.json()["detail"] == UNREADABLE


# ---------- A10: /validate-steps renders a mistyped step field ----------

def test_validate_steps_renders_a_mistyped_step_field_as_a_200_line(client):
    draft = _draft()
    draft["steps"][0]["timeout"] = "60"
    draft["steps"][0]["locked"] = "yes"
    r = client.post("/validate-steps", json={"draft": draft})
    assert r.status_code == 200, r.text
    errors = r.json()["errors"]
    assert any(e.startswith("step 1: timeout ") for e in errors), errors
    assert any(e.startswith("step 1: locked ") for e in errors), errors
    # A body-level error stays the §19 422.
    assert client.post("/validate-steps", json={}).status_code == 422
    assert client.post("/validate-steps",
                       json={"draft": {"steps": 3}}).status_code == 422


# ---------- S3: a failed secret update leaves the Keychain consistent ----------

def test_secret_update_restores_the_previous_value_when_the_save_fails(client, monkeypatch):
    from autowright import keychain
    from autowright.storage import store

    stored = client.post("/secrets", json={"name": "TOKEN", "value": "old"}).json()

    def failing_save(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store, "save_secrets", failing_save)
    with pytest.raises(OSError):
        client.put(f"/secrets/{stored['id']}", json={"value": "new"})
    assert keychain.get_secret(stored["id"]) == "old"


def test_secret_update_of_a_placeholder_removes_the_value_when_the_save_fails(client, monkeypatch):
    from autowright import keychain
    from autowright.storage import store

    stored = client.post("/secrets", json={"name": "PLACEHOLDER"}).json()
    assert stored["set"] is False

    def failing_save(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(store, "save_secrets", failing_save)
    with pytest.raises(OSError):
        client.put(f"/secrets/{stored['id']}", json={"value": "new"})
    assert keychain.get_secret(stored["id"]) is None


def test_secret_update_racing_a_delete_removes_the_written_value(client, monkeypatch):
    from autowright import keychain
    from autowright.storage import store

    stored = client.post("/secrets", json={"name": "RACED", "value": "old"}).json()
    real_set = keychain.set_secret

    def set_then_row_deleted(secret_id, value):
        real_set(secret_id, value)
        store.secrets = [s for s in store.secrets if s["id"] != secret_id]

    monkeypatch.setattr(keychain, "set_secret", set_then_row_deleted)
    r = client.put(f"/secrets/{stored['id']}", json={"value": "new"})
    assert r.status_code == 404
    assert keychain.get_secret(stored["id"]) is None


# ---------- S4: a reverted hand edit clears for duplicate and restore ----------

def test_a_reverted_hand_edit_lets_duplicate_through(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Reverted", "mock")
    script = store.auto_dir(a) / "versions" / "v1" / "02-finish.py"
    original = script.read_bytes()
    script.write_text("print('hand edit')\n", encoding="utf-8")
    assert client.post(f"/automations/{a['id']}/duplicate", json={}).status_code == 409
    assert a["versions"][1]["steps"][1]["modifiedOnDisk"] is True
    script.write_bytes(original)
    r = client.post(f"/automations/{a['id']}/duplicate", json={})
    assert r.status_code == 201, r.text
    assert not a["versions"][1]["steps"][1].get("modifiedOnDisk")


def test_a_missing_sealed_script_counts_as_modified(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Missing", "mock")
    (store.auto_dir(a) / "versions" / "v1" / "02-finish.py").unlink()
    assert [s["name"] for s in store.disk_modified_steps(a)] == ["Finish"]
    assert client.post(f"/automations/{a['id']}/duplicate", json={}).status_code == 409


def test_a_reverted_hand_edit_lets_restore_through(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Restore", "mock")
    store.save_new_version(a, make_version(note="v2"))
    script = store.auto_dir(a) / "versions" / "v1" / "01-say.py"
    original = script.read_bytes()
    script.write_text("print('hand edit')\n", encoding="utf-8")
    r = client.post(f"/automations/{a['id']}/restore", json={"version": 1})
    assert r.status_code == 422
    script.write_bytes(original)
    r = client.post(f"/automations/{a['id']}/restore", json={"version": 1})
    assert r.status_code == 200, r.text
    assert r.json()["version"] == 3


# ---------- S12: step code lands with LF line endings ----------

def test_imported_step_code_is_normalized_to_lf(client):
    from autowright.storage import store

    a, _ = transfer.import_automation(store, _archive(steps=[
        {"file": "01-a.py", "name": "A", "description": "",
         "code": "print('a')\r\n\r\nprint('b')\rprint('c')\r\n"}]))
    code = a["versions"][1]["steps"][0]["code"]
    assert code == "print('a')\n\nprint('b')\nprint('c')\n"
    assert b"\r" not in (store.auto_dir(a) / "versions" / "v1" / "01-a.py").read_bytes()


def test_saved_step_code_is_normalized_to_lf(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Saved", "mock")
    draft = make_version()
    draft["steps"][1]["code"] = "from autowright import result\r\nresult.status('ok')\r\n"
    r = client.post(f"/automations/{a['id']}/versions", json={"draft": draft})
    assert r.status_code == 200, r.text
    vd = store.auto_dir(a) / "versions" / f"v{a['current_version']}"
    assert b"\r" not in (vd / "02-finish.py").read_bytes()
    assert "\r" not in a["versions"][a["current_version"]]["steps"][1]["code"]


# ---------- C2: untrusted YAML refuses aliases; error text is capped ----------

def test_an_archive_using_aliases_is_refused(client):
    meta = ("description: ''\n"
            "l0: &l0 [x, x, x]\n"
            "l1: [*l0, *l0, *l0]\n"
            "params: []\n"
            "steps:\n- {file: 01-a.py, name: A, description: ''}\n")
    data = _archive(meta_text=meta)
    r = client.post("/automations/import", content=data)
    assert r.status_code == 422, r.text
    assert "anchors and aliases" in r.json()["detail"]


def test_load_untrusted_refuses_anchors_and_aliases():
    from autowright.untrusted_yaml import load_untrusted

    assert load_untrusted("a: [1, 2]\nb: text\n") == {"a": [1, 2], "b": "text"}
    for text in ("a: &x [1]\nb: *x\n", "a: &a [*a]\n", "a: &lonely 1\n"):
        with pytest.raises(ValueError):
            load_untrusted(text)


def test_excerpt_is_capped_escaped_and_never_walks_a_recursive_value():
    from autowright.untrusted_yaml import excerpt

    assert len(excerpt("x" * 10_000)) <= 200
    assert excerpt("a\x1b[2K\rb") == "'a\\x1b[2K\\rb'"
    loop: list = []
    loop.append(loop)
    assert len(excerpt(loop)) <= 200
    wide = [["y" * 500] * 1000] * 1000
    assert len(excerpt(wide, 80)) <= 80


def test_a_catalog_using_aliases_is_refused():
    from autowright import marketplace

    text = ("format_version: 1\n"
            "e: &e {title: T, path: 'https://x.test/a.autowright'}\n"
            "entries: [*e, *e]\n")
    with pytest.raises(marketplace.MarketplaceError) as e:
        marketplace.parse_catalog(text, location="/m/marketplace.yaml")
    assert "anchors and aliases" in str(e.value)


def test_a_catalog_format_error_quotes_a_capped_excerpt():
    from autowright import marketplace

    text = yaml.safe_dump({"format_version": "9" * 5000, "entries": []})
    with pytest.raises(marketplace.MarketplaceError) as e:
        marketplace.parse_catalog(text, location="/m/marketplace.yaml")
    assert len(str(e.value)) < 400


def test_an_imported_param_default_is_type_checked_against_its_kind(client):
    from autowright.storage import store

    with pytest.raises(transfer.TransferError) as e:
        transfer.import_automation(store, _archive(params=[
            {"name": "p", "kind": "text", "default": [1, 2]}]))
    assert str(e.value) == "param 'p': the default isn't a text value"


def test_an_import_error_never_embeds_a_whole_untrusted_value(client):
    from autowright.storage import store

    huge = {"name": "p", "kind": "bogus", "junk": ["z" * 1000] * 1000}
    with pytest.raises(transfer.TransferError) as e:
        transfer.import_automation(store, _archive(params=[huge]))
    assert str(e.value).startswith("invalid parameter definition: ")
    assert len(str(e.value)) < 300


# ---------- C9: export runs the importer's definition checks ----------

def test_export_refuses_what_the_importer_would_reject(client):
    from autowright.storage import store

    # 2026-10-30 second sweep: a missing param default no longer refuses (export
    # writes `param_default`, §5.1), so the refused shape here is a package
    # declared without a why.
    a = store.create_automation(make_version(packages=[{"pip": "requests", "import": "requests"}]),
                                "Whyless", "mock")
    r = client.get(f"/automations/{a['id']}/export")
    assert r.status_code == 422, r.text
    assert r.json()["detail"] == ("packages: requests needs a why — one line on what the "
                                  "steps use it for — repair the automation first")


def test_export_writes_the_kind_default_for_a_param_without_one(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Undefaulted", "mock")
    a["versions"][a["current_version"]]["params"][0].pop("default")
    r = client.get(f"/automations/{a['id']}/export")
    assert r.status_code == 200, r.text


# ---------- M4: /health carries the backend pid ----------

def test_health_carries_the_pid(client):
    assert client.get("/health").json()["pid"] == os.getpid()


# ---------- additions: drafts stopping, catalog busy, upgrade pins ----------

def test_a_draft_refused_for_shutdown_says_so(client, monkeypatch):
    from autowright import api

    monkeypatch.setattr(api.draft_jobs, "_stopping", True)
    r = client.post("/drafts", json={"mode": "chat", "text": "hello"})
    assert r.status_code == 409
    assert r.json()["detail"] == "the backend is shutting down"


def test_a_catalog_read_with_no_free_slot_is_503(client, monkeypatch):
    from autowright import api, marketplace

    def busy(source_id):
        raise marketplace.MarketplaceBusy(marketplace.CATALOG_BUSY)

    monkeypatch.setattr(api.marketplace_store, "read_catalog", busy)
    r = client.get("/marketplace/sources/anything/catalog")
    assert r.status_code == 503
    assert r.json()["detail"] == marketplace.CATALOG_BUSY


def test_package_update_pins_the_other_declared_packages(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    store.create_automation(make_version(packages=[
        {"pip": "pandas", "import": "pandas", "why": "tables"}]), "Pins", "mock")
    seen = {}

    def fake_upgrade(entries, wait=True, declared=()):
        seen.update(entries=entries, declared=set(declared))
        return []

    monkeypatch.setattr(api.pkglib, "upgrade", fake_upgrade)
    r = client.post("/packages/update",
                    json={"packages": [{"pip": "requests", "import": "requests"}]})
    assert r.status_code == 200, r.text
    assert seen["declared"] == {"pandas"}
    assert seen["entries"] == [{"pip": "requests", "import": "requests"}]


# ---------- C4: import runs the save path's per-step reference checks ----------

def test_an_archive_step_subscripting_an_agent_it_does_not_declare_is_refused(client):
    from autowright.storage import store

    data = _archive(steps=[{"file": "01-a.py", "name": "Ask", "description": "",
                            "agent": True, "why": "asks",
                            "agents": [{"ref": "1"}],
                            "code": "from autowright import agents\nagents['2'].ask('hi')\n"}])
    # Both refs are listed archive-wide, so only the per-step rule refuses it.
    zipped = zipfile.ZipFile(io.BytesIO(data))
    files = {n: zipped.read(n) for n in zipped.namelist()}
    files["agents.yaml"] = yaml.safe_dump({"agents": [
        {"ref": r, "name": f"Agent {r}", "description": "", "harness": "Claude Code",
         "mode": "default", "model": None} for r in ("1", "2")]}).encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for name, body in files.items():
            out.writestr(name, body)
    with pytest.raises(transfer.TransferError) as e:
        transfer.import_automation(store, buf.getvalue())
    assert str(e.value) == ("step Ask: code subscripts agents['2'], which isn't among "
                            "this step's declared agents entries")


def test_an_archive_step_package_that_is_not_declared_is_refused(client):
    from autowright.storage import store

    data = _archive(steps=[{"file": "01-a.py", "name": "Fetch", "description": "",
                            "packages": [{"import": "requests", "why": "http"}],
                            "code": "print('a')\n"}])
    with pytest.raises(transfer.TransferError) as e:
        transfer.import_automation(store, data)
    assert str(e.value) == ("step Fetch: package 'requests' isn't among the manifest's "
                            "declared packages")
