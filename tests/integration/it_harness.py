"""§15 integration harness: a real backend subprocess per test, isolated in a
tmp AUTOWRIGHT_HOME on a random localhost port, plus localhost-only doubles for
the web (http.server) and PyPI (a wheel dir served to pip via find-links)."""
import base64
import hashlib
import json
import os
import signal
import socket
import subprocess
import time
import zipfile
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent.parent
PYTHON = str(REPO / ".venv" / "bin" / "python")
CLI = str(REPO / ".venv" / "bin" / "autowright")


def wait_for(cond, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        v = cond()
        if v:
            return v
        time.sleep(0.05)
    raise TimeoutError(f"timed out waiting for {what}")


class Backend:
    """One real `python -m autowright.main` process over one tmp home."""

    def __init__(self, home: Path, extra_env: dict[str, str] | None = None):
        self.home = home
        # §15 AUTOWRIGHT_TICK_S=1: live-scheduler tests wait ~1s ticks, not 15s.
        self.env = {**os.environ, "AUTOWRIGHT_HOME": str(home),
                    "AUTOWRIGHT_TICK_S": "1", **(extra_env or {})}
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self.token = ""
        self.log_path = home / "backend.test.log"
        self._log = None

    def start(self) -> "Backend":
        (self.home / "backend.json").unlink(missing_ok=True)
        # Backend output goes to a file, never a PIPE: nothing drains a pipe,
        # so a chatty backend would fill the buffer and block mid-write.
        self._log = open(self.log_path, "ab")
        self.proc = subprocess.Popen(
            [PYTHON, "-m", "autowright.main"], env=self.env,
            stdout=self._log, stderr=subprocess.STDOUT,
        )
        info = wait_for(self._read_backend_json, 15, "backend.json + open port")
        self.port, self.token = info["port"], info["token"]
        return self

    def _read_backend_json(self):
        bj = self.home / "backend.json"
        if not bj.exists():
            return None
        try:
            info = json.loads(bj.read_text())
            port = info["port"]
        except (ValueError, KeyError):
            return None
        # §3 bind-before-publish: once backend.json exists the port must
        # already accept — a single connect with no retry, so a
        # publish-then-bind regression fails loudly instead of being
        # absorbed by the poll.
        with socket.create_connection(("127.0.0.1", port), timeout=5):
            return info

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base, timeout=30,
                            headers={"Authorization": f"Bearer {self.token}"})

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)
            self.proc.wait(timeout=10)
        self._close_log()

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        self._close_log()

    def _close_log(self) -> None:
        if self._log is not None:
            self._log.close()
            self._log = None


def build_wheel(dest: Path, name: str = "tinypkg", version: str = "1.0.0") -> Path:
    """A minimal but valid wheel, so `packages.ensure` runs a real pip install
    without any network (`PIP_NO_INDEX` + `PIP_FIND_LINKS`)."""
    files = {
        f"{name}/__init__.py": f'MARKER = "{name}-{version}"\n',
        f"{name}-{version}.dist-info/METADATA":
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        f"{name}-{version}.dist-info/WHEEL":
            "Wheel-Version: 1.0\nGenerator: autowright-tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = ""
    for path, text in files.items():
        digest = base64.urlsafe_b64encode(
            hashlib.sha256(text.encode()).digest()).rstrip(b"=").decode()
        record += f"{path},sha256={digest},{len(text.encode())}\n"
    record += f"{name}-{version}.dist-info/RECORD,,\n"
    dest.mkdir(parents=True, exist_ok=True)
    whl = dest / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as z:
        for path, text in files.items():
            z.writestr(path, text)
        z.writestr(f"{name}-{version}.dist-info/RECORD", record)
    return whl


def make_draft(**over) -> dict:
    """POST /automations draft payload — mirrors tests/conftest.make_version."""
    d = {
        "description": "Integration automation",
        "note": "Created",
        "params": [],
        "steps": [
            {"file": "01-say.py", "name": "Say", "description": "prints",
             "code": 'from autowright import log\nlog("integration says hi")\n'},
            {"file": "02-finish.py", "name": "Finish", "description": "result",
             "code": 'from autowright import result\nresult.status("ok")\nresult.chip("All good")\n'},
        ],
        "spec": [{"kind": "h1", "text": "Integration automation"}, {"kind": "p", "text": "It integrates."}],
    }
    d.update(over)
    return d


def create_auto(client: httpx.Client, name: str = "Integration", **over) -> dict:
    r = client.post("/automations", json={"draft": make_draft(**over), "name": name})
    assert r.status_code == 200, r.text
    return r.json()


def wait_status(client: httpx.Client, execution_id: str, timeout: float = 60) -> dict:
    def settled():
        e = client.get(f"/executions/{execution_id}").json()
        return e if e["status"] not in ("executing", "queued") else None

    return wait_for(settled, timeout, f"execution {execution_id} to settle")


def run_cli(home: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([CLI, *args], env={**os.environ, "AUTOWRIGHT_HOME": str(home)},
                          capture_output=True, text=True, timeout=120)
