"""2026-10-30 audit batch (storage/packages): ensure runs pip without
`--upgrade`, an Update pins the other declared packages, the Windows rename
retry, crash-orphan version folders ignored at load, the legacy draft
instructions.md carried across a draft save, skipped agent/secret rows written
back raw, a missing sealed step file counted as step-modified, fresh trigger
ids for missing/duplicate ids, Windows device names refused as filenames, a
marketplace table that can't be saved at load, step_json's lenient
timeout/retries, the `.inf` concurrency clamps, and `nextTriggerShort`."""
from pathlib import Path

import pytest
import yaml

from conftest import make_version


def _fake_dist(home, name, version):
    d = home / "site-packages" / f"{name}-{version}.dist-info"
    d.mkdir(parents=True)
    (d / "METADATA").write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n", encoding="utf-8")
    module = home / "site-packages" / name.replace("-", "_").lower()
    module.mkdir(parents=True, exist_ok=True)
    (module / "__init__.py").write_text("", encoding="utf-8")
    import importlib

    importlib.invalidate_caches()
    from autowright import packages

    packages.invalidate_scan()


class _RecordingProc:
    """subprocess.Popen double: records the argv and the constraints text."""
    calls: list[dict] = []

    def __init__(self, cmd, **kwargs):
        constraints = None
        if "-c" in cmd:
            constraints = Path(cmd[cmd.index("-c") + 1]).read_text()
        _RecordingProc.calls.append({"cmd": cmd, "constraints": constraints})
        self.pid = 4242
        self.returncode = 0

    def communicate(self, timeout=None):
        return "", ""

    def poll(self):
        return self.returncode


# ---------- S1 §6.2 ensure never passes --upgrade; the Update does ----------

def test_ensure_runs_pip_without_upgrade_and_update_with_it(home, monkeypatch):
    from autowright import packages

    _RecordingProc.calls = []
    monkeypatch.setattr(packages.subprocess, "Popen", _RecordingProc)
    _fake_dist(home, "requests", "2.31.0")
    packages.ensure([{"pip": "leftpad", "import": "leftpad"}])
    ensure_cmd = _RecordingProc.calls[-1]["cmd"]
    assert ensure_cmd[-1] == "leftpad"
    assert "--upgrade" not in ensure_cmd
    # ensure still pins every installed distribution
    assert _RecordingProc.calls[-1]["constraints"] == "requests==2.31.0\n"

    packages.upgrade([{"pip": "requests", "import": "requests"}])
    update_cmd = _RecordingProc.calls[-1]["cmd"]
    assert update_cmd[-1] == "requests"
    assert "--upgrade" in update_cmd


# ---------- S2 §6.2 an Update pins every OTHER declared package ----------

def test_update_pins_the_other_declared_packages_only(home, monkeypatch):
    from autowright import packages

    _RecordingProc.calls = []
    monkeypatch.setattr(packages.subprocess, "Popen", _RecordingProc)
    _fake_dist(home, "requests", "2.31.0")
    _fake_dist(home, "PyYAML", "6.0.2")
    _fake_dist(home, "numpy", "2.0.0")  # an undeclared transitive dependency
    packages.upgrade([{"pip": "requests", "import": "requests"}],
                     declared={"requests", "PyYAML", "leftpad"})
    # requests is the one being updated; numpy is undeclared; leftpad isn't installed
    assert set(_RecordingProc.calls[-1]["constraints"].splitlines()) == {"pyyaml==6.0.2"}


def test_declared_package_names_is_the_union_of_current_versions(store):
    store.create_automation(make_version(packages=[{"pip": "requests", "import": "requests"}]),
                            "One", "agent-1")
    store.create_automation(make_version(packages=[{"pip": "PyYAML", "import": "yaml"},
                                                   {"pip": "requests", "import": "requests"}]),
                            "Two", "agent-1")
    assert store.declared_package_names() == {"requests", "PyYAML"}


# ---------- S5 §5 the Windows rename retry ----------

def test_atomic_write_retries_the_rename_on_windows(home, monkeypatch):
    from autowright import yamlio

    real_replace = yamlio.os.replace
    attempts = []
    delays = []

    def flaky_replace(source, target):
        attempts.append(target)
        if len(attempts) <= 3:
            raise PermissionError(32, "sharing violation")
        real_replace(source, target)

    monkeypatch.setattr(yamlio.os, "replace", flaky_replace)
    monkeypatch.setattr(yamlio.time, "sleep", delays.append)
    monkeypatch.setattr(yamlio.os, "name", "nt")
    target = home / "retry.txt"
    yamlio.atomic_write_text(target, "hello")
    monkeypatch.setattr(yamlio.os, "name", "posix")
    assert target.read_text() == "hello"
    assert len(attempts) == 4
    assert delays == [0.02, 0.05, 0.1]


def test_atomic_write_gives_up_after_five_retries_on_windows(home, monkeypatch):
    from autowright import yamlio

    attempts = []
    delays = []

    def locked_replace(source, target):
        attempts.append(target)
        raise PermissionError(32, "sharing violation")

    monkeypatch.setattr(yamlio.os, "replace", locked_replace)
    monkeypatch.setattr(yamlio.time, "sleep", delays.append)
    monkeypatch.setattr(yamlio.os, "name", "nt")
    with pytest.raises(PermissionError):
        yamlio.atomic_write_text(home / "never.txt", "x")
    monkeypatch.setattr(yamlio.os, "name", "posix")
    assert len(attempts) == 6
    assert delays == [0.02, 0.05, 0.1, 0.2, 0.4]
    assert not list(home.glob(".ad-tmp-*"))  # the temp file is cleaned up


def test_atomic_write_never_retries_off_windows(home, monkeypatch):
    from autowright import yamlio

    attempts = []

    def locked_replace(source, target):
        attempts.append(target)
        raise PermissionError(13, "denied")

    monkeypatch.setattr(yamlio.os, "replace", locked_replace)
    with pytest.raises(PermissionError):
        yamlio.atomic_write_text(home / "never.txt", "x")
    assert len(attempts) == 1


# ---------- S6 §5 a version folder above current_version is a crash orphan ----------

def test_a_version_folder_above_current_is_ignored_and_numbered_past(store, home):
    import shutil

    from autowright.storage import Store

    a = store.create_automation(make_version(), "Orphaned", "agent-1")
    versions = store.auto_dir(a) / "versions"
    shutil.copytree(versions / "v1", versions / "v2")  # manifest landed, flip never did

    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    assert b["current_version"] == 1
    assert set(b["versions"]) == {1}
    assert (versions / "v2" / "automation.yaml").exists()  # left on disk
    assert s2.save_new_version(b, make_version(note="Next")) == 3
    assert (versions / "v2" / "automation.yaml").exists()


# ---------- S7 §21.4 the legacy draft instructions.md survives a draft save ----------

def test_save_draft_carries_the_legacy_instructions_md(store):
    a = store.create_automation(make_version(), "Legacy", "agent-1")
    store.save_draft(a, make_version())
    legacy = store.draft_dir(a) / "automation" / "instructions.md"
    legacy.write_text("old build notes\n", encoding="utf-8")
    store.save_draft(a, make_version(note="again"))
    assert legacy.read_text(encoding="utf-8") == "old build notes\n"


# ---------- S8 §4.7/§4.8 skipped rows are written back raw ----------

def test_skipped_agent_and_secret_rows_survive_the_next_save(home):
    from autowright import paths
    from autowright.storage import Store

    paths.agents_file().write_text(yaml.safe_dump({"agents": [
        {"id": "a1", "name": "Good", "harness": "claude"},
        {"name": "Damaged", "harness": "codex"},
    ], "default_agent": "a1"}), encoding="utf-8")
    paths.secrets_file().write_text(yaml.safe_dump({"secrets": [
        {"id": "s1", "name": "TOKEN", "description": "", "set": True},
        {"name": "NO_ID", "description": "kept"},
    ]}), encoding="utf-8")
    s = Store()
    s.load_all()
    assert [g["id"] for g in s.agents] == ["a1"]
    assert [x["id"] for x in s.secrets] == ["s1"]

    s.save_agents([*s.agents, {"id": "a2", "name": "New", "harness": "gemini"}])
    s.save_secrets()
    agents = yaml.safe_load(paths.agents_file().read_text(encoding="utf-8"))["agents"]
    secrets = yaml.safe_load(paths.secrets_file().read_text(encoding="utf-8"))["secrets"]
    assert [g.get("id") for g in agents] == ["a1", "a2", None]
    assert agents[-1] == {"name": "Damaged", "harness": "codex"}
    assert secrets[-1] == {"name": "NO_ID", "description": "kept"}


# ---------- S9 §4.1 a missing sealed step file is step-modified ----------

def test_a_missing_sealed_step_file_is_step_modified_at_load(store):
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Missing", "agent-1")
    (store.auto_dir(a) / "versions" / "v1" / "02-finish.py").unlink()
    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    finish = b["versions"][1]["steps"][1]
    assert finish["modifiedOnDisk"] is True
    kinds = [p["kind"] for p in s2.problems_json(b, b["versions"][1])]
    assert "step-modified" in kinds


def test_duplicate_refuses_when_a_sealed_file_is_missing(store, home):
    from autowright import paths
    from autowright.storage import StepModifiedError

    a = store.create_automation(make_version(), "Missing", "agent-1")
    (store.auto_dir(a) / "versions" / "v1" / "02-finish.py").unlink()
    folders_before = set(p.name for p in paths.automations_dir().iterdir())
    autos_before = set(store.autos)
    with pytest.raises(StepModifiedError):
        store.duplicate_automation(a["id"])
    assert set(store.autos) == autos_before
    assert set(p.name for p in paths.automations_dir().iterdir()) == folders_before


# ---------- S10 §4.3 fresh ids for missing or duplicate trigger ids ----------

def test_missing_and_duplicate_trigger_ids_get_fresh_ids(home):
    from autowright.storage import Store

    triggers = Store._load_triggers([
        {"id": "same", "kind": "cron", "expression": "0 8 * * *", "source": "user",
         "enabled": True},
        {"id": "same", "kind": "cron", "expression": "0 20 * * *", "source": "user",
         "enabled": True},
        {"kind": "cron", "expression": "0 12 * * *", "source": "user", "enabled": True},
    ])
    ids = [t["id"] for t in triggers]
    assert ids[0] == "same"
    assert len(set(ids)) == 3 and all(ids)


# ---------- S11 Windows device names are never filenames ----------

@pytest.mark.parametrize("device", ["nul.py", "con.py", "aux.py", "prn.py",
                                    "com1.py", "com9.py", "lpt1.py", "lpt9.py"])
def test_device_step_filenames_fall_back(device):
    from autowright.storage import safe_step_filename

    assert safe_step_filename(device, 3, "Fetch", set()) == "03-fetch.py"


def test_ordinary_step_filenames_still_pass():
    from autowright.storage import safe_step_filename

    for name in ("console.py", "com10.py", "nullify.py", "01-con.py"):
        assert safe_step_filename(name, 1, "x", set()) == name


def test_safe_filename_refuses_device_names():
    from autowright.paths import is_windows_device_name, safe_filename

    for name in ("CON", "nul", "Aux.txt", "com1.tar.gz", "LPT9", "prn "):
        assert safe_filename(name) == "automation", name
    assert safe_filename("Con Job") == "Con Job"
    assert safe_filename("console") == "console"
    assert is_windows_device_name("NUL.py") and not is_windows_device_name("com0.py")


# ---------- S13 §5 a marketplace table that can't be saved at load ----------

def test_marketplace_load_survives_an_unwritable_table(home, monkeypatch):
    from autowright import marketplace

    def refuse(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(marketplace, "save_yaml", refuse)
    market = marketplace.MarketplaceStore()
    market.load()  # the built-in seed needs a save — it must not raise
    assert any(source["builtin"] for source in market.sources)


# ---------- A4 §19 step_json serves only positive timeout / retries 1-10 ----------

@pytest.mark.parametrize("stored,expected", [
    ({"timeout": "abc"}, {}), ({"timeout": 0}, {}), ({"timeout": -5}, {}),
    ({"timeout": float("inf")}, {}), ({"timeout": 30}, {"timeout": 30}),
    ({"retries": "abc"}, {}), ({"retries": 11}, {}), ({"retries": -1}, {}),
    ({"retries": 1}, {"retries": 1}), ({"retries": 10}, {"retries": 10}),
])
def test_step_json_serves_timeout_and_retries_only_when_valid(stored, expected):
    from autowright.storage import step_json

    out = step_json({"file": "01-a.py", "name": "A", "code": "", **stored})
    assert {k: out[k] for k in ("timeout", "retries") if k in out} == expected


# ---------- E11 §4.1 `.inf` concurrency values clamp to the default ----------

def test_infinite_concurrency_values_clamp_to_the_defaults(store):
    from autowright.storage import Store, clamp_max_parallel, clamp_max_queued

    assert clamp_max_parallel(float("inf")) == 1
    assert clamp_max_queued(float("-inf")) == 0
    a = store.create_automation(make_version(), "Infinite", "agent-1")
    path = store.auto_dir(a) / "automation.yaml"
    text = path.read_text(encoding="utf-8")
    path.write_text(text + "max_parallel: .inf\nmax_queued: .inf\n", encoding="utf-8")
    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]  # the automation still loads
    assert b["max_parallel"] == 1 and b["max_queued"] == 0


# ---------- H3 §4.3 nextTriggerShort names the trigger behind nextAtMs ----------

def test_next_trigger_short_names_the_soonest_trigger(store):
    from autowright import triggers as triggerlib

    yearly = {"id": "y", "kind": "cron", "enabled": True, "expression": "0 0 1 1 *",
              "source": "user"}
    minutely = {"id": "m", "kind": "interval", "enabled": True, "every": "PT1M",
                "source": "user"}
    a = store.create_automation(make_version(), "Two schedules", None,
                                triggers=[yearly, minutely])
    j = store.auto_json(a)
    assert j["nextAtMs"] is not None
    interval = next(t for t in a["triggers"] if t["kind"] == "interval")
    assert j["nextTriggerShort"] == triggerlib.trigger_display(interval)[1]

    for t in a["triggers"]:
        t["enabled"] = t["kind"] == "cron"
    j = store.auto_json(a)
    cron = next(t for t in a["triggers"] if t["kind"] == "cron")
    assert j["nextTriggerShort"] == triggerlib.trigger_display(cron)[1]

    for t in a["triggers"]:
        t["enabled"] = False
    j = store.auto_json(a)
    assert j["nextAtMs"] is None and j["nextTriggerShort"] is None


def test_next_at_ms_is_still_computed_with_the_run_instant_without_triggers(store, monkeypatch):
    """Regression: `nextAtMs` stays the one aggregate `next_at_ms` call over
    every trigger (called even when there are none), with the run instant as
    the interval baseline — the per-trigger pass only picks the label."""
    from autowright import triggers as triggerlib

    a = store.create_automation(make_version(), "No triggers", None)
    a["_last_exec_at"] = "2026-11-01T05:30:00+00:00"
    seen = []
    real = triggerlib.next_at_ms

    def spy(triggers, *args, **kwargs):
        seen.append((list(triggers), kwargs))
        return real(triggers, *args, **kwargs)

    monkeypatch.setattr(triggerlib, "next_at_ms", spy)
    j = store.auto_json(a)
    assert seen == [([], {"run_baseline": store.run_baseline(a),
                          "interval_baseline": store.run_baseline_instant(a)})]
    assert j["nextAtMs"] is None and j["nextTriggerShort"] is None
