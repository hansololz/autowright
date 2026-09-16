"""§2 platform layer — the macOS build.

Composes the darwin implementations: osascript notifications (the code lives
here), the caffeinate keepAwake assertion (delegates to awake.py — that
module holds the caffeinate state the §15 suites pin), the launchd service
manager (delegates to service.py — the launchd implementation module whose
internals the §15 suites pin), and shared POSIX process control.
"""
from __future__ import annotations

from collections.abc import Callable

from . import posixproc
from .base import Capabilities, Platform, run_bounded


class OsascriptNotifier:
    """§6 decided: the backend posts via osascript, works headless.
    Best-effort — never raises, never blocks past the timeout."""

    def post(self, title: str, body: str) -> None:
        esc_t = title.replace("\\", "\\\\").replace('"', '\\"')
        esc_b = body.replace("\\", "\\\\").replace('"', '\\"')
        try:
            # Bounded: a wedged osascript (it blocks on its own Apple event)
            # must never hold the caller — run_bounded kills the group and
            # returns instead of blocking on a grandchild's pipe.
            run_bounded(
                ["osascript", "-e",
                 f'display notification "{esc_b}" with title "{esc_t}"'],
                timeout=10,
            )
        except Exception:
            pass


class CaffeinatePower:
    """§3/§4.9 idle-sleep assertions — delegates to awake.py, which owns the
    caffeinate subprocesses (module attribute lookup at call time, so the §15
    monkeypatches on that module keep working)."""

    def reconcile(self, enabled: bool) -> None:
        from .. import awake

        awake.reconcile(enabled)

    def hold_execution(self) -> Callable[[], None]:
        from .. import awake

        return awake.hold_execution()


class LaunchdService:
    """§3 service lifecycle — delegates to service.py, the launchd
    implementation module (module attribute lookup at call time, same
    monkeypatch rule as above)."""

    def install(self) -> str:
        from .. import service

        return service.install()

    def uninstall(self) -> str:
        from .. import service

        return service.uninstall()

    def status(self) -> str:
        from .. import service

        return service.status()

    def stop(self) -> str:
        from .. import service

        return service.stop()

    def restart(self) -> str:
        from .. import service

        return service.restart()


def build() -> Platform:
    return Platform(
        os_token="macos",
        service=LaunchdService(),
        notifier=OsascriptNotifier(),
        power=CaffeinatePower(),
        processes=posixproc.PosixProcessControl(),
        capabilities=Capabilities(imessage=True, notifications=True,
                                  keep_awake=True, service=True,
                                  agent_install=True),
    )
