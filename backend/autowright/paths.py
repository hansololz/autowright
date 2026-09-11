"""Filesystem locations (§5). Two per-OS roots (data + logs) picked from the
platform token — the §5 root table; every other location hangs off them.
AUTOWRIGHT_HOME overrides both for dev/tests (logs go to <home>/logs).
All three builds ship (macOS, Linux, Windows), so every row of the table is
live — the call sites never branch on the OS themselves. The
Electron shell resolves the same two roots in electron/platform/ — the tables
must never drift (§15 guard)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "Autowright"


def current_os() -> str:
    """§5.1 platform token (macos | windows | linux) — the archive manifest's
    `os` vocabulary, compared against §4.1 `originOs`."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


OS_DISPLAY = {"macos": "macOS", "windows": "Windows", "linux": "Linux"}


def os_display_name(token: str | None) -> str:
    """§4.1 display form of a §5.1 platform token — "macOS" / "Windows" /
    "Linux". An unrecognized stored token shows verbatim. Shared by every
    surface that names a platform (the §4.1 `os-mismatch` label, the §20 CLI
    import summary), so they never drift apart."""
    return OS_DISPLAY.get(token or "", token or "")


def machine_noun(token: str | None = None) -> str:
    """§9 per-OS copy rule: the noun backend-served copy calls the machine —
    "Mac" on macOS, "PC" on Windows and Linux alike. Defaults to the running
    platform; the backend half of the renderer's `platformCopy` helper."""
    return "Mac" if (token or current_os()) == "macos" else "PC"


def secret_store_name(token: str | None = None) -> str:
    """§9 per-OS copy rule: the §4.8 secret store's user-facing name —
    "Keychain" on macOS, "Credential Manager" on Windows, "system keyring" on
    Linux (all reached through the same `keyring` code). Defaults to the
    running platform."""
    names = {"windows": "Credential Manager", "linux": "system keyring"}
    return names.get(token or current_os(), "Keychain")


def tray_surface_name(token: str | None = None) -> str | None:
    """§9 per-OS copy rule: the §13 surface's own name in running prose —
    "menu bar" on macOS, "tray" on Windows, and None on Linux, which ships no
    tray surface at all (§13 2026-09-01) so callers drop the clause instead of
    naming one. The backend half of the renderer table's `menuBar` entry."""
    os_token = token or current_os()
    if os_token == "linux":
        return None
    return "menu bar" if os_token == "macos" else "tray"


def tray_trigger_label(token: str | None = None) -> str:
    """§4.5/§9: the display label of a `menubar` execution trigger — "Menu bar"
    on macOS, "Tray" everywhere else (a `menubar` record only reaches Linux
    inside a data folder carried over from another OS, and "Tray" is the
    nearest name it has). Derived at serialization from the running platform,
    never stored (§4.5)."""
    return "Menu bar" if (token or current_os()) == "macos" else "Tray"


def console_python() -> str:
    """§2 console-interpreter rule: the interpreter for Python children the
    backend spawns (the §6.1 executor, §6.2 pip) and for the §3 discovery
    `python` field. On Windows the service runs the backend under
    `pythonw.exe` (§3 — no console window); a Python child spawned as
    `pythonw.exe` would have no console at all, so its own console-subsystem
    children (a step's tool calls, pip's helpers) would each open a visible
    terminal window instead of inheriting the §2 hidden console — publish the
    `python.exe` sitting beside it when one exists. Everywhere else (and when
    no sibling exists) this is just `sys.executable`."""
    exe = Path(sys.executable)
    if current_os() == "windows" and exe.name.lower() == "pythonw.exe":
        console = exe.with_name("python.exe")
        try:
            if console.is_file():
                return str(console)
        except OSError:
            pass
    return sys.executable


def sweep_markers() -> list[str]:
    """§3 quit-entirely sweep: command-line substrings that identify our own
    processes. Module invocations (`-m autowright.`) survive interpreter-path
    resolution in `ps` (a dev venv python shows its resolved framework binary,
    never the venv path), the interpreter itself covers the packaged app's
    argv[0] (pip children included), its `bin/autowright*` siblings cover the
    dev entry-point scripts, and the Windows console sibling covers the
    pythonw/python pair. Never the realpath'd interpreter: in dev that is a
    shared system python and would match unrelated processes."""
    exe = Path(sys.executable)
    markers = ["-m autowright.", str(exe), str(exe.parent / "autowright")]
    if console_python() != str(exe):
        markers.append(console_python())
    return markers


def _default_data_root() -> Path:
    """§5 per-OS data root (the platform-token row of the §5 table)."""
    token = current_os()
    if token == "macos":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    if token == "windows":
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / APP_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "autowright"


def _default_logs_root() -> Path:
    """§5 per-OS logs root (the platform-token row of the §5 table)."""
    token = current_os()
    if token == "macos":
        return Path.home() / "Library" / "Logs" / APP_NAME
    if token == "windows":
        return _default_data_root() / "Logs"
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "autowright" / "log"


def app_support() -> Path:
    env = os.environ.get("AUTOWRIGHT_HOME")
    if env:
        return Path(env).expanduser()
    return _default_data_root()


def automations_dir() -> Path:
    return app_support() / "automations"


def settings_file() -> Path:
    return app_support() / "settings.yaml"


def agents_file() -> Path:
    return app_support() / "agents.yaml"


def secrets_file() -> Path:
    return app_support() / "secrets.yaml"


def backend_json() -> Path:
    return app_support() / "backend.json"


def default_data_path() -> Path:
    return app_support() / "executions"


def pending_draft_dir() -> Path:
    """§4.4: the single pending create-mode draft slot — created when the
    create flow opens, deleted when Create or Start over settles it."""
    return app_support() / "draft"


def marketplace_dir() -> Path:
    """§22.2: the marketplace sources store - `sources.yaml` plus one directory
    per source (its cached `marketplace-catalog.yaml` and `images/`). Created on
    demand by the first write, so an install that never opens the page holds no
    such directory."""
    return app_support() / "marketplace"


def import_spool_dir() -> Path:
    """§5.2: parked import archives, one file per preview token. Transient and
    disposable — deleted on confirm/eviction/expiry, and the whole directory is
    cleared at backend startup (a crashed process leaves its files behind)."""
    return app_support() / "import-spool"


def harness_workspace(provider_id: str) -> Path:
    """Empty per-provider cwd for provider CLI children — keeps their startup
    project scans out of TCC-protected folders (§6), so macOS never prompts.
    Created on demand by `harness._neutral_cwd`."""
    return app_support() / "harness" / provider_id / "workspace"


def harness_scratch(provider_id: str) -> Path:
    """§5/§8: parent of the per-call scratch dirs a file-writing drafting call
    uses as its cwd — the agent writes its response documents there and the §8
    watcher collects them. Sibling of `harness_workspace`."""
    return app_support() / "harness" / provider_id / "scratch"


def logs_dir() -> Path:
    env = os.environ.get("AUTOWRIGHT_HOME")
    if env:
        return Path(env).expanduser() / "logs"
    return _default_logs_root()


def app_log() -> Path:
    return logs_dir() / "app.log"


def ensure_dirs() -> None:
    app_support().mkdir(parents=True, exist_ok=True)
    automations_dir().mkdir(parents=True, exist_ok=True)
    default_data_path().mkdir(parents=True, exist_ok=True)
    logs_dir().mkdir(parents=True, exist_ok=True)
