"""Service tri-state audit regressions (2026-10-14).

§3: `uninstall` is `stop` plus removal — the plist and shim go only after a
clean verdict; `stop` with no plist still boots the job out by label; the
unload wait keeps waiting on an unknown probe; `_load` never reads an unknown
probe as "not registered"; `status` on a timed-out list says so; the Linux
`uninstall` keeps the unit file when its `disable --now` fails.
"""
import os
import sys
from types import SimpleNamespace

import pytest

launchd_only = pytest.mark.skipif(sys.platform != "darwin",
                                  reason="launchd implementation (service.py)")


@pytest.fixture()
def launchd(home, monkeypatch, poll_clock):
    """service.py with the plist and shim in the test home, `_launchctl`
    recorded, and `_registered` answering from a scripted sequence (its last
    answer repeats)."""
    from autowright import service

    plist = home / "LaunchAgents" / f"{service.LABEL}.plist"
    plist.parent.mkdir(parents=True)
    plist.write_bytes(b"<plist/>")
    shim = home / "bin" / "autowright"
    shim.parent.mkdir(parents=True)
    shim.write_text(service.shim_text())
    monkeypatch.setattr(service, "plist_path", lambda: plist)
    monkeypatch.setattr(service, "shim_paths", lambda: [shim])

    calls = []
    answers = {"registered": [False]}

    def fake_launchctl(*args):
        calls.append(list(args))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def fake_registered():
        seq = answers["registered"]
        return seq.pop(0) if len(seq) > 1 else seq[0]

    monkeypatch.setattr(service, "_launchctl", fake_launchctl)
    monkeypatch.setattr(service, "_registered", fake_registered)
    clock = poll_clock(service)  # no real waits inside the deadline polls
    return SimpleNamespace(mod=service, plist=plist, shim=shim, calls=calls,
                           answers=answers, clock=clock)


@launchd_only
def test_uninstall_keeps_plist_and_shim_while_launchd_still_holds_the_job(launchd):
    launchd.answers["registered"] = [True]
    out = launchd.mod.uninstall()
    assert out == "uninstall failed: launchd still reports the job"
    assert launchd.mod.result_code(out) == 1
    assert launchd.plist.exists()
    assert launchd.shim.exists()


@launchd_only
def test_uninstall_keeps_plist_and_shim_when_launchctl_timed_out(launchd):
    launchd.answers["registered"] = [None]
    out = launchd.mod.uninstall()
    assert out == "uninstall failed: launchctl timed out"
    assert launchd.mod.result_code(out) == 1
    assert launchd.plist.exists()
    assert launchd.shim.exists()


@launchd_only
def test_uninstall_removes_plist_and_shim_on_a_clean_verdict(launchd, monkeypatch):
    monkeypatch.setattr(launchd.mod, "_sweep_strays", lambda: 2)
    out = launchd.mod.uninstall()
    assert out == "service unloaded and removed · ended 2 lingering process(es)"
    assert launchd.mod.result_code(out) == 0
    assert not launchd.plist.exists()
    assert not launchd.shim.exists()


@launchd_only
def test_stop_without_a_plist_still_boots_the_job_out_by_label(launchd):
    launchd.plist.unlink()
    out = launchd.mod.stop()
    assert out == "already stopped — service not installed, nothing was running"
    assert launchd.calls[0] == ["bootout", f"gui/{os.getuid()}/{launchd.mod.LABEL}"]


@launchd_only
def test_stop_without_a_plist_fails_while_launchd_still_holds_the_job(launchd):
    launchd.plist.unlink()
    launchd.answers["registered"] = [True]
    out = launchd.mod.stop()
    assert out == "stop failed: launchd still reports the job"
    assert launchd.mod.result_code(out) == 1
    assert ["bootout", f"gui/{os.getuid()}/{launchd.mod.LABEL}"] in launchd.calls


@launchd_only
def test_unload_keeps_waiting_on_an_unknown_probe(launchd):
    # Unknown three times, then gone: the wait outlasts the unknowns instead
    # of reading the first one as "not registered".
    launchd.answers["registered"] = [None, None, None, False]
    launchd.mod._unload(launchd.plist)
    assert launchd.answers["registered"] == [False]
    assert launchd.clock.now < launchd.mod._UNLOAD_DEADLINE_S


@launchd_only
def test_unload_wait_on_unknown_ends_at_its_deadline(launchd):
    launchd.answers["registered"] = [None]
    launchd.mod._unload(launchd.plist)
    assert launchd.clock.now >= launchd.mod._UNLOAD_DEADLINE_S


@launchd_only
def test_load_reads_an_unknown_probe_as_timed_out(launchd):
    launchd.answers["registered"] = [None]
    assert launchd.mod._load(launchd.plist) == "launchctl timed out"
    launchd.answers["registered"] = [False]
    assert launchd.mod._load(launchd.plist) == \
        "launchctl accepted the plist but the job is not registered"


@launchd_only
def test_status_on_a_timed_out_list_says_so(launchd, monkeypatch):
    monkeypatch.setattr(launchd.mod, "_launchctl", lambda *a: launchd.mod._TimedOut())
    assert launchd.mod.status() == \
        "launchctl timed out — can't tell whether the service is running"


# ---------- §3 Linux: a failed `disable --now` keeps the unit ----------

def _install_unit(systemd):
    unit = systemd.mod.unit_path()
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text(systemd.mod.unit_text())
    return unit


def test_linux_uninstall_keeps_the_unit_when_disable_fails(systemd):
    from autowright import service

    unit = _install_unit(systemd)
    systemd.canned["disable"] = (1, "", "Failed to stop the unit")
    out = systemd.service.uninstall()
    assert out == "uninstall failed: couldn't stop the unit (Failed to stop the unit)"
    assert service.result_code(out) == 1
    assert unit.exists()


def test_linux_uninstall_keeps_the_unit_when_disable_times_out(systemd):
    from autowright import service

    unit = _install_unit(systemd)
    systemd.canned["disable"] = systemd.mod._TimedOut()
    out = systemd.service.uninstall()
    assert out.startswith("uninstall failed: couldn't stop the unit (")
    assert systemd.mod._TIMED_OUT in out
    assert service.result_code(out) == 1
    assert unit.exists()


def test_linux_uninstall_removes_the_unit_on_a_clean_disable(systemd):
    unit = _install_unit(systemd)
    assert systemd.service.uninstall() == "service stopped and unregistered"
    assert not unit.exists()
