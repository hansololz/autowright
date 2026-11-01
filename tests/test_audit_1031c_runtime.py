"""Runtime, service and transfer audit regressions (2026-10-31, third sweep).

§2 `ps` / `launchctl` output decodes with replacement; §3 interpreter isolation
rides as `-s -P` flags on every service definition and pip run, never as
environment variables; §5 the log cap is enforced hourly too; §3 every pip and
vendor installer child is tracked for the shutdown sweep; §8 the locked-bytes
restore never re-creates a vanished folder, replaces a planted symlink and says
when it failed; §3 the shim heal is a temp file + rename; §19 a leftover Ollama
aside is removed and the installer download is https-only; §5.1 the archive
door refuses lone surrogates and a secret name with a trailing newline, and
normalizes notes.md line endings.
"""
import os
import plistlib
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_service import launchd_only, svc  # noqa: F401 - fixture
from test_transfer import _archive, _rezip

from autowright import transfer

# ---------- D: §2 `ps` and `launchctl` output decode with replacement ----------

_LATIN1_PS = b"  4242  4242 /usr/bin/python3 caf\xe9.py autowright-marker\n"


def _ps_bytes_run(captured):
    def fake_run(cmd, **kw):
        captured.append(kw)
        # The decode the real subprocess.run does from the kwargs given.
        assert "text" not in kw
        stdout = _LATIN1_PS.decode(kw["encoding"], kw["errors"])
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")
    return fake_run


def test_group_has_command_survives_a_non_utf8_ps_line(monkeypatch):
    from autowright.platform import posixproc

    captured = []
    monkeypatch.setattr(posixproc.subprocess, "run", _ps_bytes_run(captured))
    processes = posixproc.PosixProcessControl()
    assert processes.group_has_command(4242, "autowright-marker") is True
    assert captured[0]["encoding"] == "utf-8" and captured[0]["errors"] == "replace"


def test_the_pid_table_survives_a_non_utf8_ps_line(monkeypatch):
    from autowright.platform import posixproc

    captured = []
    monkeypatch.setattr(posixproc.subprocess, "run", _ps_bytes_run(captured))
    processes = posixproc.PosixProcessControl()
    rows = processes._pid_table()
    assert rows and rows[0][:2] == (4242, 4242)
    assert "�" in rows[0][2]


def test_launchctl_output_decodes_with_replacement(monkeypatch):
    from autowright import service

    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        return SimpleNamespace(returncode=0, stdout=b"\xe9".decode(kw["encoding"], kw["errors"]),
                               stderr="")

    monkeypatch.setattr(service.subprocess, "run", fake_run)
    result = service._launchctl("print", "gui/0/x")
    assert result.stdout == "�"
    assert "text" not in seen


# ---------- F: §3 interpreter isolation as flags ----------

@launchd_only
def test_the_plist_launches_with_the_flags_and_no_environment(svc):  # noqa: F811
    svc.mod.install()
    with open(svc.plist, "rb") as f:
        plist = plistlib.load(f)
    assert plist["ProgramArguments"][1:] == ["-s", "-P", "-m", "autowright.main"]
    assert "EnvironmentVariables" not in plist


def test_the_systemd_unit_launches_with_the_flags_and_no_python_environment():
    from autowright.platform import linux

    text = linux.unit_text()
    exec_start = next(line for line in text.splitlines() if line.startswith("ExecStart="))
    assert exec_start.endswith(" -s -P -m autowright.main")
    assert not [line for line in text.splitlines()
                if line.startswith("Environment=PYTHON")]


def test_pip_env_drops_both_isolation_variables_even_when_inherited(monkeypatch):
    from autowright import packages

    monkeypatch.setenv("PYTHONNOUSERSITE", "1")
    monkeypatch.setenv("PYTHONSAFEPATH", "1")
    env = packages._pip_env()
    assert "PYTHONNOUSERSITE" not in env and "PYTHONSAFEPATH" not in env
    assert env["PIP_USER"] == "0" and env["PIP_REQUIRE_VIRTUALENV"] == "0"


class _FakePip:
    """A pip child that has already exited cleanly."""
    instances: list = []

    def __init__(self, cmd, **kw):
        self.cmd = cmd
        self.returncode = 0
        self.stdout = SimpleNamespace(close=lambda: None)
        self.stderr = SimpleNamespace(close=lambda: None)
        _FakePip.instances.append(self)

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0

    def communicate(self, timeout=None):
        return "", ""


def test_the_pip_command_carries_the_isolation_flags(home, monkeypatch):
    from autowright import packages

    _FakePip.instances = []
    monkeypatch.setattr(packages.subprocess, "Popen", _FakePip)
    assert packages._pip_install("pkg") is None
    cmd = _FakePip.instances[0].cmd
    assert cmd[1:6] == ["-s", "-P", "-m", "pip", "install"]


def test_main_drops_the_isolation_variables_before_it_starts(home, monkeypatch):
    from autowright import main as backend_main

    monkeypatch.setenv("PYTHONNOUSERSITE", "1")
    monkeypatch.setenv("PYTHONSAFEPATH", "1")
    seen = {}

    class Stop(Exception):
        pass

    def ensure_dirs():
        seen["user_site"] = os.environ.get("PYTHONNOUSERSITE")
        seen["safe_path"] = os.environ.get("PYTHONSAFEPATH")
        raise Stop

    monkeypatch.setattr(backend_main.paths, "ensure_dirs", ensure_dirs)
    with pytest.raises(Stop):
        backend_main.main()
    assert seen == {"user_site": None, "safe_path": None}


# ---------- K: §5 the log cap is enforced hourly ----------

def test_an_hourly_tick_trims_an_oversized_app_log(store, monkeypatch):
    from autowright import paths
    from autowright.engine import Engine
    from autowright.scheduler import Scheduler

    monkeypatch.setattr(paths, "LOG_CAP", 1000)
    monkeypatch.setattr(paths, "LOG_TRIM_TO", 500)
    now = {"t": datetime(2026, 10, 1, 9, 0)}
    sched = Scheduler(store, Engine(store), clock=lambda: now["t"],
                      utc_clock=lambda: now["t"])
    log = paths.app_log()
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_bytes(b"".join(f"line {i:04d}\n".encode() for i in range(200)))
    sched._tick()  # the hourly block is not due yet
    assert log.stat().st_size == 2000
    now["t"] += timedelta(hours=1, seconds=1)
    sched._tick()
    kept = log.read_bytes()
    assert len(kept) < 500 and kept.endswith(b"line 0199\n")


def test_main_still_exports_the_startup_trim():
    from autowright import main as backend_main, paths

    assert backend_main.trim_logs is paths.trim_logs


# ---------- L: §3 pip and installer children are tracked ----------

def test_a_running_pip_is_tracked_and_signalled_by_kill_running(home, monkeypatch):
    from autowright import packages, platform

    signalled = []
    monkeypatch.setattr(platform.current().processes, "signal_group",
                        lambda proc, sig=None: signalled.append(proc))
    tracked_during = {}

    class SlowPip(_FakePip):
        def communicate(self, timeout=None):
            tracked_during["tracked"] = self in packages._running
            packages.kill_running()
            return "", ""

    _FakePip.instances = []
    monkeypatch.setattr(packages.subprocess, "Popen", SlowPip)
    packages._pip_install("pkg")
    proc = _FakePip.instances[0]
    assert tracked_during == {"tracked": True}
    assert signalled == [proc]
    assert packages._running == set()


def test_a_settled_pip_leaves_the_tracking_set(home, monkeypatch):
    from autowright import packages

    monkeypatch.setattr(packages.subprocess, "Popen", _FakePip)
    packages._pip_install("pkg")
    assert packages._running == set()


def test_an_installer_child_is_tracked_and_signalled_by_kill_running(tmp_path, monkeypatch):
    from autowright import installer, platform

    signalled = []
    monkeypatch.setattr(platform.current().processes, "signal_group",
                        lambda proc, sig=None: signalled.append(proc))
    monkeypatch.setattr(installer.harness, "_neutral_cwd", lambda provider_id: str(tmp_path))
    seen = {}

    real_popen = subprocess.Popen

    def popen(cmd, **kw):
        proc = real_popen(cmd, **kw)
        seen["proc"] = proc
        return proc

    def emit(line=None, percent=None):
        # Mid-run: the child is tracked, and the sweep signals it.
        seen["tracked"] = seen["proc"] in installer._running
        installer.kill_running()

    monkeypatch.setattr(installer.subprocess, "Popen", popen)
    installer._stream_shell(["/bin/sh", "-c", "echo hello"], emit, "claude")
    assert seen["tracked"] is True
    assert signalled == [seen["proc"]]
    assert installer._running == set()


# ---------- H: §8 the locked-bytes restore ----------

def _guarded(tmp_path, *names):
    folder = tmp_path / "v1"
    folder.mkdir()
    guarded = {}
    for name in names:
        path = folder / f"{name}.py"
        path.write_bytes(f"print({name!r})\n".encode())
        guarded[path] = name
    return folder, guarded


def test_a_vanished_folder_is_skipped_never_re_created(tmp_path):
    import shutil

    from autowright import drafting

    folder, guarded = _guarded(tmp_path, "locked")
    snapshot = drafting.snapshot_locked(guarded)
    shutil.rmtree(folder)  # a Discard or a Save during the call
    assert drafting.restore_locked(guarded, snapshot) is None
    assert not folder.exists()


def test_a_vanished_folder_does_not_hide_another_tampered_file(tmp_path):
    import shutil

    from autowright import drafting

    folder, guarded = _guarded(tmp_path, "gone")
    other = tmp_path / "draft"
    other.mkdir()
    kept = other / "kept.py"
    kept.write_bytes(b"print('kept')\n")
    guarded[kept] = "kept"
    snapshot = drafting.snapshot_locked(guarded)
    shutil.rmtree(folder)
    kept.write_bytes(b"print('changed')\n")
    assert drafting.restore_locked(guarded, snapshot) == ("kept", None)
    assert kept.read_bytes() == b"print('kept')\n"
    assert not folder.exists()


def test_a_planted_symlink_is_replaced_never_written_through(tmp_path):
    from autowright import drafting

    folder, guarded = _guarded(tmp_path, "locked")
    path = next(iter(guarded))
    snapshot = drafting.snapshot_locked(guarded)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside\n")
    path.unlink()
    path.symlink_to(outside)
    assert drafting.restore_locked(guarded, snapshot) == ("locked", None)
    assert not path.is_symlink()
    assert path.read_bytes() == snapshot[path]
    assert outside.read_bytes() == b"outside\n"


def test_a_failed_restore_says_so(tmp_path, monkeypatch):
    from autowright import drafting

    folder, guarded = _guarded(tmp_path, "locked")
    path = next(iter(guarded))
    snapshot = drafting.snapshot_locked(guarded)
    path.write_bytes(b"tampered\n")

    def fail(path, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(drafting, "_write_bytes_atomically", fail)
    result = drafting.restore_locked(guarded, snapshot)
    assert result == ("locked", "No space left on device")
    message = str(drafting.LockedStepTampered(*result))
    assert message == ("the agent changed the locked step 'locked' on disk — it couldn't "
                       "be restored: No space left on device; nothing from this response "
                       "was applied")


def test_a_restored_file_keeps_the_restored_message():
    from autowright import drafting

    assert str(drafting.LockedStepTampered("locked")) == (
        "the agent changed the locked step 'locked' on disk — the file was restored; "
        "nothing from this response was applied")


# ---------- AI: §3 the shim heal is a temp file + rename ----------

@launchd_only
def test_a_failed_shim_heal_leaves_the_original_intact(svc, monkeypatch):  # noqa: F811
    from autowright import yamlio

    svc.shim.parent.mkdir(parents=True)
    original = (f"#!/bin/sh\n{svc.mod.SHIM_MARKER}\n"
                'exec "/old/gone/python3" -m autowright.cli "$@"\n')
    svc.shim.write_text(original)
    real_fdopen = os.fdopen

    class FailingFile:
        def __init__(self, fd, *a, **kw):
            self.f = real_fdopen(fd, *a, **kw)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.f.close()

        def write(self, text):
            self.f.write(text[:5])
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(yamlio.os, "fdopen", FailingFile)
    note = svc.mod._heal_one(svc.shim)
    assert "not rewritable" in note
    assert svc.shim.read_text() == original
    assert [p.name for p in svc.shim.parent.iterdir()] == [svc.shim.name]


@launchd_only
def test_a_healed_shim_carries_the_marker(svc):  # noqa: F811
    svc.shim.parent.mkdir(parents=True)
    svc.shim.write_text(f"#!/bin/sh\n{svc.mod.SHIM_MARKER}\n"
                        'exec "/old/gone/python3" -m autowright.cli "$@"\n')
    assert svc.mod._heal_one(svc.shim) == f"CLI at {svc.shim}"
    assert svc.mod.SHIM_MARKER in svc.shim.read_text()
    assert svc.shim.read_text() == svc.mod.shim_text()
    assert os.stat(svc.shim).st_mode & 0o777 == 0o755


# ---------- AK / AN: §19 the Ollama aside and the https-only download ----------

def test_a_leftover_ollama_aside_of_any_pid_is_removed(tmp_path, monkeypatch):
    from autowright import installer

    apps = tmp_path / "Applications"
    (apps / "Ollama.app" / "Contents").mkdir(parents=True)
    stale = apps / "Ollama.app.old-999"
    (stale / "Contents").mkdir(parents=True)
    monkeypatch.setattr(installer, "APPLICATIONS", str(apps))
    monkeypatch.setattr(installer, "LOCAL_BIN", str(tmp_path / "localbin"))
    monkeypatch.setattr(installer, "USR_LOCAL_BIN", str(tmp_path / "usr-local-bin"))
    monkeypatch.setattr(installer, "_ensure_login_path", lambda emit: None)
    monkeypatch.setattr(installer, "_download",
                        lambda url, dest, emit, label: Path(dest).write_bytes(b"zip"))

    def fake_run(cmd, **kw):
        if cmd[0] == "/usr/bin/ditto":
            (Path(cmd[-1]) / "Ollama.app" / "Contents").mkdir(parents=True)
        return subprocess.CompletedProcess(cmd, 1)

    monkeypatch.setattr(installer.subprocess, "run", fake_run)
    installer._install_ollama_app(lambda **k: None)
    assert sorted(p.name for p in apps.iterdir()) == ["Ollama.app"]


def test_the_installer_download_is_https_only(monkeypatch):
    from autowright import installer

    commands = []

    def stream(cmd, emit, provider_id, env_extra=None):
        commands.append(cmd)

    monkeypatch.setattr(installer, "_stream_shell", stream)
    installer._run_installer_script("https://example.invalid/install.sh", "/bin/bash",
                                    lambda **k: None, "claude")
    download = commands[0][-1]
    assert download.startswith("curl ")
    assert "--proto '=https'" in download and "--proto-redir '=https'" in download


# ---------- B / AT / AW: §5.1 the archive door ----------

def _validated(data):
    with transfer._open_archive(data) as z:
        return transfer._validate(z)


def test_an_archive_name_with_a_lone_surrogate_is_refused():
    from autowright import textsafe

    data = _rezip(_archive(), lambda name, b: (b.replace(b"name: Ported", b'name: "\\ud800"')
                                               if name == "manifest.yaml" else None))
    with pytest.raises(transfer.TransferError) as e:
        _validated(data)
    assert str(e.value) == textsafe.LONE_SURROGATE_ERROR


def test_a_secret_name_with_a_trailing_newline_is_refused():
    data = _archive(secrets=[{"ref": "1", "name": "API_KEY\n", "description": ""}])
    with pytest.raises(transfer.TransferError, match="invalid secret"):
        _validated(data)


def test_imported_notes_lose_their_carriage_returns():
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(_archive())) as src, \
            zipfile.ZipFile(buf, "w") as out:
        for name in src.namelist():
            out.writestr(name, src.read(name))
        out.writestr("automation/notes.md", "a\r\nb\r\n")
    assert _validated(buf.getvalue())["notes"] == "a\nb"
