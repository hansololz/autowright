"""§3 Windows CLI shim: the exec line names the console interpreter, never the
`pythonw.exe` the service runs the backend under. The shell writes its shim
from `backend.json`'s `python` field (= `paths.console_python()`), and
`service install` heals against a whole-file compare — the two halves have to
produce the same bytes or every shell-written shim would be rewritten on the
next install."""
import sys

from autowright import paths
from autowright.platform import windows


def test_shim_text_names_the_console_interpreter(monkeypatch, tmp_path):
    pythonw = tmp_path / "pythonw.exe"
    pythonw.touch()
    console = tmp_path / "python.exe"
    console.touch()
    monkeypatch.setattr(paths.sys, "executable", str(pythonw))
    monkeypatch.setattr(paths, "current_os", lambda: "windows")

    text = windows.shim_text()
    assert f'"{console}" -m autowright.cli %*' in text
    assert "pythonw.exe" not in text
    # Byte for byte the shell's win32.cjs form: CRLF, marker, module form.
    assert text == (f"@echo off\r\n{windows.SHIM_MARKER}\r\n"
                    f'"{console}" -m autowright.cli %*\r\n')


def test_shim_text_falls_back_to_this_interpreter(monkeypatch, tmp_path):
    """No console sibling beside a `pythonw.exe` (and every non-Windows host):
    `console_python()` is just `sys.executable`, so the shim is unchanged."""
    pythonw = tmp_path / "pythonw.exe"
    pythonw.touch()
    monkeypatch.setattr(paths.sys, "executable", str(pythonw))
    monkeypatch.setattr(paths, "current_os", lambda: "windows")
    assert f'"{pythonw}" -m autowright.cli %*' in windows.shim_text()

    monkeypatch.undo()
    assert f'"{sys.executable}" -m autowright.cli %*' in windows.shim_text()
