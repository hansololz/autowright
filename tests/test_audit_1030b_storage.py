"""2026-10-30 second sweep (storage/packages): crash-orphan version folders
set aside before numbering, duplicate dist-info dirs tidied after pip and the
newest-written one preferred, the shared step timeout/retries readers, the
md_to_blocks line split, Update's re-check after the pip lock, and a
non-text automation description."""
import datetime
import os
import shutil
from pathlib import Path

import pytest
import yaml

from conftest import make_version


def _fake_dist(home, name, version, mtime=None):
    d = home / "site-packages" / f"{name}-{version}.dist-info"
    d.mkdir(parents=True)
    (d / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8")
    module = home / "site-packages" / name.replace("-", "_").lower()
    module.mkdir(parents=True, exist_ok=True)
    (module / "__init__.py").write_text("", encoding="utf-8")
    if mtime is not None:
        os.utime(d, (mtime, mtime))
    import importlib

    importlib.invalidate_caches()
    from autowright import packages

    packages.invalidate_scan()
    return d


class _OkProc:
    """subprocess.Popen double: pip exits 0 without writing anything."""
    calls: list[list] = []

    def __init__(self, cmd, **kwargs):
        _OkProc.calls.append(cmd)
        self.pid = 4242
        self.returncode = 0

    def communicate(self, timeout=None):
        return "", ""

    def poll(self):
        return self.returncode


# ---------- S1 §5 a crash orphan is set aside before numbering ----------

def test_a_crash_orphan_is_set_aside_and_never_adopted_after_the_next_save(store):
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Orphaned", "agent-1")
    store.save_new_version(a, make_version(note="v2"))
    store.save_new_version(a, make_version(note="v3"))
    versions = store.auto_dir(a) / "versions"
    shutil.copytree(versions / "v3", versions / "v4")  # manifest landed, flip never did

    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    assert b["current_version"] == 3
    assert s2.save_new_version(b, make_version(note="Next")) == 4
    assert (versions / "v4.orphan" / "automation.yaml").exists()

    s3 = Store()
    s3.load_all()
    c = s3.autos[a["id"]]
    assert set(c["versions"]) == {1, 2, 3, 4}
    assert c["versions"][4].get("note") == "Next"  # the real save, not the orphan


def test_orphan_rename_picks_the_next_free_suffix_on_restore(store):
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Twice", "agent-1")
    versions = store.auto_dir(a) / "versions"
    (versions / "v2.orphan").mkdir()
    (versions / "v2.orphan-2").mkdir()
    shutil.copytree(versions / "v1", versions / "v2")

    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    assert s2.restore_version(b, 1) == 2
    assert (versions / "v2.orphan-3" / "automation.yaml").exists()

    s3 = Store()
    s3.load_all()
    assert set(s3.autos[a["id"]]["versions"]) == {1, 2}


# ---------- S2 §6.2 duplicate dist-info dirs ----------

def test_installed_check_prefers_the_newest_written_dist_info(home):
    from autowright import packages

    old = _fake_dist(home, "requests", "2.31.0", mtime=1_000_000)
    new = _fake_dist(home, "requests", "2.32.0", mtime=2_000_000)
    assert packages._installed_versions()["requests"] == "2.32.0"
    # Directory order never decides: swap which one is newest.
    os.utime(old, (3_000_000, 3_000_000))
    packages.invalidate_scan()
    assert packages._installed_versions()["requests"] == "2.31.0"
    assert new.exists()


@pytest.mark.parametrize("operation", ["ensure", "upgrade"])
def test_a_successful_pip_run_removes_stale_dist_info(home, monkeypatch, operation):
    from autowright import packages

    monkeypatch.setattr(packages.subprocess, "Popen", _OkProc)
    old = _fake_dist(home, "requests", "2.31.0", mtime=1_000_000)
    new = _fake_dist(home, "requests", "2.32.0", mtime=2_000_000)
    other = _fake_dist(home, "idna", "3.7", mtime=1_000_000)
    if operation == "ensure":
        packages.ensure([{"pip": "leftpad", "import": "leftpad"}])
    else:
        results = packages.upgrade([{"pip": "requests", "import": "requests"}])
        assert results[0]["version"] == "2.32.0"
    assert not old.exists()
    assert new.exists()
    assert other.exists()


def test_a_failed_pip_run_leaves_the_dist_info_alone(home, monkeypatch):
    from autowright import packages

    class _FailProc(_OkProc):
        def __init__(self, cmd, **kwargs):
            super().__init__(cmd, **kwargs)
            self.returncode = 1

    monkeypatch.setattr(packages.subprocess, "Popen", _FailProc)
    old = _fake_dist(home, "requests", "2.31.0", mtime=1_000_000)
    _fake_dist(home, "requests", "2.32.0", mtime=2_000_000)
    packages.upgrade([{"pip": "requests", "import": "requests"}])
    assert old.exists()


# ---------- S4+E4 §4.1 one reader for timeout/retries ----------

@pytest.mark.parametrize("stored, engine_retries, ui_retries", [
    (500, 0, None),
    ("3", 3, 3),
    (3, 3, 3),
    (10, 10, 10),
    (11, 0, None),
    (0, 0, None),
    (-2, 0, None),
    (float("inf"), 0, None),
    (True, 0, None),
    ("abc", 0, None),
])
def test_retries_read_the_same_in_the_engine_and_the_ui(stored, engine_retries, ui_retries):
    from autowright.engine import step_retries_for
    from autowright.storage import manifest_step_entry, step_json

    step = {"name": "s", "retries": stored}
    assert step_retries_for(step) == engine_retries
    assert step_json(step).get("retries") == ui_retries
    assert manifest_step_entry(step, "s.py").get("retries") == ui_retries


@pytest.mark.parametrize("stored, expected", [
    (float("inf"), None),
    (float("nan"), None),
    ("30", 30),
    (30, 30),
    (0, None),
    (-5, None),
    (True, None),
    ("abc", None),
    (1e300, None),  # finite, but the §6 watchdog timer overflows on it: reads as absent
    (365 * 86400, 365 * 86400),
    (365 * 86400 + 1, None),
])
def test_timeout_reads_the_same_in_the_engine_and_the_ui(stored, expected, monkeypatch):
    from autowright import engine
    from autowright.storage import manifest_step_entry, step_json

    monkeypatch.delenv("AUTOWRIGHT_STEP_TIMEOUT", raising=False)
    step = {"name": "s", "timeout": stored}
    assert engine.step_timeout_for(step) == (float(expected) if expected else engine.STEP_TIMEOUT)
    assert step_json(step).get("timeout") == expected
    assert manifest_step_entry(step, "s.py").get("timeout") == expected


def test_no_timeout_still_disables_the_watchdog():
    from autowright.engine import step_timeout_for

    assert step_timeout_for({"no_timeout": True, "timeout": 30}) is None


# ---------- S5 md_to_blocks splits on "\n" only ----------

def test_md_to_blocks_keeps_a_line_separator_inside_a_heading():
    from autowright.specmd import md_to_blocks

    blocks = md_to_blocks("# One Two\r\nbody\rmore\n\n## Next")
    assert blocks[0] == {"kind": "h1", "text": "One Two"}
    assert blocks[1]["kind"] == "p" and blocks[1]["text"] == "body more"
    assert blocks[2] == {"kind": "h2", "text": "Next"}


# ---------- S6 §6.2 Update re-checks after the pip lock ----------

def test_update_rechecks_after_waiting_for_the_pip_lock(home, monkeypatch):
    from autowright import packages

    monkeypatch.setattr(packages.subprocess, "Popen", _OkProc)
    real_acquire = packages._acquire_pip_lock

    def acquire_while_a_concurrent_ensure_installs(wait, should_stop):
        _fake_dist(home, "requests", "2.32.0")
        real_acquire(wait, should_stop)

    monkeypatch.setattr(packages, "_acquire_pip_lock", acquire_while_a_concurrent_ensure_installs)
    results = packages.upgrade([{"pip": "requests", "import": "requests"}])
    assert results[0]["status"] == "installed"
    assert results[0].get("error") is None
    assert results[0]["version"] == "2.32.0"


# ---------- A1 §5 a non-text description loads as text ----------

@pytest.mark.parametrize("stored, expected", [
    (datetime.date(2026, 10, 30), "2026-10-30"),
    (42, "42"),
    (["a", "b"], ""),
    ({"k": "v"}, ""),
    (None, ""),
])
def test_a_non_text_description_loads_as_text(store, stored, expected):
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Described", "agent-1")
    path = store.auto_dir(a) / "automation.yaml"
    top = yaml.safe_load(path.read_text(encoding="utf-8"))
    top["description"] = stored
    path.write_text(yaml.safe_dump(top), encoding="utf-8")
    assert "description: 2026-10-30\n" in path.read_text(encoding="utf-8") \
        or not isinstance(stored, datetime.date)

    s2 = Store()
    s2.load_all()
    assert s2.autos[a["id"]]["description"] == expected
