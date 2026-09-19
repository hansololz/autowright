"""Packages audit regressions (2026-09-18).

§6.2 installed means the distribution AND the declared `import` target beside
it (a `.dist-info` whose module files a killed pip never finished copying is
missing, so ensure repairs it), and §19's `wait: true` never pins a request
worker longer than one install could take.
"""
import threading

import pytest

from autowright import packages


def _dist_info(home, name="leftpad", version="1.0.0"):
    """Only the dist-info — what a killed pip leaves behind."""
    d = home / "site-packages" / f"{name}-{version}.dist-info"
    d.mkdir(parents=True)
    (d / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8")
    import importlib

    importlib.invalidate_caches()
    packages.invalidate_scan()
    return d


def _status(pip="leftpad", import_name="leftpad"):
    return packages.check([{"pip": pip, "import": import_name}])[0]


# ---------- §6.2: the import target has to be there too ----------

def test_a_dist_info_without_its_module_reads_as_missing(home):
    """§6.2: so ensure re-runs pip for it instead of reporting installed
    forever."""
    _dist_info(home)
    assert _status() == {"pip": "leftpad", "import": "leftpad", "status": "missing"}


@pytest.mark.parametrize("make", [
    lambda d: (d / "leftpad").mkdir() or (d / "leftpad" / "__init__.py").write_text(""),
    lambda d: (d / "leftpad.py").write_text("", encoding="utf-8"),
    lambda d: (d / "leftpad.cpython-314-darwin.so").write_bytes(b"\x00"),
    lambda d: (d / "leftpad.pyd").write_bytes(b"\x00"),
])
def test_a_package_a_module_and_an_extension_all_count(home, make):
    _dist_info(home)
    make(packages.site_packages_dir())
    packages.invalidate_scan()
    assert _status()["status"] == "installed"


def test_the_first_dotted_segment_is_what_has_to_be_there(home):
    _dist_info(home, "pyyaml", "6.0.2")
    assert _status("pyyaml", "yaml.cyaml")["status"] == "missing"
    (packages.site_packages_dir() / "yaml").mkdir()
    packages.invalidate_scan()
    assert _status("pyyaml", "yaml.cyaml")["status"] == "installed"


def test_an_entry_without_an_import_name_keeps_the_old_rule(home):
    """§6.2: nothing to look for beside the distribution."""
    _dist_info(home)
    assert _status(import_name="")["status"] == "installed"


def test_a_missing_distribution_is_still_missing(home):
    (packages.site_packages_dir() / "leftpad").mkdir(parents=True)
    packages.invalidate_scan()
    assert _status()["status"] == "missing"


# ---------- §19: the wait is bounded ----------

def test_a_waiting_install_gives_up_at_the_install_timeout(home, monkeypatch):
    """§19: "the wait is bounded by the per-package install timeout, after
    which the request answers the same 409"."""
    monkeypatch.setattr(packages, "INSTALL_TIMEOUT", 0.0)
    held, release = threading.Event(), threading.Event()

    def hold_the_lock():
        with packages._pip_lock:
            held.set()
            release.wait(20)

    holder = threading.Thread(target=hold_the_lock, daemon=True)
    holder.start()
    assert held.wait(5), "the lock holder never started"
    try:
        with pytest.raises(packages.PackagesBusy):
            packages.ensure([{"pip": "leftpad", "import": "leftpad"}], wait=True)
    finally:
        release.set()
        holder.join(5)


def test_a_cancel_still_beats_the_bounded_wait(home, monkeypatch):
    """§7: `should_stop` is honored while waiting — the deadline never turns a
    cancel into a busy answer."""
    monkeypatch.setattr(packages, "INSTALL_TIMEOUT", 30)
    held, release = threading.Event(), threading.Event()

    def hold_the_lock():
        with packages._pip_lock:
            held.set()
            release.wait(20)

    holder = threading.Thread(target=hold_the_lock, daemon=True)
    holder.start()
    assert held.wait(5), "the lock holder never started"
    try:
        out = packages.ensure([{"pip": "leftpad", "import": "leftpad"}],
                              should_stop=lambda: True, wait=True)
        assert out[0]["status"] == "failed"
    finally:
        release.set()
        holder.join(5)
