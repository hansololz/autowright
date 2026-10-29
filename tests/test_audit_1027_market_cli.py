"""Marketplace, transfer, installer, service and CLI audit regressions (2026-10-27).

§22.2 a malformed https link (an unbalanced IPv6 bracket) never answers 500;
§5.2 every outbound read's deadline covers the connect, a body short of its
Content-Length fails, and the GitHub API listing is capped; §19 the Ollama
installer download reads with read1 and fails short, the Linux archive is
validated before the old lib tree goes, and the Mac app swaps by renames;
§3 shim heal/remove never follows a symlink; unit and `.cmd` shim escaping;
§20 a closed pipe ends any command with exit 0, push compares authorship
against the pulled version, and a minute `--until` covers the whole minute.
"""
import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tarfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_cli import FULL_AUTO, _pushed_steps, _run, _two_step_auto, _WorkdirClient
from test_marketplace import write_table

from autowright import installer, marketplace, packages, transfer
from autowright.marketplace import MarketplaceError

BAD_LINK = "https://[host/x.yaml"
SOURCE_ID = "55555555-5555-4555-8555-555555555555"


# ---------- helpers: a server whose body ends short of its Content-Length ----------

class _ShortBody(BaseHTTPRequestHandler):
    """Declares 1000 bytes, sends 500, closes (HTTP/1.0)."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "1000")
        self.end_headers()
        self.wfile.write(b"x" * 500)
        self.wfile.flush()

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


class _HttpsView:
    """A real plain-http response wearing an https landing URL, so the §5.2
    reads run against http.client's own read1 on a short body."""

    def __init__(self, response, url):
        self._response, self._url = response, url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._response.close()
        return False

    def geturl(self):
        return self._url

    def __getattr__(self, name):
        return getattr(self._response, name)


def _plain_opener(base, seen_timeouts=None):
    def opener(request, timeout, refuse):
        if seen_timeouts is not None:
            seen_timeouts.append(timeout)
        return _HttpsView(urllib.request.urlopen(f"{base}/x", timeout=timeout),
                          request.full_url)
    return opener


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    monkeypatch.setenv("no_proxy", "*")


# ---------- 1. §22.2 a malformed https link never answers 500 ----------

def test_a_link_that_does_not_parse_is_refused_on_add_and_patch(client, tmp_path):
    from autowright import api

    r = client.post("/marketplace/sources", json={"url": BAD_LINK})
    assert r.status_code == 422
    assert r.json()["detail"] == marketplace.NOT_A_LINK
    with api.marketplace_store.lock:
        assert all(s["location"] != BAD_LINK for s in api.marketplace_store.sources)

    catalog = tmp_path / "shelf.yaml"
    catalog.write_text("format_version: 1\nentries: []\n", encoding="utf-8")
    source = client.post("/marketplace/sources", json={"path": str(catalog)}).json()
    r = client.patch(f"/marketplace/sources/{source['id']}", json={"location": BAD_LINK})
    assert r.status_code == 422
    assert r.json()["detail"] == marketplace.NOT_A_LINK


def test_a_hand_edited_unparseable_location_skips_the_row_read_only(client):
    from autowright import api

    write_table("sources:\n"
                f"- id: {SOURCE_ID}\n"
                f"  location: '{BAD_LINK}'\n")
    api.marketplace_store.load()
    r = client.get("/marketplace")
    assert r.status_code == 200
    assert all(s["id"] != SOURCE_ID for s in r.json()["sources"])
    # §22.2: the skip flips the table read-only - no save may drop the row.
    r = client.post("/marketplace/sources", json={"url": "https://example.test/a.yaml"})
    assert r.status_code == 409


def test_serializing_a_stored_unparseable_location_never_raises(market):
    assert marketplace.default_name(BAD_LINK) == BAD_LINK
    row = {"id": SOURCE_ID, "location": BAD_LINK, "expanded": True,
           "auto_refresh": False, "builtin": False, "added_at": "",
           "refreshed_at": None, "error": None}
    with market.lock:
        assert market.serialize(row)["name"] == BAD_LINK


def test_entry_references_that_do_not_parse_answer_their_own_error(client, tmp_path):
    from fastapi.testclient import TestClient

    from autowright import api

    catalog = tmp_path / "shelf.yaml"
    catalog.write_text("format_version: 1\nentries:\n"
                       "- title: Broken\n"
                       "  path: 'https://[host/x.autowright'\n"
                       "  image: 'https://[host/x.png'\n", encoding="utf-8")
    source = client.post("/marketplace/sources", json={"path": str(catalog)}).json()
    loose = TestClient(api.app, raise_server_exceptions=False)
    loose.headers.update(client.headers)
    base = f"/marketplace/sources/{source['id']}/entries/0"
    preview = loose.post(f"{base}/preview")
    assert preview.status_code == 422
    assert preview.json()["detail"] == transfer.NOT_A_LINK
    archive = loose.get(f"{base}/archive")
    assert archive.status_code == 422
    # The image route's own code for a reference that can't be read.
    image = loose.get(f"{base}/image")
    assert image.status_code == 502


def test_resolve_url_refuses_a_link_that_does_not_parse():
    with pytest.raises(transfer.TransferError, match=transfer.NOT_A_LINK):
        transfer.resolve_url("https://[github.com/o/r")
    with pytest.raises(transfer.TransferError, match=transfer.NOT_A_LINK):
        transfer.github_raw_url("https://[github.com/o/r/blob/main/x.autowright")


# ---------- 2. §5.2 deadline from before the connect; short bodies fail ----------

def test_the_open_timeout_is_capped_by_what_remains_of_the_deadline(monkeypatch):
    seen = []

    def opener(request, timeout, refuse):
        seen.append(timeout)
        raise OSError("no network here")

    monkeypatch.setattr(transfer, "open_https_only", opener)
    with pytest.raises(MarketplaceError):
        marketplace._fetch_url("https://example.test/m.yaml", cap=10, deadline_s=5)
    assert 0 < seen[0] <= 5


def test_a_short_catalog_download_fails_and_stores_nothing(market, monkeypatch):
    with _serve(_ShortBody) as base:
        monkeypatch.setattr(transfer, "open_https_only", _plain_opener(base))
        with pytest.raises(MarketplaceError, match="closed before"):
            market.add(url="https://example.test/short.yaml")
    with market.lock:
        assert all(s["location"] != "https://example.test/short.yaml"
                   for s in market.sources)


def test_a_short_archive_download_fails(monkeypatch):
    with _serve(_ShortBody) as base:
        seen = []
        monkeypatch.setattr(transfer, "open_https_only", _plain_opener(base, seen))
        with pytest.raises(transfer.TransferError, match="closed before"):
            transfer.fetch_archive("https://example.test/pack.autowright")
    assert seen[0] <= transfer.FETCH_TIMEOUT


class _Body(io.BytesIO):
    def __init__(self, data, length=None):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data) if length is None else length)}


def test_the_github_api_listing_is_capped(monkeypatch):
    big = json.dumps(["x" * 1024] * 2048).encode()  # about 2 MB
    assert len(big) > 2 * 1024 * 1024 - 1
    monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=None: _Body(big))
    with pytest.raises(transfer.TransferError, match="1 MB"):
        transfer._github_api("/repos/o/r/contents/")


def test_a_short_github_api_answer_fails(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None: _Body(b"[]", length=100))
    with pytest.raises(transfer.TransferError, match="closed before"):
        transfer._github_api("/repos/o/r/contents/")
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda req, timeout=None: _Body(b'[{"name": "a"}]'))
    assert transfer._github_api("/repos/o/r/contents/") == [{"name": "a"}]


def test_a_short_pypi_answer_raises(monkeypatch):
    with _serve(_ShortBody) as base:
        real = urllib.request.urlopen
        monkeypatch.setattr("urllib.request.urlopen",
                            lambda req, timeout=None: real(f"{base}/x", timeout=timeout))
        with pytest.raises(ConnectionError):
            packages._latest_compatible("pkg")


# ---------- 3. §19 the Ollama installer ----------

def test_a_short_installer_download_fails_the_phase(tmp_path):
    with _serve(_ShortBody) as base:
        with pytest.raises(RuntimeError, match="closed before"):
            installer._download(f"{base}/x", str(tmp_path / "x.bin"),
                                lambda **k: None, "Downloading Ollama")


def test_a_corrupt_linux_archive_keeps_the_installed_lib_tree(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    old = home / ".local" / "lib" / "ollama" / "libggml.so"
    old.parent.mkdir(parents=True)
    old.write_bytes(b"working")
    monkeypatch.setattr(installer, "_download",
                        lambda url, dest, emit, label: Path(dest).write_bytes(b"garbage"))
    monkeypatch.setattr(installer, "_ensure_login_path", lambda emit: None)
    with pytest.raises((RuntimeError, tarfile.TarError)):
        installer._install_ollama_tarball(lambda **k: None)
    assert old.read_bytes() == b"working"


@pytest.fixture()
def _ollama_app(tmp_path, monkeypatch):
    """`_install_ollama_app` with the download, ditto and pkill faked: the
    unpacked bundle holds a `new` marker; an existing install holds `old`."""
    apps = tmp_path / "Applications"
    (apps / "Ollama.app" / "Contents").mkdir(parents=True)
    (apps / "Ollama.app" / "Contents" / "old").write_text("old")
    monkeypatch.setattr(installer, "APPLICATIONS", str(apps))
    monkeypatch.setattr(installer, "LOCAL_BIN", str(tmp_path / "localbin"))
    monkeypatch.setattr(installer, "USR_LOCAL_BIN", str(tmp_path / "usr-local-bin"))
    monkeypatch.setattr(installer, "_ensure_login_path", lambda emit: None)
    monkeypatch.setattr(installer, "_download",
                        lambda url, dest, emit, label: Path(dest).write_bytes(b"zip"))

    def fake_run(cmd, **kw):
        if cmd[0] == "/usr/bin/ditto":
            bundle = Path(cmd[-1]) / "Ollama.app" / "Contents"
            bundle.mkdir(parents=True)
            (bundle / "new").write_text("new")
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    return apps


def test_the_ollama_app_swap_removes_the_aside_copy(_ollama_app):
    dest = installer._install_ollama_app(lambda **k: None)
    assert (Path(dest) / "Contents" / "new").exists()
    assert not (Path(dest) / "Contents" / "old").exists()
    assert sorted(p.name for p in _ollama_app.iterdir()) == ["Ollama.app"]


def test_a_failed_ollama_app_swap_puts_the_old_bundle_back(_ollama_app, monkeypatch):
    dest = str(_ollama_app / "Ollama.app")
    real_rename = os.rename

    def rename(src, dst):
        if str(src).endswith(".ad-new") and str(dst) == dest:
            raise OSError("disk full")
        return real_rename(src, dst)

    monkeypatch.setattr(installer.os, "rename", rename)
    with pytest.raises(OSError, match="disk full"):
        installer._install_ollama_app(lambda **k: None)
    assert (_ollama_app / "Ollama.app" / "Contents" / "old").read_text() == "old"
    assert sorted(p.name for p in _ollama_app.iterdir()) == ["Ollama.app"]


# ---------- 4. §3 shim heal/remove never follows a symlink ----------

@pytest.mark.parametrize("module_name", ["service", "windows"])
@pytest.mark.parametrize("dangling", [False, True])
def test_a_symlinked_shim_is_foreign(module_name, dangling, tmp_path, monkeypatch):
    if module_name == "service":
        from autowright import service as module
    else:
        from autowright.platform import windows as module
    target = tmp_path / "elsewhere" / "target"
    target.parent.mkdir()
    ours = f"#!/bin/sh\n{module.SHIM_MARKER}\nexec /old/python -m autowright.cli\n"
    if not dangling:
        target.write_text(ours)
        target.chmod(0o600)
    shim = tmp_path / "bin" / "autowright"
    shim.parent.mkdir()
    shim.symlink_to(target)
    monkeypatch.setattr(module, "shim_paths", lambda: [shim])

    assert "foreign" in module._heal_one(shim)
    assert module._remove_shim() is None
    assert shim.is_symlink()
    if dangling:
        assert not target.exists()
    else:
        assert target.read_text() == ours
        assert (target.stat().st_mode & 0o777) == 0o600


# ---------- 5. §5.2 the archive extension compares case-insensitively ----------

def test_an_uppercase_archive_extension_downloads_directly():
    url = "https://example.test/Tool.AUTOWRIGHT"
    assert transfer.resolve_url(url) == url


# ---------- 6. unit and .cmd shim escaping ----------

def test_a_dollar_in_the_interpreter_path_is_doubled_in_exec_start(monkeypatch):
    from autowright.platform import linux

    monkeypatch.setattr(linux, "sys", SimpleNamespace(executable="/home/a$HOME/py3"))
    assert 'ExecStart="/home/a$$HOME/py3" -m autowright.main\n' in linux.unit_text()


def test_a_percent_in_the_interpreter_path_is_doubled_in_the_cmd_shim(monkeypatch):
    from autowright.platform import windows

    monkeypatch.setattr(windows.paths, "console_python",
                        lambda: r"C:\Users\a%USERNAME%b\python.exe")
    assert (r'"C:\Users\a%%USERNAME%%b\python.exe" -m autowright.cli %*' + "\r\n"
            in windows.shim_text())


# ---------- 7. §20 a closed pipe ends any command quietly ----------

def test_a_closed_pipe_exits_zero_without_a_traceback(tmp_path):
    from autowright import cli

    script = tmp_path / "flood.py"
    script.write_text(
        "from autowright import cli\n"
        "def flood(c, a):\n"
        "    for _ in range(200000):\n"
        "        print('x' * 100)\n"
        "args = type('A', (), {'client': False, 'fn': staticmethod(flood)})\n"
        "cli.build_parser = lambda *a, **k: type('P', (), {'parse_args': lambda self: args})()\n"
        "cli.main()\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(Path(cli.__file__).parents[1])}
    proc = subprocess.Popen([sys.executable, str(script)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env)
    proc.stdout.read(10)
    proc.stdout.close()
    stderr = proc.stderr.read().decode()
    assert proc.wait(timeout=60) == 0
    assert "Traceback" not in stderr and "Exception ignored" not in stderr


# ---------- 8. §20 push compares authorship against the pulled version ----------

def _versioned(auto, n, versions=()):
    out = copy.deepcopy(auto)
    out["version"] = n
    out["versions"] = [copy.deepcopy(v) for v in versions]
    return out


def test_push_compares_against_the_version_it_pulled(tmp_path):
    import yaml

    from autowright import cli

    v3 = _versioned(_two_step_auto(), 3)
    d = tmp_path / "wd"
    cli.write_workdir(d, v3)
    assert yaml.safe_load((d / "manifest.yaml").read_text())["pulled_from"] == 3

    # v4 landed server-side: the agent rewrote step 2's script.
    v4 = _versioned(_two_step_auto(), 4, [{"version": 3, "steps": v3["steps"]}])
    v4["steps"][1]["code"] = "print('report v4')\n"
    (d / "spec.md").write_text((d / "spec.md").read_text() + "\nMore.\n", encoding="utf-8")
    c = _WorkdirClient(v4)
    _run(c, "automation", "push", "Daily Report", str(d), "--force")
    assert all("authoredBy" not in s for s in _pushed_steps(c))
    _, _, body = next(p for p in c.posted if p[1].endswith("/versions"))
    assert "pulled_from" not in json.dumps(body)


def test_push_falls_back_to_the_current_version_when_the_pulled_one_is_gone(tmp_path):
    from autowright import cli

    d = tmp_path / "wd"
    cli.write_workdir(d, _versioned(_two_step_auto(), 1))
    v4 = _versioned(_two_step_auto(), 4)
    v4["steps"][1]["code"] = "print('report v4')\n"
    c = _WorkdirClient(v4)
    _run(c, "automation", "push", "Daily Report", str(d), "--force")
    first, second = _pushed_steps(c)
    assert "authoredBy" not in first and second["authoredBy"] == "user"


def test_validate_never_sends_pulled_from(tmp_path, capsys):
    from autowright import cli

    class _Clean(_WorkdirClient):
        def req(self, method, path, body=None, timeout=30):
            if method == "POST" and path == "/validate-steps":
                self.posted.append((method, path, body))
                return {"errors": []}
            return super().req(method, path, body, timeout)

    d = tmp_path / "wd"
    cli.write_workdir(d, _versioned(FULL_AUTO, 2))
    c = _Clean()
    _run(c, "automation", "validate", str(d))
    assert capsys.readouterr().out.strip() == "ok"
    assert "pulled_from" not in json.dumps(c.posted[0][2])


def test_duplicate_named_steps_pair_kth_to_kth():
    from autowright import cli

    stored = [{"file": "01-fetch.py", "name": "Fetch", "code": "print(1)\n"},
              {"file": "02-fetch.py", "name": "Fetch", "code": "print(2)\n"}]
    draft = {"steps": [dict(s) for s in stored]}
    cli._apply_step_authorship(draft, {"manifest.yaml": ""}, stored)
    assert all("authoredBy" not in s for s in draft["steps"])

    draft = {"steps": [dict(s) for s in stored]}
    draft["steps"][1]["code"] = "print('mine')\n"
    cli._apply_step_authorship(draft, {"manifest.yaml": ""}, stored)
    assert "authoredBy" not in draft["steps"][0]
    assert draft["steps"][1]["authoredBy"] == "user"


# ---------- 10. §20 validate's help names the grant checks it skips ----------

def test_validate_help_says_grant_checks_run_on_push_and_create(capsys):
    from autowright import cli

    with pytest.raises(SystemExit):
        cli.build_parser(full=True).parse_args(["automation", "validate", "--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert "only by `automation push` and `automation create`" in help_text


@pytest.fixture()
def market(home):
    s = marketplace.MarketplaceStore()
    s.load()
    return s

