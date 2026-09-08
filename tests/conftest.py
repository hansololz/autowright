import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))

# tests/bin holds a fake `claude` CLI so agent calls exercise the real subprocess
# path (backend → executor → Popen) without a real agent installed. Prepended at
# import time so engine subprocesses inherit it.
os.environ["PATH"] = f"{REPO / 'tests' / 'bin'}{os.pathsep}{os.environ['PATH']}"
# §15: on Windows the shebang fakes aren't executable, so PATHEXT resolves the
# `.cmd` twins beside them; those shims run the Python ports on this
# interpreter. Published unconditionally — POSIX keeps resolving the sh files.
os.environ["AUTOWRIGHT_TEST_PYTHON"] = sys.executable
if os.name == "nt":
    # Windows runs a `.cmd` child through %COMSPEC%, and cmd.exe prints the
    # machine's `Command Processor\AutoRun` hook (doskey macros and the like)
    # onto that child's stdout before the batch file starts — which would
    # corrupt every fake-CLI reply the backend parses. `/d` skips the hook, so
    # the twins answer with exactly the bytes the POSIX fakes answer with.
    os.environ["COMSPEC"] = f"{os.environ.get('COMSPEC', 'cmd.exe')} /d"


class _SubprocessProxy:
    """`subprocess` with one call swapped; everything else is the real module."""

    def __init__(self, **over):
        self._over = over

    def __getattr__(self, name):
        import subprocess

        if name in self._over:
            return self._over[name]
        return getattr(subprocess, name)


# Every ad-hoc fake CLI writes UTF-8 with LF endings — what real CLIs emit,
# and what the backend's readers decode (§2 pipe-encoding contract:
# explicit encoding="utf-8", never the locale codec).
_FAKE_CLI_PREAMBLE = """\
import sys as _sys
for _st in (_sys.stdout, _sys.stderr):
    try:
        _st.reconfigure(encoding="utf-8", errors="replace", newline="\\n")
    except Exception:
        pass
"""


def fake_cli(tmp_path, body, name="claude"):
    """A real, spawnable fake CLI whose body is `body` (Python source).

    §19: what makes a file executable is per-OS, so the shape is too — a
    shebang script on POSIX, a `.cmd` shim over a `.py` on Windows, where the
    execute bit doesn't exist. Either way the caller's code spawns a real
    child through the real Popen path."""
    source = _FAKE_CLI_PREAMBLE + body
    if os.name == "nt":
        src = tmp_path / f"{name}.py"
        src.write_text(source, encoding="utf-8")
        shim = tmp_path / f"{name}.cmd"
        shim.write_bytes(f'@echo off\r\n"{sys.executable}" "{src}" %*\r\n'.encode())
        return shim
    script = tmp_path / name
    script.write_text(f"#!{sys.executable}\n{source}", encoding="utf-8")
    script.chmod(0o755)
    return script


def use_fake_osascript(monkeypatch, module):
    """§15: point `module`'s bare `osascript` spawns at the fake.

    No-op on POSIX — `PATH` already resolves `tests/bin/osascript`. On Windows
    `CreateProcess` appends only `.exe` to a bare command name (unlike
    `shutil.which`, which honors `PATHEXT`), so `["osascript", …]` can never
    reach the `.cmd` twin. Only the *name resolution* is substituted here: the
    real Python port still runs as a real child process, so argv, stdout,
    stderr and exit code are the fake's own."""
    if os.name != "nt":
        return
    import subprocess

    real_run = subprocess.run
    fake = str(REPO / "tests" / "bin" / "osascript.py")

    def run(args, *a, **kw):
        if isinstance(args, (list, tuple)) and args and args[0] == "osascript":
            args = [sys.executable, fake, *args[1:]]
        return real_run(args, *a, **kw)

    monkeypatch.setattr(module, "subprocess", _SubprocessProxy(run=run))


@pytest.fixture(autouse=True)
def fake_keychain(monkeypatch):
    """In-memory stand-in for the macOS Keychain (backend-process only calls)."""
    from autowright import keychain

    mem: dict[str, str] = {}
    monkeypatch.setattr(keychain, "get_secret", mem.get)
    monkeypatch.setattr(keychain, "set_secret", mem.__setitem__)
    monkeypatch.setattr(keychain, "delete_secret", lambda name: mem.pop(name, None))


@pytest.fixture(autouse=True)
def no_notifications(monkeypatch):
    """Default: notify.post is a no-op. The fixture yields the real function so
    tests/test_notify.py can drive the true osascript path (against the fake
    tests/bin/osascript) while every other test stays silent."""
    from autowright import notify

    real = notify.post
    monkeypatch.setattr(notify, "post", lambda title, body: None)
    yield real


@pytest.fixture(autouse=True)
def no_kill_matching(monkeypatch):
    """Default: the §3 quit-entirely sweep is a recorded no-op returning 0.
    Without this, any test that reaches `service stop` would run a REAL sweep
    against the developer's machine — the markers include this venv's python
    and `-m autowright.`, i.e. the very test run. The dedicated kill_matching
    tests drive the real bodies explicitly against faked process tables."""
    from autowright.platform import posixproc, windows

    calls: list[list[str]] = []

    def _stub(self, markers, grace_s=2.0):
        calls.append(list(markers))
        return 0

    monkeypatch.setattr(posixproc.PosixProcessControl, "kill_matching", _stub)
    monkeypatch.setattr(windows.WindowsProcessControl, "kill_matching", _stub)
    yield calls


@pytest.fixture(autouse=True)
def reset_module_globals():
    """Module-global state leaks between tests inside one worker — reset the
    known offenders before every test: the §19 `ollama serve` spawn cooldown,
    the §6 robots/site throttle caches, the §6.2 installed scan, and the two
    process-lifetime dedupe/parking maps in `api` (a served launch id or a
    parked import token from an earlier test must never decide a later one)."""
    from autowright import api, events, executor, harness, packages

    harness._serve_last_spawn = 0.0
    executor._robots.clear()
    executor._site_last.clear()
    packages.invalidate_scan()  # §6.2 installed-scan cache (keyed on the home dir)
    api._served_launches.clear()  # §19 app-start dedupe memory
    api._shutdown_callbacks.clear()  # §3 main()-registered cleanup from an earlier boot
    api._quiesce_callbacks.clear()
    events.hub._loop = None  # a lifespan-running test binds a loop that closes with it
    # A lifespan-running test's shutdown flips the process-wide engine into its
    # §3 "shutting down" refusal (start -> 409); a fresh backend process never
    # inherits that flag, so neither may the next test in this worker.
    api.engine._stopping = False
    for token in list(api._import_parked):  # §5.2 parked previews + their spool files
        api._drop_parked(token)


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """Isolated Autowright home per test."""
    monkeypatch.setenv("AUTOWRIGHT_HOME", str(tmp_path))
    from autowright import paths

    paths.ensure_dirs()
    return tmp_path


@pytest.fixture()
def task_scheduler(monkeypatch, tmp_path):
    """§3 Windows ServiceManager double: `windows._powershell` replaced by a
    recorder that models Task Scheduler's state (the same shape as the launchd
    fake in tests/test_service.py), plus the §15 `AUTOWRIGHT_SHIM` knob pointed
    into the test's tmp dir. The real powershell.exe never runs, so every test
    using this is host-independent."""
    from types import SimpleNamespace

    from autowright.platform import windows

    scripts: list[str] = []
    task = {"state": "absent"}  # Task Scheduler's view, driven by the cmdlets
    canned: dict[str, tuple[int, str, str]] = {}  # cmdlet name → forced result

    def fake_ps(script, **kw):
        scripts.append(script)
        for needle, result in canned.items():
            if needle in script:
                return result
        ok = (0, f"{windows._OK}\n", "")
        # Unregister first: "Register-ScheduledTask" is a substring of it.
        if "Unregister-ScheduledTask" in script:
            task["state"] = "absent"
            return ok
        if "Register-ScheduledTask" in script:
            task["state"] = "Ready"  # registered, not started
            return ok
        if "Start-ScheduledTask" in script:
            if task["state"] == "absent":
                return 1, "", "No MSFT_ScheduledTask objects found"
            task["state"] = "Running"
            return ok
        if "Stop-ScheduledTask" in script:
            if task["state"] == "Running":
                task["state"] = "Ready"
            return ok
        if "Get-ScheduledTask" in script:
            return 0, f"{windows._STATE_PREFIX}{task['state']}\n", ""
        raise AssertionError(f"unmodeled PowerShell script: {script}")

    monkeypatch.setattr(windows, "_powershell", fake_ps)
    monkeypatch.setattr(windows, "_POLL_INTERVAL_S", 0)  # no real poll waits
    shim = tmp_path / "shimbin" / "autowright.cmd"
    monkeypatch.setenv("AUTOWRIGHT_SHIM", str(shim))
    return SimpleNamespace(mod=windows, service=windows.WindowsService(),
                           scripts=scripts, task=task, canned=canned, shim=shim)


@pytest.fixture()
def systemd(monkeypatch, tmp_path, home):
    """§3 Linux ServiceManager double: `linux._systemctl` replaced by a
    recorder that models the systemd user manager's state (the same shape as
    the Task Scheduler double above), the unit directory pointed into the
    test's tmp dir via XDG_CONFIG_HOME, and the §15 `AUTOWRIGHT_SHIM` knob.
    The real systemctl never runs, so every test using this is
    host-independent. `home` isolates the §5 roots — install creates the logs
    directory, status reads backend.json."""
    from types import SimpleNamespace

    from autowright.platform import linux

    calls: list[tuple[str, ...]] = []
    unit = {"active": False, "enabled": False}  # the user manager's view
    canned: dict[str, object] = {}  # verb → forced (code, stdout, stderr) or _TimedOut

    def fake_systemctl(*args):
        calls.append(args)
        verb = args[0]
        if verb in canned:
            forced = canned[verb]
            if isinstance(forced, linux._TimedOut):
                return forced
            code, out, err = forced
            return SimpleNamespace(returncode=code, stdout=out, stderr=err)
        ok = SimpleNamespace(returncode=0, stdout="", stderr="")
        if verb == "daemon-reload":
            return ok
        if verb == "enable":
            unit["enabled"] = True
            return ok
        if verb == "restart":
            unit["active"] = True
            return ok
        if verb == "stop":
            unit["active"] = False
            return ok
        if verb == "disable":  # always `disable --now` in the manager
            unit["enabled"] = False
            unit["active"] = False
            return ok
        if verb == "is-active":
            state = "active" if unit["active"] else "inactive"
            return SimpleNamespace(returncode=0 if unit["active"] else 3,
                                   stdout=f"{state}\n", stderr="")
        if verb == "show":  # MainPID query on the active unit
            return SimpleNamespace(returncode=0, stdout="4242\n", stderr="")
        raise AssertionError(f"unmodeled systemctl verb: {args}")

    monkeypatch.setattr(linux, "_systemctl", fake_systemctl)
    monkeypatch.setattr(linux, "_POLL_INTERVAL_S", 0)  # no real poll waits
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    shim = tmp_path / "shimbin" / "autowright"
    monkeypatch.setenv("AUTOWRIGHT_SHIM", str(shim))
    return SimpleNamespace(mod=linux, service=linux.SystemdService(),
                           calls=calls, unit=unit, canned=canned, shim=shim)


@pytest.fixture()
def client(home):
    """Authenticated TestClient over the live app — the shared API-suite entry
    point (a mock Claude Code agent is the sole configured agent)."""
    from fastapi.testclient import TestClient

    from autowright import api
    from autowright.storage import store

    store.load_all()
    store.autos.clear()
    store.execs.clear()
    store.agents = [{"id": "mock", "harness": "Claude Code", "mode": "default",
                     "model": None}]
    store.default_agent_id = "mock"  # §4.7 single pointer
    c = TestClient(api.app)
    c.headers["Authorization"] = f"Bearer {api.AUTH_TOKEN}"
    return c


@pytest.fixture()
def devmode(home):
    """developerMode on, persisted — reqlog reads the live setting from settings.yaml."""
    from autowright import paths, reqlog
    from autowright.yamlio import save_yaml

    save_yaml(paths.settings_file(), {"developerMode": True})
    reqlog._dev_cache["t"] = 0.0  # drop the 1 s cache so the write is seen now
    yield
    reqlog._dev_cache["t"] = 0.0


@pytest.fixture()
def store(home):
    from autowright.storage import Store

    s = Store()
    s.load_all()
    return s


def read_all_logs(store, execution_id):
    """Test convenience: every log line of an execution, merged across the
    per-step-attempt files plus the execution log (§5 logs/ layout)."""
    import json

    d = store.exec_dir(execution_id) / "logs"
    out = []
    if d.exists():
        for p in sorted(d.iterdir()):
            for ln in p.read_text(encoding="utf-8").splitlines():
                try:
                    out.append(json.loads(ln))
                except ValueError:
                    pass
    return out


def make_version(**over):
    ver = {
        "description": "Test automation",
        "note": "Created",
        "params": [
            {"name": "greeting", "kind": "text", "label": "Greeting", "help": "", "default": "hello"},
            {"name": "count", "kind": "number", "label": "Count", "help": "", "min": 1, "default": 3},
        ],
        "steps": [
            {"file": "01-say.py", "name": "Say hello", "description": "prints",
             "code": 'from autowright import log, params\n'
                     'log(f"{params[\'greeting\']} x{params[\'count\']}")\n'},
            {"file": "02-finish.py", "name": "Finish", "description": "result",
             "code": 'from autowright import result\n'
                     'result.status("ok")\nresult.chip("All good")\n'},
        ],
        "spec": [{"kind": "h1", "text": "Test automation"}, {"kind": "p", "text": "It tests."}],
    }
    ver.update(over)
    return ver
