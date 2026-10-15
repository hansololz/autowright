"""Service and listener audit regressions (2026-10-15).

§3: `status` on a timed-out launchctl list is a failed status (exit 1, as on
Linux and Windows); the no-plist `stop` reports what the label bootout found
- "stopped" for a registration it booted out, "already stopped" only when
nothing was registered; Linux `stop` and `uninstall` reach the unit by name
with its file gone. §6: no Discord connection starts after the listener
manager's `stop()` returned.
"""
import os
import threading

from test_audit_1014_service import launchd, launchd_only  # noqa: F401 - fixture
from test_service import svc  # noqa: F401 - fixture


# ---------- §3 macOS: a timed-out status is a failed status ----------

@launchd_only
def test_status_on_a_timed_out_list_exits_one(launchd, monkeypatch):  # noqa: F811
    monkeypatch.setattr(launchd.mod, "_launchctl", lambda *a: launchd.mod._TimedOut())
    out = launchd.mod.status()
    assert out == ("status failed: launchctl timed out — "
                   "can't tell whether the service is running")
    assert launchd.mod.result_code(out) == 1


# ---------- §3 macOS: the no-plist stop reports what the bootout found ----------

@launchd_only
def test_no_plist_stop_of_a_registered_job_says_stopped(svc):  # noqa: F811
    svc.registered["job"] = True  # a registration that outlived its plist
    assert not svc.plist.exists()
    out = svc.mod.stop()
    assert "stopped" in out
    assert "already stopped" not in out
    assert out == "stopped — service was not installed"
    assert svc.mod.result_code(out) == 0
    assert ["launchctl", "bootout", f"gui/{os.getuid()}/{svc.mod.LABEL}"] in svc.actions()
    assert svc.registered["job"] is False


@launchd_only
def test_no_plist_stop_of_a_registered_job_reports_the_sweep(svc, monkeypatch):  # noqa: F811
    svc.registered["job"] = True
    monkeypatch.setattr(svc.mod, "_sweep_strays", lambda: 2)
    out = svc.mod.stop()
    assert out == "stopped — service was not installed; ended 2 lingering process(es)"


@launchd_only
def test_no_plist_stop_with_nothing_registered_is_already_stopped(launchd):  # noqa: F811
    launchd.plist.unlink()
    launchd.answers["registered"] = [False]
    out = launchd.mod.stop()
    assert out == "already stopped — service not installed, nothing was running"


@launchd_only
def test_no_plist_stop_after_an_unknown_probe_is_not_already_stopped(launchd):  # noqa: F811
    # Only a definite not-registered before the bootout reads as "already".
    launchd.plist.unlink()
    launchd.answers["registered"] = [None, False]
    out = launchd.mod.stop()
    assert out == "stopped — service was not installed"


@launchd_only
def test_no_plist_stop_that_times_out_still_fails(launchd):  # noqa: F811
    launchd.plist.unlink()
    launchd.answers["registered"] = [True, None]
    out = launchd.mod.stop()
    assert out == "stop failed: launchctl timed out"
    assert launchd.mod.result_code(out) == 1


# ---------- §3 Linux: the unit is reached by name with its file gone ----------

def test_linux_stop_without_a_unit_file_still_stops_the_unit(systemd):
    from autowright import service

    assert not systemd.mod.unit_path().exists()
    out = systemd.service.stop()
    assert ("stop", "ai.autowright.backend.service") in systemd.calls
    assert systemd.mod.UNIT_NAME == "ai.autowright.backend.service"
    assert service.result_code(out) == 0


def test_linux_stop_without_a_unit_file_tolerates_an_unknown_unit(systemd):
    from autowright import service

    systemd.canned["stop"] = (5, "", "Failed to stop unit: Unit not loaded.")
    out = systemd.service.stop()
    assert out == "already stopped — service not installed, nothing was running"
    assert service.result_code(out) == 0


def test_linux_stop_without_a_unit_file_fails_on_a_timeout(systemd):
    from autowright import service

    systemd.canned["stop"] = systemd.mod._TimedOut()
    out = systemd.service.stop()
    assert out == "stop failed: systemctl timed out"
    assert service.result_code(out) == 1


def test_linux_uninstall_without_a_unit_file_still_disables_the_unit(systemd):
    from autowright import service

    assert not systemd.mod.unit_path().exists()
    out = systemd.service.uninstall()
    assert ("disable", "--now", "ai.autowright.backend.service") in systemd.calls
    assert out == "service was not installed"
    assert service.result_code(out) == 0


def test_linux_uninstall_without_a_unit_file_tolerates_an_unknown_unit(systemd):
    systemd.canned["disable"] = (1, "", "Failed to disable unit: Unit file does not exist.")
    assert systemd.service.uninstall() == "service was not installed"


def test_linux_uninstall_without_a_unit_file_fails_on_a_timeout(systemd):
    from autowright import service

    systemd.canned["disable"] = systemd.mod._TimedOut()
    out = systemd.service.uninstall()
    assert out == "uninstall failed: couldn't stop the unit (systemctl timed out)"
    assert service.result_code(out) == 1


# ---------- §6: no connection starts after stop() returned ----------

def test_no_connection_starts_after_stop_returned(monkeypatch):
    from autowright import listeners

    entered = threading.Event()
    gate = threading.Event()
    log: list[tuple[str, str]] = []

    class FakeConn:
        def __init__(self, secret, manager):
            self.secret = secret
            # Hold the reconcile between its stop-flag check and the insert -
            # the window a concurrent stop() used to slip through.
            entered.set()
            gate.wait(5)

        def start(self):
            log.append(("start", self.secret))

        def stop(self):
            log.append(("stop", self.secret))

        def is_alive(self):
            return True

    monkeypatch.setattr(listeners, "_Conn", FakeConn)
    manager = listeners.Listeners(store=None, engine=None)
    monkeypatch.setattr(manager, "_desired_secrets", lambda: ({"secret-1"}, set()))

    reconcile = threading.Thread(target=manager._reconcile, daemon=True)
    reconcile.start()
    assert entered.wait(5)

    stopped = threading.Event()

    def stop():
        manager.stop()
        log.append(("stop() returned", ""))
        stopped.set()

    stopper = threading.Thread(target=stop, daemon=True)
    stopper.start()
    # stop() sets its flag while the reconcile is inside the window.
    stopper.join(0.2)
    gate.set()
    reconcile.join(5)
    stopper.join(5)
    assert stopped.is_set()
    returned = log.index(("stop() returned", ""))
    assert ("start", "secret-1") not in log[returned:]
    # Whatever started was in stop()'s snapshot and was stopped.
    if ("start", "secret-1") in log:
        assert ("stop", "secret-1") in log[:returned]
