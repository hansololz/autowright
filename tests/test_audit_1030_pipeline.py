"""2026-10-30 audit batch (drafting pipeline): scalar step lists never raise,
the lock splice's returned-block snapshot, byte-exact fence stripping, the
keep-`user` settings carry-over, the CURRENT packages sync section, quoted
Build rules, the stored-suffix rule, the full locked-step preflight, the
cleared harness proc, trimmed step names, and the shutdown start refusal.

Each test names the spec rule it pins (§8 agent pipeline in
`spec/agent-pipeline.md`, §19 POST /drafts in `spec/backend-api.md`)."""
import pytest

from autowright.drafting import parse_envelope, validate_steps

GRANTS = {"agents": [], "secrets": []}
CODE = 'from autowright import log\nlog("x")\n'


def _envelope(*entries, files=None, extra=""):
    lines = ["===FILE: manifest.yaml===", "note: n"]
    if extra:
        lines.append(extra)
    lines.append("steps:")
    lines += [f"  - {e}" for e in entries]
    for name, code in (files or {}).items():
        lines += [f"===FILE: {name}===", code.rstrip("\n")]
    return "\n".join(lines) + "\n===END===\n"


# ---------- P1 · §8 rule 3: a scalar list field is an error line ----------

@pytest.mark.parametrize("field", ["secrets: 3", "packages: true", "agents: 1.5"])
def test_scalar_list_field_is_an_error_line_not_a_type_error(field):
    agent = ", agent: true, why: w" if field.startswith("agents") else ""
    text = _envelope(f"{{ file: 01-a.py, name: A, description: d{agent}, {field} }}",
                     files={"01-a.py": CODE})
    draft, errors = validate_steps(parse_envelope(text), GRANTS)
    assert draft == {}
    assert errors and any(field.split(":")[0] in e for e in errors)


def test_a_crashing_validator_is_an_error_line_never_a_failed_job():
    from autowright.drafting import DraftJobs

    def boom(files):
        raise TypeError("'int' object is not iterable")

    result, errors, blockers, notes = DraftJobs._parse_validate(
        _envelope("{ file: 01-a.py, name: A, description: d }",
                  files={"01-a.py": CODE}), boom)
    assert result == {} and blockers is None
    assert len(errors) == 1 and "not iterable" in errors[0]


# ---------- P2 · §8 rule 3: the splice checks only the agent's own blocks ----------

def test_two_locked_steps_with_the_same_suffix_splice_cleanly():
    a = {"file": "02-new-step.py", "name": "A", "description": "a",
         "code": 'from autowright import log\nlog("a")\n', "locked": True}
    b = {"file": "03-new-step.py", "name": "B", "description": "b",
         "code": 'from autowright import log\nlog("b")\n', "locked": True}
    text = _envelope("{ file: 01-fetch.py, name: Fetch, description: d }",
                     "{ file: 02-b.py, name: B, locked: true }",
                     "{ file: 03-a.py, name: A, locked: true }",
                     files={"01-fetch.py": CODE})
    draft, errors = validate_steps(parse_envelope(text), GRANTS,
                                   locked_steps=[a, b], current_steps=[a, b])
    assert errors == []
    by_file = {s["file"]: s for s in draft["steps"]}
    assert by_file["02-new-step.py"]["name"] == "B"
    assert by_file["02-new-step.py"]["code"] == b["code"]
    assert by_file["03-new-step.py"]["name"] == "A"
    assert by_file["03-new-step.py"]["code"] == a["code"]


# ---------- P3 · §8: fence stripping keeps every code byte ----------

def test_fenced_code_with_line_separator_characters_survives_byte_exact():
    code = 'SEP = "a b c\u0085d\x0be\x0cf\x1cg\x1dh\x1ei"\n'
    text = ("===FILE: manifest.yaml===\nsteps: []\n"
            "===FILE: 01-a.py===\n```python\n" + code + "```\n===END===\n")
    files = parse_envelope(text)
    assert files["01-a.py"] == code


def test_locked_block_with_a_form_feed_is_byte_identical_through_the_fence():
    stored = 'from autowright import log\n\x0c\nlog("x")\n'
    locked = {"file": "01-a.py", "name": "A", "description": "d", "code": stored,
              "locked": True}
    text = _envelope("{ file: 01-a.py, name: A, locked: true }",
                     files={"01-a.py": "```python\n" + stored + "```"})
    draft, errors = validate_steps(parse_envelope(text), GRANTS,
                                   locked_steps=[locked], current_steps=[locked])
    assert errors == []
    assert draft["steps"][0]["code"] == stored


# ---------- P4 · §8 rule 3 keep-user: stored settings carry over ----------

def test_kept_user_step_keeps_stored_timeout_retries_and_description():
    stored = {"file": "01-a.py", "name": "A", "description": "mine",
              "code": CODE, "authoredBy": "user", "timeout": 900, "retries": 3}
    text = _envelope("{ file: 01-a.py, name: A, description: agent words, no_timeout: true }",
                     files={"01-a.py": CODE})
    draft, errors = validate_steps(parse_envelope(text), GRANTS,
                                   locked_steps=[], current_steps=[stored])
    assert errors == []
    step = draft["steps"][0]
    assert step["authored_by"] == "user"
    assert step["description"] == "mine"
    assert step["timeout"] == 900 and "no_timeout" not in step
    assert step["retries"] == 3 and "infinite_retries" not in step


def test_kept_user_step_reads_stored_camel_case_flags():
    stored = {"file": "01-a.py", "name": "A", "description": "mine", "code": CODE,
              "authoredBy": "user", "noTimeout": True, "infiniteRetries": True}
    text = _envelope("{ file: 01-a.py, name: A, description: d, timeout: 30, retries: 2 }",
                     files={"01-a.py": CODE})
    draft, errors = validate_steps(parse_envelope(text), GRANTS,
                                   locked_steps=[], current_steps=[stored])
    assert errors == []
    step = draft["steps"][0]
    assert step["no_timeout"] is True and "timeout" not in step
    assert step["infinite_retries"] is True and "retries" not in step


def test_rewritten_user_step_keeps_the_agent_entry():
    stored = {"file": "01-a.py", "name": "A", "description": "mine",
              "code": CODE, "authoredBy": "user", "timeout": 900}
    text = _envelope("{ file: 01-a.py, name: A, description: new }",
                     files={"01-a.py": 'from autowright import log\nlog("y")\n'})
    draft, errors = validate_steps(parse_envelope(text), GRANTS,
                                   locked_steps=[], current_steps=[stored])
    assert errors == []
    step = draft["steps"][0]
    assert "authored_by" not in step
    assert step["description"] == "new" and "timeout" not in step


# ---------- P5 · §8: the sync prompt carries the CURRENT packages ----------

def test_sync_prompt_carries_the_current_packages(monkeypatch):
    from autowright import drafting, harness

    monkeypatch.setattr(harness, "probe_tools", lambda: [])
    current = {"spec": "# T", "steps": [{"file": "01-a.py", "name": "A", "code": "import cv2\n"}],
               "packages": [{"pip": "opencv-python", "import": "cv2", "why": "reads frames"}]}
    prompt = drafting.build_steps_prompt("# T", current, GRANTS)
    assert "=== CURRENT packages" in prompt
    section = prompt.split("=== CURRENT packages", 1)[1].split("\n\n", 1)[0]
    assert "pip: opencv-python" in section and "import: cv2" in section
    assert "why: reads frames" in section
    bare = drafting.build_steps_prompt("# T", {**current, "packages": []}, GRANTS)
    assert "=== CURRENT packages" not in bare


# ---------- P7 · §8: a constraint `rule` quotes a Build rules bullet ----------

SPEC_WITH_RULES = """# Title

Body.

## Build Rules:

- Retry the fetch three times
- Keep
  the cache warm

### Detail

- nested bullet stays inside

## Other

- not a rule
"""


def test_build_rules_bullets_are_read_from_the_spec():
    from autowright.drafting import build_rules_bullets

    assert build_rules_bullets(SPEC_WITH_RULES) == [
        "Retry the fetch three times", "Keep the cache warm", "nested bullet stays inside"]
    assert build_rules_bullets("# T\n\nno rules\n") == []


def _constraint(rule):
    return ("===BLOCKED===\nblockers:\n  - reason: r\n    fix: f\n"
            f"    kind: constraint\n    rule: {rule}\n===END===\n")


def test_quoted_rule_passes_case_insensitively():
    from autowright.drafting import build_rules_bullets, parse_blockers

    blockers, _ = parse_blockers(_constraint("retry the FETCH"), set(),
                                 build_rules_bullets(SPEC_WITH_RULES))
    assert blockers[0]["rule"] == "retry the FETCH"


def test_paraphrased_rule_raises():
    from autowright.drafting import build_rules_bullets, parse_blockers

    with pytest.raises(ValueError, match="Build rules"):
        parse_blockers(_constraint("always fetch thrice"), set(),
                       build_rules_bullets(SPEC_WITH_RULES))
    with pytest.raises(ValueError, match="no Build rules"):
        parse_blockers(_constraint("anything"), set(), [])
    # Unknown rule set (None) skips the check.
    assert parse_blockers(_constraint("anything"), set())[0]


# ---------- P8 · §8 rule 3: the stored suffix and the full preflight ----------

@pytest.mark.parametrize("stored_file, expected", [
    ("fetch-page.py", "02-fetch-page.py"),
    ("x-.py", "02-x-.py"),
    ("07-fetch-page.py", "02-fetch-page.py"),
])
def test_splice_strips_only_a_leading_position_prefix(stored_file, expected):
    locked = {"file": stored_file, "name": "L", "description": "d", "code": CODE,
              "locked": True}
    text = _envelope("{ file: 01-a.py, name: A, description: d }",
                     "{ file: 02-l.py, name: L, locked: true }",
                     files={"01-a.py": CODE})
    draft, errors = validate_steps(parse_envelope(text), GRANTS,
                                   locked_steps=[locked], current_steps=[locked])
    assert errors == []
    assert [s["file"] for s in draft["steps"]] == ["01-a.py", expected]


def test_preflight_surfaces_a_bad_filename_locked_step():
    from autowright.drafting import locked_step_preflight

    current = {"steps": [{"file": "01-Fetch Page.py", "name": "Fetch", "description": "d",
                          "code": CODE, "locked": True}]}
    blockers = locked_step_preflight(current, GRANTS)
    assert len(blockers) == 1
    assert blockers[0]["step"] == "Fetch" and blockers[0]["kind"] == "constraint"
    assert "'01-Fetch Page.py'" in blockers[0]["reason"]
    assert "NN-name.py" in blockers[0]["reason"]


def test_preflight_surfaces_a_missing_file_and_a_blank_name():
    from autowright.drafting import locked_step_preflight

    missing = locked_step_preflight(
        {"steps": [{"file": None, "name": "Gone", "code": CODE, "locked": True}]}, GRANTS)
    assert [b["step"] for b in missing] == ["Gone"]
    assert "the step's file is missing" in missing[0]["reason"]
    blank = locked_step_preflight(
        {"steps": [{"file": "01-a.py", "name": "  ", "code": CODE, "locked": True}]}, GRANTS)
    assert len(blank) == 1 and "needs a name" in blank[0]["reason"]


# ---------- P9 · §8: a finished harness call leaves no proc behind ----------

def test_invoke_clears_the_held_proc_when_the_call_returns(monkeypatch):
    from autowright import drafting, harness

    def fake_invoke(agent, prompt, proc_holder=None, **kw):
        proc_holder["proc"] = object()
        proc_holder["release"] = lambda: None
        return "ok"

    monkeypatch.setattr(harness, "invoke", fake_invoke)
    jobs = drafting.DraftJobs()
    job = {"id": "j", "status": "building", "events": [], "_cancel": False, "_proc": {}}
    assert jobs._invoke(job, {"harness": "Claude Code"}, "p") == "ok"
    assert job["_proc"] == {}


# ---------- P10 · §4.1: names are stored and compared trimmed ----------

def test_padded_names_are_trimmed_and_match_locked_steps():
    from autowright.drafting import parse_blockers

    locked = {"file": "02-fetch.py", "name": "Fetch ", "description": "d", "code": CODE,
              "locked": True}
    text = _envelope("{ file: 01-a.py, name: ' A ', description: d }",
                     "{ file: 02-fetch.py, name: Fetch, locked: true }",
                     files={"01-a.py": CODE})
    draft, errors = validate_steps(parse_envelope(text), GRANTS,
                                   locked_steps=[locked], current_steps=[locked])
    assert errors == []
    assert [s["name"] for s in draft["steps"]] == ["A", "Fetch"]
    raw = ("===BLOCKED===\nblockers:\n  - reason: r\n    fix: f\n"
           "    kind: constraint\n    step: Fetch\n===END===\n")
    assert parse_blockers(raw, {"Fetch "})[0][0]["step"] == "Fetch"


# ---------- A5 · §3/§19: a stopping backend refuses new draft jobs ----------

def test_start_is_refused_once_the_backend_is_stopping():
    from autowright.drafting import DraftJobs, DraftRefused

    jobs = DraftJobs()
    jobs.kill_all_building()
    with pytest.raises(DraftRefused) as refused:
        jobs.start("sync", {"harness": "Claude Code"}, None, {"spec": "# T"}, GRANTS)
    assert refused.value.reason == "stopping"
    assert jobs.jobs == {}


def test_chat_constraint_blocker_with_a_paraphrased_rule_is_invalid():
    from autowright.drafting import DraftJobs, build_rules_bullets

    rules = build_rules_bullets(SPEC_WITH_RULES)
    outcome, payload, *_ = DraftJobs._chat_classify(
        _constraint("always fetch thrice"), [], 0, locked_names=set(), build_rules=rules)
    assert outcome == "invalid" and "Build rules" in payload[0]
    outcome, payload, *_ = DraftJobs._chat_classify(
        _constraint("retry the fetch"), [], 0, locked_names=set(), build_rules=rules)
    assert outcome == "blocked" and payload["blockers"][0]["rule"] == "retry the fetch"
