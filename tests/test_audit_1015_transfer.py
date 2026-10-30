"""2026-10-15 audit fixes (transfer): export refuses every step-bound shape the
importer rejects (ASCII-digit integers only), import enforces the save path's
definition rules (duplicate param names, agent/secret/package whys, bare pip
names), and an archive record nothing references is ignored before matching,
so it can never widen a grant or land as an unresolved reference."""
import io
import zipfile

import pytest
import yaml
from conftest import make_version

from autowright import transfer
from autowright.storage import new_id


@pytest.fixture(autouse=True)
def stub_check_ready(monkeypatch):
    """No test spawns a real harness status subprocess."""
    monkeypatch.setattr(transfer.harness, "check_ready",
                        lambda name, model=None, mode="default": False)


def _archive(*, params=(), packages=None, steps=None, secrets=(), agents=(),
             agent=None, triggers=()):
    """A hand-built §5.1 format-2 archive."""
    steps = list(steps or [{"file": "01-a.py", "name": "A", "description": "",
                            "code": "print('a')\n"}])
    manifest = {"format_version": 2, "exported_at": "2026-10-15T09:00:00",
                "app_version": "0.0.0", "name": "Ported", "triggers": list(triggers)}
    if agent is not None:
        manifest["agent"] = agent
    meta = {"description": "", "params": list(params),
            "steps": [{k: v for k, v in s.items() if k != "code"} for s in steps]}
    if packages is not None:
        meta["packages"] = packages
    files = [("manifest.yaml", yaml.safe_dump(manifest, sort_keys=False)),
             ("automation/automation.yaml", yaml.safe_dump(meta, sort_keys=False)),
             ("automation/spec.md", "# T\n\nBody.\n"),
             ("agents.yaml", yaml.safe_dump({"agents": list(agents)}, sort_keys=False)),
             ("secrets.yaml", yaml.safe_dump({"secrets": list(secrets)}, sort_keys=False))]
    files += [(f"automation/{s['file']}", s["code"]) for s in steps]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, text in files:
            z.writestr(path, text)
    return buf.getvalue()


def _sec(ref, name):
    return {"ref": ref, "name": name, "description": ""}


def _ag(ref, name):
    return {"ref": ref, "name": name, "description": "", "harness": "Claude Code",
            "mode": "default", "model": None}


def _put_secrets(store, *names):
    store.secrets = [{"id": new_id(), "name": n, "description": "", "set": True}
                     for n in names]
    store.save_secrets()
    return {s["name"]: s["id"] for s in store.secrets}


def _put_agents(store, *names):
    store.agents = [{"id": new_id(), "name": n, "description": "", "harness": "Claude Code",
                     "mode": "default", "model": None} for n in names]
    store.default_agent_id = store.agents[0]["id"]
    store.save_agents()
    return {g["name"]: g["id"] for g in store.agents}


# ---------- 4. §5.1 export refuses every shape the importer rejects ----------

def _step_with(store, **fields):
    a = store.create_automation(make_version(), "Bounds", None)
    a["versions"][a["current_version"]]["steps"][0].update(fields)
    return a


@pytest.mark.parametrize("retries", ["often", [3], 2.5, True, "³"])
def test_export_refuses_a_non_integer_retries(store, retries):
    a = _step_with(store, retries=retries)
    with pytest.raises(transfer.TransferError) as e:
        transfer.export_automation(store, a)
    assert "01-say.py" in str(e.value) and "non-integer retries" in str(e.value)
    assert "repair the automation first" in str(e.value)


@pytest.mark.parametrize("timeout", ["²", "٥", "-5"])
def test_export_refuses_a_non_ascii_digit_timeout(store, timeout):
    a = _step_with(store, timeout=timeout)
    with pytest.raises(transfer.TransferError) as e:
        transfer.export_automation(store, a)
    assert "non-integer timeout" in str(e.value)


@pytest.mark.parametrize("fields, words", [
    ({"timeout": -5}, "timeout below 1"),
    ({"retries": 11}, "retries outside 1-10"),
    ({"retries": "12"}, "retries outside 1-10"),
    ({"retries": -1}, "retries outside 1-10"),
    ({"timeout": 30, "no_timeout": True}, "combines timeout and no_timeout"),
    ({"retries": 3, "infinite_retries": True}, "combines retries and infinite_retries"),
])
def test_export_refuses_step_bounds_the_importer_rejects(store, fields, words):
    a = _step_with(store, **fields)
    with pytest.raises(transfer.TransferError) as e:
        transfer.export_automation(store, a)
    assert "01-say.py" in str(e.value) and words in str(e.value)
    assert "repair the automation first" in str(e.value)


def test_export_carries_in_bounds_step_integers(store):
    a = _step_with(store, timeout="120", retries="3")
    data = transfer.export_automation(store, a)
    meta = yaml.safe_load(zipfile.ZipFile(io.BytesIO(data)).read("automation/automation.yaml"))
    assert meta["steps"][0]["timeout"] == 120 and meta["steps"][0]["retries"] == 3
    # and what export wrote, import takes
    b, _ = transfer.import_automation(store, data)
    step = b["versions"][b["current_version"]]["steps"][0]
    assert step["timeout"] == 120 and step["retries"] == 3


def test_export_route_answers_422_for_retries_beside_infinite_retries(client):
    from autowright.storage import store

    a = _step_with(store, retries=2, infinite_retries=True)
    r = client.get(f"/automations/{a['id']}/export")
    assert r.status_code == 422
    assert "combines retries and infinite_retries" in r.json()["detail"]


# ---------- 5. §5.1 import rejects what POST /versions would ----------

_PARAM = {"name": "count", "kind": "number", "label": "Count", "default": 1}


@pytest.mark.parametrize("over, message", [
    ({"params": [_PARAM, dict(_PARAM, default=2)]}, "param `count` is declared twice"),
    ({"steps": [{"file": "01-a.py", "name": "A", "description": "", "agent": True,
                 "code": "print('a')\n"}]},
     "step A: agent: true requires a why"),
    ({"steps": [{"file": "01-a.py", "name": "A", "description": "", "agent": True,
                 "why": "   ", "code": "print('a')\n"}]},
     "step A: agent: true requires a why"),
    ({"agents": [_ag("1", "One"), _ag("2", "Two")],
      "steps": [{"file": "01-a.py", "name": "A", "description": "", "agent": True,
                 "why": "w", "agents": [{"ref": "1", "why": "reads"}, {"ref": "2"}],
                 "code": "print('a')\n"}]},
     "step A: agent '2' needs a why"),
    ({"secrets": [_sec("1", "API_KEY")],
      "steps": [{"file": "01-a.py", "name": "A", "description": "",
                 "secrets": [{"ref": "1"}], "code": "print('a')\n"}]},
     "step A: secret '1' needs a why"),
    ({"secrets": [_sec("1", "API_KEY")],
      "steps": [{"file": "01-a.py", "name": "A", "description": "",
                 "secrets": [{"ref": "1", "why": " "}], "code": "print('a')\n"}]},
     "step A: secret '1' needs a why"),
    ({"packages": [{"pip": "pandas", "import": "pandas"}]},
     "packages: pandas needs a why"),
    ({"packages": [{"pip": "pandas", "import": "pandas", "why": "  "}]},
     "packages: pandas needs a why"),
    ({"packages": [{"pip": "pandas==2.0", "import": "pandas", "why": "tables"}]},
     "packages: 'pandas==2.0' must be a bare distribution name"),
])
def test_import_rejects_save_path_definition_rules(store, over, message):
    before = len(store.autos)
    with pytest.raises(transfer.TransferError) as e:
        transfer.import_automation(store, _archive(**over))
    assert message in str(e.value)
    assert len(store.autos) == before


def test_import_rejection_answers_422_through_the_route(client):
    data = _archive(params=[_PARAM, dict(_PARAM)])
    r = client.post("/automations/import", content=data,
                    headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 422
    assert "param `count` is declared twice" in r.json()["detail"]


def test_a_clean_archive_still_imports(store):
    _put_secrets(store, "API_KEY")
    _put_agents(store, "One")
    a, _ = transfer.import_automation(store, _archive(
        params=[_PARAM],
        packages=[{"pip": "pandas", "import": "pandas", "why": "tables"}],
        secrets=[_sec("1", "API_KEY")], agents=[_ag("1", "One")], agent="1",
        steps=[{"file": "01-a.py", "name": "A", "description": "",
                "secrets": [{"ref": "1", "why": "calls the API"}],
                "code": "print('a')\n"},
               {"file": "02-b.py", "name": "B", "description": "", "agent": True,
                "why": "judgment", "agents": [{"ref": "1"}], "code": "print('b')\n"}]))
    ver = a["versions"][a["current_version"]]
    assert [s["name"] for s in ver["steps"]] == ["A", "B"]
    assert ver["packages"] == [{"pip": "pandas", "import": "pandas", "why": "tables"}]


# ---------- 6. §5.1 only referenced records are matched and granted ----------

def test_an_unreferenced_secret_record_is_never_granted(store):
    ids = _put_secrets(store, "API_KEY", "MAIL_PASS", "BANK_TOKEN", "BOT_TOKEN")
    a, summary = transfer.import_automation(store, _archive(
        secrets=[_sec("1", "API_KEY"), _sec("2", "MAIL_PASS"), _sec("3", "BANK_TOKEN"),
                 _sec("4", "BOT_TOKEN")],
        triggers=[{"kind": "discord", "channel": "42", "secret": "4"}],
        steps=[{"file": "01-a.py", "name": "A", "description": "",
                "secrets": [{"ref": "2", "why": "sends the mail"}],
                "code": 'x = secrets["1"]\n'}]))
    # BANK_TOKEN matches a local secret by exact name, but nothing references it
    assert ids["BANK_TOKEN"] not in a["allowed_secrets"]
    assert sorted(a["allowed_secrets"]) == sorted(
        [ids["API_KEY"], ids["MAIL_PASS"], ids["BOT_TOKEN"]])
    assert "BANK_TOKEN" not in [s["name"] for s in summary["secretsMatched"]]


def test_an_unreferenced_agent_record_is_never_granted(store):
    ids = _put_agents(store, "Author", "Helper", "Bystander", "Coder")
    a, summary = transfer.import_automation(store, _archive(
        agents=[_ag("1", "Author"), _ag("2", "Helper"), _ag("3", "Bystander"),
                _ag("4", "Coder")],
        agent="1",
        steps=[{"file": "01-a.py", "name": "A", "description": "", "agent": True,
                "why": "w",
                # §8 rule 7 (2026-10-30): a subscripted agent is one the step declares
                "agents": [{"ref": "2", "why": "helps"}, {"ref": "4", "why": "codes"}],
                "code": 'y = agents["4"]\n'}]))
    assert ids["Bystander"] not in a["enabled_agents"]
    assert a["enabled_agents"] == [ids["Author"], ids["Helper"], ids["Coder"]]
    assert "Bystander" not in [g["name"] for g in summary["agentsMatched"]]


def test_an_unreferenced_unresolved_record_is_no_problem(store):
    _put_secrets(store, "API_KEY")
    a, summary = transfer.import_automation(store, _archive(
        secrets=[_sec("1", "API_KEY"), _sec("2", "ZEBRA_GHOST")],
        steps=[{"file": "01-a.py", "name": "A", "description": "",
                "code": 'x = secrets["1"]\n'}]))
    assert summary["unresolved"] == []
    assert not a.get("unresolved_references")


def test_an_unreferenced_record_cannot_claim_a_referenced_match(store):
    """Ignored before matching: an unreferenced record listed first can't
    claim the local record a referenced one would have matched."""
    ids = _put_secrets(store, "STRIPE_API_KEY")
    a, summary = transfer.import_automation(store, _archive(
        secrets=[_sec("1", "STRIPE_KEY"), _sec("2", "STRIPE_SECRET")],
        steps=[{"file": "01-a.py", "name": "A", "description": "",
                "code": 'x = secrets["2"]\n'}]))
    assert a["allowed_secrets"] == [ids["STRIPE_API_KEY"]]
    assert [s["name"] for s in summary["secretsMatched"]] == ["STRIPE_SECRET"]
