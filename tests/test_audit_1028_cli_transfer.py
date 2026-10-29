"""CLI, transfer and marketplace audit regressions (2026-10-28).

§5.2/§22.2 a redirect to a malformed Location is a failed download, never a
500, and the auto-refresh sweep goes on past a bad row; §20 a closed pipe
ends a `sys.exit` command quietly too, a non-JSON reply is "backend isn't
reachable", link checks match the scheme case-insensitively, identical
manifest schedules collapse to one trigger, a stale workdir does not push
silently, and pull refuses a step file that isn't a plain step file name;
§5.1 import and export refuse blank and duplicate step names, and export
refuses a step modified on disk.
"""
import contextlib
import copy
import json
import os
import subprocess
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from conftest import make_version
from test_cli import FULL_AUTO, _pushed_steps, _run, _two_step_auto, _WorkdirClient
from test_marketplace import write_table
from test_transfer import _archive

from autowright import marketplace, transfer

BAD_LOCATION = "https://[evil/x.autowright"
FIRST_ID = "66666666-6666-4666-8666-666666666666"
SECOND_ID = "77777777-7777-4777-8777-777777777777"


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    monkeypatch.setenv("no_proxy", "*")


# ---------- C1. a redirect to a malformed Location ----------

class _BadRedirect(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", BAD_LOCATION)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


@contextlib.contextmanager
def _serve(handler_cls):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def _redirecting_opener(base):
    """The real https-only opener, aimed at the local plain-http server that
    answers 302 with the malformed Location."""
    def opener(request, timeout, refuse):
        built = urllib.request.build_opener(transfer.HttpsOnlyRedirectHandler(refuse))
        return built.open(f"{base}/x", timeout=timeout)
    return opener


def test_a_malformed_redirect_on_import_url_answers_422(client, monkeypatch):
    with _serve(_BadRedirect) as base:
        monkeypatch.setattr(transfer, "open_https_only", _redirecting_opener(base))
        r = client.post("/automations/import/url",
                        json={"url": "https://example.test/pack.autowright"})
    assert r.status_code == 422
    assert r.json()["detail"].startswith("download failed")


def test_a_malformed_redirect_on_marketplace_add_answers_422(client, monkeypatch):
    with _serve(_BadRedirect) as base:
        monkeypatch.setattr(transfer, "open_https_only", _redirecting_opener(base))
        r = client.post("/marketplace/sources",
                        json={"url": "https://example.test/shelf.yaml"})
    assert r.status_code == 422
    assert "download failed" in r.json()["detail"]


def test_a_malformed_redirect_on_the_github_api_is_a_transfer_error(monkeypatch):
    def urlopen(request, timeout=None):
        raise ValueError("Invalid IPv6 URL")

    monkeypatch.setattr("urllib.request.urlopen", urlopen)
    with pytest.raises(transfer.TransferError, match="couldn't reach GitHub"):
        transfer._github_api("/repos/o/r/releases/latest")


def _two_flagged_rows():
    write_table("sources:\n"
                f"- id: {FIRST_ID}\n"
                "  location: https://example.test/first.yaml\n"
                "  auto_refresh: true\n"
                f"- id: {SECOND_ID}\n"
                "  location: https://example.test/second.yaml\n"
                "  auto_refresh: true\n")
    market = marketplace.MarketplaceStore()
    market.load()
    return market


def _row(market, source_id):
    with market.lock:
        return next(s for s in market.sources if s["id"] == source_id)


def test_the_sweep_records_a_malformed_redirect_and_goes_on(home, monkeypatch):
    market = _two_flagged_rows()
    good = "format_version: 1\nname: Second\nentries: []\n"

    def read_location(location):
        if location.endswith("first.yaml"):
            with _serve(_BadRedirect) as base:
                monkeypatch.setattr(transfer, "open_https_only", _redirecting_opener(base))
                return marketplace.MarketplaceStore._read_location(market, location)
        return good

    monkeypatch.setattr(market, "_read_location", read_location)
    swept = market.auto_refresh_sweep()
    assert FIRST_ID in swept and SECOND_ID in swept
    assert "download failed" in _row(market, FIRST_ID)["error"]
    second = _row(market, SECOND_ID)
    assert second["error"] is None and second["refreshed_at"]


def test_the_sweep_guards_each_row_against_any_failure(home, monkeypatch):
    market = _two_flagged_rows()

    def fetch_catalog(location):
        if location.endswith("first.yaml"):
            raise RuntimeError("boom")
        return "format_version: 1\nname: Second\nentries: []\n", None

    monkeypatch.setattr(market, "_fetch_catalog", fetch_catalog)
    market.auto_refresh_sweep()
    assert "boom" in _row(market, FIRST_ID)["error"]
    assert _row(market, SECOND_ID)["refreshed_at"]


# ---------- C2. a closed pipe ends a sys.exit command quietly ----------

def test_a_closed_pipe_on_a_sys_exit_status_exits_zero_silently(tmp_path):
    from autowright import cli

    # The output stays in stdout's buffer (under its 8 KB size, so no print
    # flushes it early) until the command's own sys.exit(2) - the closed
    # pipe is met by main's final flush.
    script = tmp_path / "failing.py"
    script.write_text(
        "import sys\n"
        "from autowright import cli\n"
        "def fail(c, a):\n"
        "    sys.stdin.read()\n"
        "    for _ in range(60):\n"
        "        print('x' * 100)\n"
        "    sys.exit(2)\n"
        "args = type('A', (), {'client': False, 'fn': staticmethod(fail)})\n"
        "cli.build_parser = lambda *a, **k: type('P', (), {'parse_args': lambda self: args})()\n"
        "cli.main()\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(Path(cli.__file__).parents[1])}
    proc = subprocess.Popen([sys.executable, str(script)], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    proc.stdout.close()
    proc.stdin.close()
    stderr = proc.stderr.read().decode()
    assert proc.wait(timeout=60) == 0
    assert stderr == ""


# ---------- C3. step names: import and export ----------

def test_import_rejects_a_blank_step_name(store):
    data = _archive(steps=[{"file": "01-a.py", "name": "   ", "description": "",
                            "code": "print('a')\n"}])
    with pytest.raises(transfer.TransferError, match=r"^step 1 needs a name$"):
        transfer.import_automation(store, data)


def test_import_rejects_step_names_shared_once_trimmed(store):
    data = _archive(steps=[
        {"file": "01-a.py", "name": "Fetch", "description": "", "code": "print('a')\n"},
        {"file": "02-b.py", "name": " Fetch ", "description": "", "code": "print('b')\n"}])
    with pytest.raises(transfer.TransferError,
                       match=r"^step names must be unique: `Fetch` appears twice$"):
        transfer.import_automation(store, data)


def test_import_accepts_distinct_step_names(store):
    data = _archive(steps=[
        {"file": "01-a.py", "name": "Fetch", "description": "", "code": "print('a')\n"},
        {"file": "02-b.py", "name": "Report", "description": "", "code": "print('b')\n"}])
    transfer.import_automation(store, data)


def _current_steps(a):
    return a["versions"][a["current_version"]]["steps"]


def test_export_refuses_a_step_modified_on_disk(store):
    a = store.create_automation(make_version(), "Hand edited", "mock")
    transfer.export_automation(store, a)  # sealed and untouched: travels
    script = store.auto_dir(a) / "versions" / f"v{a['current_version']}" / "02-finish.py"
    script.write_text("print('changed by hand')\n", encoding="utf-8")
    with pytest.raises(transfer.TransferError, match=r"^step 'Finish' changed on disk"):
        transfer.export_automation(store, a)


def test_export_refuses_step_names_shared_once_trimmed(store):
    a = store.create_automation(make_version(), "Pre rule", "mock")
    _current_steps(a)[1]["name"] = " Say hello "
    with pytest.raises(transfer.TransferError,
                       match=r"^step names must be unique: `Say hello` appears twice"):
        transfer.export_automation(store, a)


def test_export_refuses_a_blank_step_name(store):
    a = store.create_automation(make_version(), "Blank", "mock")
    _current_steps(a)[0]["name"] = "  "
    with pytest.raises(transfer.TransferError, match=r"^step 1 needs a name"):
        transfer.export_automation(store, a)


def test_the_export_route_answers_422_for_duplicate_step_names(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Pre rule route", "mock")
    _current_steps(a)[1]["name"] = "Say hello"
    r = client.get(f"/automations/{a['id']}/export")
    assert r.status_code == 422
    assert "`Say hello` appears twice" in r.json()["detail"]


# ---------- C4. identical manifest schedules collapse to one trigger ----------

def test_the_same_cron_twice_merges_to_one_trigger_with_the_stored_id(tmp_path):
    from autowright import cli

    d = tmp_path / "wd"
    cli.write_workdir(d, FULL_AUTO)
    manifest = yaml.safe_load((d / "manifest.yaml").read_text())
    manifest["triggers"] = manifest["triggers"] * 2
    (d / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    c = _WorkdirClient()
    _run(c, "automation", "push", "Daily Report", str(d))
    _, _, body = next(p for p in c.posted if p[1].endswith("/versions"))
    crons = [t for t in body["draft"]["triggers"] if t["kind"] == "cron"]
    assert len(crons) == 1
    assert crons[0]["id"] == "t1"


def test_merge_collapses_drafted_duplicates_without_a_stored_match():
    from autowright import cli

    drafted = [{"kind": "interval", "every": "PT1H", "enabled": True, "source": "spec"},
               {"kind": "interval", "every": "PT1H", "enabled": True, "source": "spec"}]
    merged = cli.merge_draft_triggers([], drafted)
    assert len(merged) == 1


# ---------- C5. a stale workdir does not push silently ----------

def _versioned(auto, n):
    out = copy.deepcopy(auto)
    out["version"] = n
    out["versions"] = []
    return out


def _stale_workdir(tmp_path):
    from autowright import cli

    d = tmp_path / "wd"
    cli.write_workdir(d, _versioned(_two_step_auto(), 3))
    return d


def test_a_stale_push_is_refused(tmp_path):
    d = _stale_workdir(tmp_path)
    c = _WorkdirClient(_versioned(_two_step_auto(), 5))
    with pytest.raises(SystemExit) as ei:
        _run(c, "automation", "push", "Daily Report", str(d))
    assert ei.value.code == ("'Daily Report' is at v5; this workdir was pulled from v3 "
                             "— pull again, or pass --force to save over v4..v5")
    assert not any(p[1].endswith("/versions") for p in c.posted)


def test_force_pushes_a_stale_workdir(tmp_path):
    d = _stale_workdir(tmp_path)
    c = _WorkdirClient(_versioned(_two_step_auto(), 5))
    _run(c, "automation", "push", "Daily Report", str(d), "--force")
    assert _pushed_steps(c)


def test_a_manifest_without_pulled_from_is_never_refused(tmp_path):
    d = _stale_workdir(tmp_path)
    manifest = yaml.safe_load((d / "manifest.yaml").read_text())
    manifest.pop("pulled_from")
    (d / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    c = _WorkdirClient(_versioned(_two_step_auto(), 5))
    _run(c, "automation", "push", "Daily Report", str(d))
    assert _pushed_steps(c)


# ---------- C6. pull refuses a step file that isn't a plain name ----------

@pytest.mark.parametrize("file", [None, "../x.py", "/tmp/evil.py"])
def test_pull_refuses_a_step_file_that_is_not_a_plain_name(tmp_path, file):
    from autowright import cli

    auto = _two_step_auto()
    if file is None:
        auto["steps"][1].pop("file")
    else:
        auto["steps"][1]["file"] = file
    d = tmp_path / "wd"
    with pytest.raises(SystemExit) as ei:
        cli.write_workdir(d, auto)
    assert "'Report'" in str(ei.value.code)
    assert not d.exists()


# ---------- C7. non-JSON replies and case-insensitive scheme checks ----------

def test_a_non_json_reply_is_backend_not_reachable(home, monkeypatch):
    from autowright import cli, paths

    paths.backend_json().write_text(json.dumps({"port": 5151, "token": "tok"}))
    c = cli.Client()

    class _Html:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"<html>someone else's server</html>"

    monkeypatch.setattr(cli._opener, "open", lambda request, timeout: _Html())
    with pytest.raises(SystemExit) as ei:
        c.req("GET", "/automations")
    assert str(ei.value.code).startswith("backend isn't reachable at http://127.0.0.1:5151")

    class _Undecodable(_Html):
        def read(self):
            return b"\xff\xfe\xfd"

    monkeypatch.setattr(cli._opener, "open", lambda request, timeout: _Undecodable())
    with pytest.raises(SystemExit) as ei:
        c.req("GET", "/automations")
    assert str(ei.value.code).startswith("backend isn't reachable")


class _Recorder:
    def __init__(self):
        self.posted = []

    def req(self, method, path, body=None, timeout=30):
        self.posted.append((method, path, body))
        if path == "/marketplace":
            return {"sources": [{"id": FIRST_ID, "name": "Shelf"}]}
        if path == "/automations/import/url":
            raise SystemExit("stop here")
        return {"id": FIRST_ID, "name": "Shelf", "entries": []}


def test_an_uppercase_scheme_imports_as_a_link():
    c = _Recorder()
    with pytest.raises(SystemExit):
        _run(c, "automation", "import", "HTTPS://example.test/pack.autowright")
    assert c.posted[0][1] == "/automations/import/url"
    assert c.posted[0][2] == {"url": "HTTPS://example.test/pack.autowright"}


def test_an_uppercase_scheme_adds_a_marketplace_link():
    c = _Recorder()
    _run(c, "marketplace", "add", "HTTPS://example.test/shelf.yaml")
    assert c.posted[0][2] == {"url": "HTTPS://example.test/shelf.yaml"}


def test_an_uppercase_scheme_sets_a_marketplace_location_as_a_link():
    c = _Recorder()
    _run(c, "marketplace", "set", "Shelf", "location=Https://example.test/shelf.yaml")
    patch = next(body for method, _, body in c.posted if method == "PATCH")
    assert patch == {"location": "Https://example.test/shelf.yaml"}
