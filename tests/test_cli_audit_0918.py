"""§20 CLI-audit regressions (2026-09-18): the execution-reference readout,
the long calls' timeout headroom, `automation push`'s save line around the
description patch, and the shell-owned settings note."""
import json

import pytest
from test_cli import EXECS, FULL_AUTO, _WorkdirClient, _run


# ---------- §20 reference rule: a miss names real candidates ----------

class _MissClient:
    """A server whose §19 idPrefix filter matches nothing — the reference the
    user typed exists nowhere, so only the newest-rows read answers."""

    def __init__(self, execs, total=None):
        self.execs = execs
        self.total = len(execs) if total is None else total
        self.paths = []

    def req(self, method, path, body=None, timeout=30):
        assert method == "GET"
        self.paths.append(path)
        if "idPrefix=" in path:
            return {"executions": [], "total": 0}
        limit = int(path.rsplit("limit=", 1)[1])
        return {"executions": self.execs[:limit], "total": self.total}


def test_find_execution_miss_names_the_newest_executions(capsys):
    """§20: "Ambiguity or no match exits with the candidate list" — a prefix
    nothing starts with used to print "have: (none)" even with a full history,
    so the miss named nothing the user could pass back."""
    from autowright.cli import find_execution

    c = _MissClient(EXECS)
    with pytest.raises(SystemExit) as ei:
        find_execution(c, "zz")
    msg = str(ei.value.code)
    assert c.paths == ["/executions?idPrefix=zz&limit=50", "/executions?limit=20"]
    assert "no unique execution matches 'zz'" in msg
    assert "e1111111 (Daily Report, succeeded, 2026-07-29 08:00)" in msg
    assert "f3333333 (Backup, executing, 2026-07-27 10:00)" in msg
    assert "(none)" not in msg


def test_find_execution_miss_counts_from_the_envelope_total():
    """§20: the "… and N more" count comes from the §19 envelope's `total`,
    never from the capped page — a `limit` the server honored would otherwise
    report the page size as the whole history."""
    from autowright.cli import find_execution

    rows = [{"id": f"e{i:07d}-x", "automationName": "Daily Report",
             "status": "succeeded", "started": "2026-07-29 08:00"} for i in range(50)]
    c = _MissClient(rows, total=1234)
    with pytest.raises(SystemExit) as ei:
        find_execution(c, "zz")
    msg = str(ei.value.code)
    assert msg.count("(Daily Report, succeeded, 2026-07-29 08:00)") == 20
    assert msg.endswith("… and 1214 more")


def test_find_execution_with_no_executions_at_all_still_says_none():
    """An empty history has nothing to name — the readout stays "(none)"."""
    from autowright.cli import find_execution

    with pytest.raises(SystemExit) as ei:
        find_execution(_MissClient([]), "zz")
    assert "(none)" in str(ei.value.code)


def test_find_execution_prefix_hit_counts_from_the_envelope_total():
    """A prefix that matches more than the page shows 20 rows and counts the
    rest off `total`, not off the 50 rows the server returned."""
    from autowright.cli import find_execution

    rows = [{"id": f"e{i:07d}-x", "automationName": "Daily Report",
             "status": "succeeded", "started": "2026-07-29 08:00"} for i in range(50)]

    class _PrefixClient(_MissClient):
        def req(self, method, path, body=None, timeout=30):
            self.paths.append(path)
            return {"executions": self.execs, "total": self.total}

    with pytest.raises(SystemExit) as ei:
        find_execution(_PrefixClient(rows, total=77), "e")
    msg = str(ei.value.code)
    assert msg.endswith("… and 57 more")


# ---------- §20 HTTP timeouts: 660 s of headroom over the backend ----------

class _TimeoutClient:
    """Records the timeout every call rode with."""

    def __init__(self, reply=None):
        self.reply = reply or {}
        self.timeouts = []

    def req(self, method, path, body=None, timeout=30):
        self.timeouts.append((path, timeout))
        if path == "/automations/import/url":
            return {"token": "tok", "preview": {"resolvedUrl": "https://x.example/a.adx"}}
        return {**self.reply, "name": "Imported", "id": "new-id"}

    def req_raw(self, method, path, data=None, timeout=30):
        self.timeouts.append((path, timeout))
        return json.dumps({"name": "Imported", "id": "new-id"}).encode()


def test_import_from_a_url_gives_the_backend_its_deadline_first(capsys):
    """§20: "which get 660 s: 60 s of headroom over the backend's own 600 s
    download/install deadlines" — the backend's plain-word timeout has to be
    what the user reads, never this socket giving up first."""
    c = _TimeoutClient()
    _run(c, "automation", "import", "https://x.example/a.adx")
    assert c.timeouts == [("/automations/import/url", 660),
                          ("/automations/import/confirm", 660)]


def test_every_long_call_rides_the_same_headroom():
    """§20: the same 660 s on the package install, the automation delete, and
    the §22.5 marketplace calls that go over the network."""
    import inspect

    from autowright import cli

    source = inspect.getsource(cli)
    assert "timeout=600" not in source
    assert source.count("timeout=660") >= 12


# ---------- §20 push: the save line stands even when the patch fails ----------

class _PatchFailsClient(_WorkdirClient):
    """A server that saves the version and then refuses the §19 description
    patch, the way `_exit_http` surfaces any 4xx."""

    def req(self, method, path, body=None, timeout=30):
        if method == "PATCH":
            raise SystemExit("422: description must be a string")
        return super().req(method, path, body, timeout)


def test_push_reports_the_saved_version_before_a_failing_description_patch(tmp_path, capsys):
    """§20: the version has landed either way — a failing description patch
    must not swallow the `saved '<name>' as vN` line, or the user re-pushes a
    version that is already stored."""
    from autowright import cli

    d = tmp_path / "wd"
    cli.write_workdir(d, FULL_AUTO)
    manifest = (d / "manifest.yaml").read_text().replace(
        "description: Reports daily", "description: Reports every single day")
    (d / "manifest.yaml").write_text(manifest)

    with pytest.raises(SystemExit) as ei:
        _run(_PatchFailsClient(), "automation", "push", "Daily Report", str(d))
    assert "422" in str(ei.value.code)
    assert "saved 'Daily Report' as v2" in capsys.readouterr().out


def test_push_patches_the_description_after_reporting_the_save(tmp_path, capsys):
    """The ordinary path is unchanged: the version, then the description."""
    from autowright import cli

    d = tmp_path / "wd"
    cli.write_workdir(d, FULL_AUTO)
    (d / "manifest.yaml").write_text((d / "manifest.yaml").read_text().replace(
        "description: Reports daily", "description: Reports every single day"))

    c = _WorkdirClient()
    _run(c, "automation", "push", "Daily Report", str(d))
    methods = [(m, p) for m, p, _ in c.posted]
    assert methods[0][0] == "POST" and methods[0][1].endswith("/versions")
    assert ("PATCH", f"/automations/{FULL_AUTO['id']}") in methods
    assert "saved 'Daily Report' as v2" in capsys.readouterr().out


# ---------- §20 settings set: the shell-owned keys say when they land ----------

class _SettingsClient:
    def __init__(self):
        self.calls = []

    def req(self, method, path, body=None, timeout=30):
        self.calls.append((method, path, body))
        return {}


def test_settings_set_names_when_a_shell_owned_key_takes_effect(capsys):
    """§20: "after setting one of the shell-owned keys `login` or
    `menuBarIcon`, prints one extra line" — the OS side of those two is the
    Electron main process's §3 poll, not the backend's."""
    for key in ("login", "menuBarIcon"):
        c = _SettingsClient()
        _run(c, "settings", "set", f"{key}=on")
        out = capsys.readouterr().out
        assert out == f"set {key}\ntakes effect when the app next syncs, within a minute\n"
        assert c.calls == [("PATCH", "/settings", {key: True})]


def test_settings_set_stays_quiet_for_backend_owned_keys(capsys):
    """Every other key applies the moment the PATCH answers."""
    c = _SettingsClient()
    _run(c, "settings", "set", "notifications=all", "days=30")
    out = capsys.readouterr().out
    assert out == "set notifications, days\n"


def test_settings_set_says_it_once_for_a_mixed_batch(capsys):
    """One line per command, not one per shell-owned key."""
    c = _SettingsClient()
    _run(c, "settings", "set", "login=on", "menuBarIcon=off", "days=7")
    out = capsys.readouterr().out
    assert out.count("takes effect when the app next syncs, within a minute") == 1
    assert out.startswith("set login, menuBarIcon, days\n")
