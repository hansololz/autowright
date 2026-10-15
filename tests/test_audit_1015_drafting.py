"""Regression tests for the 2026-10-15 drafting / harness / draft-route
fixes — one block per fixed item.

Each test names the spec rule it pins (§8 agent pipeline in
`spec/agent-pipeline.md`, §19 POST /drafts in `spec/backend-api.md`, §7
execution.deleted in `spec/execution.md`)."""
import signal
import time

import pytest

from conftest import make_version


class _ExitedLeader:
    """A harness Popen whose leader already exited (poll() answers 0)."""

    pid = 999_999
    returncode = 0

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


def _building_job(owner="owner-1", proc=None, release=None):
    record = {"proc": proc} if proc is not None else {}
    if release is not None:
        record["release"] = release
    return {"id": "job-b", "status": "building", "mode": "chat", "_cancel": False,
            "_proc": record, "_owner": owner, "endedTime": None}


# ---------- §8: the cancel kill runs whether or not the leader exited ----------

def test_cancel_kills_the_group_and_releases_even_when_the_leader_exited(monkeypatch):
    from autowright import drafting, harness

    kills, released = [], []
    monkeypatch.setattr(harness, "kill_group", lambda proc, sig=None: kills.append(sig))
    monkeypatch.setattr(drafting.DraftJobs, "_publish", staticmethod(lambda j, status=None: None))
    jobs = drafting.DraftJobs()
    jobs.jobs["job-b"] = _building_job(proc=_ExitedLeader(),
                                       release=lambda: released.append(True))
    assert jobs.cancel("job-b") is True
    assert kills and kills[0] == signal.SIGTERM
    assert released == [True]


def test_shutdown_kills_the_group_and_releases_even_when_the_leader_exited(monkeypatch):
    from autowright import drafting, harness

    kills, released = [], []
    monkeypatch.setattr(harness, "kill_group", lambda proc, sig=None: kills.append(sig))
    jobs = drafting.DraftJobs()
    jobs.jobs["job-b"] = _building_job(proc=_ExitedLeader(),
                                       release=lambda: released.append(True))
    jobs.kill_all_building()
    assert kills == [None]
    assert released == [True]
    assert jobs.jobs["job-b"]["status"] == "cancelled"


# ---------- §19: a refused start supersedes nothing ----------

def test_a_refused_start_leaves_the_owners_building_job(monkeypatch):
    from autowright import drafting

    monkeypatch.setattr(drafting.DraftJobs, "_publish", staticmethod(lambda j, status=None: None))
    jobs = drafting.DraftJobs()
    fresh = _building_job()
    held = {"id": "job-held", "status": "done", "mode": "chat", "_cancel": False,
            "_proc": {}, "_owner": "owner-1"}
    jobs.jobs["job-held"] = held
    jobs.jobs["job-b"] = fresh
    with pytest.raises(drafting.DraftRefused) as err:
        jobs.start("chat", {"harness": "Claude Code"}, "x", None, {"agents": [], "secrets": []},
                   owner_id="owner-1", admit=lambda: "discarded")
    assert err.value.reason == "discarded"
    assert fresh["status"] == "building" and fresh["_cancel"] is False
    assert set(jobs.jobs) == {"job-held", "job-b"}


def test_a_stale_post_drafts_does_not_cancel_the_fresh_job(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Fresh", "mock")
    fresh = _building_job(owner=a["id"])
    monkeypatch.setattr(api.draft_jobs, "_run", lambda *args, **kw: None)

    def build_with_a_discard_then_a_fresh_job(*args, **kw):
        # The stale POST's context build: a discard lands, then the fresh
        # draft's own POST registers job B.
        api.delete_draft_container(a["id"])
        api.draft_jobs.jobs["job-b"] = fresh
        return []

    monkeypatch.setattr(api.testexec, "executions_context",
                        build_with_a_discard_then_a_fresh_job)
    before = set(api.draft_jobs.jobs)
    try:
        r = client.post("/drafts", json={"mode": "chat", "text": "x", "agentId": "mock",
                                         "automationId": a["id"]})
        assert r.status_code == 409
        assert r.json()["detail"] == "the draft was discarded"
        assert fresh["status"] == "building"
        assert set(api.draft_jobs.jobs) - before == {"job-b"}
    finally:
        api.draft_jobs.jobs.pop("job-b", None)


# ---------- §8: a scratch notes.md rides beside a blocker ----------

BLOCKED_STDOUT = ("I can't build this yet.\n===BLOCKED===\nblockers:\n"
                  "  - reason: r\n    fix: f\n===END===\n")


def test_recombine_appends_a_scratch_notes_md_after_the_blocker():
    from autowright import drafting, harness

    text = harness._recombine(BLOCKED_STDOUT, [("notes.md", "- learned a thing\n"),
                                               ("spec.md", "# Ignored\n")])
    blockers, notes = drafting.parse_blockers(text)
    assert blockers and blockers[0]["reason"] == "r"
    assert notes == "- learned a thing"
    assert "# Ignored" not in text


def test_recombine_ignores_other_documents_beside_a_blocker():
    from autowright import drafting, harness

    text = harness._recombine(BLOCKED_STDOUT, [("spec.md", "# Ignored\n")])
    assert text == BLOCKED_STDOUT
    blockers, notes = drafting.parse_blockers(text)
    assert blockers and notes is None


def test_output_block_allows_only_notes_md_when_blocked():
    from autowright import drafting

    text = " ".join(drafting.FILE_OUTPUT_BLOCK.split())
    assert "the ONLY file you may still write then is notes.md" in text
    assert "write NO files" not in text


# ---------- §8: an open fence in the stdout prose is closed ----------

def test_recombine_closes_a_fence_the_prose_left_open():
    from autowright import drafting, harness

    text = harness._recombine("Here is the plan:\n```\nstep one\n",
                              [("spec.md", "# Hello\n\nDoes things.\n")])
    files = drafting.parse_envelope(text)
    assert set(files) == {"spec.md"}


def test_recombine_leaves_a_balanced_prose_alone():
    from autowright import harness

    text = harness._recombine("Plan:\n```\nx\n```\n", [("spec.md", "# Hello\n")])
    assert text.startswith("Plan:\n```\nx\n```\n===FILE: spec.md===")


# ---------- §8: OpenCode preamble before a question ----------

def test_opencode_reply_starts_at_the_question_part():
    from autowright import drafting, harness

    handler = harness.OpenCodeHandler({"harness": "OpenCode"}, False)
    handler._texts = ["Let me check the channels…", "===QUESTION===\nWhich channel?"]
    reply = handler.reply("raw")
    assert reply.startswith("===QUESTION===")
    assert drafting.split_answer_kind(reply)[1] == "question"


def test_opencode_reply_without_a_question_joins_every_part():
    from autowright import harness

    handler = harness.OpenCodeHandler({"harness": "OpenCode"}, False)
    handler._texts = ["one", "two"]
    assert handler.reply("raw") == "one\n\ntwo"


# ---------- §8: the sync validator gets the unresolved-references map ----------

GHOST_SECRET = "22222222-2222-4222-8222-222222222222"

IMPORTED_STEPS = f"""===FILE: manifest.yaml===
description: d
note: n
steps:
  - {{ file: 01-a.py, name: A, description: x, secrets: [{{ id: {GHOST_SECRET}, why: auth }}] }}
===FILE: 01-a.py===
from autowright import secrets
x = secrets["{GHOST_SECRET}"]
===END===
"""

FIXED_STEPS = """===FILE: manifest.yaml===
description: d
note: n
steps:
  - { file: 01-a.py, name: A, description: x }
===FILE: 01-a.py===
from autowright import log
log("a")
===END===
"""


def test_sync_repair_round_uses_the_imported_file_copy(home, monkeypatch):
    from autowright import harness
    from autowright.drafting import DraftJobs

    prompts = []

    def fake_invoke(agent, prompt, **kw):
        prompts.append(prompt)
        return IMPORTED_STEPS if len(prompts) == 1 else FIXED_STEPS

    monkeypatch.setattr(harness, "invoke", fake_invoke)
    jobs = DraftJobs()
    current = {"spec": "# T\n\nBody.",
               "unresolved_references": {
                   GHOST_SECRET: {"kind": "secret", "name": "MAIL_PASS", "description": ""}}}
    job_id = jobs.start("sync", {"harness": "Claude Code"}, None, current,
                        {"agents": [], "secrets": []})
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and jobs.get(job_id)["status"] == "building":
        time.sleep(0.05)
    assert len(prompts) >= 2
    assert "still uses MAIL_PASS, which came from the imported file" in prompts[1]


# ---------- §7: DELETE /automations/{id} announces its test rows ----------

def test_delete_automation_publishes_execution_deleted_for_test_rows(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Doomed", "mock")
    test_id = store.create_execution({"id": a["id"], "name": "Doomed"}, "test", None, "test",
                                     [], status="succeeded")["id"]
    published = []
    monkeypatch.setattr(api.hub, "publish", lambda ev, **kw: published.append({"event": ev, **kw}))
    assert client.delete(f"/automations/{a['id']}").status_code == 200
    assert [e["executionId"] for e in published
            if e["event"] == "execution.deleted"] == [test_id]
    assert test_id not in store.execs
