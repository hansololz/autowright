"""2026-10-15 audit fixes (storage): a new version's number is free on disk
too and a failed save drops the reference-id memo, a hand-edited non-text
automation name loads as text or the directory name, and a snapshot whose
id differs from its directory is skipped by the listing (and so by every
mutation that works from it)."""
import uuid

import pytest
import yaml
from conftest import make_version

from autowright.storage import Store


# ---------- 1. §5 version numbers free in memory AND on disk ----------

def _stray_version_dir(store, a, n):
    d = store.auto_dir(a) / "versions" / f"v{n}"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text("{{{ not yaml", encoding="utf-8")
    return d


def test_a_save_skips_a_version_folder_the_load_skipped(store):
    a = store.create_automation(make_version(), "Stray", None)
    store.save_new_version(a, make_version(note="Second"))
    stray = _stray_version_dir(store, a, 3)

    assert store.save_new_version(a, make_version(note="Third")) == 4
    assert stray.exists()
    assert (stray / "manifest.yaml").read_text(encoding="utf-8") == "{{{ not yaml"


@pytest.mark.parametrize("how", ["save", "restore"])
def test_a_failed_save_never_removes_a_stray_version_folder(store, monkeypatch, how):
    a = store.create_automation(make_version(), "Stray Rollback", None)
    stray = _stray_version_dir(store, a, 2)

    def boom(_a, _mutate):
        raise OSError(28, "No space left on device")

    with monkeypatch.context() as patch, pytest.raises(OSError):
        patch.setattr(store, "_commit_toplevel", boom)
        if how == "save":
            store.save_new_version(a, make_version(note="Second"))
        else:
            store.restore_version(a, 1)
    assert stray.exists()
    assert not (store.auto_dir(a) / "versions" / "v3").exists()
    assert sorted(a["versions"]) == [1]
    assert store.save_new_version(a, make_version(note="Retry")) == 3
    assert stray.exists()


def _referencing_version(secret_id):
    ver = make_version(note="References")
    ver["steps"][0]["code"] = f'from autowright import secrets\nsecrets["{secret_id}"]\n'
    return ver


def test_a_failed_save_drops_the_reference_memo(store, monkeypatch):
    secret_id = str(uuid.uuid4())
    a = store.create_automation(make_version(), "Memo", None)
    a["unresolved_references"] = {secret_id: "Old secret"}

    def failing_write(_a):
        raise OSError(28, "No space left on device")

    # The pointer flip fails AFTER mutate memoized the attempt's references.
    with monkeypatch.context() as patch, pytest.raises(OSError):
        patch.setattr(store, "_write_toplevel", failing_write)
        store.save_new_version(a, _referencing_version(secret_id))
    assert a["current_version"] == 1
    assert "_ref_cache" not in a

    n = store.save_new_version(a, make_version(note="Clean"))
    assert n == 2
    secret_ids, _agent_ids = store.current_reference_ids(a)
    assert secret_id not in secret_ids
    assert "unresolved_references" not in a


# ---------- 2. §5 a non-text automation name ----------

def _hand_edit_name(store, a, value):
    path = store.auto_dir(a) / "automation.yaml"
    top = yaml.safe_load(path.read_text(encoding="utf-8"))
    top["name"] = value
    path.write_text(yaml.safe_dump(top), encoding="utf-8")


@pytest.mark.parametrize("value, expected", [
    (2024, "2024"),
    (1.5, "1.5"),
    (["a"], None),
    ({"k": "v"}, None),
    (True, None),
    ("", None),
    (None, None),
])
def test_a_hand_edited_name_loads_as_text(store, value, expected):
    a = store.create_automation(make_version(), "Original", None)
    _hand_edit_name(store, a, value)

    fresh = Store()
    fresh.load_all()
    loaded = fresh.autos[a["id"]]
    assert loaded["name"] == (expected if expected is not None else a["id"])
    assert isinstance(loaded["name"], str)
    # A name-collision check never raises on it.
    assert fresh.free_automation_name("Something new") == "Something new"
    assert fresh.free_automation_name(loaded["name"]) == f"{loaded['name']} 2"


def test_a_rename_check_tolerates_a_numeric_name(store, monkeypatch):
    from fastapi import HTTPException

    from autowright import api

    a = store.create_automation(make_version(), "Original", None)
    _hand_edit_name(store, a, 2024)
    fresh = Store()
    fresh.load_all()
    monkeypatch.setattr(api, "store", fresh)
    api._check_automation_name_free("Different")
    with pytest.raises(HTTPException):
        api._check_automation_name_free("2024")


# ---------- 3. §5/§6.3 a snapshot's id must equal its directory ----------

def _write_memory(store, a, name="seen.yaml", text="items: [1]\n"):
    d = store.auto_dir(a) / "memory"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text)


def test_a_snapshot_whose_id_differs_from_its_dir_is_skipped(store):
    a = store.create_automation(make_version(), "Snaps", None)
    _write_memory(store, a)
    root = store.snapshots_dir(a)
    damaged = root / str(uuid.uuid4())
    damaged.mkdir(parents=True)
    (damaged / "snapshot.yaml").write_text(yaml.safe_dump({
        "id": "..", "name": None, "reason": "manual",
        "created_at": "2020-01-01T00:00:00", "version": "v1", "size": 1, "files": 1,
    }), encoding="utf-8")

    assert store.list_snapshots(a) == []
    for _ in range(7):  # past the five-unnamed prune
        store.snapshot_memory(a, "manual")

    assert store.auto_dir(a).is_dir()
    assert (store.auto_dir(a) / "automation.yaml").exists()
    assert (store.auto_dir(a) / "memory" / "seen.yaml").exists()
    listed = store.list_snapshots(a)
    assert len(listed) == 5
    assert all(m["id"] != ".." for m in listed)
    assert damaged.exists()  # skipped, never touched
