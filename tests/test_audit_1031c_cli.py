"""CLI audit regressions (2026-10-31 third sweep).

§20: every record field the CLI prints is console-safe; `vN` is `v` plus
ASCII digits; agent names compare through the §20 name key; a non-JSON reply
with a 5xx status is the backend answering; pull writes the manifest last
through temp files, refuses another automation's workdir unless --force, and
workdir text reads drop a BOM; a secret name is matched whole.
"""
import copy
import io
import os
import urllib.error

import pytest
import yaml
from test_cli import FULL_AUTO, _auto_gets, _run, _RouteClient, _WorkdirClient

from autowright import cli

HOSTILE = "Report\x1b[2K\r"
EXECUTION_ID = "e1aaaaaa-0000-0000-0000-000000000000"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    monkeypatch.setenv("no_proxy", "*")


def _assert_escaped(out):
    assert "\x1b" not in out and "\r" not in out
    assert "Report\\x0d" in out


# ---------- AA. record fields on the console ----------

class _ExecutionClient:
    def __init__(self, full):
        self.full = full

    def req(self, method, path, body=None, timeout=30):
        if path == f"/executions/{EXECUTION_ID}":
            return self.full
        row = {k: self.full[k] for k in ("id", "automationName", "versionLabel", "status",
                                          "duration", "trigger", "started")}
        return {"executions": [row], "total": 1}


def _execution(**extra):
    return {"id": EXECUTION_ID, "automationName": HOSTILE, "versionLabel": "v1",
            "status": "failed", "duration": "2s", "trigger": "discord",
            "started": "2026-10-30T08:00:00", "steps": [], **extra}


def test_execution_list_prints_a_hostile_automation_name_escaped(capsys):
    _run(_ExecutionClient(_execution()), "execution", "list")
    _assert_escaped(capsys.readouterr().out)


def test_trigger_list_prints_a_hostile_label_escaped(capsys):
    auto = dict(copy.deepcopy(FULL_AUTO), name=HOSTILE)
    auto["triggers"] = [{"id": "t1", "kind": "cron", "label": HOSTILE, "short": HOSTILE,
                         "enabled": True}]
    _run(_RouteClient(_auto_gets(auto)), "automation", "trigger", "list", auto["id"][:8])
    _assert_escaped(capsys.readouterr().out)


def test_trigger_off_and_remove_print_the_label_escaped(capsys):
    auto = dict(copy.deepcopy(FULL_AUTO))
    auto["triggers"] = [{"id": "t1", "kind": "cron", "label": HOSTILE, "short": HOSTILE,
                         "enabled": True}]
    _run(_RouteClient(_auto_gets(auto)), "automation", "trigger", "off", auto["id"][:8], "1")
    _run(_RouteClient(_auto_gets(auto)), "automation", "trigger", "remove", auto["id"][:8], "1")
    _assert_escaped(capsys.readouterr().out)


def test_execution_show_escapes_the_trigger_message_and_record_text(capsys):
    full = _execution(
        triggerPayload={"kind": "discord", "sender": "poster\x1b[2K",
                        "channelName": "general", "guildName": None,
                        "at": "2026-10-30 08:00", "text": "hello\x1b[1A\x1b[2Kfake line\r"},
        steps=[{"name": HOSTILE, "status": "failed", "duration": "1s"}],
        error={"step": HOSTILE, "message": "boom\x1b[2K\r", "reason": "why\r"})
    _run(_ExecutionClient(full), "execution", "show", EXECUTION_ID[:8])
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\r" not in out
    assert "  hellofake line\\x0d" in out
    assert "trigger message: poster · #general" in out
    assert "step 1: Report\\x0d" in out
    assert "boom\\x0d" in out and "possible reason: why\\x0d" in out


def test_param_snapshot_and_secret_lists_escape_names(capsys):
    auto = dict(copy.deepcopy(FULL_AUTO))
    auto["params"] = [{"name": HOSTILE, "kind": "text", "value": "x"}]
    auto["snapshots"] = [{"id": "s1aaaaaa", "when": "2026-10-30", "reason": "manual",
                          "version": "v1", "size": "1 KB", "name": HOSTILE}]
    _run(_RouteClient(_auto_gets(auto)), "automation", "param", "list", auto["id"][:8])
    _run(_RouteClient(_auto_gets(auto)), "automation", "snapshot", "list", auto["id"][:8])
    secrets = [{"id": "s-1", "name": "API_TOKEN", "set": True,
                "usedBy": [{"id": "a1", "name": HOSTILE}]}]
    _run(_RouteClient({"/secrets": secrets}), "secret", "list")
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\r" not in out
    assert out.count("Report\\x0d") == 3


# ---------- AC. the vN grammar ----------

@pytest.mark.parametrize("label", ["3", "vv3", "v1_0", "v٣", "v", "v3 ", "v+3"])
def test_a_version_label_outside_the_grammar_exits(label):
    with pytest.raises(SystemExit) as ei:
        cli._version_arg(label)
    assert str(ei.value.code).startswith("version must be vN")


def test_a_version_label_parses():
    assert cli._version_arg("v3") == 3
    assert cli._version_arg("V12") == 12


# ---------- AD. agent names through the name key ----------

class _CoderClient(_WorkdirClient):
    def req(self, method, path, body=None, timeout=30):
        if method == "GET" and path == "/agents":
            return [{"id": "ag-coder", "name": "coder", "harness": "OpenCode", "model": None}]
        return super().req(method, path, body, timeout)


def test_grant_agent_with_padding_and_case_finds_the_agent(tmp_path):
    d = tmp_path / "wd"
    cli.write_workdir(d, FULL_AUTO)
    c = _CoderClient()
    _run(c, "automation", "create", str(d), "--grant-agent", "Coder ",
         "--grant-secret", "API_TOKEN")
    body = next(b for m, p, b in c.posted if m == "POST" and p == "/automations")
    assert body["stepAgents"] == ["ag-coder"]


def test_agent_check_compares_through_the_name_key():
    agents = [{"id": "ag-coder", "name": "coder", "harness": "OpenCode", "model": None}]
    c = _RouteClient({"/agents": agents}, reply={"ready": True})
    _run(c, "agent", "check", " CODER", "--json")
    assert c.calls == [("POST", "/agents/ag-coder/check", None)]


# ---------- R. a 5xx reply that is not JSON ----------

def test_a_plain_500_names_the_backend_answering():
    err = urllib.error.HTTPError("http://x", 500, "Internal Server Error", {},
                                 io.BytesIO(b"Internal Server Error"))
    with pytest.raises(SystemExit) as ei:
        cli._exit_http(err, "http://127.0.0.1:5151")
    message = str(ei.value.code)
    assert message == "the backend answered HTTP 500 Internal Server Error"
    assert "reachable" not in message


def test_a_plain_404_is_still_the_backend_not_being_reachable():
    err = urllib.error.HTTPError("http://x", 404, "Not Found", {}, io.BytesIO(b"<html>"))
    with pytest.raises(SystemExit) as ei:
        cli._exit_http(err)
    assert str(ei.value.code).startswith("backend isn't reachable")


# ---------- N. pull write order ----------

def _two_step_auto(version):
    auto = copy.deepcopy(FULL_AUTO)
    auto["version"] = version
    auto["steps"] = [
        {"file": "01-fetch.py", "name": "Fetch", "description": "", "code": f"print({version})\n"},
        {"file": "02-report.py", "name": "Report", "description": "",
         "code": f"print({version})\n"}]
    return auto


def _fail_on(monkeypatch, target):
    real_replace = os.replace

    def replace(source, destination):
        if os.path.basename(destination) == target:
            raise OSError(28, "No space left on device")
        return real_replace(source, destination)

    monkeypatch.setattr(cli.os, "replace", replace)


def test_an_interrupted_pull_keeps_the_previous_manifest(tmp_path, monkeypatch):
    d = tmp_path / "wd"
    cli.write_workdir(d, _two_step_auto(1))
    before = (d / "manifest.yaml").read_text()
    _fail_on(monkeypatch, "02-report.py")
    with pytest.raises(SystemExit):
        cli.write_workdir(d, _two_step_auto(2))
    assert (d / "manifest.yaml").read_text() == before
    assert yaml.safe_load(before)["pulled_from"] == 1
    assert sorted(p.name for p in d.iterdir()) == ["01-fetch.py", "02-report.py",
                                                   "manifest.yaml", "spec.md"]
    assert (d / "02-report.py").read_text() == "print(1)\n"


def test_an_interrupted_pull_into_a_fresh_directory_writes_no_manifest(tmp_path, monkeypatch):
    d = tmp_path / "wd"
    _fail_on(monkeypatch, "02-report.py")
    with pytest.raises(SystemExit):
        cli.write_workdir(d, _two_step_auto(1))
    assert sorted(p.name for p in d.iterdir()) == ["01-fetch.py"]


def test_the_manifest_is_written_last(tmp_path, monkeypatch):
    order = []
    real_replace = os.replace
    monkeypatch.setattr(cli.os, "replace",
                        lambda s, t: (order.append(os.path.basename(t)), real_replace(s, t)))
    auto = dict(_two_step_auto(1), notes="some notes")
    cli.write_workdir(tmp_path / "wd", auto)
    assert order == ["01-fetch.py", "02-report.py", "spec.md", "notes.md", "manifest.yaml"]


# ---------- AR. pull refuses another automation's workdir ----------

OTHER_ID = "def67890-0000-0000-0000-000000000000"


def _other():
    other = copy.deepcopy(FULL_AUTO)
    other["id"] = OTHER_ID
    other["name"] = "Report"
    other["version"] = 1
    return other


def test_pull_refuses_a_workdir_pulled_from_another_automation(tmp_path):
    d = tmp_path / "wd"
    cli.write_workdir(d, dict(copy.deepcopy(FULL_AUTO), name="Report?", version=1))
    before = (d / "manifest.yaml").read_text()
    other = _other()
    with pytest.raises(SystemExit) as ei:
        _run(_RouteClient(_auto_gets(other)), "automation", "pull", "Report", str(d))
    assert str(ei.value.code) == f"{d} holds the workdir of 'Report?' - pass --force to replace it"
    assert (d / "manifest.yaml").read_text() == before


def test_pull_refuses_managed_files_without_a_pulled_id(tmp_path):
    d = tmp_path / "wd"
    d.mkdir()
    (d / "01-old.py").write_text("print('mine')\n")
    (d / "manifest.yaml").write_text("name: Hand made\nsteps: []\n")
    with pytest.raises(SystemExit) as ei:
        _run(_RouteClient(_auto_gets(_other())), "automation", "pull", "Report", str(d))
    assert str(ei.value.code) == (f"{d} holds the workdir of 'another automation' - pass "
                                  "--force to replace it")
    assert (d / "01-old.py").exists()


def test_pull_force_replaces_another_automations_workdir(tmp_path, capsys):
    d = tmp_path / "wd"
    cli.write_workdir(d, dict(copy.deepcopy(FULL_AUTO), name="Report?", version=1))
    _run(_RouteClient(_auto_gets(_other())), "automation", "pull", "Report", str(d), "--force")
    assert yaml.safe_load((d / "manifest.yaml").read_text())["pulled_id"] == OTHER_ID


def test_pull_into_its_own_workdir_or_an_unmanaged_directory_proceeds(tmp_path, capsys):
    d = tmp_path / "wd"
    other = _other()
    cli.write_workdir(d, other)
    _run(_RouteClient(_auto_gets(other)), "automation", "pull", "Report", str(d))
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "README.md").write_text("mine\n")
    _run(_RouteClient(_auto_gets(other)), "automation", "pull", "Report", str(plain))
    assert (plain / "manifest.yaml").exists() and (plain / "README.md").exists()


def test_pull_usage_names_force():
    text = cli.build_parser()._subparsers._group_actions[0].choices["automation"] \
        ._subparsers._group_actions[0].choices["pull"].format_usage()
    assert "[--force]" in text


# ---------- AS. a BOM-prefixed workdir file ----------

def test_a_bom_prefixed_spec_md_pushes_with_its_title(tmp_path):
    d = tmp_path / "wd"
    cli.write_workdir(d, FULL_AUTO)
    spec = (d / "spec.md").read_text(encoding="utf-8")
    (d / "spec.md").write_bytes(b"\xef\xbb\xbf" + spec.encode("utf-8"))
    assert cli.read_workdir(d)["spec.md"] == spec
    c = _WorkdirClient()
    _run(c, "automation", "push", "Daily Report", str(d), "--force")
    body = next(b for m, p, b in c.posted if m == "POST" and p.endswith("/versions"))
    assert body["draft"]["spec"][0] == {"kind": "h1", "text": "Daily Report"}


# ---------- AT. a secret name is matched whole ----------

def test_a_secret_name_with_a_trailing_newline_is_refused():
    with pytest.raises(SystemExit) as ei:
        _run(_RouteClient(), "secret", "set", "API_KEY\n", "--stdin")
    assert "secret names must match" in str(ei.value.code)
