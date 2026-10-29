"""2026-10-28 audit batch (store/API/drafting): the duplicate gate decided from
the files on disk, NEL-safe YAML writes, the latest-result order on parsed
stamps, the version-label digit cap, typed spec blocks, the save's name
re-check before the settle, the settle gap for a late test, the locked
duplicate-name preflight blocker, and the save writing the validator's
normalized params."""
import time
import uuid

from conftest import make_version

GRANTS = {"agents": [], "secrets": []}


# ---------- §4.1/§19 duplicate: decided from the files on disk ----------

def test_duplicate_refuses_a_hand_edit_made_after_load(client):
    from autowright import paths
    from autowright.storage import store

    a = store.create_automation(make_version(), "Original", "mock")
    vd = store.auto_dir(a) / "versions" / "v1"
    # Hand edit AFTER load, no re-load and no execution since: the loaded
    # flag is still clear.
    (vd / "02-finish.py").write_text("print('hand edit')\r\n", encoding="utf-8")
    finish = a["versions"][1]["steps"][1]
    assert not finish.get("modifiedOnDisk")
    folders_before = set(p.name for p in paths.automations_dir().iterdir())
    autos_before = set(store.autos)

    r = client.post(f"/automations/{a['id']}/duplicate", json={})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == ("Step `Finish` was edited on disk — pull and push it, "
                                  "or save it from the editor, before duplicating")
    assert set(store.autos) == autos_before
    assert set(p.name for p in paths.automations_dir().iterdir()) == folders_before
    # The source step is now flagged, its code refreshed (newline-normalized).
    assert finish["modifiedOnDisk"] is True
    assert finish["code"] == "print('hand edit')\n"


def test_disk_modified_steps_is_empty_for_matching_files(store):
    a = store.create_automation(make_version(), "Clean", "agent-1")
    with store.lock:
        assert store.disk_modified_steps(a) == []
    assert not any(s.get("modifiedOnDisk") for s in a["versions"][1]["steps"])


def test_disk_modified_steps_returns_the_loaded_steps(store):
    a = store.create_automation(make_version(), "Edited", "agent-1")
    vd = store.auto_dir(a) / "versions" / "v1"
    (vd / "01-say.py").write_text("print('x')\n", encoding="utf-8")
    with store.lock:
        modified = store.disk_modified_steps(a)
    assert [s["name"] for s in modified] == ["Say hello"]
    assert modified[0] is a["versions"][1]["steps"][0]


# ---------- §5 a YAML string holding U+0085 is written double-quoted ----------

def test_nel_round_trips_through_every_store_write(store, home):
    from autowright.storage import Store

    ver = make_version(params=[{"name": "p", "kind": "text", "label": "L", "help": "",
                                "default": "x\x85y"}])
    a = store.create_automation(ver, "a\x85b", "agent-1")
    h = store.create_execution(a, "version", 1, "manual", [], status="failed")
    h["error"] = {"step": None, "message": "bad\x85thing", "type": "Boom"}
    store.update_execution(h)
    text = (home / "automations" / a["id"] / "automation.yaml").read_text(encoding="utf-8")
    assert '"a\\Nb"' in text

    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    assert b["name"] == "a\x85b"
    assert b["versions"][1]["params"][0]["default"] == "x\x85y"
    assert s2.read_exec_yaml(h["id"])["error"]["message"] == "bad\x85thing"


def test_dump_yaml_keeps_the_default_style_without_nel():
    from autowright.yamlio import dump_yaml

    assert dump_yaml({"name": "plain"}) == "name: plain\n"


# ---------- §7 latest result: ordered by the parsed stamp ----------

def test_latest_result_orders_by_parsed_started_at(store):
    a = store.create_automation(make_version(), "Results", None)
    older = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    older["chip"] = "Older"
    # Hand-edited space form, LATER in time but lower in a raw string compare.
    older["started_at"] = "2026-10-27T09:00:00"
    store.update_execution(older)
    newer = store.create_execution(a, "version", 1, "manual", [], status="succeeded")
    newer["chip"] = "Newer"
    newer["started_at"] = "2026-10-27 10:00:00"
    store.update_execution(newer)
    assert "2026-10-27 10:00:00" < "2026-10-27T09:00:00"  # the raw compare's trap
    latest = store.latest_result_json(a)
    assert latest["executionId"] == newer["id"] and latest["chip"] == "Newer"


# ---------- §19 version diff: a huge label is a 404 ----------

def test_a_five_thousand_digit_version_label_is_a_404(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Differ", "mock")
    r = client.get(f"/automations/{a['id']}/diff",
                   params={"from": "v" + "9" * 5000, "to": "v1"})
    assert r.status_code == 404, r.text


# ---------- §19 typed spec blocks ----------

def test_a_non_string_spec_block_text_is_a_422_and_writes_nothing(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Specced", "mock")
    draft = make_version(note="changed", spec=[{"kind": "p", "text": 5}])
    r = client.post(f"/automations/{a['id']}/versions", json={"draft": draft})
    assert r.status_code == 422, r.text
    assert not (store.auto_dir(a) / "versions" / "v2").exists()
    assert a["current_version"] == 1


def test_an_unknown_spec_block_kind_is_a_422(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Kinds", "mock")
    draft = make_version(spec=[{"kind": "h3", "text": "x"}])
    r = client.post(f"/automations/{a['id']}/versions", json={"draft": draft})
    assert r.status_code == 422, r.text


def test_typed_spec_blocks_still_save_as_plain_blocks(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Plain", "mock")
    spec = [{"kind": "h1", "text": "New"}, {"kind": "li", "text": "item"}]
    r = client.post(f"/automations/{a['id']}/versions",
                    json={"draft": make_version(spec=spec)})
    assert r.status_code == 200, r.text
    assert a["current_version"] == 2
    assert a["versions"][2]["spec"] == spec


# ---------- §19 save: the name re-checked before the settle ----------

def test_a_name_taken_before_the_settle_is_a_422_and_keeps_the_draft(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Mine", "mock")
    real = api._validate_draft_steps

    def validate_then_collide(d, owner=None):
        out = real(d, owner)
        store.create_automation(make_version(), "Taken", "mock")
        return out

    monkeypatch.setattr(api, "_validate_draft_steps", validate_then_collide)
    generation = api.draft_generations.get(a["id"], 0)
    r = client.post(f"/automations/{a['id']}/versions",
                    json={"draft": make_version(note="x"), "name": "Taken"})
    assert r.status_code == 422, r.text
    assert "already exists" in r.json()["detail"]
    assert api.draft_generations.get(a["id"], 0) == generation
    assert a["current_version"] == 1 and a["name"] == "Mine"


# ---------- §19 settle gap: a late test never recreates the draft ----------

def test_a_test_started_in_the_settle_gap_leaves_nothing_behind(client, monkeypatch):
    from autowright import api, paths
    from autowright.storage import store

    draft = make_version(steps=[{"file": "01-slow.py", "name": "Slow", "description": "",
                                 "code": "import time\ntime.sleep(1)\n"}],
                         params=[])
    started: list[str] = []
    real = api._settle_draft

    def settle_then_start(owner_id):
        out = real(owner_id)
        # A test admitted after the settle's cancels, before delete_draft.
        r = client.post("/tests", json={"draft": draft})
        assert r.status_code == 200, r.text
        started.append(r.json()["executionId"])
        return out

    monkeypatch.setattr(api, "_settle_draft", settle_then_start)
    assert client.delete("/draft/pending").json()["ok"]
    assert started
    eid = started[0]
    deadline = time.time() + 15
    while time.time() < deadline and (api.engine.is_live(eid) or eid in store.execs):
        time.sleep(0.05)
    assert eid not in store.execs
    assert not (paths.pending_draft_dir() / "test.yaml").exists()


# ---------- §8 locked-step preflight: a shared locked name ----------

def test_a_locked_name_shared_by_two_steps_blocks_without_an_agent_call(monkeypatch):
    from autowright import harness
    from autowright.drafting import DraftJobs, locked_step_preflight

    code = 'from autowright import log\nlog("x")\n'
    current = {"spec": "# T\n\nBody.", "steps": [
        {"file": "01-fetch.py", "name": "Fetch", "description": "d", "code": code,
         "locked": True},
        {"file": "02-fetch.py", "name": " Fetch ", "description": "d", "code": code},
    ]}
    blockers = locked_step_preflight(current, GRANTS)
    assert blockers == [{
        "reason": "two steps are named `Fetch` — the lock splice can't tell them apart",
        "fix": "Rename or unlock one of them", "details": "", "kind": "constraint",
        "step": "Fetch"}]

    calls = []
    monkeypatch.setattr(harness, "invoke", lambda *a, **k: calls.append(a) or "")
    jobs = DraftJobs()
    job_id = jobs.start("sync", {"harness": "Claude Code"}, None, current, GRANTS)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and jobs.get(job_id)["status"] == "building":
        time.sleep(0.05)
    j = jobs.get(job_id)
    assert calls == []
    assert j["status"] == "blocked" and j["blockedAt"] == "steps"
    assert j["blockers"][0]["step"] == "Fetch"


def test_two_locked_steps_of_one_name_block_once():
    from autowright.drafting import locked_step_preflight

    step = {"description": "d", "code": "x = 1\n", "locked": True, "name": "Same"}
    current = {"steps": [{**step, "file": "01-a.py"}, {**step, "file": "02-b.py"}]}
    blockers = locked_step_preflight(current, GRANTS)
    assert [b["step"] for b in blockers] == ["Same"]


# ---------- §8/§19 save writes the validator's normalized params ----------

def test_a_save_writes_the_normalized_param_definitions(client):
    from autowright.storage import load_yaml, store

    a = store.create_automation(make_version(), "Params", "mock")
    draft = make_version(params=[
        {"name": "greeting", "kind": "text", "label": "Greeting", "help": "",
         "default": "hello"},
        {"name": "count", "kind": "number", "default": 1, "foo": "x"},
    ])
    r = client.post(f"/automations/{a['id']}/versions", json={"draft": draft})
    assert r.status_code == 200, r.text
    assert a["current_version"] == 2
    manifest = load_yaml(store.auto_dir(a) / "versions" / "v2" / "automation.yaml")
    count = next(p for p in manifest["params"] if p["name"] == "count")
    assert "foo" not in count
    assert count == {"name": "count", "kind": "number", "default": 1, "min": 0}


def test_create_writes_the_normalized_param_definitions(client):
    from autowright.storage import load_yaml, store

    draft = make_version(params=[{"name": "count", "kind": "number", "default": 2,
                                  "stray": True}])
    draft["steps"][0]["code"] = 'from autowright import log\nlog("hi")\n'
    r = client.post("/automations", json={"draft": draft, "name": f"Made {uuid.uuid4()}",
                                          "agentId": "mock"})
    assert r.status_code == 200, r.text
    a = store.autos[r.json()["id"]]
    manifest = load_yaml(store.auto_dir(a) / "versions" / "v1" / "automation.yaml")
    assert manifest["params"] == [{"name": "count", "kind": "number", "default": 2,
                                   "min": 0}]
