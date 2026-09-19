"""§19 backend-audit regressions (2026-09-18): the liveness routes on the
event loop, the §3 shutdown running off it, the §7 execution-detail deep copy,
the §5 secret-delete order, the §11 test scratch under the app's own storage,
and the §19 install refusals' own words."""
import asyncio
import inspect
import threading
import time

from conftest import make_version


def _endpoint(path: str):
    """The function FastAPI actually calls for a route — the §19 handler."""
    from autowright import api

    route = next(r for r in api.app.routes if getattr(r, "path", None) == path)
    return route.endpoint


# ---------- §19: /health and /instructions are served on the event loop ----------

def test_health_and_instructions_are_async_handlers(client):
    """§19: both are served on the event loop, never on the request worker
    pool — a pool saturated by long blocking routes (package installs,
    imports, probes) must not make the liveness probe time out."""
    assert inspect.iscoroutinefunction(_endpoint("/health"))
    assert inspect.iscoroutinefunction(_endpoint("/instructions"))

    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["app"] == "Autowright"
    assert set(r.json()) == {"version", "app", "os", "capabilities"}
    assert set(client.get("/instructions").json()) == {"framework", "build"}


def test_instructions_serves_constants_read_once_at_import(client):
    """§19: the handler stays non-blocking because both §8 files were read at
    import — it only resolves the per-OS placeholders on the constants."""
    from autowright import drafting

    assert "{{MACHINE}}" not in client.get("/instructions").json()["framework"]
    assert drafting.CONTRACT_PREAMBLE and drafting.BUILD_INSTRUCTIONS


# ---------- §3: the shutdown work runs on a worker thread ----------

def test_shutdown_quiesce_never_blocks_the_event_loop(home, monkeypatch):
    """§3: "Both halves and the kill sweeps run on a worker thread
    (run_in_threadpool), never on the event loop" — a listener's gateway close
    can take its library's full close timeout, and the loop has to keep
    serving while it does."""
    from autowright import api
    from autowright.storage import store

    store.load_all()
    returned = threading.Event()

    def slow_quiesce() -> None:
        time.sleep(0.3)
        returned.set()

    monkeypatch.setattr(api, "_startup_callbacks", [])
    monkeypatch.setattr(api, "_quiesce_callbacks", [slow_quiesce])
    monkeypatch.setattr(api, "_shutdown_callbacks", [])
    monkeypatch.setattr(api.engine, "kill_all_live", lambda: None)
    monkeypatch.setattr(api.draft_jobs, "kill_all_building", lambda: None)

    ticks: list[str] = []

    async def drive() -> None:
        lifespan = api._lifespan(api.app)
        await lifespan.__aenter__()
        shutdown = asyncio.create_task(lifespan.__aexit__(None, None, None))
        await asyncio.sleep(0.05)  # the loop is still free while the callback sleeps
        ticks.append("loop alive")
        assert not returned.is_set(), "the callback finished before the loop got a turn"
        await shutdown
        assert returned.is_set()

    asyncio.run(drive())
    assert ticks == ["loop alive"]


# ---------- §7: a live record's steps are copied whole before serialization ----------

def test_execution_detail_deep_copies_live_steps_under_the_lock(client, monkeypatch):
    """§7/§19: the engine thread mutates a live record's steps and attempts
    while the route serializes them — the copy taken under the lock has to
    reach the attempts too, or the page can read a step half-written."""
    from autowright.storage import store

    a = store.create_automation(make_version(), "Live detail", "mock")
    steps = [{"name": "Say hello", "file": "01-say.py", "agent": False, "sha": "x",
              "status": "executing", "duration_ms": None,
              "attempts": [{"number": 1, "status": "executing",
                            "started_at": "2026-09-18T10:00:00", "duration_ms": None}]}]
    h = store.create_execution(a, "version", 1, "manual", steps)
    assert "steps" in store.execs[h["id"]], "a live record keeps its steps on the header"

    seen: dict = {}
    real_exec_json = store.exec_json

    def capture(header, full=False):
        seen["steps"] = header["steps"]
        return real_exec_json(header, full=full)

    monkeypatch.setattr(store, "exec_json", capture)
    assert client.get(f"/executions/{h['id']}").status_code == 200

    live = store.execs[h["id"]]
    assert seen["steps"] is not live["steps"]
    assert seen["steps"][0] is not live["steps"][0]
    assert seen["steps"][0]["attempts"][0] is not live["steps"][0]["attempts"][0]
    assert seen["steps"][0]["attempts"][0] == live["steps"][0]["attempts"][0]


# ---------- §5: a secret delete drops the row before the Keychain item ----------

def test_secret_delete_removes_the_row_before_the_keychain_item(client, monkeypatch):
    """§5: "Every secret-delete path … removes the `secrets.yaml` row first and
    the Keychain item second" — the harmless leftover after a crash between
    the two is an orphan Keychain item, never a row claiming a value that is
    gone."""
    from autowright import keychain
    from autowright.storage import store

    r = client.post("/secrets", json={"name": "TOKEN", "value": "v"})
    secret_id = r.json()["id"]
    rows_when_keychain_ran: list = []

    def record(sid):
        rows_when_keychain_ran.append([s["id"] for s in store.secrets])

    monkeypatch.setattr(keychain, "delete_secret", record)
    assert client.delete(f"/secrets/{secret_id}").status_code == 200
    assert rows_when_keychain_ran == [[]]  # the row was already gone
    assert store.secrets == []


def test_delete_all_secrets_removes_the_rows_before_the_keychain_items(client, monkeypatch):
    """§5: the §3 reset's delete-all follows the same order."""
    from autowright import keychain
    from autowright.storage import store

    for name in ("ONE", "TWO"):
        client.post("/secrets", json={"name": name, "value": "v"})
    rows_when_keychain_ran: list = []

    def record(sid):
        rows_when_keychain_ran.append([s["id"] for s in store.secrets])

    monkeypatch.setattr(keychain, "delete_secret", record)
    assert client.delete("/secrets").json() == {"deleted": 2}
    assert rows_when_keychain_ran == [[], []]
    assert store.secrets == []


# ---------- §11: the draft test's scratch lives under the app's own storage ----------

def _draft():
    return {"steps": [{"file": "01-ok.py", "name": "Ok", "description": "",
                       "code": "from autowright import result\nresult.status('ok')\n"}],
            "params": [], "packages": [], "spec": []}


def test_test_scratch_is_created_under_the_app_storage(client, monkeypatch):
    """§11: "copied to a scratch dir under the app's own storage
    (`<app-support>/tests/<execution id>/`, never the OS temp dir)"."""
    from autowright import api, paths, testexec

    monkeypatch.setattr(testexec, "_run", lambda *a, **kw: None)  # keep the record parked
    execution_id = testexec.start(api.engine, _draft(), None, [], [], {})
    try:
        scratch = paths.tests_scratch_dir() / execution_id
        assert (scratch / "memory").is_dir()
        assert scratch.parent == paths.app_support() / "tests"
    finally:
        api.engine._live.pop(execution_id, None)


def test_clear_scratch_sweeps_what_a_crash_left_behind(home):
    """§11: "swept at backend startup like the §8 harness scratch", so a crash
    mid-test never leaves a gigabyte memory copy behind."""
    from autowright import paths, testexec

    leftover = paths.tests_scratch_dir() / "dead-execution" / "memory"
    leftover.mkdir(parents=True)
    (leftover / "notes.md").write_text("from a crashed test")

    testexec.clear_scratch()

    assert paths.tests_scratch_dir().is_dir()
    assert list(paths.tests_scratch_dir().iterdir()) == []


def test_backend_startup_sweeps_the_test_scratch(home):
    """§11: the sweep runs in the lifespan startup, beside the §8 harness one."""
    from fastapi.testclient import TestClient

    from autowright import api, paths
    from autowright.storage import store

    store.load_all()
    leftover = paths.tests_scratch_dir() / "dead-execution"
    leftover.mkdir(parents=True)
    with TestClient(api.app):
        pass
    assert not leftover.exists()


def test_log_tail_reads_only_the_tail(client, monkeypatch):
    """§5/§8: the RECENT EXECUTIONS tail is read as a tail — parsing a whole
    multi-megabyte step log to keep its last lines is the cost `tail` exists
    to avoid."""
    from autowright import testexec
    from autowright.storage import store

    seen: dict = {}

    def fake_read_log(execution_id, step_idx=None, attempt=None, tail=None, since=None):
        seen["tail"] = tail
        return [{"text": "last line"}]

    monkeypatch.setattr(store, "read_log", fake_read_log)
    h = {"id": "e1", "steps": [{"attempts": [{"number": 3}]}]}
    assert testexec._log_tail(h, 0) == ["last line"]
    assert seen["tail"] == testexec.LOG_TAIL


# ---------- §19: the install refusals answer in their own words ----------

def test_install_409_names_an_abandoned_phase_still_finishing(client, monkeypatch):
    """§19: 409 "while one is already running for the same id — including
    while an *abandoned* phase's worker thread is still finishing its
    filesystem work after the give-up"; a retry must never race the abandoned
    run's staged move of the same bundle."""
    from autowright import installer

    monkeypatch.setattr(installer, "_jobs", {})
    monkeypatch.setattr(installer, "_phases", {})
    monkeypatch.setattr(installer, "INSTALL_TIMEOUT_S", 0.05)
    release = threading.Event()
    monkeypatch.setitem(installer._INSTALLERS, "claude",
                        lambda emit: release.wait(10))

    assert client.post("/agents/install", json={"id": "claude"}).json() == {"ok": True}
    # the phase outlives the give-up: the job fails, the worker keeps going
    deadline = time.time() + 5
    while installer.status("claude")["state"] != "failed":
        assert time.time() < deadline, "the give-up never landed"
        time.sleep(0.02)

    r = client.post("/agents/install", json={"id": "claude"})
    assert r.status_code == 409
    assert r.json()["detail"] == "the previous install is still finishing — try again in a minute"

    release.set()
    deadline = time.time() + 5
    while "claude" in installer._phases:
        assert time.time() < deadline, "the phase never cleared its claim"
        time.sleep(0.02)
    # once the phase is really over a retry is allowed again
    monkeypatch.setitem(installer._INSTALLERS, "claude", lambda emit: None)
    assert client.post("/agents/install", json={"id": "claude"}).json() == {"ok": True}


def test_install_409_names_a_running_install(client, monkeypatch):
    """§19: the ordinary refusal keeps its own words."""
    from autowright import installer

    monkeypatch.setattr(installer, "_jobs", {})
    monkeypatch.setattr(installer, "_phases", {})
    release = threading.Event()
    monkeypatch.setitem(installer._INSTALLERS, "claude", lambda emit: release.wait(10))
    try:
        assert client.post("/agents/install", json={"id": "claude"}).json() == {"ok": True}
        r = client.post("/agents/install", json={"id": "claude"})
        assert r.status_code == 409
        assert r.json()["detail"] == "an install for this provider is already running"
    finally:
        release.set()


# ---------- §19: the no-free-slot 409 is the only one carrying a reason ----------

def test_execute_capacity_409_carries_reason_capacity(client):
    """§19: "That no-free-slot 409 is the only one whose body carries
    `reason: "capacity"` beside `detail`" — the §7 busy toast keys on it."""
    from autowright.storage import store

    a = store.create_automation(make_version(), "Busy", "mock")
    a["_live"] = {"blocking"}  # §6 at_capacity reads _live
    try:
        r = client.post(f"/automations/{a['id']}/execute", json={})
        assert r.status_code == 409
        assert r.json() == {"detail": "already executing", "reason": "capacity"}
    finally:
        a["_live"] = set()


def test_other_execute_409s_carry_detail_alone(client):
    """§19: every other 409 this route answers (a full queue, a Draft queue
    attempt, the backend shutting down) carries `detail` alone."""
    from autowright.storage import store

    a = store.create_automation(make_version(), "Full queue", "mock")
    a["max_queued"] = 1
    a["_live"] = {"blocking"}
    try:
        assert client.post(f"/automations/{a['id']}/execute",
                           json={"queue": True}).status_code == 200
        r = client.post(f"/automations/{a['id']}/execute", json={"queue": True})
        assert r.status_code == 409
        assert "the queue is full (1 waiting)" in r.json()["detail"]
        assert "reason" not in r.json()

        draft = client.post(f"/automations/{a['id']}/execute",
                            json={"queue": True, "version": "draft"})
        assert draft.status_code == 409 and "reason" not in draft.json()
    finally:
        a["_live"] = set()
