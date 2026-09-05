"""Transfer archives (§5.1): export/import round-trips, the match ladders,
unresolved references, grant rules, rejection."""
import io
import zipfile

import pytest
import yaml
from conftest import make_version

from autowright import __version__, paths, transfer
from autowright.storage import AGENT_REF_RE, SECRET_REF_RE, Store, new_id


def _agent(name, harness="Claude Code", mode="default", model=None, description=""):
    return {"id": new_id(), "name": name, "description": description,
            "harness": harness, "mode": mode, "model": model}


def _build(store: Store):
    """An automation exercising every archive surface: params + values, cron +
    app_start + time triggers, an agent step, declared + code-referenced
    secrets - all references by id (§4.1/§4.3/§4.8); export translates them to
    the archive's numeric ref form."""
    store.agents = [_agent("Researcher"),
                    _agent("Coder", harness="OpenCode", mode="custom", model="anthropic/x")]
    store.default_agent_id = store.agents[0]["id"]  # §4.7 single pointer
    store.save_agents()
    api_key, bot_token, mail_pass = new_id(), new_id(), new_id()
    store.secrets = [{"id": api_key, "name": "API_KEY", "description": "service key", "set": True},
                     {"id": bot_token, "name": "BOT_TOKEN", "description": "discord bot", "set": True},
                     {"id": mail_pass, "name": "MAIL_PASS", "description": "mail", "set": True}]
    store.save_secrets()
    coder_id = store.agents[1]["id"]
    ver = {
        "description": "Watches things",
        "params": [{"name": "count", "kind": "number", "label": "Count", "help": "", "default": 3}],
        "packages": [{"pip": "pandas", "import": "pandas", "why": "builds the table"}],
        "steps": [
            {"name": "Fetch", "description": "",
             "code": f'from autowright import secrets\nx = secrets["{api_key}"]  # API_KEY\n',
             "secrets": [{"id": mail_pass, "why": "sends the mail"}]},
            {"name": "Summarize", "description": "", "code": "print('hi')\n",
             "agent": True, "why": "judgment", "agents": [{"id": coder_id}]},
        ],
        "spec": [{"kind": "h1", "text": "Watch"}, {"kind": "p", "text": "Body."}],
        "instructions": "Keep it short.",
    }
    a = store.create_automation(
        ver, name="Watcher", agent_id=store.agents[0]["id"],
        triggers=[{"id": new_id(), "kind": "cron", "enabled": True, "expression": "0 8 * * *", "timezone": "America/New_York"},
                  {"id": new_id(), "kind": "app_start", "enabled": True},
                  {"id": new_id(), "kind": "time", "enabled": True, "at": "2999-01-01T09:00"},
                  {"id": new_id(), "kind": "discord", "enabled": True, "channel": "42",
                   "secret": bot_token, "pattern": "go", "author": ["111", "777"]},
                  {"id": new_id(), "kind": "imessage", "enabled": True,
                   "from": "+15551234567", "pattern": "run"}],
        enabled_agents=[g["id"] for g in store.agents],
        allowed_secrets=[api_key, mail_pass])
    store.patch_automation(a, {"paramValues": {"count": 7}})
    return a


@pytest.fixture(autouse=True)
def stub_check_ready(monkeypatch):
    """§5.1 summary readiness flag - stubbed so no test spawns a real harness
    status subprocess; the readiness test overrides this per harness."""
    monkeypatch.setattr(transfer.harness, "check_ready",
                        lambda name, model=None, mode="default": False)


def _fresh_home(monkeypatch, tmp_path_factory):
    home2 = tmp_path_factory.mktemp("home2")
    monkeypatch.setenv("AUTOWRIGHT_HOME", str(home2))
    from autowright import paths

    paths.ensure_dirs()
    s2 = Store()
    s2.load_all()
    return s2


# ---------- hand-built format-2 archives ----------
_HERE = object()


def _sec(ref, name, description=""):
    return {"ref": ref, "name": name, "description": description}


def _ag(ref, name, harness="Claude Code", mode="default", model=None, description=""):
    return {"ref": ref, "name": name, "description": description,
            "harness": harness, "mode": mode, "model": model}


def _archive(**over):
    """A hand-built §5.1 format-2 archive: the transport shape written by hand
    so the match-ladder tests pin exactly what travels, with no exporting store
    in the way. Steps carry `code`; everything else rides automation.yaml."""
    steps = list(over.pop("steps", None) or [
        {"file": "01-a.py", "name": "A", "description": "", "code": "print('a')\n"}])
    manifest = {"format_version": over.pop("format_version", 2),
                "exported_at": "2026-08-24T09:00:00",
                "app_version": __version__,
                "name": over.pop("name", "Ported")}
    os_token = over.pop("os", _HERE)
    if os_token is _HERE:
        os_token = paths.current_os()
    if os_token is not None:
        manifest["os"] = os_token
    agent_ref = over.pop("agent", None)
    if agent_ref is not None:
        manifest["agent"] = agent_ref
    manifest["triggers"] = list(over.pop("triggers", []))
    values = over.pop("param_values", None)
    if values is not None:
        manifest["param_values"] = values
    meta = {"description": over.pop("description", ""),
            "params": list(over.pop("params", [])),
            "steps": [{k: v for k, v in s.items() if k != "code"} for s in steps]}
    if pkgs := over.pop("packages", None):
        meta["packages"] = pkgs
    agents = list(over.pop("agents", []))
    secrets = list(over.pop("secrets", []))
    spec = over.pop("spec", "# T\n\nBody.\n")
    assert not over, f"unknown archive fields: {sorted(over)}"
    files = [("manifest.yaml", yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)),
             ("automation/automation.yaml", yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)),
             ("automation/spec.md", spec),
             ("agents.yaml", yaml.safe_dump({"agents": agents}, sort_keys=False, allow_unicode=True)),
             ("secrets.yaml", yaml.safe_dump({"secrets": secrets}, sort_keys=False, allow_unicode=True))]
    files += [(f"automation/{s['file']}", s["code"]) for s in steps]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, text in files:
            z.writestr(path, text)
    return buf.getvalue()


def _put_secrets(store, *names_descs):
    store.secrets = [{"id": new_id(), "name": n, "description": d, "set": True}
                     for n, d in names_descs]
    store.save_secrets()
    return {s["name"]: s["id"] for s in store.secrets}


def _put_agents(store, agents, default=0):
    store.agents = list(agents)
    store.default_agent_id = store.agents[default]["id"] if store.agents else None
    store.save_agents()
    return {g["name"]: g["id"] for g in store.agents}


def _rezip(data, edit):
    """Rebuild the archive, letting `edit(name, bytes)` replace member content."""
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for nm in src.namelist():
            out.writestr(nm, edit(nm, src.read(nm)) or src.read(nm))
    return buf.getvalue()


def _rezip_manifest(data, edit):
    """Rewrite manifest.yaml through `edit` (a dict → dict function)."""
    return _rezip(data, lambda nm, b: (yaml.safe_dump(edit(yaml.safe_load(b))).encode()
                                       if nm == "manifest.yaml" else None))


def _rezip_meta(data, edit):
    """Rewrite automation/automation.yaml through `edit` (dict → dict)."""
    return _rezip(data, lambda nm, b: (yaml.safe_dump(edit(yaml.safe_load(b))).encode()
                                       if nm == "automation/automation.yaml" else None))


# ---------- export ----------

def test_export_layout_and_numeric_refs(store):
    """§5.1 format 2: uuids never travel - refs are the archive's reference
    format, assigned per kind in listing order (secrets by record name, agents
    drafting-first), and every id reference rides them: step entries, code
    subscripts, the discord trigger's secret, the manifest's authoring agent."""
    a = _build(store)
    data = transfer.export_automation(store, a)
    z = zipfile.ZipFile(io.BytesIO(data))
    names = set(z.namelist())
    assert {"manifest.yaml", "automation/automation.yaml", "automation/spec.md",
            "automation/instructions.md", "agents.yaml", "secrets.yaml"} <= names
    manifest = yaml.safe_load(z.read("manifest.yaml"))
    assert manifest["format_version"] == 2
    # §5.1: every export records the app version (not read on import today -
    # reserved for future version gating)
    assert manifest["app_version"] == __version__
    assert manifest["name"] == "Watcher"
    # the authoring agent travels as its agents.yaml ref, never a name or id
    assert manifest["agent"] == "1"
    # cron + app_start + discord + imessage, no ids/off; the one-shot time
    # trigger never travels, and the discord token secret rides its ref
    assert manifest["triggers"] == [{"kind": "cron", "expression": "0 8 * * *", "timezone": "America/New_York"},
                                    {"kind": "app_start"},
                                    {"kind": "discord", "channel": "42",
                                     "secret": "2", "pattern": "go",
                                     "author": ["111", "777"]},
                                    {"kind": "imessage", "from": "+15551234567",
                                     "pattern": "run"}]
    assert manifest["param_values"] == {"count": 7}
    meta = yaml.safe_load(z.read("automation/automation.yaml"))
    assert "when" not in meta and "note" not in meta
    assert meta["packages"] == [{"pip": "pandas", "import": "pandas", "why": "builds the table"}]
    # step grant entries travel as { ref, why? } - no ids, no names
    fetch, summarize = meta["steps"]
    assert fetch["secrets"] == [{"ref": "3", "why": "sends the mail"}]
    assert summarize["agents"] == [{"ref": "2"}]
    # code subscripts travel in ref form; the §6.1 trailing comment rides along
    assert z.read(f"automation/{fetch['file']}").decode() == (
        'from autowright import secrets\nx = secrets["1"]  # API_KEY\n')
    # both referenced agents travel keyed by ref, without ids or credentials
    assert yaml.safe_load(z.read("agents.yaml"))["agents"] == [
        {"ref": "1", "name": "Researcher", "description": "",
         "harness": "Claude Code", "mode": "default", "model": None},
        {"ref": "2", "name": "Coder", "description": "",
         "harness": "OpenCode", "mode": "custom", "model": "anthropic/x"}]
    # declared + code-referenced + trigger-token secrets, refs/names/descs only
    assert yaml.safe_load(z.read("secrets.yaml"))["secrets"] == [
        {"ref": "1", "name": "API_KEY", "description": "service key"},
        {"ref": "2", "name": "BOT_TOKEN", "description": "discord bot"},
        {"ref": "3", "name": "MAIL_PASS", "description": "mail"}]
    raw = data.decode("latin-1")
    assert "mail-app" not in raw  # no values anywhere
    # no local uuid leaks into any member (§5.1: uuids are install-local)
    for sid in (s["id"] for s in store.secrets):
        assert sid not in raw
    for gid in (g["id"] for g in store.agents):
        assert gid not in raw


def test_export_without_values(store):
    a = _build(store)
    z = zipfile.ZipFile(io.BytesIO(transfer.export_automation(store, a, include_values=False)))
    assert "param_values" not in yaml.safe_load(z.read("manifest.yaml"))


def test_export_strips_values_embedded_in_definitions(store):
    """§4.2/§5.1: a version written before the save-side strip can carry
    resolved value keys inside its stored param definitions — they must never
    leave the machine outside the include-values gate."""
    a = _build(store)
    ver = a["versions"][a["current_version"]]
    assert ver["params"], "fixture must carry a param definition"
    for p in ver["params"]:
        p["value"] = "super-secret-value"
    data = transfer.export_automation(store, a, include_values=False)
    meta = yaml.safe_load(zipfile.ZipFile(io.BytesIO(data)).read("automation/automation.yaml"))
    for p in meta["params"]:
        assert not ({"value", "on", "lines", "rows"} & set(p))
    assert b"super-secret-value" not in data


def test_import_rejects_non_string_descriptions(store):
    """§5.1: the whole archive validates up front — a non-string description
    would otherwise TypeError out of the similarity tokenizer (a 500) exactly
    on the normal no-exact-match path."""
    data = transfer.export_automation(store, _build(store))

    def bad_secrets(nm, b):
        if nm != "secrets.yaml":
            return None
        y = yaml.safe_load(b)
        y["secrets"][0]["description"] = 12345
        return yaml.safe_dump(y).encode()

    with pytest.raises(transfer.TransferError):
        transfer.import_automation(store, _rezip(data, bad_secrets))

    def bad_meta(meta):
        meta["description"] = 12345
        return meta

    with pytest.raises(transfer.TransferError):
        transfer.import_automation(store, _rezip_meta(data, bad_meta))


def test_export_writes_run_if_missed_only_for_an_opted_out_cron(store):
    """§5.1/§4.3: the §6 wake catch-up opt-out travels as the cron entry's
    `run_if_missed: false` - written only when the stored cron opted out, so a
    default cron's manifest entry keeps the pre-field shape."""
    ver = {"description": "", "params": [], "packages": [],
           "steps": [{"name": "Only", "description": "", "code": "print('x')\n"}],
           "spec": [{"kind": "h1", "text": "T"}], "instructions": ""}
    a = store.create_automation(
        ver, name="Sleepy", agent_id=None,
        triggers=[{"id": new_id(), "kind": "cron", "enabled": True,
                   "expression": "0 8 * * *", "timezone": "America/New_York",
                   "runIfMissed": False},
                  {"id": new_id(), "kind": "cron", "enabled": True,
                   "expression": "0 9 * * *"}])
    z = zipfile.ZipFile(io.BytesIO(transfer.export_automation(store, a)))
    assert yaml.safe_load(z.read("manifest.yaml"))["triggers"] == [
        {"kind": "cron", "expression": "0 8 * * *", "timezone": "America/New_York",
         "run_if_missed": False},
        {"kind": "cron", "expression": "0 9 * * *"}]


def test_export_rejects_dangling_reference_but_allows_odd_agent_names(store):
    """§5.1: an id no stored record holds must be repaired before the
    automation can travel (there is no record to carry it). A name with quotes
    or backslashes is no longer a problem - refs are the reference format, so
    the name is data, not syntax."""
    odd = _agent('He said "hi" \\ ok')
    _put_agents(store, [odd])
    gone = new_id()
    ver = {"description": "", "params": [], "packages": [],
           "steps": [{"name": "Only", "description": "", "code": "print('x')\n",
                      "agent": True, "why": "w", "agents": [{"id": odd["id"]}]}],
           "spec": [{"kind": "h1", "text": "T"}], "instructions": ""}
    a = store.create_automation(ver, name="Odd", agent_id=odd["id"], triggers=[])
    z = zipfile.ZipFile(io.BytesIO(transfer.export_automation(store, a)))
    assert yaml.safe_load(z.read("agents.yaml"))["agents"][0]["name"] == 'He said "hi" \\ ok'

    ver2 = dict(ver, steps=[{"name": "Only", "description": "",
                             "code": f'from autowright import secrets\nx = secrets["{gone}"]\n'}])
    b = store.create_automation(ver2, name="Dangler", agent_id=None, triggers=[])
    with pytest.raises(transfer.TransferError, match="references a secret that no longer exists"):
        transfer.export_automation(store, b)


# ---------- archive validation ----------

def test_import_rejects_format_1_with_reexport_guidance(store):
    """§5.1/§21.3: the numeric-reference break carried no migration - a
    format-1 archive is rejected with re-export guidance, nothing written."""
    before = len(store.autos)
    with pytest.raises(transfer.TransferError,
                       match=r"unsupported archive format 1 - this app reads format 2; "
                             r"re-export the automation with the current version"):
        transfer.import_automation(store, _archive(format_version=1))
    with pytest.raises(transfer.TransferError, match="unsupported archive format 99"):
        transfer.import_automation(store, _archive(format_version=99))
    assert len(store.autos) == before


def test_import_rejects_uuid_and_name_form_subscripts(store):
    """§5.1: one scan over every subscript key catches the uuid-form and
    name-form leftovers a pre-format-2 archive carries."""
    stale = "11111111-1111-4111-8111-111111111111"
    with pytest.raises(transfer.TransferError,
                       match="not one of the archive's numbered references"):
        transfer.import_automation(store, _archive(
            secrets=[_sec("1", "API_KEY")],
            steps=[{"file": "01-a.py", "name": "A", "description": "",
                    "code": f'x = secrets["{stale}"]\n'}]))
    with pytest.raises(transfer.TransferError,
                       match=r"subscripts secrets\['API_KEY'\], which is not one of the "
                             "archive's numbered references"):
        transfer.import_automation(store, _archive(
            secrets=[_sec("1", "API_KEY")],
            steps=[{"file": "01-a.py", "name": "A", "description": "",
                    "code": 'x = secrets["API_KEY"]\n'}]))
    with pytest.raises(transfer.TransferError,
                       match=r"subscripts agents\['Coder'\], which is not one of the "
                             "archive's numbered references"):
        transfer.import_automation(store, _archive(
            agents=[_ag("1", "Coder")],
            steps=[{"file": "01-a.py", "name": "A", "description": "", "agent": True,
                    "why": "w", "agents": [{"ref": "1"}],
                    "code": 'a = agents["Coder"].ask("hi")\n'}]))
    assert store.autos == {}


def test_import_rejects_id_or_name_keyed_step_entries(store):
    """§5.1: step grant entries travel as { ref, why? } - the §4.1 id form and
    the retired name form are both rejected with re-export guidance."""
    for entry in ({"id": new_id(), "why": "w"}, {"name": "API_KEY", "why": "w"}):
        with pytest.raises(transfer.TransferError,
                           match="lists secrets by id or name - archives carry numbered "
                                 "references; re-export the automation"):
            transfer.import_automation(store, _archive(
                secrets=[_sec("1", "API_KEY")],
                steps=[{"file": "01-a.py", "name": "A", "description": "",
                        "secrets": [entry], "code": "print('a')\n"}]))
    for entry in ({"id": new_id()}, {"name": "Coder"}):
        with pytest.raises(transfer.TransferError,
                           match="lists agents by id or name - archives carry numbered "
                                 "references; re-export the automation"):
            transfer.import_automation(store, _archive(
                agents=[_ag("1", "Coder")],
                steps=[{"file": "01-a.py", "name": "A", "description": "", "agent": True,
                        "why": "w", "agents": [entry], "code": "print('a')\n"}]))
    assert store.autos == {}


def test_import_rejects_refs_missing_from_the_archive(store):
    """§5.1 ref closure: every ref must resolve against the archive's own
    agents.yaml / secrets.yaml - step entries, code subscripts, the discord
    trigger's token secret, and the manifest's authoring agent alike."""
    cases = [
        ("step '01-a.py' references secret 9", dict(
            secrets=[_sec("1", "API_KEY")],
            steps=[{"file": "01-a.py", "name": "A", "description": "",
                    "secrets": [{"ref": "9", "why": "w"}], "code": "print('a')\n"}])),
        ("step '01-a.py' references agent 9", dict(
            agents=[_ag("1", "Coder")],
            steps=[{"file": "01-a.py", "name": "A", "description": "", "agent": True,
                    "why": "w", "agents": [{"ref": "9"}], "code": "print('a')\n"}])),
        ("not one of the archive's numbered references", dict(
            secrets=[_sec("1", "API_KEY")],
            steps=[{"file": "01-a.py", "name": "A", "description": "",
                    "code": 'x = secrets["7"]\n'}])),
        ("a Discord trigger references secret 4", dict(
            secrets=[_sec("1", "API_KEY")],
            triggers=[{"kind": "discord", "channel": "42", "secret": "4"}])),
        ("the manifest's agent 3 isn't listed", dict(
            agents=[_ag("1", "Coder")], agent="3")),
    ]
    for match, kwargs in cases:
        with pytest.raises(transfer.TransferError, match=match):
            transfer.import_automation(store, _archive(**kwargs))
    assert store.autos == {}


def test_import_rejects_duplicate_refs_per_kind(store):
    with pytest.raises(transfer.TransferError, match="duplicate secret refs in the archive"):
        transfer.import_automation(store, _archive(
            secrets=[_sec("1", "API_KEY"), _sec("1", "OTHER_KEY")]))
    with pytest.raises(transfer.TransferError, match="duplicate agent refs in the archive"):
        transfer.import_automation(store, _archive(
            agents=[_ag("1", "Coder"), _ag("1", "Researcher")]))
    with pytest.raises(transfer.TransferError, match="every entry needs a numbered ref"):
        transfer.import_automation(store, _archive(secrets=[{"name": "API_KEY"}]))
    with pytest.raises(transfer.TransferError, match="every entry needs a numbered ref"):
        transfer.import_automation(store, _archive(agents=[{"name": "Coder",
                                                            "harness": "Claude Code"}]))
    assert store.autos == {}


def test_import_accepts_duplicate_archive_agent_names(store):
    """§5.1: two archive agents may share a name - refs disambiguate them, so
    each step reference still resolves to exactly one local record."""
    ids = _put_agents(store, [_agent("Helper"), _agent("Other", harness="Codex")])
    a, summary = transfer.import_automation(store, _archive(
        agents=[_ag("1", "Helper"), _ag("2", "Helper", harness="Codex")],
        steps=[{"file": "01-a.py", "name": "A", "description": "", "agent": True,
                "why": "w", "agents": [{"ref": "1"}], "code": "print('a')\n"},
               {"file": "02-b.py", "name": "B", "description": "", "agent": True,
                "why": "w", "agents": [{"ref": "2"}], "code": "print('b')\n"}]))
    one, two = a["versions"][1]["steps"]
    assert one["agents"] == [{"id": ids["Helper"]}]
    assert two["agents"] == [{"id": ids["Other"]}]
    assert [(g["name"], g["matchedTo"], g["matchedBy"]) for g in summary["agentsMatched"]] == [
        ("Helper", "Helper", "name"), ("Helper", "Other", "configuration")]
    assert summary["unresolved"] == []


def test_import_local_model_agent_harness_rule(store):
    # §4.7: a local-model agent entry imports with Claude Code, Codex, or
    # OpenCode - never Gemini CLI.
    for h in ("Claude Code", "Codex", "OpenCode"):
        transfer.import_automation(store, _archive(
            agents=[_ag("1", f"Local {h}", harness=h, mode="ollama", model="qwen3:8b")],
            steps=[{"file": "01-a.py", "name": "A", "description": "", "agent": True,
                    "why": "w", "agents": [{"ref": "1"}], "code": "print('a')\n"}]))
    with pytest.raises(transfer.TransferError, match="local-model agent"):
        transfer.import_automation(store, _archive(
            agents=[_ag("1", "Local G", harness="Gemini CLI", mode="ollama",
                        model="qwen3:8b")]))
    with pytest.raises(transfer.TransferError, match="needs a model for mode"):
        transfer.import_automation(store, _archive(
            agents=[_ag("1", "Custom", mode="custom")]))


# ---------- the §5.1 match ladders ----------

def test_secrets_match_by_exact_name_then_similarity(store, monkeypatch, tmp_path_factory):
    """§5.1 secrets ladder: exact name first, then the pinned similarity rung -
    STRIPE_KEY matches STRIPE_API_KEY (both tokenize to {stripe})."""
    ids = _put_secrets(store, ("API_KEY", "service key"), ("STRIPE_API_KEY", "payments"))
    a, summary = transfer.import_automation(store, _archive(
        secrets=[_sec("1", "API_KEY", "service key"), _sec("2", "STRIPE_KEY", "payments")],
        steps=[{"file": "01-a.py", "name": "A", "description": "",
                "code": 'x = secrets["1"]\ny = secrets["2"]\n'}]))
    assert summary["secretsMatched"] == [
        {"name": "API_KEY", "matchedTo": "API_KEY", "matchedBy": "name"},
        {"name": "STRIPE_KEY", "matchedTo": "STRIPE_API_KEY", "matchedBy": "similarity"}]
    assert summary["unresolved"] == []
    assert a["versions"][1]["steps"][0]["code"] == (
        f'x = secrets["{ids["API_KEY"]}"]\ny = secrets["{ids["STRIPE_API_KEY"]}"]\n')
    # both matches are granted, in archive order
    assert a["allowed_secrets"] == [ids["API_KEY"], ids["STRIPE_API_KEY"]]


def test_similarity_rejects_unrelated_low_and_ambiguous_names(store):
    """§5.1 similarity acceptance rule, worked examples: nothing in common
    (GITHUB vs GITLAB), a score under 0.60 (SLACK_BOT vs SLACK_USER), a
    second-best inside the 0.15 margin, and a description-only overlap - all
    unresolved, never a guessed credential."""
    _put_secrets(store,
                 ("GITLAB_TOKEN", ""),          # nothing in common with GITHUB_TOKEN
                 ("SLACK_USER_TOKEN", ""),      # 1/2 - under the 0.60 bar
                 ("ACME_MAIL_ALPHA", ""),       # tied with the next: inside the margin
                 ("ACME_MAIL_BETA", ""),
                 ("QUOKKA_NINE", "gmail app password for the mail step"))
    _, summary = transfer.import_automation(store, _archive(
        secrets=[_sec("1", "GITHUB_TOKEN"), _sec("2", "SLACK_BOT_TOKEN"),
                 _sec("3", "ACME_MAIL"),
                 _sec("4", "ZEBRA_ONE", "gmail app password for the mail step")],
        steps=[{"file": "01-a.py", "name": "A", "description": "",
                "code": 'a = secrets["1"]\nb = secrets["2"]\nc = secrets["3"]\n'
                        'd = secrets["4"]\n'}]))
    assert summary["secretsMatched"] == []
    assert [u["name"] for u in summary["unresolved"]] == [
        "GITHUB_TOKEN", "SLACK_BOT_TOKEN", "ACME_MAIL", "ZEBRA_ONE"]


def test_agents_match_by_configuration_with_default_tie_break(store):
    """§5.1 agents ladder rung 2: same harness + mode + effective model, any
    name, tie-broken by (higher similarity, then the local default agent)."""
    ids = _put_agents(store, [_agent("Alpha"), _agent("Beta")], default=1)
    # nothing to tell them apart by name → the local default agent wins
    _, summary = transfer.import_automation(store, _archive(agents=[_ag("1", "Zeta")]))
    assert summary["agentsMatched"] == [
        {"name": "Zeta", "matchedTo": "Beta", "matchedBy": "configuration", "ready": False}]
    # a name the similarity score can separate beats the default-agent rung
    _, summary2 = transfer.import_automation(store, _archive(agents=[_ag("1", "Alpha Prime")]))
    assert summary2["agentsMatched"][0]["matchedTo"] == "Alpha"
    assert ids["Beta"] == store.default_agent_id


def test_agent_exact_match_needs_name_harness_mode_and_effective_model(store):
    """§5.1 rung 1: grant name (casefolded) + harness + mode + effective model
    (`model` compares as null under mode default)."""
    _put_agents(store, [_agent("coder", harness="OpenCode", mode="custom",
                               model="anthropic/x")])
    _, summary = transfer.import_automation(store, _archive(
        agents=[_ag("1", "Coder", harness="OpenCode", mode="custom", model="anthropic/x")]))
    assert summary["agentsMatched"][0]["matchedBy"] == "name"
    # same name, a different model → not the exact rung; the configuration rung
    # doesn't apply either (models differ), so the similarity rung takes it
    _, summary2 = transfer.import_automation(store, _archive(
        agents=[_ag("1", "Coder", harness="OpenCode", mode="custom", model="anthropic/y")]))
    assert summary2["agentsMatched"][0]["matchedBy"] == "similarity"


def test_claim_rule_gives_a_local_record_to_one_ref_only(store):
    """§5.1: a local record is claimed by at most one archive ref - two refs
    can never collapse onto one record; within a pass, listing order wins."""
    ids = _put_secrets(store, ("STRIPE_API_KEY", ""))
    a, summary = transfer.import_automation(store, _archive(
        secrets=[_sec("1", "STRIPE_KEY"), _sec("2", "STRIPE_SECRET")],
        steps=[{"file": "01-a.py", "name": "A", "description": "",
                "code": 'x = secrets["1"]\ny = secrets["2"]\n'}]))
    assert summary["secretsMatched"] == [
        {"name": "STRIPE_KEY", "matchedTo": "STRIPE_API_KEY", "matchedBy": "similarity"}]
    assert summary["unresolved"] == [
        {"kind": "secret", "name": "STRIPE_SECRET", "description": ""}]
    code = a["versions"][1]["steps"][0]["code"]
    assert f'x = secrets["{ids["STRIPE_API_KEY"]}"]' in code
    minted = next(i for i in SECRET_REF_RE.findall(code) if i != ids["STRIPE_API_KEY"])
    assert a["unresolved_references"][minted]["name"] == "STRIPE_SECRET"


# ---------- unresolved references ----------

def _unresolved_archive():
    """One unmatched secret (code subscript + step entry) and one unmatched
    agent (step entry), plus the manifest authoring agent pointing at it."""
    return _archive(
        name="Needs fixing",
        secrets=[_sec("1", "MAIL_PASS", "mail password")],
        agents=[_ag("1", "Ghost", harness="Gemini CLI")],
        agent="1",
        steps=[{"file": "01-a.py", "name": "A", "description": "",
                "secrets": [{"ref": "1", "why": "sends the mail"}],
                "code": 'from autowright import secrets\nx = secrets["1"]  # MAIL_PASS\n'},
               {"file": "02-b.py", "name": "B", "description": "", "agent": True,
                "why": "judgment", "agents": [{"ref": "1"}], "code": "print('b')\n"}])


def test_unresolved_references_land_and_are_stored(store):
    """§5.1: a ref no rung matched lands as a freshly minted local id plus a
    §4.1 unresolved_references entry - the import still succeeds, creates no
    records, and the automation arrives needing attention."""
    _put_agents(store, [_agent("Alpha")])
    _put_secrets(store, ("UNRELATED_VALUE", ""))
    secrets_before = [dict(s) for s in store.secrets]
    agents_before = [dict(g) for g in store.agents]

    a, summary = transfer.import_automation(store, _unresolved_archive())

    # the minted ids are substituted everywhere the ref appeared
    code = a["versions"][1]["steps"][0]["code"]
    (sid,) = SECRET_REF_RE.findall(code)
    assert a["versions"][1]["steps"][0]["secrets"] == [{"id": sid, "why": "sends the mail"}]
    (entry,) = a["versions"][1]["steps"][1]["agents"]
    aid = entry["id"]
    assert {sid, aid}.isdisjoint({s["id"] for s in store.secrets}
                                 | {g["id"] for g in store.agents})
    assert a["unresolved_references"] == {
        sid: {"kind": "secret", "name": "MAIL_PASS", "description": "mail password"},
        aid: {"kind": "agent", "name": "Ghost", "description": ""}}
    # §5.1 summary: archive order, secrets before agents
    assert summary["unresolved"] == [
        {"kind": "secret", "name": "MAIL_PASS", "description": "mail password"},
        {"kind": "agent", "name": "Ghost", "description": ""}]
    assert summary["secretsMatched"] == [] and summary["agentsMatched"] == []
    # no records created, nothing unresolved granted
    assert store.secrets == secrets_before and store.agents == agents_before
    assert a["allowed_secrets"] == []
    assert a["enabled_agents"] == [agents_before[0]["id"]]
    # §4.1: stored top-level, serialized as unresolvedReferences, and read back
    top = yaml.safe_load((store.auto_dir(a) / "automation.yaml").read_text(encoding="utf-8"))
    assert top["unresolved_references"] == a["unresolved_references"]
    assert store.auto_json(a)["unresolvedReferences"] == a["unresolved_references"]
    s2 = Store()
    s2.load_all()
    assert s2.autos[a["id"]]["unresolved_references"] == a["unresolved_references"]
    # §4.1 problems: the unresolved kinds, naming the archive records
    noun = paths.machine_noun()
    labels = [p for p in store.auto_json(a)["problems"]
              if p["kind"].endswith("-unresolved")]
    assert labels == [
        {"kind": "secret-unresolved",
         "label": f"Imported secret MAIL_PASS has no match on this {noun}. "
                  "Pick one of your secrets on the edit page."},
        {"kind": "agent-unresolved",
         "label": f"Imported agent Ghost has no match on this {noun}. "
                  "Choose one of your agents on the edit page."}]


def test_export_of_unresolved_reference_names_the_import(store):
    """§5.1: exporting a needs-attention automation answers 422 in the
    import's own words - the record never existed on this machine, so the
    deleted-record "no longer exists" copy would lie."""
    _put_agents(store, [_agent("Alpha")])
    a, _ = transfer.import_automation(store, _unresolved_archive())
    with pytest.raises(transfer.TransferError) as e:
        transfer.export_automation(store, a)
    msg = str(e.value)
    assert "step 'A'" in msg
    assert "still uses MAIL_PASS from the imported file" in msg
    assert f"no match on this {paths.machine_noun()}" in msg
    assert "fix it in the editor before exporting" in msg
    assert "no longer exists" not in msg


def test_unresolved_drafting_agent_falls_back_to_the_default_pointer(store):
    """§5.1: an unresolved manifest agent falls back to the local default
    agent - and that fallback never stands in for the same ref's step
    references, which stay unresolved."""
    ids = _put_agents(store, [_agent("Alpha"), _agent("Beta")], default=1)
    a, _ = transfer.import_automation(store, _unresolved_archive())
    assert a["agent_id"] == ids["Beta"]
    assert a["enabled_agents"] == [ids["Beta"]]
    (entry,) = a["versions"][1]["steps"][1]["agents"]
    assert entry["id"] not in ids.values()
    assert a["unresolved_references"][entry["id"]]["name"] == "Ghost"


def test_discord_trigger_with_an_unresolved_secret(store):
    """§5.1: a trigger's token secret resolves through the same ladder - an
    unresolved one lands minted, and the §4.1 trigger-case problem says so."""
    _put_secrets(store, ("UNRELATED_VALUE", ""))
    a, summary = transfer.import_automation(store, _archive(
        secrets=[_sec("1", "BOT_TOKEN", "discord bot")],
        triggers=[{"kind": "discord", "channel": "42", "secret": "1", "pattern": "go"}]))
    t = a["triggers"][0]
    assert t["kind"] == "discord" and t["enabled"] is False
    assert t["secret"] not in {s["id"] for s in store.secrets}
    assert a["unresolved_references"] == {
        t["secret"]: {"kind": "secret", "name": "BOT_TOKEN", "description": "discord bot"}}
    assert summary["unresolved"] == [
        {"kind": "secret", "name": "BOT_TOKEN", "description": "discord bot"}]
    noun = paths.machine_noun()
    assert {"kind": "secret-unresolved",
            "label": f"A trigger needs the imported secret BOT_TOKEN, "
                     f"which has no match on this {noun}."} in store.auto_json(a)["problems"]


def test_import_onto_an_empty_machine_succeeds_with_no_agent(store, monkeypatch,
                                                             tmp_path_factory):
    """§5.1: a machine with no agents and no secrets lands everything
    unresolved - agent_id null, no grants - and the import still succeeds."""
    s2 = _fresh_home(monkeypatch, tmp_path_factory)
    assert s2.agents == [] and s2.secrets == []
    a, summary = transfer.import_automation(s2, _unresolved_archive())
    assert a["agent_id"] is None
    assert a["enabled_agents"] == [] and a["allowed_secrets"] == []
    assert [u["name"] for u in summary["unresolved"]] == ["MAIL_PASS", "Ghost"]
    assert s2.agents == [] and s2.secrets == []
    assert a["id"] in s2.autos


# ---------- landing: content, grants, comments ----------

def test_import_on_same_machine_matches_everything_and_grants_it(store):
    """§5.1: a same-machine round trip matches every reference by name, grants
    every match (authoring agent first), and lands under the deduped name."""
    a = _build(store)
    data = transfer.export_automation(store, a)
    b, summary = transfer.import_automation(store, data)
    ids = {s["name"]: s["id"] for s in store.secrets}
    gids = {g["name"]: g["id"] for g in store.agents}
    assert b["name"] == "Watcher 2" and summary["renamedFrom"] == "Watcher"
    assert summary["secretsMatched"] == [
        {"name": n, "matchedTo": n, "matchedBy": "name"}
        for n in ("API_KEY", "BOT_TOKEN", "MAIL_PASS")]
    assert summary["agentsMatched"] == [
        {"name": "Researcher", "matchedTo": "Researcher", "matchedBy": "name", "ready": False},
        {"name": "Coder", "matchedTo": "Coder", "matchedBy": "name", "ready": False}]
    assert summary["unresolved"] == []
    assert "unresolved_references" not in b
    assert store.auto_json(b)["unresolvedReferences"] == {}
    # §5.1 grants: matched secrets in archive order; authoring agent first
    assert b["allowed_secrets"] == [ids["API_KEY"], ids["BOT_TOKEN"], ids["MAIL_PASS"]]
    assert b["enabled_agents"] == [gids["Researcher"], gids["Coder"]]
    assert b["agent_id"] == gids["Researcher"]
    # the content survives with every reference rewritten to the local ids
    fetch, summarize = b["versions"][1]["steps"]
    assert fetch["code"] == ('from autowright import secrets\n'
                             f'x = secrets["{ids["API_KEY"]}"]  # API_KEY\n')
    assert fetch["secrets"] == [{"id": ids["MAIL_PASS"], "why": "sends the mail"}]
    assert summarize["agents"] == [{"id": gids["Coder"]}]
    assert b["versions"][1]["spec"] == a["versions"][1]["spec"]
    assert b["versions"][1]["instructions"] == "Keep it short."
    assert b["versions"][1]["note"] == "Imported"
    assert b["param_values"] == {"count": 7}
    # every trigger lands off, with fresh ids
    assert all(not t["enabled"] for t in b["triggers"])
    assert {t["kind"] for t in b["triggers"]} == {"cron", "app_start", "discord", "imessage"}
    d = next(t for t in b["triggers"] if t["kind"] == "discord")
    assert (d["channel"], d["secret"], d["pattern"], d["author"]) == \
        ("42", ids["BOT_TOKEN"], "go", ["111", "777"])
    im = next(t for t in b["triggers"] if t["kind"] == "imessage")
    assert (im["from"], im["pattern"]) == ("+15551234567", "run")
    # nothing unresolved, nothing ungranted (the declared package is its own
    # §6.2 problem, unrelated to the match ladders)
    assert [p for p in store.auto_json(b)["problems"]
            if p["kind"] != "package-missing"] == []
    # a fresh Store sees the same state after a reload (§5 disk-first)
    s3 = Store()
    s3.load_all()
    assert b["id"] in s3.autos


def test_import_on_fresh_machine_keeps_content_and_flags_every_reference(
        store, monkeypatch, tmp_path_factory):
    """§5.1: on a machine holding none of the exporter's records, the content
    still lands in full - every reference simply arrives unresolved."""
    a = _build(store)
    data = transfer.export_automation(store, a)
    s2 = _fresh_home(monkeypatch, tmp_path_factory)
    b, summary = transfer.import_automation(s2, data)
    assert b["id"] != a["id"]
    assert b["current_version"] == 1
    assert b["versions"][1]["spec"] == a["versions"][1]["spec"]
    assert b["param_values"] == {"count": 7}
    assert [u["name"] for u in summary["unresolved"]] == [
        "API_KEY", "BOT_TOKEN", "MAIL_PASS", "Researcher", "Coder"]
    assert [u["kind"] for u in summary["unresolved"]] == ["secret"] * 3 + ["agent"] * 2
    assert len(b["unresolved_references"]) == 5
    assert s2.secrets == [] and s2.agents == []
    assert b["agent_id"] is None and b["enabled_agents"] == []


def test_import_grants_ride_the_creation_call(store):
    """§5.1: grants pass directly into create_automation - no post-create grant
    patch, so no window exists in which the automation is stored with different
    grants than it ends up with. The only patch left seeds param values."""
    a = _build(store)
    data = transfer.export_automation(store, a)
    patches = []
    orig = store.patch_automation
    store.patch_automation = lambda auto, patch: (patches.append(dict(patch)),
                                                  orig(auto, patch))[1]
    try:
        b, _ = transfer.import_automation(store, data)
        assert patches == [{"paramValues": {"count": 7}}]
        # the grants are already in the on-disk top-level yaml (the one write)
        top = yaml.safe_load((store.auto_dir(b) / "automation.yaml").read_text(encoding="utf-8"))
        assert top["allowed_secrets"] == b["allowed_secrets"]
        assert top["enabled_agents"] == b["enabled_agents"]
        # an archive without values patches nothing at all
        patches.clear()
        c, _ = transfer.import_automation(
            store, transfer.export_automation(store, a, include_values=False))
        assert patches == []
        assert c["param_values"] == {}
    finally:
        store.patch_automation = orig


def test_enabled_agents_put_the_drafting_agent_first(store):
    """§5.1: the authoring agent leads stepAgents when it wasn't already among
    them, so a bare `agent: true` step's first-enabled-agent fallback lands on
    it - even when the archive lists it second."""
    ids = _put_agents(store, [_agent("Coder", harness="Codex"), _agent("Researcher")])
    a, _ = transfer.import_automation(store, _archive(
        agents=[_ag("1", "Coder", harness="Codex"), _ag("2", "Researcher")],
        agent="2",
        steps=[{"file": "01-a.py", "name": "A", "description": "", "agent": True,
                "why": "w", "agents": [{"ref": "1"}], "code": "print('a')\n"}]))
    assert a["agent_id"] == ids["Researcher"]
    assert a["enabled_agents"] == [ids["Researcher"], ids["Coder"]]


def test_import_rewrites_a_trailing_comment_after_a_renaming_match(store):
    """§5.1/§6.1: a subscript's trailing `# NAME` comment is rewritten to the
    matched record's name when the match renamed - so the comment stays
    truthful. Any other comment, and an unresolved ref's, is left alone."""
    ids = _put_secrets(store, ("STRIPE_API_KEY", ""))
    code = ('from autowright import secrets\n'
            'x = secrets["1"]  # STRIPE_KEY\n'
            'y = secrets["1"]  # the key the checkout step needs\n'
            'z = secrets["2"]  # GHOST_KEY\n')
    a, _ = transfer.import_automation(store, _archive(
        secrets=[_sec("1", "STRIPE_KEY"), _sec("2", "GHOST_KEY")],
        steps=[{"file": "01-a.py", "name": "A", "description": "", "code": code}]))
    landed = a["versions"][1]["steps"][0]["code"]
    ghost = next(i for i in SECRET_REF_RE.findall(landed) if i != ids["STRIPE_API_KEY"])
    assert landed == ('from autowright import secrets\n'
                      f'x = secrets["{ids["STRIPE_API_KEY"]}"]  # STRIPE_API_KEY\n'
                      f'y = secrets["{ids["STRIPE_API_KEY"]}"]  # the key the checkout '
                      'step needs\n'
                      f'z = secrets["{ghost}"]  # GHOST_KEY\n')


def test_import_summary_matched_agents_carry_readiness(store, monkeypatch):
    """§5.1: each matched agent's summary entry carries `ready` - the §19
    check-ready rule run on the MATCHED record's harness/mode/model."""
    a = _build(store)
    data = transfer.export_automation(store, a)
    calls = []

    def fake_ready(name, model=None, mode="default"):
        calls.append((name, model, mode))
        return name == "Claude Code"

    monkeypatch.setattr(transfer.harness, "check_ready", fake_ready)
    _b, summary = transfer.import_automation(store, data)
    assert {g["name"]: g["ready"] for g in summary["agentsMatched"]} == \
        {"Researcher": True, "Coder": False}
    # checked with each matched agent's real config, once per config
    assert sorted(calls) == [("Claude Code", None, "default"),
                             ("OpenCode", "anthropic/x", "custom")]


def test_import_writes_only_the_automation(store):
    """§5.1: import creates no agent or secret records, so agents.yaml and
    secrets.yaml are not written at all - not even rewritten unchanged."""
    a = _build(store)
    data = transfer.export_automation(store, a)
    files = [paths.agents_file(), paths.secrets_file()]
    before = [(p.read_bytes(), p.stat().st_mtime_ns) for p in files]
    b, _ = transfer.import_automation(store, data)
    assert [(p.read_bytes(), p.stat().st_mtime_ns) for p in files] == before
    assert len(store.secrets) == 3 and len(store.agents) == 2
    assert b["id"] in store.autos


def test_import_without_manifest_agent_uses_default_pointer(store):
    """§5.1/§4.7: an archive exported with no authoring agent lands on THE
    app-default agent - resolved through the single `default_agent` pointer."""
    ids = _put_agents(store, [_agent("Researcher"), _agent("Coder")], default=1)
    a, _ = transfer.import_automation(store, _archive())
    assert a["agent_id"] == ids["Coder"] == store.default_agent_id


# ---------- preview (§5.2) ----------

def test_preview_matches_what_the_import_lands(store):
    """§5.2: preview and confirm run one entry point, so the dry matchedTo /
    matchedBy are exactly what lands - and preview writes nothing."""
    _put_secrets(store, ("STRIPE_API_KEY", ""))
    _put_agents(store, [_agent("Alpha")])
    data = _archive(
        name="Mixed", description="Mixes things",
        secrets=[_sec("1", "STRIPE_KEY"), _sec("2", "GHOST_KEY", "no match here")],
        agents=[_ag("1", "Alpha"), _ag("2", "Spooky", harness="Gemini CLI")],
        agent="1",
        params=[{"name": "count", "kind": "number", "label": "Count"}],
        packages=[{"pip": "pandas", "import": "pandas"}],
        steps=[{"file": "01-a.py", "name": "A", "description": "d",
                "code": 'x = secrets["1"]\ny = secrets["2"]\n'},
               {"file": "02-b.py", "name": "B", "description": "", "agent": True,
                "why": "w", "agents": [{"ref": "1"}, {"ref": "2"}],
                "code": "print('b')\n"}])
    before = (len(store.autos), len(store.secrets), len(store.agents))
    p = transfer.preview_archive(store, data)
    assert (len(store.autos), len(store.secrets), len(store.agents)) == before
    assert p["name"] == "Mixed" and p["landsAs"] == "Mixed" and p["description"] == "Mixes things"
    assert [s["name"] for s in p["steps"]] == ["A", "B"]
    assert [s["agent"] for s in p["steps"]] == [False, True]
    assert p["params"] == [{"name": "count", "kind": "number"}]
    assert p["packages"] == [{"pip": "pandas", "import": "pandas"}]
    assert p["secrets"] == [
        {"name": "STRIPE_KEY", "description": "", "matchedTo": "STRIPE_API_KEY",
         "matchedBy": "similarity"},
        {"name": "GHOST_KEY", "description": "no match here", "matchedTo": None,
         "matchedBy": None}]
    assert p["agents"] == [
        {"name": "Alpha", "harness": "Claude Code", "mode": "default", "model": None,
         "matchedTo": "Alpha", "matchedBy": "name"},
        {"name": "Spooky", "harness": "Gemini CLI", "mode": "default", "model": None,
         "matchedTo": None, "matchedBy": None}]
    # §5.1/§5.2 agreement: the landed summary says exactly the same
    _a, summary = transfer.import_automation(store, data)
    assert summary["secretsMatched"] == [
        {"name": s["name"], "matchedTo": s["matchedTo"], "matchedBy": s["matchedBy"]}
        for s in p["secrets"] if s["matchedTo"]]
    assert [(g["name"], g["matchedTo"], g["matchedBy"]) for g in summary["agentsMatched"]] == \
        [(g["name"], g["matchedTo"], g["matchedBy"]) for g in p["agents"] if g["matchedTo"]]
    assert [u["name"] for u in summary["unresolved"]] == ["GHOST_KEY", "Spooky"]
    # A broken archive rejects with the §5.1 message.
    with pytest.raises(transfer.TransferError, match="not a valid .autowright archive"):
        transfer.preview_archive(store, b"junk")


def test_import_name_dedupe_previews_and_reports(store, monkeypatch, tmp_path_factory):
    """§4.1/§5.1: a taken automation name previews (`landsAs`) and lands
    deduped, and the summary carries `renamedFrom`; a free name previews as
    itself and the summary reports no rename."""
    a = _build(store)
    data = transfer.export_automation(store, a)
    pv = transfer.preview_archive(store, data)
    assert pv["name"] == "Watcher"
    assert pv["landsAs"] == "Watcher 2"
    b, summary = transfer.import_automation(store, data)
    assert b["name"] == "Watcher 2"
    assert summary["renamedFrom"] == "Watcher"
    s2 = _fresh_home(monkeypatch, tmp_path_factory)
    pv2 = transfer.preview_archive(s2, data)
    assert pv2["landsAs"] == pv2["name"] == "Watcher"
    c, summary2 = transfer.import_automation(s2, data)
    assert c["name"] == "Watcher"
    assert summary2["renamedFrom"] is None


# ---------- step bounds, filenames, hand-edited archives ----------

def test_step_limits_retry_pair_and_handle_normalization(store, monkeypatch, tmp_path_factory):
    """§5.1: the §4.1 timeout AND retry pairs travel - an infinite_retries
    listener must not become single-attempt on another Mac. A hand-edited
    archive's formatted iMessage handle normalizes on import (stored verbatim
    it would never match chat.db's E.164 handles and silently never fire)."""
    ver = {"description": "", "params": [], "packages": [],
           "steps": [{"name": "Long", "description": "", "code": "print('x')\n",
                      "timeout": 900, "retries": 4},
                     {"name": "Listen", "description": "", "code": "print('y')\n",
                      "no_timeout": True, "infinite_retries": True}],
           "spec": [{"kind": "h1", "text": "T"}], "instructions": ""}
    a = store.create_automation(
        ver, name="Limits", agent_id=None,
        triggers=[{"id": new_id(), "kind": "imessage", "enabled": True,
                   "from": "+15551234567"}])
    data = transfer.export_automation(store, a)
    meta = yaml.safe_load(zipfile.ZipFile(io.BytesIO(data)).read("automation/automation.yaml"))
    s1, s2 = meta["steps"]
    assert s1["timeout"] == 900 and s1["retries"] == 4
    assert s2["no_timeout"] is True and s2["infinite_retries"] is True

    def formatted(m):
        for t in m["triggers"]:
            if t["kind"] == "imessage":
                t["from"] = "+1 (555) 123-4567"
        return m

    s2_store = _fresh_home(monkeypatch, tmp_path_factory)
    b, _summary = transfer.import_automation(s2_store, _rezip_manifest(data, formatted))
    t1, t2 = b["versions"][1]["steps"]
    assert t1["timeout"] == 900 and t1["retries"] == 4
    # internal shape is snake_case only - no camelCase ever reaches storage
    assert t2.get("no_timeout") and t2.get("infinite_retries")
    assert b["triggers"][0]["from"] == "+15551234567"


def test_import_rejects_out_of_bounds_step_limits(store):
    """§5.1: imported steps obey the §8 bounds - retries 1-10, timeout never
    with no_timeout, retries never with infinite_retries. An archive can't land
    a step no drafting call could produce."""
    ver = {"description": "", "params": [], "packages": [],
           "steps": [{"name": "Only", "description": "", "code": "print('x')\n",
                      "timeout": 60, "retries": 2}],
           "spec": [{"kind": "h1", "text": "T"}], "instructions": ""}
    a = store.create_automation(ver, name="Bounds", agent_id=None, triggers=[])
    data = transfer.export_automation(store, a)
    before = len(store.autos)

    def edited(**over):
        def edit(meta):
            meta["steps"][0].update(over)
            return meta
        return _rezip_meta(data, edit)

    with pytest.raises(transfer.TransferError, match="invalid step retries: 11"):
        transfer.import_automation(store, edited(retries=11))
    with pytest.raises(transfer.TransferError, match="invalid step retries: 0"):
        transfer.import_automation(store, edited(retries=0))
    with pytest.raises(transfer.TransferError,
                       match="can't combine timeout and no_timeout"):
        transfer.import_automation(store, edited(no_timeout=True))
    with pytest.raises(transfer.TransferError,
                       match="can't combine retries and infinite_retries"):
        transfer.import_automation(store, edited(infinite_retries=True))
    assert len(store.autos) == before


def test_step_filename_traversal_rejected(store):
    a = _build(store)
    data = transfer.export_automation(store, a)
    before = len(store.autos)

    def evil(meta):
        meta["steps"][0]["file"] = "../evil.py"
        return meta

    with pytest.raises(transfer.TransferError, match="invalid step filename"):
        transfer.import_automation(store, _rezip_meta(data, evil))
    assert len(store.autos) == before


def test_import_rejects_unordered_step_filenames(store):
    """§5.1: step filenames obey the NN-name.py rule in listed order - a looser
    archive used to import fine and then 422 on every later save."""
    a = store.create_automation(make_version(), "Archivey", None)
    data = transfer.export_automation(store, a)
    zin = zipfile.ZipFile(io.BytesIO(data))
    meta = yaml.safe_load(zin.read("automation/automation.yaml"))
    meta["steps"][0]["file"] = "say.py"  # no NN- prefix
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zout:
        for n in zin.namelist():
            if n == "automation/automation.yaml":
                zout.writestr(n, yaml.safe_dump(meta))
            elif n == "automation/01-say.py":
                zout.writestr("automation/say.py", zin.read(n))
            else:
                zout.writestr(n, zin.read(n))
    with pytest.raises(transfer.TransferError, match="NN-name.py"):
        transfer.import_automation(store, buf.getvalue())


def test_duplicate_app_start_and_invalid_triggers_rejected(store):
    before = len(store.autos)
    with pytest.raises(transfer.TransferError, match="more than one app_start"):
        transfer.import_automation(store, _archive(
            triggers=[{"kind": "app_start"}, {"kind": "app_start"}]))
    with pytest.raises(transfer.TransferError, match="unsupported trigger in the archive"):
        transfer.import_automation(store, _archive(triggers=[{"kind": "time",
                                                              "at": "2999-01-01T09:00"}]))
    with pytest.raises(transfer.TransferError, match="invalid trigger in the archive"):
        transfer.import_automation(store, _archive(triggers=[{"kind": "cron",
                                                              "expression": "nope"}]))
    # §5.1: a discord entry's `secret` is a numbered ref, never a name or id
    with pytest.raises(transfer.TransferError,
                       match="needs the numbered reference of the secret"):
        transfer.import_automation(store, _archive(
            secrets=[_sec("1", "BOT_TOKEN")],
            triggers=[{"kind": "discord", "channel": "42", "secret": "BOT_TOKEN"}]))
    assert len(store.autos) == before


def test_import_lands_run_if_missed_from_the_manifest(store):
    """§5.1/§4.3: a manifest cron with `run_if_missed: false` lands the stored
    opt-out; without the key the imported cron takes the true default, and a
    non-boolean is a rejected archive."""
    a, _ = transfer.import_automation(store, _archive(triggers=[
        {"kind": "cron", "expression": "0 8 * * *", "run_if_missed": False},
        {"kind": "cron", "expression": "0 9 * * *"}]))
    assert [t.get("runIfMissed", "absent") for t in a["triggers"]] == [False, "absent"]
    before = len(store.autos)
    with pytest.raises(transfer.TransferError,
                       match="invalid trigger in the archive: run if missed must be "
                             "true or false"):
        transfer.import_automation(store, _archive(triggers=[
            {"kind": "cron", "expression": "0 8 * * *", "run_if_missed": "no"}]))
    assert len(store.autos) == before


def test_import_rejects_and_writes_nothing(store):
    a = _build(store)
    data = transfer.export_automation(store, a)
    before = (len(store.autos), len(store.secrets), len(store.agents))
    files = [paths.agents_file(), paths.secrets_file()]
    files_before = [p.read_bytes() for p in files]

    with pytest.raises(transfer.TransferError, match="not a valid"):
        transfer.import_automation(store, b"garbage")

    # a manifest step whose script file is missing from the zip
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for n in src.namelist():
            if not n.endswith(".py"):
                out.writestr(n, src.read(n))
    with pytest.raises(transfer.TransferError, match="missing automation/"):
        transfer.import_automation(store, buf.getvalue())

    bad_agent = _rezip(data, lambda n, b: yaml.safe_dump(
        {"agents": [{"ref": "1", "name": "X", "harness": "Nope"}]}).encode()
        if n == "agents.yaml" else None)
    with pytest.raises(transfer.TransferError, match="invalid agent"):
        transfer.import_automation(store, bad_agent)

    bad_secret = _rezip(data, lambda n, b: yaml.safe_dump(
        {"secrets": [{"ref": "1", "name": "lower_case"}]}).encode()
        if n == "secrets.yaml" else None)
    with pytest.raises(transfer.TransferError, match="invalid secret in the archive"):
        transfer.import_automation(store, bad_secret)

    assert (len(store.autos), len(store.secrets), len(store.agents)) == before
    assert [p.read_bytes() for p in files] == files_before


def test_import_without_optional_members_succeeds(store):
    """§5.1: agents.yaml / secrets.yaml are optional - a reference-free archive
    stripped of them imports rather than being rejected. An archive that DOES
    carry references can't lose the yaml its refs must resolve against."""
    def _strip(data):
        src = zipfile.ZipFile(io.BytesIO(data))
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as out:
            for nm in src.namelist():
                if nm not in ("agents.yaml", "secrets.yaml"):
                    out.writestr(nm, src.read(nm))
        return buf.getvalue()

    ver = {"description": "", "params": [], "packages": [],
           "steps": [{"name": "Only", "description": "", "code": "print('x')\n"}],
           "spec": [{"kind": "h1", "text": "T"}], "instructions": ""}
    plain = store.create_automation(ver, name="Plain", agent_id=None, triggers=[])
    b, summary = transfer.import_automation(
        store, _strip(transfer.export_automation(store, plain)))
    assert b["id"] in store.autos
    assert summary["secretsMatched"] == [] and summary["agentsMatched"] == []
    assert summary["unresolved"] == []
    # reference-carrying archive: the stripped yaml leaves its refs dangling
    a = _build(store)
    with pytest.raises(transfer.TransferError, match="isn't listed in the archive's"):
        transfer.import_automation(store, _strip(transfer.export_automation(store, a)))


# ---------- untrusted input: caps and member parse rejects ----------

def test_total_decompressed_size_cap(store):
    """Members individually under _MAX_MEMBER_BYTES whose sum crosses
    _MAX_TOTAL_BYTES → rejected up front, nothing written."""
    before = (len(store.autos), len(store.secrets), len(store.agents))
    member = bytes(30 * 1024 * 1024)                 # zeros - deflates tiny
    assert len(member) < transfer._MAX_MEMBER_BYTES
    n = transfer._MAX_TOTAL_BYTES // len(member) + 1  # sum > total cap
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(n):
            z.writestr(f"pad{i}.bin", member)
    data = buf.getvalue()
    assert len(data) < transfer.MAX_ARCHIVE_BYTES     # the archive itself stays small
    with pytest.raises(transfer.TransferError, match="decompresses far beyond"):
        transfer.import_automation(store, data)
    assert (len(store.autos), len(store.secrets), len(store.agents)) == before


def test_import_rejects_oversized_member(client):
    from autowright import transfer

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.yaml", "\0" * (transfer._MAX_MEMBER_BYTES + 1))
    r = client.post("/automations/import", content=buf.getvalue())
    assert r.status_code == 422
    assert "large" in r.json()["detail"]


def test_member_yaml_and_text_parse_rejects(store):
    """§5.1: a member that is missing, non-YAML, a non-mapping, or non-UTF-8
    text is a TransferError naming the member - never a stack trace, never a
    partial import."""
    a = _build(store)
    data = transfer.export_automation(store, a)
    before = len(store.autos)

    def rejects(match, edit):
        with pytest.raises(transfer.TransferError, match=match):
            transfer.import_automation(store, _rezip(data, edit))

    # manifest.yaml gone entirely (rebuild without it)
    src = zipfile.ZipFile(io.BytesIO(data))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for nm in src.namelist():
            if nm != "manifest.yaml":
                out.writestr(nm, src.read(nm))
    with pytest.raises(transfer.TransferError, match="missing manifest.yaml"):
        transfer.import_automation(store, buf.getvalue())

    rejects("manifest.yaml isn't valid YAML",
            lambda nm, b: b"{ not: [valid" if nm == "manifest.yaml" else None)
    rejects("manifest.yaml must hold a YAML mapping",
            lambda nm, b: b"- just\n- a list\n" if nm == "manifest.yaml" else None)
    # an empty member parses to {} - rejected for what it lacks (no format
    # field), not with a parse crash
    rejects("unsupported archive format None",
            lambda nm, b: b"\n" if nm == "manifest.yaml" else None)
    # spec.md is read as text - non-UTF-8 bytes are named, not decoded lossily
    rejects("automation/spec.md isn't valid UTF-8",
            lambda nm, b: b"\xff\xfe broken" if nm == "automation/spec.md" else None)
    assert len(store.autos) == before


def test_manifest_and_meta_shape_rejects(store):
    """§5.1 _validate: each malformed manifest/automation.yaml shape gets its
    own clear reject - param_values, params, packages, steps."""
    a = _build(store)
    data = transfer.export_automation(store, a)
    before = len(store.autos)
    cases = [
        ("manifest param_values must be a mapping",
         _rezip_manifest(data, lambda m: {**m, "param_values": [1, 2]})),
        ("the manifest has no automation name",
         _rezip_manifest(data, lambda m: {**m, "name": "  "})),
        ("the manifest's agent must be a numbered ref",
         _rezip_manifest(data, lambda m: {**m, "agent": "Researcher"})),
        ("param definitions must be a list",
         _rezip_meta(data, lambda m: {**m, "params": {"name": "x"}})),
        ("invalid parameter definition",
         _rezip_meta(data, lambda m: {**m, "params": [{"name": "x", "kind": "nope"}]})),
        ("invalid packages declaration",
         _rezip_meta(data, lambda m: {**m, "packages": [{"pip": "pandas"}]})),
        ("the archive holds no steps", _rezip_meta(data, lambda m: {**m, "steps": []})),
        ("invalid step manifest entry",
         _rezip_meta(data, lambda m: {**m, "steps": [{"file": "01-a.py"}]})),
    ]
    for match, bad in cases:
        with pytest.raises(transfer.TransferError, match=match):
            transfer.import_automation(store, bad)
    assert len(store.autos) == before


# ---------- §4.1 originOs ----------

def test_export_records_os_and_same_platform_round_trip(store, monkeypatch, tmp_path_factory):
    """§5.1: every export records the exporting machine's platform token; a
    same-platform import stamps §4.1 originOs and flags nothing."""
    here = paths.current_os()
    a = _build(store)
    data = transfer.export_automation(store, a)
    manifest = yaml.safe_load(zipfile.ZipFile(io.BytesIO(data)).read("manifest.yaml"))
    assert manifest["os"] == here
    s2 = _fresh_home(monkeypatch, tmp_path_factory)
    pv = transfer.preview_archive(s2, data)
    assert (pv["os"], pv["osMismatch"]) == (here, False)
    b, summary = transfer.import_automation(s2, data)
    assert (summary["os"], summary["osMismatch"]) == (here, False)
    assert b["origin_os"] == here
    assert not any(p["kind"] == "os-mismatch" for p in s2.auto_json(b)["problems"])


def test_import_from_another_os_flags_needs_fixing(store, monkeypatch, tmp_path_factory):
    """§5.1/§4.1: a foreign platform token never rejects - it stamps originOs,
    rides preview/summary as osMismatch, and surfaces as the os-mismatch
    problem until an edit save clears it (a §5 reload keeps it)."""
    a = _build(store)
    # A token that is foreign wherever this suite runs (§5.1 vocabulary).
    other = "linux" if paths.current_os() == "windows" else "windows"
    display = paths.os_display_name(other)
    win = _rezip_manifest(transfer.export_automation(store, a),
                          lambda m: {**m, "os": other})
    s2 = _fresh_home(monkeypatch, tmp_path_factory)
    pv = transfer.preview_archive(s2, win)
    assert (pv["os"], pv["osMismatch"]) == (other, True)
    b, summary = transfer.import_automation(s2, win)
    assert (summary["os"], summary["osMismatch"]) == (other, True)
    assert b["origin_os"] == other
    os_rows = [p for p in s2.auto_json(b)["problems"] if p["kind"] == "os-mismatch"]
    assert os_rows == [{"kind": "os-mismatch",
                        "label": f"Built on {display} — its steps may need rewriting "
                                 f"before they run on this {paths.machine_noun()}."}]
    # §5 disk-first: originOs survives a reload …
    s3 = Store()
    s3.load_all()
    b2 = s3.autos[b["id"]]
    assert b2["origin_os"] == other
    # … and an edit save clears it (§4.1: a local rework supersedes
    # "built elsewhere")
    s3.save_new_version(b2, dict(b2["versions"][1]))
    assert "origin_os" not in b2
    s4 = Store()
    s4.load_all()
    assert "origin_os" not in s4.autos[b["id"]]


def test_import_os_token_rules(store, monkeypatch, tmp_path_factory):
    """§5.1: an absent token is legal (nothing stamps, nothing flags); an
    unrecognized token is legal and always mismatches (label shows it
    verbatim); a malformed token rejects."""
    a = _build(store)
    data = transfer.export_automation(store, a)
    s2 = _fresh_home(monkeypatch, tmp_path_factory)
    legacy = _rezip_manifest(data, lambda m: {k: v for k, v in m.items() if k != "os"})
    pv = transfer.preview_archive(s2, legacy)
    assert (pv["os"], pv["osMismatch"]) == (None, False)
    b, summary = transfer.import_automation(s2, legacy)
    assert (summary["os"], summary["osMismatch"]) == (None, False)
    assert "origin_os" not in b
    assert not any(p["kind"] == "os-mismatch" for p in s2.auto_json(b)["problems"])
    unknown = _rezip_manifest(data, lambda m: {**m, "os": "beos"})
    b2, summary2 = transfer.import_automation(s2, unknown)
    assert (summary2["os"], summary2["osMismatch"]) == ("beos", True)
    row = next(p for p in s2.auto_json(b2)["problems"] if p["kind"] == "os-mismatch")
    assert "Built on beos" in row["label"]
    with pytest.raises(transfer.TransferError, match="os must be a non-empty string"):
        transfer.import_automation(s2, _rezip_manifest(data, lambda m: {**m, "os": "  "}))


# ---------- §5.2 URL import ----------

def test_resolve_url_rules():
    # https only
    with pytest.raises(transfer.TransferError, match="https"):
        transfer.resolve_url("http://example.com/a.autowright")
    # a direct .autowright link passes through, any host
    url = "https://example.com/dl/manga.autowright"
    assert transfer.resolve_url(url) == url
    # a non-github page with no archive suffix is rejected
    with pytest.raises(transfer.TransferError, match="direct link"):
        transfer.resolve_url("https://example.com/some/page")
    # an unrecognized github path is rejected
    with pytest.raises(transfer.TransferError, match="unrecognized github.com"):
        transfer.resolve_url("https://github.com/alice/repo/issues/3")


def test_resolve_github_release_tag_and_root_fallback(monkeypatch):
    def fake_api(path):
        table = {
            "/repos/alice/watcher/releases/latest": {"assets": [
                {"name": "notes.zip", "browser_download_url": "https://x/zip"},
                {"name": "watcher.autowright", "browser_download_url": "https://x/w"}]},
            "/repos/alice/watcher/releases/tags/v2": {"assets": [
                {"name": "watcher.autowright", "browser_download_url": "https://x/v2"}]},
            "/repos/alice/norel/releases/latest": None,
            "/repos/alice/norel/contents/": [
                {"type": "file", "name": "b.autowright", "download_url": "https://x/b"},
                {"type": "file", "name": "a.autowright", "download_url": "https://x/a"},
                {"type": "dir", "name": "c.autowright"}],
            "/repos/alice/empty/releases/latest": None,
            "/repos/alice/empty/contents/": [],
        }
        assert path in table, path
        return table[path]

    monkeypatch.setattr(transfer, "_github_api", fake_api)
    # repo page and /releases/latest → the release's .autowright asset
    assert transfer.resolve_url("https://github.com/alice/watcher") == "https://x/w"
    assert transfer.resolve_url("https://github.com/alice/watcher/releases/latest") == "https://x/w"
    # a tagged release resolves against that release's assets
    assert transfer.resolve_url("https://github.com/alice/watcher/releases/tag/v2") == "https://x/v2"
    # no release with an asset → repo root, first .autowright alphabetically
    assert transfer.resolve_url("https://github.com/alice/norel/") == "https://x/a"
    with pytest.raises(transfer.TransferError, match="no .autowright archive"):
        transfer.resolve_url("https://github.com/alice/empty")


class _FakeResp:
    def __init__(self, data, url="https://x/a.autowright"):
        self._buf, self._url = io.BytesIO(data), url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self, n=-1):
        return self._buf.read(n)

    def geturl(self):
        return self._url


def test_fetch_archive_download_cap_and_redirect_guard(monkeypatch):
    monkeypatch.setattr(transfer.urllib.request, "urlopen",
                        lambda req, timeout: _FakeResp(b"DATA"))
    data, resolved = transfer.fetch_archive("https://x/a.autowright")
    assert data == b"DATA" and resolved == "https://x/a.autowright"

    # the byte cap aborts mid-download
    monkeypatch.setattr(transfer, "MAX_ARCHIVE_BYTES", 4)
    monkeypatch.setattr(transfer.urllib.request, "urlopen",
                        lambda req, timeout: _FakeResp(b"toolarge"))
    with pytest.raises(transfer.TransferError, match="64 MB import limit"):
        transfer.fetch_archive("https://x/a.autowright")

    # a redirect off https is refused even though the pasted URL was https
    monkeypatch.setattr(transfer, "MAX_ARCHIVE_BYTES", 64 * 1024 * 1024)
    monkeypatch.setattr(transfer.urllib.request, "urlopen",
                        lambda req, timeout: _FakeResp(b"D", url="http://x/a.autowright"))
    with pytest.raises(transfer.TransferError, match="redirected off https"):
        transfer.fetch_archive("https://x/a.autowright")


def test_github_api_error_mapping(monkeypatch):
    """§5.2 _github_api over a faked urllib: 404 → None, 403/429 → the
    rate-limit message, other codes → the generic one, network errors →
    'couldn't reach GitHub'; a 200 parses the JSON body."""
    import json as jsonlib
    import urllib.error

    def urlopen_for(result):
        def fake(req, timeout):
            assert req.full_url.startswith("https://api.github.com/")
            if isinstance(result, Exception):
                raise result
            return _FakeResp(jsonlib.dumps(result).encode())
        return fake

    def http_error(code):
        return urllib.error.HTTPError("https://api.github.com/x", code, "err", {}, None)

    monkeypatch.setattr(transfer.urllib.request, "urlopen",
                        urlopen_for({"assets": []}))
    assert transfer._github_api("/repos/a/b/releases/latest") == {"assets": []}

    monkeypatch.setattr(transfer.urllib.request, "urlopen", urlopen_for(http_error(404)))
    assert transfer._github_api("/repos/a/b/releases/latest") is None

    for code in (403, 429):
        monkeypatch.setattr(transfer.urllib.request, "urlopen", urlopen_for(http_error(code)))
        with pytest.raises(transfer.TransferError, match="rate-limited"):
            transfer._github_api("/repos/a/b/releases/latest")

    monkeypatch.setattr(transfer.urllib.request, "urlopen", urlopen_for(http_error(500)))
    with pytest.raises(transfer.TransferError, match="GitHub answered 500"):
        transfer._github_api("/repos/a/b/releases/latest")

    monkeypatch.setattr(transfer.urllib.request, "urlopen",
                        urlopen_for(urllib.error.URLError("no route to host")))
    with pytest.raises(transfer.TransferError, match="couldn't reach GitHub"):
        transfer._github_api("/repos/a/b/releases/latest")


def test_safe_filename_rules():
    """§19: the export filename is the automation name sanitized for the
    filesystem - separators and control characters out, never empty."""
    assert transfer.safe_filename("Daily/Report:2") == "Daily Report 2"
    assert transfer.safe_filename('  "quoted"  ') == "quoted"
    assert transfer.safe_filename("...") == "automation"
    assert transfer.safe_filename("漫画ウォッチャー") == "漫画ウォッチャー"
