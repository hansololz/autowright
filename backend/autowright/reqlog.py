"""§5 request-log files: one file per request under <logs>/requests, developerMode-gated.

HTTP requests (API middleware) and agent requests (harness.invoke) land in the
same directory, named <YYYYMMDD-HHMMSS-mmm>_<TAG>_<detail>.log so lexicographic
name order is chronological. Newest 500 files kept; writes are best-effort.

Same gate, second surface: §5 build-failure records under <logs>/build-failures —
one file per §8 drafting call whose response failed validation (newest 200 kept),
material for improving the agent instructions later.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from . import paths
from .yamlio import load_yaml

MAX_FILES = 500
BUILD_MAX_FILES = 200
BODY_CAP = 256 * 1024
STEM_CAP = 96

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
_TOKEN = re.compile(r"token=[^&\s\"']+")
_lock = threading.Lock()

# developerMode is read straight from settings.yaml (1 s cache) rather than from the
# in-memory store: harness.invoke also runs inside the executor subprocess,
# whose Store is never loaded — the file is the one truth both processes see,
# and the toggle stays live without a restart.
_dev_cache = {"t": 0.0, "on": False}


def enabled(settings=None) -> bool:
    """`settings` is the backend's in-memory §5 settings mapping, passed by the
    §19 HTTP middleware: it runs on the event loop once per request, and the
    file-cached read below would put a stat + parse there every second. The
    executor subprocess has no Store, so it passes nothing and reads the file."""
    if settings is not None:
        return bool(settings.get("developerMode"))
    now = time.monotonic()
    if now - _dev_cache["t"] > 1.0:
        _dev_cache["t"] = now
        try:
            _dev_cache["on"] = bool((load_yaml(paths.settings_file()) or {}).get("developerMode"))
        except Exception:  # noqa: BLE001 — unreadable settings ≙ feature off
            _dev_cache["on"] = False
    return _dev_cache["on"]


def requests_dir():
    return paths.logs_dir() / "requests"


def build_failures_dir():
    return paths.logs_dir() / "build-failures"


def stamp() -> str:
    """Millisecond Pacific timestamp — same zone as the §5 app.log framing."""
    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y%m%d-%H%M%S-%f")[:-3]


def sanitize(detail: str) -> str:
    """URL path / harness name → filename-safe detail chunk."""
    s = _SAFE.sub("-", detail.lstrip("/")).strip("-")
    return s or "root"


def write(ts: str, tag: str, detail: str, text: str) -> None:
    """Write one request file (collision → _2, _3, …), then prune to MAX_FILES."""
    _write(requests_dir(), f"{ts}_{tag}_{sanitize(detail)}"[:STEM_CAP], text, MAX_FILES)


def _write(d, stem: str, text: str, cap: int) -> None:
    """One log file into `d` (collision → _2, _3, …), then prune to `cap`."""
    if not enabled():
        return
    try:
        with _lock:
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{stem}.log"
            n = 1
            while path.exists():
                n += 1
                path = d / f"{stem}_{n}.log"
            path.write_text(text, encoding="utf-8")
            names = sorted(p.name for p in d.iterdir() if p.name.endswith(".log"))
            for name in names[:-cap]:
                (d / name).unlink(missing_ok=True)
    except OSError:
        pass


def _body_text(body: bytes, total: int, redact: bool) -> str:
    """`body` is the captured prefix (≤ BODY_CAP), `total` the true byte count."""
    if redact:
        return "[redacted — secret material]"
    if total == 0:
        return "(empty)"
    note = f"\n… truncated ({total} bytes total, first {len(body)} kept)" if total > len(body) else ""
    try:
        return body.decode("utf-8") + note
    except UnicodeDecodeError:
        return f"<binary, {total} bytes>"


def write_http(ts: str, scope: dict, req_body: bytes, req_total: int, status: int,
               resp_body: bytes, resp_total: int, duration_ms: float) -> None:
    """One served HTTP request → one file. Token query + Authorization header
    scrubbed; /secrets bodies fully redacted (§5: values never touch disk)."""
    path = scope.get("path") or "/"
    query = (scope.get("query_string") or b"").decode("latin-1")
    target = _TOKEN.sub("token=***", path + (f"?{query}" if query else ""))
    headers = []
    for k, v in scope.get("headers") or []:
        key = k.decode("latin-1")
        val = "***" if key.lower() == "authorization" else v.decode("latin-1")
        headers.append(f"  {key}: {val}")
    redact = path.startswith("/secrets")
    method = scope.get("method") or "?"
    text = (f"{method} {target} → {status} · {duration_ms:.0f} ms\n\n"
            f"request headers:\n" + "\n".join(headers) + "\n\n"
            f"request body ({req_total} bytes):\n{_body_text(req_body, req_total, redact)}\n\n"
            f"response body ({resp_total} bytes):\n{_body_text(resp_body, resp_total, redact)}\n")
    write(ts, method, path, text)


def write_agent(ts: str, harness: str, model: str, prompt: str,
                response: str | None, error: str | None, duration_ms: float) -> None:
    """One harness.invoke() → one file: full prompt + full response/error —
    never truncated, mirroring the §5 app.log framing."""
    tail = (f"response ({len(response)} chars):\n{response}\n" if response is not None
            else f"request failed: {error}\n")
    text = (f"agent request · harness={harness} · model={model} · {duration_ms:.0f} ms\n\n"
            f"prompt ({len(prompt)} chars):\n{prompt}\n\n{tail}")
    write(ts, "AGENT", harness, text)


def write_build_failure(ts: str, mode: str, call: str, harness: str, model: str,
                        outcome: str, prompt: str, rounds: list[dict],
                        blockers: list[dict] | None) -> None:
    """One §8 drafting call whose response failed validation → one file under
    <logs>/build-failures/. Self-contained: every invalid round's full error
    list and full raw response, the diagnosis blockers when the call ended
    there, and the call's original prompt — never truncated."""
    parts = [f"build failure · mode={mode} · call={call} · harness={harness}"
             f" · model={model} · outcome={outcome}"]
    for i, r in enumerate(rounds, 1):
        errors, response = r["errors"], r["response"]
        parts.append(f"round {i} validation errors ({len(errors)}):\n- " + "\n- ".join(errors))
        parts.append(f"round {i} response ({len(response)} chars):\n{response}")
    if blockers:
        lines = []
        for b in blockers:
            lines.append(f"- reason: {b.get('reason', '')}")
            lines.append(f"  fix: {b.get('fix', '')}")
            if b.get("details"):
                lines.append(f"  details: {b['details']}")
        parts.append("diagnosis blockers:\n" + "\n".join(lines))
    parts.append(f"prompt ({len(prompt)} chars):\n{prompt}")
    _write(build_failures_dir(),
           f"{ts}_{sanitize(f'{mode}-{call}')}_{sanitize(outcome)}"[:STEM_CAP],
           "\n\n".join(parts) + "\n", BUILD_MAX_FILES)
