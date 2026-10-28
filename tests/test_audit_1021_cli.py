"""§20 CLI and §5.1/§5.2 transfer regressions (2026-10-21 audit): the empty
data path, the duplicate warning and timeout, the `Keys:` help block, a
non-UTF-8 secret on stdin, kv JSON values, `agent check` ambiguity, a
too-late cancel, the trickled download deadline, and an export with a step
script missing on disk."""
import argparse
import io

import pytest
from conftest import make_version
from test_cli import AUTO_ID, FULL_AUTO, _RouteClient, _run

from autowright import transfer
from autowright.storage import MissingStepScriptError


# ---------- §20 settings set: an empty dataPath is a usage error ----------

def test_settings_set_empty_data_path_is_a_usage_error():
    c = _RouteClient()
    with pytest.raises(SystemExit) as ei:
        _run(c, "settings", "set", "dataPath=")
    assert ei.value.code == "dataPath can't be empty"
    assert c.calls == []  # never the current directory adopted


# ---------- §20 automation duplicate: 660 s timeout and the warning line ----------

def test_duplicate_runs_under_the_long_timeout_and_warns_on_stderr(capsys):
    c = _RouteClient({"/automations": [FULL_AUTO]},
                     reply={"id": "dup12345-0000", "name": "Daily Report 2",
                            "warning": "the memory wasn't copied"})
    _run(c, "automation", "duplicate", "Daily Report", "--copy-memory")
    assert c.timeouts == [("POST", f"/automations/{AUTO_ID}/duplicate", 660)]
    out = capsys.readouterr()
    assert "duplicated 'Daily Report' as 'Daily Report 2' [dup12345]" in out.out
    assert out.err == "warning: the memory wasn't copied\n"


def test_duplicate_without_a_warning_prints_nothing_on_stderr(capsys):
    c = _RouteClient({"/automations": [FULL_AUTO]},
                     reply={"id": "dup12345-0000", "name": "Daily Report 2"})
    _run(c, "automation", "duplicate", "Daily Report")
    assert capsys.readouterr().err == ""


def test_duplicate_json_carries_the_warning_field(capsys):
    import json

    reply = {"id": "dup12345-0000", "name": "Daily Report 2", "warning": "memory skipped"}
    c = _RouteClient({"/automations": [FULL_AUTO]}, reply=reply)
    _run(c, "automation", "duplicate", "Daily Report", "--json")
    out = capsys.readouterr()
    assert json.loads(out.out)["warning"] == "memory skipped"
    assert out.err == ""


# ---------- §20 help rendering: a `Keys:` block stays line-for-line ----------

def test_automation_set_help_keeps_the_key_lines():
    from autowright import cli

    parser = cli.build_parser(full=True)
    for word in ("automation", "set"):
        parser = next(a for a in parser._actions
                      if isinstance(a, argparse._SubParsersAction)).choices[word]
    lines = [line.strip() for line in parser.format_help().splitlines()]
    assert "Keys:" in lines
    assert "notifications=inherit|attention|all|never" in lines
    assert "maxParallel=<int ≥ 1>" in lines
    assert "maxQueued=<int ≥ 0>" in lines


# ---------- §20 secret set --stdin: bytes that aren't UTF-8 ----------

def test_secret_set_stdin_that_is_not_utf8_exits_1(monkeypatch, capsys):
    from autowright import cli

    stdin = io.TextIOWrapper(io.BytesIO(b"\xff\xfe\xfa"), encoding="utf-8")
    monkeypatch.setattr(cli.sys, "stdin", stdin)
    c = _RouteClient({"/secrets": []})
    with pytest.raises(SystemExit) as ei:
        _run(c, "secret", "set", "API_TOKEN", "--stdin")
    assert ei.value.code == "stdin isn't valid UTF-8"
    assert c.calls == []
    assert capsys.readouterr().out == ""


# ---------- §20 param value forms: kv JSON values are JSON text ----------

def test_kv_json_object_values_become_json_text():
    from autowright.cli import parse_param_value

    p = {"name": "headers", "kind": "kv"}
    rows = parse_param_value(p, '{"a": "x", "b": null, "c": true, "d": 3, '
                                '"e": {"f": [1, 2]}}')
    assert rows == [{"key": "a", "value": "x"}, {"key": "b", "value": "null"},
                    {"key": "c", "value": "true"}, {"key": "d", "value": "3"},
                    {"key": "e", "value": '{"f": [1, 2]}'}]


# ---------- §20 reference resolution: `agent check` ambiguity ----------

def test_agent_check_with_a_shared_name_exits_with_the_candidates():
    agents = [{"id": "aaaa1111-x", "name": "Coder", "harness": "Claude Code"},
              {"id": "bbbb2222-y", "name": "coder", "harness": "Codex"}]
    c = _RouteClient({"/agents": agents})
    with pytest.raises(SystemExit) as ei:
        _run(c, "agent", "check", "Coder")
    msg = str(ei.value.code)
    assert "'Coder' is ambiguous" in msg
    assert "Coder (aaaa1111)" in msg and "coder (bbbb2222)" in msg
    assert c.calls == []  # no probe ran against the first match


# ---------- §20 execution cancel: too late ----------

def test_execution_cancel_on_a_finished_record_says_too_late(capsys):
    execution = {"id": "e1111111-a", "automationName": "Daily Report",
                 "status": "succeeded", "started": "2026-07-29 08:00"}
    c = _RouteClient({"/executions?idPrefix=e111&limit=50":
                      {"executions": [execution], "total": 1}},
                     reply={"ok": False})
    with pytest.raises(SystemExit) as ei:
        _run(c, "execution", "cancel", "e111")
    assert ei.value.code == "too late - it had already finished"
    assert "cancelled" not in capsys.readouterr().out


def test_execution_cancel_that_lands_prints_cancelled(capsys):
    execution = {"id": "e1111111-a", "automationName": "Daily Report",
                 "status": "executing", "started": "2026-07-29 08:00"}
    c = _RouteClient({"/executions?idPrefix=e111&limit=50":
                      {"executions": [execution], "total": 1}},
                     reply={"ok": True})
    _run(c, "execution", "cancel", "e111")
    assert "cancelled e1111111" in capsys.readouterr().out


# ---------- §5.2 download: read1 lets the deadline see trickled bytes ----------

def test_fetch_archive_times_out_on_one_byte_per_read1(monkeypatch):
    class _Trickle:
        def __init__(self):
            self.reads = 0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def geturl(self):
            return "https://example.test/pack.autowright"

        def read(self, n=-1):
            raise AssertionError("read() fills its buffer - fetch_archive must use read1")

        def read1(self, n=-1):
            self.reads += 1
            return b"x"

    response = _Trickle()
    clock = iter(range(0, 10_000, 100))  # each read1 is 100 s later
    monkeypatch.setattr(transfer.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(transfer, "resolve_url", lambda url: url)
    monkeypatch.setattr(transfer, "open_https_only",
                        lambda req, timeout, refuse: response)
    with pytest.raises(transfer.TransferError, match="timed out"):
        transfer.fetch_archive("https://example.test/pack.autowright")
    # 600 s deadline from t=0, taken before the connect (whose socket timeout
    # reads the clock at t=100): the read at t=700 is the one refused
    assert response.reads == 6


# ---------- §5.1 export: a step script missing on disk ----------

def test_export_refuses_a_step_script_missing_on_disk(store):
    a = store.create_automation(make_version(), name="Missing", agent_id=None, triggers=[])
    version_dir = store.auto_dir(a) / "versions" / f"v{a['current_version']}"
    step = a["versions"][a["current_version"]]["steps"][0]
    (version_dir / step["file"]).unlink()
    # The §19 restore's own error, so the API answers 409 for both (§5).
    with pytest.raises(MissingStepScriptError) as ei:
        transfer.export_automation(store, a)
    assert str(ei.value) == f"step script `{step['file']}` is missing on disk"
