"""§19/§20/§3 backend API, CLI and service regressions (2026-10-13): the CLI's
tilde expansion, the tri-state launchd probe on stop/uninstall, push's
building-job guard, the mid-delete and draft-generation checks at drafting-job
registration, the typed draft identity keys, the per-automation agent-delete
re-point, and marketplace entry numbers mapped through the listing."""
import copy
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from conftest import make_version
from test_cli import FULL_AUTO, _MarketClient, _WorkdirClient, _run


def _draft(**over):
    d = {
        "name": "Typed draft",
        "spec": [{"kind": "h1", "text": "Typed draft"}],
        "steps": [{"file": "01-echo.py", "name": "Echo", "description": "",
                   "code": "from autowright import log\nlog('hi')\n"}],
        "triggers": [],
    }
    d.update(over)
    return d


# ---------- §20: dataPath= and marketplace paths are tilde-expanded ----------

class _Recorder:
    """Minimal §20 client stub: GETs answer from a table, writes are recorded."""

    def __init__(self, gets=None, reply=None):
        self.gets = gets or {}
        self.reply = reply or {}
        self.calls = []

    def req(self, method, path, body=None, timeout=30):
        if method == "GET":
            assert path in self.gets, f"unexpected GET {path}"
            return copy.deepcopy(self.gets[path])
        self.calls.append((method, path, body))
        return copy.deepcopy(self.reply)


MARKET = [{"id": "m1111111-a", "name": "Mine", "location": "/x/marketplace.yaml",
           "expanded": True, "autoRefresh": False,
           "entries": [{"index": 0, "title": "A"}, {"index": 2, "title": "B"},
                       {"index": 3, "title": "C"}]}]


def test_settings_set_data_path_expands_the_tilde(monkeypatch, tmp_path):
    """§20: zsh leaves `~` after `=` alone — the CLI expands it before making
    the path absolute, so `dataPath=~/AwData` never lands in a `~` folder."""
    monkeypatch.setenv("HOME", str(tmp_path / "homedir"))
    monkeypatch.chdir(tmp_path)
    c = _Recorder()
    _run(c, "settings", "set", "dataPath=~/AwData")
    assert c.calls == [("POST", "/settings/data-path",
                        {"path": str(tmp_path / "homedir" / "AwData")})]


def test_marketplace_paths_expand_the_tilde_but_links_travel_as_typed(monkeypatch, tmp_path):
    """§22.5: `set location=` and `add` expand `~` in a file path; a link is
    not a path and travels untouched."""
    monkeypatch.setenv("HOME", str(tmp_path / "homedir"))
    monkeypatch.chdir(tmp_path)
    c = _Recorder(gets={"/marketplace": {"sources": MARKET}}, reply={"name": "Mine"})
    _run(c, "marketplace", "set", "Mine", "location=~/x")
    assert c.calls == [("PATCH", "/marketplace/sources/m1111111-a",
                        {"location": str(tmp_path / "homedir" / "x")})]

    c = _Recorder(gets={"/marketplace": {"sources": MARKET}}, reply={"name": "Mine"})
    _run(c, "marketplace", "set", "Mine", "location=https://example.com/~/m.yaml")
    assert c.calls == [("PATCH", "/marketplace/sources/m1111111-a",
                        {"location": "https://example.com/~/m.yaml"})]

    c = _Recorder(reply={"id": "m2222222-b", "name": "New", "entries": []})
    _run(c, "marketplace", "add", "~/cat.yaml")
    assert c.calls == [("POST", "/marketplace/sources",
                        {"path": str(tmp_path / "homedir" / "cat.yaml")})]


# ---------- §22.5: entry numbers are positions in the listing ----------

def test_marketplace_install_sends_the_listed_entrys_index():
    """§22.5: a leniently-read copy can serve indexes with gaps — `install X 2`
    sends the index of the second listed entry, not 1."""
    c = _MarketClient(MARKET, writes={
        "/marketplace/sources/m1111111-a/entries/2/preview": {"token": "tok9"},
        "/automations/import/confirm": {
            "automation": {"name": "B", "id": "deadbeef-1"},
            "summary": {"secretsMatched": [], "agentsMatched": [], "unresolved": [],
                        "packages": []}}})
    _run(c, "marketplace", "install", "Mine", "2")
    assert c.calls == [
        ("POST", "/marketplace/sources/m1111111-a/entries/2/preview", None),
        ("POST", "/automations/import/confirm", {"token": "tok9"}),
    ]


def test_marketplace_audit_sends_the_listed_entrys_index():
    c = _Recorder(gets={"/marketplace": {"sources": MARKET},
                        "/marketplace/sources/m1111111-a/entries/3/archive": {"files": []}})
    _run(c, "marketplace", "audit", "Mine", "3")
    assert c.calls == []  # a read, nothing written


def test_marketplace_entry_number_out_of_range_keeps_its_error():
    c = _Recorder(gets={"/marketplace": {"sources": MARKET}})
    with pytest.raises(SystemExit) as ei:
        _run(c, "marketplace", "install", "Mine", "4")
    assert str(ei.value.code) == "'Mine' lists 3 automation(s) - there is no entry 4"
    assert c.calls == []


# ---------- §20: push refuses while a drafting job is building ----------

class _BuildingJobClient(_WorkdirClient):
    """No draft yet, but the §19 draft container answers a building job."""

    def req(self, method, path, body=None, timeout=30):
        if method == "GET" and path == f"/draft/{self.auto['id']}":
            self.fetched.append(path)
            return {"draft": None, "job": {"jobId": "j1", "status": "building",
                                           "mode": "chat"}}
        return super().req(method, path, body, timeout)


def test_push_refuses_while_a_drafting_job_is_building(tmp_path, capsys):
    from autowright import cli

    d = tmp_path / "wd"
    cli.write_workdir(d, FULL_AUTO)
    c = _BuildingJobClient()
    with pytest.raises(SystemExit) as ei:
        _run(c, "automation", "push", "Daily Report", str(d))
    assert str(ei.value.code) == ("'Daily Report' has an unsaved draft in the app - "
                                  "pass --discard-draft to replace it")
    assert c.posted == []

    c = _BuildingJobClient()
    _run(c, "automation", "push", "Daily Report", str(d), "--discard-draft")
    assert any(p[1] == f"/automations/{FULL_AUTO['id']}/versions" for p in c.posted)
    assert "saved 'Daily Report' as v2" in capsys.readouterr().out


# ---------- §3: stop/uninstall judge "still registered" tri-state ----------

launchd_only = pytest.mark.skipif(sys.platform != "darwin",
                                  reason="launchd implementation")


@pytest.fixture()
def wedged_service(home, monkeypatch):
    """service.py with the plist in the test home and every launchctl wedged."""
    from autowright import service

    plist = home / "LaunchAgents" / f"{service.LABEL}.plist"
    plist.parent.mkdir(parents=True)
    plist.write_bytes(b"<plist/>")
    monkeypatch.setattr(service, "plist_path", lambda: plist)
    monkeypatch.setattr(service, "shim_paths", lambda: [home / "bin" / "autowright"])

    def wedged(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(service.subprocess, "run", wedged)
    # §3: an unknown probe keeps the unload wait going until its deadline —
    # shortened here so the wedged cases don't sit out the real 10 s.
    monkeypatch.setattr(service, "_UNLOAD_DEADLINE_S", 0.2)
    return SimpleNamespace(mod=service, plist=plist)


@launchd_only
def test_stop_with_a_wedged_launchctl_fails_instead_of_claiming_stopped(wedged_service):
    """§3: a timed-out probe is unknown, never "not registered" — with
    KeepAlive the job launchd still holds would respawn the backend."""
    svc = wedged_service.mod
    out = svc.stop()
    assert out == "stop failed: launchctl timed out"
    assert svc.result_code(out) == 1


@launchd_only
def test_uninstall_with_a_wedged_launchctl_fails(wedged_service):
    svc = wedged_service.mod
    out = svc.uninstall()
    assert out.split(" · ")[0] == "uninstall failed: launchctl timed out"
    assert svc.result_code(out) == 1


@launchd_only
def test_registered_is_tri_state(wedged_service, monkeypatch):
    svc = wedged_service.mod
    assert svc._registered() is None
    monkeypatch.setattr(svc.subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(returncode=113, stdout="", stderr=""))
    assert svc._registered() is False
    monkeypatch.setattr(svc.subprocess, "run",
                        lambda cmd, **kw: SimpleNamespace(returncode=0, stdout="", stderr=""))
    assert svc._registered() is True


# ---------- §19: mid-delete + draft generation at job registration ----------

def _fake_start(api, monkeypatch, started):
    """draft_jobs.start registers a real building record (so cancel finds it)."""
    def start(mode, agent, user_text, current, grants, **kw):
        job_id = f"job-{len(started)}"
        started.append(job_id)
        api.draft_jobs.jobs[job_id] = {"id": job_id, "status": "building",
                                       "_cancel": False, "_proc": {},
                                       "_owner": kw.get("owner_id")}
        return job_id

    monkeypatch.setattr(api.draft_jobs, "start", start)


def test_a_discard_during_the_context_build_refuses_the_job(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Discarded", "mock")
    started: list[str] = []
    _fake_start(api, monkeypatch, started)

    def build_with_a_discard(*args, **kw):
        api.delete_draft_container(a["id"])  # DELETE /draft lands mid-build
        return []

    monkeypatch.setattr(api.testexec, "executions_context", build_with_a_discard)
    try:
        r = client.post("/drafts", json={"mode": "chat", "text": "x", "agentId": "mock",
                                         "automationId": a["id"]})
        assert r.status_code == 409
        assert r.json()["detail"] == "the draft was discarded"
        assert started and api.draft_jobs.jobs[started[0]]["status"] == "cancelled"
        assert api.draft_jobs.job_for(a["id"]) is None  # no live job left
    finally:
        for k in started:
            api.draft_jobs.jobs.pop(k, None)


def test_an_automation_delete_during_the_context_build_cancels_the_job(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Deleting", "mock")
    started: list[str] = []
    _fake_start(api, monkeypatch, started)

    def build_with_a_delete(*args, **kw):
        a["_deleting"] = True  # DELETE /automations flagged it mid-build
        return []

    monkeypatch.setattr(api.testexec, "executions_context", build_with_a_delete)
    try:
        r = client.post("/drafts", json={"mode": "chat", "text": "x", "agentId": "mock",
                                         "automationId": a["id"]})
        assert r.status_code == 404  # the pre-check's answer: already gone
        assert api.draft_jobs.jobs[started[0]]["status"] == "cancelled"
    finally:
        a.pop("_deleting", None)
        for k in started:
            api.draft_jobs.jobs.pop(k, None)


def test_a_draft_post_without_a_discard_still_registers(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Kept", "mock")
    started: list[str] = []
    _fake_start(api, monkeypatch, started)
    try:
        r = client.post("/drafts", json={"mode": "chat", "text": "x", "agentId": "mock",
                                         "automationId": a["id"]})
        assert r.status_code == 200
        assert api.draft_jobs.jobs[started[0]]["status"] == "building"
    finally:
        for k in started:
            api.draft_jobs.jobs.pop(k, None)


def test_tests_refuse_an_automation_being_deleted(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    a = store.create_automation(make_version(), "Deleting", "mock")
    a["_deleting"] = True
    monkeypatch.setattr(api.testexec, "start",
                        lambda *args, **kw: pytest.fail("no step code mid-delete"))
    try:
        r = client.post("/tests", json={"draft": _draft(), "automationId": a["id"]})
        assert r.status_code == 409
        assert r.json()["detail"] == "this automation is being deleted"
    finally:
        a.pop("_deleting", None)


# ---------- §19: draft identity keys are typed ----------

def test_draft_identity_keys_are_typed(client):
    assert client.post("/automations", json={"draft": _draft(name=5)}).status_code == 422
    assert client.post("/automations", json={"draft": _draft(description=[])}).status_code == 422
    assert client.post("/automations", json={"draft": _draft(note=1)}).status_code == 422
    assert client.post("/automations", json={"draft": _draft(triggers=3)}).status_code == 422
    r = client.post("/automations", json={"draft": _draft()})
    assert r.status_code == 200
    assert r.json()["name"] == "Typed draft"


# ---------- §5: agent delete re-points per automation ----------

def test_agent_delete_skips_an_unwritable_automation_and_still_publishes(client, monkeypatch):
    from autowright import api
    from autowright.storage import store

    gid = client.post("/agents", json={"harness": "Codex", "name": "Doomed"}).json()["id"]
    first = store.create_automation(make_version(), "First", gid)
    second = store.create_automation(make_version(), "Second", gid)
    real_patch = store.patch_automation

    def patch(a, repoint, *args, **kw):
        if a["id"] == first["id"]:
            raise OSError("disk full")
        return real_patch(a, repoint, *args, **kw)

    monkeypatch.setattr(store, "patch_automation", patch)
    published = []
    monkeypatch.setattr(api.hub, "publish", lambda ev, **kw: published.append({"event": ev, **kw}))

    r = client.delete(f"/agents/{gid}")
    assert r.status_code == 200
    assert all(g["id"] != gid for g in store.agents)
    assert second["agent_id"] == "mock"
    assert first["agent_id"] == gid  # skipped, logged
    events = [e["event"] for e in published]
    assert "agents.changed" in events
    assert [e.get("automationId") for e in published
            if e["event"] == "automation.changed"] == [second["id"]]
