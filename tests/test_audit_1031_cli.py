"""CLI audit regressions (2026-10-31 sweep).

§20: a workdir pushed into another automation still has its identity fields
gated by `pulled_name` / `pulled_description`; the automation names,
descriptions, step and param names and trigger labels that `automation list`,
`automation show` and the execution candidate list print are console-safe.
"""
import copy

import pytest
import yaml
from test_cli import FULL_AUTO, _auto_gets, _run, _RouteClient, _WorkdirClient

from autowright import cli

OTHER_ID = "def67890-0000-0000-0000-000000000000"
HOSTILE = "\x1b[31mX\r"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    monkeypatch.setenv("no_proxy", "*")


class _MintingClient(_WorkdirClient):
    """A save mints the next version after the automation's current one."""

    def req(self, method, path, body=None, timeout=30):
        r = super().req(method, path, body, timeout)
        if method == "POST" and path.endswith("/versions"):
            r = {**r, "version": self.auto["version"] + 1}
        return r


def _versioned(auto, n):
    out = copy.deepcopy(auto)
    out["version"] = n
    out["versions"] = []
    return out


def _other():
    other = _versioned(FULL_AUTO, 5)
    other["id"] = OTHER_ID
    other["name"] = "Other Report"
    other["description"] = "Other description"
    return other


def _version_body(c):
    return next(b for m, p, b in c.posted if m == "POST" and p.endswith("/versions"))


# ---------- C1. a foreign push keeps the identity gate ----------

def test_a_foreign_push_with_the_pulled_name_and_description_sends_neither(tmp_path):
    d = tmp_path / "wd"
    cli.write_workdir(d, _versioned(FULL_AUTO, 1))
    before = yaml.safe_load((d / "manifest.yaml").read_text())
    c = _MintingClient(_other())
    _run(c, "automation", "push", "Other Report", str(d))
    assert "name" not in _version_body(c)
    assert not [p for m, p, _ in c.posted if m == "PATCH"]
    after = yaml.safe_load((d / "manifest.yaml").read_text())
    # pulled_from is not rewritten to the target's minted version
    assert after["pulled_from"] == before["pulled_from"] == 1


def test_a_foreign_push_with_a_changed_name_sends_it(tmp_path):
    d = tmp_path / "wd"
    cli.write_workdir(d, _versioned(FULL_AUTO, 1))
    manifest = yaml.safe_load((d / "manifest.yaml").read_text())
    manifest["name"] = "Renamed Report"
    (d / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    c = _MintingClient(_other())
    _run(c, "automation", "push", "Other Report", str(d))
    assert _version_body(c)["name"] == "Renamed Report"
    after = yaml.safe_load((d / "manifest.yaml").read_text())
    assert after["pulled_from"] == 1


# ---------- C2. automation text on the console ----------

def test_automation_list_prints_a_hostile_name_escaped(capsys):
    autos = [{"id": "abc12345-x", "name": HOSTILE, "triggerChip": "Daily 8:00",
              "lastStatus": "succeeded", "resultChip": ""}]
    _run(_RouteClient({"/automations": autos}), "automation", "list")
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\r" not in out
    assert "X\\x0d" in out


def test_automation_show_prints_hostile_text_escaped(capsys):
    full = dict(copy.deepcopy(FULL_AUTO), name=HOSTILE, description=HOSTILE,
                specMeta="v2", lastStatus="succeeded",
                triggers=[{"kind": "cron", "label": HOSTILE, "enabled": True}],
                params=[{"name": HOSTILE, "kind": "text", "default": ""}])
    full["steps"] = [{"file": "01-fetch.py", "name": HOSTILE, "description": "",
                      "code": "print(1)\n"}]
    _run(_RouteClient(_auto_gets(full)), "automation", "show", "abc12345")
    out = capsys.readouterr().out
    assert "\x1b" not in out and "\r" not in out
    lines = out.splitlines()
    assert lines[0].startswith("X\\x0d [")
    assert lines[1] == "X\\x0d"
    assert "trigger 1: X\\x0d" in out
    assert "param X\\x0d (text)" in out
    assert "step 1: X\\x0d" in out


class _ExecutionsClient:
    def req(self, method, path, body=None, timeout=30):
        rows = [{"id": "e1aaaaaa-0000", "automationName": HOSTILE,
                 "status": "succeeded", "started": "2026-10-31T08:00:00"},
                {"id": "e1bbbbbb-0000", "automationName": "Fine",
                 "status": "failed", "started": "2026-10-31T09:00:00"}]
        return {"executions": rows, "total": len(rows)}


def test_the_execution_candidate_list_is_console_safe():
    with pytest.raises(SystemExit) as ei:
        cli.find_execution(_ExecutionsClient(), "e1")
    message = str(ei.value.code)
    assert "\x1b" not in message and "\r" not in message
    assert "X\\x0d" in message
