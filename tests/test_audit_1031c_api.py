"""2026-10-31 third sweep audit batch (API routes): the §19 lone-surrogate
request door and the replacing response encoder, the app-level OSError 500,
the disk-first stale-execution repair, the §6.3 stage/clear/commit order of
the memory clear, the post-create discipline of POST /automations, the §4.9
settings response keys, the parked-import expiry timer, the §4.7 agent-name
key, the shutdown pip/installer sweep, the draft-generation cleanup on delete,
the secret-name fullmatch, and the no-proxy Ollama pull."""
import asyncio
import json
import time

from conftest import make_version

SURROGATE = "text must not contain lone surrogate characters"


def _post_raw(client, url, body, method="post"):
    """Send `body` as JSON text with `\\udXXX` escapes intact: a lone
    surrogate can't be UTF-8 encoded, so it must ride as a JSON escape."""
    return getattr(client, method)(url, content=json.dumps(body).encode("ascii"),
                                   headers={"Content-Type": "application/json"})


# ---------- B: the §19 lone-surrogate door ----------

def test_create_with_a_lone_surrogate_in_the_name_answers_422_and_stores_nothing(client):
    from autowright.storage import store

    r = _post_raw(client, "/automations",
                  {"draft": make_version(), "agentId": "mock", "name": "X\ud800Y"})
    assert r.status_code == 422
    assert r.json()["detail"] == SURROGATE
    assert store.autos == {}


def test_patch_with_a_lone_surrogate_in_the_description_answers_422(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Surrogate patch", "mock")
    before = client.get(f"/automations/{a['id']}").json()["description"]
    r = _post_raw(client, f"/automations/{a['id']}", {"description": "\udce9"}, method="patch")
    assert r.status_code == 422
    assert r.json()["detail"] == SURROGATE
    assert client.get(f"/automations/{a['id']}").json()["description"] == before


def test_a_surrogate_in_a_nested_key_is_refused_too(client):
    draft = make_version()
    draft["steps"][0]["\ud83d"] = 1
    r = _post_raw(client, "/automations", {"draft": draft, "agentId": "mock"})
    assert r.status_code == 422
    assert r.json()["detail"] == SURROGATE


def test_validate_steps_body_with_a_lone_surrogate_answers_422(client):
    draft = make_version()
    draft["steps"][0]["code"] = "print('\udc80')\n"
    r = _post_raw(client, "/validate-steps", {"draft": draft})
    assert r.status_code == 422
    assert r.json()["detail"] == SURROGATE


def test_a_stored_surrogate_still_answers_state(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Stored surrogate", "mock")
    a["name"] = "Bad\ud800Name"  # what an older version could have stored
    r = client.get("/state")
    assert r.status_code == 200
    names = [x["name"] for x in r.json()["automations"]]
    assert len(names) == 1 and names[0].startswith("Bad") and names[0].endswith("Name")


# ---------- Q: one app-level OSError handler ----------

def test_an_escaping_oserror_answers_a_json_500_naming_the_reason(client, monkeypatch):
    from autowright.storage import store

    def full_disk(settings):
        raise OSError(28, "No space left on device", "/x")

    monkeypatch.setattr(store, "save_settings", full_disk)
    r = client.patch("/settings", json={"days": 30})
    assert r.status_code == 500
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == {"detail": "couldn't write to disk — No space left on device: /x"}


def test_a_network_oserror_answers_the_io_error_shape(client, monkeypatch):
    import urllib.error

    from autowright.storage import store

    def unreachable(settings):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(store, "save_settings", unreachable)
    r = client.patch("/settings", json={"days": 30})
    assert r.status_code == 500
    assert r.json() == {
        "detail": "the backend hit an I/O error — <urlopen error connection refused>"}


def test_an_http_exception_is_not_swallowed_by_the_oserror_handler(client):
    r = client.get("/automations/nope")
    assert r.status_code == 404
    assert r.json()["detail"] == "automation not found"


# ---------- C: the stale-execution repair is disk-first per record ----------

def test_repair_skips_a_record_whose_write_fails_and_repairs_the_rest(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Repair Disk", "mock")
    steps = [{"name": "One", "file": "01-one.py", "status": "executing", "attempts": []}]
    first = store.create_execution(a, "version", 1, "manual", [dict(s) for s in steps])
    second = store.create_execution(a, "version", 1, "manual", [dict(s) for s in steps])
    queued = store.create_execution(a, "version", 1, "manual", [dict(s) for s in steps])
    queued["status"] = "queued"
    store.update_execution(queued)
    assert {first["id"], second["id"]} <= a["_live"]
    real = store.update_execution
    failing = {first["id"], queued["id"]}

    def flaky(h):
        if h["id"] in failing:
            raise OSError(28, "No space left on device")
        return real(h)

    monkeypatch.setattr(store, "update_execution", flaky)
    api._repair_stale_executing()  # no exception escapes

    # The second record is repaired, on disk and in memory.
    assert store.execs[second["id"]]["status"] == "interrupted"
    assert store.read_exec_yaml(second["id"])["status"] == "interrupted"
    # The failing ones keep their disk status, and memory never ran ahead.
    assert store.read_exec_yaml(first["id"])["status"] == "executing"
    assert store.execs[first["id"]]["status"] == "executing"
    assert store.read_exec_yaml(queued["id"])["status"] == "queued"
    assert store.execs[queued["id"]]["status"] == "queued"
    # The derived state was refreshed, and the record whose write failed was
    # dropped from the live set too: no engine thread owns it, so it never
    # holds a capacity slot (the next boot repairs it again).
    assert second["id"] not in a["_live"]
    assert first["id"] not in a["_live"]
    assert a["_live"] == set()


def test_repair_refreshes_derived_state_when_the_first_pass_raises(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Repair Refresh", "mock")
    h = store.create_execution(a, "version", 1, "manual",
                               [{"name": "One", "file": "01-one.py", "status": "executing",
                                 "attempts": []}])
    calls = []
    real_refresh = store._refresh_exec_derived

    def counting_refresh():
        calls.append(1)
        real_refresh()

    monkeypatch.setattr(store, "_refresh_exec_derived", counting_refresh)
    monkeypatch.setattr(store, "exec_full", lambda execution_id: (_ for _ in ()).throw(
        RuntimeError("boom")))
    try:
        api._repair_stale_executing()
    except RuntimeError:
        pass
    assert calls == [1]
    assert h["id"] in a["_live"]


# ---------- S: memory clear stages, clears, then commits ----------

def _seed_memory(store, a):
    mem = store.auto_dir(a) / "memory"
    mem.mkdir(parents=True, exist_ok=True)
    (mem / "seen.yaml").write_text("- one\n", encoding="utf-8")
    return mem


def test_memory_clear_that_fails_commits_no_snapshot_and_leaves_no_stage(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Clear Order", "mock")
    _seed_memory(store, a)
    before = store.list_snapshots(a)
    published = []
    monkeypatch.setattr(api, "_publish_auto_changed", lambda auto: published.append(auto["id"]))

    def held_open(auto):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(store, "clear_memory", held_open)
    r = client.post(f"/automations/{a['id']}/memory/clear")
    assert r.status_code == 409
    assert r.json()["detail"] == "couldn't remove it from disk: Permission denied"
    assert store.list_snapshots(a) == before
    assert not [p for p in store.auto_dir(a).iterdir()
                if p.name.startswith(store.SNAPSHOT_STAGE_PREFIX)]
    assert (store.auto_dir(a) / "memory" / "seen.yaml").exists()
    assert published == []


def test_memory_clear_commits_the_pre_clear_snapshot_and_publishes(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Clear Commit", "mock")
    _seed_memory(store, a)
    published = []
    monkeypatch.setattr(api, "_publish_auto_changed", lambda auto: published.append(auto["id"]))
    r = client.post(f"/automations/{a['id']}/memory/clear")
    assert r.status_code == 200
    assert [m["reason"] for m in store.list_snapshots(a)] == ["pre-clear"]
    assert not (store.auto_dir(a) / "memory" / "seen.yaml").exists()
    assert not [p for p in store.auto_dir(a).iterdir()
                if p.name.startswith(store.SNAPSHOT_STAGE_PREFIX)]
    assert published == [a["id"]]


# ---------- AH: POST /automations never 500s after the create ----------

def test_create_answers_the_automation_when_the_concurrency_write_fails(client, monkeypatch):
    from autowright.storage import store

    real = store.patch_automation

    def flaky(a, patch):
        if "maxParallel" in patch:
            raise OSError(28, "No space left on device")
        return real(a, patch)

    monkeypatch.setattr(store, "patch_automation", flaky)
    r = client.post("/automations", json={"draft": make_version(), "agentId": "mock",
                                          "name": "Post Create", "concurrency": {"maxParallel": 2}})
    assert r.status_code == 200
    assert r.json()["name"] == "Post Create"
    assert len(store.autos) == 1
    assert [a["name"] for a in store.autos.values()] == ["Post Create"]


def test_create_answers_the_automation_when_the_chat_migration_fails(client, monkeypatch):
    from autowright.storage import store

    def broken(a):
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(store, "migrate_pending_chat", broken)
    r = client.post("/automations", json={"draft": make_version(), "agentId": "mock",
                                          "name": "Chat Lost"})
    assert r.status_code == 200
    assert len(store.autos) == 1


# ---------- AE: GET /settings answers the §4.9 keys only ----------

def test_settings_answer_only_the_documented_keys(client):
    from autowright import api
    from autowright.storage import store

    store.settings["imessageAutomation"] = "granted"
    store.settings["handAdded"] = 1
    expected = set(api.SETTINGS_RESPONSE_KEYS) | {"dataPath", "dataSize", "appPath"}
    assert set(api.SETTINGS_RESPONSE_KEYS) == {
        "login", "menuBarIcon", "keepAwake", "automaticUpdateCheck", "notifications",
        "days", "keepForever", "developerMode", "cliEnabled"}
    got = client.get("/settings").json()
    assert set(got) == expected
    state = client.get("/state").json()["settings"]
    assert set(state) == expected


# ---------- AB: a parked import preview expires by timer ----------

def test_a_parked_preview_spool_file_goes_at_expiry_without_another_request(client, monkeypatch):
    from autowright import api, transfer

    a = client.post("/automations", json={"draft": make_version(), "agentId": "mock"}).json()
    data = client.get(f"/automations/{a['id']}/export").content
    monkeypatch.setattr(transfer, "preview_archive", lambda store, data: {})
    monkeypatch.setattr(api, "_IMPORT_TTL", 0.2)
    r = client.post("/automations/import/preview", content=data,
                    headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200
    token = r.json()["token"]
    _, spool = api._import_parked[token]
    assert spool.exists()
    time.sleep(0.6)
    assert not spool.exists()
    assert token not in api._import_parked
    assert token not in api._import_timers


def test_confirm_and_eviction_cancel_the_slot_timer(client, monkeypatch):
    from autowright import api, transfer

    a = client.post("/automations", json={"draft": make_version(), "agentId": "mock"}).json()
    data = client.get(f"/automations/{a['id']}/export").content
    monkeypatch.setattr(transfer, "preview_archive", lambda store, data: {})
    tokens = []
    for _ in range(api._IMPORT_SLOTS + 1):
        r = client.post("/automations/import/preview", content=data,
                        headers={"Content-Type": "application/octet-stream"})
        tokens.append(r.json()["token"])
    # the oldest was evicted, and its timer went with it
    assert tokens[0] not in api._import_timers
    assert set(api._import_timers) == set(tokens[1:])
    client.post("/automations/import/confirm", json={"token": tokens[1]})
    assert tokens[1] not in api._import_timers


# ---------- AD: agent names compare through the §4.1 name key ----------

def test_agent_name_with_trailing_space_and_case_clashes_like_an_exact_duplicate(client):
    assert client.post("/agents", json={"harness": "Codex", "name": "coder"}).status_code == 200
    exact = client.post("/agents", json={"harness": "Codex", "name": "coder"})
    fuzzy = client.post("/agents", json={"harness": "Codex", "name": "Coder "})
    assert exact.status_code == 422
    assert fuzzy.status_code == exact.status_code
    assert fuzzy.json()["detail"] == exact.json()["detail"]


# ---------- L: shutdown kills tracked pip runs and installer scripts ----------

def test_shutdown_kills_running_pip_and_installer_children(monkeypatch):
    from autowright import api, installer, packages

    calls = []
    monkeypatch.setattr(api, "_startup_callbacks", [])
    monkeypatch.setattr(api, "_quiesce_callbacks", [])
    monkeypatch.setattr(api, "_shutdown_callbacks", [])
    monkeypatch.setattr(api.engine, "kill_all_live", lambda: None)
    monkeypatch.setattr(api.draft_jobs, "kill_all_building", lambda: None)
    monkeypatch.setattr(api, "_repair_stale_executing", lambda: None)
    monkeypatch.setattr(packages, "kill_running", lambda: calls.append("pip"))
    monkeypatch.setattr(installer, "kill_running", lambda: calls.append("installer"))

    async def drive() -> None:
        lifespan = api._lifespan(api.app)
        await lifespan.__aenter__()
        await lifespan.__aexit__(None, None, None)

    asyncio.run(drive())
    assert calls == ["pip", "installer"]


# ---------- AQ: delete drops the automation's draft generation ----------

def test_delete_drops_the_draft_generation(client):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Generation Gone", "mock")
    api._settle_draft(a["id"])
    assert a["id"] in api.draft_generations
    assert client.delete(f"/automations/{a['id']}").status_code == 200
    assert a["id"] not in api.draft_generations


# ---------- AT: secret names are fullmatched ----------

def test_secret_name_with_a_trailing_newline_answers_422(client):
    from autowright.storage import store

    r = client.post("/secrets", json={"name": "API_KEY\n", "value": "v"})
    assert r.status_code == 422
    assert store.secrets == []


# ---------- V: the HTTP pull uses the no-proxy opener ----------

def test_http_pull_goes_through_the_ollama_opener(monkeypatch):
    import urllib.request

    from autowright import api, harness
    from autowright.events import hub

    seen = []

    class _Stream:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def __iter__(self):
            yield b'{"status": "success"}'

    def opener(request, timeout):
        seen.append((request.full_url, timeout))
        return _Stream()

    def forbidden(*args, **kwargs):
        raise AssertionError("the pull must not use the proxy-honouring urlopen")

    monkeypatch.setattr(harness, "ollama_urlopen", opener)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(hub, "publish", lambda ev, **kw: None)
    api._ollama_pull_http("llama3")
    assert seen == [(f"{harness.OLLAMA_URL}/api/pull", 30)]
