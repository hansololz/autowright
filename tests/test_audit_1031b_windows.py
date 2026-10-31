"""Audit 2026-10-31 second sweep, Windows platform half (spec/packaging.md §3):
the .cmd CLI shim writes an interpreter under %LOCALAPPDATA% relative to the
variable (cmd reads batch files in the OEM code page, so a literal UTF-8
profile path was misread), byte-identical to win32.cjs's shimText; and the
Task Scheduler action carries the interpreter-isolation pair as `-s -P`."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from autowright import paths
from autowright.platform import windows

WIN32_CJS = Path(__file__).resolve().parent.parent / "app" / "electron" / "platform" / "win32.cjs"

JOSE_LOCAL = "C:\\Users\\José\\AppData\\Local"
JOSE_PYTHON = JOSE_LOCAL + "\\Programs\\Autowright\\resources\\python\\python.exe"


def _shim_for(monkeypatch, python: str, local: str | None) -> str:
    monkeypatch.setattr(paths, "console_python", lambda: python)
    if local is None:
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
    else:
        monkeypatch.setenv("LOCALAPPDATA", local)
    return windows.shim_text()


def test_shim_under_localappdata_is_written_relative_to_the_variable(monkeypatch):
    assert _shim_for(monkeypatch, JOSE_PYTHON, JOSE_LOCAL) == (
        "@echo off\r\nrem autowright CLI shim\r\n"
        '"%LOCALAPPDATA%\\Programs\\Autowright\\resources\\python\\python.exe"'
        " -m autowright.cli %*\r\n")


def test_shim_prefix_is_case_insensitive_and_ignores_a_trailing_separator(monkeypatch):
    text = _shim_for(monkeypatch, JOSE_LOCAL + "\\Programs\\py.exe",
                     "c:\\users\\josé\\appdata\\local\\")
    assert '"%LOCALAPPDATA%\\Programs\\py.exe" -m autowright.cli %*' in text


def test_shim_outside_localappdata_stays_literal(monkeypatch):
    program_files = "C:\\Program Files\\Autowright\\resources\\python\\python.exe"
    assert (f'"{program_files}" -m autowright.cli %*'
            in _shim_for(monkeypatch, program_files, JOSE_LOCAL))
    # Only at a separator: a sibling folder sharing the prefix is not under it.
    low = "C:\\Users\\José\\AppData\\LocalLow\\py.exe"
    assert f'"{low}" -m autowright.cli %*' in _shim_for(monkeypatch, low, JOSE_LOCAL)
    # No LOCALAPPDATA at all: literal, as before.
    assert f'"{JOSE_PYTHON}" -m autowright.cli %*' in _shim_for(monkeypatch, JOSE_PYTHON, None)


def test_shim_doubles_a_percent_in_the_rest_never_the_variable(monkeypatch):
    local = "C:\\Users\\100%dev\\AppData\\Local"
    text = _shim_for(monkeypatch, local + "\\Programs\\50%\\python.exe", local)
    assert '"%LOCALAPPDATA%\\Programs\\50%%\\python.exe" -m autowright.cli %*' in text
    # Outside the variable the whole path still doubles.
    assert ('"C:\\100%%\\python.exe" -m autowright.cli %*'
            in _shim_for(monkeypatch, "C:\\100%\\python.exe", local))


CASES = [
    (JOSE_PYTHON, JOSE_LOCAL),
    (JOSE_LOCAL + "\\Programs\\py.exe", "c:\\users\\josé\\appdata\\local\\"),
    ("C:\\Program Files\\Autowright\\resources\\python\\python.exe", JOSE_LOCAL),
    ("C:\\Users\\José\\AppData\\LocalLow\\py.exe", JOSE_LOCAL),
    ("C:\\Users\\100%dev\\AppData\\Local\\Programs\\50%\\python.exe",
     "C:\\Users\\100%dev\\AppData\\Local"),
    ("C:\\100%\\python.exe", None),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
@pytest.mark.parametrize(("python", "local"), CASES)
def test_shim_is_byte_identical_to_win32_cjs(monkeypatch, python, local):
    """§3: the two writers of the shim stay byte-identical, or install would
    heal every shim the shell wrote."""
    script = (
        "const [mod, python, local] = process.argv.slice(1);"
        "if (local === '') delete process.env.LOCALAPPDATA;"
        "else process.env.LOCALAPPDATA = local;"
        "process.stdout.write(JSON.stringify(require(mod).shimText(python)));"
    )
    out = subprocess.run(
        ["node", "-e", script, str(WIN32_CJS), python, local or ""],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=60)
    assert _shim_for(monkeypatch, python, local) == json.loads(out.stdout)


def test_task_scheduler_action_isolates_the_interpreter(monkeypatch):
    """§3: the Task Scheduler action carries no environment, so the
    PYTHONNOUSERSITE=1 / PYTHONSAFEPATH=1 pair rides as `-s -P`."""
    scripts: list[str] = []
    monkeypatch.setattr(windows, "_run_op", lambda script: scripts.append(script) or None)
    assert windows._register("C:\\Program Files\\Autowright\\pythonw.exe") is None
    (script,) = scripts
    assert ("New-ScheduledTaskAction -Execute "
            "'C:\\Program Files\\Autowright\\pythonw.exe' "
            "-Argument '-s -P -m autowright.main'") in script
