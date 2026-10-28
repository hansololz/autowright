"""2026-10-27 audit batch: step-name uniqueness, the lock splice's stored
filename, the §8 locked-step preflight, the diagnosis parse's locked names,
`POST /validate-steps` automationId, the narrowed restore gate, the duplicate
409 on a step-modified source, the §4.4 minting rule, and the Claude
stream-json parser guards."""
import json
import time as _time
import uuid

from conftest import make_version

from autowright.drafting import parse_envelope, validate_steps

GRANTS = {"agents": [], "secrets": []}

_real_sleep = _time.sleep


def _run_job(jobs, *args):
    job_id = jobs.start(*args)
    deadline = _time.monotonic() + 10
    while _time.monotonic() < deadline:
        j = jobs.get(job_id)
        if j["status"] != "building":
            return j
        _real_sleep(0.05)
    raise AssertionError("job never settled")


def _envelope(*entries, files=None):
    lines = ["===FILE: manifest.yaml===", "note: n", "steps:"]
    lines += [f"  - {e}" for e in entries]
    for name, code in (files or {}).items():
        lines += [f"===FILE: {name}===", code.rstrip("\n")]
    return "\n".join(lines) + "\n===END===\n"


CODE = 'from autowright import log\nlog("x")\n'


# ---------- §4.1 / §8 rule 3: step names are non-blank and unique ----------

def test_duplicate_step_names_are_an_error():
    text = _envelope("{ file: 01-a.py, name: Fetch, description: d }",
                     "{ file: 02-b.py, name: ' Fetch ', description: d }",
                     files={"01-a.py": CODE, "02-b.py": CODE})
    _, errors = validate_steps(parse_envelope(text), GRANTS)
    assert errors == ["step names must be unique: `Fetch` appears twice"]


def test_three_steps_of_one_name_say_how_many():
    text = _envelope("{ file: 01-a.py, name: X, description: d }",
                     "{ file: 02-b.py, name: X, description: d }",
                     "{ file: 03-c.py, name: X, description: d }",
                     files={"01-a.py": CODE, "02-b.py": CODE, "03-c.py": CODE})
    _, errors = validate_steps(parse_envelope(text), GRANTS)
    assert errors == ["step names must be unique: `X` appears 3 times"]


def test_blank_or_missing_step_name_is_an_error():
    text = _envelope("{ file: 01-a.py, name: A, description: d }",
                     "{ file: 02-b.py, name: '  ', description: d }",
                     "{ file: 03-c.py, description: d }",
                     files={"01-a.py": CODE, "02-b.py": CODE, "03-c.py": CODE})
    _, errors = validate_steps(parse_envelope(text), GRANTS)
    assert "step 2 needs a name" in errors
    assert "step 3 needs a name" in errors


def test_sync_prompt_and_framework_state_the_unique_name_rule():
    from pathlib import Path

    from autowright import drafting

    assert "Step names are non-blank and unique" in drafting.STEPS_TASK
    framework = (Path(drafting.__file__).parent / "instructions"
                 / "framework-instructions.md").read_text(encoding="utf-8")
    assert "Step names are non-blank and unique" in framework


# ---------- §8 rule 3: an identical block under the stored filename ----------

LOCKED_CODE = 'from autowright import log\nlog("stored")\n'
LOCKED_STEP = {"file": "01-fetch-page.py", "name": "Fetch page", "description": "stored desc",
               "code": LOCKED_CODE, "authoredBy": "user", "locked": True}


def _locked_validate(text):
    locked = [dict(LOCKED_STEP)]
    return validate_steps(parse_envelope(text), GRANTS, locked_steps=locked,
                          current_steps=locked)


def test_identical_block_under_the_stored_filename_is_accepted():
    # The agent moved the locked step to position 2 but returned its script
    # under the stored filename, byte-identical.
    text = _envelope("{ file: 01-a.py, name: A, description: d }",
                     "{ file: 02-fetch-page.py, name: Fetch page, locked: true }",
                     files={"01-a.py": CODE, "01-fetch-page.py": LOCKED_CODE})
    draft, errors = _locked_validate(text)
    assert errors == []
    assert [s["file"] for s in draft["steps"]] == ["01-a.py", "02-fetch-page.py"]
    assert draft["steps"][1]["code"] == LOCKED_CODE


def test_changed_block_under_the_stored_filename_is_an_error():
    text = _envelope("{ file: 01-a.py, name: A, description: d }",
                     "{ file: 02-fetch-page.py, name: Fetch page, locked: true }",
                     files={"01-a.py": CODE, "01-fetch-page.py": 'print("changed")\n'})
    _, errors = _locked_validate(text)
    assert "step `Fetch page` is locked — do not return its file" in errors


# ---------- §8 locked-step preflight ----------

def test_locked_step_preflight_blocks_without_an_agent_call(monkeypatch):
    from autowright import harness
    from autowright.drafting import DraftJobs

    calls = []
    monkeypatch.setattr(harness, "invoke", lambda *a, **k: calls.append(a) or "")
    gone = str(uuid.uuid4())
    bad_secret = {**LOCKED_STEP, "code": LOCKED_CODE + f'secrets["{gone}"]\n'}
    bad_import = {"file": "02-parse.py", "name": "Parse", "description": "d",
                  "code": "import totally_not_allowed\n", "locked": True}
    fine = {"file": "03-ok.py", "name": "Ok", "description": "d",
            "code": LOCKED_CODE, "locked": True}
    current = {"spec": "# T\n\nBody.", "steps": [bad_secret, bad_import, fine]}
    j = _run_job(DraftJobs(), "sync", {"harness": "Claude Code"}, None, current, GRANTS)
    assert calls == []
    assert j["status"] == "blocked" and j["blockedAt"] == "steps"
    assert not j.get("diagnosed")
    fix = "Unlock the step so the next sync can rewrite it, or restore what it needs"
    by_step = {b["step"]: b for b in j["blockers"]}
    assert set(by_step) == {"Fetch page", "Parse"}
    for b in j["blockers"]:
        assert b["kind"] == "constraint" and b["fix"] == fix
    assert gone in by_step["Fetch page"]["reason"]
    assert by_step["Parse"]["reason"] == ("02-parse.py: import totally_not_allowed "
                                          "isn't allowed")


def test_locked_step_preflight_passes_clean_locked_steps():
    from autowright.drafting import locked_step_preflight

    current = {"steps": [dict(LOCKED_STEP),
                         {"file": "02-free.py", "name": "Free", "code": "import nope\n"}]}
    # Only locked steps are checked; an unlocked step is the agent's to fix.
    assert locked_step_preflight(current, GRANTS) == []


# ---------- §8 diagnosis: the parse knows the locked names ----------

def test_diagnosis_constraint_blocker_must_name_a_locked_step(monkeypatch):
    from autowright import harness
    from autowright.drafting import DraftJobs

    reply = ("===BLOCKED===\nblockers:\n  - kind: constraint\n    step: Not locked\n"
             "    reason: r\n    fix: f\n===END===\n")
    monkeypatch.setattr(harness, "invoke", lambda *a, **k: reply)
    job = {"id": "j", "events": [], "stage": "s", "status": "building", "mode": "sync",
           "_cancel": False, "_proc": {}}
    blockers = DraftJobs()._diagnose(job, {"harness": "Claude Code"}, "prompt", "raw",
                                     ["some error"], locked_names={"Fetch page"})
    # The diagnosis' blocker failed the parse: the deterministic fallback.
    assert len(blockers) == 1 and "kind" not in blockers[0]
    assert "didn't build" in blockers[0]["reason"]

    ok = reply.replace("Not locked", "Fetch page")
    monkeypatch.setattr(harness, "invoke", lambda *a, **k: ok)
    blockers = DraftJobs()._diagnose(job, {"harness": "Claude Code"}, "prompt", "raw",
                                     ["some error"], locked_names={"Fetch page"})
    assert blockers[0]["step"] == "Fetch page"


# ---------- §19 POST /validate-steps automationId ----------

def test_validate_steps_reads_the_automations_unresolved_references(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Imported", "mock")
    sid = str(uuid.uuid4())
    a["unresolved_references"] = {sid: {"kind": "secret", "name": "Old token"}}
    draft = make_version()
    draft["steps"][0]["secrets"] = [{"id": sid, "why": "auth"}]

    plain = client.post("/validate-steps", json={"draft": draft}).json()["errors"]
    assert plain and not any("imported file" in e for e in plain)
    r = client.post("/validate-steps", json={"draft": draft, "automationId": a["id"]})
    assert r.status_code == 200
    assert any("Old token" in e and "imported file" in e for e in r.json()["errors"])

    r = client.post("/validate-steps", json={"draft": draft, "automationId": "nope"})
    assert r.status_code == 404


# ---------- §19 duplicate: refused on a step-modified source ----------

def test_duplicate_refuses_a_step_modified_source(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Original", "mock")
    vd = store.auto_dir(a) / "versions" / "v1"
    (vd / "02-finish.py").write_text("print('hand edit')\n", encoding="utf-8")
    a["versions"][1] = store._load_version_folder(vd)
    before = set(store.autos)
    r = client.post(f"/automations/{a['id']}/duplicate", json={})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == ("Step `Finish` was edited on disk — pull and push it, "
                                  "or save it from the editor, before duplicating")
    assert set(store.autos) == before


# ---------- §4.4 minting: a step modified on disk always mints ----------

def test_identical_save_over_a_step_modified_version_mints(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Minter", "mock")
    r = client.post(f"/automations/{a['id']}/versions", json={"draft": make_version()})
    assert r.status_code == 200 and a["current_version"] == 1  # unchanged: no mint

    vd = store.auto_dir(a) / "versions" / "v1"
    hand = "from autowright import result\nresult.status('hand')\n"
    (vd / "02-finish.py").write_text(hand, encoding="utf-8")
    a["versions"][1] = store._load_version_folder(vd)
    assert a["versions"][1]["steps"][1]["modifiedOnDisk"] is True

    draft = make_version()
    draft["steps"][1]["code"] = hand  # identical to the loaded content
    r = client.post(f"/automations/{a['id']}/versions", json={"draft": draft})
    assert r.status_code == 200, r.text
    assert a["current_version"] == 2
    assert not any(s.get("modifiedOnDisk") for s in a["versions"][2]["steps"])
    assert client.get(f"/automations/{a['id']}").json()["problems"] == []


def test_version_content_ignores_the_in_memory_mark():
    from autowright.api import _version_content

    v = make_version()
    marked = make_version()
    marked["steps"][0]["modifiedOnDisk"] = True
    assert _version_content(v) == _version_content(marked)


# ---------- harness: Claude stream-json guards ----------

def test_claude_stream_line_skips_non_dict_fields():
    from autowright.harness import _claude_stream_line as parse

    assert parse(json.dumps({"type": "stream_event", "event": "ping"})) == (None, None, [])
    assert parse(json.dumps({"type": "stream_event",
                             "event": {"type": "content_block_delta",
                                       "delta": "oops"}})) == (None, None, [])
    assert parse(json.dumps({"type": "stream_event",
                             "event": {"type": "content_block_delta",
                                       "delta": ["x"]}})) == (None, None, [])
    assert parse(json.dumps({"type": "assistant", "message": "text"})) == (None, None, [])
    assert parse(json.dumps({"type": "assistant", "message": [1]})) == (None, None, [])
