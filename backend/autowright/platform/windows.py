"""§2 platform layer — the Windows build.

The §3 service manager (Task Scheduler), process control, the §3 idle-sleep
power assertions and the §3 WinRT toast notifier are all real. Windows has no
signalable process groups, so "group" operations act on the process tree
rooted at the child's pid (`taskkill /T`), and the §4.5 persisted group id
stays the child's pid — same pid == group invariant as POSIX own-session
spawns.
"""
from __future__ import annotations

import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from .. import paths
from .base import Capabilities, Platform

# Windows-only in CPython's subprocess module; the numeric value is part of
# the Win32 ABI. The getattr keeps this module importable (and its §15 tests
# runnable) on POSIX hosts.
_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
# §2 spawn policy: the backend runs under pythonw.exe (§3), so a
# console-subsystem child spawned without this opens its own visible terminal
# window on the user's desktop.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)

# SetThreadExecutionState flags (Win32 ABI). ES_DISPLAY_REQUIRED is
# deliberately absent — §3 keeps display sleep allowed.
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001


def _set_thread_execution_state(flags: int) -> bool:
    """Thin wrapper over kernel32's SetThreadExecutionState. Answers False
    where the call isn't available (POSIX hosts, so this module still imports
    and its §15 tests still run) or when the API reports failure — never
    raises. The §15 suites monkeypatch this function."""
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return False
    try:
        return bool(windll.kernel32.SetThreadExecutionState(ctypes.c_uint(flags)))
    except Exception:  # noqa: BLE001
        return False


class WindowsPower:
    """§3 idle-sleep assertions: one thread-owned
    `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` behind a
    counted set of holds — the permanent §4.9 keepAwake hold plus one per
    active execution. The state is set while the count is positive and
    cleared (`ES_CONTINUOUS` alone) when it reaches zero.

    Execution state is *per thread*, so every call is made from one dedicated
    long-lived daemon thread fed by a queue; the counting itself is
    lock-guarded. Windows clears a thread's execution state when the process
    dies, so a crashed backend can never leave an orphan. Nothing here ever
    raises."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._permanent = False
        self._holds = 0
        self._active = False
        self._queue: queue.Queue[int] = queue.Queue()
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------- PowerAssertion

    def reconcile(self, enabled: bool) -> None:
        """The permanent §4.9 assertion — idempotent: enabling twice is still
        one hold."""
        with self._lock:
            if enabled == self._permanent:
                return
            self._permanent = enabled
            self._refresh_locked()

    def hold_execution(self) -> Callable[[], None]:
        """One §3 per-execution hold; the returned release drops exactly one,
        however often it is called."""
        with self._lock:
            self._holds += 1
            self._refresh_locked()
        released = threading.Event()

        def release() -> None:
            if released.is_set():
                return
            released.set()
            with self._lock:
                if self._holds > 0:
                    self._holds -= 1
                self._refresh_locked()

        return release

    # ------------------------------------------------------------- mechanics

    def _refresh_locked(self) -> None:
        active = (1 if self._permanent else 0) + self._holds > 0
        if active == self._active:
            return  # already in the wanted state — no redundant call
        self._active = active
        self._submit(_ES_CONTINUOUS | _ES_SYSTEM_REQUIRED if active else _ES_CONTINUOUS)

    def _submit(self, flags: int) -> None:
        self._queue.put(flags)
        if not self._ensure_worker():
            # No thread to be had (never observed in practice): apply inline
            # rather than silently dropping the assertion.
            self._drain()

    def _ensure_worker(self) -> bool:
        thread = self._thread
        if thread is not None and thread.is_alive():
            return True
        try:
            thread = threading.Thread(target=self._loop, name="autowright-power",
                                      daemon=True)
            thread.start()
        except Exception:  # noqa: BLE001
            self._thread = None
            return False
        self._thread = thread
        return True

    def _loop(self) -> None:
        while True:
            flags = self._queue.get()
            try:
                _set_thread_execution_state(flags)
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._queue.task_done()

    def _drain(self) -> None:
        while True:
            try:
                flags = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                _set_thread_execution_state(flags)
            except Exception:  # noqa: BLE001
                pass
            finally:
                self._queue.task_done()

    def _wait_idle(self, timeout: float = 5.0) -> None:
        """§15 test seam: block until every queued state change has been
        applied by the worker thread."""
        deadline = threading.Event()
        waiter = threading.Thread(target=lambda: (self._queue.join(), deadline.set()),
                                  daemon=True)
        waiter.start()
        deadline.wait(timeout)


class WindowsProcessControl:
    """§3 process-tree control via taskkill. No graceful cross-tree signal
    exists on Windows, so both grades (`sig` set or None) kill the tree hard —
    the §7 term-then-kill escalation collapses to one kill."""

    def session_kwargs(self) -> dict:
        """Own process group per child, so the persisted group id (== pid)
        stays meaningful and Ctrl events can't propagate from a console —
        plus a hidden console (§2 spawn policy), so a console-subsystem child
        of the windowless backend never opens a terminal window."""
        return {"creationflags": _NEW_PROCESS_GROUP | _NO_WINDOW}

    def signal_group(self, proc, sig: int | None = None) -> None:
        self.kill_group(proc.pid)
        # Belt-and-braces for a tree taskkill couldn't address (already-gone
        # pid raced by a fresh child): kill the direct child if still alive.
        if proc.poll() is None:
            proc.kill()

    def kill_group(self, pgid: int) -> None:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(pgid)],
                           capture_output=True, timeout=10,
                           creationflags=_NO_WINDOW)  # §2 spawn policy
        except (OSError, subprocess.SubprocessError):
            pass

    def group_has_command(self, pgid: int, marker: str) -> bool:
        """§3 pid-reuse guard: needs pid + creation-time identity on Windows;
        until that lands, answer False — never kill what can't be verified
        (orphan recovery becomes a no-op, same as an unreadable process
        table on POSIX)."""
        return False

    def kill_matching(self, markers: Sequence[str], grace_s: float = 2.0) -> int:
        """§3 quit-entirely sweep: kill every process whose command line
        carries one of `markers`, by the taskkill tree kill (both TERM/KILL
        grades collapse to it — `grace_s` is ignored). The enumerating
        PowerShell's own command line embeds the marker literals, so it must
        exclude itself (`$PID`), and the sweeping python is excluded by pid.
        Rows with a NULL CommandLine (elevated/system processes) are skipped —
        never kill what can't be verified. Returns the matched count."""
        clauses = " -or ".join(f"$c.Contains({_ps_literal(m)})" for m in markers)
        script = (
            "$ErrorActionPreference = 'SilentlyContinue'; "
            "Get-CimInstance Win32_Process | ForEach-Object { "
            "$c = $_.CommandLine; "
            f"if ($c -and $_.ProcessId -ne $PID -and "
            f"$_.ProcessId -ne {os.getpid()} -and ({clauses})) "
            "{ 'AWPID:' + $_.ProcessId } }"
        )
        _code, out, _err = _powershell(script)
        pids = []
        for line in out.splitlines():
            tail = line.strip().removeprefix("AWPID:")
            if tail != line.strip() and tail.isdigit():
                pids.append(int(tail))
        for pid in pids:
            self.kill_group(pid)
        return len(pids)


# ------------------------------------------------ §3 service: Task Scheduler

LABEL = "ai.autowright.backend"  # the same reverse-DNS label as the LaunchAgent
SHIM_MARKER = "rem autowright CLI shim"  # §3 .cmd shim marker (mirrors win32.cjs)

POWERSHELL_TIMEOUT_S = 30
_TIMED_OUT = "PowerShell timed out"
_OK = "AWOK"  # printed by every mutating script that reached its end
_STATE_PREFIX = "AWSTATE:"  # prefix of the single line the state query prints
_ABSENT = "absent"  # the state query's word for "no such task"
# Start/stop are asynchronous — poll the task's state briefly (§3: never claim
# success for a task that is not registered or never started).
_POLL_TRIES = 20
_POLL_INTERVAL_S = 0.25


def _ps_literal(value: str) -> str:
    """One PowerShell single-quoted literal: no expansion at all, embedded
    quotes doubled. Every interpolated value goes through here — paths hold
    spaces, and the scripts below carry no double quotes so Windows
    command-line quoting has nothing to mangle."""
    return "'" + str(value).replace("'", "''") + "'"


def _powershell(script: str, timeout: float = POWERSHELL_TIMEOUT_S) -> tuple[int, str, str]:
    """Run one PowerShell operation (ScheduledTasks cmdlets, §3 — never bare
    schtasks.exe, where restart-on-failure is not expressible; or the §3 WinRT
    toast below).

    Time-boxed like `launchctl` (§3): a wedged or missing PowerShell answers a
    plain non-zero result with a plain-word message, never a TimeoutExpired
    traceback and never a hang — the app's ensure-backend step waits on this.
    Pipes are explicit UTF-8 (§2 pipe-encoding contract)."""
    argv = ["powershell.exe", "-NoProfile", "-NonInteractive",
            "-OutputFormat", "Text", "-Command", script]
    try:
        r = subprocess.run(argv, capture_output=True, encoding="utf-8",
                           errors="replace", timeout=timeout,
                           creationflags=_NO_WINDOW)  # §2 spawn policy
    except subprocess.TimeoutExpired:
        return 1, "", _TIMED_OUT
    except OSError as e:  # no powershell.exe on this box
        return 1, "", f"powershell.exe could not be run ({e})"
    return r.returncode, r.stdout or "", r.stderr or ""


def _first_line(text: str) -> str:
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return ""


def _error_of(code: int, out: str, err: str) -> str:
    """One plain-word failure reason from a PowerShell result — PowerShell
    writes its error records to stderr, but a policy/host failure can land on
    stdout instead, so both are consulted before falling back to the code."""
    return _first_line(err) or _first_line(out) or f"powershell exited {code}"


def _run_op(script: str) -> str | None:
    """A mutating operation: None on success, else the failure reason. Success
    is the script's own end-marker, not merely exit 0."""
    code, out, err = _powershell(script)
    if code == 0 and _OK in out:
        return None
    return _error_of(code, out, err)


def _query_state() -> tuple[str, str | None]:
    """(state, error). State is Task Scheduler's own word ("Running", "Ready",
    "Disabled", …) or "absent" when no such task is registered."""
    script = (
        "$ErrorActionPreference = 'Stop'; "
        f"$t = @(Get-ScheduledTask -TaskName {_ps_literal(LABEL)} "
        "-ErrorAction SilentlyContinue); "
        f"if ($t.Count -eq 0) {{ '{_STATE_PREFIX}{_ABSENT}' }} "
        f"else {{ '{_STATE_PREFIX}' + $t[0].State }}"
    )
    code, out, err = _powershell(script)
    for line in out.splitlines():
        line = line.strip()
        if line.startswith(_STATE_PREFIX):
            return line[len(_STATE_PREFIX):].strip(), None
    return "", _error_of(code, out, err)


def _register(program: str) -> str | None:
    """Register or update the task in place (`-Force`), scoped to this user.

    Settings mirror the launchd contract: `-AtLogOn` is RunAtLoad,
    `-RestartCount/-RestartInterval` is KeepAlive, and a long-lived backend
    additionally needs no execution time limit (Task Scheduler's default kills
    a task after 3 days), no battery gating, and `-StartWhenAvailable`."""
    return _run_op(
        "$ErrorActionPreference = 'Stop'; "
        "$user = $env:USERDOMAIN + '\\' + $env:USERNAME; "
        f"$action = New-ScheduledTaskAction -Execute {_ps_literal(program)} "
        f"-Argument {_ps_literal('-m autowright.main')}; "
        "$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user; "
        "$settings = New-ScheduledTaskSettingsSet -RestartCount 3 "
        "-RestartInterval (New-TimeSpan -Minutes 1) "
        "-ExecutionTimeLimit ([TimeSpan]::Zero) "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
        "-StartWhenAvailable; "
        f"Register-ScheduledTask -TaskName {_ps_literal(LABEL)} -Action $action "
        "-Trigger $trigger -Settings $settings -User $user -Force | Out-Null; "
        f"'{_OK}'"
    )


def _unregister() -> str | None:
    return _run_op(
        "$ErrorActionPreference = 'Stop'; "
        f"Unregister-ScheduledTask -TaskName {_ps_literal(LABEL)} -Confirm:$false; "
        f"'{_OK}'"
    )


def _start_task() -> str | None:
    return _run_op(
        "$ErrorActionPreference = 'Stop'; "
        f"Start-ScheduledTask -TaskName {_ps_literal(LABEL)}; "
        f"'{_OK}'"
    )


def _stop_task() -> str | None:
    """Stop every running instance. A task that is registered but not running
    is not an error — Stop-ScheduledTask is a no-op there — and a task that
    isn't registered at all is silenced too, so install can stop-then-register
    unconditionally (the launchd unload-then-load shape)."""
    return _run_op(
        "$ErrorActionPreference = 'Stop'; "
        f"Stop-ScheduledTask -TaskName {_ps_literal(LABEL)} "
        "-ErrorAction SilentlyContinue; "
        f"'{_OK}'"
    )


def _await_running(want: bool) -> str | None:
    """Poll the task's state until it is (or is no longer) Running. §3: an
    accepted registration proves nothing — success is only ever the state Task
    Scheduler actually reports afterwards."""
    state = ""
    for attempt in range(_POLL_TRIES):
        if attempt:
            time.sleep(_POLL_INTERVAL_S)
        state, err = _query_state()
        if err:
            return err
        if (state == "Running") == want:
            return None
    if state == _ABSENT:
        return "the task is gone from Task Scheduler"
    if want:
        return f"the task is registered but did not start (state {state})"
    return "the task is still running"


def _sweep_strays() -> int:
    """§3 quit-entirely sweep, Windows half: same markers, taskkill tree
    kills (see WindowsProcessControl.kill_matching)."""
    return WindowsProcessControl().kill_matching(paths.sweep_markers())


def _backend_program() -> tuple[str, str | None]:
    """§3 action program: `pythonw.exe` beside this interpreter — it sits next
    to `python.exe` in both the venv (`Scripts\\`) and the flat
    python-build-standalone layouts, and keeps a console window from flashing
    at every logon. A layout without it still registers, on this interpreter,
    with the reason on the result line."""
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")
    try:
        if pythonw.is_file():
            return str(pythonw), None
    except OSError:
        pass
    return str(exe), (f"no pythonw.exe beside {exe.name} — running it directly "
                      "(a console window may flash at logon)")


# ------------------------------------------------------ §3 toast notifier

AUMID = "ai.autowright.app"  # §3 identifiers — the app's AppUserModelID
TOAST_TIMEOUT_S = 10  # §6 notifications are best-effort: never block the caller

# §3 AUMID registration probe. An unpackaged app cannot own an AUMID: Windows
# only accepts one that a Start-menu shortcut carries, which the §3 NSIS
# installer creates (electron-builder stamps the appId onto the shortcut it
# writes). Two signals, either of which means the id exists on this machine:
#
#   • the Start-menu shortcut the installer wrote — per-user first
#     (`perMachine: false`, §3), the all-users menu second so a future
#     machine-wide install probes true as well;
#   • `HKCU\Software\Classes\AppUserModelId\<AUMID>` — the registration an app
#     writes when it wants a display name/icon for its own id.
#
# The shortcut's *contents* are not read (that needs the shell property store,
# and a pywin32 dependency §17 does not carry): presence is the proxy, and a
# toast posted against an id Windows does not know simply never appears —
# which is the same silent degrade as every other failure here. A dev machine
# without the installed app has neither signal, so `notifications` stays false
# there (§2: probed, not assumed).
SHORTCUT_NAME = "Autowright.lnk"  # nsis `shortcutName` in app/package.json
_START_MENU_TAIL = ("Microsoft", "Windows", "Start Menu", "Programs", SHORTCUT_NAME)


def _start_menu_shortcuts() -> list[Path]:
    roots = [os.environ.get("APPDATA"), os.environ.get("PROGRAMDATA")]
    return [Path(r).joinpath(*_START_MENU_TAIL) for r in roots if r]


def _aumid_registered() -> bool:
    """True when this machine knows the §3 AUMID. Never raises — an
    unreadable registry or profile counts as not registered."""
    for lnk in _start_menu_shortcuts():
        try:
            if lnk.is_file():
                return True
        except OSError:
            pass
    try:
        import winreg  # Windows-only: absent on the POSIX test hosts
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            rf"Software\Classes\AppUserModelId\{AUMID}"):
            return True
    except OSError:
        return False


def _toast_script(title: str, body: str) -> str:
    """The §3 toast: a WinRT `ToastNotificationManager` invocation, no extra
    dependency. Text goes in through `CreateTextNode` on the built-in
    ToastText02 template rather than hand-written toast XML, so a title or
    body carrying `<`, `&` or a quote can neither break the document nor
    inject markup — and the script keeps the module's single-quoted-literals
    rule (no double quotes for Windows command-line quoting to mangle)."""
    return (
        "$ErrorActionPreference = 'Stop'; "
        "[Windows.UI.Notifications.ToastNotificationManager, "
        "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null; "
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, "
        "ContentType = WindowsRuntime] | Out-Null; "
        "$xml = [Windows.UI.Notifications.ToastNotificationManager]::"
        "GetTemplateContent("
        "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
        "$text = $xml.GetElementsByTagName('text'); "
        f"$text.Item(0).AppendChild($xml.CreateTextNode({_ps_literal(title)}))"
        " | Out-Null; "
        f"$text.Item(1).AppendChild($xml.CreateTextNode({_ps_literal(body)}))"
        " | Out-Null; "
        "$toast = New-Object Windows.UI.Notifications.ToastNotification $xml; "
        "[Windows.UI.Notifications.ToastNotificationManager]::"
        f"CreateToastNotifier({_ps_literal(AUMID)}).Show($toast)"
    )


class WindowsNotifier:
    """§6 OS notifications on Windows: a PowerShell WinRT toast posted under
    the §3 AUMID. Best-effort exactly like the macOS osascript notifier —
    never raises, never blocks past the timeout, and says nothing about a
    failure: an unregistered AUMID (a dev run) or a disabled notification
    setting simply produces no toast."""

    def post(self, title: str, body: str) -> None:
        try:
            _powershell(_toast_script(title, body), timeout=TOAST_TIMEOUT_S)
        except Exception:  # noqa: BLE001 — best-effort, §6
            pass


# ------------------------------------------------------- §3 .cmd CLI shim

def shim_paths() -> list[Path]:
    """§3 shim location on Windows — user-local only. AUTOWRIGHT_SHIM is the
    §15 test knob (mirrored in electron/main.cjs): it overrides the location."""
    forced = os.environ.get("AUTOWRIGHT_SHIM")
    if forced:
        return [Path(forced)]
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return [base / paths.APP_NAME / "bin" / "autowright.cmd"]


def shim_text() -> str:
    """§3 batch shim for the console interpreter, module form, CRLF (mirrors
    `win32.cjs`'s `shimText`, byte for byte — the shell writes it, install
    heals it, and a whole-file compare decides whether it needs healing).
    `paths.console_python()`, never `sys.executable`: the service runs the
    backend under `pythonw.exe` (§3), and that is also what the shell writes
    its shim from (`backend.json`'s `python` field) — the CLI needs a console,
    and both halves must agree on one exec line or install would heal every
    shim the shell wrote."""
    return (f"@echo off\r\n{SHIM_MARKER}\r\n"
            f'"{paths.console_python()}" -m autowright.cli %*\r\n')


def _heal_one(p: Path) -> str | None:
    """Heal a single candidate location; None when nothing is there. Read as
    bytes: the file is CRLF by contract, and universal-newline text reading
    would make every comparison fail. An undecodable (binary) file can't carry
    the marker: foreign, left alone, never a traceback."""
    try:
        current = p.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if SHIM_MARKER not in current:
        return f"foreign {p} left alone"
    wanted = shim_text()
    if current == wanted:
        return f"CLI at {p}"
    try:
        p.write_bytes(wanted.encode("utf-8"))
        return f"CLI at {p}"
    except OSError as e:
        return (f"CLI shim at {p} not rewritable ({e}) — "
                f"use `{paths.console_python()} -m autowright.cli`")


def _heal_shim() -> str:
    """§3: healing only, never creation. Creating a shim belongs to the
    Electron shell's cli-install flow. Here: a shim that is ours and points at
    another interpreter (moved install, dev↔prod switch, update) is rewritten
    in place — a user-owned file rewrites without a directory write."""
    notes = [n for n in (_heal_one(p) for p in shim_paths()) if n]
    if not notes:
        return f"CLI not installed — use `{paths.console_python()} -m autowright.cli`"
    return " · ".join(notes)


def _remove_shim() -> str | None:
    """Delete the shim only when the marker says it's ours (§3). A failed
    delete goes on the result line instead of raising."""
    notes = []
    for p in shim_paths():
        try:
            if SHIM_MARKER not in p.read_bytes().decode("utf-8"):
                continue
        except (OSError, UnicodeDecodeError):
            continue
        try:
            p.unlink()
        except OSError as e:
            notes.append(f"CLI shim left at {p} — couldn't delete it ({e})")
    return " · ".join(notes) if notes else None


def _port_note() -> str:
    """The §3 discovery port, for the status line. A SIGKILL'd backend leaves a
    stale (possibly truncated) backend.json — status must report, not crash."""
    bj = paths.backend_json()
    if not bj.exists():
        return ""
    try:
        return f" · port {json.loads(bj.read_text())['port']}"
    except (OSError, ValueError, KeyError, TypeError):
        return " · stale backend.json"


class WindowsService:
    """§3 service lifecycle on Windows: the `ai.autowright.backend` Task
    Scheduler task, registered through the PowerShell ScheduledTasks cmdlets.

    Task Scheduler is the launchd equivalent: logon trigger == RunAtLoad,
    restart-on-failure == KeepAlive, one registered instance == launchd's
    single-instance guarantee. Every verb answers a §3-style result line, so
    `service.result_code` maps success and failure identically to macOS."""

    def install(self) -> str:
        program, program_note = _backend_program()
        _stop_task()  # unload-then-load: never leave the old instance running
        if err := _register(program):
            return f"install failed: {err}"
        if err := _start_task():
            return f"install failed: {err}"
        if err := _await_running(True):
            return f"install failed: {err}"
        notes = [f"installed and started (Task Scheduler task {LABEL})"]
        if program_note:
            notes.append(program_note)
        notes.append(_heal_shim())
        return " · ".join(notes)

    def uninstall(self) -> str:
        state, err = _query_state()
        if err:
            return f"uninstall failed: {err}"
        if state == _ABSENT:
            out = "service was not installed"
        else:
            _stop_task()  # best effort — unregistering kills the instance anyway
            if err := _unregister():
                return f"uninstall failed: {err}"
            out = "service stopped and unregistered"
        shim_note = _remove_shim()
        return f"{out} · {shim_note}" if shim_note else out

    def status(self) -> str:
        state, err = _query_state()
        if err:
            return f"status failed: {err}"
        if state == _ABSENT:
            return "not installed"
        if state == "Running":
            return f"active (task running){_port_note()}"
        # §3: a registered task that isn't running is stopped on purpose
        # (`service stop`), not "not installed" — and not a failure.
        return "stopped (task present) — returns at next logon or app launch"

    def stop(self) -> str:
        """§3 quit-entirely backend half: stop the task, keep it registered,
        then sweep stray processes — the graceful pass never runs on a Windows
        stop, so the sweep is what clears the own-process-group children the
        tree kill orphans (pip, executors, stray CLI invocations)."""
        state, err = _query_state()
        if err:
            return f"stop failed: {err}"
        if state == _ABSENT:
            # §3: no registration, but strays can still be alive — sweep them.
            # Either way a successful, idempotent stop (mirrors macOS).
            n = _sweep_strays()
            if n:
                return f"stopped — service was not installed; ended {n} lingering process(es)"
            return "already stopped — service not installed, nothing was running"
        if err := _stop_task():
            return f"stop failed: {err}"
        if err := _await_running(False):
            return f"stop failed: {err}"
        n = _sweep_strays()
        note = f" · ended {n} lingering process(es)" if n else ""
        return f"stopped — returns at next logon or app launch{note}"

    def restart(self) -> str:
        state, err = _query_state()
        if err:
            return f"restart failed: {err}"
        if state == _ABSENT:
            return "not installed — use `autowright service install` first"
        if err := _stop_task():
            return f"restart failed: {err}"
        if err := _await_running(False):
            return f"restart failed: {err}"
        if err := _start_task():
            return f"restart failed: {err}"
        if err := _await_running(True):
            return f"restart failed: {err}"
        return "restarted"


def build(_os_display: str) -> Platform:
    # The display name only fed the degraded service placeholder; the signature
    # stays uniform for `current()` now that the real Task Scheduler manager
    # has landed.
    #
    # §3: the notifier is always composed — posting is harmless where the AUMID
    # is unknown — but the *capability* is probed, never assumed, so clients
    # gating on §19 /health only offer notifications where a toast can actually
    # appear (the packaged build's environment).
    return Platform(
        os_token="windows",
        service=WindowsService(),
        notifier=WindowsNotifier(),
        power=WindowsPower(),
        processes=WindowsProcessControl(),
        capabilities=Capabilities(imessage=False,
                                  notifications=_aumid_registered(),
                                  keep_awake=True, service=True,
                                  agent_install=False),
    )
