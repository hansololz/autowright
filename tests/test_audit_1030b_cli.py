"""CLI and transfer audit regressions (2026-10-30 second sweep).

§20: a schedule deleted in the app stays deleted across repeated pushes;
`push --lock/--unlock` edits reach the workdir manifest; the `pulled_*`
baseline and the stale check apply only to the automation `pulled_id` names;
`validate` ignores a `pulled_id` this backend doesn't know; untrusted text in
lookup lists, HTTP details and the resolved import URL is console-safe.
§5.1: export writes each param's effective default. §5.2: the GitHub API
read refuses a redirect off https, and a listed asset must be https.
"""
import copy
import io
import json
import urllib.error
import zipfile

import pytest
import yaml
from conftest import make_version
from test_cli import FULL_AUTO, _pushed_steps, _run, _WorkdirClient

from autowright import cli, transfer

OTHER_ID = "def67890-0000-0000-0000-000000000000"
FOREIGN_ID = "aaaaaaaa-0000-0000-0000-000000000000"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    monkeypatch.setenv("no_proxy", "*")


def _versioned(auto, n):
    out = copy.deepcopy(auto)
    out["version"] = n
    out["versions"] = []
    return out


class _MintingClient(_WorkdirClient):
    """A save mints the next version after the automation's current one."""

    def req(self, method, path, body=None, timeout=30):
        r = super().req(method, path, body, timeout)
        if method == "POST" and path.endswith("/versions"):
            r = {**r, "version": self.auto["version"] + 1}
        return r


def _version_body(c):
    return next(b for m, p, b in c.posted if m == "POST" and p.endswith("/versions"))


def _schedules(body):
    return [t for t in body["draft"]["triggers"] if t["kind"] in ("cron", "interval")]


# ---------- C1. a schedule deleted in the app stays deleted ----------

def test_a_schedule_deleted_in_the_app_is_not_re_added_on_the_second_push(tmp_path):
    d = tmp_path / "wd"
    cli.write_workdir(d, _versioned(FULL_AUTO, 1))
    current = _versioned(FULL_AUTO, 1)
    current["triggers"] = [t for t in current["triggers"] if t["kind"] != "cron"]

    c = _MintingClient(current)
    _run(c, "automation", "push", "Daily Report", str(d))
    assert _schedules(_version_body(c)) == []
    manifest = yaml.safe_load((d / "manifest.yaml").read_text())
    # the suppressed line stays in the baseline, so it stays suppressed
    assert manifest["pulled_triggers"] == [{"cron": "0 8 * * *", "timezone": "Asia/Tokyo"}]

    (d / "01-fetch.py").write_text("print('second edit')\n")
    second = copy.deepcopy(current)
    second["version"] = 2
    c = _MintingClient(second)
    _run(c, "automation", "push", "Daily Report", str(d))
    assert _schedules(_version_body(c)) == []


# ---------- C2. push --lock/--unlock reach the workdir ----------

def test_a_lock_flag_survives_the_next_plain_push(tmp_path):
    d = tmp_path / "wd"
    cli.write_workdir(d, _versioned(FULL_AUTO, 1))
    c = _MintingClient(_versioned(FULL_AUTO, 1))
    _run(c, "automation", "push", "Daily Report", str(d), "--lock", "Fetch")
    assert _pushed_steps(c)[0].get("locked") is True
    manifest = yaml.safe_load((d / "manifest.yaml").read_text())
    assert manifest["steps"][0]["locked"] is True
    assert manifest["pulled_from"] == 2

    after = _versioned(FULL_AUTO, 2)
    after["steps"][0]["locked"] = True
    c = _MintingClient(after)
    _run(c, "automation", "push", "Daily Report", str(d))
    assert _pushed_steps(c)[0].get("locked") is True


def test_an_unlock_flag_survives_the_next_plain_push(tmp_path):
    auto = _versioned(FULL_AUTO, 1)
    auto["steps"][0]["locked"] = True
    d = tmp_path / "wd"
    cli.write_workdir(d, auto)
    assert yaml.safe_load((d / "manifest.yaml").read_text())["steps"][0]["locked"] is True
    _run(_MintingClient(auto), "automation", "push", "Daily Report", str(d),
         "--unlock", "Fetch")
    assert "locked" not in yaml.safe_load((d / "manifest.yaml").read_text())["steps"][0]

    c = _MintingClient(_versioned(FULL_AUTO, 2))
    _run(c, "automation", "push", "Daily Report", str(d))
    assert "locked" not in _pushed_steps(c)[0]


# ---------- C3. the baseline belongs to the pulled automation ----------

def test_a_workdir_pushed_into_another_automation_ignores_the_baseline(tmp_path):
    d = tmp_path / "wd"
    cli.write_workdir(d, _versioned(FULL_AUTO, 1))
    before = (d / "manifest.yaml").read_text()
    other = _versioned(FULL_AUTO, 5)
    other["id"] = OTHER_ID
    other["name"] = "Other Report"
    other["triggers"] = [t for t in other["triggers"] if t["kind"] != "cron"]
    c = _MintingClient(other)
    # v5 is above the workdir's pulled_from 1, but that is the other
    # automation's version: not refused as stale
    _run(c, "automation", "push", "Other Report", str(d))
    body = _version_body(c)
    assert [(t["kind"], t["expression"]) for t in _schedules(body)] == [("cron", "0 8 * * *")]
    assert body["name"] == "Daily Report"
    # no pulled_* rewrite for a workdir pulled from another automation
    assert (d / "manifest.yaml").read_text() == before


# ---------- C4. validate ignores an unknown pulled_id ----------

class _ValidateClient(_WorkdirClient):
    def req(self, method, path, body=None, timeout=30):
        if method == "POST" and path == "/validate-steps":
            self.posted.append((method, path, body))
            return {"errors": []}
        if method == "GET" and path.startswith("/automations/"):
            if path != f"/automations/{self.auto['id']}":
                raise SystemExit("404: automation not found")
        return super().req(method, path, body, timeout)


def test_validate_ignores_a_pulled_id_this_backend_does_not_know(tmp_path, capsys):
    foreign = _versioned(FULL_AUTO, 1)
    foreign["id"] = FOREIGN_ID
    d = tmp_path / "wd"
    cli.write_workdir(d, foreign)
    c = _ValidateClient(_versioned(FULL_AUTO, 1))
    _run(c, "automation", "validate", str(d))
    assert capsys.readouterr().out.strip() == "ok"
    assert "automationId" not in c.posted[0][2]
    assert f"/automations/{FOREIGN_ID}" not in c.fetched


# ---------- C5. export writes the effective param default ----------

def test_export_writes_a_mismatched_default_as_the_kind_default(store):
    params = [{"name": "greeting", "kind": "text", "label": "Greeting", "help": "",
               "default": 30},
              {"name": "count", "kind": "number", "label": "Count", "help": "", "min": 1,
               "default": 3}]
    a = store.create_automation(make_version(params=params), "Mismatched", "mock")
    data = transfer.export_automation(store, a)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        meta = yaml.safe_load(z.read("automation/automation.yaml"))
    assert [(p["name"], p["default"]) for p in meta["params"]] == [("greeting", ""),
                                                                    ("count", 3)]


# ---------- C6. untrusted text on the console ----------

def test_lookup_lists_are_console_safe():
    items = [{"id": "11111111-aaaa", "name": "Shelf\x1b[31m red"},
             {"id": "22222222-bbbb", "name": "Evil\rGood"}]
    with pytest.raises(SystemExit) as ei:
        cli._resolve(items, "zzz", "marketplace")
    message = str(ei.value.code)
    assert "\x1b" not in message and "\r" not in message
    assert "Evil\\x0dGood" in message
    with pytest.raises(SystemExit) as ei:
        cli._resolve(items, "e", "marketplace")
    message = str(ei.value.code)
    assert "\x1b" not in message and "\r" not in message


def test_an_http_detail_is_console_safe():
    detail = json.dumps({"detail": "step Evil\rGood\x1b[2K: agent '1' needs a why"})
    e = urllib.error.HTTPError("http://x", 422, "Unprocessable", {},
                               io.BytesIO(detail.encode()))
    with pytest.raises(SystemExit) as ei:
        cli._exit_http(e)
    message = str(ei.value.code)
    assert message == "422: step Evil\\x0dGood: agent '1' needs a why"


class _ImportClient:
    def req(self, method, path, body=None, timeout=30):
        if path == "/automations/import/url":
            return {"token": "t", "preview": {
                "resolvedUrl": "https://example.test/a.autowright\r\x1b[2Kok"}}
        return {"id": "new", "name": "Imported", "triggers": []}


def test_the_resolved_import_url_is_console_safe(capsys, monkeypatch):
    monkeypatch.setattr(cli, "ensure_packages", lambda c, p: None)
    _run(_ImportClient(), "automation", "import", "https://github.com/o/r")
    out = capsys.readouterr().out
    assert "resolved to https://example.test/a.autowright\\x0dok" in out
    assert "\x1b" not in out


# ---------- C8. the GitHub API read is https only ----------

def test_the_github_api_refuses_a_redirect_off_https(monkeypatch):
    def follow(*a, **kw):
        raise AssertionError("the plain urlopen would follow the redirect")

    def opener(request, *, timeout, refuse):
        handler = transfer.HttpsOnlyRedirectHandler(refuse)
        handler.redirect_request(request, io.BytesIO(), 302, "Found", {},
                                 "http://api.github.example/repos/o/r/releases/latest")

    monkeypatch.setattr("urllib.request.urlopen", follow)
    monkeypatch.setattr(transfer, "open_https_only", opener)
    with pytest.raises(transfer.TransferError, match=transfer.REDIRECTED_OFF_HTTPS):
        transfer._github_api("/repos/o/r/releases/latest")


@pytest.mark.parametrize("listing", [
    {"/repos/o/r/releases/latest": {"assets": [
        {"name": "a.autowright", "browser_download_url": "http://example.test/a.autowright"}]}},
    {"/repos/o/r/releases/latest": None,
     "/repos/o/r/contents/": [{"name": "a.autowright", "type": "file",
                               "download_url": "http://example.test/a.autowright"}]},
])
def test_a_listed_asset_that_is_not_https_is_refused_before_any_fetch(monkeypatch, listing):
    def never(*a, **kw):
        raise AssertionError("no request may be sent")

    monkeypatch.setattr(transfer, "_github_api", lambda path: listing.get(path))
    monkeypatch.setattr(transfer, "open_https_only", never)
    with pytest.raises(transfer.TransferError, match="isn't https"):
        transfer.fetch_archive("https://github.com/o/r")
