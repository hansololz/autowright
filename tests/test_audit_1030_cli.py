"""CLI audit regressions (2026-10-30).

§20: a push that minted vM moves the workdir's `pulled_from` to M, so the
edit-push-edit-push loop is never refused as stale; push applies only the
manifest-side name/description/schedule changes against the pulled baseline;
`validate` sends the workdir's `pulled_id` as the automationId and words an
imported reference the server's way; untrusted catalog and archive text is
made console-safe; a non-JSON reply (an error body, or the import's) is
"backend isn't reachable"; a catalog read that can't get a slot answers
"the backend is busy"; pull refuses two steps sharing one file name.
"""
import copy
import io
import json
import urllib.error

import pytest
import yaml
from test_cli import API_TOKEN_ID, FULL_AUTO, _pushed_steps, _run, _WorkdirClient
from test_marketplace import market, write_catalog  # noqa: F401 - fixture

from autowright import cli, marketplace

SHELF_ID = "88888888-8888-4888-8888-888888888888"
UNRESOLVED_ID = "99999999-9999-4999-8999-999999999999"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    monkeypatch.setenv("no_proxy", "*")


def _versioned(auto, n):
    out = copy.deepcopy(auto)
    out["version"] = n
    out["versions"] = []
    return out


def _edit_step(d, text):
    (d / "01-fetch.py").write_text(text)


class _MintingClient(_WorkdirClient):
    """A save mints the next version after the automation's current one."""

    def __init__(self, auto, mint=True):
        super().__init__(auto)
        self.mint = mint

    def req(self, method, path, body=None, timeout=30):
        r = super().req(method, path, body, timeout)
        if method == "POST" and path.endswith("/versions"):
            r = {**r, "version": self.auto["version"] + (1 if self.mint else 0)}
        return r


# ---------- C1. push moves pulled_from to the version it minted ----------

def test_a_second_push_from_the_same_workdir_is_not_refused(tmp_path):
    d = tmp_path / "wd"
    cli.write_workdir(d, _versioned(FULL_AUTO, 1))
    before = (d / "manifest.yaml").read_text()
    _edit_step(d, "print('first edit')\n")
    _run(_MintingClient(_versioned(FULL_AUTO, 1)), "automation", "push", "Daily Report", str(d))
    after = (d / "manifest.yaml").read_text()
    assert yaml.safe_load(after)["pulled_from"] == 2
    # only the pulled_from line changed
    assert after == before.replace("pulled_from: 1", "pulled_from: 2")

    _edit_step(d, "print('second edit')\n")
    c = _MintingClient(_versioned(FULL_AUTO, 2))
    _run(c, "automation", "push", "Daily Report", str(d))
    assert _pushed_steps(c)[0]["code"] == "print('second edit')\n"
    assert yaml.safe_load((d / "manifest.yaml").read_text())["pulled_from"] == 3


def test_a_push_that_minted_nothing_leaves_the_manifest(tmp_path):
    d = tmp_path / "wd"
    cli.write_workdir(d, _versioned(FULL_AUTO, 1))
    before = (d / "manifest.yaml").read_text()
    _run(_MintingClient(_versioned(FULL_AUTO, 1), mint=False),
         "automation", "push", "Daily Report", str(d))
    assert (d / "manifest.yaml").read_text() == before


# ---------- C3. push applies only manifest-side changes ----------

def test_pull_records_the_baseline(tmp_path):
    d = tmp_path / "wd"
    cli.write_workdir(d, _versioned(FULL_AUTO, 1))
    manifest = yaml.safe_load((d / "manifest.yaml").read_text())
    assert manifest["pulled_id"] == FULL_AUTO["id"]
    assert manifest["pulled_name"] == "Daily Report"
    assert manifest["pulled_description"] == "Reports daily"
    assert manifest["pulled_triggers"] == [{"cron": "0 8 * * *", "timezone": "Asia/Tokyo"}]


def test_an_in_app_rename_and_a_deleted_cron_survive_a_push(tmp_path):
    d = tmp_path / "wd"
    cli.write_workdir(d, _versioned(FULL_AUTO, 1))
    # since the pull: renamed, description edited, the cron deleted - in the app
    current = _versioned(FULL_AUTO, 1)
    current["name"] = "Daily Report 2"
    current["description"] = "Edited in the app"
    current["triggers"] = [t for t in current["triggers"] if t["kind"] != "cron"]
    c = _MintingClient(current)
    _run(c, "automation", "push", "Daily Report 2", str(d))
    _, _, body = next(p for p in c.posted if p[1].endswith("/versions"))
    assert "name" not in body
    assert not any(t["kind"] == "cron" for t in body["draft"]["triggers"])
    assert not any(m == "PATCH" for m, _, _ in c.posted)


def test_a_manifest_side_rename_still_applies(tmp_path):
    d = tmp_path / "wd"
    cli.write_workdir(d, _versioned(FULL_AUTO, 1))
    manifest = yaml.safe_load((d / "manifest.yaml").read_text())
    manifest["name"] = "Weekly Report"
    manifest["description"] = "Reports weekly"
    (d / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    c = _MintingClient(_versioned(FULL_AUTO, 1))
    _run(c, "automation", "push", "Daily Report", str(d))
    _, _, body = next(p for p in c.posted if p[1].endswith("/versions"))
    assert body["name"] == "Weekly Report"
    assert ("PATCH", f"/automations/{FULL_AUTO['id']}",
            {"description": "Reports weekly"}) in c.posted


def test_a_pushed_manifest_rename_becomes_the_baseline(tmp_path):
    """§20: a push that applied a manifest-side rename rewrites `pulled_name`,
    so an in-app rename after that push survives the next push."""
    d = tmp_path / "wd"
    cli.write_workdir(d, _versioned(FULL_AUTO, 1))
    before = (d / "manifest.yaml").read_text()
    renamed = before.replace("name: Daily Report", "name: Weekly Report", 1)
    (d / "manifest.yaml").write_text(renamed)
    c = _MintingClient(_versioned(FULL_AUTO, 1))
    _run(c, "automation", "push", "Daily Report", str(d))
    _, _, body = next(p for p in c.posted if p[1].endswith("/versions"))
    assert body["name"] == "Weekly Report"
    after = (d / "manifest.yaml").read_text()
    # line-replaced in place: the rest of the file is byte for byte
    assert after == renamed.replace("pulled_name: Daily Report",
                                    "pulled_name: Weekly Report").replace(
        "pulled_from: 1", "pulled_from: 2")

    # renamed again in the app since that push
    current = _versioned(FULL_AUTO, 2)
    current["name"] = "Weekly Report 2"
    _edit_step(d, "print('edited again')\n")
    c = _MintingClient(current)
    _run(c, "automation", "push", "Weekly Report 2", str(d))
    _, _, body = next(p for p in c.posted if p[1].endswith("/versions"))
    assert "name" not in body


def test_a_pushed_description_and_schedule_become_the_baseline(tmp_path):
    d = tmp_path / "wd"
    cli.write_workdir(d, _versioned(FULL_AUTO, 1))
    manifest = yaml.safe_load((d / "manifest.yaml").read_text())
    manifest["description"] = "Reports weekly"
    manifest["triggers"].append({"every": "PT2H"})
    (d / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    c = _MintingClient(_versioned(FULL_AUTO, 1))
    _run(c, "automation", "push", "Daily Report", str(d))
    assert ("PATCH", f"/automations/{FULL_AUTO['id']}",
            {"description": "Reports weekly"}) in c.posted
    after = yaml.safe_load((d / "manifest.yaml").read_text())
    assert after["pulled_description"] == "Reports weekly"
    assert after["pulled_triggers"] == [{"cron": "0 8 * * *", "timezone": "Asia/Tokyo"},
                                        {"every": "PT2H"}]
    assert after["pulled_from"] == 2


def test_a_refused_description_patch_leaves_its_baseline(tmp_path):
    class _PatchFails(_MintingClient):
        def req(self, method, path, body=None, timeout=30):
            if method == "PATCH":
                raise SystemExit("422: description must be a string")
            return super().req(method, path, body, timeout)

    d = tmp_path / "wd"
    cli.write_workdir(d, _versioned(FULL_AUTO, 1))
    (d / "manifest.yaml").write_text((d / "manifest.yaml").read_text().replace(
        "description: Reports daily", "description: Reports weekly", 1))
    with pytest.raises(SystemExit):
        _run(_PatchFails(_versioned(FULL_AUTO, 1)), "automation", "push", "Daily Report", str(d))
    after = yaml.safe_load((d / "manifest.yaml").read_text())
    assert after["pulled_description"] == "Reports daily"
    assert after["pulled_from"] == 2


# ---------- C8. validate sends the automationId and the unresolved map ----------

class _ValidateClient(_WorkdirClient):
    def req(self, method, path, body=None, timeout=30):
        if method == "POST" and path == "/validate-steps":
            self.posted.append((method, path, body))
            return {"errors": []}
        return super().req(method, path, body, timeout)


def test_validate_sends_the_pulled_automation_id(tmp_path, capsys):
    d = tmp_path / "wd"
    cli.write_workdir(d, _versioned(FULL_AUTO, 1))
    c = _ValidateClient(_versioned(FULL_AUTO, 1))
    _run(c, "automation", "validate", str(d))
    assert capsys.readouterr().out.strip() == "ok"
    assert c.posted[0][2]["automationId"] == FULL_AUTO["id"]
    assert "pulled_" not in json.dumps(c.posted[0][2]["draft"])


def test_validate_words_an_imported_reference_like_the_server(tmp_path, capsys):
    auto = _versioned(FULL_AUTO, 1)
    auto["steps"][0]["secrets"] = [{"id": UNRESOLVED_ID, "why": "posts the report"}]
    auto["allowedSecrets"] = [API_TOKEN_ID, UNRESOLVED_ID]
    auto["unresolvedReferences"] = {
        UNRESOLVED_ID: {"kind": "secret", "name": "SLACK_TOKEN", "description": ""}}
    d = tmp_path / "wd"
    cli.write_workdir(d, auto)
    with pytest.raises(SystemExit) as ei:
        _run(_ValidateClient(auto), "automation", "validate", str(d))
    assert ei.value.code == 1
    err = capsys.readouterr().err
    assert "this step still uses SLACK_TOKEN" in err
    assert UNRESOLVED_ID not in err


# ---------- C5. untrusted text on the console ----------

def test_console_safe_escapes_controls_and_strips_ansi():
    assert cli._console_safe("a\tb\nc") == "a\tb\nc"
    assert cli._console_safe('os.system("id")\rprint("ok")') == 'os.system("id")\\x0dprint("ok")'
    assert cli._console_safe("\x1b[2K\x1b[1Ahidden") == "hidden"
    assert cli._console_safe("x\x1bcy") == "xy"
    assert cli._console_safe("bell\x07") == "bell\\x07"
    assert cli._console_safe("del\x7f") == "del\\x7f"
    assert cli._console_safe("csi\x9b2K c1\x85") == "csi\\x9b2K c1\\x85"
    assert cli._console_safe("caf\u00e9 \u00a0ok") == "caf\u00e9 \u00a0ok"


class _AuditClient:
    def req(self, method, path, body=None, timeout=30):
        if path == "/marketplace":
            return {"sources": [{
                "id": SHELF_ID, "name": "Shelf\x1b[31m", "location": None,
                "entries": [{"index": 0, "title": "Evil\rGood", "description": ""}]}]}
        return {"files": [{"path": "steps/01-a.py",
                           "text": 'import os; os.system("id")\rprint("ok")   \n'
                                   "\x1b[1A\x1b[2Kprint('cover')\n"}]}


def test_marketplace_audit_and_list_print_console_safe_text(capsys):
    _run(_AuditClient(), "marketplace", "audit", "Shelf", "1")
    out = capsys.readouterr().out
    assert "\r" not in out and "\x1b" not in out
    assert 'os.system("id")\\x0dprint("ok")' in out
    assert "print('cover')" in out
    _run(_AuditClient(), "marketplace", "list")
    out = capsys.readouterr().out
    assert "\r" not in out and "\x1b" not in out
    assert "Shelf [" in out and "1. Evil\\x0dGood" in out


# ---------- C6/C7. a reply that isn't JSON ----------

def _real_client(home):
    from autowright import paths

    paths.backend_json().write_text(json.dumps({"port": 5151, "token": "tok"}))
    return cli.Client()


def test_an_html_error_body_is_backend_not_reachable(home, monkeypatch):
    c = _real_client(home)

    def opener(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {},
                                     io.BytesIO(b"<!DOCTYPE html><html>nope</html>"))

    monkeypatch.setattr(cli._opener, "open", opener)
    with pytest.raises(SystemExit) as ei:
        c.req("GET", "/automations")
    message = str(ei.value.code)
    assert message.startswith("backend isn't reachable at http://127.0.0.1:5151")
    assert "DOCTYPE" not in message
    with pytest.raises(SystemExit) as ei:
        c.req_raw("GET", "/automations/x/export")
    assert str(ei.value.code).startswith("backend isn't reachable")


def test_import_parses_its_reply_under_the_json_guard(home, monkeypatch, tmp_path):
    c = _real_client(home)

    class _Html:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"<html>someone else's server</html>"

    monkeypatch.setattr(cli._opener, "open", lambda request, timeout: _Html())
    archive = tmp_path / "a.autowright"
    archive.write_bytes(b"PK\x03\x04")
    with pytest.raises(SystemExit) as ei:
        _run(c, "automation", "import", str(archive))
    assert str(ei.value.code).startswith("backend isn't reachable at http://127.0.0.1:5151")


# ---------- C10. a catalog read that can't get a slot ----------

def test_a_catalog_read_without_a_slot_answers_busy(market, tmp_path, monkeypatch):  # noqa: F811
    f = write_catalog(tmp_path, [{"title": "One", "description": "",
                                  "path": str(tmp_path / "one.autowright")}], name="Shelf")
    source = market.add(path=str(f))
    monkeypatch.setattr(marketplace, "CATALOG_SLOT_WAIT_S", 0.05)
    taken = 0
    try:
        while marketplace._read_slots.acquire(blocking=False):
            taken += 1
        with pytest.raises(marketplace.MarketplaceBusy) as e:
            market.read_catalog(source["id"])
        assert str(e.value) == "the backend is busy — try again"
    finally:
        for _ in range(taken):
            marketplace._read_slots.release()
    assert market.read_catalog(source["id"])["name"] == "Shelf"


def test_the_cli_prints_the_busy_message(home, monkeypatch):
    c = _real_client(home)

    class _Json:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(self.data).encode()

    def opener(request, timeout):
        if request.full_url.endswith("/marketplace"):
            return _Json({"sources": [{"id": SHELF_ID, "name": "Shelf",
                                       "location": "/m/marketplace.yaml", "entries": []}]})
        raise urllib.error.HTTPError(
            request.full_url, 503, "Service Unavailable", {},
            io.BytesIO(json.dumps({"detail": marketplace.CATALOG_BUSY}).encode()))

    monkeypatch.setattr(cli._opener, "open", opener)
    with pytest.raises(SystemExit) as ei:
        _run(c, "marketplace", "catalog", "set", "Shelf", "name=New")
    message = str(ei.value.code)
    assert message == "503: the backend is busy — try again"
    assert "restart" not in message


# ---------- C12. pull refuses two steps sharing one file ----------

def test_pull_refuses_two_steps_sharing_a_file(tmp_path):
    auto = _versioned(FULL_AUTO, 1)
    auto["steps"].append({"file": "01-fetch.py", "name": "Report",
                          "description": "print the report", "code": "print('report')\n"})
    d = tmp_path / "wd"
    with pytest.raises(SystemExit) as ei:
        cli.write_workdir(d, auto)
    assert ei.value.code == ("can't pull: steps 'Fetch' and 'Report' share the file "
                             "'01-fetch.py'")
    assert not d.exists()
