"""Declared-package management (§6.2) — no network, no pip runs."""
import io
import json

import pytest


def _fake_process_control(monkeypatch, fall_back_to_child=False):
    """§2: `_pip_install` spawns and kills through the platform layer's
    ProcessControl. Swap it for a recorder (host-independent — never the real
    killpg/taskkill) and hand back the list of killed pids. With
    `fall_back_to_child`, the fake behaves like a group kill that finds no
    group: the direct child is killed instead (posixproc's own fallback)."""
    import dataclasses

    from autowright import platform as platmod

    killed: list[int] = []

    class RecordingProcesses:
        def session_kwargs(self) -> dict:
            return {}

        def signal_group(self, proc, sig=None) -> None:
            killed.append(proc.pid)
            if fall_back_to_child and proc.poll() is None:
                proc.kill()

    fake = dataclasses.replace(platmod.current(), processes=RecordingProcesses())
    monkeypatch.setattr(platmod, "current", lambda: fake)
    return killed


def _fake_dist(home, name="requests", version="2.31.0", invalidate=True, import_name=None):
    """Minimal installed distribution in the §6.2 site-packages dir: the
    dist-info AND the import target beside it, which is what §6.2 counts as
    installed (a dist-info alone is a killed pip's leftover)."""
    d = home / "site-packages" / f"{name}-{version}.dist-info"
    d.mkdir(parents=True)
    (d / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8")
    module = home / "site-packages" / (import_name or name.replace("-", "_").lower())
    module.mkdir(parents=True, exist_ok=True)
    (module / "__init__.py").write_text("", encoding="utf-8")
    # importlib.metadata's FastPath caches directory listings keyed on a
    # coarse mtime — under -n auto load, a dist created within the same tick
    # reads back as absent (version None). Drop the caches every time.
    import importlib

    importlib.invalidate_caches()
    # §6.2: only pip writes to that directory in production, and every pip run
    # drops the cached scan — a fabricated dist-info stands in for one, so it
    # drops the cache too (the directory key itself is only re-read once a
    # second). `invalidate=False` is for the cache test, which owns the key.
    if invalidate:
        from autowright import packages

        packages.invalidate_scan()


def test_norm_pep503():
    from autowright.packages import _norm

    assert _norm("requests") == "requests"
    assert _norm("Beautiful.Soup_4") == "beautiful-soup-4"
    assert _norm("A__b..c--d") == "a-b-c-d"
    assert _norm("Typing_Extensions") == "typing-extensions"


def test_pip_name_re():
    from autowright.packages import PIP_NAME_RE

    assert PIP_NAME_RE.match("requests")
    assert PIP_NAME_RE.match("beautifulsoup4")
    assert PIP_NAME_RE.match("typing_extensions")
    assert not PIP_NAME_RE.match("bad name!")
    assert not PIP_NAME_RE.match("")
    assert not PIP_NAME_RE.match("-leading")
    assert not PIP_NAME_RE.match("requests==2.0")  # §6.2: bare names, no specifier


def test_check_missing_then_installed(home):
    from autowright.packages import check

    entries = [{"pip": "requests", "import": "requests"}]
    assert check(entries) == [{"pip": "requests", "import": "requests", "status": "missing"}]

    _fake_dist(home, "requests", "2.31.0")
    assert check(entries) == [{"pip": "requests", "import": "requests",
                               "status": "installed", "version": "2.31.0"}]
    # normalization applies: manifest spelling differs, distribution still found
    assert check([{"pip": "Requests", "import": "requests"}])[0]["status"] == "installed"
    # invalid names never match anything
    assert check([{"pip": "bad name!", "import": "x"}])[0]["status"] == "missing"


def _pypi_payload():
    """Releases crafted so every skip rule fires before the winner (1.2)."""
    return {"releases": {
        "2.0a1": [{"filename": "pkg-2.0a1-py3-none-any.whl"}],           # prerelease
        "1.9.dev1": [{"filename": "pkg-1.9.dev1-py3-none-any.whl"}],     # dev release
        "1.8": [],                                                        # no files
        "1.7": [{"filename": "pkg-1.7-py3-none-any.whl", "yanked": True}],
        "1.6": [{"filename": "pkg-1.6.tar.gz"}],                          # sdist only
        "1.5": [{"filename": "pkg-1.5-cp27-cp27m-manylinux1_x86_64.whl"}],  # bad tags
        "1.2": [{"filename": "pkg-1.2-py3-none-any.whl"}],                # winner
        "not-a-version": [{"filename": "pkg-x-py3-none-any.whl"}],
    }}


def test_latest_compatible_picks_newest_valid_wheel(monkeypatch):
    from autowright.packages import _latest_compatible

    urls = []

    def fake_urlopen(req, timeout=None):
        urls.append(req.full_url)
        return io.BytesIO(json.dumps(_pypi_payload()).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert _latest_compatible("Pkg") == "1.2"
    # lookup goes to the PEP 503-normalized project URL
    assert urls == ["https://pypi.org/pypi/pkg/json"]


def test_latest_compatible_none_when_nothing_ships_a_usable_wheel(monkeypatch):
    from autowright.packages import _latest_compatible

    payload = {"releases": {"1.0": [{"filename": "pkg-1.0.tar.gz"}]}}
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: io.BytesIO(json.dumps(payload).encode("utf-8")))
    assert _latest_compatible("pkg") is None


def test_fetch_failure_means_no_update_badge(monkeypatch):
    """§6.2 advisory contract: _latest_compatible raises on a fetch failure;
    the swallow lives in outdated's probe (its only caller), so through the
    §19 outdated endpoint the badge simply stays off."""
    import urllib.error

    from autowright import packages

    def boom(req, timeout=None):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(urllib.error.URLError):
        packages._latest_compatible("pkg")

    monkeypatch.setattr(packages, "_installed_versions", lambda: {"pkg": "1.0"})
    assert packages.outdated([{"pip": "pkg", "import": "pkg"}]) == [
        {"pip": "pkg", "import": "pkg"}]  # no "latest" key


def test_outdated_empty_entries_and_skip_paths(home, monkeypatch):
    """outdated([]) short-circuits; a broken dist-info never blocks the
    installed check; an unparsable wheel filename is skipped, not fatal."""
    from autowright import packages

    assert packages.outdated([]) == []

    # broken dist-info (no Name in METADATA) — siblings still resolve
    bad = home / "site-packages" / "broken-0.0.0.dist-info"
    bad.mkdir(parents=True)
    (bad / "METADATA").write_text("Metadata-Version: 2.1\n", encoding="utf-8")
    _fake_dist(home, "requests", "2.31.0")
    assert packages.check([{"pip": "requests", "import": "requests"}])[0]["status"] == "installed"

    # a .whl whose filename doesn't parse is skipped; the next release wins
    payload = {"releases": {
        "1.3": [{"filename": "pkg-1.3.whl"}],  # not name-ver-tags shaped
        "1.2": [{"filename": "pkg-1.2-py3-none-any.whl"}],
    }}
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda req, timeout=None: io.BytesIO(json.dumps(payload).encode("utf-8")))
    assert packages._latest_compatible("pkg") == "1.2"


def test_pip_install_command_shape_and_pins(home, monkeypatch):
    """§6.2 _pip_install: wheels-only into the site-packages dir; with
    pin_installed the constraints file pins every installed distribution
    except the target, and is removed afterwards."""
    from pathlib import Path
    from types import SimpleNamespace

    from autowright import packages

    _fake_dist(home, "requests", "2.31.0")
    _fake_dist(home, "PyYAML", "6.0.2")
    seen = {}

    class FakeProc:
        def __init__(self, cmd, **kw):
            seen["cmd"] = cmd
            i = cmd.index("-c")
            seen["constraints_path"] = cmd[i + 1]
            # read while it still exists — it is deleted right after the run
            seen["constraints"] = Path(cmd[i + 1]).read_text()
            self.pid = 4242
            self.returncode = 0

        def communicate(self, timeout=None):
            return "", ""

        def poll(self):
            return self.returncode

    monkeypatch.setattr(packages.subprocess, "Popen", FakeProc)
    assert packages._pip_install("requests", pin_installed=True) is None
    cmd = seen["cmd"]
    assert cmd[-1] == "requests"
    assert cmd[cmd.index("--only-binary") + 1] == ":all:"
    assert cmd[cmd.index("--target") + 1] == str(packages.site_packages_dir())
    # pins: every installed dist (PEP 503-normalized) except the target
    assert set(seen["constraints"].splitlines()) == {"pyyaml==6.0.2"}
    assert not Path(seen["constraints_path"]).exists()  # tmp file cleaned up


def test_pip_install_timeout_cancel_and_stderr_tail(home, monkeypatch):
    import subprocess as sp

    from autowright import packages

    killed = _fake_process_control(monkeypatch)

    class HungProc:
        def __init__(self, cmd, **kw):
            self.pid = 4242
            self.returncode = None
            self._killed = False
            self.reaped = False
            # A survivor of the group kill holds the pipes open, so the drain
            # times out too and closes them directly.
            self.stdout, self.stderr = io.StringIO(), io.StringIO()

        def communicate(self, timeout=None):
            # The drain's own communicate hangs too — the pipes are held open
            # by the survivor, so the direct kill + wait below is the way out.
            raise sp.TimeoutExpired("pip", timeout)

        def poll(self):
            return self.returncode

        def kill(self):
            self._killed = True

        def wait(self, timeout=None):
            self.reaped = True
            return 0

    spawned = []
    monkeypatch.setattr(packages.subprocess, "Popen",
                        lambda cmd, **kw: spawned.append(HungProc(cmd, **kw)) or spawned[-1])
    # signal_group is a no-op recorder, so communicate() keeps hanging until
    # the deadline path fires — make the deadline immediate.
    monkeypatch.setattr(packages, "INSTALL_TIMEOUT", 0)
    assert packages._pip_install("leftpad") == "pip timed out after 0 s"
    assert killed == [4242]
    # §6.2: the hard-killed pip is reaped — an unwaited one stays a zombie.
    assert spawned[-1]._killed and spawned[-1].reaped

    # should_stop wins before the deadline — the run comes back "cancelled".
    monkeypatch.setattr(packages, "INSTALL_TIMEOUT", 600)
    killed.clear()
    assert packages._pip_install("leftpad", should_stop=lambda: True) == "cancelled"
    assert killed == [4242]

    class FailedProc:
        def __init__(self, cmd, **kw):
            self.pid = 4242
            self.returncode = 1

        def communicate(self, timeout=None):
            return "", "ERROR: one\n  two  \nthree\nfour\n"

        def poll(self):
            return self.returncode

    monkeypatch.setattr(packages.subprocess, "Popen", FailedProc)
    # message = last 3 stderr lines, stripped, joined with " · "
    assert packages._pip_install("leftpad") == "two · three · four"


def test_ensure_invalid_cancelled_and_missing_only(home, monkeypatch):
    """§6.2 ensure: invalid names and a should_stop cancel fail without pip;
    pip runs (pinned) only for missing entries and fills the real version."""
    from autowright import packages

    calls = []

    def fake_pip(name, pin_installed=False, should_stop=None):
        calls.append((name, pin_installed))
        _fake_dist(home, name, "1.0.0")  # what a successful install leaves behind
        return None

    monkeypatch.setattr(packages, "_pip_install", fake_pip)

    out = packages.ensure([{"pip": "bad name!", "import": "x"}])
    assert out == [{"pip": "bad name!", "import": "x", "status": "failed",
                    "error": "not a bare distribution name"}]
    assert calls == []

    out = packages.ensure([{"pip": "leftpad", "import": "leftpad"}],
                          should_stop=lambda: True)
    assert out == [{"pip": "leftpad", "import": "leftpad", "status": "failed",
                    "error": "cancelled"}]
    assert calls == []

    _fake_dist(home, "requests", "2.31.0")
    out = packages.ensure([{"pip": "requests", "import": "requests"},
                           {"pip": "leftpad", "import": "leftpad"}])
    assert calls == [("leftpad", True)]  # installed entry untouched, pins on
    assert out == [
        {"pip": "requests", "import": "requests", "status": "installed", "version": "2.31.0"},
        {"pip": "leftpad", "import": "leftpad", "status": "installed", "version": "1.0.0"},
    ]


def test_ensure_fast_path_never_spawns_pip(home, monkeypatch):
    from autowright import packages

    def no_pip(*a, **kw):
        raise AssertionError("ensure must not run pip when everything is installed")

    monkeypatch.setattr(packages.subprocess, "Popen", no_pip)
    _fake_dist(home, "requests", "2.31.0")
    _fake_dist(home, "pyyaml", "6.0.2", import_name="yaml")
    out = packages.ensure([{"pip": "requests", "import": "requests"},
                           {"pip": "pyyaml", "import": "yaml"}])
    assert out == [
        {"pip": "requests", "import": "requests", "status": "installed", "version": "2.31.0"},
        {"pip": "pyyaml", "import": "yaml", "status": "installed", "version": "6.0.2"},
    ]


def test_pip_install_spawn_failure_and_kill_fallback(home, monkeypatch):
    """§6.2 _pip_install edges: a pip that can't even spawn returns the OS
    error as the failure; a kill whose group is already gone falls back to
    the direct child; a constraints file already gone is swallowed."""
    import subprocess as sp
    from pathlib import Path

    from autowright import packages

    _fake_dist(home, "requests", "2.31.0")  # something to pin → constraints exist

    def no_spawn(cmd, **kw):
        raise OSError("posix_spawn failed")

    monkeypatch.setattr(packages.subprocess, "Popen", no_spawn)
    assert packages._pip_install("leftpad", pin_installed=True) == "posix_spawn failed"

    # the layer's group kill finds no group → the direct proc.kill() fallback
    _fake_process_control(monkeypatch, fall_back_to_child=True)

    class HungProc:
        def __init__(self, cmd, **kw):
            self.pid = 4242
            self.returncode = None
            self.killed = False
            # simulate an outside cleanup racing the finally-unlink
            i = cmd.index("-c")
            Path(cmd[i + 1]).unlink()

        def communicate(self, timeout=None):
            if self.killed or timeout is None:
                return "", ""
            raise sp.TimeoutExpired("pip", timeout)

        def poll(self):
            return None

        def kill(self):
            self.killed = True

    monkeypatch.setattr(packages.subprocess, "Popen", HungProc)
    monkeypatch.setattr(packages, "INSTALL_TIMEOUT", 0)
    out = packages._pip_install("leftpad", pin_installed=True)
    assert out == "pip timed out after 0 s"  # fallback kill let communicate drain


def test_ensure_reports_progress_and_pip_failure(home, monkeypatch):
    """§6.2 ensure: on_progress fires once per actual pip run, and a pip
    failure lands on the entry as status failed + the pip error text."""
    from autowright import packages

    _fake_dist(home, "requests", "2.31.0")
    progressed = []
    monkeypatch.setattr(packages, "_pip_install",
                        lambda name, pin_installed=False, should_stop=None:
                        "no matching distribution")
    out = packages.ensure([{"pip": "requests", "import": "requests"},
                           {"pip": "leftpad", "import": "leftpad"}],
                          on_progress=progressed.append)
    assert progressed == ["leftpad"]  # never for the already-installed entry
    assert out == [
        {"pip": "requests", "import": "requests", "status": "installed", "version": "2.31.0"},
        {"pip": "leftpad", "import": "leftpad", "status": "failed",
         "error": "no matching distribution"},
    ]


def test_upgrade_always_runs_pip_and_reports(home, monkeypatch):
    """§19 upgrade (the §11 Update button): pip runs unpinned for an installed
    distribution; invalid names, entries that aren't installed, and pip
    failures come back failed with no version, successes re-read the real
    installed version."""
    from autowright import packages

    _fake_dist(home, "requests", "2.31.0")
    _fake_dist(home, "leftpad", "1.0.0")
    calls = []

    def fake_pip(name, pin_installed=False, should_stop=None):
        calls.append((name, pin_installed))
        if name == "leftpad":
            return "no matching distribution"
        # what a real upgrade leaves behind: the new dist-info replaces the old
        import shutil
        shutil.rmtree(home / "site-packages" / f"{name}-2.31.0.dist-info")
        _fake_dist(home, name, "9.9.9")
        return None

    monkeypatch.setattr(packages, "_pip_install", fake_pip)
    out = packages.upgrade([{"pip": "bad name!", "import": "x"},
                            {"pip": "requests", "import": "requests"},
                            {"pip": "leftpad", "import": "leftpad"}])
    # invalid name: no pip run; the others run unpinned (upgrade moves them)
    assert calls == [("requests", False), ("leftpad", False)]
    assert out[0] == {"pip": "bad name!", "import": "x", "status": "failed",
                      "error": "not a bare distribution name"}
    assert out[1]["status"] == "installed" and out[1]["version"] == "9.9.9"
    assert out[2] == {"pip": "leftpad", "import": "leftpad", "status": "failed",
                      "error": "no matching distribution"}


def test_installed_scan_is_cached_until_the_directory_changes(home, monkeypatch):
    """§6.2/§4.1: the installed-check is served from a cached scan - the §4.1
    problems audit runs it per automation on every /state, so it must neither
    walk site-packages every time nor re-read the directory key once per
    automation. The cache drops when the directory changes."""
    from autowright import packages

    scans = []
    real = packages._scan_installed
    monkeypatch.setattr(packages, "_scan_installed",
                        lambda: scans.append(1) or real())
    keys = []
    real_key = packages._scan_key
    monkeypatch.setattr(packages, "_scan_key", lambda: keys.append(1) or real_key())

    entries = [{"pip": "requests", "import": "requests"}]
    assert packages.check(entries)[0]["status"] == "missing"
    packages.check(entries)
    packages.check(entries)
    assert len(scans) == 1  # repeated checks hit the cache
    assert len(keys) == 1  # and inside the window don't even re-read the key

    # Past the key window the directory is read again, and a new dist-info
    # moves the key.
    monkeypatch.setattr(packages, "_SCAN_KEY_TTL_S", 0.0)
    _fake_dist(home, "requests", "2.31.0", invalidate=False)
    assert packages.check(entries)[0]["version"] == "2.31.0"
    assert len(scans) == 2
    packages.check(entries)
    assert len(scans) == 2  # cached again at the new key

    packages.invalidate_scan()  # what ensure/upgrade call after every pip run
    packages.check(entries)
    assert len(scans) == 3


def test_ensure_invalidates_the_scan_after_installing(home, monkeypatch):
    """§6.2: a pip run inside one ensure must be visible to the check that
    reads back the installed version, even at an unchanged directory key."""
    from autowright import packages

    def fake_pip(name, pin_installed=False, should_stop=None):
        _fake_dist(home, name, "1.2.3")
        return None

    monkeypatch.setattr(packages, "_pip_install", fake_pip)
    packages.check([{"pip": "leftpad", "import": "leftpad"}])  # seeds the cache
    out = packages.ensure([{"pip": "leftpad", "import": "leftpad"}])
    assert out[0]["status"] == "installed" and out[0]["version"] == "1.2.3"


# ---------- §6.2/§19: a held pip lock is answered, never waited out ----------

def test_ensure_and_upgrade_report_a_busy_pip_lock_instead_of_waiting(home):
    """§19: "a request that finds the pip lock held by another install answers
    409 … instead of holding a request worker until it frees" — the module
    raises `PackagesBusy` and the route turns it into that 409."""
    import threading
    import time

    from autowright import packages

    # `home`: the §6.2 site-packages dir is the empty temp one, so leftpad is
    # missing and ensure has to reach for the lock (the real user dir may hold it).
    held, release = threading.Event(), threading.Event()

    def hold_the_lock():
        with packages._pip_lock:
            held.set()
            release.wait(20)

    holder = threading.Thread(target=hold_the_lock, daemon=True)
    holder.start()
    assert held.wait(5), "the lock holder never started"
    entries = [{"pip": "leftpad", "import": "leftpad"}]
    try:
        started = time.monotonic()
        with pytest.raises(packages.PackagesBusy):
            packages.ensure(entries, wait=False)
        with pytest.raises(packages.PackagesBusy):
            packages.upgrade(entries, wait=False)
        assert time.monotonic() - started < 2
        # The waiting mode (engine / drafting / import) never raises: a stop
        # that lands during the wait answers cancelled rows at once.
        started = time.monotonic()
        rows = packages.ensure(entries, should_stop=lambda: True)
        assert [(r["status"], r["error"]) for r in rows] == [("failed", "cancelled")]
        rows = packages.upgrade(entries, should_stop=lambda: True)
        assert [(r["status"], r["error"]) for r in rows] == [("failed", "cancelled")]
        assert time.monotonic() - started < 2
    finally:
        release.set()
        holder.join(5)


def test_upgrade_never_installs_what_isnt_installed(home, monkeypatch):
    """§19: "an entry that isn't installed is never installed by update — it
    answers `status: failed`, `error: "not installed"`"."""
    from autowright import packages

    _fake_dist(home, "requests", "2.31.0")
    calls = []

    def fake_pip(name, pin_installed=False, should_stop=None):
        calls.append(name)
        return None

    monkeypatch.setattr(packages, "_pip_install", fake_pip)
    out = packages.upgrade([{"pip": "leftpad", "import": "leftpad"},
                            {"pip": "requests", "import": "requests"}])
    assert calls == ["requests"]  # nothing was fetched for the absent one
    assert out[0] == {"pip": "leftpad", "import": "leftpad",
                      "status": "failed", "error": "not installed"}
    assert out[1]["status"] == "installed"


def test_upgrade_honors_should_stop_between_packages(home, monkeypatch):
    """§6.2: the same cancel hook `ensure` carries — a §7 cancel must not wait
    out the remaining pip runs."""
    from autowright import packages

    _fake_dist(home, "requests", "2.31.0")
    _fake_dist(home, "leftpad", "1.0.0")
    calls = []
    monkeypatch.setattr(packages, "_pip_install",
                        lambda name, pin_installed=False, should_stop=None:
                        calls.append(name) or None)

    out = packages.upgrade([{"pip": "requests", "import": "requests"},
                            {"pip": "leftpad", "import": "leftpad"}],
                           should_stop=lambda: len(calls) >= 1)
    assert calls == ["requests"]
    assert out[1] == {"pip": "leftpad", "import": "leftpad",
                      "status": "failed", "error": "cancelled"}


# ---------- §6.2: the interpreter's wheel tags are built once per process ----------

def test_the_supported_wheel_tags_are_computed_once(monkeypatch):
    """§6.2: `sys_tags()` builds the platform's whole tag matrix and can't
    change within a process — the §19 outdated sweep pays for it once, not
    once per package."""
    from autowright import packages

    packages._supported_tags.cache_clear()
    try:
        assert packages._supported_tags() is packages._supported_tags()
        assert packages._supported_tags.cache_info().misses == 1
    finally:
        packages._supported_tags.cache_clear()
