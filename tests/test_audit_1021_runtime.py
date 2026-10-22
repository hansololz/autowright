"""Execution-runtime audit regressions (2026-10-21).

§7 a cancel landing after a step exited 0 unsignalled leaves that step
`succeeded`; §7 a defused pipe reader still drains what the pipe holds (bounded
at 1 MiB); §6.1 the redaction set carries each value's JSON/repr forms and its
newline-stripped form; §6.3 a skipped or failed pre-version copy stays owed and
a failed commit discards its staged copy; §6 a queue promotion failing with any
error never strands an `executing` record; §6 an oversized trigger payload never
rides the environment; §8 the RECENT EXECUTIONS `result.md` excerpt is redacted;
§8 the scratch watcher drops a name that became a symlink and strips a BOM.
"""
import os
import threading
import time
import uuid

from conftest import make_version, read_all_logs, wait_done


def _add_secret(store, name: str) -> str:
    secret_id = str(uuid.uuid4())
    store.secrets.append({"id": secret_id, "name": name, "description": "", "set": True})
    return secret_id


def _wait(predicate, timeout: float = 5.0, what: str = "condition") -> None:
    deadline = time.time() + timeout
    while not predicate():
        assert time.time() < deadline, f"{what} never happened"
        time.sleep(0.02)


# ---------- §7: a cancel after a step exited 0 unsignalled leaves it succeeded ----------

def _cancel_after_step(monkeypatch, step_index: int) -> None:
    """Patch the engine so `cancel` lands right after step `step_index`
    (1-based) returned exit 0 — between the executor's exit and the status
    decision — when the step's process is already gone (no signal sent)."""
    from autowright.engine import Engine

    orig = Engine._execute_step

    def wrapped(self, auto, ver, h, s, index, *args, **kwargs):
        rc = orig(self, auto, ver, h, s, index, *args, **kwargs)
        if index == step_index:
            assert rc == 0
            assert self.cancel(h["id"]) is True
        return rc

    monkeypatch.setattr(Engine, "_execute_step", wrapped)


def test_cancel_after_a_non_last_step_exited_zero_keeps_it_succeeded(store, monkeypatch):
    from autowright.engine import Engine

    _cancel_after_step(monkeypatch, 1)
    engine = Engine(store)
    a = store.create_automation(make_version(), "Late per-step cancel", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])

    assert [s["status"] for s in h["steps"]] == ["succeeded", "cancelled"]
    assert h["status"] == "cancelled"


def test_cancel_after_the_last_step_exited_zero_finishes_succeeded(store, monkeypatch):
    from autowright import notify
    from autowright.engine import Engine

    posted: list[tuple[str, str]] = []
    monkeypatch.setattr(notify, "post", lambda title, body: posted.append((title, body)))
    ver = make_version()
    ver["steps"][-1]["code"] = ('from autowright import result\n'
                                'result.status("attention")\nresult.chip("Look at this")\n')
    _cancel_after_step(monkeypatch, len(ver["steps"]))
    engine = Engine(store)
    a = store.create_automation(ver, "Last step late cancel", None)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])

    assert [s["status"] for s in h["steps"]] == ["succeeded", "succeeded"]
    assert h["status"] == "succeeded"
    assert h["chip"] == "Look at this" and h["chip_status"] == "attention"
    _wait(lambda: posted, what="the notification")
    assert posted == [("Last step late cancel", "Look at this")]


# ---------- §7: the defused reader drains what the pipe already holds ----------

def _fill_pipe(write_fd: int) -> int:
    """Write until the pipe is full (non-blocking); the total written."""
    os.set_blocking(write_fd, False)
    total = 0
    line = b"y" * 1023 + b"\n"
    while True:
        try:
            total += os.write(write_fd, line)
        except BlockingIOError:
            return total


def test_a_defused_reader_drains_a_full_pipe_before_eof():
    from autowright import harness

    read_fd, write_fd = os.pipe()
    read_file = os.fdopen(read_fd, "r", encoding="utf-8")
    try:
        written = _fill_pipe(write_fd)  # the write end stays open: no EOF ever
        assert written >= 16 * 1024
        reader = harness.PipeReader(read_file)
        reader.defuse()  # the drain grace ran out with the pipe still full
        received = 0
        while True:
            line = reader.readline()
            if line == "":
                break
            received += len(line.encode("utf-8"))
        assert received == written
        reader.close()
    finally:
        os.close(write_fd)
        read_file.close()


def test_a_defused_reader_stops_draining_at_the_cap():
    from autowright import harness

    read_fd, write_fd = os.pipe()
    read_file = os.fdopen(read_fd, "r", encoding="utf-8")
    stop = threading.Event()

    def writer() -> None:
        chunk = b"z" * 4095 + b"\n"
        try:
            while not stop.is_set():
                os.write(write_fd, chunk)
        except OSError:
            pass

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        reader = harness.PipeReader(read_file)
        _wait(lambda: harness.select.select([read_fd], [], [], 0)[0], what="pipe data")
        reader.defuse()
        received = 0
        started = time.time()
        while True:
            line = reader.readline()
            if line == "":
                break
            received += len(line.encode("utf-8"))
            assert time.time() - started < 20, "the drain never ended"
        assert received <= harness.DEFUSE_DRAIN_CAP
        reader.close()
    finally:
        stop.set()
        read_file.close()  # a writer parked on the full pipe gets EPIPE
        thread.join(5)
        os.close(write_fd)


# ---------- §6.1: the redaction set carries each value's escaped forms ----------

def test_build_redactions_carries_json_repr_and_newline_stripped_forms():
    from autowright.engine import build_redactions

    quoted = 'a\\b"c\'d-quote-value'
    accented = "pässwörd-ünïcode"
    short = "abc12\n"
    redactions = build_redactions({"q": quoted, "u": accented, "s": short},
                                  {"q": "QUOTED", "u": "ACCENTED", "s": "SHORT"})
    assert redactions[repr(quoted)[1:-1]] == "QUOTED"
    import json

    assert redactions[json.dumps(quoted)[1:-1]] == "QUOTED"
    assert redactions[json.dumps(accented)[1:-1]] == "ACCENTED"
    assert redactions["abc12"] == "SHORT"
    assert redactions[quoted] == "QUOTED" and redactions[short] == "SHORT"


def test_a_variant_never_overrides_another_values_whole_entry():
    from autowright.engine import build_redactions

    redactions = build_redactions({"one": "tok\n", "two": "tok"},
                                  {"one": "ONE", "two": "TWO"})
    assert redactions["tok"] == "TWO"


def _run_printing_secret(store, value: str, code: str) -> tuple[dict, list[dict]]:
    from autowright import keychain
    from autowright.engine import Engine

    secret_id = _add_secret(store, "TOKEN")
    keychain.set_secret(secret_id, value)
    ver = make_version()
    ver["steps"][0]["code"] = (f'import json\nfrom autowright import secrets\n'
                               f'v = secrets["{secret_id}"]\n' + code)
    a = store.create_automation(ver, f"Leak {uuid.uuid4().hex[:6]}", None)
    store.patch_automation(a, {"allowedSecrets": [secret_id]})
    engine = Engine(store)
    h = engine.start(a, "manual")
    wait_done(engine, h["id"])
    return h, read_all_logs(store, h["id"])


def test_print_repr_of_a_secret_with_backslash_and_quote_is_redacted(store):
    value = 'k\\ey"with\'quotes-77'
    h, logs = _run_printing_secret(store, value, "print(repr(v))\n")
    assert h["status"] == "succeeded"
    text = "\n".join(l["text"] for l in logs)
    assert repr(value)[1:-1] not in text and "quotes-77" not in text
    assert "•••" in text
    assert "TOKEN" in h["redacted_secrets"]


def test_json_dumps_of_a_non_ascii_secret_is_redacted(store):
    value = "clé-sécrète-ünï"
    h, logs = _run_printing_secret(store, value, "print(json.dumps({'k': v}))\n")
    assert h["status"] == "succeeded"
    text = "\n".join(l["text"] for l in logs)
    import json

    assert json.dumps(value)[1:-1] not in text
    assert "\\u00e9" not in text
    assert "•••" in text


def test_a_short_secret_ending_in_a_newline_is_redacted_without_it(store):
    value = "pw42x\n"
    h, logs = _run_printing_secret(store, value, "print('using', v.strip(), 'now')\n")
    assert h["status"] == "succeeded"
    text = "\n".join(l["text"] for l in logs)
    assert "pw42x" not in text
    assert "using ••• now" in text


# ---------- §6.3: the pre-version snapshot stays owed ----------

def _version_record(store, a):
    ver = a["versions"][a["current_version"]]
    h = store.create_execution(a, "version", a["current_version"], "manual",
                               [{"name": s["name"], "file": s["file"], "agent": False,
                                 "sha": "x", "status": "queued", "duration_ms": None,
                                 "attempts": []} for s in ver["steps"]])
    h["_pre_snapshot"] = f"v{a['current_version']}"  # what `start` would have decided
    return ver, h


def test_a_cancel_before_step_one_leaves_the_pre_version_snapshot_owed(store):
    from autowright.engine import Engine

    engine = Engine(store)
    a = store.create_automation(make_version(), "Owed", None)
    (store.auto_dir(a) / "memory").mkdir(parents=True, exist_ok=True)
    (store.auto_dir(a) / "memory" / "notes.md").write_text("before")
    ver, h = _version_record(store, a)

    engine._execute(a, ver, h, {"proc": None, "cancel": True})
    assert h["status"] == "cancelled"
    assert a["_pre_version_owed"] == {a["current_version"]}
    assert not store.pre_version_snapshot_exists(a, f"v{a['current_version']}")

    h2 = engine.start(a, "manual")
    wait_done(engine, h2["id"])
    assert h2["status"] == "succeeded"
    assert store.pre_version_snapshot_exists(a, f"v{a['current_version']}")
    assert a["_pre_version_owed"] == set()


def test_a_failed_pre_version_commit_discards_its_staged_copy_and_stays_owed(store, monkeypatch):
    from autowright.engine import Engine

    engine = Engine(store)
    a = store.create_automation(make_version(), "Commit fails", None)
    (store.auto_dir(a) / "memory").mkdir(parents=True, exist_ok=True)
    (store.auto_dir(a) / "memory" / "notes.md").write_text("before")
    real_commit = store.commit_snapshot

    def failing_commit(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "commit_snapshot", failing_commit)
    ver, h = _version_record(store, a)
    engine._execute(a, ver, h, {"proc": None, "cancel": False})

    assert h["status"] == "failed"
    leftovers = [p.name for p in store.auto_dir(a).iterdir()
                 if p.name.startswith(store.SNAPSHOT_STAGE_PREFIX)]
    assert leftovers == []
    assert a["_pre_version_owed"] == {a["current_version"]}

    monkeypatch.setattr(store, "commit_snapshot", real_commit)
    h2 = engine.start(a, "manual")
    wait_done(engine, h2["id"])
    assert store.pre_version_snapshot_exists(a, f"v{a['current_version']}")


# ---------- §6: a promotion failing with any error never strands the head ----------

def test_a_promotion_raising_oserror_lands_the_head_and_frees_the_slot(store, monkeypatch):
    from autowright.engine import Engine
    from autowright.firing import drain_queue, queue_manual

    engine = Engine(store)
    a = store.create_automation(make_version(), "Promotion fails", None)
    a["max_queued"] = 10
    a["_live"] = {"blocking"}
    head, queued = queue_manual(store, engine, a, "manual")
    assert queued is True
    a["_live"] = set()

    def broken_promote(h, steps, params=None):
        h["status"] = "executing"  # the in-memory flip lands before the write fails
        raise OSError("No space left on device")

    monkeypatch.setattr(store, "promote_execution", broken_promote)
    drain_queue(store, engine, a["id"])

    assert head["status"] == "skipped"
    assert head["note"] == "the execution couldn't be started"
    assert not engine.is_live(head["id"])
    assert head["id"] not in a["_live"]
    assert not engine.at_capacity(a)


# ---------- §6: an oversized trigger payload never rides the environment ----------

def _payload_run(store, payload: dict) -> list[str]:
    from autowright.engine import Engine

    ver = make_version()
    ver["steps"][0]["code"] = (
        'import os\nfrom autowright import execution, log\n'
        'log("env=" + str("AUTOWRIGHT_TRIGGER_PAYLOAD" in os.environ))\n'
        'log("len=" + str(len(execution.trigger_payload["text"])))\n')
    a = store.create_automation(ver, f"Payload {uuid.uuid4().hex[:6]}", None)
    engine = Engine(store)
    h = engine.start(a, "discord", payload=payload)
    wait_done(engine, h["id"])
    assert h["status"] == "succeeded"
    return [l["text"] for l in read_all_logs(store, h["id"])]


def test_an_oversized_trigger_payload_is_left_out_of_the_environment(store):
    texts = _payload_run(store, {"kind": "discord", "sender": "Dave", "text": "x" * 70_000})
    assert "env=False" in texts
    assert "len=70000" in texts


def test_a_small_trigger_payload_is_still_exported(store):
    texts = _payload_run(store, {"kind": "discord", "sender": "Dave", "text": "hi"})
    assert "env=True" in texts
    assert "len=2" in texts


# ---------- §8: the RECENT EXECUTIONS result.md excerpt is redacted ----------

def test_the_result_md_excerpt_is_redacted_before_the_prompt(store, monkeypatch):
    from autowright import keychain
    from autowright import testexec as tr

    monkeypatch.setattr(tr, "store", store)
    secret_id = _add_secret(store, "API_KEY")
    keychain.set_secret(secret_id, "sk-live-result-secret-99")
    step = {"file": "01-s.py", "name": "S", "description": "", "code": "pass\n"}
    a = store.create_automation(make_version(steps=[step]), "Result leak", None,
                                allowed_secrets=[secret_id])
    h = store.create_execution(a, "version", 1, "manual",
                               [{"name": "S", "file": "01-s.py", "agent": False, "sha": "x",
                                 "status": "succeeded", "duration_ms": 1000, "attempts": []}],
                               status="succeeded")
    result_dir = store.exec_dir(h["id"]) / "result"
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "result.md").write_text("token is sk-live-result-secret-99 ok\n",
                                          encoding="utf-8")

    section = tr.executions_context(a, [step])

    assert "sk-live-result-secret-99" not in section
    assert "token is ••• ok" in section


# ---------- §8: the scratch watcher's symlink and BOM rules ----------

def test_a_document_that_became_a_symlink_is_dropped(tmp_path):
    from autowright import harness

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("private content\n", encoding="utf-8")
    (scratch / "spec.md").write_text("# Spec\n", encoding="utf-8")
    (scratch / "notes.md").write_text("notes\n", encoding="utf-8")
    watcher = harness._ScratchWatcher(scratch, harness.ProgressSink())
    watcher._poll()  # both first seen as regular files

    (scratch / "spec.md").unlink()
    (scratch / "spec.md").symlink_to(outside)

    documents = watcher.documents()
    assert [name for name, _ in documents] == ["notes.md"]
    assert all("private content" not in content for _, content in documents)


def test_a_leading_utf8_bom_is_stripped(tmp_path):
    from autowright import harness

    (tmp_path / "manifest.yaml").write_bytes("﻿steps: []\n".encode("utf-8"))
    watcher = harness._ScratchWatcher(tmp_path, harness.ProgressSink())
    watcher._poll()
    assert watcher.documents() == [("manifest.yaml", "steps: []\n")]
