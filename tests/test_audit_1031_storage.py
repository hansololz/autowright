"""2026-10-31 sweep (storage): a stored version's param definitions normalized
at load (§21 2026-10-31 migrate-on-load), wrong-type top-level list and mapping
fields, a date-typed snapshot `created_at`, a crash-orphan set-aside that
fails, the one text-form rule for name and description, and a non-numeric
number `min`."""
import uuid
from pathlib import Path

import pytest
import yaml

from conftest import make_version


def _rewrite(path: Path, edit) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _draft_from(ver: dict) -> dict:
    """An editor-style draft built from the loaded version."""
    return {
        "description": "Test automation",
        "note": "Edited",
        "params": ver["params"],
        "steps": [{"file": s["file"], "name": s["name"], "description": s.get("description", ""),
                   "code": s["code"]} for s in ver["steps"]],
        "spec": ver["spec"],
    }


# ---------- P1 §21 2026-10-31: a v0.13.2-shape version still saves ----------

def test_old_shape_param_definitions_normalize_at_load_and_the_next_save_is_200(client):
    from autowright.storage import store

    a = store.create_automation(make_version(), "Old shape", "mock")
    manifest = store.auto_dir(a) / "versions" / "v1" / "automation.yaml"

    def edit(data):
        # The shape v0.13.2 could save before the §8 validator checked defaults.
        data["params"] = [
            {"name": "wait", "kind": "text", "label": "Wait", "help": "", "default": 30},
            {"name": "headers", "kind": "kv", "label": "Headers", "help": "", "default": {}},
            {"name": "verbose", "kind": "toggle", "label": True, "help": "", "default": False},
        ]
    _rewrite(manifest, edit)
    before = manifest.read_text(encoding="utf-8")

    store.load_all()
    b = store.autos[a["id"]]
    params = {p["name"]: p for p in b["versions"][1]["params"]}
    assert params["wait"]["default"] == ""
    assert params["headers"]["default"] == []
    assert "label" not in params["verbose"]
    assert params["verbose"]["default"] is False
    assert manifest.read_text(encoding="utf-8") == before  # nothing written at load

    draft = _draft_from(b["versions"][1])
    draft["steps"][0]["code"] += "# edited\n"  # an editor step edit mints a version
    r = client.post(f"/automations/{b['id']}/versions", json={"draft": draft})
    assert r.status_code == 200, r.text
    assert b["current_version"] == 2


def test_unknown_kind_is_kept_and_a_matching_default_is_untouched():
    from autowright.storage import normalize_param_definitions

    out = normalize_param_definitions([
        {"name": "odd", "kind": "color", "default": "red"},
        {"name": "n", "kind": "number", "default": 5, "help": ["x"]},
        {"name": "", "kind": "text"},
        {"kind": "text"},
    ])
    assert out[0]["kind"] == "color"
    assert out[0]["default"] == ""  # not a value of an unknown kind
    assert out[1] == {"name": "n", "kind": "number", "default": 5}
    assert len(out) == 2


# ---------- S2 §5: wrong-type list and mapping fields load empty ----------

def test_wrong_type_toplevel_fields_and_a_nameless_param_never_break_state(store):
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Wrong types", "agent-1")
    d = store.auto_dir(a)

    def top(data):
        data["enabled_agents"] = "agent-1"
        data["allowed_secrets"] = ["ok", 3, {"x": 1}]
        data["param_values"] = ["not", "a", "mapping"]
    _rewrite(d / "automation.yaml", top)

    def version(data):
        data["params"].append({"kind": "text", "default": "x"})
        data["params"].append({"name": 7, "kind": "text"})
    _rewrite(d / "versions" / "v1" / "automation.yaml", version)

    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    assert b["enabled_agents"] == []
    assert b["allowed_secrets"] == ["ok"]
    assert b["param_values"] == {}
    assert [p["name"] for p in b["versions"][1]["params"]] == ["greeting", "count"]
    s2.auto_json(b)  # the /state serialization never raises


# ---------- S3 §5: an unquoted snapshot created_at lists without raising ----------

def test_date_typed_snapshot_created_at_sorts_with_text_ones(store):
    a = store.create_automation(make_version(), "Snapshots", "agent-1")
    root = store.snapshots_dir(a)
    first, second = str(uuid.uuid4()), str(uuid.uuid4())
    for sid, stamp in ((first, "2026-10-30"), (second, "'2026-10-31T09:00:00'")):
        (root / sid).mkdir(parents=True)
        (root / sid / "snapshot.yaml").write_text(
            f"id: {sid}\nreason: manual\ncreated_at: {stamp}\n", encoding="utf-8")
    listed = store.list_snapshots(a)
    assert [m["id"] for m in listed] == [second, first]
    assert all(isinstance(m["created_at"], str) for m in listed)
    assert listed[1]["created_at"] == "2026-10-30"


# ---------- S4 §5: a failed orphan set-aside refuses the save ----------

def test_a_failing_orphan_rename_refuses_the_save_and_leaves_the_folder(store, monkeypatch):
    import shutil

    from autowright.storage import Store

    a = store.create_automation(make_version(), "Stuck orphan", "agent-1")
    store.save_new_version(a, make_version(note="v2"))
    versions = store.auto_dir(a) / "versions"
    shutil.copytree(versions / "v2", versions / "v3")  # manifest landed, flip never did

    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    real = Path.rename

    def rename(self, target):
        if self.name == "v3":
            raise PermissionError(13, "Permission denied")
        return real(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    # 2026-10-31 second sweep: the refusal is typed (OrphanSetAsideError),
    # carrying the folder, no longer the raw OSError.
    from autowright.storage import OrphanSetAsideError
    with pytest.raises(OrphanSetAsideError):
        s2.save_new_version(b, make_version(note="Next"))
    with pytest.raises(OrphanSetAsideError):
        s2.restore_version(b, 1)
    assert b["current_version"] == 2
    assert (versions / "v3" / "automation.yaml").exists()
    assert not (versions / "v4").exists()
    assert not (versions / "v3.orphan").exists()


# ---------- S5 §5: one text-form rule for name and description ----------

def test_date_name_and_boolean_description_load_as_text(store):
    from autowright.storage import Store, scalar_text

    a = store.create_automation(make_version(), "Plain", "agent-1")

    def top(data):
        data["name"] = "PLACEHOLDER"
        data["description"] = True
    _rewrite(store.auto_dir(a) / "automation.yaml", top)
    path = store.auto_dir(a) / "automation.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("PLACEHOLDER", "2026-10-30"),
                    encoding="utf-8")

    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    assert b["name"] == "2026-10-30"
    assert b["description"] == "true"
    assert scalar_text(1.5) == "1.5"
    assert scalar_text(False) == "false"
    assert scalar_text(["x"]) is None
    assert scalar_text(None) is None


def test_an_empty_or_unusable_name_falls_to_the_directory_name(store):
    from autowright.storage import Store

    a = store.create_automation(make_version(), "Plain", "agent-1")

    def top(data):
        data["name"] = ""
        data["description"] = {"a": 1}
    _rewrite(store.auto_dir(a) / "automation.yaml", top)
    s2 = Store()
    s2.load_all()
    b = s2.autos[a["id"]]
    assert b["name"] == a["id"]
    assert b["description"] == ""


# ---------- C3 §5 export: a non-numeric number min falls back to 0 ----------

def test_param_default_ignores_a_text_min():
    from autowright.storage import param_default

    assert param_default({"name": "n", "kind": "number", "min": "1e3"}) == 0
    assert param_default({"name": "n", "kind": "number", "min": True}) == 0
    assert param_default({"name": "n", "kind": "number", "min": 2}) == 2
    assert param_default({"name": "n", "kind": "number", "min": "1e3", "default": "x"}) == 0
