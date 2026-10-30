"""Runtime audit regressions (2026-10-28).

§4.8 key-gated JSON leaf probes (a service-account key's public leaves never
redact or block); §7 the line writer's explicit-flush hold-back and its
re-entrant lock; §4.1/§7 the two-way, newline-normalized execution-start seal
check and the draft's start-time disk read; §7 a retry pass that sets no chip
keeps the record's chip; §7 pass durations on the monotonic clock; §6.2 a pip
install that provides no module for the declared import.
"""
import json
import sys
import threading

import pytest
from conftest import make_version, read_all_logs, wait_done

from autowright import keychain

PEM_BODY = ["MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7",
            "q1w2e3r4t5y6u7i8o9p0AaSsDdFfGgHhJjKkLlZzXxCcVvBbNnMm",
            "ZyXwVuTsRqPoNmLkJiHgFeDcBa9876543210zzYYxxWWvvUUttSS"]
PRIVATE_KEY = ("-----BEGIN PRIVATE KEY-----\n" + "\n".join(PEM_BODY)
               + "\n-----END PRIVATE KEY-----\n")
PRIVATE_KEY_ID = "0123456789abcdef0123456789abcdef01234567"
GCP_SERVICE_ACCOUNT = json.dumps({
    "type": "service_account",
    "project_id": "my-project-123456",
    "private_key_id": PRIVATE_KEY_ID,
    "private_key": PRIVATE_KEY,
    "client_email": "robot@my-project-123456.iam.gserviceaccount.com",
    "client_id": "123456789012345678901",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
    "client_x509_cert_url": "https://www.googleapis.com/robot/v1/metadata/x509/robot",
    "universe_domain": "googleapis.com",
}, indent=2)


def add_secret(store, name) -> str:
    import uuid

    secret_id = str(uuid.uuid4())
    store.secrets.append({"id": secret_id, "name": name, "description": "", "set": True})
    return secret_id


# ---------- §4.8: key-gated JSON leaf probes ----------

def test_service_account_probes_hold_the_key_material_only():
    from autowright.executor import secret_probes

    probes = secret_probes(GCP_SERVICE_ACCOUNT)
    for line in PEM_BODY:
        assert line in probes
    assert PRIVATE_KEY_ID in probes
    assert "googleapis.com" not in probes
    assert "service_account" not in probes
    assert "https://oauth2.googleapis.com/token" not in probes
    assert "https://accounts.google.com/o/oauth2/auth" not in probes
    assert "my-project-123456" not in probes


def test_outbound_scan_lets_a_request_naming_the_host_through():
    from autowright.executor import scan_outbound

    scan_outbound("GET https://sheets.googleapis.com/v4", "agent prompt",
                  {"SERVICE_ACCOUNT": GCP_SERVICE_ACCOUNT})
    with pytest.raises(RuntimeError, match="SERVICE_ACCOUNT"):
        scan_outbound("key: " + PEM_BODY[1], "agent prompt",
                      {"SERVICE_ACCOUNT": GCP_SERVICE_ACCOUNT})


def test_secret_keyed_and_keyless_leaves_still_probe():
    from autowright.executor import secret_probes

    assert "abcdefgh1234" in secret_probes('{"api_key": "abcdefgh1234"}')
    assert "abcdefgh1234" in secret_probes('["abcdefgh1234"]')
    assert "abcdefgh1234" in secret_probes('[["abcdefgh1234"]]')
    aws = secret_probes('{"aws_access_key_id": "AKIAIOSFODNN7EXAMPLE", "region": "us-east-1"}')
    assert "AKIAIOSFODNN7EXAMPLE" in aws
    assert "us-east-1" not in aws


def test_secret_keyed_url_leaves_never_probe():
    from autowright.executor import secret_probes

    probes = secret_probes('{"token_endpoint": "https://auth.example.com/token-path",'
                           ' "client_secret": "s3cr3t-value-xyz"}')
    assert "https://auth.example.com/token-path" not in probes
    assert "s3cr3t-value-xyz" in probes


# ---------- §7: the line writer's explicit flush and re-entrant lock ----------

def _guarded_writer(monkeypatch, guards):
    from autowright import executor

    emitted = []
    monkeypatch.setattr(executor, "emit", lambda op, **kw: emitted.append(kw["text"]))
    monkeypatch.setattr(executor, "_cut_guards", list(guards))
    monkeypatch.setattr(executor, "_GUARD_TAIL", executor._guard_tail(list(guards)))
    return executor._LineWriter("out"), emitted


def test_explicit_flush_never_splits_a_secret_value(monkeypatch):
    value = "SECRETVALUE123456"
    writer, emitted = _guarded_writer(monkeypatch, [value])
    writer.write("the key is SECRETVAL")
    writer.flush()
    assert all("SECRETVAL" not in text for text in emitted)  # the head is held
    writer.write("UE123456\n")
    assert sum(value in text for text in emitted) == 1
    assert not any(text.endswith("SECRETVAL") or text.startswith("UE123456")
                   for text in emitted)


def test_explicit_flush_emits_text_that_starts_no_secret(monkeypatch):
    writer, emitted = _guarded_writer(monkeypatch, ["SECRETVALUE123456"])
    writer.write("progress 50%")
    writer.flush()
    assert emitted == ["progress 50%"]
    assert writer.buf == ""


def test_final_flush_releases_the_held_tail(monkeypatch):
    writer, emitted = _guarded_writer(monkeypatch, ["SECRETVALUE123456"])
    writer.write("tail SECRET")
    writer.flush()
    assert emitted == ["tail "]
    writer.final_flush()
    assert emitted == ["tail ", "SECRET"]
    assert writer.buf == ""


def test_reentrant_write_from_emit_never_deadlocks(monkeypatch):
    from autowright import executor

    emitted = []
    writer = executor._LineWriter("out")
    reentered = {"done": False}

    def emit_that_prints(op, **kw):
        emitted.append(kw["text"])
        if not reentered["done"]:
            reentered["done"] = True
            sys.stdout.write("printed from a handler\n")

    monkeypatch.setattr(executor, "emit", emit_that_prints)
    monkeypatch.setattr(sys, "stdout", writer)
    worker = threading.Thread(target=lambda: writer.write("first line\n"), daemon=True)
    worker.start()
    worker.join(5)
    assert not worker.is_alive(), "the re-entrant write deadlocked"
    assert emitted == ["first line", "printed from a handler"]


def test_emit_lock_is_reentrant():
    from autowright import executor

    assert executor._emit_lock.acquire(timeout=1)
    try:
        assert executor._emit_lock.acquire(timeout=1)
        executor._emit_lock.release()
    finally:
        executor._emit_lock.release()


# ---------- §4.1/§7: the two-way, newline-normalized seal check ----------

def test_reverted_hand_edit_clears_the_flag_and_refreshes_code(store, monkeypatch):
    from autowright import engine as engine_mod
    from autowright.engine import Engine

    a = store.create_automation(make_version(), "Reverted", None)
    script = store.auto_dir(a) / "versions" / "v1" / "01-say.py"
    original_bytes = script.read_bytes()
    original_text = a["versions"][1]["steps"][0]["code"]
    script.write_text('from autowright import log\nlog("edited by hand")\n', encoding="utf-8")
    published = []
    monkeypatch.setattr(engine_mod.hub, "publish",
                        lambda event, **kw: published.append((event, kw)))
    engine = Engine(store)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    step = a["versions"][1]["steps"][0]
    assert step.get("modifiedOnDisk") is True
    assert step["code"] == 'from autowright import log\nlog("edited by hand")\n'
    assert any(p["kind"] == "step-modified" for p in store.auto_json(a)["problems"])

    script.write_bytes(original_bytes)
    published.clear()
    h2 = engine.start(a, "manual")
    wait_done(engine, h2["id"])
    assert h2["status"] == "succeeded"
    assert "modifiedOnDisk" not in step
    assert step["code"] == original_text == original_bytes.decode("utf-8")
    assert not any(p["kind"] == "step-modified" for p in store.auto_json(a)["problems"])
    assert any(event == "automation.changed" and kw.get("automationId") == a["id"]
               and not any(p["kind"] == "step-modified"
                           for p in (kw.get("automation") or {}).get("problems") or [])
               for event, kw in published)


def test_crlf_file_with_the_sealed_text_never_changes_code(store):
    from autowright.engine import Engine

    a = store.create_automation(make_version(), "Crlf", None)
    script = store.auto_dir(a) / "versions" / "v1" / "01-say.py"
    original_text = a["versions"][1]["steps"][0]["code"]
    script.write_bytes(original_text.replace("\n", "\r\n").encode("utf-8"))
    engine = Engine(store)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "succeeded"
    code = a["versions"][1]["steps"][0]["code"]
    assert "\r" not in code
    assert code == original_text


def test_draft_step_is_read_from_disk_at_start(store):
    from autowright.engine import Engine

    sid = add_secret(store, "DRAFT_TOKEN")
    keychain.set_secret(sid, "draft-token-value")
    a = store.create_automation(make_version(), "Draft disk", None)
    store.patch_automation(a, {"allowedSecrets": [sid]})
    store.save_draft(a, make_version())
    draft_script = store.auto_dir(a) / "draft" / "automation" / "01-say.py"
    loaded_code = a["draft"]["steps"][0]["code"]
    draft_script.write_text("from autowright import log, secrets\n"
                            f'value = secrets["{sid}"]\n'
                            'log(f"draft token length {len(value)}")\n', encoding="utf-8")
    engine = Engine(store)
    h = engine.start(a, "manual", version_label="Draft")
    wait_done(engine, h["id"])
    assert h["status"] == "succeeded"  # the on-disk subscript was injected
    assert any("draft token length 17" in line["text"] for line in read_all_logs(store, h["id"]))
    # no seal for a draft: nothing flagged, the loaded draft untouched
    assert "modifiedOnDisk" not in a["draft"]["steps"][0]
    assert a["draft"]["steps"][0]["code"] == loaded_code


def test_staleness_compare_accepts_a_crlf_written_step():
    from autowright import testexec
    from autowright.storage import step_script_sha

    code = "print('a')\nprint('b')\n"
    current = [{"code": code}]
    cur_shas = [(testexec._step_sha(s), testexec._crlf_step_sha(s)) for s in current]
    record = {"id": "x", "status": "succeeded", "kind": "draft",
              "steps": [{"sha": step_script_sha(code.replace("\n", "\r\n").encode())}]}
    block = testexec._execution_block(record, cur_shas, False, 0, {}, None)
    assert "steps match the current draft" in block
    record["steps"][0]["sha"] = step_script_sha(b"print('other')\n")
    block = testexec._execution_block(record, cur_shas, False, 0, {}, None)
    assert "ran older steps" in block


# ---------- §7: a retry pass that sets no chip keeps the record's chip ----------

def test_retry_status_only_keeps_the_chip(store):
    from autowright.engine import Engine

    engine = Engine(store)
    ver = make_version()
    ver["steps"] = [
        {"file": "01-chip.py", "name": "Chip", "description": "",
         "code": 'from autowright import result\nresult.status("attention")\n'
                 'result.chip("Look at this")\n'},
        {"file": "02-flaky.py", "name": "Flaky", "description": "",
         "code": 'import os\nfrom autowright import result\n'
                 'assert os.path.exists("flag"), "flaky"\nresult.status("ok")\n'},
    ]
    a = store.create_automation(ver, "Chip status only", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    assert h["status"] == "failed"
    (store.exec_dir(h["id"]) / "workspace" / "flag").write_text("ok")
    h2 = engine.retry(a, h)
    wait_done(engine, h2["id"])
    assert h2["status"] == "succeeded"
    assert h2["chip"] == "Look at this"
    assert h2["chip_status"] == "attention"


# ---------- §7: pass durations on the monotonic clock ----------

def test_sum_pass_uses_the_monotonic_difference(monkeypatch):
    from autowright import engine as engine_mod
    from autowright.engine import Engine

    wall = 1_800_000_000.0
    state = {"pass_start": wall, "pass_start_monotonic": 100.0}
    h = {"duration_ms": None, "_pass_start": wall}
    monkeypatch.setattr(engine_mod.time, "time", lambda: wall - 3600)  # clock stepped back
    monkeypatch.setattr(engine_mod.time, "monotonic", lambda: 100.75)
    Engine._sum_pass(object.__new__(Engine), h, state)
    assert h["duration_ms"] == 750
    assert state["pass_start"] is None and state["pass_start_monotonic"] is None


def test_pass_elapsed_never_negative_without_a_monotonic_start(monkeypatch):
    from autowright import engine as engine_mod

    wall = 1_800_000_000.0
    monkeypatch.setattr(engine_mod.time, "time", lambda: wall - 60)
    assert engine_mod._pass_elapsed_ms({"pass_start": wall}) == 0


# ---------- §6.2: a pip install that provides no module ----------

def test_install_without_the_declared_module_fails(home, monkeypatch):
    from autowright import packages

    calls = []

    def fake_pip(name, pin_installed=False, should_stop=None, **_):
        calls.append(name)
        d = home / "site-packages" / f"{name}-1.0.0.dist-info"
        d.mkdir(parents=True, exist_ok=True)
        (d / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0.0\n", encoding="utf-8")
        import importlib
        importlib.invalidate_caches()
        return None

    monkeypatch.setattr(packages, "_pip_install", fake_pip)
    out = packages.ensure([{"pip": "wrongdist", "import": "rightmodule"}])
    assert calls == ["wrongdist"]
    assert out == [{"pip": "wrongdist", "import": "rightmodule", "status": "failed",
                    "error": "distribution wrongdist has no module rightmodule"}]
