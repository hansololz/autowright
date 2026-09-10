"""§2 platform layer — the Linux build.

Composes the Linux implementations: the §3 systemd user-unit service manager
(`SystemdService`, the code lives here), the `notify-send` notifier, the
`systemd-inhibit` power assertions (`SystemdInhibitPower`), and shared POSIX
process control. Capabilities are probed at composition, never assumed: a
host missing `systemctl`, `notify-send`, or `systemd-inhibit` composes the
degraded fallback piece for that seam (plain words / silent no-op — never a
crash on a missing tool) and answers `false` for the §19 capability flag.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .. import paths
from . import fallback, posixproc
from .base import Capabilities, Platform

LABEL = "ai.autowright.backend"
UNIT_NAME = f"{LABEL}.service"

SYSTEMCTL_TIMEOUT_S = 30
_TIMED_OUT = "systemctl timed out"
# The §3 active-state poll: a 10 s wall-clock deadline, the same window as
# launchd's bootout wait — never a probe count, so a slow but answering
# `systemctl` can't stretch one verb into minutes.
_POLL_DEADLINE_S = 10.0
_POLL_INTERVAL_S = 0.25


def unit_path() -> Path:
    """§3: the per-user unit file — systemd reads user units from
    `$XDG_CONFIG_HOME/systemd/user` (default `~/.config/systemd/user`)."""
    cfg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(cfg) if cfg else Path.home() / ".config"
    return base / "systemd" / "user" / UNIT_NAME


def _unit_escape(s: str) -> str:
    """`%` is a systemd specifier introducer anywhere in a unit file."""
    return s.replace("%", "%%")


def _unit_quote(s: str) -> str:
    """One double-quoted ExecStart word, systemd quoting rules."""
    return '"' + _unit_escape(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def unit_text() -> str:
    """§3 unit contents for this interpreter. `Restart=always` +
    `StartLimitIntervalSec=0` is KeepAlive's never-give-up restart,
    `RestartSec=2` its throttle; `WantedBy=default.target` is RunAtLoad.
    `append:` log routing (systemd ≥ 240) matches launchd's file capture, so
    the §9.3 overlay and `main.py`'s startup trim work unchanged."""
    logs = paths.logs_dir()
    return (
        "[Unit]\n"
        "Description=Autowright backend\n"
        "StartLimitIntervalSec=0\n"
        "\n"
        "[Service]\n"
        f"ExecStart={_unit_quote(sys.executable)} -m autowright.main\n"
        "Restart=always\n"
        "RestartSec=2\n"
        f"StandardOutput=append:{_unit_escape(str(logs / 'backend.out.log'))}\n"
        f"StandardError=append:{_unit_escape(str(logs / 'backend.err.log'))}\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


class _TimedOut:
    """Stand-in for a `systemctl` call that never returned (§3): a wedged
    systemctl must not hang the caller — the app's ensure-backend step waits
    on it — so a timeout reads as a plain non-zero result with a plain-word
    message, never a TimeoutExpired traceback."""

    returncode = 1
    stdout = ""
    stderr = _TIMED_OUT


def _systemctl(*args: str) -> subprocess.CompletedProcess | _TimedOut:
    """Every `systemctl --user` call goes through here: captured, text,
    time-boxed. The §15 suites swap this out for a recorder."""
    try:
        return subprocess.run(["systemctl", "--user", *args],
                              capture_output=True, text=True,
                              timeout=SYSTEMCTL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return _TimedOut()


def _run_ok(*args: str) -> str | None:
    """Run one systemctl verb; None on success, a plain-word error otherwise."""
    r = _systemctl(*args)
    if r.returncode == 0:
        return None
    return (r.stderr.strip() or r.stdout.strip()
            or f"systemctl {args[0]} exited {r.returncode}")


def _active_state() -> tuple[str, str | None]:
    """The unit's `is-active` answer (state, error). `is-active` exits
    non-zero for every state but `active`, so the state is the printed word,
    never the exit code."""
    r = _systemctl("is-active", UNIT_NAME)
    if isinstance(r, _TimedOut):
        return "", _TIMED_OUT
    return (r.stdout.strip() or r.stderr.strip() or "unknown"), None


def _await_active(want: bool) -> str | None:
    """Poll until the unit is (or is no longer) active. §3: an accepted
    `systemctl` invocation proves nothing — success is only ever the state
    systemd actually reports afterwards."""
    state = ""
    deadline = time.monotonic() + _POLL_DEADLINE_S
    while True:
        state, err = _active_state()
        if err:
            return err
        if (state == "active") == want:
            return None
        if time.monotonic() >= deadline:
            break
        time.sleep(_POLL_INTERVAL_S)
    if want:
        return f"the unit is enabled but did not start (state {state})"
    return "the unit is still running"


def _port_note() -> str:
    """The §3 discovery port, for the status line. A SIGKILL'd backend leaves
    a stale (possibly truncated) backend.json — status must report, not
    crash."""
    bj = paths.backend_json()
    if not bj.exists():
        return ""
    try:
        return f" · port {json.loads(bj.read_text())['port']}"
    except (OSError, ValueError, KeyError, TypeError):
        return " · stale backend.json"


def _sweep_strays() -> int:
    """§3 quit-entirely sweep, Linux half: same markers, shared POSIX
    group-kill semantics."""
    return posixproc.PosixProcessControl().kill_matching(paths.sweep_markers())


class SystemdService:
    """§3 service lifecycle on Linux: the `ai.autowright.backend.service`
    systemd user unit, driven through `systemctl --user`.

    systemd is the launchd equivalent: an enabled `WantedBy=default.target`
    unit == RunAtLoad, `Restart=always` == KeepAlive, one unit == launchd's
    single-instance guarantee. Every verb answers a §3-style result line, so
    `service.result_code` maps success and failure identically to macOS."""

    def install(self) -> str:
        # systemd creates the append: log files but not their directory (the
        # same rule as launchd's log paths).
        paths.logs_dir().mkdir(parents=True, exist_ok=True)
        p = unit_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(unit_text())
        except OSError as e:
            return f"install failed: couldn't write {p} ({e})"
        if err := _run_ok("daemon-reload"):
            return f"install failed: {err}"
        if err := _run_ok("enable", UNIT_NAME):
            return f"install failed: {err}"
        # Restart, not start: a rewritten unit (moved install, dev↔prod
        # switch, update) must always be adopted — the unload-then-load rule
        # in systemd clothes.
        if err := _run_ok("restart", UNIT_NAME):
            return f"install failed: {err}"
        if err := _await_active(True):
            return f"install failed: {err}"
        from .. import service

        return f"installed and started ({p}) · {service._heal_shim()}"

    def uninstall(self) -> str:
        from .. import service

        p = unit_path()
        if not p.exists():
            out = "service was not installed"
        else:
            _systemctl("disable", "--now", UNIT_NAME)  # best effort
            try:
                p.unlink()
            except OSError as e:
                return f"uninstall failed: couldn't delete {p} ({e})"
            _systemctl("daemon-reload")
            out = "service stopped and unregistered"
        shim_note = service._remove_shim()
        return f"{out} · {shim_note}" if shim_note else out

    def status(self) -> str:
        # §3: an inactive unit whose file is still on disk is "stopped", not
        # "not installed" — it returns at next login or `service install`.
        if not unit_path().exists():
            return "not installed"
        state, err = _active_state()
        if err:
            return f"status failed: {err}"
        if state == "active":
            r = _systemctl("show", UNIT_NAME, "-p", "MainPID", "--value")
            pid = r.stdout.strip() if r.returncode == 0 else ""
            head = f"active (pid {pid})" if pid and pid != "0" else "active (unit running)"
            return head + _port_note()
        return "stopped (unit present) — returns at next login or app launch"

    def stop(self) -> str:
        """§3 quit-entirely backend half: stop the unit, keep it enabled,
        then sweep stray processes (directly-spawned dev backends, stray CLI
        invocations — whatever lives outside the unit's cgroup)."""
        if not unit_path().exists():
            # §3: no registration, but strays can still be alive — sweep them.
            # Either way a successful, idempotent stop (mirrors macOS).
            n = _sweep_strays()
            if n:
                return f"stopped — service was not installed; ended {n} lingering process(es)"
            return "already stopped — service not installed, nothing was running"
        if err := _run_ok("stop", UNIT_NAME):
            return f"stop failed: {err}"
        if err := _await_active(False):
            return f"stop failed: {err}"
        n = _sweep_strays()
        note = f" · ended {n} lingering process(es)" if n else ""
        return f"stopped — returns at next login or app launch{note}"

    def restart(self) -> str:
        if not unit_path().exists():
            return "not installed — use `autowright service install` first"
        if err := _run_ok("restart", UNIT_NAME):
            return f"restart failed: {err}"
        if err := _await_active(True):
            return f"restart failed: {err}"
        return "restarted"


class NotifySendNotifier:
    """§6 notifications via `notify-send` (libnotify), the darwin contract:
    best-effort — never raises, never blocks past the timeout."""

    def post(self, title: str, body: str) -> None:
        try:
            subprocess.run(
                ["notify-send", "--app-name=Autowright", "--", title, body],
                capture_output=True,
                timeout=10,
            )
        except Exception:  # noqa: BLE001
            pass


class SystemdInhibitPower:
    """§3/§4.9 idle-sleep assertions: each is its own `systemd-inhibit
    --what=idle` subprocess wrapping `tail --pid=<backend pid> -f /dev/null`,
    so the inhibitor lock is released the moment the backend dies — the same
    no-orphan guarantee `caffeinate -w` gives on macOS. `--what=idle` only:
    display sleep stays allowed. Same shape as awake.py; nothing here ever
    raises."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None

    @staticmethod
    def _spawn(why: str) -> subprocess.Popen:
        return subprocess.Popen(
            ["systemd-inhibit", "--what=idle", "--who=Autowright",
             f"--why={why}", "tail", f"--pid={os.getpid()}", "-f", "/dev/null"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def reconcile(self, enabled: bool) -> None:
        """The permanent §4.9 assertion — idempotent, called at backend boot
        and from every PATCH /settings."""
        with self._lock:
            if enabled:
                if self._proc is not None and self._proc.poll() is None:
                    return
                try:
                    self._proc = self._spawn("keep-awake setting")
                except Exception:  # noqa: BLE001
                    self._proc = None
            elif self._proc is not None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=5)  # reap — no zombie until GC
                except Exception:  # noqa: BLE001
                    pass
                self._proc = None

    def hold_execution(self) -> Callable[[], None]:
        """§3 per-execution hold: an inhibitor of its own for the duration of
        one execution. Returns the release callable — safe to call twice; a
        spawn that fails simply holds nothing."""
        try:
            proc = self._spawn("active execution")
        except Exception:  # noqa: BLE001
            return lambda: None

        released = threading.Event()

        def release() -> None:
            if released.is_set():
                return
            released.set()
            try:
                proc.terminate()
                proc.wait(timeout=5)  # reap — no zombie until GC
            except Exception:  # noqa: BLE001
                pass

        return release


def build() -> Platform:
    has_systemctl = shutil.which("systemctl") is not None
    has_notify_send = shutil.which("notify-send") is not None
    has_inhibit = shutil.which("systemd-inhibit") is not None
    return Platform(
        os_token="linux",
        service=SystemdService() if has_systemctl
        else fallback.UnsupportedService(paths.os_display_name("linux")),
        notifier=NotifySendNotifier() if has_notify_send else fallback.NullNotifier(),
        power=SystemdInhibitPower() if has_inhibit else fallback.NullPower(),
        processes=posixproc.PosixProcessControl(),
        capabilities=Capabilities(imessage=False,
                                  notifications=has_notify_send,
                                  keep_awake=has_inhibit,
                                  service=has_systemctl,
                                  agent_install=True),
    )
