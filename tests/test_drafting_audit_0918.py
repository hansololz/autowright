"""Regression tests for the 2026-09-18 drafting audit — one per fixed item.

Each test names the spec rule it pins (§8 agent pipeline in
`spec/agent-pipeline.md`, §19 draft jobs in `spec/backend-api.md`)."""
import threading
import time

import pytest

from autowright.drafting import (DraftJobs, parse_blockers, parse_envelope,
                                 validate_steps)

GRANTS = {"agents": [], "secrets": []}

STEPS_HEAD = """===FILE: manifest.yaml===
name: Hello
description: Says hello
note: Created
steps:
  - { file: 01-a.py, name: A, description: d }
"""


def one_step_envelope(code: str, manifest: str | None = None) -> str:
    """A minimal valid sync envelope holding one step whose body is `code`."""
    return (manifest or STEPS_HEAD) + f"===FILE: 01-a.py===\n{code}\n===END===\n"


# ---------- §8 envelope rule 1: ===FILE: detection is shape-aware ----------

def test_a_fenced_file_marker_is_prose_never_an_envelope():
    """§8 rule 1: a chat reply whose only ===FILE: line sits inside a markdown
    fence is an answer showing what an actions.yaml looks like — it must never
    arm a sync."""
    raw = ("Sure — an actions.yaml looks like this:\n\n"
           "```yaml\n"
           "===FILE: actions.yaml===\n"
           "sync: true\n"
           "```\n\n"
           "Say the word and I'll run it.")
    outcome, payload, _, _, _ = DraftJobs._chat_classify(raw)
    assert outcome == "done"
    assert "actions" not in payload
    assert payload["answer"] == raw.strip()


def test_a_fenced_block_body_still_parses_inside_a_real_envelope():
    """§8 rule 1: fence state decides only where the envelope STARTS — a
    block's own content may be fenced (models love ```python around step
    code), and _strip_fence takes it off."""
    raw = one_step_envelope("```python\nfrom autowright import log\nlog(\"a\")\n```")
    files = parse_envelope(raw)
    assert files["01-a.py"] == 'from autowright import log\nlog("a")\n'


def test_a_fenced_marker_before_a_real_envelope_parses_from_the_real_one():
    """§8 rule 1: the envelope starts at the first UNFENCED marker — an
    example quoted above it is prose, not a block."""
    raw = ("Here's the shape:\n\n```yaml\n===FILE: spec.md===\n# not real\n```\n\n"
           "===FILE: notes.md===\n- the real one\n===END===\n")
    files = parse_envelope(raw)
    assert set(files) == {"notes.md"}
    outcome, payload, _, _, _ = DraftJobs._chat_classify(raw)
    assert outcome == "done"
    assert payload["notes"] == "- the real one"
    # the fenced example rides along as part of the answer prose
    assert payload["answer"].endswith("```")


def test_a_blocker_that_quotes_a_fenced_envelope_carries_no_notes():
    """§8 rule 1: the file-block scan beside a blocker envelope is shape-aware
    too — a blocker whose prose SHOWS what a notes.md block looks like is a
    clean no-notes blocker, never a quoted example adopted as the notes."""
    raw = ("A notes block looks like this:\n\n"
           "```md\n===FILE: notes.md===\n- quoted\n===END===\n```\n\n"
           "===BLOCKED===\n"
           "blockers:\n"
           "  - reason: I can't reach the API\n"
           "    fix: Give me a token\n"
           "===END===\n")
    blockers, notes = parse_blockers(raw)
    assert notes is None
    assert [b["reason"] for b in blockers] == ["I can't reach the API"]


def test_an_all_fenced_envelope_has_no_blocks():
    raw = "```\n===FILE: notes.md===\n- quoted\n===END===\n```\n"
    with pytest.raises(ValueError, match="no ===FILE: blocks"):
        parse_envelope(raw)


# ---------- §8 rules 6/7: name-literal subscripts are scanned too ----------

def test_a_secret_name_subscript_is_a_validation_error():
    """§8 rule 6: the scan matches ANY quoted literal — the name where the id
    belongs is the likeliest slip, and it must fail here rather than at
    runtime."""
    raw = one_step_envelope('token = secrets["API_TOKEN"]')
    _, errors = validate_steps(parse_envelope(raw), GRANTS)
    assert errors == ["step A: code subscripts secrets['API_TOKEN'], which isn't "
                      "among the allowed secrets — allowed: none"]


def test_an_agent_name_subscript_is_a_validation_error():
    """§8 rule 7: the agents scan matches any quoted literal the same way."""
    raw = one_step_envelope('answer = agents["Claude"].ask("what?")')
    _, errors = validate_steps(parse_envelope(raw), GRANTS)
    assert errors == ["step A: code subscripts agents['Claude'] but the step "
                      "declares no agents entries"]


def test_a_granted_secret_uuid_still_passes():
    sid = "11111111-2222-3333-4444-555555555555"
    raw = one_step_envelope(f'token = secrets["{sid}"]  # TOKEN')
    grants = {"agents": [], "secrets": [{"id": sid, "name": "Token"}]}
    draft, errors = validate_steps(parse_envelope(raw), grants)
    assert errors == []
    assert draft["secretReferences"] == [sid]


# ---------- §8 rule 3: manifest field types + duplicate params ----------

def test_a_yaml_boolean_step_name_is_a_validation_error():
    """§8 rule 3: YAML 1.1 turns `name: on` into a boolean, which would land
    verbatim in the version and break the §9.2 page and the §5.1 importer."""
    manifest = ("===FILE: manifest.yaml===\n"
                "name: Hello\n"
                "description: Says hello\n"
                "steps:\n"
                "  - { file: 01-a.py, name: on, description: d }\n")
    raw = one_step_envelope("x = 1", manifest)
    _, errors = validate_steps(parse_envelope(raw), GRANTS)
    assert "step 1: `name` must be a string" in errors


def test_a_mapping_step_description_is_a_validation_error():
    manifest = ("===FILE: manifest.yaml===\n"
                "name: Hello\n"
                "description: Says hello\n"
                "steps:\n"
                "  - file: 01-a.py\n"
                "    name: A\n"
                "    description:\n"
                "      what: it does\n")
    raw = one_step_envelope("x = 1", manifest)
    _, errors = validate_steps(parse_envelope(raw), GRANTS)
    assert "step 1: `description` must be a string" in errors


def test_a_mapping_why_never_crashes_the_validator():
    """§8 rule 3: the agent-step `why` check reads the field as text — a
    mapping there used to raise out of validate_steps instead of failing it."""
    manifest = ("===FILE: manifest.yaml===\n"
                "name: Hello\n"
                "description: Says hello\n"
                "steps:\n"
                "  - file: 01-a.py\n"
                "    name: A\n"
                "    description: d\n"
                "    agent: true\n"
                "    why:\n"
                "      because: judgment\n")
    raw = one_step_envelope("x = 1", manifest)
    _, errors = validate_steps(parse_envelope(raw), GRANTS)
    assert "step 1: `why` must be a string" in errors


def test_a_non_string_manifest_note_is_a_validation_error():
    manifest = ("===FILE: manifest.yaml===\n"
                "name: Hello\n"
                "description: Says hello\n"
                "note: on\n"
                "steps:\n"
                "  - { file: 01-a.py, name: A, description: d }\n")
    raw = one_step_envelope("x = 1", manifest)
    _, errors = validate_steps(parse_envelope(raw), GRANTS)
    assert "manifest `note` must be a string" in errors


def test_a_duplicate_param_name_is_a_validation_error():
    """§8 rule 3: two entries of one name would silently collapse — one error
    per repeated name feeds the repair round."""
    manifest = ("===FILE: manifest.yaml===\n"
                "name: Hello\n"
                "description: Says hello\n"
                "params:\n"
                "  - { name: url, kind: text, label: A, help: h, default: '' }\n"
                "  - { name: url, kind: text, label: B, help: h, default: '' }\n"
                "  - { name: url, kind: text, label: C, help: h, default: '' }\n"
                "steps:\n"
                "  - { file: 01-a.py, name: A, description: d }\n")
    raw = one_step_envelope("x = 1", manifest)
    _, errors = validate_steps(parse_envelope(raw), GRANTS)
    assert errors == ["param `url` is declared twice"]


def test_a_non_string_param_name_is_a_validation_error():
    manifest = ("===FILE: manifest.yaml===\n"
                "name: Hello\n"
                "description: Says hello\n"
                "params:\n"
                "  - { name: on, kind: toggle, label: A, help: h, default: true }\n"
                "steps:\n"
                "  - { file: 01-a.py, name: A, description: d }\n")
    raw = one_step_envelope("x = 1", manifest)
    _, errors = validate_steps(parse_envelope(raw), GRANTS)
    assert any("`name` must be a nonempty string" in e for e in errors)


# ---------- §19: one building job per owner ----------

def test_a_second_job_for_one_owner_cancels_the_first(monkeypatch):
    """§19 POST /drafts: one BUILDING job per owner is a backend invariant —
    the previous job's harness is killed, its record settles cancelled and is
    consumed at once, so two agent runs can never write the same draft."""
    from autowright import harness

    release = threading.Event()
    started = threading.Event()

    def blocking_invoke(agent, prompt, timeout=300, proc_holder=None, on_chunk=None,
                        should_abort=None, web=False, on_tool=None, on_file=None):
        started.set()
        release.wait(10)
        return "===FILE: spec.md===\n# Hello\n\nDoes things.\n===END===\n"

    monkeypatch.setattr(harness, "invoke", blocking_invoke)
    jobs = DraftJobs()
    first = jobs.start("chat", {"harness": "Claude Code"}, "one",
                       {"spec": "# T\n\nbody"}, GRANTS, owner_id="auto-1")
    assert started.wait(5)
    with jobs._lock:
        first_record = jobs.jobs[first]
    second = jobs.start("chat", {"harness": "Claude Code"}, "two",
                        {"spec": "# T\n\nbody"}, GRANTS, owner_id="auto-1")
    try:
        assert first_record["status"] == "cancelled"
        assert first_record["_cancel"] is True
        # consumed at once: the superseded record is dropped, and the owner's
        # job ref is the new job, never the cancelled one
        assert jobs.get(first) is None
        ref = jobs.job_for("auto-1")
        assert ref["jobId"] == second
        assert jobs.get(second)["status"] == "building"
    finally:
        release.set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and jobs.get(second)["status"] == "building":
            time.sleep(0.05)


def test_a_second_job_for_another_owner_leaves_the_first_building(monkeypatch):
    """§19: the invariant is per owner — a different owner's job runs on."""
    from autowright import harness

    release = threading.Event()
    started = threading.Event()

    def blocking_invoke(agent, prompt, timeout=300, proc_holder=None, on_chunk=None,
                        should_abort=None, web=False, on_tool=None, on_file=None):
        started.set()
        release.wait(10)
        return "===FILE: spec.md===\n# Hello\n\nDoes things.\n===END===\n"

    monkeypatch.setattr(harness, "invoke", blocking_invoke)
    jobs = DraftJobs()
    first = jobs.start("chat", {"harness": "Claude Code"}, "one",
                       {"spec": "# T\n\nbody"}, GRANTS, owner_id="auto-1")
    assert started.wait(5)
    second = jobs.start("chat", {"harness": "Claude Code"}, "two",
                        {"spec": "# T\n\nbody"}, GRANTS, owner_id="auto-2")
    try:
        assert jobs.get(first)["status"] == "building"
        assert jobs.get(second)["status"] == "building"
    finally:
        release.set()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and any(
                jobs.get(j)["status"] == "building" for j in (first, second)):
            time.sleep(0.05)


# ---------- §8: ===END=== tolerates a trailing carriage return ----------

def test_a_crlf_end_marker_closes_the_envelope():
    raw = "===FILE: notes.md===\n- a thing\n===END===\r\n"
    assert parse_envelope(raw) == {"notes.md": "- a thing\n"}
