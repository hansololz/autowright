"""Regression tests for the 2026-10-14 drafting / harness / draft-settle
fixes — one block per fixed item.

Each test names the spec rule it pins (§8 agent pipeline in
`spec/agent-pipeline.md`, §19 draft generation in `spec/backend-api.md`, §5
YAML error set in `spec/storage.md`)."""
import pytest

from conftest import make_version


# ---------- §8: the OUTPUT section states both repair-round cases ----------

def test_output_block_states_the_kept_and_the_full_envelope_cases():
    from autowright import drafting

    text = " ".join(drafting.FILE_OUTPUT_BLOCK.split())
    assert "write only the failed files" in text
    assert "write EVERY file again" in text
    assert "on a full-envelope round a file you skip is lost" in text


def test_unattributed_chat_repair_asks_for_the_full_envelope():
    from autowright import drafting

    prompt = drafting.DraftJobs._chat_repair_prompt("PROMPT", "raw", ["bad"], {}, [])
    assert "resend the full envelope" in prompt
    assert "kept" not in prompt


# ---------- §8: a fence line is ``` plus anything with no further backtick ----------

@pytest.mark.parametrize("opener", ["```yaml ", "```yaml title=x", "``` ", "```yaml\t"])
@pytest.mark.parametrize("mark", ["===END===", "===BLOCKED===", "===FILE: spec.md==="])
def test_a_fence_with_an_info_string_or_trailing_space_hides_markers(opener, mark):
    from autowright import harness

    text = f"prose\n{opener}\n{mark}\n```\n"
    pattern = harness.re.compile("^" + harness.re.escape(mark) + r"\s*$", harness.re.M)
    assert harness._mark_outside_fences(text, pattern) is None
    assert harness.blocked_mark_outside_fences(text) is None
    assert harness.file_mark_outside_fences(text) is None
    # The same marker outside the fence is still found.
    assert harness._mark_outside_fences(text + mark + "\n", pattern) is not None


def test_a_line_with_a_further_backtick_is_not_a_fence():
    from autowright import harness

    text = "```a`b\n===BLOCKED===\n"
    assert harness.blocked_mark_outside_fences(text) is not None


def test_strip_fence_agrees_on_an_info_string():
    from autowright import drafting

    assert drafting._strip_fence("```yaml title=x \na: 1\n```") == "a: 1"


# ---------- §8: every scratch document is size-checked before it is read ----------

def test_documents_caps_a_polled_document_that_grew(tmp_path, monkeypatch):
    from autowright import harness

    monkeypatch.setattr(harness, "STDOUT_CAP_CHARS", 100)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    watcher = harness._ScratchWatcher(scratch, harness.ProgressSink(), "Codex")
    (scratch / "notes.md").write_text("- small\n", encoding="utf-8")
    watcher._poll()
    assert watcher.documents() == [("notes.md", "- small\n")]
    # Grew past the cap after its last poll: never read, the call fails.
    (scratch / "notes.md").write_text("x" * 500, encoding="utf-8")
    with pytest.raises(harness.HarnessError, match="output — aborting"):
        watcher.documents()


# ---------- §5: PyYAML's ValueError / RecursionError are validation errors ----------

def test_actions_yaml_impossible_date_is_a_validation_error():
    from autowright import drafting

    data, errors = drafting.validate_actions("a: 2026-13-45\n")
    assert data == {}
    assert errors and errors[0].startswith("actions.yaml doesn't parse as yaml")


def test_actions_yaml_deep_nesting_is_a_validation_error():
    from autowright import drafting

    data, errors = drafting.validate_actions("[" * 5000 + "]" * 5000)
    assert data == {}
    assert errors and errors[0].startswith("actions.yaml doesn't parse as yaml")


def test_manifest_yaml_impossible_date_is_a_validation_error():
    from autowright import drafting

    data, errors = drafting.validate_steps({"manifest.yaml": "a: 2026-13-45\n"})
    assert data == {}
    assert errors and errors[0].startswith("manifest.yaml doesn't parse")


def test_manifest_yaml_deep_nesting_is_a_validation_error():
    from autowright import drafting

    data, errors = drafting.validate_steps({"manifest.yaml": "[" * 5000 + "]" * 5000})
    assert data == {}
    assert errors and errors[0].startswith("manifest.yaml doesn't parse")


# ---------- §19: every draft settle bumps the generation ----------

def _fake_start(api, monkeypatch, started, status="building"):
    """draft_jobs.start registers a real record (so cancel/discard find it)."""
    def start(mode, agent, user_text, current, grants, **kw):
        job_id = f"job-{len(started)}"
        started.append(job_id)
        api.draft_jobs.jobs[job_id] = {"id": job_id, "status": status,
                                       "mode": mode, "_cancel": False, "_proc": {},
                                       "_owner": kw.get("owner_id")}
        return job_id

    monkeypatch.setattr(api.draft_jobs, "start", start)


def test_a_version_save_during_the_context_build_refuses_the_job(client, monkeypatch):
    from autowright import api, models
    from autowright.storage import store

    a = store.create_automation(make_version(), "Saved", "mock")
    started: list[str] = []
    _fake_start(api, monkeypatch, started)

    def build_with_a_save(*args, **kw):
        # POST /automations/{id}/versions lands mid-build
        api.save_version(a["id"], models.VersionSave(draft=make_version()))
        return []

    monkeypatch.setattr(api.testexec, "executions_context", build_with_a_save)
    try:
        r = client.post("/drafts", json={"mode": "chat", "text": "x", "agentId": "mock",
                                         "automationId": a["id"]})
        assert r.status_code == 409
        assert r.json()["detail"] == "the draft was discarded"
        assert started and api.draft_jobs.jobs[started[0]]["status"] == "cancelled"
        assert api.draft_jobs.job_for(a["id"]) is None
    finally:
        for k in started:
            api.draft_jobs.jobs.pop(k, None)


def test_a_settling_create_during_the_context_build_refuses_the_pending_job(client, monkeypatch):
    from autowright import api, models

    started: list[str] = []
    _fake_start(api, monkeypatch, started)

    def build_with_a_create(*args, **kw):
        # POST /automations (settlePending) lands mid-build
        api.create_auto(models.AutomationCreate(draft=make_version(), agentId="mock"))
        return []

    monkeypatch.setattr(api.testexec, "executions_context", build_with_a_create)
    try:
        r = client.post("/drafts", json={"mode": "chat", "text": "x", "agentId": "mock"})
        assert r.status_code == 409
        assert r.json()["detail"] == "the draft was discarded"
        assert started and api.draft_jobs.jobs[started[0]]["status"] == "cancelled"
        assert api.draft_jobs.job_for(None) is None
    finally:
        for k in started:
            api.draft_jobs.jobs.pop(k, None)


def test_a_create_without_settle_pending_leaves_the_generation(client):
    from autowright import api

    before = api.draft_generations.get(None, 0)
    r = client.post("/automations", json={"draft": make_version(), "agentId": "mock",
                                          "settlePending": False})
    assert r.status_code == 200
    assert api.draft_generations.get(None, 0) == before
    r = client.post("/automations", json={"draft": make_version(), "agentId": "mock"})
    assert r.status_code == 200
    assert api.draft_generations.get(None, 0) == before + 1


# ---------- §19: a job settled before the check is dropped, not held ----------

def test_a_discarded_job_that_already_settled_leaves_no_held_outcome(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Settled", "mock")
    started: list[str] = []
    # A missing harness binary fails on its own thread at once.
    _fake_start(api, monkeypatch, started, status="failed")
    published = []
    monkeypatch.setattr(api.draft_jobs, "_publish",
                        lambda j, status=None: published.append(status or j["status"]))

    def build_with_a_discard(*args, **kw):
        api.delete_draft_container(a["id"])
        return []

    monkeypatch.setattr(api.testexec, "executions_context", build_with_a_discard)
    try:
        r = client.post("/drafts", json={"mode": "chat", "text": "x", "agentId": "mock",
                                         "automationId": a["id"]})
        assert r.status_code == 409
        assert r.json()["detail"] == "the draft was discarded"
        assert started and started[0] not in api.draft_jobs.jobs
        assert published == ["consumed"]
        assert client.get(f"/draft/{a['id']}").json().get("job") is None
    finally:
        for k in started:
            api.draft_jobs.jobs.pop(k, None)
