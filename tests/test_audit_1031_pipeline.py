"""2026-10-31 sweep audit batch (drafting pipeline, transfer, installer): the
one-year step timeout cap at the validator and the API model, an integer
iMessage handle as an error, a number param's `min` typed, transfer code the
parser can't handle answering TransferError, and the installer's progress
publish held under the generation check.

Each test names the spec rule it pins (§8 agent pipeline in
`spec/agent-pipeline.md`, §4.1 in `spec/data-model.md`, §5.1 in
`spec/storage.md`, §19 install streaming in `spec/backend-api.md`)."""
import threading
import time

import pytest
from pydantic import ValidationError
from test_audit_1030b_pipeline import _await_state, _validate
from test_transfer import _archive

from autowright import paths, transfer
from autowright.drafting import parse_dialect_entry
from autowright.models import StepIn
from autowright.storage import STEP_TIMEOUT_MAX_S


# ---------- E1 · §8 rule 8 / §4.1: a timeout is at most one year ----------

def test_a_timeout_above_one_year_is_an_error_line():
    draft, errors = _validate(entry="{ file: 01-a.py, name: A, description: d, "
                                    f"timeout: {STEP_TIMEOUT_MAX_S + 1} }}")
    assert draft == {}
    assert "step A: timeout must be at most one year (31536000 seconds)" in errors


def test_a_timeout_of_exactly_one_year_passes():
    draft, errors = _validate(entry="{ file: 01-a.py, name: A, description: d, "
                                    f"timeout: {STEP_TIMEOUT_MAX_S} }}")
    assert errors == []
    assert draft["steps"][0]["timeout"] == STEP_TIMEOUT_MAX_S


def test_the_api_step_model_rejects_a_timeout_above_one_year():
    with pytest.raises(ValidationError):
        StepIn.model_validate({"name": "A", "timeout": STEP_TIMEOUT_MAX_S + 1})
    assert StepIn.model_validate({"name": "A", "timeout": STEP_TIMEOUT_MAX_S}).timeout \
        == STEP_TIMEOUT_MAX_S


# ---------- P2 · §8 rule 9: an integer iMessage handle is an error ----------

def test_an_integer_imessage_handle_names_the_quote_fix():
    entry, error = parse_dialect_entry({"imessage": 15551234567}, cron_source="spec")
    assert entry is None
    assert error == ('triggers: an iMessage handle must be quoted text like '
                     '"+15551234567" (an unquoted number loses its +)')


def test_a_quoted_imessage_handle_still_validates():
    draft, errors = _validate(extra='triggers:\n  - imessage: "+15551234567"')
    assert errors == []
    assert draft["triggers"][0]["from"] == "+15551234567"


# ---------- C3 · §8 rule 3: a number param's `min` is a number ----------

def test_a_text_min_on_a_number_param_is_an_error_line():
    draft, errors = _validate(extra=(
        "params:\n"
        "  - { name: wait, kind: number, label: Wait, default: 5000, min: '1e3' }"))
    assert draft == {}
    assert "param wait: `min` must be a number" in errors


def test_a_number_min_passes():
    draft, errors = _validate(extra=(
        "params:\n"
        "  - { name: wait, kind: number, label: Wait, default: 10, min: 5 }"))
    assert errors == []
    assert draft["params"][0]["min"] == 5


# ---------- P3 · §5.1: code the parser can't handle is a TransferError ----------

def test_import_of_code_too_complex_to_parse_answers_transfer_error(store):
    step = {"file": "01-a.py", "name": "A", "description": "",
            "code": "-" * 1000000 + "1"}
    with pytest.raises(transfer.TransferError, match=r"step '01-a\.py' isn't valid Python"):
        transfer.import_automation(store, _archive(steps=[step]))
    assert store.autos == {}
    assert list(paths.automations_dir().iterdir()) == []


# ---------- P4 · §19: no progress event after the terminal one ----------

def test_an_emit_in_flight_at_the_give_up_publishes_before_the_terminal_event(monkeypatch):
    """§19: the give-up swaps the generation token under the job lock, and the
    progress publish is held under that same lock with the check, so an emit
    that passed the check can't stream after the terminal event."""
    from autowright import installer

    monkeypatch.setattr(installer, "_jobs", {})
    monkeypatch.setattr(installer, "_phases", {})
    monkeypatch.setattr(installer, "INSTALL_TIMEOUT_S", 0.2)
    release = threading.Event()

    def recipe(emit) -> None:
        emit(line="Downloading", percent=10)
        release.wait(10)

    def publish(**kw) -> None:
        if not kw.get("done"):
            # Outlast the install cap: the give-up fires while this progress
            # publish is still in flight.
            time.sleep(0.5)
        events.append(kw)

    monkeypatch.setitem(installer._INSTALLERS, "claude", recipe)
    events = []
    try:
        assert installer.start("claude", publish) is True
        _await_state("claude", "failed")
        deadline = time.monotonic() + 5
        while not (events and events[-1].get("done")) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert [e.get("done") for e in events] == [False, True]
        assert events[-1]["ok"] is False
    finally:
        release.set()
