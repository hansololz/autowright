"""2026-10-31 failure-class sweep, batch 9: §8 locked-step enforcement — the
preflight's empty-script blocker (and the stored-field lines it already
surfaces), the splice's refusal to write an empty locked script, and the
locked-bytes guard bracketing every drafting call (sync, repair, chat)."""
import copy
import time as _time

from conftest import make_version

from autowright.drafting import locked_step_preflight, parse_envelope, validate_steps

GRANTS = {"agents": [{"id": "ag1", "name": "Helper"}], "secrets": []}

_real_sleep = _time.sleep

LOCKED_CODE = 'from autowright import log\nlog("stored")\n'
FREE_CODE = 'from autowright import log\nlog("a")\n'
LOCKED = {"file": "02-fetch.py", "name": "Fetch", "description": "stored desc",
          "code": LOCKED_CODE, "authored_by": "user", "locked": True}
FREE = {"file": "01-a.py", "name": "A", "description": "d", "code": FREE_CODE}

EMPTY_REASON = "its script is missing or empty — the lock would keep an empty step"
EMPTY_FIX = "Restore the script, or unlock the step so the next sync can rewrite it"
PREFLIGHT_FIX = "Unlock the step so the next sync can rewrite it, or restore what it needs"


def _preflight(locked: dict) -> list[dict]:
    return locked_step_preflight({"steps": [dict(FREE), locked]}, GRANTS)


# ---------- §8 rule 8: the locked-step preflight ----------

def test_preflight_blocks_a_locked_step_with_an_empty_script():
    assert _preflight(dict(LOCKED, code="")) == [
        {"kind": "constraint", "step": "Fetch", "reason": EMPTY_REASON,
         "fix": EMPTY_FIX, "details": ""}]


def test_preflight_counts_a_whitespace_only_script_as_empty():
    assert [b["reason"] for b in _preflight(dict(LOCKED, code="  \n\t\n"))] == [EMPTY_REASON]


def test_preflight_blocks_a_locked_step_without_a_code_key():
    step = {k: v for k, v in LOCKED.items() if k != "code"}
    assert [(b["step"], b["reason"], b["fix"]) for b in _preflight(step)] == [
        ("Fetch", EMPTY_REASON, EMPTY_FIX)]


def test_preflight_blocks_stored_retries_past_the_cap():
    blockers = _preflight(dict(LOCKED, retries=11))
    assert blockers == [{"kind": "constraint", "step": "Fetch",
                         "reason": "retries must be an integer from 1 to 10",
                         "fix": PREFLIGHT_FIX, "details": ""}]


def test_preflight_blocks_a_stored_agents_entry_with_a_name_key():
    step = dict(LOCKED, code='from autowright import agent, log\nlog(agent.ask("hi"))\n',
                agent=True, why="w", agents=[{"id": "ag1", "why": "w", "name": "Helper"}])
    blockers = _preflight(step)
    assert [b["step"] for b in blockers] == ["Fetch"]
    assert blockers[0]["reason"] == (
        "agents must be a list of { id, why? } granted-agent entries "
        "(ids from the grants yaml, no name key)")
    assert blockers[0]["fix"] == PREFLIGHT_FIX


def test_preflight_empty_script_blocker_sits_beside_other_steps_blockers():
    other = {"file": "03-parse.py", "name": "Parse", "description": "d",
             "code": "import totally_not_allowed\n", "locked": True}
    current = {"steps": [dict(FREE), dict(LOCKED, code=""), other]}
    by_step = {b["step"]: b for b in locked_step_preflight(current, GRANTS)}
    assert set(by_step) == {"Fetch", "Parse"}
    assert by_step["Fetch"]["reason"] == EMPTY_REASON
    assert by_step["Parse"]["fix"] == PREFLIGHT_FIX


# ---------- §8 rule 3: the splice refuses an empty locked script ----------

def _envelope(*entries, files=None):
    lines = ["===FILE: manifest.yaml===", "note: n", "steps:"]
    lines += [f"  - {e}" for e in entries]
    for name, code in (files or {}).items():
        lines += [f"===FILE: {name}===", code.rstrip("\n")]
    return "\n".join(lines) + "\n===END===\n"


def _splice_validate(text, locked):
    current = [dict(FREE), locked]
    return validate_steps(parse_envelope(text), GRANTS, locked_steps=[copy.deepcopy(locked)],
                          current_steps=copy.deepcopy(current))


EMPTY_SPLICE = "step `Fetch` is locked but its stored script is empty — restore it or unlock the step"


def test_splice_refuses_an_empty_stored_script_with_one_error_line():
    text = _envelope("{ file: 01-a.py, name: A, description: d }",
                     "{ file: 02-fetch.py, name: Fetch, locked: true }",
                     files={"01-a.py": FREE_CODE})
    draft, errors = _splice_validate(text, dict(LOCKED, code=""))
    assert draft == {}
    assert errors == [EMPTY_SPLICE]


def test_splice_refuses_an_empty_stored_script_mid_list_without_cascading():
    text = _envelope("{ file: 01-a.py, name: A, description: d }",
                     "{ file: 02-fetch.py, name: Fetch, locked: true }",
                     "{ file: 03-c.py, name: C, description: d }",
                     files={"01-a.py": FREE_CODE, "03-c.py": FREE_CODE})
    _, errors = _splice_validate(text, {k: v for k, v in LOCKED.items() if k != "code"})
    assert errors == [EMPTY_SPLICE]


def test_splice_still_accepts_a_stored_script():
    text = _envelope("{ file: 01-a.py, name: A, description: d }",
                     "{ file: 02-fetch.py, name: Fetch, locked: true }",
                     files={"01-a.py": FREE_CODE})
    draft, errors = _splice_validate(text, dict(LOCKED))
    assert errors == []
    assert draft["steps"][1]["code"] == LOCKED_CODE


# ---------- §8 locked-bytes guard ----------

TAMPERED = ("the agent changed the locked step 'Finish' on disk — "
            "the file was restored; nothing from this response was applied")

SAY_CODE = make_version()["steps"][0]["code"]
FINISH_CODE = make_version()["steps"][1]["code"]
SYNC_REPLY = _envelope("{ file: 01-say.py, name: Say hello, description: prints }",
                       "{ file: 02-finish.py, name: Finish, locked: true }",
                       files={"01-say.py": SAY_CODE})


def _locked_version():
    ver = make_version()
    ver["steps"][1].update({"authored_by": "user", "locked": True})
    return ver


def _setup(with_draft=True):
    from autowright.storage import store

    a = store.create_automation(_locked_version(), "Locked", "mock")
    if with_draft:
        store.save_draft(a, _locked_version())
    version_file = store.auto_dir(a) / "versions" / f"v{a['current_version']}" / "02-finish.py"
    draft_file = store.draft_dir(a) / "automation" / "02-finish.py"
    assert version_file.read_bytes() == FINISH_CODE.encode()
    return a, version_file, draft_file


def _run(client, monkeypatch, a, fake, mode="sync"):
    from autowright import api, harness

    calls: list[str] = []

    def invoke(agent, prompt, **kw):
        calls.append(prompt)
        return fake(len(calls))

    monkeypatch.setattr(harness, "invoke", invoke)
    body = {"mode": mode, "agentId": "mock", "automationId": a["id"]}
    if mode == "chat":
        body["text"] = "make it better"
    r = client.post("/drafts", json=body)
    assert r.status_code == 200, r.text
    job_id = r.json()["jobId"]
    deadline = _time.monotonic() + 10
    while _time.monotonic() < deadline:
        j = api.draft_jobs.get(job_id)
        if j["status"] != "building":
            return j, calls
        _real_sleep(0.05)
    raise AssertionError("job never settled")


def _draft_snapshot(a):
    from autowright.storage import store

    folder = store.draft_dir(a) / "automation"
    return {p.name: p.read_bytes() for p in sorted(folder.iterdir()) if p.is_file()}


def test_guard_restores_a_version_folder_rewrite_and_fails_the_job(client, monkeypatch):
    a, version_file, _ = _setup()
    before = _draft_snapshot(a)

    def fake(n):
        version_file.write_text('print("evil")\n', encoding="utf-8")
        return SYNC_REPLY

    j, _ = _run(client, monkeypatch, a, fake)
    assert j["status"] == "failed"
    assert j["error"] == TAMPERED
    assert j["draft"] is None
    assert version_file.read_bytes() == FINISH_CODE.encode()
    assert _draft_snapshot(a) == before


def test_guard_restores_a_draft_folder_rewrite(client, monkeypatch):
    a, version_file, draft_file = _setup()
    before = _draft_snapshot(a)

    def fake(n):
        draft_file.write_text('print("evil")\n', encoding="utf-8")
        return SYNC_REPLY

    j, _ = _run(client, monkeypatch, a, fake)
    assert (j["status"], j["error"]) == ("failed", TAMPERED)
    assert draft_file.read_bytes() == FINISH_CODE.encode()
    assert _draft_snapshot(a) == before
    assert version_file.read_bytes() == FINISH_CODE.encode()


def test_guard_re_creates_a_deleted_locked_file(client, monkeypatch):
    a, version_file, _ = _setup()

    def fake(n):
        version_file.unlink()
        return SYNC_REPLY

    j, _ = _run(client, monkeypatch, a, fake)
    assert (j["status"], j["error"]) == ("failed", TAMPERED)
    assert version_file.read_bytes() == FINISH_CODE.encode()


def test_guard_leaves_an_appeared_file_alone_but_fails_the_job(client, monkeypatch):
    # No draft folder at the start: the draft path's snapshot is skipped
    # whole, so a draft-folder write can't count. The version folder's file
    # is deleted before the call so its snapshot is None.
    a, version_file, _ = _setup(with_draft=False)
    version_file.unlink()

    def fake(n):
        version_file.write_text('print("planted")\n', encoding="utf-8")
        return SYNC_REPLY

    # The preflight reads `current` (the in-memory version), which still
    # carries the code, so the agent call runs.
    j, _ = _run(client, monkeypatch, a, fake)
    assert (j["status"], j["error"]) == ("failed", TAMPERED)
    assert version_file.read_text(encoding="utf-8") == 'print("planted")\n'


def test_guard_catches_a_tampering_repair_round(client, monkeypatch):
    a, version_file, _ = _setup()

    def fake(n):
        if n == 1:
            return "not an envelope"
        version_file.write_text('print("evil")\n', encoding="utf-8")
        return SYNC_REPLY

    j, calls = _run(client, monkeypatch, a, fake)
    assert len(calls) == 2
    assert (j["status"], j["error"]) == ("failed", TAMPERED)
    assert version_file.read_bytes() == FINISH_CODE.encode()


def test_guard_catches_a_tampering_chat_call(client, monkeypatch):
    a, version_file, draft_file = _setup()
    before = _draft_snapshot(a)

    def fake(n):
        draft_file.write_text('print("evil")\n', encoding="utf-8")
        return "Sure thing."

    j, _ = _run(client, monkeypatch, a, fake, mode="chat")
    assert (j["status"], j["error"]) == ("failed", TAMPERED)
    assert _draft_snapshot(a) == before
    assert version_file.read_bytes() == FINISH_CODE.encode()


def test_guard_ignores_an_unrelated_file(client, monkeypatch):
    from autowright.storage import store

    a, version_file, _ = _setup()
    stray = store.auto_dir(a) / "versions" / f"v{a['current_version']}" / "scratch.txt"

    def fake(n):
        stray.write_text("helper", encoding="utf-8")
        return SYNC_REPLY

    j, _ = _run(client, monkeypatch, a, fake)
    assert j["status"] == "done", j.get("error")
    assert stray.read_text(encoding="utf-8") == "helper"
    assert version_file.read_bytes() == FINISH_CODE.encode()


def test_guard_passes_an_untouched_call_through(client, monkeypatch):
    a, version_file, draft_file = _setup()
    before = _draft_snapshot(a)

    j, _ = _run(client, monkeypatch, a, lambda n: SYNC_REPLY)
    assert j["status"] == "done", j.get("error")
    assert [s["code"] for s in j["draft"]["steps"]] == [SAY_CODE, FINISH_CODE]
    assert version_file.read_bytes() == FINISH_CODE.encode()
    assert _draft_snapshot(a) == before


def test_guard_runs_when_the_harness_call_raises(client, monkeypatch):
    from autowright import harness

    a, version_file, _ = _setup()

    def fake(n):
        version_file.write_text('print("evil")\n', encoding="utf-8")
        raise harness.HarnessError("boom")

    j, _ = _run(client, monkeypatch, a, fake)
    assert (j["status"], j["error"]) == ("failed", TAMPERED)
    assert version_file.read_bytes() == FINISH_CODE.encode()


def test_guard_covers_the_version_copy_when_the_draft_reordered_the_step(client, monkeypatch):
    """§8 locked-bytes guard: the draft lists the locked step at another
    position (`01-finish.py`) than the version folder (`02-finish.py`) — the
    version copy is found by its stored suffix and guarded all the same."""
    from autowright.storage import store

    a, version_file, _ = _setup(with_draft=False)
    reordered = _locked_version()
    finish, say = reordered["steps"][1], reordered["steps"][0]
    finish["file"], say["file"] = "01-finish.py", "02-say.py"
    reordered["steps"] = [finish, say]
    store.save_draft(a, reordered)
    assert (store.draft_dir(a) / "automation" / "01-finish.py").is_file()

    def fake(n):
        version_file.write_text('print("evil")\n', encoding="utf-8")
        return SYNC_REPLY

    j, _ = _run(client, monkeypatch, a, fake)
    assert (j["status"], j["error"]) == ("failed", TAMPERED)
    assert version_file.read_bytes() == FINISH_CODE.encode()
