"""Backend API (§19): localhost JSON over HTTP + one WebSocket, bearer-token auth."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets as pysecrets
import subprocess
import threading
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import __version__, harness, imessage, installer, keychain, models, paths, platform
from . import drafting, packages as pkglib, reqlog, timefmt, transfer, triggers as triggerlib
from .drafting import draft_jobs
from .engine import Engine, kill_orphan_agent_group, kill_orphan_group
from .events import OVERFLOW, hub
from .firing import cancel_unmatched_queue, drain_queue, finish_never_ran, fire_trigger, queue_manual
from .storage import (LiveExecutionError, StoreUnwritableError, _kind_ok, is_test,
                      exec_started_ms, iter_file_stats, new_id, size_label, store,
                      strip_param_values)
from . import testexec

log = logging.getLogger("autowright.api")

AUTH_TOKEN = pysecrets.token_hex(24)
SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")

engine = Engine(store)
_bearer = HTTPBearer(auto_error=False)


def token_ok(candidate: str | None) -> bool:
    """§19: constant-time — never leak the token through comparison timing."""
    return bool(candidate) and pysecrets.compare_digest(candidate, AUTH_TOKEN)


def auth(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    if cred is None or not token_ok(cred.credentials):
        raise HTTPException(401, "bad token")


# §3: main() registers its shutdown work here — uvicorn re-raises the captured
# SIGTERM once run() returns, so nothing after run() (a `finally` included)
# ever executes on a signal-driven stop. The lifespan below is the one place
# shutdown code reliably runs.
_shutdown_callbacks: list = []
_startup_callbacks: list = []


def register_shutdown(callback) -> None:
    _shutdown_callbacks.append(callback)


def register_startup(callback) -> None:
    """Run at lifespan startup, AFTER the stale-execution repair — main()
    registers the scheduler/listener starts here. Starting them before the
    repair opened a window where a tick could drain a DB-restored `queued`
    header (header-only: no `steps`/`redacted_secrets` until exec_full
    inflation → KeyError in update_execution) and where stale `executing`
    rows still held `_live` slots against admission."""
    _startup_callbacks.append(callback)


@asynccontextmanager
async def _lifespan(_: FastAPI):
    hub.bind_loop(asyncio.get_running_loop())
    _clear_import_spool()  # §5.2: spool files a crashed process left behind
    harness.clear_scratch()  # §5/§8: scratch dirs a crashed process left behind
    _repair_stale_executing()
    for callback in _startup_callbacks:
        callback()
    hub.publish("automation.changed")
    yield
    # §3: live step groups die with this backend — the successor's startup
    # recovery marks their records interrupted, and an orphan must not keep
    # writing memory/ beside the second copy the next cron tick starts.
    engine.kill_all_live()
    # §3: drafting harnesses die with it too — a stopping backend must never
    # leave an agent harness session group running with nobody to collect it.
    draft_jobs.kill_all_building()
    # §3: main()'s registered cleanup (guard thread, scheduler, listeners,
    # backend.json unlink) — error-tolerant, a failing callback must not keep
    # the next one from running.
    for callback in _shutdown_callbacks:
        try:
            callback()
        except Exception:  # noqa: BLE001
            pass


# §19: no interactive docs. /health is the only unauthenticated route, and any
# website in a browser can reach localhost — the app must not hand one its
# schema.
app = FastAPI(title="Autowright backend", version=__version__, lifespan=_lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)

# §19: the Electron renderer calls us cross-origin — packaged it loads from
# file:// (Origin: null), with the §15 renderer-URL knob from a local dev
# server. Those are the only shapes allowed: a page on the open internet can
# reach localhost too, and must not be handed even an unauthenticated response.
# One rule for both modes — no dev-only branch. The bearer token remains the
# real gate, and the service binds to 127.0.0.1 only.
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^(null|http://(localhost|127\.0\.0\.1)(:\d+)?)$",
    allow_methods=["*"], allow_headers=["*"])


class _RequestLogMiddleware:
    """§5 request-log files: while developerMode is on, every served HTTP request
    (never the /ws WebSocket) lands as one file under <logs>/requests. Pure
    ASGI — taps the receive/send streams, so bodies are captured without
    disturbing them; only the first BODY_CAP bytes of each are kept."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not reqlog.enabled():
            await self.app(scope, receive, send)
            return
        ts = reqlog.stamp()
        t0 = time.monotonic()
        req_body, resp_body = bytearray(), bytearray()
        totals = [0, 0]
        status = [0]

        async def recv():
            msg = await receive()
            if msg.get("type") == "http.request":
                chunk = msg.get("body") or b""
                totals[0] += len(chunk)
                if len(req_body) < reqlog.BODY_CAP:
                    req_body.extend(chunk[: reqlog.BODY_CAP - len(req_body)])
            return msg

        async def snd(msg):
            if msg["type"] == "http.response.start":
                status[0] = msg["status"]
            elif msg["type"] == "http.response.body":
                chunk = msg.get("body") or b""
                totals[1] += len(chunk)
                if len(resp_body) < reqlog.BODY_CAP:
                    resp_body.extend(chunk[: reqlog.BODY_CAP - len(resp_body)])
            await send(msg)

        try:
            await self.app(scope, recv, snd)
        finally:
            reqlog.write_http(ts, scope, bytes(req_body), totals[0], status[0],
                              bytes(resp_body), totals[1],
                              (time.monotonic() - t0) * 1000)


app.add_middleware(_RequestLogMiddleware)


# §5/§19 unreadable-store guard: any write that would rewrite a top-level file
# which failed to load this session answers 409 — one handler for every route,
# so a corrupt file is degraded, never overwritten by its default.
@app.exception_handler(StoreUnwritableError)
async def _unwritable_handler(request: Request, exc: StoreUnwritableError):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=409, content={"detail": str(exc)})


def _auto_or_404(automation_id: str) -> dict:
    a = store.autos.get(automation_id)
    if not a:
        raise HTTPException(404, "automation not found")
    return a


def _auto_json_locked(a: dict) -> dict:
    """Serialize an automation under store.lock — the only correct way to build
    a response payload from live state (auto_json reads fields the engine and
    scheduler mutate concurrently)."""
    with store.lock:
        return store.auto_json(a)


def _publish_auto_changed(a: dict) -> None:
    """§19: a single-automation change event carries the changed row (list
    shape) so clients patch in place instead of re-fetching /state. Bare
    `automation.changed` stays the many-changed resync signal."""
    with store.lock:
        payload = store.auto_json(a, full=False)
    hub.publish("automation.changed", automationId=a["id"], automation=payload)


def _agent_or_404(agent_id: str) -> dict:
    # Every helper below that walks a store collection takes the lock itself
    # (it is an RLock, so the call sites already holding it are unaffected):
    # an unlocked walk can hit "dictionary changed size during iteration" when
    # the scheduler or engine writes mid-request. Same rule as
    # `_auto_json_locked`.
    with store.lock:
        for a in store.agents:
            if a["id"] == agent_id:
                return a
    raise HTTPException(404, "agent not found")


def _check_agent_refs(agent_id: str | None, step_agents: list | None) -> None:
    """§19 cross-field rule: `agentId` and `stepAgents` entries must reference
    configured agents. Needs store state, so it lives here as a 422 rather
    than in the request model."""
    with store.lock:
        ids = {g["id"] for g in store.agents}
    if agent_id is not None and agent_id not in ids:
        raise HTTPException(422, f"agentId {agent_id!r} isn't a configured agent")
    for x in step_agents or ():
        if x not in ids:
            raise HTTPException(422, f"stepAgents entry {x!r} isn't a configured agent")


def _clean_name(name: str | None) -> str | None:
    """§4.1/§4.7: names store trimmed — a whitespace-only name is a blank name,
    so padding can never dodge the uniqueness checks."""
    return (name or "").strip() or None


def _check_automation_name_free(name: str, exclude_id: str | None = None) -> None:
    """§4.1 uniqueness: automation names are unique across automations,
    case-insensitively — unambiguous display and the §20 exact-name/substring
    resolution rely on it. Write-time only: duplicates already on disk still
    load (the §4.7 rule)."""
    wanted = name.lower()
    with store.lock:
        clash = next((o for o in store.autos.values()
                      if o["id"] != exclude_id and o["name"].strip().lower() == wanted), None)
    if clash is not None:
        raise HTTPException(
            422, f"an automation named {clash['name']!r} already exists - "
                 "automation names must be unique")


def _check_grant_name_free(agent: dict, exclude_id: str | None = None) -> None:
    """§4.7 uniqueness: the effective grant name (name, else harness) is unique
    across agents, case-insensitively — the §8 grants yaml, the §20
    case-insensitive name flags, and unambiguous display all rely on it.
    Write-time only: duplicates already on disk still load."""
    wanted = harness.grant_name(agent).lower()
    with store.lock:
        clash = next((o for o in store.agents
                      if o["id"] != exclude_id and harness.grant_name(o).lower() == wanted), None)
    if clash is not None:
        raise HTTPException(
            422, f"an agent named {harness.grant_name(clash)!r} already exists - "
                 "agent names must be unique")


def _check_secret_refs(allowed_secrets: list | None) -> None:
    """§19 cross-field rule: `allowedSecrets` entries must reference stored
    secrets by their §4.8 id — the same shape rule as `stepAgents`."""
    with store.lock:
        ids = {s["id"] for s in store.secrets}
    for x in allowed_secrets or ():
        if x not in ids:
            raise HTTPException(422, f"allowedSecrets entry {x!r} isn't a stored secret id")


def _check_param_values(a: dict, values: dict) -> None:
    """§19 cross-field rule: every paramValues entry must match the
    automation's param definitions by name AND kind — a typo'd name or a
    mistyped value is a 422, never a silently-ignored store."""
    defs = {p["name"]: p.get("kind")
            for p in a["versions"].get(a["current_version"], {}).get("params") or []}
    for name, v in values.items():
        if name not in defs:
            raise HTTPException(422, f"paramValues names an unknown param {name!r}")
        if not _kind_ok(defs[name], v):
            raise HTTPException(
                422, f"paramValues[{name!r}] doesn't match the param's kind ({defs[name]})")


def _staged_values(a: dict, values: dict) -> dict:
    """§4.2 chat-staged map on save/create: entries matched by name AND kind
    against the current version's definitions land as stored values; unmatched
    entries drop silently (the lenient §5 matching — the definitions may have
    just been rebuilt, so a stale name is expected here, never a 422)."""
    defs = {p["name"]: p.get("kind")
            for p in a["versions"].get(a["current_version"], {}).get("params") or []}
    return {k: v for k, v in values.items() if k in defs and _kind_ok(defs[k], v)}


def _version_content(v: dict) -> dict:
    """§4.4 operational-only save: the versioned content, canonicalized so the
    stored serialization compares equal to a draft round-trip — falsy step and
    package keys drop (the serialized shape carries `agents: []`/nulls the
    manifest dialect omits), and param definitions compare without their
    resolved value fields (`on`/`lines`/`rows`/`value` — values are §4.2
    operational state, not versioned content)."""
    def strip(d: dict) -> dict:
        return {k: x for k, x in d.items() if x}

    def pdef(p: dict) -> dict:
        return {k: x for k, x in p.items() if k not in ("on", "lines", "rows", "value")}

    return {"params": [pdef(p) for p in v.get("params") or []],
            "packages": [strip(p) for p in v.get("packages") or []],
            "steps": [strip(s) for s in v.get("steps") or []],
            "spec": v.get("spec") or [],
            "instructions": v.get("instructions") or "",
            "notes": v.get("notes") or ""}


def _validate_draft_steps(d: dict, a: dict | None = None) -> None:
    """§19 server-side step validation: POST /automations and /versions run
    the §8 step validators (`drafting.validate_steps` — ast.parse, the §6.2
    import allowlist, manifest schema and step-file ordering, the
    timeout/retry rules) on the sent draft and answer 422 with the errors —
    an invalid draft can never land as a version, whatever client sent it.
    Grants context mirrors the §20 CLI's existence check: all configured
    agents + all stored secrets (ids; names ride along for error copy).
    `a` (the automation being saved, when one exists) supplies the §4.1
    unresolved_references map so a still-unfixed imported reference gets the
    §8 imported-file error copy instead of a raw id."""
    import yaml

    files: dict[str, str] = {}
    man_steps: list[dict] = []
    for s in d.get("steps") or []:
        if not isinstance(s, dict):
            raise HTTPException(422, "each step must be an object")
        files[str(s.get("file") or "")] = str(s.get("code") or "")
        # The serialized step shape carries `agents: []` / `secrets: []` /
        # `packages: []` / nulls on steps that have none — the validator's
        # manifest dialect omits those keys instead, so strip the empties on
        # the way in.
        man_steps.append({k: v for k, v in s.items()
                          if k != "code" and v is not None
                          and not (k in ("agents", "secrets", "packages", "why") and not v)})
    manifest = {"steps": man_steps, "params": d.get("params") or [],
                "packages": d.get("packages") or []}
    files["manifest.yaml"] = yaml.safe_dump(manifest, sort_keys=False)
    grants = {"agents": [{"id": g["id"], "name": harness.grant_name(g)} for g in store.agents],
              "secrets": [{"id": s["id"], "name": s["name"]} for s in store.secrets]}
    _, errors = drafting.validate_steps(
        files, grants, unresolved=(a or {}).get("unresolved_references"))
    if errors:
        raise HTTPException(422, "the draft doesn't validate: " + "; ".join(errors))


# The executions tree can be GBs across thousands of directories; the size
# label is display-only, so one walk per TTL window is plenty — and it must
# never run while holding store.lock (it would stall live log streaming).
_DATA_SIZE_TTL_S = 30
_data_size_cache: tuple[float, str] | None = None
# Serializes the walk itself: concurrent /state calls landing on an expired
# cache would each run the full recursive stat walk, burning a threadpool
# worker apiece for the same answer.
_data_size_lock = threading.Lock()


def _data_size_label() -> str:
    global _data_size_cache
    with _data_size_lock:
        now = time.monotonic()
        if _data_size_cache and now - _data_size_cache[0] < _DATA_SIZE_TTL_S:
            return _data_size_cache[1]
        p = store.executions_dir()
        total = sum(st.st_size for st in iter_file_stats(p))
        _data_size_cache = (now, size_label(total))
        return _data_size_cache[1]


def _agents_json() -> list[dict]:
    out = []
    with store.lock:
        for ag in store.agents:
            # §4.7 usedBy: { id, name } entries — the id is what the §12 chips
            # navigate by (a name lookup would be ambiguous under duplicate names).
            used = [{"id": a["id"], "name": a["name"]} for a in store.autos.values()
                    if a["agent_id"] == ag["id"]
                    or any(ag["id"] == e.get("id")
                           for s in a["versions"].get(a["current_version"], {}).get("steps", [])
                           for e in (s.get("agents") or []))]
            out.append({**ag, "usedBy": used,
                        "default": ag["id"] == store.default_agent_id})
    return out


def _settings_json() -> dict:
    s = dict(store.settings)
    s["dataPath"] = str(store.data_path())
    s["dataSize"] = _data_size_label()
    s["appPath"] = str(paths.app_support())
    return s


def _secrets_json() -> list[dict]:
    with store.lock:
        return [{"id": s["id"], "name": s["name"], "description": s.get("description") or "",
                 "set": bool(s.get("set", True)),
                 "usedBy": store.secret_used_by(s["id"])}
                for s in sorted(store.secrets, key=lambda s: s["name"])]


def _agent_grant(g: dict) -> dict:
    """§8 grants yaml entry: id, name, description, harness, model — the id is
    what the drafted manifest entries and agents["<id>"] code subscripts must
    carry, so the authoring agent needs it verbatim."""
    e = {"id": g["id"], "name": harness.grant_name(g)}
    if g.get("description"):
        e["description"] = g["description"]
    e["harness"] = g.get("harness", "")
    e["model"] = g.get("model") or "harness default"
    return e


def _secret_grant(secret_id: str) -> dict | None:
    """§8 grants yaml entry: id, name + description (omitted when empty).
    A dangling id grants nothing — None, skipped by the caller."""
    with store.lock:
        s = next((s for s in store.secrets if s["id"] == secret_id), None)
    if s is None:
        return None
    e = {"id": s["id"], "name": s["name"]}
    if s.get("description"):
        e["description"] = s["description"]
    return e


# ---------- health / state ----------
@app.get("/health")
def health() -> dict:
    # §2 platform layer: `os` is the §5.1 platform token; `capabilities` is
    # what this OS can honor — clients gate features here, never by sniffing
    # the platform at a call site.
    plat = platform.current()
    return {"version": __version__, "app": "Autowright",
            "os": plat.os_token, "capabilities": plat.capabilities.as_dict()}


@app.get("/instructions", dependencies=[Depends(auth)])
def instructions() -> dict:
    """§8 instruction files for the create/edit page:
    framework-instructions.md + default-build-instructions.md, with the
    §8 {{MACHINE}} placeholder resolved to the per-OS noun."""
    return {"framework": drafting.contract_preamble(),
            "defaultBuild": drafting.default_instructions()}


@app.get("/state", dependencies=[Depends(auth)])
def state() -> dict:
    settings = _settings_json()  # walks the executions tree — never under the lock
    with store.lock:
        # §7 window: every live header plus the newest finished page, in the
        # canonical order — deeper history pages in via GET /executions and
        # never rides the snapshot whole. Sort the raw headers and serialize
        # only the window: exec_json per row is three timestamp parses plus a
        # locale strftime, and paying that for every execution ever held (an
        # unbounded set under keepForever) on every /state — under store.lock —
        # is exactly the cost the §7 window exists to remove.
        hs = sorted(store.execs.values(),
                    key=lambda h: (-exec_started_ms(h), h["id"]))
        finished_left = EXECUTIONS_PAGE_LIMIT
        window = []
        for h in hs:
            if h["status"] in LIVE_STATUSES:
                window.append(store.exec_json(h))
            elif finished_left > 0:
                window.append(store.exec_json(h))
                finished_left -= 1
        return {
            "version": __version__,
            "automations": [store.auto_json(a) for a in store.autos.values()],
            "executions": window,
            "executionsTotal": len(store.execs),
            "agents": _agents_json(),
            "secrets": _secrets_json(),
            "settings": settings,
            "pendingDraft": store.pending_draft_summary(),
            # §19 background continuation: every building or held drafting job,
            # owner-keyed — backs the §9.1 drafting notes (kept current by the
            # draftjob.changed event).
            "draftJobs": draft_jobs.all_jobs(),
        }


# ---------- automations ----------
@app.get("/automations", dependencies=[Depends(auth)])
def list_autos() -> list[dict]:
    with store.lock:
        return [store.auto_json(a) for a in store.autos.values()]


@app.get("/automations/{automation_id}", dependencies=[Depends(auth)])
def get_auto(automation_id: str) -> dict:
    return _auto_json_locked(_auto_or_404(automation_id))


@app.patch("/automations/{automation_id}", dependencies=[Depends(auth)])
def patch_auto(automation_id: str, body: models.AutomationPatch) -> dict:
    a = _auto_or_404(automation_id)
    # §19: the model checked types/shapes (incl. the §6 concurrency ints with
    # their floors); the store-state cross-field rules land here.
    patch = body.model_dump(exclude_unset=True)
    if "name" in patch:
        # §4.1: trimmed, and a rename into another automation's name 422s
        # (excluding self, so a case-only rename keeps working).
        patch["name"] = _clean_name(patch["name"])
        if patch["name"]:
            _check_automation_name_free(patch["name"], exclude_id=a["id"])
    if "agentId" in patch or "stepAgents" in patch:
        _check_agent_refs(patch.get("agentId"), patch.get("stepAgents"))
    if "allowedSecrets" in patch:
        _check_secret_refs(patch.get("allowedSecrets"))
    if "paramValues" in patch:
        _check_param_values(a, patch["paramValues"])
    if "triggers" in patch:
        # §19: whole-list replace; message kinds / bad expressions / past times → 422.
        norm, err = triggerlib.normalize_triggers(
            patch["triggers"], existing_ids={t["id"] for t in a["triggers"]})
        if err:
            raise HTTPException(422, err)
        patch = {**patch, "triggers": norm}
    store.patch_automation(a, patch)
    if "triggers" in patch:
        # §6: a waiting entry whose trigger was just turned off or removed is
        # cancelled — it could never be re-admitted, and promoting it would
        # execute a firing the user just switched off.
        cancel_unmatched_queue(store, engine, automation_id)
    _publish_auto_changed(a)
    # A raised maxParallel may have just opened a slot for a waiting firing (§6).
    drain_queue(store, engine, automation_id)
    return _auto_json_locked(a)


@app.post("/automations/{automation_id}/queue/clear", dependencies=[Depends(auth)])
def clear_queue(automation_id: str) -> dict:
    """§19: cancel every §6 firing-queue entry waiting on this automation.
    Running executions are untouched — an empty queue answers 0, not 404."""
    a = _auto_or_404(automation_id)
    n = 0
    # Snapshot under the lock — queued_execs walks store.execs, and an
    # unlocked walk races a scheduler-tick insert (dict-changed-size 500).
    with store.lock:
        heads = list(store.queued_execs(automation_id))
    for h in heads:
        if engine.cancel(h["id"]):
            n += 1
    if n:
        _publish_auto_changed(a)
    return {"cancelled": n}


@app.post("/triggers/preview", dependencies=[Depends(auth)])
def triggers_preview(body: models.TriggersPreview) -> dict:
    """§19: a pure function endpoint — no state read or written. Validates and
    labels §4.3-shaped trigger dicts with the same triggers.py code that gates
    the PATCH, one result per entry in order. An invalid entry is a
    `valid: false` result, never a 422 (the editors preview half-typed state);
    only a body that isn't a list of trigger dicts gets the ordinary 422.
    Trigger math exists once, here on the backend — the renderer keeps no
    local mirror."""
    out: list[dict] = []
    seen_app_start = False
    for t in body.triggers:
        norm, err = triggerlib.normalize_triggers([t])
        if not err and norm[0]["kind"] == "app_start":
            # §4.3: at most one app_start per list — per-entry validation
            # can't see the duplicate, the loop can.
            if seen_app_start:
                err = "only one app-start trigger per automation"
            seen_app_start = True
        if err:
            entry = {"valid": False, "error": err, "label": "", "short": "", "nextAtMs": None}
            try:  # best-effort display for a half-typed entry
                entry["label"], entry["short"] = triggerlib.trigger_display(t)
            except Exception:  # noqa: BLE001 — undisplayable is fine, not an error
                pass
            out.append(entry)
            continue
        n = norm[0]
        label, short = triggerlib.trigger_display(n)
        nxt = triggerlib.trigger_next(n)  # None for app_start/message kinds and elapsed one-shots
        entry = {"valid": True, "label": label, "short": short,
                 "nextAtMs": int(nxt.timestamp() * 1000) if nxt else None}
        if nxt:
            entry["nextLabel"] = f"{nxt.strftime('%b')} {nxt.day}, {timefmt.clock(nxt)}"
        out.append(entry)
    return {"triggers": out}


@app.delete("/automations/{automation_id}", dependencies=[Depends(auth)])
def delete_auto(automation_id: str) -> dict:
    a = _auto_or_404(automation_id)
    with store.lock:
        # §19: close the admission window first — the automation stays in
        # store.autos until the last line, so a scheduler tick, listener
        # dispatch, or app-start firing admitted mid-delete would escape the
        # wait set below and re-create the tree after the rmtree. engine.start
        # refuses while this flag is set.
        a["_deleting"] = True
        live = list(a.get("_live") or ())
    for eid in live:
        engine.cancel(eid)
    # §19: delete settles the draft work too - a live §11 test or building §8
    # job would outlive the rmtree and resurrect the deleted directory when it
    # lands (tests never join _live, so the loop above misses them).
    live += _cancel_live_draft_work(automation_id)
    # §6: waiting firings go with it — their sender is told rather than left
    # waiting on an automation that no longer exists. Snapshot under the lock
    # (an unlocked store.execs walk races a scheduler-tick insert).
    with store.lock:
        queued = list(store.queued_execs(automation_id))
    for h in queued:
        engine.cancel(h["id"])
    # §19: a cancelled step gets a SIGTERM grace window (§7) — wait for the
    # engine threads to actually finish before the rmtree, or a step writing
    # memory/ during the window re-creates the directory with no versions/,
    # leaving a ghost tree the UI can never see.
    if live and not engine.wait_finished(live):
        logging.getLogger(__name__).warning(
            "delete %s: an execution thread outlived the kill grace — removing anyway", automation_id)
    store.delete_automation(a)
    # §19: the deleted row travels as automation=None — clients drop it in place.
    hub.publish("automation.changed", automationId=automation_id, automation=None)
    return {"ok": True}


def _draft_to_version(d: dict) -> dict:
    # §4.1 spelling boundary: the request models already normalized the steps'
    # camelCase flags (noTimeout/infiniteRetries) to snake_case — nothing past
    # the models reads the camel keys.
    return {"description": d.get("description", ""), "note": d.get("note", ""),
            # §4.2: the draft's params were seeded from the merged API shape —
            # strip the resolved-value keys so a version never stores values
            # inside its definitions (values live top-level; §5.1 export gates
            # on param_values alone).
            "params": strip_param_values(d.get("params")),
            "packages": d.get("packages", []),
            "steps": d.get("steps") or [],
            "spec": d.get("spec") or [], "instructions": d.get("instructions"),
            "notes": d.get("notes") or ""}


@app.post("/automations", dependencies=[Depends(auth)])
def create_auto(body: models.AutomationCreate) -> dict:
    d = body.draft.plain()
    if not d.get("steps"):
        raise HTTPException(422, "draft has no steps")
    _check_agent_refs(body.agentId, body.stepAgents)
    _check_secret_refs(body.allowedSecrets)
    # Create: no automation exists yet, so no trigger id is "already stored" —
    # a staged one-shot whose moment passed while the draft sat is the §4.3
    # spent case and drops out of the list; a no-id past time still 422s.
    triggers, err = triggerlib.normalize_triggers(d.get("triggers") or [],
                                                  existing_ids=set())
    if err:
        raise HTTPException(422, err)
    _validate_draft_steps(d)  # §19: the §8 validators run server-side
    _cancel_live_draft_work(None)  # §11/§19: Create settles the pending slot — its live test and jobs die
    a = store.create_automation(
        _draft_to_version(d),
        # §4.1/§19: the name may be agent-seeded or the fallback, so create
        # never 422s on a collision — create_automation dedupes it.
        name=_clean_name(body.name) or _clean_name(d.get("name")) or "New automation",
        agent_id=body.agentId,
        triggers=triggers,
        enabled_agents=body.stepAgents,
        allowed_secrets=body.allowedSecrets,
    )
    # §4.2/§19: the chat-staged value map applies after v1 lands — matched by
    # name+kind against v1's definitions, unmatched entries dropped silently.
    if body.paramValues:
        if matched := _staged_values(a, body.paramValues):
            store.patch_automation(a, {"paramValues": matched})
    # §8/§19: the chat-staged concurrency object applies like the PATCH's
    # fields — the request model validated the floors already.
    if conc := (body.concurrency.model_dump(exclude_unset=True)
                if body.concurrency is not None else None):
        store.patch_automation(a, conc)
    # §4.4 thread lifetime: the slot's chat moves onto the new automation —
    # the conversation continues on its edit page — behind the boundary
    # marker, so the pre-create session never reaches a later chat's agent.
    store.migrate_pending_chat(a)
    store.append_chat_marker(a, "Created as v1.")
    # §4.4: Create consumes the pending create-mode slot — settled drafts are
    # never resurrected.
    store.delete_draft(None)
    hub.publish("draft.changed")
    _publish_auto_changed(a)
    return _auto_json_locked(a)


@app.post("/automations/{automation_id}/versions", dependencies=[Depends(auth)])
def save_version(automation_id: str, body: models.VersionSave) -> dict:
    a = _auto_or_404(automation_id)
    d = body.draft.plain()
    if not d.get("steps"):
        raise HTTPException(422, "draft has no steps")
    sent = body.model_dump(exclude_unset=True)
    if "name" in sent:
        # §4.1/§19: the identity patch validates like the PATCH's, up front —
        # 422 aborts the save before anything lands.
        sent["name"] = _clean_name(sent["name"])
        if sent["name"]:
            _check_automation_name_free(sent["name"], exclude_id=a["id"])
    if "agentId" in sent or "stepAgents" in sent:
        _check_agent_refs(sent.get("agentId"), sent.get("stepAgents"))
    if "allowedSecrets" in sent:
        _check_secret_refs(sent.get("allowedSecrets"))
    # §4.3/§4.4: the draft's trigger list (merged in the editor) replaces the
    # automation's — validated like the PATCH, and before the version lands.
    triggers = None
    if "triggers" in d:
        triggers, err = triggerlib.normalize_triggers(
            d["triggers"], existing_ids={t["id"] for t in a["triggers"]})
        if err:
            raise HTTPException(422, err)
    _validate_draft_steps(d, a)  # §19: the §8 validators run server-side
    # A refused settle must be side-effect free: check the 409 condition
    # before the cancels (the in-lock check below stays authoritative).
    with store.lock:
        _reject_live_draft_exec(a)
    _cancel_live_draft_work(automation_id)  # §11/§19: saving settles the draft — its live test and jobs die
    ver = _draft_to_version(d)
    with store.lock:
        # Same guard as PUT/DELETE draft: saving deletes the draft container,
        # and a live Draft execution reads its step scripts lazily mid-run.
        _reject_live_draft_exec(a)
        # §4.4 operational-only save: unchanged versioned content mints no
        # version — the triggers/values/grants patch below still applies.
        cur = a["versions"].get(a["current_version"]) or {}
        minted = _version_content(ver) != _version_content(cur)
        n = store.save_new_version(a, ver) if minted else a["current_version"]
        patch = {k: sent[k] for k in ("agentId", "stepAgents", "allowedSecrets", "name") if k in sent}
        if triggers is not None:
            patch["triggers"] = triggers
        # §4.2: staged values land after the version — matched name+kind
        # against the landing definitions, unmatched dropped silently.
        if body.paramValues:
            if matched := _staged_values(a, body.paramValues):
                patch["paramValues"] = matched
        # §8/§19: staged concurrency lands with the save, like the PATCH.
        if body.concurrency is not None:
            patch.update(body.concurrency.model_dump(exclude_unset=True))
        if patch:
            store.patch_automation(a, patch)
        store.delete_draft(a)
        # §4.4 boundary marker: saving settles the draft — the thread stays,
        # split so the settled session never reaches a later chat's agent.
        store.append_chat_marker(a, f"Draft saved as v{n}." if minted
                                 else "Changes saved — no new version.")
    if triggers is not None:
        # §6: same rule as the PATCH — the saved version's trigger list may have
        # dropped or disabled the trigger some waiting entry came from.
        cancel_unmatched_queue(store, engine, automation_id)
    _publish_auto_changed(a)
    return {"version": n, "automation": _auto_json_locked(a)}


def _cancel_live_draft_work(container_id: str | None) -> list[str]:
    """§11/§19 draft settle: cancel the container's still-executing test and
    its still-building §8 drafting jobs — a settled draft never leaves a test
    or agent harness process running. The test record is marked under the lock
    so testexec._run deletes it when it lands (instead of it surviving the
    draft or rewriting the settled container's test.yaml); the cancels
    themselves run outside the lock — they kill processes. Returns the
    cancelled test execution ids so delete can wait on their threads."""
    with store.lock:
        live = [h for h in store.execs.values()
                if is_test(h) and h["automation_id"] == container_id
                and h["status"] == "executing"]
        for h in live:
            h["_draft_settled"] = True
    for h in live:
        engine.cancel(h["id"])
    draft_jobs.cancel_for(container_id)
    return [h["id"] for h in live]


def _reject_live_draft_exec(a: dict) -> None:
    """409 while a Draft-version execution runs: rewriting or pruning the
    draft's step scripts mid-run would make later steps execute code that no
    longer matches the recorded per-step sha (§7). Call under store.lock."""
    for eid in a.get("_live") or ():
        live = store.execs.get(eid)
        if live and live.get("kind") == "draft":
            raise HTTPException(409, "a draft execution is in progress")


# §19: the one draft-container surface — GET/PUT/DELETE /draft/{owner} +
# POST /draft/{owner}/open, where `owner` is an automation id (its draft/
# container) or the literal `pending` (the §4.4 create-mode slot <root>/draft/).
def _draft_owner(owner: str) -> dict | None:
    """None → the pending slot; an automation owner that doesn't resolve is 404."""
    if owner == "pending":
        return None
    return _auto_or_404(owner)


def _publish_draft_changed(a: dict | None) -> None:
    if a is None:
        hub.publish("draft.changed")  # §19 GET /state pendingDraft consumers
    else:
        _publish_auto_changed(a)


@app.get("/draft/{owner}", dependencies=[Depends(auth)])
def get_draft_container(owner: str) -> dict:
    a = _draft_owner(owner)
    out = store.draft_container_json(a)
    # §19 background continuation: the owner's building job or held outcome
    # rides the envelope (not inside `draft` — a first message still in flight
    # may have landed no draft at all), backing the §11 re-attach.
    if job := draft_jobs.job_for(a["id"] if a is not None else None):
        out["job"] = job
    return out


@app.post("/draft/{owner}/open", dependencies=[Depends(auth)])
def open_draft_container(owner: str) -> dict:
    store.open_draft(_draft_owner(owner))
    return {"ok": True}


@app.put("/draft/{owner}", dependencies=[Depends(auth)])
def put_draft_container(owner: str, body: models.DraftPut) -> dict:
    a = _draft_owner(owner)
    d = body.draft.plain()
    # §4.4: the draft snapshot carries the editor's grant selections and trigger
    # list as draft-only keys — never applied to the automation until saved.
    # Triggers pass through unvalidated — saving (Create / vN+1) normalizes them.
    ver = _draft_to_version(d)
    ver["step_agents"] = d.get("stepAgents")
    ver["allowed_secrets"] = d.get("allowedSecrets")
    # §4.2: the chat-staged value map rides the snapshot as a draft-only key.
    ver["param_values"] = d.get("paramValues")
    # §8: the chat-staged concurrency object rides the snapshot the same way.
    ver["concurrency"] = d.get("concurrency")
    # §8/§11: the drafted test-value map rides the snapshot as a draft-only key.
    ver["test_values"] = d.get("testValues")
    # §4.4/§11: the dirty-gate state rides the snapshot — stored only when set,
    # so a resumed out-of-sync draft keeps saving locked.
    ver["out_of_sync"] = bool(d.get("outOfSync")) or None
    with store.lock:
        if a is None:
            # §19: the pending payload also carries the identity fields no
            # automation record exists to hold (name, triggers; agentId beside).
            store.save_draft(None, ver, name=d.get("name"), agent_id=body.agentId,
                             triggers=d.get("triggers") or [])
        else:
            _reject_live_draft_exec(a)
            ver["triggers"] = d.get("triggers")
            store.save_draft(a, ver)
    _publish_draft_changed(a)
    return {"ok": True}


@app.delete("/draft/{owner}", dependencies=[Depends(auth)])
def delete_draft_container(owner: str) -> dict:
    a = _draft_owner(owner)
    # A refused settle must be side-effect free: check the 409 condition
    # before the cancels (the in-lock check below stays authoritative).
    if a is not None:
        with store.lock:
            _reject_live_draft_exec(a)
    _cancel_live_draft_work(a["id"] if a is not None else None)  # §11/§19: discard settles the draft
    with store.lock:
        if a is not None:
            _reject_live_draft_exec(a)
        store.delete_draft(a)
        # §4.4 thread lifetime: discarding settles the draft but keeps the
        # thread — behind the boundary marker, appended server-side so a
        # settled session never reaches a later chat's agent.
        store.append_chat_marker(a, "Draft discarded.")
    _publish_draft_changed(a)
    return {"ok": True}


# §19: the §11 chat-thread surface — GET/PUT /chat/{owner}, resolved like
# /draft/{owner}. The thread lives at the container root (§4.4 thread
# lifetime) and outlives the draft; the §4.4 boundary markers are appended by
# the settle endpoints above, never through this surface.
@app.get("/chat/{owner}", dependencies=[Depends(auth)])
def get_chat_thread(owner: str) -> dict:
    a = _draft_owner(owner)
    return {"chat": store.chat_json(store.chat_dir(a))}


@app.put("/chat/{owner}", dependencies=[Depends(auth)])
def put_chat_thread(owner: str, body: models.ChatPut) -> dict:
    store.save_chat(_draft_owner(owner), body.chat)
    return {"ok": True}


# ---------- transfer archives (§5.1) ----------
@app.get("/automations/{automation_id}/export", dependencies=[Depends(auth)])
def export_auto(automation_id: str, values: int = 1):
    a = _auto_or_404(automation_id)
    try:
        data = transfer.export_automation(store, a, include_values=bool(values))
    except transfer.TransferError as e:
        # §5.1: an export reference matching no stored record (a deleted secret
        # or agent, or a §4.1 unresolved import reference) answers 422 naming
        # the step or trigger — never a 500.
        raise HTTPException(422, str(e)) from e
    from urllib.parse import quote

    from fastapi.responses import Response

    fname = transfer.safe_filename(a["name"]) + ".autowright"
    ascii_name = fname.encode("ascii", "replace").decode() or "automation.autowright"
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition":
                             f'attachment; filename="{ascii_name}"; '
                             f"filename*=UTF-8''{quote(fname)}"})


async def _archive_body(request: Request) -> bytes:
    # §19: the archive is the raw request body — no multipart. Stream it in
    # with the transfer cap applied so an oversized upload can't balloon RAM
    # before import_automation ever sees it.
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > transfer.MAX_ARCHIVE_BYTES:
            raise HTTPException(413, "the archive is larger than the 64 MB import limit")
        chunks.append(chunk)
    return b"".join(chunks)


def _land_import(data: bytes) -> dict:
    # §5.1: import never writes the agents or secrets stores (it creates no
    # records), so the §19 unreadable-store guard doesn't apply here — the
    # only writes are the new automation and its param values.
    try:
        a, summary = transfer.import_automation(store, data)
    except transfer.TransferError as e:
        raise HTTPException(422, str(e)) from e
    _publish_auto_changed(a)
    if summary["packages"]:
        # §5.1: a successful import starts the §6.2 package ensure in the
        # background and republishes the automation when it finishes, so a
        # package-missing problem clears without a reload. The §20 CLI's own
        # foreground ensure is idempotent and serializes on the same pip lock.
        pkgs = [dict(p) for p in summary["packages"]]

        def ensure_imported() -> None:
            try:
                pkglib.ensure(pkgs)
            except Exception:  # noqa: BLE001 — §6.2: a failed install stays a problem entry
                log.exception("post-import package ensure failed")
            _publish_auto_changed(a)

        threading.Thread(target=ensure_imported, daemon=True).start()
    return {"automation": _auto_json_locked(a), "summary": summary}


@app.post("/automations/import", dependencies=[Depends(auth)])
async def import_auto(request: Request) -> dict:
    # Threadpool, not the loop: landing an archive decompresses up to 256 MB
    # and writes a whole version folder — inline it would stall every other
    # request and the §19 WebSocket for the duration.
    data = await _archive_body(request)
    return await run_in_threadpool(_land_import, data)


# §5.2 preview tokens: the validated archive is parked between the preview and
# confirm calls, so the user imports exactly the bytes reviewed. The bytes are
# spooled to a file under the §5 `import-spool/` dir — four 64 MB archives would
# pin a quarter gigabyte of RAM for the whole 15-minute TTL otherwise. Only the
# parked-at stamp and the spool path stay in memory.
_IMPORT_TTL = 15 * 60
_IMPORT_SLOTS = 4
_IMPORT_GONE = "the import preview expired — fetch it again"
_import_parked: dict[str, tuple[float, Path]] = {}
# Parked-state lock: previews run on the event loop while url/confirm run on
# the threadpool, so the sweep/evict/insert read-modify-write in _park_archive
# and the pops genuinely race without it.
_import_lock = threading.Lock()


def _drop_parked(token: str) -> None:
    """Forget one parked archive and delete its spool file. Caller holds
    _import_lock."""
    slot = _import_parked.pop(token, None)
    if slot is not None:
        slot[1].unlink(missing_ok=True)


def _clear_import_spool() -> None:
    """Startup sweep: a crashed process leaves spool files behind, and the
    in-memory tokens that addressed them died with it — nothing in there can
    ever be claimed again."""
    d = paths.import_spool_dir()
    if not d.exists():
        return
    try:
        stale = list(d.iterdir())
    except OSError as e:
        # An unlistable spool dir (bad permissions, a data path on a
        # disconnected volume) must never brick startup into a launchd crash
        # loop — the sweep is housekeeping, so skip it with a warning.
        log.warning("couldn't read the import spool dir %s (%s) — skipping the sweep", d, e)
        return
    for p in stale:
        try:
            p.unlink()
        except OSError:
            log.warning("couldn't remove stale import spool file %s", p)


def _park_archive(data: bytes) -> str:
    now = time.time()
    token = pysecrets.token_hex(16)
    d = paths.import_spool_dir()
    p = d / f"{token}.autowright"
    try:
        d.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    except OSError as e:
        p.unlink(missing_ok=True)  # a half-written spool file never survives
        raise HTTPException(
            507, f"couldn't hold the archive for review: {e.strerror or e}") from e
    with _import_lock:
        for k in [k for k, (t, _) in _import_parked.items() if now - t > _IMPORT_TTL]:
            _drop_parked(k)
        while len(_import_parked) >= _IMPORT_SLOTS:
            _drop_parked(min(_import_parked, key=lambda k: _import_parked[k][0]))
        _import_parked[token] = (now, p)
    return token


@app.post("/automations/import/preview", dependencies=[Depends(auth)])
async def import_preview(request: Request) -> dict:
    data = await _archive_body(request)

    def _preview() -> dict:
        try:
            preview = transfer.preview_archive(store, data)
        except transfer.TransferError as e:
            raise HTTPException(422, str(e)) from e
        return {"token": _park_archive(data), "preview": preview}

    # Threadpool, not the loop — same rule as the direct import above.
    return await run_in_threadpool(_preview)


@app.post("/automations/import/url", dependencies=[Depends(auth)])
def import_url(body: models.ImportUrl) -> dict:
    url = body.url.strip()
    if not url:
        raise HTTPException(422, "no URL given")
    try:
        data, resolved = transfer.fetch_archive(url)
        preview = transfer.preview_archive(store, data)
    except transfer.TransferError as e:
        raise HTTPException(422, str(e)) from e
    preview["sourceUrl"] = url
    preview["resolvedUrl"] = resolved
    return {"token": _park_archive(data), "preview": preview}


@app.post("/automations/import/confirm", dependencies=[Depends(auth)])
def import_confirm(body: models.ImportConfirm) -> dict:
    with _import_lock:
        slot = _import_parked.pop(body.token, None)
    if slot is None:
        raise HTTPException(404, _IMPORT_GONE)
    parked_at, path = slot
    try:
        if time.time() - parked_at > _IMPORT_TTL:
            raise HTTPException(404, _IMPORT_GONE)
        try:
            data = path.read_bytes()
        except OSError as e:
            # The spool file is gone (an outside cleanup, a failed write) — the
            # token can never land anything now, so it answers like an expired
            # one rather than 500ing on the read.
            raise HTTPException(404, _IMPORT_GONE) from e
    finally:
        # One-time either way: spent, expired, or unreadable, the file goes.
        path.unlink(missing_ok=True)
    return _land_import(data)


@app.post("/automations/{automation_id}/restore", dependencies=[Depends(auth)])
def restore(automation_id: str, body: models.VersionRestore) -> dict:
    a = _auto_or_404(automation_id)
    v = body.version
    if v not in a["versions"]:
        raise HTTPException(404, f"v{v} not found")
    n = store.restore_version(a, v)
    _publish_auto_changed(a)
    return {"version": n, "automation": _auto_json_locked(a)}


@app.delete("/automations/{automation_id}/versions/{version}", dependencies=[Depends(auth)])
def delete_version(automation_id: str, version: int) -> dict:
    """§4.4/§19 delete an old version. Guards under one lock span: never the
    current version (400), never one a live or queued execution records (409 —
    an admitted version execution must not lose its content before or mid-run)."""
    a = _auto_or_404(automation_id)
    with store.lock:
        if version == a["current_version"]:
            raise HTTPException(400, "the current version can't be deleted — restore another version first")
        if version not in a["versions"]:
            raise HTTPException(404, f"v{version} not found")
        if any(x["automation_id"] == a["id"] and x.get("kind") == "version"
               and x.get("version") == version and x["status"] in ("executing", "queued")
               for x in store.execs.values()):
            raise HTTPException(409, f"an execution is using v{version} — wait for it to finish")
        store.delete_version(a, version)
    _publish_auto_changed(a)
    return {"automation": _auto_json_locked(a)}


@app.post("/automations/{automation_id}/execute", dependencies=[Depends(auth)])
def execute_auto(automation_id: str, body: models.ExecuteBody | None = None) -> dict:
    a = _auto_or_404(automation_id)
    body = body or models.ExecuteBody()
    # §4.5/§19: the record stores the trigger's machine kind; manual starts are
    # `manual` (Execute now, CLI) or `menubar` (the tray panel) — the model
    # rejects anything else, and a non-string version, with a 422.
    # §6/§19 `queue: true`: the §9.2 popup's Queue action — at capacity the
    # start is admitted to the firing queue instead of refused.
    try:
        if body.queue:
            h, queued = queue_manual(store, engine, a, body.trigger,
                                     version_label=body.version)
        else:
            h, queued = engine.start(a, body.trigger, version_label=body.version), False
    except LookupError as e:  # unknown version label — not a liveness conflict
        raise HTTPException(404, str(e)) from e
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"executionId": h["id"], "queued": queued}


# §19: the app-start dedupe memory, bounded to the most recent _LAUNCH_MEMORY
# ids (a plain dict, used as an insertion-ordered set — oldest dropped first).
# A backend that outlives thousands of app launches must not grow this without
# limit; retries arrive seconds apart, so an id only falls out long after any
# retry carrying it could still be in flight.
_LAUNCH_MEMORY = 256
_served_launches: dict[str, None] = {}


@app.post("/app-started", dependencies=[Depends(auth)])
def app_started(body: models.AppStarted) -> dict:
    """§6 app-start firing: the Electron main process calls this once per app
    launch; every automation holding an enabled `app_start` trigger executes.
    Idempotent per `launchId` (required, §19 — the model rejects a missing or
    empty one): the caller retries until it gets a response, and a reply lost
    in flight must not fire everything a second time."""
    with store.lock:
        if body.launchId in _served_launches:
            return {"fired": 0}
        _served_launches[body.launchId] = None
        while len(_served_launches) > _LAUNCH_MEMORY:
            del _served_launches[next(iter(_served_launches))]
    with store.lock:
        autos = list(store.autos.values())
    fired = 0
    for a in autos:
        t = next((t for t in a["triggers"]
                  if t["kind"] == "app_start" and t["enabled"]), None)
        if not t:
            continue
        # One automation that can't start (a disk error creating its record)
        # must not 500 the batch: the rest would never fire, and the caller's
        # retry would re-fire the ones that already did.
        try:
            if fire_trigger(store, engine, a, t):
                fired += 1
        except Exception:  # noqa: BLE001
            log.exception("app-start firing failed for automation %s", a["id"])
    return {"fired": fired}


@app.get("/imessage/permissions", dependencies=[Depends(auth)])
def imessage_permissions() -> dict:
    """§19: the §9 permission checklist's status source. `fullDisk` probes
    chat.db right now (never prompts); `automation` is the remembered result
    of the backend's most recent Apple Events send to Messages."""
    return {"fullDisk": imessage.fda_status(),
            "automation": imessage.automation_status()}


@app.post("/imessage/permissions/automation-probe", dependencies=[Depends(auth)])
def imessage_automation_probe() -> dict:
    """§19: fire a benign Apple Event at Messages.app so macOS shows the
    Automation consent prompt; blocks until the user answers it."""
    return {"automation": imessage.automation_probe()}


# ---------- tests (§11 Test — §19 POST /tests) ----------
def _mock_payload(mock: dict) -> dict:
    """§19 `triggerMock` → the §4.5 payload stored on the test record. Fields
    the backend can't truthfully supply are null; `at` is the test start."""
    kind = mock.get("kind")
    text = mock.get("text")
    sender = mock.get("sender")
    if kind not in ("discord", "imessage"):
        raise HTTPException(422, "triggerMock kind must be discord | imessage")
    if not isinstance(text, str) or not text or not isinstance(sender, str) or not sender:
        raise HTTPException(422, "triggerMock needs nonempty text and sender")
    if kind == "imessage":
        return {"kind": "imessage", "text": text, "sender": sender,
                "chat": None, "messageId": None, "at": timefmt.now_iso()}
    channel = mock.get("channel")
    secret = mock.get("secret")
    if not isinstance(channel, str) or not channel.isascii() or not channel.isdigit():
        raise HTTPException(422, "triggerMock channel must be an ASCII-digit string")
    if not isinstance(secret, str) or not triggerlib.SECRET_ID_RE.match(secret):
        raise HTTPException(422, "triggerMock secret must be a secret id (uuid)")
    return {"kind": "discord", "text": text, "sender": sender, "channel": channel,
            "channelName": None, "guildName": None, "messageId": None,
            "guildId": None, "secret": secret, "at": timefmt.now_iso()}


@app.post("/tests", dependencies=[Depends(auth)])
def post_test(body: models.TestStart) -> dict:
    d = body.draft.plain()
    if not d.get("steps"):
        raise HTTPException(422, "draft with steps required")
    payload = _mock_payload(body.triggerMock) if body.triggerMock else None
    auto = None
    if body.automationId:
        # A stale/unknown automationId must 404 — falling through to create mode
        # would delete the unrelated pending slot's test record.
        auto = _auto_or_404(body.automationId)
    # §19: grant arrays as in /drafts — create mode (no automationId) defaults to
    # ALL agents/secrets when the arrays are absent, edit mode to the
    # automation's grants.
    enabled = body.enabledAgents
    if enabled is None:
        enabled = auto["enabled_agents"] if auto else [g["id"] for g in store.agents]
    allowed = body.allowedSecrets
    if allowed is None:
        allowed = auto["allowed_secrets"] if auto else [s["id"] for s in store.secrets]
    try:
        execution_id = testexec.start(engine, d, auto, enabled, allowed,
                                 body.paramValues or {}, trigger_payload=payload,
                                 steps_fingerprint=body.stepsFingerprint)
    except RuntimeError as e:  # §19: one live test per draft container
        raise HTTPException(409, str(e)) from e
    return {"executionId": execution_id}


# ---------- declared packages (§6.2 — §19 /packages/*) ----------
@app.post("/packages/check", dependencies=[Depends(auth)])
def packages_check(body: models.PackagesBody) -> dict:
    return {"packages": pkglib.check([p.plain() for p in body.packages])}


@app.post("/packages/install", dependencies=[Depends(auth)])
def packages_install(body: models.PackagesBody) -> dict:
    # Blocking §6.2 ensure — FastAPI runs sync endpoints on a worker thread,
    # and the module lock serializes concurrent pip runs.
    return {"packages": pkglib.ensure([p.plain() for p in body.packages])}


@app.post("/packages/outdated", dependencies=[Depends(auth)])
def packages_outdated(body: models.PackagesBody) -> dict:
    # §6.2 update check — read-only PyPI lookups; failures just omit `latest`.
    return {"packages": pkglib.outdated([p.plain() for p in body.packages])}


@app.post("/packages/update", dependencies=[Depends(auth)])
def packages_update(body: models.PackagesBody) -> dict:
    """§6.2 update: `pip install --upgrade` in the shared directory — no
    manifest writes; manifests carry no version. Blocking like /install."""
    entries = [p.plain() for p in body.packages]
    for e in entries:
        if not pkglib.PIP_NAME_RE.match(e["pip"].strip()):
            raise HTTPException(422, f"not a bare distribution name: {e['pip']!r}")
    return {"packages": pkglib.upgrade(entries)}


@app.get("/automations/{automation_id}/memory/files", dependencies=[Depends(auth)])
def list_memory_files(automation_id: str) -> dict:
    # §19 read-only memory listing (backs §20 `automation memory show`).
    # No live-execution 409 and no lock: §6 atomic commit means a read
    # never sees a partial file.
    a = _auto_or_404(automation_id)
    return {"files": store.memory_files(a)}


@app.get("/automations/{automation_id}/memory/files/{name:path}", dependencies=[Depends(auth)])
def get_memory_file(automation_id: str, name: str) -> dict:
    # §19 one memory file's content — same lock-free read rule as the list.
    a = _auto_or_404(automation_id)
    p = store.memory_file_path(a, name)
    if p is None:
        raise HTTPException(422, f"not a memory-relative file path: {name!r}")
    if not p.is_file():
        raise HTTPException(404, "no such memory file")
    try:
        data = p.read_bytes()
    except OSError as e:
        # A step deleted it between the check and the read — 404, not 500.
        raise HTTPException(404, "no such memory file") from e
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(422, "binary file — open it from the memory directory on disk "
                                 f"instead: {store.memory_stats(a)['path']}")
    return {"name": name, "size": len(data), "text": text}


@app.post("/automations/{automation_id}/memory/clear", dependencies=[Depends(auth)])
def clear_memory(automation_id: str) -> dict:
    # §9.2 MEMORY card: "Clear memory" — next execution starts fresh.
    a = _auto_or_404(automation_id)
    # §19: same guard as manual snapshot and restore — a mid-execution clear
    # could delete files a step is reading right now. The snapshot copy
    # stages OUTSIDE store.lock (stage_snapshot's rule: a memory dir can be
    # gigabytes and every request queues behind the lock); the commit
    # re-checks `_live` so a copy that raced an execution never lands.
    with store.memory_ops:
        with store.lock:
            if a.get("_live"):
                raise HTTPException(409, "an execution is in progress")
        staged = store.stage_snapshot(a, "pre-clear")  # §6.3 — None when memory is empty or the toggle is off
        with store.lock:
            if a.get("_live"):
                if staged is not None:
                    store.discard_snapshot(staged[0])
                raise HTTPException(409, "an execution is in progress")
            if staged is not None:
                store.commit_snapshot(a, staged, "pre-clear")
            store.clear_memory(a)
    _publish_auto_changed(a)
    return {"ok": True}


@app.post("/automations/{automation_id}/memory/snapshots", dependencies=[Depends(auth)])
def create_snapshot(automation_id: str, body: models.SnapshotCreate | None = None) -> dict:
    # §6.3 manual snapshot — 409 while live, 422 when memory is empty. The
    # copy stages OUTSIDE store.lock (a memory dir can be gigabytes); the
    # commit re-checks `_live`, so a copy that raced a trigger into
    # half-written memory is discarded, never committed.
    a = _auto_or_404(automation_id)
    with store.memory_ops:
        with store.lock:
            if a.get("_live"):
                raise HTTPException(409, "an execution is in progress")
        staged = store.stage_snapshot(a, "manual")
        if staged is None:
            raise HTTPException(422, "memory is empty")
        with store.lock:
            if a.get("_live"):
                store.discard_snapshot(staged[0])
                raise HTTPException(409, "an execution is in progress")
            meta = store.commit_snapshot(a, staged, "manual",
                                         name=((body.name if body else None) or "").strip() or None)
    if meta is None:
        raise HTTPException(422, "memory is empty")
    _publish_auto_changed(a)
    return {"snapshot": store.snapshot_json(meta)}


@app.patch("/automations/{automation_id}/memory/snapshots/{snapshot_id}", dependencies=[Depends(auth)])
def rename_snapshot(automation_id: str, snapshot_id: str, body: models.SnapshotRename | None = None) -> dict:
    a = _auto_or_404(automation_id)
    meta = store.rename_snapshot(a, snapshot_id, body.name if body else None)
    if meta is None:
        raise HTTPException(404, "snapshot not found")
    _publish_auto_changed(a)
    return {"snapshot": store.snapshot_json(meta)}


@app.post("/automations/{automation_id}/memory/snapshots/{snapshot_id}/restore", dependencies=[Depends(auth)])
def restore_snapshot(automation_id: str, snapshot_id: str) -> dict:
    a = _auto_or_404(automation_id)
    # Same guard as create_snapshot; the copies stage outside store.lock and
    # restore_snapshot itself re-checks `_live` at its commit.
    with store.memory_ops:
        with store.lock:
            if a.get("_live"):
                raise HTTPException(409, "an execution is in progress")
        try:
            if store.restore_snapshot(a, snapshot_id) is None:
                raise HTTPException(404, "snapshot not found")
        except LiveExecutionError:
            raise HTTPException(409, "an execution is in progress") from None
    _publish_auto_changed(a)
    return {"ok": True}


@app.delete("/automations/{automation_id}/memory/snapshots/{snapshot_id}", dependencies=[Depends(auth)])
def delete_snapshot(automation_id: str, snapshot_id: str) -> dict:
    a = _auto_or_404(automation_id)
    if not store.delete_snapshot(a, snapshot_id):
        raise HTTPException(404, "snapshot not found")
    _publish_auto_changed(a)
    return {"ok": True}


# ---------- drafts ----------
@app.post("/drafts", dependencies=[Depends(auth)])
def post_draft(body: models.DraftJobStart) -> dict:
    mode = body.mode
    if mode == "chat" and not (body.text or "").strip():
        raise HTTPException(422, "chat mode needs a nonempty text")
    agent = _agent_or_404(body.agentId or store.default_agent_id
                          or (store.agents[0]["id"] if store.agents else ""))
    # §19: an automationId that doesn't resolve answers 404 (like the
    # stale-automationId 404 on /tests) — never a silent fall-back to the
    # no-automation grant defaults below.
    auto = _auto_or_404(body.automationId) if body.automationId else None
    current = body.current.plain() if body.current is not None else None
    if auto and current is None:
        current = auto["versions"][auto["current_version"]]
    if auto and (current or {}).get("triggers") is None:
        # §8: triggers are unversioned top-level state — attach the stored list
        # so the steps call's CURRENT-triggers reference has it (the editor's
        # `current.triggers` wins when the body carries one). Checked for None,
        # not key presence: a version-folder-seeded `current` always carries
        # the key with a None value (§5 load model).
        current = dict(current or {})
        current["triggers"] = auto["triggers"]
    if auto and auto.get("unresolved_references"):
        # §8: the §5.1 import's no-match map rides every call shape as the
        # IMPORTED REFERENCES THAT NEED FIXING section, so the agent knows
        # what the archive wanted and can rebind or drop the references.
        current = dict(current or {})
        current["unresolved_references"] = auto["unresolved_references"]
    if auto and mode == "chat":
        # §8 AUTOMATION section: name/description are §4.1 top-level identity, not
        # versioned content — attach the stored values when the body's
        # `current` carries none (the editor's win).
        current = dict(current or {})
        current.setdefault("name", auto["name"])
        current.setdefault("description", auto.get("description", ""))
        # §8 CURRENT concurrency: top-level operational state like triggers —
        # the editor's staged object (possibly partial) merges over the stored
        # values, so the prompt always shows the effective pair.
        current["concurrency"] = {"maxParallel": auto["max_parallel"],
                                  "maxQueued": auto["max_queued"],
                                  **(current.get("concurrency") or {})}
    # §19: an explicit `spec` in the body wins — sync/edit regenerate against the
    # PROVIDED spec (§8), e.g. the in-editor draft, not the stored version's spec.
    if body.spec is not None:
        current = dict(current or {})
        current["spec"] = body.spec
    # (§4.1 spelling boundary: the request model already normalized `current`'s
    # camelCase step flags to snake_case.)
    if not body.automationId and not (current or {}).get("instructions"):
        # §8: with no automation, drafting falls back to the default
        # best-practice build instructions — belt-and-braces; the editor
        # normally seeds and sends them with the fresh draft.
        current = dict(current or {})
        current["instructions"] = drafting.default_instructions()
    # §8/§19: in-editor grant arrays in the body win over the stored automation's —
    # the editor's live toggles are the truth while a draft is being worked on.
    enabled_ids = body.enabledAgents
    if enabled_ids is None:
        # §19: with an automation, fall back to the stored grants; without one
        # (a fresh create-flow draft), every configured agent — the same
        # all-enabled seed the Review page starts from.
        enabled_ids = auto["enabled_agents"] if auto else [a["id"] for a in store.agents]
    allowed = body.allowedSecrets
    if allowed is None:
        # no automation defaults to every stored secret — the same all-on seed
        # the Review page's secrets card starts from
        allowed = auto["allowed_secrets"] if auto else [s["id"] for s in store.secrets]
    grants = {
        "agents": [_agent_grant(g) for g in store.agents if g["id"] in enabled_ids],
        # a dangling allowed id grants nothing (the secret was deleted)
        "secrets": [e for e in (_secret_grant(secret_id) for secret_id in allowed) if e],
    }
    executions = pkg_state = None
    if mode == "chat":
        # §8/§19: the backend assembles the RECENT EXECUTIONS and PACKAGES context —
        # the editor never sends run output. `executionId` (the §11 Fix-with-AI
        # entry) forces that execution into the section in full detail.
        executions = testexec.executions_context(auto, (current or {}).get("steps") or [],
                                                 body.executionId)
        if pkgs := (current or {}).get("packages"):
            pkg_state = pkglib.check([{"pip": p.get("pip"), "import": p.get("import")}
                                      for p in pkgs])
    # §19: the owner stamp — the automation's draft container, or the pending
    # slot when no automationId was sent — lets the draft-settle endpoints
    # cancel this job when the draft settles.
    job_id = draft_jobs.start(mode, agent, body.text, current, grants,
                              chat_history=body.chat, executions=executions,
                              pkg_state=pkg_state,
                              owner_id=auto["id"] if auto else None)
    return {"jobId": job_id}


@app.get("/drafts/{job_id}", dependencies=[Depends(auth)])
def get_draft(job_id: str) -> dict:
    j = draft_jobs.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return j


@app.delete("/drafts/{job_id}", dependencies=[Depends(auth)])
def cancel_draft(job_id: str) -> dict:
    return {"ok": draft_jobs.cancel(job_id)}


@app.post("/drafts/{job_id}/ack", dependencies=[Depends(auth)])
def ack_draft(job_id: str) -> dict:
    """§19 background continuation: the §11 editor consumed a settled job's
    outcome (applied + persisted) — drop the held record."""
    result = draft_jobs.ack(job_id)
    if result == "missing":
        raise HTTPException(404, "job not found")
    if result == "building":
        raise HTTPException(409, "job is still building — only settled jobs are consumable")
    return {"ok": True}

# ---------- executions ----------
# §4.6 execution statuses, plus §19's `finished` group value (any terminal one).
EXECUTION_STATUSES = ("queued", "executing", "succeeded", "failed",
                      "cancelled", "skipped", "interrupted")
LIVE_STATUSES = ("queued", "executing")
EXECUTIONS_PAGE_LIMIT = 50  # §7: the /state finished window and the page size


@app.get("/executions", dependencies=[Depends(auth)])
def list_execs(automation: str | None = None, status: str | None = None,
               limit: int | None = Query(None, ge=1),
               before_started_ms: int | None = Query(None, alias="beforeStartedMs"),
               before_id: str | None = Query(None, alias="beforeId")) -> dict:
    """§19 executions query: headers in the §7 canonical order (startedMs desc,
    id asc on ties), status filter over the §4.6 vocabulary + `finished`, and
    the keyset cursor for paging. `total` counts every match, not the page;
    `limit` omitted means every match (§20 reference resolution reads that)."""
    if status is not None and status != "finished" and status not in EXECUTION_STATUSES:
        raise HTTPException(422, "unknown status — one of: "
                            + ", ".join(EXECUTION_STATUSES) + ", finished")
    if (before_started_ms is None) != (not before_id):
        # An empty beforeId would degrade the keyset to a bare timestamp
        # filter (every id compares > ""), duplicating tie rows across pages —
        # a half cursor answers 422 whichever half is missing (§19).
        raise HTTPException(422, "beforeStartedMs and beforeId select the cursor "
                                 "position together — one without the other is ambiguous")
    with store.lock:
        hs = list(store.execs.values())
        if automation:
            hs = [h for h in hs if h["automation_id"] == automation]
        if status == "finished":
            hs = [h for h in hs if h["status"] not in LIVE_STATUSES]
        elif status:
            hs = [h for h in hs if h["status"] == status]
        # Sort headers on the shared canonical key and serialize only the page
        # actually returned (exec_json for every match on every keyset fetch
        # is the §7 unbounded-history cost the paging exists to avoid).
        keyed = sorted(((exec_started_ms(h), h) for h in hs),
                       key=lambda p: (-p[0], p[1]["id"]))
        total = len(keyed)
        if before_started_ms is not None:
            # Strictly after the cursor position in sort order — stable while
            # new executions land above the page.
            keyed = [(ms, h) for ms, h in keyed
                     if ms < before_started_ms
                     or (ms == before_started_ms and h["id"] > before_id)]
        return {"executions": [store.exec_json(h) for _, h in keyed[:limit]],
                "total": total}


@app.get("/executions/{execution_id}", dependencies=[Depends(auth)])
def get_exec(execution_id: str) -> dict:
    with store.lock:
        h = store.exec_full(execution_id)
        if not h:
            raise HTTPException(404, "execution not found")
        return store.exec_json(h, full=True)


@app.get("/executions/{execution_id}/logs", dependencies=[Depends(auth)])
def get_exec_logs(execution_id: str, step: int | None = None, attempt: int | None = None,
                  tail: int | None = Query(None, ge=1)) -> dict:
    """§19: lazy per-step-attempt log — no params selects the execution log.
    `tail` keeps only the last N lines of the selected log (same shape)."""
    if execution_id not in store.execs:
        raise HTTPException(404, "execution not found")
    if (step is None) != (attempt is None):
        # §19: the pair travels together — half a selector would silently
        # resolve to attempt 1 and read as the wrong file.
        raise HTTPException(422, "step and attempt go together — send both or neither")
    lines = store.read_log(execution_id, step, attempt, tail=tail)
    return {"lines": [{"time": l.get("time", ""), "kind": l.get("kind", "out"),
                       "sequence": l.get("sequence", 0), "text": l.get("text", "")} for l in lines]}


@app.get("/executions/{execution_id}/result/{name}", dependencies=[Depends(auth)])
def get_result_file(execution_id: str, name: str):
    """§4.5: raw result-dir file (result.md, result.html, images) for the §7 file views."""
    if execution_id not in store.execs:
        raise HTTPException(404, "execution not found")
    d = (store.exec_dir(execution_id) / "result").resolve()
    f = (d / name).resolve()
    if f.parent != d or not f.is_file():
        raise HTTPException(404, "file not found")
    from fastapi.responses import FileResponse

    return FileResponse(f)


@app.post("/executions/{execution_id}/cancel", dependencies=[Depends(auth)])
def cancel_exec(execution_id: str) -> dict:
    return {"ok": engine.cancel(execution_id)}


@app.post("/executions/{execution_id}/retry", dependencies=[Depends(auth)])
def retry_exec(execution_id: str) -> dict:
    h = store.execs.get(execution_id)
    if not h:
        raise HTTPException(404, "execution not found")
    if h["automation_id"] is None:
        # §4.5 create-mode test records have no automation to resolve — retry
        # answers the §19 test rule's 409 (the draft may have changed), not a
        # 404 for a record that plainly exists.
        raise HTTPException(409, "the draft may have changed — execute a new test from the editor")
    a = _auto_or_404(h["automation_id"])
    try:
        h2 = engine.retry(a, h)
    except LookupError as e:
        # §19: a deleted version (or record) no longer resolves — 404, like
        # execute_auto's unknown-version mapping; not a liveness conflict.
        raise HTTPException(404, str(e)) from e
    except RuntimeError as e:
        # §7: retry answers 409 while live or when a re-saved draft's steps
        # drifted from the record.
        raise HTTPException(409, str(e)) from e
    return {"executionId": h2["id"]}


@app.post("/executions/{execution_id}/skip-step", dependencies=[Depends(auth)])
def skip_step(execution_id: str, body: models.SkipStep) -> dict:
    if execution_id not in store.execs:
        raise HTTPException(404, "execution not found")
    if not engine.skip_step(execution_id, body.index):
        raise HTTPException(409, "that step isn't executing right now")
    return {"ok": True}


# ---------- agents ----------
HARNESSES = ("Claude Code", "Gemini CLI", "Codex", "OpenCode")


@app.get("/agents", dependencies=[Depends(auth)])
def list_agents() -> list[dict]:
    with store.lock:
        return _agents_json()


@app.post("/agents", dependencies=[Depends(auth)])
def add_agent(body: models.AgentAdd) -> dict:
    # §19 unreadable-store guard, before any state changes (here and in every
    # agents/secrets/settings write below): in-memory state must never hold
    # what disk refused.
    store.require_writable(paths.agents_file())
    harness_name = body.harness
    if harness_name not in HARNESSES:
        raise HTTPException(422, "unknown harness")
    mode = body.mode
    # §4.7: mode ollama is a harness driving a local Ollama model — valid with
    # Claude Code, Codex, and OpenCode, never Gemini CLI; mode custom
    # is a user-typed model string valid with every harness; model is null
    # only in default mode — a null model means the harness uses whatever it
    # is already configured with.
    if mode == "ollama" and harness_name not in harness.LOCAL_MODEL_HARNESSES:
        raise HTTPException(422, "local-model mode needs Claude Code, Codex, or OpenCode")
    model = (body.model or None) if mode != "default" else None
    if mode == "ollama" and not model:
        raise HTTPException(422, "local-model mode needs a model")
    if mode == "custom" and not model:
        raise HTTPException(422, "custom-model mode needs a model")
    import uuid

    with store.lock:
        ag = {"id": str(uuid.uuid4()), "name": _clean_name(body.name), "description": body.description or "",
              "harness": harness_name, "mode": mode, "model": model}
        _check_grant_name_free(ag)
        store.agents.append(ag)
        if store.default_agent_id is None:
            store.default_agent_id = ag["id"]  # §4.7: the first agent is the default
        store.save_agents()
    hub.publish("agents.changed")
    return {**ag, "default": ag["id"] == store.default_agent_id}


@app.patch("/agents/{agent_id}", dependencies=[Depends(auth)])
def patch_agent(agent_id: str, patch: models.AgentPatch) -> dict:
    store.require_writable(paths.agents_file())
    # Same validation as POST — a PATCH must not be able to create an agent
    # shape POST rejects (e.g. mode ollama with no model, §4.7).
    body = patch.model_dump(exclude_unset=True)
    if "name" in body:
        body["name"] = _clean_name(body["name"])  # §4.7: trimmed; whitespace-only unnames
    if "harness" in body and body["harness"] not in HARNESSES:
        raise HTTPException(422, "unknown harness")
    with store.lock:
        ag = _agent_or_404(agent_id)
        mode = body.get("mode", ag.get("mode", "default"))
        harness_name = body.get("harness", ag.get("harness"))
        if mode == "ollama" and harness_name not in harness.LOCAL_MODEL_HARNESSES:
            raise HTTPException(422, "local-model mode needs Claude Code, Codex, or OpenCode")
        model = body["model"] if "model" in body else ag.get("model")
        if mode == "ollama" and not model:
            raise HTTPException(422, "local-model mode needs a model")
        if mode == "custom" and not model:
            raise HTTPException(422, "custom-model mode needs a model")
        # §4.7 uniqueness — checked only when the merged result would CHANGE
        # the effective grant name (name, else harness): unrelated-field
        # patches of pre-existing on-disk duplicates keep working.
        merged = {**ag, **{k: body[k] for k in ("name", "harness") if k in body}}
        if harness.grant_name(merged).lower() != harness.grant_name(ag).lower():
            _check_grant_name_free(merged, exclude_id=agent_id)
        # After the last 422 above — a refused patch must not have already
        # flipped the in-memory default pointer (unsaved, unpublished, and
        # silently divergent from what clients show).
        if body.get("default"):
            store.default_agent_id = agent_id  # §4.7: single pointer
        if "harness" in body:
            ag["harness"] = body["harness"]
        for k in ("name", "model", "mode", "description"):
            if k in body:
                ag[k] = body[k]
        if ag.get("mode", "default") == "default":
            ag["model"] = None
        store.save_agents()
    hub.publish("agents.changed")
    return {**ag, "default": ag["id"] == store.default_agent_id}


@app.delete("/agents/{agent_id}", dependencies=[Depends(auth)])
def delete_agent(agent_id: str) -> dict:
    store.require_writable(paths.agents_file())
    with store.lock:
        ag = _agent_or_404(agent_id)
        store.agents = [g for g in store.agents if g["id"] != agent_id]
        # §4.7: repoint the default
        if store.default_agent_id == agent_id:
            store.default_agent_id = store.agents[0]["id"] if store.agents else None
        for a in store.autos.values():
            changed = False
            if a["agent_id"] == agent_id:
                a["agent_id"] = store.default_agent_id
                changed = True
            if agent_id in a["enabled_agents"]:
                a["enabled_agents"] = [x for x in a["enabled_agents"] if x != agent_id]
                changed = True
            if changed:
                store.patch_automation(a, {})
        store.save_agents()
    hub.publish("agents.changed")
    hub.publish("automation.changed")
    return {"ok": True}


@app.post("/agents/{agent_id}/check", dependencies=[Depends(auth)])
def check_agent(agent_id: str) -> dict:
    ag = _agent_or_404(agent_id)
    return {"status": "ready" if harness.check_ready(ag["harness"], ag.get("model"),
                                                     ag.get("mode", "default"))
            else "needs-setup"}


@app.get("/agents/detect", dependencies=[Depends(auth)])
def detect_agents() -> list[dict]:
    return harness.detect()


def _provider_or_422(provider_id: str | None) -> str:
    if provider_id not in harness.PROVIDER_NAME:
        raise HTTPException(422, "unknown provider")
    return provider_id


@app.post("/agents/check-harness", dependencies=[Depends(auth)])
def check_harness(body: models.CheckHarness) -> dict:
    """§19: the §4.7 readiness check before an agent record exists (§10)."""
    if body.harness not in harness.HARNESS_ID:
        raise HTTPException(422, "unknown harness")
    return {"status": "ready" if harness.check_ready(body.harness, body.model, body.mode)
            else "needs-setup"}


@app.get("/agents/signin/{provider_id}", dependencies=[Depends(auth)])
def agents_signin(provider_id: str) -> dict:
    return harness.signin_state(_provider_or_422(provider_id))


@app.post("/agents/install", dependencies=[Depends(auth)])
def agents_install(body: models.ProviderId) -> dict:
    pid = _provider_or_422(body.id)
    # §19: the install channels are macOS-shaped — where the §2 agentInstall
    # capability is false the endpoint degrades to a plain "by hand" line.
    if not platform.current().capabilities.agent_install:
        raise HTTPException(409, f"Installing agents from Autowright isn't supported on "
                                 f"{paths.os_display_name(paths.current_os())} yet — "
                                 f"install {harness.PROVIDER_NAME[pid]} by hand.")

    def publish(**kw) -> None:
        hub.publish("harness.install", id=pid,
                    **{k: v for k, v in kw.items() if v is not None})

    if not installer.start(pid, publish):
        raise HTTPException(409, "an install for this provider is already running")
    return {"ok": True}


@app.get("/agents/install/{provider_id}", dependencies=[Depends(auth)])
def agents_install_status(provider_id: str) -> dict:
    return installer.status(_provider_or_422(provider_id))


@app.post("/agents/login", dependencies=[Depends(auth)])
def agents_login(body: models.ProviderId) -> dict:
    """§19 sign-in help — only when the provider needs it."""
    pid = _provider_or_422(body.id)
    if pid == "ollama":
        raise HTTPException(409, "Ollama needs no sign-in")
    # §19: the Terminal sign-in flow is macOS-shaped — where the §2
    # agentInstall capability is false the endpoint degrades to a plain line.
    # It precedes the installed/signed-in checks, whose copy names the §9
    # machine noun.
    if not platform.current().capabilities.agent_install:
        raise HTTPException(409, f"Sign-in help isn't supported on "
                                 f"{paths.os_display_name(paths.current_os())} yet — "
                                 f"run {harness.PROVIDER_NAME[pid]}'s sign-in from a terminal.")
    st = harness.signin_state(pid)
    if not st["installed"]:
        raise HTTPException(409, f"{harness.PROVIDER_NAME[pid]} isn't installed "
                                 f"on this {paths.machine_noun()}")
    if st["signedIn"] is True:
        raise HTTPException(409, "already signed in")
    try:
        method = installer.login(pid)
    except RuntimeError as e:
        raise HTTPException(409, str(e)) from e
    return {"ok": True, "method": method}


@app.get("/ollama/status", dependencies=[Depends(auth)])
def ollama_status() -> dict:
    return harness.ollama_status()


_PULL_UNITS = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}


class _PullProgress:
    """One overall percent for an `ollama pull` (§19).

    Raw `ollama pull` output restarts its own bar per layer (one multi-GB blob
    plus small metadata layers), so a naive per-line percent flashes back to 0
    over and over. This byte-weights every layer seen so far and never goes
    backwards; lines with a bare `N%` but no byte counts fall back to that
    number under the same monotonic clamp.
    """

    def __init__(self) -> None:
        self._layers: dict[str, tuple[float, float]] = {}  # layer id → (done, total) bytes
        self.percent: int | None = None

    def update(self, line: str) -> int | None:
        layer = re.match(r"pulling ([0-9a-f]{4,})", line)
        counts = re.search(r"([\d.]+)\s*(B|KB|MB|GB|TB)\s*/\s*([\d.]+)\s*(B|KB|MB|GB|TB)", line)
        pct: int | None = None
        if layer and counts:
            self._layers[layer.group(1)] = (
                float(counts.group(1)) * _PULL_UNITS[counts.group(2)],
                float(counts.group(3)) * _PULL_UNITS[counts.group(4)])
            total = sum(t for _, t in self._layers.values())
            if total:
                pct = int(sum(d for d, _ in self._layers.values()) * 100 / total)
        else:
            bare = re.search(r"(\d{1,3})%", line)
            if bare:
                pct = int(bare.group(1))
        if pct is not None:
            self.percent = max(self.percent or 0, min(pct, 100))
        return self.percent

    def update_layer(self, digest: str | None, completed: float | None,
                     total: float | None) -> int | None:
        """Structured variant for the server's `/api/pull` stream (§19)."""
        if digest and total:
            self._layers[digest] = (float(completed or 0), float(total))
            grand = sum(t for _, t in self._layers.values())
            if grand:
                pct = int(sum(d for d, _ in self._layers.values()) * 100 / grand)
                self.percent = max(self.percent or 0, min(pct, 100))
        return self.percent


def _ollama_pull_http(model: str) -> None:
    """§19: pull through the server's `/api/pull` stream — no CLI involved."""
    prog = _PullProgress()
    ok = False
    err = ""
    try:
        req = urllib.request.Request(
            f"{harness.OLLAMA_URL}/api/pull",
            data=json.dumps({"model": model}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            for raw in r:
                try:
                    msg = json.loads(raw.decode())
                except ValueError:
                    continue
                if msg.get("error"):
                    err = str(msg["error"])
                    break
                status = str(msg.get("status") or "")
                pct = prog.update_layer(msg.get("digest"), msg.get("completed"),
                                        msg.get("total"))
                if status == "success":
                    ok = True
                extra = {} if pct is None else {"percent": pct}
                hub.publish("ollama.pull", model=model, line=status, done=False, **extra)
    except Exception as e:  # noqa: BLE001
        err = f"pull failed: {e}"
    hub.publish("ollama.pull", model=model, line="" if ok else err, done=True, ok=ok,
                **({"percent": 100} if ok else {}))


def _ollama_pull_cli(model: str) -> None:
    """CLI fallback for when the server isn't answering (§19)."""
    try:
        binpath = harness.ollama_bin()
        if not binpath:
            raise FileNotFoundError(binpath)
        proc = subprocess.Popen([binpath, "pull", model], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                # §2 pipe-encoding contract
                                encoding="utf-8", errors="replace",
                                env=harness.spawn_env(binpath),
                                # §2 spawn policy (hidden console on Windows)
                                **platform.current().processes.session_kwargs())
        prog = _PullProgress()
        for line in proc.stdout:  # type: ignore[union-attr]
            stripped = line.strip()
            pct = prog.update(stripped)
            extra = {} if pct is None else {"percent": pct}
            hub.publish("ollama.pull", model=model, line=stripped, done=False, **extra)
        proc.wait()
        ok = proc.returncode == 0
        hub.publish("ollama.pull", model=model, line="", done=True, ok=ok,
                    **({"percent": 100} if ok else {}))
    except FileNotFoundError:
        hub.publish("ollama.pull", model=model, line="Ollama isn't running", done=True, ok=False)


@app.post("/ollama/pull", dependencies=[Depends(auth)])
def ollama_pull(body: models.OllamaPull) -> dict:
    model = body.model
    if not model:
        raise HTTPException(422, "model required")
    # Never let a model name parse as an option to `ollama pull`.
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", model):
        raise HTTPException(422, "invalid model name")

    def pull() -> None:
        # §19: /ollama/status reads installed/active from the server answering,
        # so the pull must work in exactly that state — ride the server's own
        # API and never require a resolvable CLI binary alongside it.
        if harness._ollama_models() is not None:
            _ollama_pull_http(model)
        else:
            _ollama_pull_cli(model)
        hub.publish("agents.changed")

    threading.Thread(target=pull, daemon=True).start()
    return {"ok": True}


# ---------- secrets ----------
@app.get("/secrets", dependencies=[Depends(auth)])
def list_secrets() -> list[dict]:
    return _secrets_json()


def _secret_entity(s: dict) -> dict:
    """§19: the entity shape (a GET /secrets entry) the write routes return,
    so a creating client learns the minted id without a second fetch."""
    with store.lock:
        return {"id": s["id"], "name": s["name"],
                "description": s.get("description") or "",
                "set": bool(s.get("set", True)),
                "usedBy": store.secret_used_by(s["id"])}


def _secret_store_rejected(e: Exception) -> str:
    """§19 secret-write 503 detail, in the §9 per-OS copy rule's wording: the
    store's own name ("Keychain" / "Credential Manager" / "system keyring"),
    and the per-OS remedy clause — unlocking has no Windows analogue ("— try
    again" there), while macOS names the login Keychain and Linux the keyring
    (the Secret Service store does lock)."""
    remedy = {"windows": "try again",
              "linux": "unlock your keyring and try again"}.get(
        paths.current_os(), "unlock the login Keychain and try again")
    return f"your {paths.secret_store_name()} didn't accept the value ({e}) — {remedy}"


@app.post("/secrets", dependencies=[Depends(auth)])
def create_secret(body: models.SecretCreate) -> dict:
    store.require_writable(paths.secrets_file())  # before the Keychain write
    name = body.name
    if not SECRET_NAME_RE.match(name):
        raise HTTPException(422, "secret names must match [A-Z][A-Z0-9_]* — "
                                 "uppercase letters, digits and underscores, starting with a letter")
    with store.lock:
        # §4.8 uniqueness — enforced at create; the name is immutable after.
        if any(s["name"] == name for s in store.secrets):
            raise HTTPException(422, f"a secret named {name} already exists")
    secret_id = new_id()  # §4.8: the minted id is the reference identity — it also keys the Keychain
    if body.value:
        # Keychain IPC can block for seconds (locked keychain, consent prompt) —
        # never hold store.lock across it; the engine would stall mid-execution.
        try:
            keychain.set_secret(secret_id, body.value)
        except Exception as e:  # noqa: BLE001 — keyring's error zoo is open-ended
            # A locked keychain or a denied consent prompt is a routine macOS
            # condition, not a server bug: clean 503, nothing stored.
            raise HTTPException(503, _secret_store_rejected(e)) from e
    with store.lock:
        if any(s["name"] == name for s in store.secrets):
            # A racing create landed the name while the Keychain IPC ran —
            # undo the fresh entry (best-effort) and answer the same 422.
            keychain.delete_secret(secret_id)
            raise HTTPException(422, f"a secret named {name} already exists")
        entry = {"id": secret_id, "name": name, "description": body.description or "",
                 "set": bool(body.value)}
        store.secrets.append(entry)
        store.save_secrets()
        out = _secret_entity(entry)
    hub.publish("secrets.changed")
    return out


@app.put("/secrets/{secret_id}", dependencies=[Depends(auth)])
def put_secret(secret_id: str, body: models.SecretPut) -> dict:
    store.require_writable(paths.secrets_file())  # before the Keychain write
    with store.lock:
        if not any(s["id"] == secret_id for s in store.secrets):
            raise HTTPException(404, "no such secret")
    sent = body.model_dump(exclude_unset=True)
    if body.value:
        # Keychain IPC outside the lock — see create_secret.
        try:
            keychain.set_secret(secret_id, body.value)
        except Exception as e:  # noqa: BLE001 — keyring's error zoo is open-ended
            raise HTTPException(503, _secret_store_rejected(e)) from e
    with store.lock:
        # Re-find: the entry may have been deleted while the Keychain IPC ran.
        existing = next((s for s in store.secrets if s["id"] == secret_id), None)
        if existing is None:
            raise HTTPException(404, "no such secret")
        if body.value:
            existing["set"] = True
        if "description" in sent:
            existing["description"] = body.description or ""
        store.save_secrets()
        out = _secret_entity(existing)
    hub.publish("secrets.changed")
    return out


@app.delete("/secrets/{secret_id}", dependencies=[Depends(auth)])
def delete_secret(secret_id: str) -> dict:
    store.require_writable(paths.secrets_file())  # before the Keychain delete
    with store.lock:
        if not any(s["id"] == secret_id for s in store.secrets):
            raise HTTPException(404, "no such secret")
    keychain.delete_secret(secret_id)  # Keychain IPC — outside the lock (see create_secret)
    with store.lock:
        store.secrets = [s for s in store.secrets if s["id"] != secret_id]
        store.save_secrets()
    hub.publish("secrets.changed")
    return {"ok": True}


@app.delete("/secrets", dependencies=[Depends(auth)])
def delete_all_secrets() -> dict:
    """§19: the whole-store sweep behind the §3 reset/uninstall flows and §20
    `secret delete --all`. Nothing stored is success, not a 404 — the §3 callers
    run it blind. Automations' `allowed_secrets` grants and step references are
    left as written, exactly like the per-id delete (§4.1 `secret-missing`)."""
    store.require_writable(paths.secrets_file())  # before the Keychain deletes
    with store.lock:
        swept = [s["id"] for s in store.secrets]
    for secret_id in swept:  # Keychain IPC — outside the lock (see create_secret)
        keychain.delete_secret(secret_id)
    with store.lock:
        # Filter rather than clear: a create that landed while the Keychain IPC
        # ran keeps its row — its value was never deleted.
        store.secrets = [s for s in store.secrets if s["id"] not in set(swept)]
        store.save_secrets()
    hub.publish("secrets.changed")  # one event covers the whole sweep
    return {"deleted": len(swept)}


# ---------- settings ----------
@app.get("/settings", dependencies=[Depends(auth)])
def get_settings() -> dict:
    return _settings_json()


@app.patch("/settings", dependencies=[Depends(auth)])
def patch_settings(body: models.SettingsPatch) -> dict:
    store.require_writable(paths.settings_file())
    # §19: strictly typed by the request model — the §4.9 booleans must be
    # booleans and `days` a real int (bool/float/string are 422s), so a bad
    # value can never persist and silently break the retention sweep.
    patch = body.model_dump(exclude_unset=True)
    if "days" in patch:
        patch["days"] = max(1, patch["days"])  # §4.9: floor 1
    with store.lock:
        for k in ("login", "menuBarIcon", "keepAwake", "automaticUpdateCheck", "notifications", "days",
                  "keepForever", "developerMode", "cliEnabled"):
            if k in patch:
                store.settings[k] = patch[k]
        store.save_settings()
    # §3: applies live, no restart — through the §2 platform layer.
    platform.current().power.reconcile(bool(store.settings.get("keepAwake")))
    hub.publish("settings.changed")
    return _settings_json()


@app.post("/settings/data-path", dependencies=[Depends(auth)])
def set_data_path(body: models.DataPath) -> dict:
    store.require_writable(paths.settings_file())
    global _data_size_cache
    raw = body.path.strip()
    if not raw:
        raise HTTPException(422, "path required")
    new_root = Path(raw).expanduser()
    target = new_root if new_root.name == "executions" else new_root / "executions"
    # Refuse before creating anything: a 409'd request must not leave a stray
    # executions/ dir inside the folder the user picked. The swap below
    # re-checks both under the lock; this pre-check just keeps the error
    # path write-free.
    with store.lock:
        if any(h["status"] == "executing" for h in store.execs.values()):
            raise HTTPException(409, "an execution is in progress — try again when it finishes")
        if any(h["status"] == "queued" for h in store.execs.values()):
            raise HTTPException(409, "a queued execution is waiting — try again when the queue is empty")
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise HTTPException(422, f"can't create that directory: {e}") from e
    # §19: the store must own its directory exclusively — dataSize sums it,
    # the startup reconcile scans it, and the §3 reset deletes execution
    # content from it. Only an empty dir or a previous Autowright executions
    # dir (the DB family plus per-execution dirs) may be adopted; dot-hidden
    # files (.DS_Store) don't count.
    db_family = {"executions.db", "executions.db-wal", "executions.db-shm"}
    try:
        for entry in target.iterdir():
            if entry.name.startswith(".") or entry.name in db_family:
                continue
            if entry.is_dir() and (entry / "execution.yaml").is_file():
                continue
            raise HTTPException(
                422, "that folder already has unrelated files in it — choose an empty folder")
    except OSError as e:
        raise HTTPException(422, f"can't read that directory: {e}") from e
    # Nothing moves: execution state lives in the executions dir, so switching
    # the path just closes the old DB and reloads from the new location. The
    # whole swap holds the lock — an engine thread finishing mid-swap would
    # otherwise hit a closed DB and die with the execution stuck "executing" —
    # and is refused while an execution is live (it writes to the old dir).
    with store.lock:
        if any(h["status"] == "executing" for h in store.execs.values()):
            raise HTTPException(409, "an execution is in progress — try again when it finishes")
        # §6: a queued firing would not survive the reload — the in-memory
        # queue dies, the entry never executes and never finishes `skipped`,
        # and its sender is never told. Refuse rather than strand it.
        if any(h["status"] == "queued" for h in store.execs.values()):
            raise HTTPException(409, "a queued execution is waiting — try again when the queue is empty")
        store.close_exec_db()
        store.settings["dataPath"] = str(target)
        store.save_settings()
        store.load_all()
        # The new location may hold records a crashed backend left "executing" —
        # repair them here too, or the automation would be wedged in 409s.
        _repair_stale_executing()
    _data_size_cache = None
    hub.publish("settings.changed")
    hub.publish("automation.changed")
    return _settings_json()


# ---------- websocket ----------
@app.websocket("/ws")
async def ws(sock: WebSocket, token: str = Query("")) -> None:
    if not token_ok(token):
        await sock.close(code=4401)
        return
    await sock.accept()
    q = hub.subscribe()

    async def pump() -> None:
        while True:
            msg = await q.get()
            if msg is OVERFLOW:
                # This client stalled and lost events — close so it reconnects
                # and re-syncs (1013: try again later).
                await sock.close(code=1013)
                return
            await sock.send_json(msg)

    # Stream in a side task and block on receive(): a quiet client publishes
    # nothing, so without the receive-watch a disconnect goes unseen and the
    # handler sits in q.get() forever — holding uvicorn's graceful shutdown.
    sender = asyncio.ensure_future(pump())
    try:
        while (await sock.receive())["type"] != "websocket.disconnect":
            pass
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        sender.cancel()
        try:
            await sender  # retrieve a send-side error so it never logs as unretrieved
        except (asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
            pass
        hub.unsubscribe(q)


def _repair_stale_executing() -> None:
    """§3: a record can only be 'executing' while an engine thread owns it —
    anything else (backend restart, a data-path switch onto a crashed tree) is
    marked interrupted. A leftover §6 `queued` record is swept too: the in-memory
    queue died with the process and the sender stopped waiting long ago, so it
    finishes `skipped` rather than executing minutes or days late. Callers hold
    store.lock (RLock, re-entry is fine)."""
    with store.lock:
        for h in list(store.execs.values()):
            if h["status"] not in ("queued", "executing"):
                continue
            if h["status"] == "executing" and engine.is_live(h["id"]):
                continue
            full = store.exec_full(h["id"]) or {**h, "steps": [], "redacted_secrets": [], "params": []}
            if full["status"] not in ("queued", "executing"):
                # §5: the yaml is authoritative — the index row went stale (a
                # crash between the yaml write and the sqlite commit of a
                # finish). Re-adopt the yaml truth instead of rewriting a
                # completed execution as interrupted/skipped.
                store.execs[full["id"]] = full
                store.update_execution(full)
                continue
            if full["status"] == "queued":
                store.execs[full["id"]] = full
                finish_never_ran(store, full, "backend restarted before this ran")
                continue
            # §3: the previous backend's step group may still be running —
            # kill it before freeing the slot, or the next cron tick starts
            # a second copy writing the same memory/ dir.
            if full.get("pgid"):
                kill_orphan_group(full["pgid"])
            full["pgid"] = None
            # §4.5 agentPgids: an agent call in flight at the crash left its
            # own-session harness CLI behind — sweep it with the same
            # pid-reuse care.
            for g in full.get("agent_pgids") or []:
                kill_orphan_agent_group(g)
            full["agent_pgids"] = []
            full["status"] = "interrupted"
            full["note"] = full["note"] or "backend restarted mid-execution"
            for s in full["steps"]:
                if s["status"] == "executing":
                    s["status"] = "interrupted"
                    for a in s.get("attempts", []):
                        if a["status"] == "executing":
                            a["status"] = "interrupted"
            store.execs[full["id"]] = full
            store.update_execution(full)
        store._refresh_exec_derived()


