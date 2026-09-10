"""launchd LaunchAgent management (§3): install/uninstall/status/restart.

This module is the macOS ServiceManager implementation of the §2 platform
layer (platform/darwin.py delegates here); ACTIONS below routes through the
composed platform, so on an OS with no service manager the same entry points
answer a plain "not supported" failure line instead of crashing on a missing
launchctl.

Writes a per-user plist to ~/Library/LaunchAgents/ pointing at this interpreter.
The one registration path: the app's ensure-backend step runs
`python -m autowright.service install` at launch (the __main__ dispatch below —
the app never invokes the CLI), and headless setups run the same functions via
the §20 `autowright service` wrapper — install rewrites and adopts an existing
registration.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import time
from pathlib import Path

from . import paths, platform

LABEL = "ai.autowright.backend"
SHIM_MARKER = "# autowright CLI shim"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def shim_paths() -> list[Path]:
    """§3 shim location — user-local only. AUTOWRIGHT_SHIM is the §15 test
    knob (mirrored in electron/main.cjs): it overrides the location."""
    forced = os.environ.get("AUTOWRIGHT_SHIM")
    if forced:
        return [Path(forced)]
    return [Path.home() / ".local" / "bin" / "autowright"]


def shim_text() -> str:
    """Shim contents for this interpreter, module form (pip's bin/ entry
    scripts carry staging-path shebangs)."""
    return (f'#!/bin/sh\n{SHIM_MARKER}\n'
            f'exec "{sys.executable}" -m autowright.cli "$@"\n')


def _heal_one(p: Path) -> str | None:
    """Heal a single candidate location; None when nothing is there. An
    undecodable (binary) file can't carry the marker: foreign, left alone,
    never a traceback (same tolerance as _remove_shim)."""
    try:
        current = p.read_text()
    except (OSError, UnicodeDecodeError):
        return None
    if SHIM_MARKER not in current:
        return f"foreign {p} left alone"
    wanted = shim_text()
    if current == wanted:
        return f"CLI at {p}"
    try:
        p.write_text(wanted)
        p.chmod(0o755)
        return f"CLI at {p}"
    except OSError as e:
        return (f"CLI shim at {p} not rewritable ({e}) — "
                f"use `{sys.executable} -m autowright.cli`")


def _heal_shim() -> str:
    """§3: healing only, never creation. Creating a shim belongs to the
    Electron shell's cli-install flow (silent, ~/.local/bin). Here: a shim
    that is ours and points at another interpreter (moved bundle, dev↔prod
    switch, update) is rewritten in place — sudo-free, since a user-owned
    file rewrites without a directory write."""
    notes = [n for n in (_heal_one(p) for p in shim_paths()) if n]
    if not notes:
        return f"CLI not installed — use `{sys.executable} -m autowright.cli`"
    return " · ".join(notes)


def _remove_shim() -> str | None:
    """Delete the shim only when the marker says it's ours (§3). A failed
    delete goes on the result line instead of raising."""
    notes = []
    for p in shim_paths():
        try:
            if SHIM_MARKER not in p.read_text():
                continue
        except (OSError, UnicodeDecodeError):
            continue
        try:
            p.unlink()
        except OSError as e:
            notes.append(f"CLI shim left at {p} — couldn't delete it ({e})")
    return " · ".join(notes) if notes else None


LAUNCHCTL_TIMEOUT_S = 30
_TIMED_OUT = "launchctl timed out"
# The §3 registration polls are bounded by wall-clock deadlines, never a poll
# count: a slow `launchctl print` must not stretch one verb into minutes.
_UNLOAD_DEADLINE_S = 10.0
_SWEEP_DEADLINE_S = 5.0  # the stop's second window, after the stray sweep
_POLL_INTERVAL_S = 0.25


class _TimedOut:
    """Stand-in for a `launchctl` call that never returned (§3): a wedged
    launchctl must not hang the caller — the app's ensure-backend step waits on
    it — so a timeout reads as a plain non-zero result with a plain-word
    message, never a TimeoutExpired traceback."""

    returncode = 1
    stdout = ""
    stderr = _TIMED_OUT


def _launchctl(*args: str) -> subprocess.CompletedProcess | _TimedOut:
    """Every `launchctl` call goes through here: captured, text, time-boxed."""
    try:
        return subprocess.run(["launchctl", *args], capture_output=True,
                              text=True, timeout=LAUNCHCTL_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return _TimedOut()


def _registered() -> bool:
    """Whether launchd currently knows the job (running or not)."""
    return _launchctl("print", f"gui/{os.getuid()}/{LABEL}").returncode == 0


def _unload(p: Path) -> None:
    """Stop the agent whatever the bootstrap context: modern `bootout
    gui/<uid>` first, legacy `unload` as the fallback. Booting out a running
    job is asynchronous — wait for launchd to finish removing it, or the
    immediate re-bootstrap in install()/restart() races the teardown, fails,
    and leaves no registration at all (§3)."""
    if _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}").returncode != 0:
        _launchctl("unload", str(p))
    deadline = time.monotonic() + _UNLOAD_DEADLINE_S
    while True:
        if not _registered():
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(_POLL_INTERVAL_S)


def _load(p: Path) -> str | None:
    """Start the agent; returns an error string or None. `launchctl load`
    fails from a non-Aqua bootstrap context (SSH — the §3 headless path), so
    try modern `bootstrap gui/<uid>` first and legacy `load` second. Either
    can exit 0 without actually loading (legacy `load` swallows errors), so
    success is only ever the job existing in launchd afterwards (§3) — never
    report an unregistered service as installed."""
    r = _launchctl("bootstrap", f"gui/{os.getuid()}", str(p))
    if r.returncode != 0:
        r2 = _launchctl("load", str(p))
        if r2.returncode != 0:
            return (r.stderr.strip() or r2.stderr.strip()
                    or f"launchctl exited {r.returncode}")
    if not _registered():
        return "launchctl accepted the plist but the job is not registered"
    return None


def install() -> str:
    plist = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, "-m", "autowright.main"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(paths.logs_dir() / "backend.out.log"),
        "StandardErrorPath": str(paths.logs_dir() / "backend.err.log"),
    }
    paths.logs_dir().mkdir(parents=True, exist_ok=True)  # launchd won't create it for the log paths
    p = plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        plistlib.dump(plist, f)
    _unload(p)
    if err := _load(p):
        return f"install failed: {err}"
    return f"installed and started ({p}) · {_heal_shim()}"


def uninstall() -> str:
    p = plist_path()
    _unload(p)
    shim_note = _remove_shim()
    if p.exists():
        p.unlink()
        out = "service unloaded and removed"
    else:
        out = "service was not installed"
    return f"{out} · {shim_note}" if shim_note else out


def status() -> str:
    r = _launchctl("list")
    for line in r.stdout.splitlines():
        if LABEL in line:
            pid = line.split()[0]
            alive = pid != "-"
            bj = paths.backend_json()
            port = ""
            if bj.exists():
                import json

                # A SIGKILL'd backend leaves a stale (possibly truncated)
                # backend.json — status must report, not crash.
                try:
                    port = f" · port {json.loads(bj.read_text())['port']}"
                except (OSError, ValueError, KeyError, TypeError):
                    port = " · stale backend.json"
            return ("active" if alive else "loaded, not active") + f" (pid {pid}){port}"
    # §3: an unloaded job with its plist still on disk is "stopped", not
    # "not installed" — it returns at next login or `service install`.
    if plist_path().exists():
        return "stopped (plist present) — returns at next login or app launch"
    return "not installed"


def _sweep_strays() -> int:
    """§3 quit-entirely sweep: end every lingering process of ours (executor
    groups, pip children, stray CLI invocations, a backend that outlived the
    unload wait) so the app bundle deletes cleanly right after a stop. The
    count feeds the result line's informational note."""
    return platform.current().processes.kill_matching(paths.sweep_markers())


def stop() -> str:
    """§3 quit-entirely backend half: unload the job and sweep stray
    processes, keep plist and shim."""
    p = plist_path()
    if not p.exists():
        # §3: no registration, but a directly-spawned backend (isolated dev)
        # or strays can still be alive — sweep them. Either way this is a
        # successful stop: stop is idempotent, and "nothing registered,
        # nothing running" already satisfies it (it must not abort the §4.9
        # QUIT/RESET flows on a machine whose registration is missing).
        n = _sweep_strays()
        if n:
            return f"stopped — service was not installed; ended {n} lingering process(es)"
        return "already stopped — service not installed, nothing was running"
    _unload(p)
    n = _sweep_strays()
    if n and _registered():
        # bootout already requested removal, so KeepAlive cannot respawn the
        # job — the sweep's kill is what lets launchd finish a removal that
        # outlived _unload's wait. Give it a short second window.
        deadline = time.monotonic() + _SWEEP_DEADLINE_S
        while True:
            if not _registered():
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(_POLL_INTERVAL_S)
    if _registered():
        return "stop failed: launchd still reports the job"
    note = f" · ended {n} lingering process(es)" if n else ""
    return f"stopped — returns at next login or app launch{note}"


def restart() -> str:
    p = plist_path()
    if not p.exists():
        return "not installed — use `autowright service install` first"
    _unload(p)
    err = _load(p)
    return "restarted" if err is None else f"restart failed: {err}"


def _dispatch(action: str) -> str:
    """§2 platform layer: route an action through the composed ServiceManager —
    launchd (the functions above) on macOS, the fallback's plain "not
    supported on <OS> yet" failure line elsewhere."""
    return getattr(platform.current().service, action)()


ACTIONS = {a: (lambda a=a: _dispatch(a))
           for a in ("install", "uninstall", "status", "restart", "stop")}


def result_code(out: str) -> int:
    """§3 exit code for an action's result line: 1 for a failed action or a
    missing install, 0 otherwise. Shim notes after "·" are informational.
    Shared by main() below and the §20 `autowright service` wrapper, so both
    entry points exit alike."""
    head = out.split("·")[0]
    return 1 if ("failed" in head or head.startswith("not installed")) else 0


def main(argv: list[str]) -> int:
    """`python -m autowright.service <action>` — the §3 registration entry the
    app's ensure-backend step execs (the UI never invokes the CLI). The §20
    `autowright service` group wraps the same ACTIONS."""
    # §2 pipe-encoding contract: result lines carry ·/— and the Electron
    # ensure-backend step captures them as UTF-8; the Windows locale codec
    # (cp1252) would mojibake, so pin the stream at the entry.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    if len(argv) != 1 or argv[0] not in ACTIONS:
        print(f"usage: python -m autowright.service {{{'|'.join(ACTIONS)}}}",
              file=sys.stderr)
        return 2
    out = ACTIONS[argv[0]]()
    print(out)
    return result_code(out)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
