"""§19 backend-audit regressions (2026-09-09): lock discipline on the hot API
paths, the delete/retry/pull guards, and the additive log + event surfaces."""
import io
import threading
import time
import zipfile

import pytest

from conftest import make_version


def _lock_held():
    """Whether this thread currently owns store.lock (RLock introspection)."""
    from autowright.storage import store

    return store.lock._is_owned()


# ---------- §19 GET /executions: serialize outside the lock ----------

def test_executions_page_serializes_outside_the_store_lock(client, monkeypatch):
    """§19: the matched headers are copied under the lock and serialized
    outside it — exec_json for a long history must never hold the engine off."""
    from autowright.storage import store

    a = store.create_automation(make_version(), "Held", "mock")
    store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    held = []
    real = store.exec_json
    monkeypatch.setattr(store, "exec_json",
                        lambda h, full=False: (held.append(_lock_held()), real(h, full=full))[1])
    body = client.get("/executions").json()
    assert body["total"] == 2 and len(body["executions"]) == 2
    assert held == [False, False]


# ---------- §4.8 usedBy: one index pass per /secrets ----------

def test_secrets_usedby_scans_each_step_once(client):
    """One `SECRET_REF_RE` scan per step per call, not one per (secret × step)."""
    from autowright import api
    from autowright.storage import store

    class _CountingPattern:
        def __init__(self, real):
            self.real, self.calls = real, 0

        def findall(self, text):
            self.calls += 1
            return self.real.findall(text)

    for name in ("ALPHA", "BETA", "GAMMA"):
        assert client.post("/secrets", json={"name": name, "value": "x"}).status_code == 200
    user = store.create_automation(make_version(), "User", "mock")  # 2 steps
    store.create_automation(make_version(), "Other", "mock")        # 2 steps
    assert user["versions"][1]["steps"]

    counting = _CountingPattern(api.SECRET_REF_RE)
    api.SECRET_REF_RE = counting
    try:
        listed = client.get("/secrets").json()
    finally:
        api.SECRET_REF_RE = counting.real
    assert [s["name"] for s in listed] == ["ALPHA", "BETA", "GAMMA"]
    # 2 automations × 2 steps — not 3 secrets × 4 steps
    assert counting.calls == 4


# ---------- §19 DELETE /automations: the _deleting flag is cleared on failure ----------

def test_failed_delete_clears_the_deleting_flag(client, monkeypatch):
    """§19: a delete that fails partway must not leave the automation silently
    refusing every firing — the flag goes back and admissions resume."""
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Fragile", "mock")
    a["_live"] = {"ghost"}  # forces the wait below to run
    monkeypatch.setattr(api.engine, "wait_finished",
                        lambda ids, timeout=None: (_ for _ in ()).throw(RuntimeError("disk gone")))
    with pytest.raises(RuntimeError):
        client.delete(f"/automations/{a['id']}")
    monkeypatch.undo()  # the wait is real again for the admission check below
    assert not a.get("_deleting")
    assert a["id"] in store.autos
    a["_live"] = set()
    # engine.start refuses while the flag is set — it is admitted again now.
    h = api.engine.start(a, "manual")
    assert h["id"]
    api.engine.cancel(h["id"])
    api.engine.wait_finished([h["id"]])


# ---------- §19 POST /executions/{id}/retry: every test record is a 409 ----------

def test_retry_of_an_edit_mode_test_record_is_409(client):
    """§19: retry is refused for every §4.5 test record, edit mode included —
    the draft it ran may have changed. 409, never a 404."""
    from autowright.storage import store

    a = store.create_automation(make_version(), "Edited", "mock")
    h = store.create_execution(a, "test", None, "test", [], status="failed")
    assert h["automation_id"] == a["id"]
    r = client.post(f"/executions/{h['id']}/retry")
    assert r.status_code == 409
    assert "execute a new test" in r.json()["detail"]


# ---------- §6.3 snapshots: delete + rename serialize on memory_ops ----------

class _RecordingLock:
    """A real lock that counts its acquisitions (`store.lock` / `memory_ops`)."""

    def __init__(self, real):
        self.real, self.entered = real, 0

    def __enter__(self):
        self.entered += 1
        return self.real.__enter__()

    def __exit__(self, *exc):
        return self.real.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self.real, name)


def test_snapshot_delete_and_rename_hold_the_memory_ops_lock(client, monkeypatch):
    """§6.3: every snapshot mutation serializes on one memory-operations lock."""
    from autowright.storage import store

    a = store.create_automation(make_version(), "Remembers", "mock")
    mem = store.auto_dir(a) / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "notes.txt").write_text("remembered", encoding="utf-8")
    created = client.post(f"/automations/{a['id']}/memory/snapshots", json={"name": "keep"})
    assert created.status_code == 200
    snapshot_id = created.json()["snapshot"]["id"]

    recording = _RecordingLock(store.memory_ops)
    monkeypatch.setattr(store, "memory_ops", recording)
    assert client.patch(f"/automations/{a['id']}/memory/snapshots/{snapshot_id}",
                        json={"name": "renamed"}).status_code == 200
    assert recording.entered == 1
    assert client.delete(f"/automations/{a['id']}/memory/snapshots/{snapshot_id}").status_code == 200
    assert recording.entered == 2


# ---------- §3 stale-execution repair: the orphan kills run off the lock ----------

def test_stale_repair_kills_orphans_off_the_store_lock(client, monkeypatch):
    """§3: the pid-reuse guard shells out to the process table — that must
    never happen while store.lock is held."""
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Crashed", "mock")
    h = store.create_execution(a, "version", 1, "manual",
                               [{"name": "Say hello", "file": "01-say.py", "status": "executing"}])
    h["pgid"] = 424242
    h["agent_pgids"] = [424243]
    store.update_execution(h)

    held = []
    monkeypatch.setattr(api, "kill_orphan_group", lambda pgid: held.append(("group", pgid, _lock_held())))
    monkeypatch.setattr(api, "kill_orphan_agent_group",
                        lambda pgid: held.append(("agent", pgid, _lock_held())))
    api._repair_stale_executing()
    assert held == [("group", 424242, False), ("agent", 424243, False)]
    assert store.execs[h["id"]]["status"] == "interrupted"
    repaired = store.exec_full(h["id"])
    # the freed slot: no group left to kill, and the live step is interrupted too
    assert repaired["pgid"] is None and repaired["agent_pgids"] == []
    assert repaired["steps"][0]["status"] == "interrupted"


# ---------- §5 request-log gate: no file IO on the event loop ----------

def test_request_log_gate_reads_no_file(client, monkeypatch):
    """§5: the middleware answers the developerMode gate from the in-memory
    settings map — `load_yaml` never runs on the event loop."""
    from autowright import reqlog

    read = []
    real = reqlog.load_yaml
    monkeypatch.setattr(reqlog, "load_yaml", lambda p: (read.append(p), real(p))[1])
    reqlog._dev_cache["t"] = 0.0  # a file-backed gate would refill the cache here
    assert client.get("/state").status_code == 200
    assert read == []
    # The passed mapping is the whole answer; the executor subprocess passes none.
    assert reqlog.enabled({"developerMode": True}) is True
    assert reqlog.enabled({}) is False


# ---------- §19 Ollama pull: one per model, capped, killed at shutdown ----------

@pytest.fixture()
def pull_state():
    """§19 pull bookkeeping is module-global — never leak it between tests."""
    from autowright import api

    api._pulling.clear()
    api._pull_procs.clear()
    yield
    api._pulling.clear()
    api._pull_procs.clear()


def test_second_pull_of_the_same_model_is_409(client, monkeypatch, pull_state):
    from autowright import api, harness

    release = threading.Event()
    monkeypatch.setattr(harness, "_ollama_models", lambda: [])
    monkeypatch.setattr(api, "_ollama_pull_http", lambda model: release.wait(10))
    try:
        assert client.post("/ollama/pull", json={"model": "llama3"}).status_code == 200
        second = client.post("/ollama/pull", json={"model": "llama3"})
        assert second.status_code == 409 and second.json()["detail"] == "already pulling"
        # a different model is unaffected — the guard is per model
        assert client.post("/ollama/pull", json={"model": "qwen3"}).status_code == 200
    finally:
        release.set()
    deadline = time.time() + 10
    while api._pulling and time.time() < deadline:
        time.sleep(0.02)
    assert not api._pulling
    # …and the model is pullable again once the first one is done
    assert client.post("/ollama/pull", json={"model": "llama3"}).status_code == 200
    release.set()


def test_pull_past_the_wall_clock_cap_publishes_a_failed_terminal_event(monkeypatch, pull_state):
    """§19: past the cap the pull ends `ok: false` with a "pull timed out" line —
    the §12 card watches for the terminal event and would pull forever without it."""
    import urllib.request

    from autowright import api
    from autowright.events import hub

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __iter__(self):
            while True:  # a server dribbling progress lines forever
                yield b'{"status": "pulling manifest"}'

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: _Stream())
    monkeypatch.setattr(api, "PULL_DEADLINE_S", 0)  # the deadline is already past
    published = []
    monkeypatch.setattr(hub, "publish", lambda ev, **kw: published.append({"event": ev, **kw}))
    api._ollama_pull_http("llama3")
    terminal = [e for e in published if e["event"] == "ollama.pull" and e["done"]]
    assert len(terminal) == 1
    assert terminal[0]["ok"] is False and terminal[0]["line"] == "pull timed out"


def test_shutdown_kills_a_tracked_pull_child(monkeypatch, pull_state):
    """§3: a CLI-mode pull child is killed by group with the rest of the sweep."""
    from autowright import api
    from autowright.platform import posixproc, windows

    class _FakeProc:
        pid = 4242

        def poll(self):
            return None

    killed = []
    for control in (posixproc.PosixProcessControl, windows.WindowsProcessControl):
        monkeypatch.setattr(control, "signal_group",
                            lambda self, proc, sig=None: killed.append(proc))
    proc = _FakeProc()
    api._pull_procs.add(proc)
    api._kill_pull_procs()
    assert killed == [proc] and not api._pull_procs


# ---------- §5.2 import spool: swept on every preview and every confirm ----------

def test_confirm_sweeps_expired_spool_files(client):
    """§5.2: an expired parked archive leaves disk at the next confirm — not
    only when the next preview happens to come along."""
    from autowright import api

    token = api._park_archive(b"stale archive bytes")
    parked_at, path = api._import_parked[token]
    assert path.exists()
    api._import_parked[token] = (parked_at - api._IMPORT_TTL - 1, path)
    # an unrelated confirm: unknown token, but the sweep still runs
    assert client.post("/automations/import/confirm", json={"token": "nope"}).status_code == 404
    assert token not in api._import_parked and not path.exists()


# ---------- §19: no unlocked store walks ----------

def _guarded(items, seen):
    """A store collection that records whether store.lock was held per walk."""

    class _Guarded(list):
        def __iter__(self):
            seen.append(_lock_held())
            return list.__iter__(self)

    return _Guarded(items)


def test_store_collection_walks_run_under_the_lock(client, monkeypatch):
    """§19: every helper that walks store.agents / store.secrets takes the lock —
    an unlocked walk hits "dictionary changed size during iteration" when the
    scheduler or engine writes mid-request."""
    from autowright import api
    from autowright.storage import store

    seen = []
    monkeypatch.setattr(store, "agents", _guarded(list(store.agents), seen))
    monkeypatch.setattr(store, "secrets", _guarded(list(store.secrets), seen))
    lock = _RecordingLock(store.lock)
    monkeypatch.setattr(store, "lock", lock)

    # _validate_draft_steps
    api._validate_draft_steps({"steps": [{"file": "01-say.py", "name": "Say", "code": "x = 1\n"}]})
    # post_test
    monkeypatch.setattr(api.testexec, "start", lambda *a, **kw: "test-1")
    assert client.post("/tests", json={"draft": {"steps": [
        {"file": "01-echo.py", "name": "Echo", "description": "", "code": "x = 1\n"}]}}).status_code == 200
    # post_draft
    monkeypatch.setattr(api.draft_jobs, "start", lambda *a, **kw: "job-1")
    assert client.post("/drafts", json={"mode": "chat", "text": "hello"}).status_code == 200

    assert seen and all(seen)
    assert lock.entered


# ---------- §5.2 download: the whole-download deadline ----------

def test_url_import_times_out_on_a_trickling_server(client, monkeypatch):
    """§5.2: the per-read timeout can't catch a server sending one byte at a
    time — the whole-download deadline can, and it lands as a 422."""
    import urllib.request

    from autowright import transfer

    class _Trickle:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def geturl(self):
            return "https://example.test/pack.autowright"

        def read(self, _n):
            return b"x"

    monkeypatch.setattr(transfer, "resolve_url", lambda url: "https://example.test/pack.autowright")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **kw: _Trickle())
    monkeypatch.setattr(transfer, "FETCH_DEADLINE_S", 0)  # the deadline is already past
    r = client.post("/automations/import/url",
                    json={"url": "https://example.test/pack.autowright"})
    assert r.status_code == 422 and "timed out" in r.json()["detail"]


# ---------- §5.1 import caps: entry count ----------

def test_archive_with_too_many_entries_is_rejected(client):
    """§5.1: the central directory is materialized before any size check can
    run — the entry count is capped at 1,000 members."""
    from autowright import transfer

    def _zip(count):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for i in range(count):
                z.writestr(f"padding/{i}.txt", "")
        return buf.getvalue()

    with pytest.raises(transfer.TransferError) as err:
        transfer.preview_archive(None, _zip(1001))
    assert "far more files" in str(err.value)
    r = client.post("/automations/import/preview", content=_zip(1001))
    assert r.status_code == 422 and "far more files" in r.json()["detail"]
    # under the cap the entry count is not what rejects it
    under = client.post("/automations/import/preview", content=_zip(1000))
    assert under.status_code == 422 and "far more files" not in under.json()["detail"]


# ---------- §19 POST /drafts: no first-agent fallback ----------

def test_draft_job_404_when_no_default_agent_resolves(client):
    """§19: the job's agent is the explicit agentId, else the default — 404 when
    neither resolves. The §5 load path already repoints a dangling stored
    default at the first agent, so there is no fallback left to make here."""
    from autowright.storage import store

    store.default_agent_id = None  # agents exist, but nothing points at one
    r = client.post("/drafts", json={"mode": "chat", "text": "hello"})
    assert r.status_code == 404
    store.agents = []
    assert client.post("/drafts", json={"mode": "chat", "text": "hello"}).status_code == 404


# ---------- §19 execution.deleted ----------

def test_settling_a_draft_publishes_execution_deleted(client, monkeypatch):
    """§19: a settling draft's §4.5 test rows leave the §7 list live."""
    from autowright import api
    from autowright.storage import store

    published = []
    monkeypatch.setattr(api.hub, "publish", lambda ev, **kw: published.append({"event": ev, **kw}))
    monkeypatch.setattr(store, "delete_draft", lambda a: ["exec-1", "exec-2"])
    assert client.delete("/draft/pending").status_code == 200
    assert [e["executionId"] for e in published if e["event"] == "execution.deleted"] \
        == ["exec-1", "exec-2"]


def test_starting_a_test_publishes_execution_deleted_for_the_superseded_record(client, monkeypatch):
    """§11 keep-latest: the replaced test record's row is dropped live."""
    from autowright import api
    from autowright.storage import store

    published = []
    monkeypatch.setattr(api.hub, "publish", lambda ev, **kw: published.append({"event": ev, **kw}))
    monkeypatch.setattr(store, "delete_test_execs", lambda automation_id: ["old-test"])
    r = client.post("/tests", json={"draft": {"name": "Quick", "steps": [
        {"file": "01-ok.py", "name": "Ok", "description": "",
         "code": "from autowright import result\nresult.status('ok')\n"}]}})
    assert r.status_code == 200
    execution_id = r.json()["executionId"]
    deadline = time.time() + 30
    while time.time() < deadline:
        if any(e["event"] == "execution.finished" and e["executionId"] == execution_id
               for e in published):
            break
        time.sleep(0.05)
    assert [e["executionId"] for e in published if e["event"] == "execution.deleted"] == ["old-test"]


# ---------- §19 GET /executions/{id}/logs?sinceSequence= ----------

def test_log_since_sequence_filters_before_the_tail(client):
    """§19: `sinceSequence` keeps only lines past that per-file sequence, and
    `tail` applies AFTER it — a tail taken first would hand back fewer new
    lines than the §20 follow loop asked for."""
    import json as jsonlib

    from autowright.storage import store

    a = store.create_automation(make_version(), "Logged", "mock")
    h = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    log_file = store.log_file(h["id"], store.EXEC_LOG)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("".join(
        jsonlib.dumps({"time": "12:00:00", "kind": "out", "sequence": n, "text": f"line {n}"}) + "\n"
        for n in range(1, 6)), encoding="utf-8")

    base = f"/executions/{h['id']}/logs"
    assert [l["sequence"] for l in client.get(base).json()["lines"]] == [1, 2, 3, 4, 5]
    assert [l["sequence"] for l in client.get(f"{base}?sinceSequence=2").json()["lines"]] == [3, 4, 5]
    # tail after the filter: 3,4,5 tailed to 2 — never 4,5 tailed then filtered away
    assert [l["sequence"] for l in
            client.get(f"{base}?sinceSequence=2&tail=2").json()["lines"]] == [4, 5]
    assert client.get(f"{base}?sinceSequence=5").json()["lines"] == []
    assert client.get(f"{base}?sinceSequence=-1").status_code == 422
