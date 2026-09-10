"""launchd LaunchAgent management (§3): install/status/uninstall.

launchctl is never touched — every subprocess.run is a recorded fake, and
plist_path is redirected into the per-test home.
"""
import json
import os
import plistlib
import sys
from types import SimpleNamespace

import pytest

# §3: service.py IS the launchd implementation — these pin its internals
# (plist bytes, launchctl verbs, gui/<uid> domain). The Windows and Linux
# ServiceManagers are their own modules (platform/windows.py Task Scheduler,
# platform/linux.py systemd); their tests live in tests/test_platform.py and
# run on every host.
launchd_only = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="launchd implementation; the per-OS managers are platform/windows.py "
           "and platform/linux.py, tested host-independently in test_platform.py")


def _degraded_platform_service():
    """§3: whether this host composes the placeholder service manager (every
    verb answers "<verb> failed: not supported on <OS> yet", exit 1) — read
    off the §2 platform layer, never sniffed. The `main()` dispatch tests
    below assert that real behavior instead of skipping."""
    from autowright import paths, platform as platmod
    from autowright.platform import fallback

    degraded = isinstance(platmod.current().service, fallback.UnsupportedService)
    why = f"not supported on {paths.os_display_name(paths.current_os())} yet"
    return degraded, why


def _windows_platform_service():
    """§3: whether this host composes the Task Scheduler manager — the
    dispatch tests then fake PowerShell instead of launchctl."""
    from autowright import platform as platmod
    from autowright.platform import windows

    return isinstance(platmod.current().service, windows.WindowsService)


def _linux_platform_service():
    """§3: whether this host composes the systemd manager — the dispatch
    tests then fake systemctl instead of launchctl."""
    from autowright import platform as platmod
    from autowright.platform import linux

    return isinstance(platmod.current().service, linux.SystemdService)


_degraded_service, _degraded_why = _degraded_platform_service()
_windows_service = _windows_platform_service()
_linux_service = _linux_platform_service()


@pytest.fixture()
def svc(home, monkeypatch):
    """service module with plist_path in tmp home and subprocess.run recorded."""
    from autowright import service

    plist = home / "LaunchAgents" / f"{service.LABEL}.plist"
    monkeypatch.setattr(service, "plist_path", lambda: plist)
    # The single §3 shim location, redirected into the per-test home.
    shim = home / "bin" / "autowright"
    monkeypatch.setattr(service, "shim_paths", lambda: [shim])

    calls = []
    results = {}  # verb ("bootstrap"/"bootout"/"load"/"unload"/"list") → canned result
    registered = {"job": False}  # launchd's view, driven by the verbs below

    def fake_run(cmd, **kw):
        calls.append(list(cmd))
        assert cmd[0] == "launchctl"
        verb = cmd[1]
        # `print` answers from the modeled launchd state — the §3 unload poll
        # and load verification depend on it flipping with the other verbs.
        if verb == "print":
            return SimpleNamespace(returncode=0 if registered["job"] else 113,
                                   stdout="", stderr="")
        r = results.get(verb, SimpleNamespace(returncode=0, stdout="", stderr=""))
        if r.returncode == 0:
            if verb in ("bootout", "unload"):
                registered["job"] = False
            elif verb in ("bootstrap", "load"):
                registered["job"] = True
        return r

    monkeypatch.setattr(service.subprocess, "run", fake_run)

    def actions():
        """Recorded calls minus the `print` state probes."""
        return [c for c in calls if c[1] != "print"]

    return SimpleNamespace(mod=service, plist=plist,
                           shim=shim,
                           calls=calls, results=results, actions=actions,
                           registered=registered)


@pytest.fixture()
def dispatch(svc, request):
    """The host's real ServiceManager with its OS layer faked: launchctl
    through `svc` on macOS, PowerShell through `task_scheduler` on Windows,
    systemctl through `systemd` on Linux. `mark_installed()` puts that
    manager in its registered state, whichever it is — so the §3 dispatch
    assertions read the same on every host."""
    tasks = request.getfixturevalue("task_scheduler") if _windows_service else None
    systemd = request.getfixturevalue("systemd") if _linux_service else None

    def mark_installed():
        if tasks is not None:
            tasks.task["state"] = "Ready"  # registered, not running
            return
        if systemd is not None:
            unit = systemd.mod.unit_path()
            unit.parent.mkdir(parents=True, exist_ok=True)
            unit.write_text(systemd.mod.unit_text())  # enabled, not running
            return
        svc.plist.parent.mkdir(parents=True, exist_ok=True)
        svc.plist.write_bytes(b"<plist/>")

    svc.tasks = tasks
    svc.systemd = systemd
    svc.mark_installed = mark_installed
    return svc


def _gui_domain():
    return f"gui/{os.getuid()}"


# ---------------------------------------------------------------- install

@launchd_only
def test_install_writes_plist_and_reloads(svc):
    out = svc.mod.install()
    assert str(svc.plist) in out and out.startswith("installed and started")

    with open(svc.plist, "rb") as f:
        plist = plistlib.load(f)
    assert plist["Label"] == "ai.autowright.backend"
    assert plist["ProgramArguments"] == [sys.executable, "-m", "autowright.main"]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True

    # stop-then-start, in that order: modern bootout/bootstrap against the
    # per-user gui domain (the legacy verbs are fallbacks, not tried when the
    # modern ones succeed).
    assert svc.actions() == [
        ["launchctl", "bootout", f"{_gui_domain()}/{svc.mod.LABEL}"],
        ["launchctl", "bootstrap", _gui_domain(), str(svc.plist)],
    ]


@launchd_only
def test_wedged_launchctl_times_out_into_a_plain_failure(svc, monkeypatch):
    """§3: every launchctl call is time-boxed — the app's ensure-backend step
    waits on this, so a wedged launchctl must report an ordinary failure line
    (exit 1) rather than hanging or raising TimeoutExpired at the caller."""
    import subprocess

    def wedged(cmd, **kw):
        assert kw["timeout"] == svc.mod.LAUNCHCTL_TIMEOUT_S  # bounded, every call
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(svc.mod.subprocess, "run", wedged)
    out = svc.mod.install()
    assert out == "install failed: launchctl timed out"
    assert svc.mod.result_code(out) == 1
    # and the read-only actions degrade instead of raising
    assert svc.mod.status() == "stopped (plist present) — returns at next login or app launch"
    assert svc.mod.restart() == "restart failed: launchctl timed out"


# ---------------------------------------------------------------- CLI shim

@launchd_only
def test_install_never_creates_shim(svc):
    # §3: creation is the Electron shell's explicit privileged flow —
    # `service install` only heals. No shim → a manual-invocation note, and
    # registration itself still succeeds.
    out = svc.mod.install()
    assert out.startswith("installed and started")
    assert "CLI not installed" in out
    assert f"{sys.executable} -m autowright.cli" in out
    assert not svc.shim.exists()


@launchd_only
def test_install_heals_existing_shim(svc):
    # A moved bundle or dev↔prod switch heals through re-install (§3): our
    # marker + user-writable → rewritten in place onto this interpreter.
    svc.shim.parent.mkdir(parents=True)
    svc.shim.write_text(f"#!/bin/sh\n{svc.mod.SHIM_MARKER}\n"
                        f'exec "/old/gone/python3" -m autowright.cli "$@"\n')
    out = svc.mod.install()
    assert f"CLI at {svc.shim}" in out
    text = svc.shim.read_text()
    assert text == svc.mod.shim_text()
    assert f'exec "{sys.executable}" -m autowright.cli "$@"' in text
    assert svc.shim.stat().st_mode & 0o111  # executable


@launchd_only
def test_install_reports_current_shim(svc):
    svc.shim.parent.mkdir(parents=True)
    svc.shim.write_text(svc.mod.shim_text())
    assert f"CLI at {svc.shim}" in svc.mod.install()


@launchd_only
def test_install_reports_unwritable_shim(svc):
    # Ours, wrong interpreter, not writable: reported with the module-form
    # fallback, never fatal (§3).
    svc.shim.parent.mkdir(parents=True)
    svc.shim.write_text(f"#!/bin/sh\n{svc.mod.SHIM_MARKER}\n"
                        f'exec "/old/gone/python3" -m autowright.cli "$@"\n')
    svc.shim.chmod(0o555)
    svc.shim.parent.chmod(0o555)
    try:
        out = svc.mod.install()
    finally:
        svc.shim.parent.chmod(0o755)
        svc.shim.chmod(0o755)
    assert out.startswith("installed and started")
    assert f"CLI shim at {svc.shim} not rewritable" in out
    assert f"{sys.executable} -m autowright.cli" in out


@launchd_only
def test_install_leaves_foreign_shim_alone(svc):
    svc.shim.parent.mkdir(parents=True)
    svc.shim.write_text("#!/bin/sh\necho someone else's autowright\n")
    out = svc.mod.install()
    assert f"foreign {svc.shim} left alone" in out
    assert "someone else" in svc.shim.read_text()


@launchd_only
def test_install_leaves_undecodable_foreign_file_alone(svc):
    # §3: a non-UTF-8 file named autowright (some other tool's compiled
    # binary) can't carry the marker; install leaves it alone and still
    # succeeds, never a traceback (same tolerance as uninstall).
    payload = b"\x00\x80\xff not utf-8"
    svc.shim.parent.mkdir(parents=True)
    svc.shim.write_bytes(payload)
    out = svc.mod.install()
    assert out.startswith("installed and started")
    assert svc.shim.read_bytes() == payload


def test_shim_paths_env_knob(monkeypatch, home):
    # AUTOWRIGHT_SHIM (§15) overrides the location — tests and dev never
    # touch the real ~/.local/bin. Uses the real shim_paths (the svc fixture
    # replaces it, so no fixture here).
    from pathlib import Path

    from autowright import service

    monkeypatch.setenv("AUTOWRIGHT_SHIM", str(home / "elsewhere" / "aw"))
    assert service.shim_paths() == [home / "elsewhere" / "aw"]
    monkeypatch.delenv("AUTOWRIGHT_SHIM")
    assert service.shim_paths() == [Path.home() / ".local" / "bin" / "autowright"]


@launchd_only
def test_install_heals_shim(svc):
    # Ours, pointing at another interpreter → rewritten in place (§3).
    svc.shim.parent.mkdir(parents=True)
    svc.shim.write_text(f"#!/bin/sh\n{svc.mod.SHIM_MARKER}\n"
                        f'exec "/old/gone/python3" -m autowright.cli "$@"\n')
    out = svc.mod.install()
    assert f"CLI at {svc.shim}" in out
    assert svc.shim.read_text() == svc.mod.shim_text()


@launchd_only
def test_uninstall_removes_own_shim(svc):
    svc.shim.parent.mkdir(parents=True)
    svc.shim.write_text(svc.mod.shim_text())
    svc.mod.uninstall()
    assert not svc.shim.exists()


@launchd_only
def test_uninstall_reports_undeletable_shim(svc):
    svc.shim.parent.mkdir(parents=True)
    svc.shim.write_text(svc.mod.shim_text())
    svc.shim.parent.chmod(0o555)  # deletion needs a directory write
    try:
        out = svc.mod.uninstall()
    finally:
        svc.shim.parent.chmod(0o755)
    assert f"CLI shim left at {svc.shim}" in out and "couldn't delete" in out
    assert svc.shim.exists()


@launchd_only
def test_uninstall_leaves_foreign_shim_alone(svc):
    # No marker → not ours → never touched (§3).
    svc.shim.parent.mkdir(parents=True)
    svc.shim.write_text("#!/bin/sh\necho someone else's autowright\n")
    svc.mod.uninstall()
    assert svc.shim.exists()


# ------------------------------------------- `python -m autowright.service`

def test_main_dispatches_and_prints(dispatch, capsys):
    # §3: main() dispatches through platform.current().service, so what it
    # prints is the platform's own result line — launchd's on macOS, Task
    # Scheduler's on Windows (same head grammar), the degraded "not supported"
    # line (exit 1) where no service manager exists.
    if _degraded_service:
        assert dispatch.mod.main(["install"]) == 1
        assert f"install failed: {_degraded_why}" in capsys.readouterr().out
        return
    assert dispatch.mod.main(["install"]) == 0
    assert "installed and started" in capsys.readouterr().out


def test_main_rejects_bad_usage(svc, capsys):
    assert svc.mod.main([]) == 2
    assert svc.mod.main(["frobnicate"]) == 2
    assert "usage:" in capsys.readouterr().err


@launchd_only
def test_main_exit_code_on_failure(svc, capsys):
    svc.results["bootstrap"] = SimpleNamespace(returncode=5, stdout="",
                                               stderr="Bootstrap failed: 5\n")
    svc.results["load"] = SimpleNamespace(returncode=1, stdout="",
                                          stderr="Load failed: 5\n")
    assert svc.mod.main(["install"]) == 1


@launchd_only
def test_result_code_from_real_action_output(svc, capsys):
    # §3/§20: result_code is the one exit-code rule, shared by main() and the
    # CLI `service` wrapper; probe it against real action output.
    assert svc.mod.result_code(svc.mod.stop()) == 0       # no plist: idempotent already-stopped
    assert svc.mod.result_code(svc.mod.install()) == 0
    assert svc.mod.result_code(svc.mod.status()) == 0     # plist present: stopped, not a failure


@launchd_only
def test_install_falls_back_to_legacy_load(svc):
    # A box where the modern verbs fail (or an old macOS): legacy unload/load
    # still bootstraps the service.
    svc.results["bootout"] = SimpleNamespace(returncode=1, stdout="", stderr="")
    svc.results["bootstrap"] = SimpleNamespace(returncode=5, stdout="",
                                               stderr="Bootstrap failed: 5\n")
    out = svc.mod.install()
    assert out.startswith("installed and started")
    assert ["launchctl", "unload", str(svc.plist)] in svc.calls
    assert ["launchctl", "load", str(svc.plist)] in svc.calls


@launchd_only
def test_install_reports_failed_load(svc):
    svc.results["bootstrap"] = SimpleNamespace(returncode=5, stdout="",
                                               stderr="Bootstrap failed: 5\n")
    svc.results["load"] = SimpleNamespace(returncode=1, stdout="",
                                          stderr="Load failed: 5\n")
    out = svc.mod.install()
    assert out == "install failed: Bootstrap failed: 5"
    assert svc.plist.exists()  # plist written before the load attempt


# ---------------------------------------------------------------- status

def _list_result(stdout):
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


@launchd_only
def test_status_parses_active_pid_and_port(svc, home):
    svc.results["list"] = _list_result(
        "PID\tStatus\tLabel\n"
        "77\t0\tcom.apple.other\n"
        "1234\t0\tai.autowright.backend\n")
    from autowright import paths

    paths.backend_json().write_text(json.dumps({"port": 5151, "token": "t"}))
    assert svc.mod.status() == "active (pid 1234) · port 5151"


@launchd_only
def test_status_loaded_not_active(svc):
    svc.results["list"] = _list_result("-\t0\tai.autowright.backend\n")
    assert svc.mod.status() == "loaded, not active (pid -)"


@launchd_only
def test_status_not_installed(svc):
    svc.results["list"] = _list_result("1\t0\tcom.apple.other\n")
    assert svc.mod.status() == "not installed"


def test_status_stopped_with_plist_present(dispatch, capsys):
    # §3: a registered service that isn't running = stopped on purpose
    # (`service stop`), not "not installed" — and not a failure (exit 0).
    # Registered means plist-on-disk under launchd, a registered task under
    # Task Scheduler; both answer "stopped (…)".
    dispatch.results["list"] = _list_result("1\t0\tcom.apple.other\n")
    dispatch.mark_installed()
    if _degraded_service:
        # No service manager to be stopped: main() reports the platform's
        # degraded status line and exits 1 (§3 result-code rule, unchanged).
        assert dispatch.mod.main(["status"]) == 1
        assert f"status failed: {_degraded_why}" in capsys.readouterr().out
        return
    assert dispatch.mod.main(["status"]) == 0
    assert capsys.readouterr().out.startswith("stopped (")


@launchd_only
def test_status_not_installed_exits_nonzero(svc, capsys):
    svc.results["list"] = _list_result("1\t0\tcom.apple.other\n")
    assert svc.mod.main(["status"]) == 1
    capsys.readouterr()


@launchd_only
def test_status_tolerates_stale_or_garbage_backend_json(svc):
    svc.results["list"] = _list_result("42\t0\tai.autowright.backend\n")
    from autowright import paths

    bj = paths.backend_json()
    for garbage in ('{"port": 51', "not json at all", json.dumps({"token": "t"}),
                    ""):
        bj.write_text(garbage)
        assert svc.mod.status() == "active (pid 42) · stale backend.json"


# ---------------------------------------------------------------- uninstall

@launchd_only
def test_uninstall_removes_plist_and_unloads(svc):
    svc.plist.parent.mkdir(parents=True, exist_ok=True)
    svc.plist.write_bytes(b"<plist/>")
    assert svc.mod.uninstall() == "service unloaded and removed"
    assert not svc.plist.exists()
    assert svc.actions() == [["launchctl", "bootout", f"{_gui_domain()}/{svc.mod.LABEL}"]]


@launchd_only
def test_uninstall_when_not_installed(svc):
    assert svc.mod.uninstall() == "service was not installed"
    # the stop is still attempted (harmless), plist untouched
    assert svc.actions() == [["launchctl", "bootout", f"{_gui_domain()}/{svc.mod.LABEL}"]]


# ---------------------------------------------------------------- stop

@launchd_only
def test_stop_unloads_but_keeps_plist_and_shim(svc):
    # §3 quit-entirely backend half: bootout only — plist and shim survive,
    # the service returns at next login or app launch.
    svc.plist.parent.mkdir(parents=True, exist_ok=True)
    svc.plist.write_bytes(b"<plist/>")
    svc.shim.parent.mkdir(parents=True)
    svc.shim.write_text(svc.mod.shim_text())
    out = svc.mod.stop()
    assert out.startswith("stopped")
    assert svc.plist.exists()
    assert svc.shim.exists()
    assert svc.actions() == [["launchctl", "bootout", f"{_gui_domain()}/{svc.mod.LABEL}"]]


@launchd_only
def test_stop_when_not_installed(svc, capsys, no_kill_matching):
    """§3: stop is idempotent — no registration and nothing running already
    satisfies it (exit 0), so quit-all/reset proceed on an unregistered
    machine instead of aborting."""
    from autowright import paths

    out = svc.mod.stop()
    assert out == "already stopped — service not installed, nothing was running"
    assert svc.mod.result_code(out) == 0
    assert svc.actions() == []  # nothing to unload
    # The §3 sweep still ran — with the §15 stub it simply found nothing.
    assert no_kill_matching[0] == paths.sweep_markers()
    assert svc.mod.main(["stop"]) == 0
    capsys.readouterr()


@launchd_only
def test_stop_without_a_plist_reports_the_strays_it_ended(svc, monkeypatch):
    """§3: no registration, but a directly-spawned backend (isolated dev) or
    strays can still be alive — ending them is a successful stop."""
    monkeypatch.setattr(svc.mod, "_sweep_strays", lambda: 3)
    out = svc.mod.stop()
    assert out == "stopped — service was not installed; ended 3 lingering process(es)"
    assert svc.mod.result_code(out) == 0
    assert svc.actions() == []  # the sweep is the whole stop


@launchd_only
def test_stop_sweeps_strays_after_the_bootout(svc, monkeypatch):
    """§3 quit-entirely: the sweep runs once launchd has been told to stop the
    job (so KeepAlive can't respawn what it kills), and its count rides on the
    result line as an informational note after the "·"."""
    svc.plist.parent.mkdir(parents=True, exist_ok=True)
    svc.plist.write_bytes(b"<plist/>")
    seen = []

    def sweep():
        seen.append(svc.actions())
        return 2

    monkeypatch.setattr(svc.mod, "_sweep_strays", sweep)
    out = svc.mod.stop()
    assert out == ("stopped — returns at next login or app launch · "
                   "ended 2 lingering process(es)")
    assert svc.mod.result_code(out) == 0
    assert svc.plist.exists()  # plist survives, as always
    # Order: the bootout had already run when the sweep fired.
    assert seen == [[["launchctl", "bootout", f"{_gui_domain()}/{svc.mod.LABEL}"]]]


@launchd_only
def test_stop_gives_launchd_a_second_window_after_the_sweep(svc, monkeypatch,
                                                              poll_clock):
    """§3: bootout can outlive _unload's own wait — the sweep's kill is what
    lets launchd finish the removal, so stop re-polls before judging."""
    poll_clock(svc.mod)  # no real waits inside the §3 deadline polls
    svc.plist.parent.mkdir(parents=True, exist_ok=True)
    svc.plist.write_bytes(b"<plist/>")
    state = {"swept": False, "polls": 0}

    def sweep():
        state["swept"] = True
        return 1

    def registered():
        if not state["swept"]:
            return True  # still there through _unload's whole wait
        state["polls"] += 1
        return state["polls"] < 3  # gone on the third poll after the sweep

    monkeypatch.setattr(svc.mod, "_sweep_strays", sweep)
    monkeypatch.setattr(svc.mod, "_registered", registered)
    out = svc.mod.stop()
    assert out == ("stopped — returns at next login or app launch · "
                   "ended 1 lingering process(es)")
    assert svc.mod.result_code(out) == 0
    assert state["polls"] > 1  # the second window was actually used


@launchd_only
def test_stop_fails_when_the_job_outlives_the_sweep(svc, monkeypatch, poll_clock):
    """A job launchd still reports after the sweep is still a failed stop
    (exit 1) — the app must not quit its UI on top of a live backend."""
    poll_clock(svc.mod)  # no real waits inside the §3 deadline polls
    monkeypatch.setattr(svc.mod, "_sweep_strays", lambda: 1)
    monkeypatch.setattr(svc.mod, "_registered", lambda: True)
    svc.plist.parent.mkdir(parents=True, exist_ok=True)
    svc.plist.write_bytes(b"<plist/>")
    out = svc.mod.stop()
    assert out == "stop failed: launchd still reports the job"
    assert svc.mod.result_code(out) == 1


@launchd_only
def test_unload_wait_is_bounded_by_a_wall_clock_deadline(svc, monkeypatch,
                                                         poll_clock):
    """§3: the bootout wait is a 10 s wall-clock deadline, never a poll count —
    a `launchctl print` that takes seconds to answer must not stretch the wait
    into minutes. The deadline is checked after every probe, so a slow probe
    ends the loop instead of extending it."""
    clock = poll_clock(svc.mod)
    probes = []

    def slow_registered():
        probes.append(clock.now)
        clock.sleep(3)  # the probe itself blocks for 3 s
        return True     # the job never goes away

    monkeypatch.setattr(svc.mod, "_registered", slow_registered)
    svc.plist.parent.mkdir(parents=True, exist_ok=True)
    svc.plist.write_bytes(b"<plist/>")
    svc.mod._unload(svc.plist)
    # Every probe started inside the deadline, and the wait ended once one
    # overran it — never 40 probes of 3 s.
    assert probes[-1] < svc.mod._UNLOAD_DEADLINE_S
    assert clock.now >= svc.mod._UNLOAD_DEADLINE_S
    assert 1 < len(probes) <= 5


@launchd_only
def test_stops_second_window_is_bounded_by_a_wall_clock_deadline(svc, monkeypatch,
                                                                 poll_clock):
    """§3: the window after the sweep is 5 s of wall clock — the same rule as
    the bootout wait, half the budget — and a still-registered job is still a
    failed stop."""
    clock = poll_clock(svc.mod)
    state = {"swept": False}
    probes = []

    def sweep():
        state["swept"] = True
        return 1

    def slow_registered():
        if state["swept"]:
            probes.append(clock.now)
        clock.sleep(3)  # the probe itself blocks for 3 s
        return True     # the job never goes away

    monkeypatch.setattr(svc.mod, "_sweep_strays", sweep)
    monkeypatch.setattr(svc.mod, "_registered", slow_registered)
    svc.plist.parent.mkdir(parents=True, exist_ok=True)
    svc.plist.write_bytes(b"<plist/>")
    out = svc.mod.stop()
    assert out == "stop failed: launchd still reports the job"
    assert svc.mod.result_code(out) == 1
    # probes[0] is the guard that opens the window and probes[-1] the judging
    # probe after it; the window's own probes all started inside its 5 s.
    assert probes[-2] - probes[1] < svc.mod._SWEEP_DEADLINE_S
    assert 1 < len(probes) <= 5


@launchd_only
def test_install_and_restart_never_sweep(svc, monkeypatch):
    """§3: the sweep belongs to stop alone — install and restart bring the
    service back up, and killing our own fresh processes would defeat them."""
    def forbidden():
        raise AssertionError("install/restart must not sweep")

    monkeypatch.setattr(svc.mod, "_sweep_strays", forbidden)
    assert svc.mod.install().startswith("installed and started")
    assert svc.mod.restart() == "restarted"


def test_main_dispatches_stop(dispatch, capsys):
    dispatch.mark_installed()
    if _degraded_service:
        assert dispatch.mod.main(["stop"]) == 1
        assert f"stop failed: {_degraded_why}" in capsys.readouterr().out
        return
    assert dispatch.mod.main(["stop"]) == 0
    assert "stopped" in capsys.readouterr().out


@launchd_only
def test_stop_failed_when_still_registered(svc, monkeypatch, capsys, poll_clock):
    # launchd refuses both bootout and legacy unload and keeps the job:
    # stop must report failure (exit 1) — the app then must not quit (§3).
    poll_clock(svc.mod)  # no real waits inside the §3 deadline polls
    svc.plist.parent.mkdir(parents=True, exist_ok=True)
    svc.plist.write_bytes(b"<plist/>")
    svc.registered["job"] = True
    svc.results["bootout"] = SimpleNamespace(returncode=1, stdout="", stderr="")
    svc.results["unload"] = SimpleNamespace(returncode=1, stdout="", stderr="")
    assert svc.mod.stop() == "stop failed: launchd still reports the job"
    assert svc.mod.main(["stop"]) == 1
    capsys.readouterr()


# ------------------------------------------------- §3 discovery guard (main.py)

def test_republish_rewrites_missing_file(home):
    from autowright import main as backend_main, paths

    payload = json.dumps({"port": 1, "token": "t", "version": "x", "pid": 42})
    paths.backend_json().unlink(missing_ok=True)
    assert backend_main.republish_if_lost(payload) is True
    assert json.loads(paths.backend_json().read_text())["pid"] == 42
    if os.name == "posix":
        # §3: 0600 is the POSIX protection for the bearer token. On Windows
        # mode bits restrict nothing — the file's protection is the
        # %LOCALAPPDATA% profile ACL it is written under.
        assert (paths.backend_json().stat().st_mode & 0o777) == 0o600


def test_republish_recreates_wiped_home(home):
    import shutil

    from autowright import main as backend_main, paths

    shutil.rmtree(home)
    assert backend_main.republish_if_lost(json.dumps({"port": 1})) is True
    assert paths.backend_json().exists()
    assert paths.automations_dir().is_dir()  # ensure_dirs ran first


def test_republish_rewrites_corrupt_file(home):
    from autowright import main as backend_main, paths

    paths.backend_json().write_text('{"port": 12')  # SIGKILL-style truncation
    assert backend_main.republish_if_lost('{"port": 1}') is True
    assert json.loads(paths.backend_json().read_text()) == {"port": 1}


def test_republish_stands_down_once_shutdown_has_started(home):
    """§3: shutdown sets the stop flag and then unlinks backend.json — a guard
    already past its read must not resurrect it. The flag is re-checked (under
    the publish lock) immediately before the write, so a signal-driven stop
    really does leave no file behind."""
    from autowright import main as backend_main, paths

    paths.backend_json().unlink(missing_ok=True)
    assert backend_main.republish_if_lost('{"port": 1}', lambda: True) is False
    assert not paths.backend_json().exists()
    # unflagged, the same call still republishes
    assert backend_main.republish_if_lost('{"port": 1}', lambda: False) is True
    assert paths.backend_json().exists()


def test_republish_leaves_any_valid_file_alone(home):
    from autowright import main as backend_main, paths

    # A valid file holding another pid may be the successor's during a
    # service restart — never clobber it.
    paths.backend_json().write_text(json.dumps({"port": 9, "pid": 999}))
    assert backend_main.republish_if_lost('{"port": 1}') is False
    assert json.loads(paths.backend_json().read_text())["port"] == 9


# ------------------------------------------------- §5 log size cap (main.py)

def test_trim_logs_drops_oldest_lines(home, monkeypatch):
    from autowright import main as backend_main, paths

    monkeypatch.setattr(backend_main, "LOG_CAP", 1000)
    monkeypatch.setattr(backend_main, "LOG_TRIM_TO", 500)
    log = paths.app_log()
    lines = [f"line {i:04d}\n".encode() for i in range(200)]  # 2000 bytes
    log.write_bytes(b"".join(lines))
    backend_main.trim_logs()
    kept = log.read_bytes()
    assert len(kept) < 500  # trimmed to the target, minus the partial first line
    assert kept.startswith(b"line ")  # cut at a line boundary
    assert kept.endswith(b"line 0199\n")  # newest lines survive


def test_trim_logs_leaves_small_and_missing_files_alone(home, monkeypatch):
    from autowright import main as backend_main, paths

    monkeypatch.setattr(backend_main, "LOG_CAP", 1000)
    monkeypatch.setattr(backend_main, "LOG_TRIM_TO", 500)
    log = paths.logs_dir() / "backend.out.log"
    log.write_bytes(b"small\n" * 10)
    backend_main.trim_logs()  # backend.err.log missing — must not raise
    assert log.read_bytes() == b"small\n" * 10


# --------------------------------------- §3 Windows log routing (main.py)

def test_route_logs_points_the_backend_streams_at_the_launchd_filenames(home, monkeypatch):
    """§3: Task Scheduler captures no stdout/stderr, so on Windows the backend
    writes the very files the launchd plist names — the §9.3 overlay and the §5
    docs hold unchanged. Line-buffered, so a crash loses at most a line."""
    from autowright import main as backend_main, paths

    monkeypatch.setattr(paths, "current_os", lambda: "windows")
    monkeypatch.setattr(sys, "stdout", sys.stdout)  # restored at teardown
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    backend_main.route_logs()
    try:
        assert sys.stdout.name == str(paths.logs_dir() / "backend.out.log")
        assert sys.stderr.name == str(paths.logs_dir() / "backend.err.log")
        print("out line")  # noqa: T201 — the point of the test
        print("err line", file=sys.stderr)  # noqa: T201
        # line-buffered: readable before anything is closed
        assert (paths.logs_dir() / "backend.out.log").read_text(encoding="utf-8") \
            == "out line\n"
    finally:
        sys.stdout.close()
        sys.stderr.close()
    assert (paths.logs_dir() / "backend.err.log").read_text(encoding="utf-8") \
        == "err line\n"


def test_route_logs_appends_and_is_a_noop_off_windows(home, monkeypatch):
    from autowright import main as backend_main, paths

    out = paths.logs_dir() / "backend.out.log"
    out.write_text("earlier run\n", encoding="utf-8")
    monkeypatch.setattr(paths, "current_os", lambda: "macos")
    monkeypatch.setattr(sys, "stdout", sys.stdout)
    monkeypatch.setattr(sys, "stderr", sys.stderr)
    before = sys.stdout
    backend_main.route_logs()
    assert sys.stdout is before  # launchd captures the streams itself

    monkeypatch.setattr(paths, "current_os", lambda: "windows")
    backend_main.route_logs()
    try:
        print("later run")  # noqa: T201
    finally:
        sys.stdout.close()
        sys.stderr.close()
    assert out.read_text(encoding="utf-8") == "earlier run\nlater run\n"


def test_main_trims_logs_before_routing_them(home, monkeypatch):
    """Order matters (§3): a writer holding backend.out/err.log open across the
    startup trim turns that trim into a Windows sharing violation."""
    from autowright import main as backend_main

    class Stop(Exception):
        pass

    order = []
    monkeypatch.setattr(backend_main, "trim_logs", lambda: order.append("trim"))

    def route():
        order.append("route")
        raise Stop  # abort main() before it binds a socket

    monkeypatch.setattr(backend_main, "route_logs", route)
    with pytest.raises(Stop):
        backend_main.main()
    assert order == ["trim", "route"]


# ------------------------------------------------- §2 CLI leaf invariant

def test_no_backend_module_imports_the_cli():
    """§2: the CLI is a pure leaf — the UI and the backend must never depend on
    or invoke it. Pin the import direction: no module in the autowright package
    besides cli.py itself may import autowright.cli. (service.py's shim_text
    mentions `-m autowright.cli` as the shim file's *contents* — a string, not
    an import — which this scan correctly ignores.)"""
    import ast
    from pathlib import Path

    import autowright

    pkg = Path(autowright.__file__).parent
    offenders = []
    for py in pkg.rglob("*.py"):
        if py.name == "cli.py":
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(a.name == "autowright.cli" or a.name.startswith("autowright.cli.")
                       for a in node.names):
                    offenders.append(f"{py.name}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if (mod == "autowright.cli" or mod.startswith("autowright.cli.")
                        or (node.level >= 1 and (mod == "cli" or mod.startswith("cli.")))
                        or (node.level >= 1 and mod == ""
                            and any(a.name == "cli" for a in node.names))):
                    offenders.append(f"{py.name}:{node.lineno}")
    assert not offenders, f"backend modules import the CLI (§2 leaf invariant): {offenders}"
