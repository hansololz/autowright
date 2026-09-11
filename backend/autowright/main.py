"""Backend entry point: bind localhost on a free port, write backend.json (§3),
load files, start the scheduler, serve the API."""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import signal
import socket
import sys
import threading
from collections.abc import Callable

import uvicorn

from . import __version__, api, paths, platform
from .listeners import Listeners
from .scheduler import Scheduler
from .storage import store
from .yamlio import atomic_write_text


LOG_CAP = 100 * 1024 * 1024  # §5 log size cap
LOG_TRIM_TO = LOG_CAP // 2  # trim target — half the cap, so a saturated log isn't rewritten every boot

# §3 time-boxed graceful shutdown: the renderer keeps /ws open during quit-all
# and reset, and an unbounded uvicorn shutdown waits on open WebSockets forever,
# blowing the service stop's 10 s deregistration wait. At this bound uvicorn
# force-closes connections; the lifespan shutdown and the backend.json unlink
# below still run after it.
SHUTDOWN_GRACE_S = 5


def trim_logs() -> None:
    """§5 log size cap: at startup, trim any over-cap backend log in place to
    its newest LOG_TRIM_TO bytes (cut at a line boundary) — oldest lines die
    first. In place because launchd holds backend.out/err.log open in append
    mode; renaming would leave the live fd writing to the renamed file."""
    for name in ("app.log", "backend.out.log", "backend.err.log"):
        path = paths.logs_dir() / name
        try:
            if path.stat().st_size <= LOG_CAP:
                continue
            with open(path, "rb+") as f:
                f.seek(-LOG_TRIM_TO, os.SEEK_END)
                tail = f.read()
                nl = tail.find(b"\n")
                if nl != -1:
                    tail = tail[nl + 1:]
                f.seek(0)
                f.write(tail)
                f.truncate()
        except OSError:
            continue


def route_logs() -> None:
    """§3 Windows log routing: Task Scheduler captures no stdout/stderr the way
    launchd does, so the backend opens the very same two files the launchd
    plist names — backend.out.log / backend.err.log under the §5 logs root — and
    points sys.stdout/sys.stderr at them. The §9.3 log overlay and the §5 docs
    then hold unchanged on both platforms.

    Called from main() only (never at import), and only after trim_logs(): a
    writer holding these handles across the startup trim turns it into a
    sharing violation on Windows. Append mode, line-buffered, explicit UTF-8
    (§2 pipe-encoding contract — the locale codec is cp1252 here), so a crash
    loses at most a partial line. No-op on every other OS, where the service
    manager captures the streams itself."""
    if paths.current_os() != "windows":
        return
    logs = paths.logs_dir()
    try:
        logs.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    for name, stream in (("backend.out.log", "stdout"), ("backend.err.log", "stderr")):
        try:
            f = open(logs / name, "a", encoding="utf-8", errors="replace", buffering=1)
        except OSError:
            continue  # an unwritable log must never stop the backend booting
        setattr(sys, stream, f)


class _DevModeFilter(logging.Filter):
    """§4.9 developerMode: INFO request logs pass only while the setting is on.
    Also scrubs the auth token from logged request lines — the WS handshake
    carries it in the query string, and the access log would otherwise copy
    the sole credential into backend.out.log (not 0600 like backend.json)."""

    _TOKEN = re.compile(r"token=[^&\s\"']+")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(
                self._TOKEN.sub("token=***", a) if isinstance(a, str) else a
                for a in record.args)
        return record.levelno >= logging.WARNING or bool(store.settings.get("developerMode"))


# §3: serializes the discovery guard's republish against shutdown's unlink —
# the two must never interleave into a resurrected backend.json.
_publish_lock = threading.Lock()


def republish_if_lost(payload: str, stopped: Callable[[], bool] | None = None) -> bool:
    """§3 discovery guard: rewrite backend.json when it is missing or
    unreadable (recreating the §5 dirs first) — an externally wiped
    Application Support dir must not strand clients on a healthy backend that
    launchd KeepAlive will never restart. A well-formed file is left alone
    whatever pid it holds: during a service restart it may already be the
    successor's. Returns True when it rewrote.

    `stopped` is the shutdown flag, re-checked under `_publish_lock` right
    before the write: shutdown sets it and then unlinks the file, and a guard
    already past its read would otherwise republish a stale port/token on top
    of the unlink — breaking the §3 guarantee that a signal-driven stop leaves
    no backend.json behind. The lock makes the check-then-write atomic against
    that unlink, so the two can only interleave in a harmless order."""
    try:
        json.loads(paths.backend_json().read_text())
        return False
    except (OSError, ValueError):
        with _publish_lock:
            if stopped is not None and stopped():
                return False
            paths.ensure_dirs()
            atomic_write_text(paths.backend_json(), payload, mode=0o600)
            return True


def main() -> None:
    paths.ensure_dirs()
    trim_logs()  # before route_logs: the trim must find no handle held open
    route_logs()
    store.load_all()
    api.marketplace_store.load()  # §22.2: the sources store, loaded once at startup
    # Bind before publishing: uvicorn serves on this very socket, so the port
    # is ours the moment backend.json exists. Probing a free port, closing it,
    # and letting uvicorn rebind would leave a gap where another process can
    # take the port — with backend.json (auth token included) already pointing
    # clients at it.
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", int(os.environ.get("AUTOWRIGHT_PORT", 0))))
    # §3: listen before publishing too — bind alone reserves the port but
    # refuses connects until listen; a client reading backend.json the moment
    # it appears must be queued in the backlog, not refused, while uvicorn
    # (which serves on this very socket) finishes starting.
    sock.listen()
    port = sock.getsockname()[1]
    # §3 discovery: port + auth token, 0600. `python` feeds the shell's
    # CLI-on-PATH shim (§3) so it execs the interpreter that runs the backend.
    discovery = json.dumps({
        "port": port, "token": api.AUTH_TOKEN, "version": __version__, "pid": os.getpid(),
        "python": paths.console_python(),
    })
    stop_guard = threading.Event()

    def unlink_own_backend_json() -> None:
        # Only remove our own discovery file: during a service restart the
        # successor may already have published its fresh port/token here —
        # deleting that would strand the UI/CLI on nothing (§3). Under the
        # same lock the guard writes beneath, so it cannot resurrect the file.
        with _publish_lock:
            try:
                if json.loads(paths.backend_json().read_text()).get("pid") == os.getpid():
                    paths.backend_json().unlink()
            except (OSError, ValueError):
                pass

    # §3: uvicorn owns SIGTERM/SIGINT only once Server.run() installs its
    # handlers — a stop signal landing between the publish below and that
    # installation would die on the default handler and leave backend.json
    # behind. Until (and again after) run(): unlink our own file, then die by
    # the signal's default action, so exit semantics match a plain kill.
    def _early_term(signum: int, _frame: object) -> None:
        stop_guard.set()
        unlink_own_backend_json()
        signal.signal(signum, signal.SIG_DFL)
        signal.raise_signal(signum)

    signal.signal(signal.SIGTERM, _early_term)
    signal.signal(signal.SIGINT, _early_term)

    atomic_write_text(paths.backend_json(), discovery, mode=0o600)
    # §3 discovery guard: backend.json is written once at boot — if something
    # deletes it while we live, clients are stranded forever. Re-publish it.

    def _guard() -> None:
        while not stop_guard.wait(10):
            # The flag is re-checked inside, immediately before the write: a
            # shutdown landing mid-republish must win, not be undone.
            republish_if_lost(discovery, stop_guard.is_set)

    threading.Thread(target=_guard, name="backend-json-guard", daemon=True).start()
    scheduler = Scheduler(store, api.engine)
    listeners = Listeners(store, api.engine)  # §6 message-trigger listener manager

    # Started from the api lifespan, after _repair_stale_executing — a tick
    # before the repair could act on DB-restored header-only records (see
    # api.register_startup). stop() before a never-run start() is safe.
    def startup() -> None:
        scheduler.start()
        listeners.start()

    api.register_startup(startup)

    # §3: all shutdown work runs from the api lifespan — uvicorn re-raises the
    # captured SIGTERM once run() returns, so the `finally` below never
    # executes on a signal-driven stop (a launchd bootout, quit-all, reset).
    # Run-once: the finally still covers a run() that returns without a signal.
    # Two halves, in the order §3 requires: quiesce (stop producing work) runs
    # before the lifespan kills live executions and drafting harnesses, the
    # rest after. Each half is guarded by its own Event so it runs exactly
    # once whichever path reaches it.
    quiesce_done = threading.Event()
    shutdown_done = threading.Event()

    def quiesce() -> None:
        if quiesce_done.is_set():
            return
        quiesce_done.set()
        scheduler.stop()
        listeners.stop()

    def shutdown() -> None:
        if shutdown_done.is_set():
            return
        shutdown_done.set()
        quiesce()  # defensively: a run() that returned without ever running the lifespan
        stop_guard.set()  # before the unlink below — the guard must not resurrect the file
        unlink_own_backend_json()

    api.register_quiesce(quiesce)
    api.register_shutdown(shutdown)
    # §3/§4.9 permanent assertion, through the §2 platform layer.
    platform.current().power.reconcile(bool(store.settings.get("keepAwake")))
    # §4.9 developerMode: request logging (every HTTP request via the uvicorn access
    # log, every agent request via autowright.harness) prints only while the
    # Settings toggle is on. The filter reads the live setting, so flipping the
    # toggle applies immediately — no restart. WARNING+ always prints.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(message)s")
    logging.getLogger().handlers[0].addFilter(_DevModeFilter())
    # uvicorn's dictConfig would wipe filters added to its loggers up front, so
    # the filter rides in on its log_config handlers instead.
    log_config = copy.deepcopy(uvicorn.config.LOGGING_CONFIG)
    log_config["filters"] = {"devmode": {"()": _DevModeFilter}}
    for handler in log_config["handlers"].values():
        handler.setdefault("filters", []).append("devmode")
    try:
        config = uvicorn.Config(api.app, host="127.0.0.1", port=port,
                                log_level="info", log_config=log_config,
                                timeout_graceful_shutdown=SHUTDOWN_GRACE_S)
        uvicorn.Server(config).run(sockets=[sock])
    finally:
        shutdown()


if __name__ == "__main__":
    main()
