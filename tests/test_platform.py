"""§2 platform layer: composition, capability flags, degraded fallbacks, and
the §5 per-OS root table (backend half of the drift guard — the Electron half
is app/tests/platform-roots.test.ts; both pin the same spec table). The §9
per-OS copy table is guarded the same way: this file holds the backend half
(paths.machine_noun / secret_store_name), app/tests/platform-copy-table.test.ts
the renderer one."""
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from autowright import paths, platform, service
from autowright.platform import darwin, fallback, linux, posixproc, windows

# The §15 `no_kill_matching` autouse fixture (conftest.py) swaps both sweep
# bodies for a recorded no-op, so no test ever sweeps the developer's own
# machine. These bindings are taken at collection — before any fixture runs —
# so the dedicated sweep tests below can drive the REAL bodies against faked
# process tables.
REAL_POSIX_SWEEP = posixproc.PosixProcessControl.kill_matching
REAL_WINDOWS_SWEEP = windows.WindowsProcessControl.kill_matching

# The §2 ProcessControl protocol surface. Every build composes all of it — a
# platform missing one method breaks a call site only at runtime, so the
# composition tests below check the whole set (the §3 quit-entirely sweep's
# `kill_matching` included).
PROCESS_CONTROL = ("session_kwargs", "signal_group", "kill_group",
                   "group_has_command", "kill_matching")


def _composes_process_control(plat) -> bool:
    return all(callable(getattr(plat.processes, name, None)) for name in PROCESS_CONTROL)


# ---------------------------------------------------------------- composition

def test_darwin_build_composes_full_capabilities():
    """darwin.build() composes on any host (the module imports everywhere), so
    the macOS table is pinned wherever the suite runs."""
    plat = darwin.build()
    assert plat.os_token == "macos"
    assert plat.capabilities.as_dict() == {
        "imessage": True, "notifications": True, "keepAwake": True, "service": True,
        "agentInstall": True}
    assert isinstance(plat.service, darwin.LaunchdService)
    assert isinstance(plat.notifier, darwin.OsascriptNotifier)
    assert isinstance(plat.power, darwin.CaffeinatePower)
    assert isinstance(plat.processes, posixproc.PosixProcessControl)
    assert _composes_process_control(plat)


# What each §2 build composes, keyed by §5.1 platform token — the one table the
# host-dependent assertions below read, so they state a per-OS truth instead of
# assuming the suite runs on a Mac.
#
# Windows `notifications` is the one entry that is not a constant: §3 makes it a
# *probe* (a toast needs the AUMID a §3 NSIS install registers), so the table
# states the rule — "whatever the probe answers on this machine" — and the
# probe's own behavior is pinned by the dedicated tests below and in
# tests/test_notify.py. A dev machine without the installed app answers False.
EXPECTED_CAPABILITIES = {
    "macos": {"imessage": True, "notifications": True, "keepAwake": True,
              "service": True, "agentInstall": True},
    "windows": {"imessage": False, "notifications": windows._aumid_registered(),
                "keepAwake": True, "service": True, "agentInstall": False},
    # Linux probes three of its flags at composition (§2): each is the
    # presence of the OS tool on this host's PATH, so the table states the
    # rule; the dedicated linux.build() tests below pin both probe outcomes.
    "linux": {"imessage": False,
              "notifications": shutil.which("notify-send") is not None,
              "keepAwake": shutil.which("systemd-inhibit") is not None,
              "service": shutil.which("systemctl") is not None,
              "agentInstall": True},
}


def test_current_composes_this_hosts_platform():
    """§2: `current()` picks the build for the host it runs on, and its
    capability table is that build's."""
    plat = platform.current()
    assert plat.os_token == paths.current_os()
    assert plat.capabilities.as_dict() == EXPECTED_CAPABILITIES[plat.os_token]


def test_fallback_build_flags_everything_off():
    plat = fallback.build("linux", "Linux")
    assert plat.os_token == "linux"
    assert plat.capabilities.as_dict() == {
        "imessage": False, "notifications": False, "keepAwake": False, "service": False,
        "agentInstall": False}
    # The notifier and both power assertions are silent no-ops, never raise.
    plat.notifier.post("t", "b")
    plat.power.reconcile(True)
    plat.power.reconcile(False)
    release = plat.power.hold_execution()
    release()
    release()  # double release is harmless
    # Process control is real even in a degraded build (Linux runs the POSIX one).
    assert isinstance(plat.processes, posixproc.PosixProcessControl)
    assert _composes_process_control(plat)


def test_windows_build_composes_real_service_process_control_and_power():
    """§2/§3: every Windows implementation is real now — the Task Scheduler
    service manager, tree-kill process control, the keepAwake assertion and the
    WinRT toast notifier."""
    plat = windows.build("Windows")
    assert plat.os_token == "windows"
    assert plat.capabilities.as_dict() == EXPECTED_CAPABILITIES["windows"]
    assert isinstance(plat.service, windows.WindowsService)
    assert isinstance(plat.processes, windows.WindowsProcessControl)
    assert isinstance(plat.power, windows.WindowsPower)
    assert isinstance(plat.notifier, windows.WindowsNotifier)
    assert _composes_process_control(plat)


def test_windows_notifications_capability_is_probed_not_assumed(monkeypatch):
    """§3: `notifications` is true only where the AUMID is registered — a
    packaged install's Start-menu shortcut. The notifier itself is composed
    either way (posting where the id is unknown is harmless, and silent)."""
    monkeypatch.setattr(windows, "_aumid_registered", lambda: True)
    plat = windows.build("Windows")
    assert plat.capabilities.notifications is True
    assert isinstance(plat.notifier, windows.WindowsNotifier)

    monkeypatch.setattr(windows, "_aumid_registered", lambda: False)
    plat = windows.build("Windows")
    assert plat.capabilities.notifications is False
    assert isinstance(plat.notifier, windows.WindowsNotifier)


def test_current_routes_windows_token_to_groundwork_build(monkeypatch):
    monkeypatch.setattr(paths, "current_os", lambda: "windows")
    platform.current.cache_clear()
    try:
        assert isinstance(platform.current().processes, windows.WindowsProcessControl)
    finally:
        platform.current.cache_clear()


def test_windows_session_kwargs_are_a_new_process_group_with_no_window():
    kwargs = windows.WindowsProcessControl().session_kwargs()
    # The Win32 CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW flags (§2 spawn
    # policy: a console child of the pythonw backend must not open a terminal
    # window); no POSIX-only Popen kwargs.
    assert kwargs == {"creationflags": 0x00000200 | 0x08000000}


def test_console_python_swaps_pythonw_for_its_console_sibling(monkeypatch, tmp_path):
    """§2 console-interpreter rule: under the §3 pythonw.exe service, Python
    children (executor, pip) spawn via the python.exe sibling so their own
    console children inherit the hidden console instead of opening terminal
    windows. No sibling, or any other interpreter name/OS: sys.executable
    unchanged."""
    pythonw = tmp_path / "pythonw.exe"
    pythonw.touch()
    monkeypatch.setattr(paths.sys, "executable", str(pythonw))
    monkeypatch.setattr(paths, "current_os", lambda: "windows")
    assert paths.console_python() == str(pythonw)  # no sibling yet
    console = tmp_path / "python.exe"
    console.touch()
    assert paths.console_python() == str(console)

    monkeypatch.setattr(paths, "current_os", lambda: "macos")
    assert paths.console_python() == str(pythonw)  # rule is Windows-only

    monkeypatch.setattr(paths, "current_os", lambda: "windows")
    monkeypatch.setattr(paths.sys, "executable", str(console))
    assert paths.console_python() == str(console)  # already the console one


def test_sweep_markers_cover_module_form_interpreter_and_entry_point(monkeypatch, tmp_path):
    """§3 quit-entirely: what the sweep matches command lines against — the
    module form (it survives `ps`'s interpreter-path resolution), this
    interpreter, and the `bin/autowright*` entry-point scripts beside it."""
    exe = tmp_path / "bin" / "python3.13"
    monkeypatch.setattr(paths.sys, "executable", str(exe))
    monkeypatch.setattr(paths, "console_python", lambda: str(exe))
    assert paths.sweep_markers() == [
        "-m autowright.", str(exe), str(tmp_path / "bin" / "autowright")]

    # The Windows pythonw/python pair: the console sibling runs our children,
    # so its own path is a marker too — appended only when it differs.
    console = tmp_path / "bin" / "python.exe"
    monkeypatch.setattr(paths, "console_python", lambda: str(console))
    assert paths.sweep_markers() == [
        "-m autowright.", str(exe), str(tmp_path / "bin" / "autowright"), str(console)]


# ------------------------------------------------- §3 quit-entirely sweep
#
# No process is ever signalled here: the POSIX tests drive the real
# kill_matching body (REAL_POSIX_SWEEP, bound at collection) against a scripted
# `ps` table with os.killpg/os.kill recorded, and the Windows test drives the
# real body with _powershell and the tree kill recorded.

def _sweep(markers, grace_s=2.0) -> int:
    return REAL_POSIX_SWEEP(posixproc.PosixProcessControl(), markers, grace_s)


@pytest.fixture()
def ps_table(monkeypatch):
    """A scripted process table plus recorded signals. Each `ps` call answers
    the next snapshot (the last one repeats), and the grace clock only advances
    when the body sleeps — so nothing here waits on wall time."""
    state = SimpleNamespace(snapshots=[[]], signals=[], reads=0, now=0.0)

    def fake_ps(argv, **kw):
        assert argv[:2] == ["ps", "-axo"]
        state.reads += 1
        rows = state.snapshots[min(state.reads - 1, len(state.snapshots) - 1)]
        return SimpleNamespace(
            stdout="".join(f"{pid} {pgid} {cmd}\n" for pid, pgid, cmd in rows))

    monkeypatch.setattr(posixproc.subprocess, "run", fake_ps)
    monkeypatch.setattr(posixproc.os, "killpg",
                        lambda pgid, sig: state.signals.append(("killpg", pgid, sig)))
    monkeypatch.setattr(posixproc.os, "kill",
                        lambda pid, sig: state.signals.append(("kill", pid, sig)))
    monkeypatch.setattr(posixproc.time, "monotonic", lambda: state.now)
    monkeypatch.setattr(posixproc.time, "sleep",
                        lambda s: setattr(state, "now", state.now + s))
    return state


def test_posix_sweep_terminates_the_group_then_kills_the_survivor(ps_table):
    """§3: TERM per group, a grace window, then KILL for whatever is still
    there. The count is what matched, not what needed the second signal."""
    backend = "/opt/Autowright/python -m autowright.main"
    ps_table.snapshots = [
        [(4001, 4001, backend),
         (4002, 4001, "/opt/Autowright/python -m autowright.executor 7")],
        [(4001, 4001, backend)],  # the executor died on TERM, the backend hung on
    ]
    assert _sweep(["-m autowright."], grace_s=0.3) == 2
    assert ps_table.signals == [("killpg", 4001, signal.SIGTERM),
                                ("killpg", 4001, signal.SIGKILL)]


def test_posix_sweep_counts_every_match_and_signals_each_group_once(ps_table):
    """One TERM per *group*, whatever the marker that matched — and a command
    line carrying no marker is left alone."""
    ps_table.snapshots = [
        [(4301, 4301, "/opt/Autowright/python -m autowright.main"),
         (4302, 4301, "/opt/Autowright/python -m pip install httpx"),
         (4303, 4303, "/opt/Autowright/bin/autowright run daily"),
         (4304, 4304, "/usr/bin/vim autowright-notes.txt")],
        [],
    ]
    markers = ["-m autowright.", "/opt/Autowright/python",
               "/opt/Autowright/bin/autowright"]
    assert _sweep(markers, grace_s=1.0) == 3
    # The group order is a set's — the pairs are what matter.
    assert set(ps_table.signals) == {("killpg", 4301, signal.SIGTERM),
                                     ("killpg", 4303, signal.SIGTERM)}


def test_posix_sweep_never_signals_this_process(ps_table):
    """The sweeper itself runs `-m autowright.service stop` — a marker match."""
    ps_table.snapshots = [
        [(os.getpid(), os.getpgid(0) + 1, "python -m autowright.service stop")]]
    assert _sweep(["-m autowright."]) == 0
    assert ps_table.signals == []


def test_posix_sweep_never_signals_its_own_process_group(ps_table):
    """§3: the Electron caller during quit-all and the shell job in CLI use sit
    in the sweeper's own group and carry a marker too."""
    ps_table.snapshots = [
        [(os.getpid() + 1, os.getpgid(0), "Electron … -m autowright.main")]]
    assert _sweep(["-m autowright."]) == 0
    assert ps_table.signals == []


def test_posix_sweep_leaves_a_process_that_died_on_term(ps_table):
    ps_table.snapshots = [[(4100, 4100, "/opt/Autowright/python -m autowright.main")],
                          []]
    assert _sweep(["-m autowright."], grace_s=1.0) == 1
    assert ps_table.signals == [("killpg", 4100, signal.SIGTERM)]


def test_posix_sweep_spares_a_reused_pid_with_another_command(ps_table):
    """§3 pid-reuse guard: the survivor check is (pid, command) — the same pid
    running somebody else's line by KILL time is not ours to kill."""
    ps_table.snapshots = [
        [(4200, 4200, "/opt/Autowright/python -m autowright.executor 3")],
        [(4200, 4200, "/usr/bin/vim notes.txt")],
    ]
    assert _sweep(["-m autowright."], grace_s=1.0) == 1
    assert ps_table.signals == [("killpg", 4200, signal.SIGTERM)]


def test_posix_sweep_falls_back_to_per_pid_signals_when_the_group_is_gone(ps_table,
                                                                         monkeypatch):
    """A group that is gone (or partly foreign) answers ProcessLookupError —
    the matched pids inside it are then signalled one by one."""
    def refuse(pgid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(posixproc.os, "killpg", refuse)
    ps_table.snapshots = [
        [(4401, 4400, "/opt/Autowright/python -m autowright.main"),
         (4402, 4400, "/opt/Autowright/python -m autowright.executor 1")],
        [],
    ]
    assert _sweep(["-m autowright."], grace_s=1.0) == 2
    assert ps_table.signals == [("kill", 4401, signal.SIGTERM),
                                ("kill", 4402, signal.SIGTERM)]


def test_posix_sweep_signals_nothing_when_the_table_is_unreadable(ps_table, monkeypatch):
    """§3: never kill what can't be verified — an unreadable `ps` sweeps
    nothing and reports nothing ended."""
    def unreadable(argv, **kw):
        raise OSError("no ps on this box")

    monkeypatch.setattr(posixproc.subprocess, "run", unreadable)
    assert _sweep(["-m autowright."]) == 0
    assert ps_table.signals == []


def test_windows_sweep_matches_command_lines_and_tree_kills_each_pid(monkeypatch):
    """§3 Windows half: one CIM enumeration whose matches come back as
    AWPID lines, then a taskkill tree kill per pid."""
    scripts = []
    killed = []

    def fake_powershell(script, **kw):
        scripts.append(script)
        return 0, "AWPID:123\nnoise\nAWPID:456\n", ""

    monkeypatch.setattr(windows, "_powershell", fake_powershell)
    monkeypatch.setattr(windows.WindowsProcessControl, "kill_group",
                        lambda self, pgid: killed.append(pgid))
    markers = ["-m autowright.", r"C:\Program Files\Autowright\pythonw.exe"]
    n = REAL_WINDOWS_SWEEP(windows.WindowsProcessControl(), markers)
    assert n == 2
    assert killed == [123, 456]  # one tree kill each, non-AWPID lines ignored

    script = scripts[0]
    assert "$_.ProcessId -ne $PID" in script  # the enumerating PowerShell itself
    assert f"$_.ProcessId -ne {os.getpid()}" in script  # the sweeping python
    assert "$c -and" in script  # a NULL CommandLine can't be verified — skipped
    assert "$c.Contains('-m autowright.')" in script
    assert r"$c.Contains('C:\Program Files\Autowright\pythonw.exe')" in script
    assert '"' not in script  # single-quoted PS literals only — nothing to mangle


def test_windows_kill_group_is_a_taskkill_tree_kill(monkeypatch):
    ran = []
    monkeypatch.setattr(windows.subprocess, "run",
                        lambda argv, **kw: ran.append(argv))
    windows.WindowsProcessControl().kill_group(1234)
    assert ran == [["taskkill", "/F", "/T", "/PID", "1234"]]


def test_windows_signal_group_kills_tree_then_direct_child(monkeypatch):
    """Both grades (sig set or None) collapse to the tree kill, and a child
    the tree kill didn't reap is killed directly."""
    ran = []
    monkeypatch.setattr(windows.subprocess, "run",
                        lambda argv, **kw: ran.append(argv))

    class Proc:
        pid = 77
        killed = False

        def poll(self):
            return None

        def kill(self):
            self.killed = True

    for sig in (None, 15):
        proc = Proc()
        windows.WindowsProcessControl().signal_group(proc, sig)
        assert proc.killed
    assert ran == [["taskkill", "/F", "/T", "/PID", "77"]] * 2


# ------------------------------------------------- §3 Windows power assertion

SET_AWAKE = 0x80000000 | 0x00000001  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
CLEAR = 0x80000000                   # ES_CONTINUOUS alone — never ES_DISPLAY_REQUIRED


@pytest.fixture()
def win_power(monkeypatch):
    """A WindowsPower whose SetThreadExecutionState is a recorder. The real
    calls happen on the class's dedicated thread, so every assertion waits for
    the queue to drain first."""
    calls: list[int] = []
    monkeypatch.setattr(windows, "_set_thread_execution_state",
                        lambda flags: (calls.append(flags), True)[1])
    power = windows.WindowsPower()

    def settled() -> list[int]:
        power._wait_idle()
        return calls

    return power, settled


def test_windows_power_permanent_hold_is_counted_and_idempotent(win_power):
    """§3/§4.9: reconcile(True) sets the state once; a second reconcile(True)
    is not a second hold, and an execution hold on top adds no extra call."""
    power, settled = win_power
    power.reconcile(True)
    assert settled() == [SET_AWAKE]
    power.reconcile(True)
    assert settled() == [SET_AWAKE]  # idempotent — still one set
    release = power.hold_execution()
    assert settled() == [SET_AWAKE]  # already asserted; nothing to change
    release()
    assert settled() == [SET_AWAKE]  # the permanent hold still stands
    power.reconcile(False)
    assert settled() == [SET_AWAKE, CLEAR]  # last hold gone → cleared


def test_windows_power_execution_hold_alone_sets_and_clears(win_power):
    """§3 per-execution hold with keepAwake off: one set on acquire, one
    clear on release, and a double release drops nothing extra."""
    power, settled = win_power
    release = power.hold_execution()
    assert settled() == [SET_AWAKE]
    second = power.hold_execution()
    assert settled() == [SET_AWAKE]  # count 1 → 2, state unchanged
    release()
    release()  # double release is a no-op — the second hold still holds
    assert settled() == [SET_AWAKE]
    second()
    assert settled() == [SET_AWAKE, CLEAR]


def test_windows_power_reconcile_off_with_no_holds_is_silent(win_power):
    """Turning an assertion that was never on off again changes nothing."""
    power, settled = win_power
    power.reconcile(False)
    assert settled() == []


def test_windows_power_is_silent_when_the_setter_fails(monkeypatch):
    """A SetThreadExecutionState that answers False (or no Win32 at all, the
    POSIX test host) degrades silently — nothing here ever raises."""
    monkeypatch.setattr(windows, "_set_thread_execution_state", lambda flags: False)
    power = windows.WindowsPower()
    power.reconcile(True)
    release = power.hold_execution()
    release()
    power.reconcile(False)
    power._wait_idle()


def test_windows_pid_reuse_guard_answers_false():
    """§3: no pid+creation-time identity check yet — orphan recovery must
    no-op rather than kill an unverifiable tree."""
    assert windows.WindowsProcessControl().group_has_command(99, "autowright.executor") is False


# ------------------------------------------ §3 Windows service: Task Scheduler
#
# powershell.exe never runs here: the `task_scheduler` fixture (conftest.py)
# swaps `windows._powershell` for a recorder that models Task Scheduler's
# state, so every test below runs on any host.

TASK = "ai.autowright.backend"
_CMDLETS = ("Unregister-ScheduledTask", "Register-ScheduledTask",
            "Start-ScheduledTask", "Stop-ScheduledTask", "Get-ScheduledTask")


def _cmdlets(scripts):
    """The operation each recorded PowerShell script performs, in order."""
    out = []
    for script in scripts:
        out.append(next((c for c in _CMDLETS if c in script), script))
    return out


@pytest.fixture()
def win_service(task_scheduler, monkeypatch):
    """The Task Scheduler double plus a pinned action program, so the result
    lines and registration script read the same on every host."""
    monkeypatch.setattr(windows, "_backend_program",
                        lambda: (r"C:\Program Files\Autowright\pythonw.exe", None))
    return task_scheduler


def _no_shim_note():
    return f"CLI not installed — use `{sys.executable} -m autowright.cli`"


def test_windows_install_registers_starts_and_verifies(win_service):
    out = win_service.service.install()
    assert out == (f"installed and started (Task Scheduler task {TASK}) · "
                   f"{_no_shim_note()}")
    assert service.result_code(out) == 0
    assert win_service.task["state"] == "Running"
    # Stop-then-register-then-start (the launchd unload/load shape), then the
    # state read that verifies it actually started.
    assert _cmdlets(win_service.scripts) == [
        "Stop-ScheduledTask", "Register-ScheduledTask", "Start-ScheduledTask",
        "Get-ScheduledTask"]


def test_windows_registration_pins_the_launchd_equivalents(win_service):
    win_service.service.install()
    reg = next(s for s in win_service.scripts
               if "Register-ScheduledTask" in s and "Unregister" not in s)
    # RunAtLoad → -AtLogOn (this user only); KeepAlive → restart-on-failure.
    assert ("New-ScheduledTaskAction -Execute "
            r"'C:\Program Files\Autowright\pythonw.exe' "
            "-Argument '-m autowright.main'") in reg
    assert "New-ScheduledTaskTrigger -AtLogOn -User $user" in reg
    assert "-RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)" in reg
    # A long-lived backend: no 3-day execution kill, no battery gating.
    assert "-ExecutionTimeLimit ([TimeSpan]::Zero)" in reg
    assert "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries" in reg
    assert "-StartWhenAvailable" in reg
    assert f"Register-ScheduledTask -TaskName '{TASK}'" in reg and "-Force" in reg
    assert '"' not in reg  # single-quoted PS literals only — nothing to mangle


def test_windows_install_never_claims_success_for_a_task_that_never_started(win_service):
    """§3 launchd reality (b), mapped: the cmdlets can accept everything and
    leave the job unstarted — success is the state Task Scheduler reports."""
    win_service.canned["Start-ScheduledTask"] = (0, f"{windows._OK}\n", "")
    out = win_service.service.install()
    assert out == "install failed: the task is registered but did not start (state Ready)"
    assert service.result_code(out) == 1


def test_windows_install_reports_a_failed_registration(win_service):
    win_service.canned["Register-ScheduledTask"] = (
        1, "", "Register-ScheduledTask : Access is denied.\nAt line:1 char:1\n")
    out = win_service.service.install()
    assert out == "install failed: Register-ScheduledTask : Access is denied."
    assert service.result_code(out) == 1


def test_windows_backend_program_prefers_pythonw(monkeypatch, tmp_path):
    """§3: pythonw.exe beside this interpreter (venv Scripts\\ and the flat
    python-build-standalone layout both have it) — no console window flashes at
    logon. A layout without it still registers, with the reason on the line."""
    from types import SimpleNamespace

    exe = tmp_path / "python.exe"
    exe.write_text("")
    pythonw = tmp_path / "pythonw.exe"
    pythonw.write_text("")
    # The module's own `sys` reference, so nothing global moves under a
    # concurrently running thread.
    monkeypatch.setattr(windows, "sys", SimpleNamespace(executable=str(exe)))
    assert windows._backend_program() == (str(pythonw), None)
    pythonw.unlink()
    program, note = windows._backend_program()
    assert program == str(exe)
    assert note == ("no pythonw.exe beside python.exe — running it directly "
                    "(a console window may flash at logon)")


def test_windows_status_three_states(win_service, home):
    from autowright import paths

    out = win_service.service.status()
    assert out == "not installed" and service.result_code(out) == 1

    win_service.task["state"] = "Ready"
    out = win_service.service.status()
    assert out == "stopped (task present) — returns at next logon or app launch"
    assert service.result_code(out) == 0  # stopped on purpose is not a failure

    win_service.task["state"] = "Running"
    assert win_service.service.status() == "active (task running)"
    paths.backend_json().write_text(json.dumps({"port": 5151, "token": "t"}))
    assert win_service.service.status() == "active (task running) · port 5151"
    paths.backend_json().write_text('{"port": 51')  # SIGKILL-style truncation
    assert win_service.service.status() == "active (task running) · stale backend.json"


def test_windows_stop_keeps_the_task_registered(win_service):
    """§3 quit-entirely backend half: the task stays registered and returns at
    the next logon — the Windows form of launchd's bootout-only rule."""
    win_service.task["state"] = "Running"
    out = win_service.service.stop()
    assert out == "stopped — returns at next logon or app launch"
    assert service.result_code(out) == 0
    assert win_service.task["state"] == "Ready"  # still registered
    assert "Unregister-ScheduledTask" not in _cmdlets(win_service.scripts)


def test_windows_stop_when_not_installed(win_service):
    """§3: idempotent stop — absent registration, nothing running, exit 0."""
    out = win_service.service.stop()
    assert out == "already stopped — service not installed, nothing was running"
    assert service.result_code(out) == 0
    assert "Stop-ScheduledTask" not in _cmdlets(win_service.scripts)


def test_windows_stop_reports_the_strays_it_ended(win_service, monkeypatch):
    """§3: the tree kill orphans own-process-group children (pip, executors,
    stray CLI invocations) — the sweep ends them, and says how many."""
    win_service.task["state"] = "Running"
    monkeypatch.setattr(windows, "_sweep_strays", lambda: 2)
    out = win_service.service.stop()
    assert out == ("stopped — returns at next logon or app launch · "
                   "ended 2 lingering process(es)")
    assert service.result_code(out) == 0
    assert win_service.task["state"] == "Ready"  # still registered


def test_windows_stop_without_a_task_still_sweeps(win_service, monkeypatch):
    """No registration, but strays can still be alive — ending them is a
    successful stop (the macOS rule, mirrored)."""
    monkeypatch.setattr(windows, "_sweep_strays", lambda: 3)
    out = win_service.service.stop()
    assert out == "stopped — service was not installed; ended 3 lingering process(es)"
    assert service.result_code(out) == 0
    assert "Stop-ScheduledTask" not in _cmdlets(win_service.scripts)


def test_windows_sweep_strays_uses_the_shared_markers(no_kill_matching):
    """Both halves sweep for the same §3 markers — one definition, in paths."""
    assert windows._sweep_strays() == 0
    assert no_kill_matching == [paths.sweep_markers()]


def test_windows_stop_reports_a_task_that_is_still_running(win_service):
    """§3: stop reports failure when the job is still there afterwards — the
    app must not quit its UI while the backend it promised to stop lives on."""
    win_service.task["state"] = "Running"
    win_service.canned["Stop-ScheduledTask"] = (0, f"{windows._OK}\n", "")
    out = win_service.service.stop()
    assert out == "stop failed: the task is still running"
    assert service.result_code(out) == 1


def test_windows_restart_stops_then_starts(win_service):
    win_service.task["state"] = "Running"
    out = win_service.service.restart()
    assert out == "restarted" and service.result_code(out) == 0
    assert win_service.task["state"] == "Running"
    assert _cmdlets(win_service.scripts) == [
        "Get-ScheduledTask",   # registered?
        "Stop-ScheduledTask", "Get-ScheduledTask",   # stopped?
        "Start-ScheduledTask", "Get-ScheduledTask"]  # started?


def test_windows_restart_when_not_installed(win_service):
    out = win_service.service.restart()
    assert out == "not installed — use `autowright service install` first"
    assert service.result_code(out) == 1


def test_windows_uninstall_stops_and_unregisters(win_service):
    win_service.task["state"] = "Running"
    out = win_service.service.uninstall()
    assert out == "service stopped and unregistered"
    assert service.result_code(out) == 0
    assert win_service.task["state"] == "absent"
    assert _cmdlets(win_service.scripts) == [
        "Get-ScheduledTask", "Stop-ScheduledTask", "Unregister-ScheduledTask"]


def test_windows_uninstall_when_not_installed(win_service):
    out = win_service.service.uninstall()
    assert out == "service was not installed"
    assert service.result_code(out) == 0  # nothing to remove is not a failure


def test_windows_powershell_calls_are_time_boxed_and_utf8(monkeypatch):
    """§3: a wedged PowerShell must answer an ordinary failure line (exit 1),
    never hang the caller (the app's ensure-backend step waits on this) and
    never raise TimeoutExpired. §2: pipes are explicit UTF-8."""
    calls = []

    def wedged(argv, **kw):
        calls.append((argv, kw))
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    monkeypatch.setattr(windows.subprocess, "run", wedged)
    monkeypatch.setattr(windows, "_POLL_INTERVAL_S", 0)
    svc = windows.WindowsService()
    for verb in ("install", "uninstall", "status", "stop", "restart"):
        out = getattr(svc, verb)()
        assert out == f"{verb} failed: PowerShell timed out"
        assert service.result_code(out) == 1
    argv, kw = calls[0]
    assert argv[:6] == ["powershell.exe", "-NoProfile", "-NonInteractive",
                        "-OutputFormat", "Text", "-Command"]
    assert kw["timeout"] == windows.POWERSHELL_TIMEOUT_S == 30
    assert kw["capture_output"] is True
    assert kw["encoding"] == "utf-8" and kw["errors"] == "replace"
    assert kw["creationflags"] == windows._NO_WINDOW  # §2 spawn policy


def test_windows_missing_powershell_is_a_plain_failure_line(monkeypatch):
    def missing(argv, **kw):
        raise FileNotFoundError(2, "The system cannot find the file specified")

    monkeypatch.setattr(windows.subprocess, "run", missing)
    out = windows.WindowsService().status()
    assert out.startswith("status failed: powershell.exe could not be run (")
    assert service.result_code(out) == 1


# ------------------------------------------------------ §3 Windows .cmd shim

def test_windows_shim_paths_and_text(monkeypatch, tmp_path):
    monkeypatch.delenv("AUTOWRIGHT_SHIM", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert windows.shim_paths() == [
        tmp_path / "Local" / "Autowright" / "bin" / "autowright.cmd"]
    # §15 knob (mirrored in electron/main.cjs): tests never touch the real one.
    monkeypatch.setenv("AUTOWRIGHT_SHIM", str(tmp_path / "elsewhere" / "aw.cmd"))
    assert windows.shim_paths() == [tmp_path / "elsewhere" / "aw.cmd"]
    # Byte-for-byte the shell's win32.cjs form: CRLF, marker, module form.
    assert windows.shim_text() == (
        f"@echo off\r\nrem autowright CLI shim\r\n"
        f'"{sys.executable}" -m autowright.cli %*\r\n')


def test_ps_literal_doubles_embedded_quotes():
    assert windows._ps_literal(r"C:\Bob's Files\py.exe") == r"'C:\Bob''s Files\py.exe'"


def test_windows_install_heals_our_shim(win_service):
    """§3: ours (marker) + another interpreter → rewritten in place."""
    shim = win_service.shim
    shim.parent.mkdir(parents=True)
    shim.write_bytes(f"@echo off\r\n{windows.SHIM_MARKER}\r\n"
                     '"C:\\old\\gone\\pythonw.exe" -m autowright.cli %*\r\n'.encode())
    out = win_service.service.install()
    assert f"CLI at {shim}" in out
    raw = shim.read_bytes()
    assert raw == windows.shim_text().encode()
    assert b"\r\r\n" not in raw and raw.endswith(b"%*\r\n")  # CRLF exactly once
    assert f'"{sys.executable}" -m autowright.cli %*' in raw.decode()


def test_windows_install_reports_a_current_shim(win_service):
    shim = win_service.shim
    shim.parent.mkdir(parents=True)
    shim.write_bytes(windows.shim_text().encode())
    assert f"CLI at {shim}" in win_service.service.install()


def test_windows_install_never_creates_a_shim(win_service):
    # §3: creation is the Electron shell's cli-install flow; install only heals.
    out = win_service.service.install()
    assert _no_shim_note() in out
    assert not win_service.shim.exists()


def test_windows_install_leaves_a_foreign_cmd_alone(win_service):
    shim = win_service.shim
    shim.parent.mkdir(parents=True)
    shim.write_bytes(b"@echo off\r\necho someone else's autowright\r\n")
    out = win_service.service.install()
    assert f"foreign {shim} left alone" in out
    assert b"someone else" in shim.read_bytes()


def test_windows_install_leaves_an_undecodable_file_alone(win_service):
    payload = b"\x00\x80\xff not utf-8"
    shim = win_service.shim
    shim.parent.mkdir(parents=True)
    shim.write_bytes(payload)
    out = win_service.service.install()
    assert out.startswith("installed and started")
    assert shim.read_bytes() == payload


def test_windows_uninstall_removes_our_shim(win_service):
    win_service.task["state"] = "Ready"
    shim = win_service.shim
    shim.parent.mkdir(parents=True)
    shim.write_bytes(windows.shim_text().encode())
    assert win_service.service.uninstall() == "service stopped and unregistered"
    assert not shim.exists()


def test_windows_uninstall_leaves_a_foreign_cmd_alone(win_service):
    shim = win_service.shim
    shim.parent.mkdir(parents=True)
    shim.write_bytes(b"@echo off\r\necho someone else's autowright\r\n")
    win_service.service.uninstall()
    assert shim.exists()


def test_windows_uninstall_reports_an_undeletable_shim(win_service, monkeypatch):
    shim = win_service.shim
    shim.parent.mkdir(parents=True)
    shim.write_bytes(windows.shim_text().encode())

    def refuse(self):
        raise OSError("in use")

    monkeypatch.setattr(Path, "unlink", refuse)
    out = win_service.service.uninstall()
    assert f"CLI shim left at {shim}" in out and "couldn't delete" in out
    assert service.result_code(out) == 0  # informational, after the "·"


# ---------------------------------------------- §3 Linux service: systemd unit
#
# systemctl never runs here: the `systemd` fixture (conftest.py) swaps
# `linux._systemctl` for a recorder that models the user manager's state, so
# every test below runs on any host.

UNIT = "ai.autowright.backend.service"


def _systemd_verbs(calls):
    return [c[0] for c in calls]


def test_linux_install_writes_unit_enables_starts_and_verifies(systemd):
    out = systemd.service.install()
    assert out == f"installed and started ({linux.unit_path()}) · {_no_shim_note()}"
    assert service.result_code(out) == 0
    assert linux.unit_path().exists()
    assert systemd.unit == {"active": True, "enabled": True}
    # Write + reload + enable, then a *restart* (never a bare start — a
    # rewritten unit must always be adopted), then the state read that
    # verifies it actually started.
    assert _systemd_verbs(systemd.calls) == [
        "daemon-reload", "enable", "restart", "is-active"]


def test_linux_unit_pins_the_launchd_equivalents(systemd):
    systemd.service.install()
    text = linux.unit_path().read_text()
    # RunAtLoad → enabled WantedBy=default.target; KeepAlive → Restart=always
    # with launchd-style throttle and no start-limit give-up.
    assert f'ExecStart="{sys.executable}" -m autowright.main\n' in text
    assert "Restart=always\n" in text
    assert "RestartSec=2\n" in text
    assert "StartLimitIntervalSec=0\n" in text
    assert "WantedBy=default.target\n" in text
    # §3 log routing: launchd's file capture, systemd clothes — the §9.3
    # overlay and main.py's startup trim depend on these exact files.
    assert f"StandardOutput=append:{paths.logs_dir() / 'backend.out.log'}\n" in text
    assert f"StandardError=append:{paths.logs_dir() / 'backend.err.log'}\n" in text
    assert paths.logs_dir().is_dir()  # systemd creates the files, not the dir


def test_linux_install_never_claims_success_for_a_unit_that_never_started(systemd):
    """§3 launchd reality (b), mapped: systemctl can accept every verb and
    leave the unit dead — success is the state systemd reports afterwards."""
    systemd.canned["is-active"] = (3, "activating\n", "")
    out = systemd.service.install()
    assert out == "install failed: the unit is enabled but did not start (state activating)"
    assert service.result_code(out) == 1


def test_linux_install_reports_a_failed_enable(systemd):
    systemd.canned["enable"] = (1, "", "Failed to enable unit: Access denied\n")
    out = systemd.service.install()
    assert out == "install failed: Failed to enable unit: Access denied"
    assert service.result_code(out) == 1


def test_linux_install_reports_a_wedged_systemctl(systemd):
    """§3: a wedged systemctl must never hang the caller — the timeout reads
    as a plain-word failure, never a traceback."""
    systemd.canned["daemon-reload"] = linux._TimedOut()
    out = systemd.service.install()
    assert out == "install failed: systemctl timed out"
    assert service.result_code(out) == 1


def test_linux_status_three_states(systemd):
    out = systemd.service.status()
    assert out == "not installed" and service.result_code(out) == 1

    systemd.service.install()
    systemd.unit["active"] = False
    out = systemd.service.status()
    assert out == "stopped (unit present) — returns at next login or app launch"
    assert service.result_code(out) == 0  # stopped on purpose is not a failure

    systemd.unit["active"] = True
    assert systemd.service.status() == "active (pid 4242)"
    paths.backend_json().write_text(json.dumps({"port": 5151, "token": "t"}))
    assert systemd.service.status() == "active (pid 4242) · port 5151"
    paths.backend_json().write_text('{"port": 51')  # SIGKILL-style truncation
    assert systemd.service.status() == "active (pid 4242) · stale backend.json"


def test_linux_stop_keeps_the_unit_enabled(systemd, monkeypatch):
    """§3 quit-entirely backend half: the unit stays enabled and returns at
    the next login — the systemd form of launchd's bootout-only rule."""
    systemd.service.install()
    monkeypatch.setattr(linux, "_sweep_strays", lambda: 0)
    out = systemd.service.stop()
    assert out == "stopped — returns at next login or app launch"
    assert service.result_code(out) == 0
    assert systemd.unit == {"active": False, "enabled": True}
    assert "disable" not in _systemd_verbs(systemd.calls)


def test_linux_stop_reports_the_strays_it_ended(systemd, monkeypatch):
    systemd.service.install()
    monkeypatch.setattr(linux, "_sweep_strays", lambda: 2)
    out = systemd.service.stop()
    assert out == ("stopped — returns at next login or app launch · "
                   "ended 2 lingering process(es)")
    assert service.result_code(out) == 0


def test_linux_stop_when_not_installed(systemd, monkeypatch):
    """§3: with no unit a sweep that ended strays is a successful stop (how
    quit-all succeeds against a directly-spawned dev backend); an empty sweep
    is a successful, idempotent no-op."""
    monkeypatch.setattr(linux, "_sweep_strays", lambda: 0)
    out = systemd.service.stop()
    assert out == "already stopped — service not installed, nothing was running"
    assert service.result_code(out) == 0
    assert "stop" not in _systemd_verbs(systemd.calls)

    monkeypatch.setattr(linux, "_sweep_strays", lambda: 3)
    out = systemd.service.stop()
    assert out == "stopped — service was not installed; ended 3 lingering process(es)"
    assert service.result_code(out) == 0


def test_linux_uninstall_disables_and_removes_the_unit(systemd):
    systemd.service.install()
    out = systemd.service.uninstall()
    assert out == "service stopped and unregistered"
    assert service.result_code(out) == 0
    assert not linux.unit_path().exists()
    assert "disable" in _systemd_verbs(systemd.calls)
    assert systemd.unit == {"active": False, "enabled": False}

    out = systemd.service.uninstall()
    assert out == "service was not installed"


def test_linux_restart_requires_an_install(systemd):
    out = systemd.service.restart()
    assert out == "not installed — use `autowright service install` first"
    assert service.result_code(out) == 1


def test_linux_unit_path_honors_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert linux.unit_path() == tmp_path / "cfg" / "systemd" / "user" / UNIT
    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert linux.unit_path() == tmp_path / ".config" / "systemd" / "user" / UNIT


def test_linux_unit_text_escapes_systemd_specials(systemd, monkeypatch):
    """`%` is a specifier introducer and ExecStart words are quoted — an
    interpreter path carrying either must survive verbatim."""
    from types import SimpleNamespace as NS

    monkeypatch.setattr(linux, "sys", NS(executable='/odd path/100%/py"3'))
    text = linux.unit_text()
    assert 'ExecStart="/odd path/100%%/py\\"3" -m autowright.main\n' in text


def test_linux_notifier_posts_best_effort(monkeypatch):
    ran: list[list[str]] = []
    monkeypatch.setattr(linux.subprocess, "run",
                        lambda cmd, **kw: ran.append(cmd))
    linux.NotifySendNotifier().post("Title", "Body")
    assert ran == [["notify-send", "--app-name=Autowright", "--", "Title", "Body"]]

    def boom(cmd, **kw):
        raise OSError("no notify-send")

    monkeypatch.setattr(linux.subprocess, "run", boom)
    linux.NotifySendNotifier().post("t", "b")  # never raises


class _FakeInhibit:
    def __init__(self, cmd):
        self.cmd = cmd
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        return 0


def test_linux_power_assertions_spawn_and_release_inhibitors(monkeypatch):
    """§3: one `systemd-inhibit --what=idle` per assertion, wrapping a watch
    on the backend pid so the lock dies with the backend (`caffeinate -w`'s
    no-orphan guarantee)."""
    spawned: list[_FakeInhibit] = []

    def fake_popen(cmd, **kw):
        proc = _FakeInhibit(cmd)
        spawned.append(proc)
        return proc

    monkeypatch.setattr(linux.subprocess, "Popen", fake_popen)
    power = linux.SystemdInhibitPower()
    power.reconcile(True)
    power.reconcile(True)  # idempotent — still one inhibitor
    assert len(spawned) == 1
    cmd = spawned[0].cmd
    assert cmd[:2] == ["systemd-inhibit", "--what=idle"]
    assert "--who=Autowright" in cmd
    assert f"--pid={os.getpid()}" in " ".join(cmd)

    release = power.hold_execution()
    assert len(spawned) == 2
    release()
    release()  # double release is harmless
    assert spawned[1].terminated and not spawned[0].terminated

    power.reconcile(False)
    assert spawned[0].terminated


def test_linux_build_probes_the_host_tools(monkeypatch):
    """§2: capabilities are probed at composition — a host with the tools
    composes the real pieces, a host without them the fallback pieces, and
    neither ever crashes."""
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    plat = linux.build()
    assert plat.os_token == "linux"
    assert isinstance(plat.service, linux.SystemdService)
    assert isinstance(plat.notifier, linux.NotifySendNotifier)
    assert isinstance(plat.power, linux.SystemdInhibitPower)
    assert isinstance(plat.processes, posixproc.PosixProcessControl)
    assert _composes_process_control(plat)
    assert plat.capabilities.as_dict() == {
        "imessage": False, "notifications": True, "keepAwake": True,
        "service": True, "agentInstall": True}

    monkeypatch.setattr(shutil, "which", lambda name: None)
    plat = linux.build()
    assert isinstance(plat.service, fallback.UnsupportedService)
    assert isinstance(plat.notifier, fallback.NullNotifier)
    assert isinstance(plat.power, fallback.NullPower)
    assert plat.capabilities.as_dict() == {
        "imessage": False, "notifications": False, "keepAwake": False,
        "service": False, "agentInstall": True}
    assert plat.service.install() == "install failed: not supported on Linux yet"


# ---------------------------------------------------- §3 degraded service verbs

def test_fallback_service_answers_plain_failure_lines():
    svc = fallback.build("windows", "Windows").service
    for verb in ("install", "uninstall", "status", "stop", "restart"):
        out = getattr(svc, verb)()
        assert out == f"{verb} failed: not supported on Windows yet"
        assert service.result_code(out) == 1  # §3 exit-code rule


def test_service_actions_route_through_platform(monkeypatch):
    """§2: `python -m autowright.service <verb>` and the §20 wrapper go through
    the composed ServiceManager — on an unsupported OS they degrade to the
    plain failure line instead of crashing on a missing launchctl."""
    monkeypatch.setattr(service.platform, "current",
                        lambda: fallback.build("linux", "Linux"))
    assert service.ACTIONS["install"]() == "install failed: not supported on Linux yet"
    assert service.main(["install"]) == 1


# ---------------------------------------------------------- §5 per-OS root table

@pytest.fixture()
def bare_home(monkeypatch, tmp_path):
    """Roots resolve from the OS defaults: no AUTOWRIGHT_HOME, a pinned home,
    and no XDG/Windows env overrides leaking in from the host."""
    monkeypatch.delenv("AUTOWRIGHT_HOME", raising=False)
    for var in ("XDG_DATA_HOME", "XDG_STATE_HOME", "LOCALAPPDATA"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def test_root_table_macos(bare_home, monkeypatch):
    monkeypatch.setattr(paths, "current_os", lambda: "macos")
    assert paths.app_support() == bare_home / "Library" / "Application Support" / "Autowright"
    assert paths.logs_dir() == bare_home / "Library" / "Logs" / "Autowright"


def test_root_table_linux_defaults_and_xdg(bare_home, monkeypatch):
    monkeypatch.setattr(paths, "current_os", lambda: "linux")
    assert paths.app_support() == bare_home / ".local" / "share" / "autowright"
    assert paths.logs_dir() == bare_home / ".local" / "state" / "autowright" / "log"
    monkeypatch.setenv("XDG_DATA_HOME", str(bare_home / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(bare_home / "xdg-state"))
    assert paths.app_support() == bare_home / "xdg-data" / "autowright"
    assert paths.logs_dir() == bare_home / "xdg-state" / "autowright" / "log"


def test_root_table_windows_defaults_and_localappdata(bare_home, monkeypatch):
    monkeypatch.setattr(paths, "current_os", lambda: "windows")
    assert paths.app_support() == bare_home / "AppData" / "Local" / "Autowright"
    assert paths.logs_dir() == bare_home / "AppData" / "Local" / "Autowright" / "Logs"
    monkeypatch.setenv("LOCALAPPDATA", str(bare_home / "LocalAppData"))
    assert paths.app_support() == bare_home / "LocalAppData" / "Autowright"
    assert paths.logs_dir() == bare_home / "LocalAppData" / "Autowright" / "Logs"


# ------------------------------------------------------ §9 per-OS copy table


def test_copy_table_machine_noun():
    """§9 per-OS copy rule, backend half: "Mac" on macOS, "PC" on Windows and
    Linux alike. The renderer half is app/src/platformCopyTable.ts, pinned by
    app/tests/platform-copy-table.test.ts — the two must never drift."""
    assert paths.machine_noun("macos") == "Mac"
    assert paths.machine_noun("windows") == "PC"
    assert paths.machine_noun("linux") == "PC"


def test_copy_table_secret_store_name():
    """§9 per-OS copy rule, backend half: the §4.8 store's user-facing name.
    Same three strings the renderer table serves."""
    assert paths.secret_store_name("macos") == "Keychain"
    assert paths.secret_store_name("windows") == "Credential Manager"
    assert paths.secret_store_name("linux") == "system keyring"


def test_copy_table_tray_surface_name():
    """§9 per-OS copy rule, backend half of the renderer table's `menuBar`
    entry: "menu bar" on macOS, "tray" on Windows, and None on Linux (§13: no
    tray surface, so callers drop the clause)."""
    assert paths.tray_surface_name("macos") == "menu bar"
    assert paths.tray_surface_name("windows") == "tray"
    assert paths.tray_surface_name("linux") is None


def test_copy_table_tray_trigger_label():
    """§4.5/§9: the `menubar` execution trigger label — "Menu bar" on macOS,
    "Tray" on Windows and Linux."""
    assert paths.tray_trigger_label("macos") == "Menu bar"
    assert paths.tray_trigger_label("windows") == "Tray"
    assert paths.tray_trigger_label("linux") == "Tray"


def test_copy_table_defaults_to_the_running_platform(monkeypatch):
    """Both helpers default to `current_os()` — the caller never has to know
    which platform it is on. An empty token is the same as none (the renderer's
    '' boot default has no equivalent here: the backend always knows its own
    OS), and an unrecognized token falls back to the §5.1 non-macOS shape."""
    for token in ("macos", "windows", "linux"):
        monkeypatch.setattr(paths, "current_os", lambda token=token: token)
        assert paths.machine_noun() == paths.machine_noun(token)
        assert paths.machine_noun("") == paths.machine_noun(token)
        assert paths.secret_store_name() == paths.secret_store_name(token)
        assert paths.secret_store_name("") == paths.secret_store_name(token)
        assert paths.tray_surface_name() == paths.tray_surface_name(token)
        assert paths.tray_trigger_label() == paths.tray_trigger_label(token)
    assert paths.machine_noun("plan9") == "PC"
    assert paths.tray_surface_name("plan9") == "tray"
    assert paths.tray_trigger_label("plan9") == "Tray"
    assert paths.secret_store_name("plan9") == "Keychain"


def test_autowright_home_overrides_every_os(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTOWRIGHT_HOME", str(tmp_path / "override"))
    for token in ("macos", "linux", "windows"):
        monkeypatch.setattr(paths, "current_os", lambda token=token: token)
        assert paths.app_support() == tmp_path / "override"
        assert paths.logs_dir() == tmp_path / "override" / "logs"


# ------------------------------------------------------------- §19 /health

def test_health_serves_os_and_capabilities(client):
    plat = platform.current()
    body = client.get("/health").json()
    assert body["os"] == plat.os_token
    assert body["capabilities"] == plat.capabilities.as_dict()
    # …and what it serves is this host's real table, not an empty echo.
    assert body["capabilities"] == EXPECTED_CAPABILITIES[plat.os_token]
    assert body["app"] == "Autowright" and body["version"]


# ----------------------------- appended coverage: restart, stop, wedged, power

def test_linux_restart_of_an_installed_unit(systemd):
    """§3: restart is the unit verb plus the state read that proves it. A
    systemctl that accepts the verb and leaves the unit dead is a failure."""
    systemd.service.install()
    out = systemd.service.restart()
    assert out == "restarted" and service.result_code(out) == 0
    assert _systemd_verbs(systemd.calls)[-2:] == ["restart", "is-active"]

    systemd.canned["restart"] = (1, "", "Unit not found\n")
    out = systemd.service.restart()
    assert out == "restart failed: Unit not found"
    assert service.result_code(out) == 1

    del systemd.canned["restart"]
    systemd.canned["is-active"] = (3, "failed\n", "")
    out = systemd.service.restart()
    assert out == "restart failed: the unit is enabled but did not start (state failed)"
    assert service.result_code(out) == 1


def test_linux_stop_reports_a_failed_verb_and_a_unit_still_running(systemd):
    """§3: the app must not quit its UI while the backend it promised to stop
    lives on: both the verb and the state afterwards have to agree."""
    systemd.service.install()
    systemd.canned["stop"] = (1, "", "Interactive authentication required\n")
    out = systemd.service.stop()
    assert out == "stop failed: Interactive authentication required"
    assert service.result_code(out) == 1

    del systemd.canned["stop"]
    systemd.canned["is-active"] = (0, "active\n", "")
    out = systemd.service.stop()
    assert out == "stop failed: the unit is still running"
    assert service.result_code(out) == 1


def test_linux_status_reports_a_wedged_systemctl(systemd):
    """§3: the state read behind `status` is time-boxed like every other call:
    a wedged systemctl reads as a plain-word failure, never a hang."""
    systemd.service.install()
    systemd.canned["is-active"] = linux._TimedOut()
    out = systemd.service.status()
    assert out == "status failed: systemctl timed out"
    assert service.result_code(out) == 1


def test_linux_power_survives_a_missing_systemd_inhibit(monkeypatch):
    """§3: a host with no `systemd-inhibit` holds nothing and says nothing:
    the assertions are best-effort, so neither verb may raise."""
    def missing(cmd, **kw):
        raise FileNotFoundError("systemd-inhibit")

    monkeypatch.setattr(linux.subprocess, "Popen", missing)
    power = linux.SystemdInhibitPower()
    power.reconcile(True)
    assert power._proc is None
    release = power.hold_execution()
    release()
    release()  # a hold that spawned nothing is still safe to release twice


def test_windows_restart_of_a_registered_task(win_service):
    """§3: restart stops and starts the registered task, proving each half by
    the state Task Scheduler reports; either cmdlet failing is the failure."""
    win_service.service.install()
    out = win_service.service.restart()
    assert out == "restarted" and service.result_code(out) == 0
    assert win_service.task["state"] == "Running"

    win_service.canned["Stop-ScheduledTask"] = (1, "", "Access is denied.\n")
    out = win_service.service.restart()
    assert out == "restart failed: Access is denied."
    assert service.result_code(out) == 1

    del win_service.canned["Stop-ScheduledTask"]
    win_service.canned["Start-ScheduledTask"] = (1, "", "The system cannot find the file.\n")
    out = win_service.service.restart()
    assert out == "restart failed: The system cannot find the file."
    assert service.result_code(out) == 1


def test_windows_install_reports_a_failed_start(win_service):
    win_service.canned["Start-ScheduledTask"] = (1, "", "Access is denied")
    out = win_service.service.install()
    assert out == "install failed: Access is denied"
    assert service.result_code(out) == 1


def test_windows_install_reports_a_task_that_vanished(win_service):
    """§3: the state read can answer that no such task is registered at all.
    A registration that silently went away is its own message, not "did not
    start"."""
    win_service.canned["Get-ScheduledTask"] = (
        0, f"{windows._STATE_PREFIX}{windows._ABSENT}\n", "")
    out = win_service.service.install()
    assert out == "install failed: the task is gone from Task Scheduler"
    assert service.result_code(out) == 1


class _NoThreads:
    """`threading` whose Thread constructor always fails; everything else is
    the real module (conftest's `_SubprocessProxy` shape)."""

    @staticmethod
    def Thread(*args, **kwargs):
        raise RuntimeError("can't start new thread")

    def __getattr__(self, name):
        return getattr(threading, name)


def test_windows_power_applies_inline_when_no_worker_can_start(monkeypatch):
    """§3: with no thread to be had the assertion is applied inline rather than
    silently dropped: the same flags, on the calling thread."""
    calls: list[int] = []
    monkeypatch.setattr(windows, "_set_thread_execution_state",
                        lambda flags: (calls.append(flags), True)[1])
    monkeypatch.setattr(windows, "threading", _NoThreads())
    power = windows.WindowsPower()
    power.reconcile(True)
    assert calls == [SET_AWAKE]
    release = power.hold_execution()
    release()
    assert calls == [SET_AWAKE]  # the permanent hold still stands
    power.reconcile(False)
    assert calls == [SET_AWAKE, CLEAR]
