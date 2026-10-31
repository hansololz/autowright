"""Engine audit regressions (2026-10-31 sweep).

§6.1 a direct firing whose start raises is logged with its traceback, the same
test the queue drain applies: only the engine's admission refusals
(`AdmissionRefused`, `CapacityError`) stay quiet (`spec/engine.md`, "logged
with its traceback by the queue drain and by a direct firing alike").
"""
import logging

import pytest
from conftest import make_version


def _fire_with_start_raising(store, monkeypatch, exc):
    from autowright.engine import Engine
    from autowright.firing import fire_trigger

    engine = Engine(store)
    a = store.create_automation(make_version(), "Raising", None)

    def broken_start(*args, **kwargs):
        raise exc

    monkeypatch.setattr(engine, "start", broken_start)
    return fire_trigger(store, engine, a, {"id": "t1", "kind": "cron", "enabled": True})


def _firing_tracebacks(caplog):
    return [r for r in caplog.records
            if r.name == "autowright.firing" and r.exc_info
            and "failed" in r.getMessage()]


def test_a_plain_runtime_error_from_start_is_logged_with_its_traceback(store, monkeypatch, caplog):
    caplog.set_level(logging.ERROR, logger="autowright.firing")
    assert _fire_with_start_raising(store, monkeypatch, RuntimeError("boom")) is False
    logged = _firing_tracebacks(caplog)
    assert len(logged) == 1
    assert isinstance(logged[0].exc_info[1], RuntimeError)


@pytest.mark.parametrize("refusal", ["AdmissionRefused", "CapacityError"])
def test_an_admission_refusal_from_start_is_not_logged(store, monkeypatch, caplog, refusal):
    from autowright import engine as engine_mod

    caplog.set_level(logging.ERROR, logger="autowright.firing")
    exc = getattr(engine_mod, refusal)("this automation is being deleted")
    assert _fire_with_start_raising(store, monkeypatch, exc) is False
    assert _firing_tracebacks(caplog) == []
