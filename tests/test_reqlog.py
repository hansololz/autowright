"""§5 request-log files: one file per HTTP/agent request under <logs>/requests."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(home):
    from autowright import api
    from autowright.storage import store

    store.load_all()
    store.autos.clear()
    store.execs.clear()
    c = TestClient(api.app)
    c.headers["Authorization"] = f"Bearer {api.AUTH_TOKEN}"
    return c


def _files(home):
    d = home / "logs" / "requests"
    return sorted(d.iterdir()) if d.exists() else []


def test_sanitize_and_filename_shape():
    from autowright import reqlog

    assert reqlog.sanitize("/state") == "state"
    assert reqlog.sanitize("/automations/ab c/execute") == "automations-ab-c-execute"
    assert reqlog.sanitize("/") == "root"
    # stem cap: a huge path truncates, never errors
    long = "/x" * 200
    assert len(f"stamp_GET_{reqlog.sanitize(long)}"[: reqlog.STEM_CAP]) <= reqlog.STEM_CAP


def test_off_by_default_writes_nothing(client, home):
    client.get("/state")
    assert _files(home) == []


def test_http_request_writes_one_file(client, home, devmode):
    client.get("/state")
    files = [p for p in _files(home) if "_GET_state" in p.name]
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "GET /state → 200" in text
    assert "request headers:" in text and "response body" in text
    # bearer token never lands in the file
    from autowright import api
    assert api.AUTH_TOKEN not in text
    assert "authorization: ***" in text


def test_secrets_bodies_redacted(client, home, devmode):
    client.put("/secrets/MY_TOKEN", json={"value": "super-secret-value"})
    files = [p for p in _files(home) if "_PUT_secrets" in p.name]
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "super-secret-value" not in text
    assert "[redacted — secret material]" in text


def test_agent_request_writes_file(home, devmode, monkeypatch):
    import io
    from autowright import harness

    class _P:
        returncode = 0
        stdout = io.StringIO("ok")
        stderr = io.StringIO("")
        # §8: on Windows the prompt is piped here instead of riding argv.
        stdin = io.StringIO()
        def wait(self, timeout=None): return 0
        def poll(self): return 0
        def kill(self): pass

    monkeypatch.setattr(harness, "resolve_bin", lambda name: f"/usr/local/bin/{name}")
    monkeypatch.setattr(harness.subprocess, "Popen", lambda cmd, **kw: _P())
    harness.invoke({"harness": "Claude Code"}, "question: hi?")
    files = [p for p in _files(home) if "_AGENT_Claude-Code" in p.name]
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "prompt (13 chars):\nquestion: hi?" in text
    assert "response (2 chars):\nok" in text


def test_prune_keeps_newest(home, devmode, monkeypatch):
    from autowright import reqlog

    monkeypatch.setattr(reqlog, "MAX_FILES", 5)
    for i in range(8):
        reqlog.write(f"20260726-000000-{i:03d}", "GET", "/state", f"body {i}")
    names = [p.name for p in _files(home)]
    assert len(names) == 5
    assert names[0].startswith("20260726-000000-003")  # oldest three pruned


def test_build_failure_record_written(home, devmode):
    # §5 build-failure records: one self-contained file per validation-failed
    # drafting call — rounds' errors + full responses, blockers, the prompt.
    from autowright import reqlog

    reqlog.write_build_failure(
        reqlog.stamp(), "create", "steps", "Claude Code", "configured default",
        "diagnosed", "PROMPT TEXT",
        [{"errors": ["manifest.yaml is missing"], "response": "bad one"},
         {"errors": ["steps must be nonempty", "spec.md is missing"], "response": "bad two"}],
        [{"reason": "r", "fix": "f", "details": "d"}])
    d = home / "logs" / "build-failures"
    files = sorted(d.iterdir())
    assert len(files) == 1
    assert "_create-steps_diagnosed" in files[0].name
    text = files[0].read_text(encoding="utf-8")
    assert "mode=create · call=steps · harness=Claude Code" in text
    assert "round 1 validation errors (1):\n- manifest.yaml is missing" in text
    assert "round 2 validation errors (2):" in text
    assert "round 2 response (7 chars):\nbad two" in text
    assert "- reason: r\n  fix: f\n  details: d" in text
    assert "prompt (11 chars):\nPROMPT TEXT" in text


def test_build_failure_off_without_devmode(home):
    from autowright import reqlog

    reqlog._dev_cache["t"] = 0.0  # force a re-read of the (absent) setting
    reqlog.write_build_failure(reqlog.stamp(), "create", "spec", "h", "m", "repaired",
                               "p", [{"errors": ["e"], "response": "r"}], None)
    assert not (home / "logs" / "build-failures").exists()


def test_build_failure_prune_keeps_newest(home, devmode, monkeypatch):
    from autowright import reqlog

    monkeypatch.setattr(reqlog, "BUILD_MAX_FILES", 3)
    for i in range(5):
        reqlog.write_build_failure(f"20260726-000000-{i:03d}", "create", "spec", "h", "m",
                                   "repaired", "p", [{"errors": ["e"], "response": "r"}], None)
    names = sorted(p.name for p in (home / "logs" / "build-failures").iterdir())
    assert len(names) == 3
    assert names[0].startswith("20260726-000000-002")


def test_the_kept_names_are_held_in_memory(home, devmode, monkeypatch):
    """§5: a file lands on every request — the kept names are seeded from one
    `iterdir` and appended to afterwards, so no write re-lists the directory."""
    from autowright import reqlog

    monkeypatch.setattr(reqlog, "MAX_FILES", 3)
    reqlog._kept_names.clear()
    d = home / "logs" / "requests"
    listings = []
    real_iterdir = Path.iterdir
    monkeypatch.setattr(Path, "iterdir",
                        lambda self: listings.append(self) or real_iterdir(self))

    for i in range(6):
        reqlog.write(f"20260726-000000-{i:03d}", "GET", "/state", f"body {i}")

    assert listings.count(d) == 1  # only the seeding read
    monkeypatch.undo()
    names = sorted(p.name for p in d.iterdir())
    assert len(names) == 3
    assert names[0].startswith("20260726-000000-003")  # the oldest three pruned
