"""2026-10-30 second-sweep audit batch (drafting pipeline + installer): typed
param defaults/label/help, Build rules read from the spec's list-item blocks,
deeply nested step code as an error line, unquoted iMessage handles, typed
`mention` / `agent` flags, and the installer give-up muting its phase.

Each test names the spec rule it pins (§8 agent pipeline in
`spec/agent-pipeline.md`, §19 install streaming in `spec/backend-api.md`)."""
import threading
import time

import pytest

from autowright.drafting import parse_dialect_entry, parse_envelope, validate_steps

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


def _validate(extra="", entry="{ file: 01-a.py, name: A, description: d }",
              code=CODE):
    return validate_steps(parse_envelope(
        _envelope(entry, files={"01-a.py": code}, extra=extra)), GRANTS)


# ---------- P1 · §8 rule 3: a param default is a value of its kind ----------

@pytest.mark.parametrize("param, error", [
    ("{ name: ports, kind: list, label: Ports, default: [80, 443] }",
     "param ports: the default isn't a list value"),
    ("{ name: wait, kind: text, label: Wait, default: 30 }",
     "param wait: the default isn't a text value"),
    ("{ name: notify, kind: toggle, label: Notify, default: 'yes' }",
     "param notify: the default isn't a toggle value"),
])
def test_a_mistyped_param_default_is_an_error_line(param, error):
    draft, errors = _validate(extra=f"params:\n  - {param}")
    assert draft == {}
    assert error in errors


def test_a_matching_or_null_param_default_passes():
    draft, errors = _validate(extra=(
        "params:\n"
        "  - { name: ports, kind: list, label: Ports, default: ['80'] }\n"
        "  - { name: wait, kind: number, label: Wait, default: null }"))
    assert errors == []
    assert [p["name"] for p in draft["params"]] == ["ports", "wait"]


@pytest.mark.parametrize("field", ["label", "help"])
def test_a_non_text_label_or_help_is_an_error_line(field):
    draft, errors = _validate(extra=(
        f"params:\n  - {{ name: city, kind: text, default: x, {field}: {{ a: 1 }} }}"))
    assert draft == {}
    assert f"param city: `{field}` must be text" in errors


# ---------- P2 · §8: Build rules are the spec's `li` blocks ----------

def test_an_indented_continuation_is_not_part_of_the_rule():
    from autowright.drafting import build_rules_bullets, parse_blockers

    spec = "# T\n\nBody.\n\n## Build rules\n\n- Keep\n  the cache warm\n"
    bullets = build_rules_bullets(spec)
    assert bullets == ["Keep"]
    blocker = ("===BLOCKED===\nblockers:\n  - reason: r\n    fix: f\n"
               "    kind: constraint\n    rule: keep the cache warm\n===END===\n")
    with pytest.raises(ValueError, match="Build rules"):
        parse_blockers(blocker, set(), bullets)


def test_star_plus_and_numbered_lines_are_not_rules():
    from autowright.drafting import build_rules_bullets

    spec = ("# T\n\n## Build rules\n\n* star one\n* star two\n+ plus\n"
            "1. numbered\n- dash rule\n\n# Next\n\n- outside\n")
    assert build_rules_bullets(spec) == ["dash rule"]


# ---------- P3 · §8 rule 4: deeply nested code is an error line ----------

@pytest.mark.parametrize("code", ["x = " + "-" * 10000 + "1\n",
                                  "x = 1" + "+1" * 1000000 + "\n"])
def test_deeply_nested_step_code_is_an_error_line(code):
    draft, errors = _validate(code=code)
    assert draft == {}
    assert "01-a.py: couldn't be parsed (too deeply nested)" in errors


@pytest.mark.parametrize("exc", [MemoryError, RecursionError, ValueError])
def test_a_parser_overflow_is_never_raised(monkeypatch, exc):
    import ast

    from autowright import imports_check

    def overflow(*args, **kwargs):
        raise exc("Parser stack overflowed")

    monkeypatch.setattr(ast, "parse", overflow)
    assert imports_check.disallowed_imports("import os\n") == []
    draft, errors = _validate()
    assert "01-a.py: couldn't be parsed (too deeply nested)" in errors


# ---------- P4 · §8 rule 9: an unquoted iMessage handle keeps its `+` ----------

def test_an_integer_imessage_handle_reads_as_plus_digits():
    entry, error = parse_dialect_entry({"imessage": 15551234567}, cron_source="spec")
    assert error is None
    assert entry["from"] == "+15551234567"


def test_an_unquoted_imessage_handle_in_a_manifest_validates():
    draft, errors = _validate(extra="triggers:\n  - imessage: +15551234567")
    assert errors == []
    assert draft["triggers"][0]["from"] == "+15551234567"


# ---------- P5 · §8 rules 7 and 9: flags are booleans, never truthiness ----------

@pytest.mark.parametrize("value", ['"true"', "1", "yes please"])
def test_a_non_boolean_discord_mention_is_an_error(value):
    import yaml

    t = yaml.safe_load(f"{{ discord: '123', secret: s, mention: {value} }}")
    entry, error = parse_dialect_entry(t, cron_source="spec")
    assert entry is None
    assert error == "triggers: `mention` must be true or false"


def test_a_quoted_false_agent_flag_is_an_error_not_an_agent_step():
    draft, errors = _validate(entry='{ file: 01-a.py, name: A, description: d, agent: "false" }')
    assert draft == {}
    assert "step 1: `agent` must be true or false" in errors
    assert not any("requires a why" in e for e in errors)


# ---------- P6 · §19: the give-up mutes the abandoned phase ----------

def _await_state(provider_id, state):
    from autowright import installer

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        snap = installer.status(provider_id)
        if snap["state"] == state:
            return snap
        time.sleep(0.01)
    raise AssertionError(f"the {provider_id} job never reached {state}")


def test_a_late_emit_after_the_give_up_is_discarded(monkeypatch):
    """§19: "the abandoned phase's late progress is discarded". The give-up
    kept the phase's own generation, so its later emits added progress keys
    to the failed snapshot and published after the terminal event."""
    from autowright import installer

    monkeypatch.setattr(installer, "_jobs", {})
    monkeypatch.setattr(installer, "_phases", {})
    monkeypatch.setattr(installer, "INSTALL_TIMEOUT_S", 0.2)
    emitters = []
    release = threading.Event()

    def recipe(emit) -> None:
        emitters.append(emit)
        release.wait(10)

    monkeypatch.setitem(installer._INSTALLERS, "claude", recipe)
    events = []
    try:
        assert installer.start("claude", lambda **kw: events.append(kw)) is True
        failed = _await_state("claude", "failed")
        assert "timed out" in failed["error"]
        deadline = time.monotonic() + 5
        while not (events and events[-1].get("done")) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert events[-1]["done"] is True and events[-1]["ok"] is False
        published = len(events)

        emitters[0](line="Installing the Ollama app…", percent=90)

        assert installer.status("claude") == failed
        assert len(events) == published
        # The phase is still running, so a retry is still refused.
        assert installer.start("claude", lambda **kw: None) is False
    finally:
        release.set()
