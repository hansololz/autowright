"""`autowright` CLI (§20): full-parity client of the §19 backend API.

Noun-verb groups (automation, execution, secret, agent, settings, service) plus
status/instructions. Authoring goes through workdirs — spec.md + manifest.yaml +
NN-name.py step files — validated with the same §8 validators the drafting
pipeline uses. `--json` on read verbs prints the raw API JSON. Exit codes:
0 ok · 1 error (usage errors and Ctrl-C included) · 2 a followed execution
ended other than succeeded; 2 signals nothing else.
"""
from __future__ import annotations

import argparse
import getpass
import json
import shutil
import sys
import textwrap
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import paths, service

# The backend is always 127.0.0.1 — a configured corporate proxy (http_proxy
# env) must never see these requests (or the bearer token). ProxyHandler({})
# disables proxying outright.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _exit_http(e: urllib.error.HTTPError) -> None:
    # §20: print the API's detail message, never the raw JSON body.
    body = e.read().decode()
    try:
        detail = json.loads(body).get("detail")
    except (ValueError, AttributeError):
        detail = None
    if isinstance(detail, str):
        message = detail
    elif isinstance(detail, list) and detail and isinstance(detail[0], dict):
        # §20: a list-shaped validation detail (the pydantic 422 form) prints
        # as the first error's field path and message, never the raw JSON.
        loc = ".".join(str(x) for x in detail[0].get("loc") or [] if x != "body")
        msg = str(detail[0].get("msg") or "invalid value")
        message = f"{loc}: {msg}" if loc else msg
    else:
        message = body
    sys.exit(f"{e.code}: {message[:300]}")


class Client:
    def __init__(self) -> None:
        bj = paths.backend_json()
        if not bj.exists():
            sys.exit("backend isn't up (no backend.json) — start it with "
                     "`autowright service install` or `autowright-backend`")
        try:
            info = json.loads(bj.read_text())
            self.base = f"http://127.0.0.1:{info['port']}"
            self.token = info["token"]
        except (OSError, ValueError, KeyError, TypeError):
            # A SIGKILL'd backend can leave a stale/truncated backend.json.
            sys.exit("backend.json is stale or unreadable — restart the backend with "
                     "`autowright service restart` or `autowright-backend`")

    def req(self, method: str, path: str, body: dict | None = None, timeout: int = 30):
        # §20 HTTP timeouts: 30 s default; the three legitimately long calls
        # (package install, URL import, automation delete) override to 600 s.
        r = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method=method,
        )
        try:
            with _opener.open(r, timeout=timeout) as resp:
                return json.loads(resp.read().decode() or "{}")
        except urllib.error.HTTPError as e:
            _exit_http(e)
        except (urllib.error.URLError, TimeoutError) as e:
            # §3: backend.json can be well-formed yet point at a dead backend
            # (SIGKILL leftovers) — same clean guidance as a stale file, no traceback.
            sys.exit(f"backend isn't reachable at {self.base} ({e}) — restart it with "
                     "`autowright service restart` or `autowright-backend`")

    def req_raw(self, method: str, path: str, data: bytes | None = None) -> bytes:
        """Binary request — §5.1 archives and §7 result files go over the wire as raw bytes."""
        r = urllib.request.Request(
            self.base + path, data=data,
            headers={"Authorization": f"Bearer {self.token}",
                     "Content-Type": "application/octet-stream"},
            method=method,
        )
        try:
            with _opener.open(r, timeout=30) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            _exit_http(e)
        except (urllib.error.URLError, TimeoutError) as e:
            sys.exit(f"backend isn't reachable at {self.base} ({e}) — restart it with "
                     "`autowright service restart` or `autowright-backend`")


# ---------------------------------------------------------------- lookups

def find_automation(c: Client, ref: str) -> dict:
    autos = c.req("GET", "/automations")
    for a in autos:
        if a["id"] == ref:
            return a
    # §20: the short ids the CLI prints must resolve back — try id prefix
    # before names.
    matches = [a for a in autos if a["id"].startswith(ref)]
    # §4.1 names are unique at write time, but duplicates already on disk
    # still load — §20: ambiguity exits with the candidate list, never a
    # silent first-match (substring matches can still collide anyway).
    if not matches:
        matches = [a for a in autos if a["name"].lower() == ref.lower()]
    if not matches:
        matches = [a for a in autos if ref.lower() in a["name"].lower()]
    if len(matches) == 1:
        return matches[0]
    if matches:
        sys.exit(f"{ref!r} is ambiguous — matches: "
                 + ", ".join(f"{a['name']} ({a['id'][:8]})" for a in matches)
                 + " — use the id instead")
    sys.exit(f"no automation matches {ref!r} — "
             f"have: {', '.join(a['name'] for a in autos) or '(none)'}")


def find_execution(c: Client, ref: str | None) -> dict:
    # §19/§20: no limit — reference resolution reads the uncapped list, so
    # every short id the CLI ever printed resolves back (§20 reference rule).
    execs = c.req("GET", "/executions")["executions"]
    if not ref:
        if execs:
            return execs[0]
        sys.exit("no execution found")
    matches = [e for e in execs if e["id"].startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    sys.exit(f"no unique execution matches {ref!r} — "
             f"have: {', '.join(f'{e['id'][:8]} ({e['automationName']}, {e['status']}, {e['started']})' for e in execs) or '(none)'}")


def _pjson(data) -> None:
    print(json.dumps(data, indent=2))


# ---------------------------------------------------------------- log follow

def follow_exec(c: Client, execution_id: str) -> str:
    # Logs are lazy (§19): poll the record for step/attempt structure, then
    # fetch each attempt's log file and print lines past the last seen sequence.
    # An attempt fetched once after it reached a terminal status can't grow —
    # skip it on later polls instead of re-downloading its whole file forever.
    seen: dict[tuple, int] = {}   # (step index | None, attempt | None) → last printed sequence
    settled: set[tuple] = set()
    try:
        while True:
            e = c.req("GET", f"/executions/{execution_id}")
            targets: list[tuple[int | None, int | None, bool]] = [(None, None, False)]  # the execution log
            for i, s in enumerate(e.get("steps", [])):
                for a in s.get("attempts") or []:
                    terminal = a.get("status") not in ("executing", "queued")
                    targets.append((i, a["number"], terminal))
            for step, attempt, terminal in targets:
                key = (step, attempt)
                if key in settled:
                    continue
                q = "" if step is None else f"?step={step}&attempt={attempt}"
                lines = c.req("GET", f"/executions/{execution_id}/logs{q}").get("lines", [])
                last = seen.get(key, 0)
                for ln in lines:
                    if ln["sequence"] > last:
                        print(f"  {ln['time']} [{ln['kind']}] {ln['text']}")
                        last = ln["sequence"]
                seen[key] = last
                if terminal:
                    settled.add(key)
            # §20 follow semantics: `queued` (§6 firing queue) is not terminal —
            # keep polling through promotion to executing and on to a real end.
            if e["status"] not in ("executing", "queued"):
                print(f"→ {e['status']} in {e['duration']}")
                return e["status"]
            time.sleep(1)
    except KeyboardInterrupt:
        # §20: Ctrl-C while following is an error exit (1), never a traceback,
        # and never 2, which is exclusively the follow-failure signal. Only the
        # watching stops; the execution runs on in the backend.
        sys.exit(f"interrupted: execution {execution_id} is still running; "
                 "check it with `autowright execution show`")


def _exit_by_status(status: str) -> None:
    """§20 exit codes: 2 when a followed execution ends other than succeeded."""
    if status != "succeeded":
        sys.exit(2)


# ---------------------------------------------------------------- workdir (§20)

WORKDIR_META = ("spec.md", "manifest.yaml", "instructions.md", "notes.md")
# §4.2: the resolved value fields per kind — stripped from pulled manifests
# (values are user-owned operational state, set via `param set`, never versioned).
PARAM_VALUE_KEYS = ("on", "lines", "rows", "value")


def read_workdir(d: Path) -> dict[str, str]:
    from .drafting import STEP_FILE_RE

    if not d.is_dir():
        sys.exit(f"{d} is not a directory")
    files = {}
    try:
        entries = sorted(d.iterdir())
    except OSError as e:
        # §20 exit-code rule: a directory the command can't read is a message
        # on stderr, never a traceback.
        sys.exit(f"can't read {d}: {e.strerror or e}")
    for f in entries:
        if f.name in WORKDIR_META or STEP_FILE_RE.match(f.name):
            try:
                files[f.name] = f.read_text(encoding="utf-8")
            except OSError as e:
                sys.exit(f"can't read {f}: {e.strerror or e}")
            except UnicodeDecodeError:
                sys.exit(f"can't read {f}: not UTF-8 text")
    return files


def _all_grants(c: Client) -> dict:
    """§20: the validators' *existence* context — all configured agents + all
    stored secrets (ids; names ride along for the §8 error copy), so a
    manifest referencing an unknown one fails with the §8
    message. Which known ids the automation may use is `_grants`'s job."""
    agents = [{"id": a["id"], "name": a.get("name") or a["harness"]}
              for a in c.req("GET", "/agents")]
    secrets = [{"id": s["id"], "name": s["name"]} for s in c.req("GET", "/secrets")]
    return {"agents": agents, "secrets": secrets}


def validate_workdir(c: Client, d: Path) -> dict:
    """§8 validation of a workdir; prints every error and exits 1 on failure.
    Returns the draft payload (spec blocks + steps/params/packages/triggers/instr)."""
    from . import drafting

    files = read_workdir(d)
    errors: list[str] = []
    spec: dict = {}
    if "spec.md" in files:
        spec, errs = drafting.validate_spec({"spec.md": files["spec.md"]})
        errors += errs
    else:
        errors.append("spec.md is missing")
    step_files = {n: t for n, t in files.items() if n not in ("spec.md", "instructions.md", "notes.md")}
    # §20: the manifest's cron entries may carry `run_if_missed` (§4.3), a key
    # the §8 rule-9 dialect does not know; lifted out before validation and
    # stamped back onto the drafted crons by (expression, timezone).
    step_files, opted_out, errs = _lift_run_if_missed(step_files)
    errors += errs
    draft, errs = drafting.validate_steps(step_files, _all_grants(c))
    errors += errs
    for t in draft.get("triggers") or []:
        if t["kind"] == "cron" and (t["expression"], t.get("timezone")) in opted_out:
            t["runIfMissed"] = False
    if errors:
        print(f"{d} doesn't validate:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)
    draft["spec"] = spec["blocks"]
    # §20: the workdir manifest carries `name`/`description` (unlike the §8
    # sync manifest, where the validator ignores them) - read them here.
    import yaml

    manifest = yaml.safe_load(files.get("manifest.yaml") or "") or {}
    if isinstance(manifest, dict):
        if manifest.get("name"):
            draft["name"] = str(manifest["name"])
        if manifest.get("description"):
            draft["description"] = str(manifest["description"])
    if "instructions.md" in files:
        draft["instructions"] = files["instructions.md"].strip()
    if "notes.md" in files:
        draft["notes"] = files["notes.md"].strip()
    return draft


def _lift_run_if_missed(step_files: dict[str, str]) -> tuple[dict[str, str], set, list[str]]:
    """Strip `run_if_missed` from the manifest's cron entries, returning the
    files to validate, the (expression, timezone) keys that opted out, and any
    errors (a non-boolean value). A manifest without the key passes through
    byte-for-byte."""
    import yaml

    text = step_files.get("manifest.yaml")
    if not text:
        return step_files, set(), []
    try:
        manifest = yaml.safe_load(text)
    except yaml.YAMLError:
        return step_files, set(), []  # the drafting validator reports the parse error
    entries = manifest.get("triggers") if isinstance(manifest, dict) else None
    if not isinstance(entries, list) or not any(
            isinstance(t, dict) and "run_if_missed" in t for t in entries):
        return step_files, set(), []
    opted_out: set = set()
    errors: list[str] = []
    for t in entries:
        if not isinstance(t, dict) or "run_if_missed" not in t:
            continue
        v = t.pop("run_if_missed")
        if "cron" not in t:
            errors.append("triggers: run_if_missed applies to cron entries only")
        elif not isinstance(v, bool):
            errors.append("triggers: run_if_missed must be true or false")
        elif v is False:
            opted_out.add((str(t["cron"]).strip(), str(t["timezone"]) if t.get("timezone") else None))
    return {**step_files, "manifest.yaml": yaml.safe_dump(manifest, sort_keys=False)}, opted_out, errors


def _manifest_step(s: dict) -> dict:
    e: dict = {"file": s.get("file"), "name": s.get("name", ""), "description": s.get("description", "")}
    if s.get("agent"):
        e["agent"] = True
        e["why"] = s.get("why", "")
        if s.get("agents"):
            e["agents"] = list(s["agents"])
    if s.get("secrets"):
        e["secrets"] = list(s["secrets"])
    if s.get("packages"):
        e["packages"] = list(s["packages"])
    if s.get("timeout"):
        e["timeout"] = s["timeout"]
    if s.get("noTimeout"):  # API spelling — the CLI only ever reads API step JSON
        e["no_timeout"] = True
    if s.get("retries"):
        e["retries"] = s["retries"]
    if s.get("infiniteRetries"):
        e["infinite_retries"] = True
    return e


def write_workdir(d: Path, auto: dict) -> list[str]:
    """§20 pull: materialize an automation's current version into a workdir.
    Returns the written filenames. Any file it can't write is a message on
    stderr (§20 exit-code rule), never a traceback."""
    import yaml

    from . import specmd

    try:
        return _write_workdir(d, auto, yaml, specmd)
    except OSError as e:
        sys.exit(f"can't write to {d}: {e.strerror or e}")


def _write_workdir(d: Path, auto: dict, yaml, specmd) -> list[str]:
    d.mkdir(parents=True, exist_ok=True)
    written = ["spec.md", "manifest.yaml"]
    (d / "spec.md").write_text(specmd.blocks_to_md(auto.get("spec") or []), encoding="utf-8")
    manifest: dict = {"name": auto["name"], "description": auto.get("description", "")}
    crons = [{"cron": t["expression"], **({"timezone": t["timezone"]} if t.get("timezone") else {}),
              # §4.3 `run_if_missed`: written only when the cron opted out (absent = true)
              **({"run_if_missed": False} if t.get("runIfMissed") is False else {})}
             for t in auto.get("triggers") or [] if t["kind"] == "cron"]
    if crons:
        manifest["triggers"] = crons
    params = [{k: v for k, v in p.items() if k not in PARAM_VALUE_KEYS}
              for p in auto.get("params") or []]
    if params:
        manifest["params"] = params
    if auto.get("packages"):
        manifest["packages"] = auto["packages"]
    manifest["steps"] = [_manifest_step(s) for s in auto.get("steps") or []]
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
    for s in auto.get("steps") or []:
        (d / s["file"]).write_text(s.get("code", ""), encoding="utf-8")
        written.append(s["file"])
    if auto.get("instructions"):
        (d / "instructions.md").write_text(auto["instructions"] + "\n", encoding="utf-8")
        written.append("instructions.md")
    if (auto.get("notes") or "").strip():
        (d / "notes.md").write_text(auto["notes"].strip() + "\n", encoding="utf-8")
        written.append("notes.md")
    return written


def merge_draft_triggers(stored: list[dict], drafted: list[dict]) -> list[dict]:
    """§4.3 trigger merge, client-side like the editor: drafted crons replace
    the spec-sourced cron subset ((expression, timezone) matches keep id, enabled
    state, and source and take the manifest entry's run_if_missed, new entries
    arrive enabled with source: spec, unmatched
    spec-sourced stored crons drop — `source: user` crons always survive);
    drafted message/app-start
    entries add only when no stored trigger matches their identity fields;
    stored non-cron triggers always survive."""
    def same_non_cron(a: dict, b: dict) -> bool:
        if a["kind"] != b["kind"]:
            return False
        if a["kind"] == "app_start":
            return True
        if a["kind"] == "imessage":
            return (a.get("from") == b.get("from")
                    and (a.get("pattern") or "") == (b.get("pattern") or ""))
        if a["kind"] == "discord":
            return (a.get("channel") == b.get("channel") and a.get("secret") == b.get("secret")
                    and (a.get("pattern") or "") == (b.get("pattern") or "")
                    and bool(a.get("mention")) == bool(b.get("mention"))
                    and (a.get("author") or []) == (b.get("author") or []))
        return False

    out = [t for t in stored if t["kind"] != "cron" or t.get("source") == "user"]
    for d in drafted:
        if d["kind"] == "cron":
            kept = next((t for t in stored if t["kind"] == "cron"
                         and t["expression"] == d["expression"] and t.get("timezone") == d.get("timezone")), None)
            if kept is None:
                out.append(d)
            else:
                # §20: the manifest entry decides run_if_missed (absent = true).
                merged = {k: v for k, v in kept.items() if k != "runIfMissed"}
                if d.get("runIfMissed") is False:
                    merged["runIfMissed"] = False
                i = next((i for i, x in enumerate(out) if x is kept), None)
                if i is None:
                    out.append(merged)
                else:  # a matched user cron already survived above
                    out[i] = merged
        elif d["kind"] != "time" and not any(same_non_cron(t, d) for t in stored):
            out.append(d)
    return out


def ensure_packages(c: Client, pkgs: list[dict]) -> None:
    """§20: run the §6.2 ensure for a just-saved version's declared packages,
    so an install failure surfaces at save time, not when a trigger fires. A
    failure warns and never fails the save (the §8 rule) — the engine's
    pre-execution ensure retries it before anything runs."""
    if not pkgs:
        return
    r = c.req("POST", "/packages/install", {"packages": pkgs}, timeout=600)
    for p in r.get("packages", []):
        if p.get("status") == "installed":
            version = f" {p['version']}" if p.get("version") else ""
            print(f"  package {p['pip']}{version} installed")
        else:
            print(f"  warning: package {p['pip']} failed to install — "
                  f"{p.get('error') or 'unknown error'}")


def _step_grant_ids(draft: dict, key: str) -> set[str]:
    # `agents` holds {id, why?} entries, `secrets` {id, why} (§4.1) — bare
    # strings are tolerated as ids for hand-written manifests.
    return {v["id"] if isinstance(v, dict) else v
            for s in draft.get("steps", []) for v in s.get(key) or []
            if (v.get("id") if isinstance(v, dict) else v)}


def _grants(c: Client, args, draft: dict,
            stored_agents: list[str] = (), stored_secrets: list[str] = ()) -> tuple[list[str], list[str]]:
    """§20 grant model: the saved grants are the stored lists plus the explicit
    --grant-agent/--grant-secret flags — no all-on seed, no silent widening.
    Both flags take NAMES (the human surface) and map to ids on save; the
    needed-vs-granted comparison is id-set against id-set. Every id the
    workdir needs (per-step agents; per-step secrets plus
    code-referenced secretReferences) must be granted, or this exits 1 naming the
    exact flags to add. Unknown flag names exit with the candidate list."""
    agents = c.req("GET", "/agents")
    secrets = c.req("GET", "/secrets")
    known_agents = [a.get("name") or a["harness"] for a in agents]
    known_secrets = [s["name"] for s in secrets]
    # id → name maps, for the flag suggestions in error copy — names appear
    # only in what gets printed, never in the comparison.
    agent_name_by_id = {a["id"]: (a.get("name") or a["harness"]) for a in agents}
    secret_name_by_id = {s["id"]: s["name"] for s in secrets}

    granted_agent_ids = list(stored_agents)
    for name in args.grant_agent:
        # §4.7 uniqueness makes the case-insensitive match unambiguous.
        match = [a for a in agents
                 if (a.get("name") or a["harness"]).lower() == name.lower()]
        if not match:
            sys.exit(f"no agent named {name!r} — have: {', '.join(known_agents) or '(none)'}")
        if match[0]["id"] not in granted_agent_ids:
            granted_agent_ids.append(match[0]["id"])
    granted_secret_ids = list(stored_secrets)
    for name in args.grant_secret:
        match = [s for s in secrets if s["name"] == name]
        if not match:
            sys.exit(f"no stored secret named {name!r} — have: "
                     f"{', '.join(sorted(known_secrets)) or '(none)'}")
        if match[0]["id"] not in granted_secret_ids:
            granted_secret_ids.append(match[0]["id"])

    need_agents = _step_grant_ids(draft, "agents")
    need_secrets = _step_grant_ids(draft, "secrets") | set(draft.get("secretReferences") or [])
    # Manifest ids are existence-checked by the validators; code-referenced
    # secrets (secretReferences) aren't, so a nonexistent one needs storing, not a flag.
    unknown = sorted(i for i in need_secrets - set(granted_secret_ids)
                     if i not in secret_name_by_id)
    if unknown:
        sys.exit(f"the step code references secrets that don't exist: {', '.join(unknown)} — "
                 f"store them first with `autowright secret set <NAME>`")
    missing = ([f"--grant-agent {agent_name_by_id[i]}"
                for i in sorted(need_agents - set(granted_agent_ids)) if i in agent_name_by_id]
               + [f"--grant-secret {secret_name_by_id[i]}"
                  for i in sorted(need_secrets - set(granted_secret_ids))
                  if i in secret_name_by_id])
    if missing:
        sys.exit("the steps use agents/secrets this automation isn't granted — "
                 f"grants are explicit; re-run with {' '.join(missing)}")
    # §20: an agent step with no `agents:` list runs on the first enabled agent,
    # so it still needs at least one granted agent.
    if not granted_agent_ids and any(s.get("agent") for s in draft.get("steps", [])):
        sys.exit("an agent step needs at least one granted agent — re-run with "
                 f"--grant-agent <NAME> (have: {', '.join(known_agents) or '(none)'})")
    return granted_agent_ids, sorted(granted_secret_ids)


# ---------------------------------------------------------------- top level

def cmd_status(c: Client, args) -> None:
    h = c.req("GET", "/health")
    s = c.req("GET", "/state")
    info = {
        "version": h.get("version"), "backend": c.base,
        "automations": len(s.get("automations") or []),
        # §19: /state.executions is the §7 window, not the list — the real
        # count rides executionsTotal.
        "executions": s.get("executionsTotal") or len(s.get("executions") or []),
        "agents": len(s.get("agents") or []),
        "secrets": len(s.get("secrets") or []),
        "pendingDraft": s.get("pendingDraft"),
    }
    if args.json:
        _pjson(info)
        return
    print(f"backend {info['version']} at {info['backend']}")
    print(f"{info['automations']} automations · {info['executions']} executions · "
          f"{info['agents']} agents · {info['secrets']} secrets")
    if info["pendingDraft"]:
        print(f"pending create draft: {info['pendingDraft'].get('name') or '(unnamed)'}")


def cmd_instructions(c: Client, args) -> None:
    r = c.req("GET", "/instructions")
    if args.json:
        _pjson(r)
        return
    print(r.get("framework", ""))


# ---------------------------------------------------------------- automation

def cmd_automation_list(c: Client, args) -> None:
    autos = c.req("GET", "/automations")
    if args.json:
        _pjson(autos)
        return
    for a in autos:
        chip = a["triggerChip"] + (" (off)" if a.get("allTriggersOff") else "")
        # §20 needs-fixing parity: mark rows whose §4.1 problems list is
        # non-empty; `automation show` prints the labels.
        fixing = "needs fixing  " if a.get("problems") else ""
        print(f"{a['name']:<32} {chip:<16} {a['lastStatus']:<11} "
              f"{fixing}{a.get('resultChip') or ''}  [{a['id'][:8]}]")


def cmd_automation_show(c: Client, args) -> None:
    a = find_automation(c, args.automation)
    full = c.req("GET", f"/automations/{a['id']}")
    if args.json:
        _pjson(full)
        return
    print(f"{full['name']} [{full['id']}] — {full['specMeta']}")
    if full.get("description"):
        print(full["description"])
    print(f"status: {full['lastStatus']}"
          + (f" · {full['resultChip']}" if full.get("resultChip") else ""))
    if full.get("problems"):
        # §20 needs-fixing parity: one indented line per §4.1 problem label.
        print("needs fixing:")
        for p in full["problems"]:
            print(f"  {p['label']}")
    for i, t in enumerate(full.get("triggers") or [], 1):
        print(f"trigger {i}: {t['label']}" + _trigger_marks(t))
    for p in full.get("params") or []:
        print(f"param {p['name']} ({p['kind']}): {_param_value(p)!r}")
    # §4.1: step secrets entries carry ids — resolve to names for display
    # (a dangling id shows its short prefix).
    secret_names = {s["id"]: s["name"] for s in c.req("GET", "/secrets")} \
        if any(s.get("secrets") for s in full.get("steps") or []) else {}
    for i, s in enumerate(full.get("steps") or [], 1):
        tags = "".join([
            " [agent]" if s.get("agent") else "",
            (" [secrets: "
             + ", ".join(secret_names.get(e["id"], f"{e['id'][:8]}…") for e in s["secrets"])
             + "]") if s.get("secrets") else ""])
        print(f"step {i}: {s['name']}{tags}")
    vs = [f"v{v['version']}" for v in full.get("versions") or []]
    if vs:
        print(f"history: {', '.join(vs)}")
    if full.get("draft"):
        print("has an unsaved draft")


def cmd_automation_pull(c: Client, args) -> None:
    from .transfer import safe_filename

    a = find_automation(c, args.automation)
    full = c.req("GET", f"/automations/{a['id']}")
    d = Path(args.dir or safe_filename(full["name"]))
    for name in write_workdir(d, full):
        print(f"wrote {d / name}")


def cmd_automation_push(c: Client, args) -> None:
    a = find_automation(c, args.automation)
    full = c.req("GET", f"/automations/{a['id']}")
    draft = validate_workdir(c, Path(args.dir))
    if args.note:
        draft["note"] = args.note
    draft["triggers"] = merge_draft_triggers(full.get("triggers") or [], draft["triggers"])
    # §20 grant model: stored grants plus explicit --grant-* flags, never
    # widened by what the pushed steps happen to reference.
    step_agents, allowed_secrets = _grants(c, args, draft,
                                           stored_agents=full.get("stepAgents") or [],
                                           stored_secrets=full.get("allowedSecrets") or [])
    body = {"draft": draft, "stepAgents": step_agents, "allowedSecrets": allowed_secrets}
    # §20: the workdir manifest's identity fields apply on push — a changed
    # name rides the version save (§19 name patch), a changed description
    # lands via the §19 PATCH after it; absent/blank values change nothing.
    if draft.get("name"):
        body["name"] = draft["name"]
    r = c.req("POST", f"/automations/{a['id']}/versions", body)
    # Reported before the description PATCH — a failing PATCH exits, and the
    # version has already landed either way.
    print(f"saved {draft.get('name') or full['name']!r} as v{r['version']}")
    description = draft.get("description")
    if description and description != (full.get("description") or ""):
        c.req("PATCH", f"/automations/{a['id']}", {"description": description})
    ensure_packages(c, draft.get("packages") or [])


def cmd_automation_create(c: Client, args) -> None:
    d = Path(args.dir)
    draft = validate_workdir(c, d)
    name = args.name or draft.get("name") or d.resolve().name
    agents = c.req("GET", "/agents")
    agent_id = None
    if args.agent:
        match = [a for a in agents
                 if (a.get("name") or a["harness"]).lower() == args.agent.lower()]
        if not match:
            sys.exit(f"no agent named {args.agent!r} — have: "
                     f"{', '.join(a.get('name') or a['harness'] for a in agents) or '(none)'}")
        agent_id = match[0]["id"]
    # §20 grant model: create grants exactly the --grant-* flags — no all-on seed.
    step_agents, allowed_secrets = _grants(c, args, draft)
    body = {"draft": draft, "name": name, "agentId": agent_id,
            "stepAgents": step_agents, "allowedSecrets": allowed_secrets}
    r = c.req("POST", "/automations", body)
    n = len(r.get("triggers") or [])
    print(f"created {r['name']!r} [{r['id'][:8]}] — "
          + (f"{n} trigger(s) enabled" if n else "no triggers (execute manually)"))
    ensure_packages(c, draft.get("packages") or [])


def cmd_automation_delete(c: Client, args) -> None:
    a = find_automation(c, args.automation)
    if not args.yes:
        sys.exit(f"deleting {a['name']!r} removes every version and its execution "
                 "history — add --yes to confirm")
    c.req("DELETE", f"/automations/{a['id']}", timeout=600)
    print(f"deleted {a['name']!r}")


def cmd_automation_restore(c: Client, args) -> None:
    a = find_automation(c, args.automation)
    v = args.version.lstrip("vV")
    if not v.isdigit():
        sys.exit(f"version must be vN, got {args.version!r}")
    r = c.req("POST", f"/automations/{a['id']}/restore", {"version": int(v)})
    print(f"restored v{v} of {a['name']!r} as v{r.get('version', '?')}")


def cmd_automation_execute(c: Client, args) -> None:
    a = find_automation(c, args.automation)
    body: dict = {"trigger": "manual"}  # §4.5 machine kind; the API serializes the label
    if args.version:
        body["version"] = args.version
    if args.queue:
        body["queue"] = True  # §6/§19 manual queue admission when every slot is taken
    r = c.req("POST", f"/automations/{a['id']}/execute", body)
    if r.get("queued"):
        print(f"queued — execution {r['executionId']} (waiting for a free slot)")
    else:
        print(f"started — execution {r['executionId']}")
    if args.follow:
        _exit_by_status(follow_exec(c, r["executionId"]))


def cmd_automation_export(c: Client, args) -> None:
    from .transfer import safe_filename

    a = find_automation(c, args.automation)
    q = "?values=0" if args.no_values else ""
    data = c.req_raw("GET", f"/automations/{a['id']}/export{q}")
    path = args.path or f"{safe_filename(a['name'])}.autowright"
    try:
        with open(path, "wb") as f:
            f.write(data)
    except OSError as e:
        # §20: an unwritable path (missing directory, no permission, read-only
        # volume) is a plain error message on stderr, never an OSError traceback.
        sys.exit(f"can't write {path}: {e.strerror or e}")
    print(f"exported {a['name']!r} to {path}")


def cmd_automation_import(c: Client, args) -> None:
    if args.path.startswith(("http://", "https://")):
        # §5.2: fetch + preview on the backend, confirm immediately — the typed
        # command is the user's explicit action (http:// gets the backend's 422).
        pr = c.req("POST", "/automations/import/url", {"url": args.path}, timeout=600)
        resolved = pr.get("preview", {}).get("resolvedUrl")
        if resolved and resolved != args.path.strip():
            print(f"resolved to {resolved}")
        r = c.req("POST", "/automations/import/confirm", {"token": pr.get("token")})
    else:
        try:
            with open(args.path, "rb") as f:
                data = f.read()
        except OSError as e:
            # §20: an unreadable archive is a plain message on stderr, never a
            # raw OSError.
            sys.exit(f"can't read {args.path}: {e.strerror or e}")
        r = json.loads(c.req_raw("POST", "/automations/import", data).decode() or "{}")
    s = r.get("summary", {})
    print(f"imported {r.get('automation', {}).get('name', '?')!r} [{r.get('automation', {}).get('id', '')[:8]}]")
    if s.get("renamedFrom"):
        # §5.1 name dedupe — the archive's name was taken.
        print(f"  renamed from {s['renamedFrom']!r} - that name already exists")
    if s.get("osMismatch"):
        # §5.1: the archive was exported on another platform. §20 names it the
        # §4.1 display way ("macOS"), never the raw §5.1 token ("macos").
        print(f"  built on {paths.os_display_name(s.get('os'))} - "
              "its steps may need rewriting on this machine")
    def _match_label(m: dict, ready_suffix: bool = False) -> str:
        # §20: the archive name, with " -> local" only when the match renamed;
        # a matched agent whose harness isn't ready is marked like §12.
        label = m["name"] + (f" -> {m['matchedTo']}" if m["matchedTo"] != m["name"] else "")
        if ready_suffix and not m.get("ready", True):
            label += " (needs setup)"
        return label

    if s.get("secretsMatched"):
        print("  secrets matched: " + ", ".join(_match_label(m) for m in s["secretsMatched"]))
    if s.get("agentsMatched"):
        print("  agents matched: " + ", ".join(
            _match_label(m, ready_suffix=True) for m in s["agentsMatched"]))
    if s.get("unresolved"):
        print("  no match on this machine: " + ", ".join(
            f"{u['kind']} {u['name']}" for u in s["unresolved"]))
    ensure_packages(c, s.get("packages") or [])
    if s.get("unresolved"):
        print("  this automation needs attention - open it and fix the "
              "highlighted agents and secrets")
    print("  triggers imported off — enable them with `autowright automation trigger on`")


# ------------------------------------------------------------ automation param

def _param_value(p: dict):
    kind = p.get("kind")
    if kind == "toggle":
        return p.get("on", False)
    if kind == "list":
        return p.get("lines", [])
    if kind == "kv":
        return p.get("rows", [])
    return p.get("value")


def parse_param_value(p: dict, raw: str):
    """§20: parse a `param set` VALUE by the definition's kind."""
    kind = p["kind"]
    if kind == "toggle":
        low = raw.strip().lower()
        if low in ("on", "true", "1", "yes"):
            return True
        if low in ("off", "false", "0", "no"):
            return False
        sys.exit(f"param {p['name']}: toggle takes on|off, got {raw!r}")
    if kind == "number":
        try:
            return int(raw)
        except ValueError:
            sys.exit(f"param {p['name']}: number takes an integer, got {raw!r}")
    if kind == "list":
        if raw.strip().startswith("["):
            try:
                v = json.loads(raw)
            except ValueError as e:
                sys.exit(f"param {p['name']}: bad JSON array — {e}")
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                sys.exit(f"param {p['name']}: expected a JSON array of strings")
            return v
        return [x.strip() for x in raw.split(",") if x.strip()]
    if kind == "kv":
        if raw.strip().startswith("{"):
            try:
                v = json.loads(raw)
            except ValueError as e:
                sys.exit(f"param {p['name']}: bad JSON object — {e}")
            if not isinstance(v, dict):
                sys.exit(f"param {p['name']}: expected a JSON object")
            return [{"key": k, "value": str(x)} for k, x in v.items()]
        rows = []
        for pair in raw.split(","):
            if pair.strip():
                k, sep, v = pair.partition("=")
                if not sep or not k.strip():
                    sys.exit(f"param {p['name']}: kv takes k=v[,k=v] or a JSON object, got {raw!r}")
                rows.append({"key": k.strip(), "value": v.strip()})
        return rows
    return raw  # text


def cmd_param_list(c: Client, args) -> None:
    a = find_automation(c, args.automation)
    params = c.req("GET", f"/automations/{a['id']}")["params"]
    if args.json:
        _pjson(params)
        return
    for p in params:
        print(f"{p['name']:<24} {p['kind']:<8} {_param_value(p)!r}")


def cmd_param_set(c: Client, args) -> None:
    a = find_automation(c, args.automation)
    defs = {p["name"]: p for p in c.req("GET", f"/automations/{a['id']}")["params"]}
    values = {}
    for item in args.values:
        name, sep, raw = item.partition("=")
        if not sep:
            sys.exit(f"expected NAME=VALUE, got {item!r}")
        if name not in defs:
            sys.exit(f"no param named {name!r} — have: {', '.join(defs) or '(none)'}")
        values[name] = parse_param_value(defs[name], raw)
    c.req("PATCH", f"/automations/{a['id']}", {"paramValues": values})
    print(f"set {', '.join(values)} on {a['name']!r}")


# ---------------------------------------------------------- automation trigger

def cmd_trigger_list(c: Client, args) -> None:
    a = find_automation(c, args.automation)
    triggers = c.req("GET", f"/automations/{a['id']}")["triggers"]
    if args.json:
        _pjson(triggers)
        return
    if not triggers:
        # §9 per-OS copy rule: name the §13 surface the way this OS does, or
        # drop the clause where there is none (Linux).
        surface = paths.tray_surface_name()
        tail = f" or the {surface}" if surface else ""
        print(f"no triggers — executes only via `automation execute`{tail}")
    for i, t in enumerate(triggers, 1):
        print(f"{i}. {t['label']}" + _trigger_marks(t))


def _trigger_marks(t: dict) -> str:
    """§20 list/show suffixes: " (off)" then " (no catch-up)" (§4.3 runIfMissed off)."""
    return ((" (off)" if not t["enabled"] else "")
            + (" (no catch-up)" if t.get("runIfMissed") is False else ""))


def _stored_triggers(c: Client, automation_id: str) -> list[dict]:
    # label/short are §4.3 display derivations — the PATCH normalizer ignores
    # extra keys, so stored entries round-trip as-is.
    return c.req("GET", f"/automations/{automation_id}")["triggers"]


def _trigger_at_index(triggers: list[dict], n: str) -> dict:
    if not n.isdigit() or not 1 <= int(n) <= len(triggers):
        sys.exit(f"trigger index must be 1..{len(triggers)} (see `automation trigger list`)")
    return triggers[int(n) - 1]


def cmd_trigger_add(c: Client, args) -> None:
    a = find_automation(c, args.automation)
    if args.discord:
        if not args.secret:
            sys.exit("a Discord trigger needs --secret, the name of the secret "
                     "holding the bot token")
        # §20: --secret takes the NAME (the human surface, like the grant
        # flags); the trigger stores the secret's §4.8 id.
        secrets = c.req("GET", "/secrets")
        match = [s for s in secrets if s["name"] == args.secret]
        if not match:
            names = sorted(s["name"] for s in secrets)
            sys.exit(f"no stored secret named {args.secret!r} — have: "
                     f"{', '.join(names) or '(none)'}")
        entry: dict = {"kind": "discord", "channel": args.discord,
                       "secret": match[0]["id"], "enabled": True}
        if args.pattern:
            entry["pattern"] = args.pattern
        if args.mention:
            entry["mention"] = True
        if args.author:
            entry["author"] = [a.strip() for v in args.author
                               for a in v.split(",") if a.strip()]
    elif args.imessage:
        entry = {"kind": "imessage", "from": args.imessage, "enabled": True}
        if args.pattern:
            entry["pattern"] = args.pattern
    elif args.app_start:
        entry = {"kind": "app_start", "enabled": True}
    elif args.at:
        entry = {"kind": "time", "at": args.at, "enabled": True}
    elif args.expression:
        # §4.3: a hand-added cron is user-sourced — it survives later syncs/pushes.
        entry = {"kind": "cron", "expression": args.expression, "enabled": True,
                 "source": "user"}
    else:
        sys.exit("give a cron expression, --at for a one-shot, --app-start, "
                 "--discord for a Discord message trigger, or --imessage for "
                 "an iMessage trigger")
    if args.timezone:
        entry["timezone"] = args.timezone
    if getattr(args, "no_run_if_missed", False):
        if entry["kind"] not in ("cron", "time"):
            sys.exit("--no-run-if-missed applies to a cron schedule or --at one-shot only")
        entry["runIfMissed"] = False  # §4.3: stored only when false
    triggers = _stored_triggers(c, a["id"]) + [entry]
    r = c.req("PATCH", f"/automations/{a['id']}", {"triggers": triggers})
    print(f"added — now: {r['triggerChip']}")


def cmd_trigger_toggle(c: Client, args) -> None:
    a = find_automation(c, args.automation)
    triggers = _stored_triggers(c, a["id"])
    t = _trigger_at_index(triggers, args.index)
    t["enabled"] = args.fn_enabled
    c.req("PATCH", f"/automations/{a['id']}", {"triggers": triggers})
    print(f"trigger {args.index} ({t['short']}) now {'on' if t['enabled'] else 'off'}")


def cmd_trigger_remove(c: Client, args) -> None:
    a = find_automation(c, args.automation)
    triggers = _stored_triggers(c, a["id"])
    t = _trigger_at_index(triggers, args.index)
    triggers.remove(t)
    c.req("PATCH", f"/automations/{a['id']}", {"triggers": triggers})
    print(f"removed trigger {args.index} ({t['short']})")


# ----------------------------------------------------- automation memory/snapshot

def cmd_memory_show(c: Client, args) -> None:
    # §20 memory inspection — the authoring surface's only read access to
    # memory contents; list the files, or print one file's text verbatim.
    a = find_automation(c, args.automation)
    if args.file:
        r = c.req("GET", f"/automations/{a['id']}/memory/files/"
                         + urllib.parse.quote(args.file, safe="/"))
        if args.json:
            _pjson(r)
        else:
            sys.stdout.write(r["text"])
        return
    r = c.req("GET", f"/automations/{a['id']}/memory/files")
    if args.json:
        _pjson(r["files"])
        return
    if not r["files"]:
        print("memory is empty")
        return
    for f in r["files"]:
        print(f"{f['size']:>9}  {f['updated']:<16} {f['name']}")


def cmd_memory_clear(c: Client, args) -> None:
    a = find_automation(c, args.automation)
    c.req("POST", f"/automations/{a['id']}/memory/clear")
    print("memory cleared (a pre-clear snapshot was taken when memory existed)")


def _find_snapshot(c: Client, auto: dict, ref: str) -> dict:
    snaps = c.req("GET", f"/automations/{auto['id']}")["snapshots"]
    matches = [s for s in snaps if s["id"].startswith(ref)]
    if len(matches) == 1:
        return matches[0]
    sys.exit(f"no unique snapshot matches {ref!r} — "
             f"have: {', '.join(f'{s['id'][:8]} ({s['when']}, {s['reason']}, {s['version']}, {s['size']}{', ' + s['name'] if s.get('name') else ''})' for s in snaps) or '(none)'}")


def cmd_snapshot_list(c: Client, args) -> None:
    a = find_automation(c, args.automation)
    snaps = c.req("GET", f"/automations/{a['id']}")["snapshots"]
    if args.json:
        _pjson(snaps)
        return
    for s in snaps:
        name = s.get("name") or ""
        print(f"{s['id'][:8]}  {s['when']:<16} {s['reason']:<11} {s['version']:<5} "
              f"{s['size']:<9} {name}")


def cmd_snapshot_create(c: Client, args) -> None:
    a = find_automation(c, args.automation)
    r = c.req("POST", f"/automations/{a['id']}/memory/snapshots",
              {"name": args.name} if args.name else {})
    print(f"snapshot {r.get('snapshot', {}).get('id', '')[:8]} created")


def cmd_snapshot_restore(c: Client, args) -> None:
    a = find_automation(c, args.automation)
    s = _find_snapshot(c, a, args.snapshot)
    c.req("POST", f"/automations/{a['id']}/memory/snapshots/{s['id']}/restore")
    print(f"restored snapshot {s['id'][:8]} (a pre-restore snapshot was taken first)")


def cmd_snapshot_delete(c: Client, args) -> None:
    a = find_automation(c, args.automation)
    s = _find_snapshot(c, a, args.snapshot)
    c.req("DELETE", f"/automations/{a['id']}/memory/snapshots/{s['id']}")
    print(f"deleted snapshot {s['id'][:8]}")


# ---------------------------------------------------------------- execution

def cmd_execution_list(c: Client, args) -> None:
    # §20: -n rides to the server as the §19 limit — only the printed rows
    # cross the wire.
    q = [f"limit={args.n}"]
    if args.automation:
        q.append(f"automation={find_automation(c, args.automation)['id']}")
    if args.status:
        q.append(f"status={args.status}")
    data = c.req("GET", "/executions?" + "&".join(q))
    if args.json:
        _pjson(data)
        return
    for e in data["executions"]:
        print(f"{e['started']:<22} {e['automationName']:<30} {e['versionLabel']:<6} {e['status']:<11} "
              f"{e['duration']:<8} {e['trigger']:<9} [{e['id'][:8]}]")


def cmd_execution_show(c: Client, args) -> None:
    e = find_execution(c, args.execution)
    full = c.req("GET", f"/executions/{e['id']}")
    if args.json:
        _pjson(full)
        return
    print(f"{full['automationName']} {full['versionLabel']} — {full['status']} in {full['duration']} "
          f"({full['trigger']}, {full['started']}) [{full['id']}]")
    p = full.get("triggerPayload")
    if p:
        # §20 trigger-message parity: kind-aware like the UI's TRIGGER MESSAGE
        # block — an iMessage payload has no channel; never print the secret.
        if p.get("channelName"):
            origin = f"#{p['channelName']}"
            if p.get("guildName"):
                origin += f" · {p['guildName']}"
        else:
            origin = p.get("channel")
        parts = [p["sender"], *([origin] if origin else []), p["at"]]
        print("trigger message: " + " · ".join(parts))
        print(f"  {p['text']}")
    for i, s in enumerate(full.get("steps") or [], 1):
        print(f"step {i}: {s['name']:<32} {s['status']:<11} {s.get('duration') or ''}")
    if full.get("error"):
        err = full["error"]
        print(f"error in {err['step']!r}: {err['message']}")
        if err.get("reason"):
            print(f"possible reason: {err['reason']}")
    r = full.get("result") or {}
    if r.get("chip"):
        print(f"result: {r['chip']}")
    for f in r.get("files") or []:
        print(f"file: {f['name']} ({f['size']})")


def cmd_execution_tail(c: Client, args) -> None:
    e = find_execution(c, args.execution)
    _exit_by_status(follow_exec(c, e["id"]))


def cmd_execution_cancel(c: Client, args) -> None:
    e = find_execution(c, args.execution)
    c.req("POST", f"/executions/{e['id']}/cancel")
    print(f"cancelled {e['id'][:8]}")


def cmd_execution_retry(c: Client, args) -> None:
    e = find_execution(c, args.execution)
    c.req("POST", f"/executions/{e['id']}/retry")
    print(f"retrying {e['id'][:8]} in place")
    if args.follow:
        _exit_by_status(follow_exec(c, e["id"]))


def cmd_execution_skip(c: Client, args) -> None:
    e = find_execution(c, args.execution)
    full = c.req("GET", f"/executions/{e['id']}")
    executing = [i for i, s in enumerate(full.get("steps") or [])
                 if s["status"] == "executing"]
    if not executing:
        sys.exit("no step is executing")
    c.req("POST", f"/executions/{e['id']}/skip-step", {"index": executing[0]})
    print(f"skipping step {executing[0] + 1}")


def cmd_execution_result(c: Client, args) -> None:
    e = find_execution(c, args.execution)
    if not args.name:
        full = c.req("GET", f"/executions/{e['id']}")
        for f in (full.get("result") or {}).get("files") or []:
            print(f"{f['name']} ({f['size']})")
        return
    from urllib.parse import quote

    # Encoded like memory show's path — a result file named with a space or
    # `#` (steps name these freely) must survive the request line.
    data = c.req_raw("GET", f"/executions/{e['id']}/result/{quote(args.name)}")
    try:
        sys.stdout.buffer.write(data)
    except BrokenPipeError:
        # Piped into `head` and the reader closed early — a normal end.
        sys.exit(0)


# ---------------------------------------------------------------- secret/agent

def cmd_secret_list(c: Client, args) -> None:
    secrets = c.req("GET", "/secrets")
    if args.json:
        _pjson(secrets)
        return
    for s in secrets:
        tag = "" if s.get("set", True) else " (not set)"
        # §4.8: usedBy entries are { id, name } — names are the display.
        used = ", ".join(u["name"] for u in s.get("usedBy") or []) or "not used yet"
        print(f"{s['name'] + tag:<28} used by: {used}")


def _secret_by_name(c: Client, name: str) -> dict | None:
    """§20: names are the CLI's secret surface; the API routes are id-keyed
    (§4.8) — resolve before calling them."""
    return next((s for s in c.req("GET", "/secrets") if s["name"] == name), None)


def cmd_secret_set(c: Client, args) -> None:
    # §20: a secret value never rides argv — it would land in shell history and
    # in every local process's view of the process list.
    value = sys.stdin.readline().rstrip("\n") if args.stdin \
        else getpass.getpass(f"value for {args.name}: ")
    if not value:
        # §20: errors go to stderr through sys.exit, like every other exit-1 path.
        sys.exit("no value given, nothing saved")
    # §20 upsert feel, CLI-side: an existing name edits via the id route,
    # a new one creates.
    existing = _secret_by_name(c, args.name)
    if existing:
        c.req("PUT", f"/secrets/{existing['id']}", {"value": value})
    else:
        c.req("POST", "/secrets", {"name": args.name, "value": value})
    print(f"saved to your {paths.secret_store_name()}")


def cmd_secret_delete(c: Client, args) -> None:
    if args.all:
        # §20: --all sweeps the whole store, so it takes no name and never
        # guesses — one or the other, and the destructive guard on top.
        if args.name:
            sys.exit("`secret delete --all` deletes every stored secret — "
                     f"drop the name {args.name!r} or the flag")
        if not args.yes:
            sys.exit("deleting every stored secret also removes each value from your "
                     f"{paths.secret_store_name()} — add --yes to confirm")
        deleted = c.req("DELETE", "/secrets")["deleted"]
        print(f"removed {deleted} secret(s) from your {paths.secret_store_name()}")
        return
    if not args.name:
        sys.exit("`secret delete` needs a secret name, or --all to delete every stored secret")
    existing = _secret_by_name(c, args.name)
    if existing is None:
        names = sorted(s["name"] for s in c.req("GET", "/secrets"))
        sys.exit(f"no stored secret named {args.name!r} — have: "
                 f"{', '.join(names) or '(none)'}")
    c.req("DELETE", f"/secrets/{existing['id']}")
    print(f"removed from your {paths.secret_store_name()}")


def cmd_agent_list(c: Client, args) -> None:
    agents = c.req("GET", "/agents")
    if args.json:
        _pjson(agents)
        return
    for a in agents:
        star = "*" if a.get("default") else " "
        print(f"{star} {(a.get('name') or a['harness']):<24} {a['harness']:<12} "
              f"{a['model'] or 'default model'}")


def cmd_agent_check(c: Client, args) -> None:
    agents = c.req("GET", "/agents")
    match = [a for a in agents
             if (a.get("name") or a["harness"]).lower() == args.agent.lower()]
    if not match:
        sys.exit(f"no agent named {args.agent!r} — have: "
                 f"{', '.join(a.get('name') or a['harness'] for a in agents) or '(none)'}")
    r = c.req("POST", f"/agents/{match[0]['id']}/check")
    print(json.dumps(r))


# ---------------------------------------------------------------- settings

def _menu_bar_icon_help() -> str:
    """§9 per-OS copy rule for the `settings set` help: the §13 surface is the
    menu bar on macOS and the tray on Windows; Linux has none, and the key
    still parses and stores there (§4.9) — say so."""
    surface = paths.tray_surface_name()
    if surface is None:
        return "show the tray icon (Linux has no tray, so this is ignored)"
    return f"show the {surface} icon"


SETTINGS_KEYS = {"login": bool, "menuBarIcon": bool, "keepAwake": bool, "automaticUpdateCheck": bool,
                 "notifications": str, "days": int, "keepForever": bool, "developerMode": bool,
                 "cliEnabled": bool}


def cmd_settings_show(c: Client, args) -> None:
    s = c.req("GET", "/settings")
    if args.json:
        _pjson(s)
        return
    for k, v in s.items():
        print(f"{k:<14} {v}")


def cmd_settings_set(c: Client, args) -> None:
    patch: dict = {}
    for item in args.values:
        k, sep, raw = item.partition("=")
        if not sep:
            sys.exit(f"expected KEY=VALUE, got {item!r}")
        if k == "dataPath":
            c.req("POST", "/settings/data-path", {"path": raw})
            print(f"execution data now at {raw}")
            continue
        if k not in SETTINGS_KEYS:
            sys.exit(f"unknown setting {k!r} — have: {', '.join(SETTINGS_KEYS)}, dataPath")
        kind = SETTINGS_KEYS[k]
        if kind is bool:
            low = raw.strip().lower()
            # Strict like `param set`'s toggle parse — a typo must not
            # silently become False.
            if low in ("on", "true", "1", "yes"):
                patch[k] = True
            elif low in ("off", "false", "0", "no"):
                patch[k] = False
            else:
                sys.exit(f"{k} takes on|off, got {raw!r}")
        elif kind is int:
            try:
                patch[k] = int(raw)
            except ValueError:
                sys.exit(f"{k} takes an integer, got {raw!r}")
        else:
            patch[k] = raw
    if patch:
        c.req("PATCH", "/settings", patch)
        print(f"set {', '.join(patch)}")


# ---------------------------------------------------------------- service

def cmd_service(_c, args) -> None:
    # Thin wrapper over the §3 registration entry (`python -m autowright.service`):
    # same ACTIONS, one registration path, and the same §3 exit codes via
    # service.result_code, so both entry points exit alike.
    out = service.ACTIONS[args.action]()
    print(out)
    if service.result_code(out):
        sys.exit(1)


# ---------------------------------------------------------------- parser

class Parser(argparse.ArgumentParser):
    """§20 exit codes: argparse exits 2 on a usage error, but 2 is reserved
    exclusively for "the followed execution didn't succeed", which a harness must
    be able to branch on. Usage errors take the ordinary error exit (1), with
    the usage line and the message on stderr like every other CLI error.
    Subparsers inherit this class (argparse's `parser_class` defaults to the
    parent's type), so the whole tree exits alike — and so every level of the
    tree gets the §20 expanded command listing from `format_help` below."""

    def error(self, message: str):
        self.print_usage(sys.stderr)
        sys.exit(f"{self.prog}: {message}")

    def format_help(self) -> str:
        """§20 expanded command listings: argparse's stock subcommand list is a
        bare name and one-liner, which leaves a command's flags reachable only by
        running its own `--help`. Swap it for the generated listing in
        `_commands_block` — usage line, description, and options are argparse's
        as usual, so only the listing itself is ours."""
        sub = next((a for a in self._actions
                    if isinstance(a, argparse._SubParsersAction)), None)
        if sub is None:
            return super().format_help()

        head = self._get_formatter()
        head.add_usage(self.usage, self._actions, self._mutually_exclusive_groups)
        head.add_text(self.description)

        tail = self._get_formatter()
        for group in self._action_groups:
            # The subparsers action is the listing — never also a bare row under
            # "positional arguments" — and -h is not news to someone who just
            # typed a command to find out what it does. Dropping both leaves
            # these parsers with an empty options section, which is then skipped.
            actions = [a for a in group._group_actions
                       if a is not sub and not isinstance(a, argparse._HelpAction)]
            if not actions:
                continue
            tail.start_section(group.title)
            tail.add_text(group.description)
            tail.add_arguments(actions)
            tail.end_section()
        tail.add_text(self.epilog)

        return head.format_help() + _commands_block(self, sub) + tail.format_help()


class Help(argparse.HelpFormatter):
    """§20 help text: wrap each paragraph of a description separately (argparse's
    default collapses blank lines, running the summary and the detail together),
    but leave an epilog's example block line-for-line as written. argparse's own
    RawDescriptionHelpFormatter is all-or-nothing — it would leave the prose
    unwrapped too — and `_fill_text` is the one hook they all run through, so the
    `Examples:` opener every example epilog carries is what tells them apart."""

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        if text.startswith("Examples:"):
            return "\n".join(indent + line for line in text.splitlines())
        fill = super()._fill_text
        return "\n\n".join(fill(p, width, indent) for p in text.split("\n\n"))


# ------------------------------------------------- §20 expanded command listing

def _summary(parser) -> str:
    """The first paragraph of a description — what a listing prints. The rest of
    the description is detail, kept for the command's own `--help`."""
    return (parser.description or "").split("\n\n")[0]


def _placeholder(action) -> str:
    """A positional's placeholder: its metavar in <angle brackets>, or the choices
    it accepts when it has none (`service`'s verb list)."""
    if action.choices and not action.metavar:
        return "<" + "|".join(str(c) for c in action.choices) + ">"
    name = action.metavar or action.dest
    return name if name.startswith("<") else f"<{name}>"


def _token(action) -> str:
    """One argument as it appears on a signature line."""
    if not action.option_strings:
        token = _placeholder(action)
        if action.nargs == "?":
            return f"[{token}]"
        if action.nargs == "+":
            return f"{token}..."
        if action.nargs == "*":
            return f"[{token}...]"
        return token
    flag = action.option_strings[0]
    if action.nargs == 0:  # store_true and friends take no value
        return f"[{flag}]"
    # A repeatable flag (the grant flags, --author) says so with an ellipsis.
    repeat = "..." if action.__class__.__name__ == "_AppendAction" else ""
    return f"[{flag} {action.metavar or action.dest.upper()}]{repeat}"


def _label(action) -> str:
    """One argument as it appears in the indented row under a signature line.
    A positional whose signature spells out its choices is named by its dest
    here — the vocabulary is already on the signature line, and repeating it
    would push every help string off the column."""
    if not action.option_strings:
        return f"<{action.dest}>" if action.choices and not action.metavar \
            else _placeholder(action)
    flags = ", ".join(action.option_strings)
    return flags if action.nargs == 0 else f"{flags} {action.metavar or action.dest.upper()}"


def _one_liners(sub) -> dict:
    """The `help` one-liner each subcommand was registered with — what a group
    lists its verbs by, one level down from the level being expanded."""
    return {a.dest: a.help or "" for a in sub._get_subactions()}


def _arguments(parser) -> list:
    """A command's own arguments, positionals first — the order they are typed
    in, which is not the order they were registered in (`--json` is added by
    `_sub` before the command's positionals exist)."""
    args = [a for a in parser._actions
            if not isinstance(a, (argparse._HelpAction, argparse._SubParsersAction))]
    return [a for a in args if not a.option_strings] + [a for a in args if a.option_strings]


def _wrap(text: str, width: int, indent: str, hanging: str | None = None) -> list[str]:
    return textwrap.wrap(text, max(width, len(indent) + 20), initial_indent=indent,
                         subsequent_indent=hanging if hanging is not None else indent) or []


def _commands_block(parser, sub) -> str:
    """§20: the generated listing that replaces argparse's name-and-one-liner
    list. Every entry carries its full signature — positionals in <angle
    brackets>, flags in [square brackets] — its summary, and one row per
    argument. A subcommand that is itself a group lists its verbs by name
    instead of expanding them, so each `--help` prints its own level in full and
    names the level below."""
    width = min(shutil.get_terminal_size().columns - 2, 96)
    title = ((sub.metavar or "command") + "s").upper()
    out = ["", f"{title}:"]

    for name, child in sub.choices.items():
        signature = " ".join([name] + [_token(a) for a in _arguments(child)])
        out += _wrap(signature, width, "  ", hanging="      ")
        out += _wrap(_summary(child), width, "      ")

        # A group lists its verbs by their one-liners; a leaf expands its own
        # arguments. Either way this level prints in full and names the next.
        nested = next((a for a in child._actions
                       if isinstance(a, argparse._SubParsersAction)), None)
        rows = (list(_one_liners(nested).items()) if nested else
                [(_label(a), a.help or "") for a in _arguments(child)])
        # §20: a blank line and a label keep the prose and the rows from reading
        # as one column, and name which kind of block this is.
        if rows:
            out += ["", f"      {'verbs' if nested else 'arguments'}:"]
        pad = min(max((len(label) for label, _ in rows), default=0), 24)
        for label, text in rows:
            gutter = " " * (10 + pad)
            if len(label) > pad:
                out += [f"        {label}"] + _wrap(text, width, gutter)
            else:
                first, *rest = _wrap(text, width, gutter) or [gutter]
                out += [f"        {label.ljust(pad)}  {first.strip()}"] + rest
        out.append("")

    out += _wrap(f"Run `{parser.prog} <{(sub.metavar or 'command').lower()}> --help` for the "
                 "full description, the accepted values, and examples.", width, "  ")
    return "\n".join(out) + "\n\n"


def _help_fn(parser):
    """§20: what a parser with no command of its own does — print its own help.
    `client` goes false alongside it, so a bare `autowright` answers on a machine
    whose backend is down, which is exactly when someone types it."""
    def show(_c, _args) -> None:
        parser.print_help()
    return show


def _sub(parent, name: str, fn, help: str, client: bool = True, json_flag: bool = False,
         description: str | None = None, epilog: str | None = None):
    # §20 help text: `help` is the one-liner the parent's command list shows;
    # `description` is the prose `<command> --help` prints, defaulting to it.
    p = parent.add_parser(name, help=help, description=description or help, epilog=epilog,
                          formatter_class=Help)
    # fn is None for a group — `autowright automation` prints the automation
    # listing instead of failing on the missing verb (§20 bare invocation).
    p.set_defaults(fn=fn or _help_fn(p), client=client and fn is not None)
    if json_flag:
        p.add_argument("--json", action="store_true",
                       help="print the raw API JSON instead of the human columns")
    return p


# §20 help text: the reference forms live on every positional that takes one,
# from one helper — the resolution rule is discoverable at the point of use.
_REF_FORMS = ("its name (case-insensitive), a unique part of its name, its id, or a unique "
              "id prefix — every [abcd1234] form these commands print resolves back")
# The compact form for the argument rows; the long form is stated once, in the
# description of each group whose verbs take one.
_REF = "which automation: its name, a unique part of its name, its id, or an id prefix"
_EXEC_REF = "which execution, by id or id prefix (default: the most recent one)"


def _ref(p) -> None:
    p.add_argument("automation", help=_REF)


def _exec_ref(p) -> None:
    p.add_argument("execution", nargs="?", help=_EXEC_REF)


# §20: the full surface is on. False registers only `service` (the group the
# packaged app shells out to at launch, §3 ensure-backend) — kept as a switch
# because the test suite exercises both parser shapes.
CLI_ENABLED = True

_DISABLED_NOTE = ("Automation commands are disabled in this release — "
                  "create, edit, and execute automations in the Autowright app.")


def _grant_flags(p) -> None:
    """§20 grant model: the explicit, repeatable grant flags on create/push."""
    p.add_argument("--grant-agent", action="append", default=[], metavar="NAME",
                   help="let the automation's steps use this configured agent, by agent "
                        "name (repeat the flag for several agents)")
    p.add_argument("--grant-secret", action="append", default=[], metavar="NAME",
                   help="let the automation's steps read this stored secret, by secret "
                        "name (repeat the flag for several secrets)")


def _add_service(top) -> None:
    p = _sub(top, "service", cmd_service, "start, stop, and register the background service",
             client=False,
             description="Control the background service that keeps Autowright running when "
                         "the app isn't open — the thing that fires your schedules and "
                         "watches for message triggers."
                         "\n\n"
                         "This is the only group that works with the backend down, so it is "
                         "where you start when another command says it can't reach one. "
                         "`status` reports whether the service is registered and running; "
                         "`install` registers it to come up at login and starts it now."
                         "\n\n"
                         "`stop` leaves it registered so it returns at the next login. "
                         "`uninstall` unregisters it entirely, and also removes the "
                         "`autowright` command from your PATH if Autowright was the one that "
                         "put it there, so run it from inside the app bundle if you mean to "
                         "carry on from a terminal. Automations, secrets, and execution "
                         "history are untouched by any of these.",
             epilog="Examples:\n"
                    "  autowright service status      is it registered and running?\n"
                    "  autowright service install     register it to start at login\n"
                    "  autowright service restart     stop it and start it again\n"
                    "  autowright service stop        stop it, leaving it registered\n"
                    "  autowright service uninstall   unregister it entirely")
    p.add_argument("action", choices=["install", "uninstall", "status", "restart", "stop"],
                   help="what to do with the service")


def build_parser(full: bool = CLI_ENABLED) -> argparse.ArgumentParser:
    ap = Parser(
        prog="autowright", formatter_class=Help,
        description=(
            "Autowright from the command line: the same automations, executions, secrets, "
            "agents, and settings the app shows, driven headlessly over the local backend. "
            "Authoring happens in a workdir — pull an automation into a directory, edit its "
            "spec, manifest, and step files with any editor, then push the directory back as "
            "a new version."
            "\n\n"
            "Every command talks to the Autowright backend running on this machine, so the "
            "backend has to be up: `autowright status` says whether it is, and the `service` "
            "group starts it. That group is the exception — it is the one that works with "
            "everything down."
            "\n\n"
            "Read verbs take --json, which prints the backend's own JSON instead of the human "
            "columns, so scripts and agents never have to parse prose."
            if full else
            # The disabled shape registers only `service`, so it must not promise
            # a surface that isn't there.
            "Autowright from the command line. This release registers only the service "
            "group — the background service that keeps your automations firing when the app "
            "isn't open."),
        # §20: a parser holding subcommands ends at its listing — no epilog above
        # a listing the reader hasn't reached yet.
        epilog=None if full else _DISABLED_NOTE)
    top = ap.add_subparsers(dest="cmd", required=False, metavar="COMMAND")
    # §20 bare invocation: `autowright` alone prints this help and exits 0,
    # rather than answering the question "what is this?" with a usage stub.
    ap.set_defaults(fn=_help_fn(ap), client=False)

    if not full:
        _add_service(top)
        return ap

    _sub(top, "status", cmd_status, "check the backend is up, with entity counts",
         json_flag=True,
         description="Print the backend's version and how many automations, executions, "
                     "agents, and secrets it holds. The quickest way to confirm the CLI can "
                     "reach a running Autowright before doing anything else."
                     "\n\n"
                     "It doubles as a health check in scripts: with no backend reachable it "
                     "exits 1 with a message pointing at the `service` group, rather than "
                     "printing counts.")
    _sub(top, "instructions", cmd_instructions,
         "print the framework instructions step code must follow", json_flag=True,
         description="Print the framework instructions verbatim — the contract step code is "
                     "written against: the step SDK, the imports steps may use, and the "
                     "policy sections the validators enforce. Read this before writing step "
                     "files by hand."
                     "\n\n"
                     "It is the same text the app gives its own authoring agents, and it ships "
                     "with the app rather than with your automations, so re-read it after an "
                     "update instead of working from a saved copy. --json prints the "
                     "framework and build instruction files together.")

    # ---- automation
    ag = _sub(top, "automation", None, "create, inspect, edit, and execute automations",
              description="Everything about automations. The authoring verbs work on a "
                          "workdir — a plain directory holding spec.md (the spec as "
                          "markdown), manifest.yaml (name, description, triggers, params, "
                          "packages, steps), and one NN-name.py file per step. Pull one out, "
                          "edit the files, push them back."
                          "\n\n"
                          "Wherever a verb takes an automation, name it by " + _REF_FORMS +
                          ". Saving never overwrites: push and create add a version, and the "
                          "old ones stay in the history where `restore` can bring them "
                          "back.").add_subparsers(dest="verb", required=False, metavar="VERB")
    _sub(ag, "list", cmd_automation_list, "list every automation on this machine",
         json_flag=True,
         description="One row per automation: its name, how it is triggered, the status of "
                     "its last execution, and its short id. A row carrying a `needs fixing` "
                     "marker has something stopping it from executing — `automation show` "
                     "spells out what."
                     "\n\n"
                     "The trigger column reads (off) when every one of an automation's "
                     "triggers is switched off, so it will only execute when you ask it to. "
                     "The short id in brackets is a real reference: pass it to any verb that "
                     "takes an automation.")
    p = _sub(ag, "show", cmd_automation_show,
             "print one automation in full: spec, steps, triggers, params", json_flag=True,
             description="One automation's whole record: what it does, every step, its "
                         "triggers, its parameters and their current values, the agents and "
                         "secrets it is allowed to use, its memory snapshots, and anything "
                         "that needs fixing before it can execute."
                         "\n\n"
                         "This is the read-only view. To change what an automation does, "
                         "`automation pull` it into a directory and push the edited files "
                         "back; to change a parameter value or a trigger without a new "
                         "version, use `param set` or the `trigger` verbs.")
    _ref(p)
    p = _sub(ag, "pull", cmd_automation_pull, "copy an automation into a directory to edit",
             description="Write an automation's current version into a directory as editable "
                         "files: spec.md, manifest.yaml, one NN-name.py per step, plus "
                         "instructions.md and notes.md when the version has them. Edit them "
                         "with anything, then `automation push` the directory back."
                         "\n\n"
                         "Parameter values are deliberately not written. A version describes "
                         "what an automation does; the values it does it with are operational "
                         "state you own, so they never round-trip through a version. Read and "
                         "change them with the `param` verbs instead."
                         "\n\n"
                         "A pull is a copy, not a lock: the automation can still execute, and "
                         "can still be edited in the app, while you have the files open. "
                         "Pushing saves your files as the next version whatever happened "
                         "meanwhile, so pull again if you have been away a while.",
             epilog="Examples:\n"
                    "  autowright automation pull \"daily report\"            into ./daily report\n"
                    "  autowright automation pull \"daily report\" ./report   into ./report")
    _ref(p)
    p.add_argument("dir", nargs="?",
                   help="directory to write into (default: a new directory named after the "
                        "automation, in the current directory)")
    p = _sub(ag, "push", cmd_automation_push, "save an edited directory as the next version",
             description="Validate a workdir and save it as this automation's next version. "
                         "The same checks the app runs when it builds an automation run "
                         "first: spec shape, parameter kinds and defaults, one step file per "
                         "declared step in order, Python that parses, only allowed imports, "
                         "timeout and trigger rules. Problems print one per line and nothing "
                         "is saved, so you can fix the files and push again."
                         "\n\n"
                         "Grants only ever grow here. If the edited steps use an agent or a "
                         "secret the automation isn't allowed yet, the save is refused and "
                         "the exact --grant flags to add are printed; passing them widens "
                         "what it may use. Nothing is ever revoked by a push — that is done "
                         "on the automation's edit page in the app."
                         "\n\n"
                         "The manifest's schedules replace the stored ones, matched on the "
                         "expression and timezone so an untouched manifest round-trips "
                         "unchanged. Message and app-start triggers, and anything you added "
                         "with `trigger add`, survive a push untouched. Packages the saved "
                         "version declares are installed afterwards, and an install that "
                         "fails warns without failing the save.",
             epilog="Examples:\n"
                    "  autowright automation push report ./report\n"
                    "  autowright automation push report ./report --note \"retry on 429\"\n"
                    "  autowright automation push report ./report --grant-secret MAIL_PASS")
    _ref(p)
    p.add_argument("dir", help="the workdir to validate and save")
    p.add_argument("--note", metavar="TEXT",
                   help="a short note describing this version, shown in the version history")
    _grant_flags(p)
    p = _sub(ag, "create", cmd_automation_create, "create a new automation from a directory",
             description="Validate a workdir and create a new automation from it, as version "
                         "1. The same checks `automation push` runs apply, and the same "
                         "nothing-is-written-on-failure rule."
                         "\n\n"
                         "Nothing is granted implicitly. If the steps use an agent or a "
                         "secret, grant it with --grant-agent / --grant-secret, or the save "
                         "is refused and the exact flags to add are printed. A step that "
                         "calls an agent without naming one runs on the first enabled agent, "
                         "so it still needs at least one --grant-agent. Granting something "
                         "the steps don't use yet is allowed."
                         "\n\n"
                         "The name comes from --name, then the manifest, then the directory's "
                         "own name; a name already taken gets a number appended rather than "
                         "colliding. The new automation's triggers arrive from the manifest "
                         "switched on, so check them with `trigger list` if you are not ready "
                         "for it to execute on its own.",
             epilog="Examples:\n"
                    "  autowright automation create ./report\n"
                    "  autowright automation create ./report --name \"Daily report\"\n"
                    "  autowright automation create ./report --grant-agent Coder "
                    "--grant-secret MAIL_PASS")
    p.add_argument("dir", help="the workdir to validate and create from")
    p.add_argument("--name", metavar="TEXT",
                   help="name the new automation (default: the manifest's name, then the "
                        "directory's name)")
    p.add_argument("--agent", metavar="NAME",
                   help="which configured agent is recorded as the author, by agent name")
    _grant_flags(p)
    p = _sub(ag, "delete", cmd_automation_delete, "delete an automation and everything it has",
             description="Delete an automation, every version of it, its memory, its memory "
                         "snapshots, and its whole execution history, including the result "
                         "files those executions produced. This cannot be undone, so it needs "
                         "--yes."
                         "\n\n"
                         "To keep a copy first, `automation export` it to a file — that "
                         "carries the automation itself, though not its history. If it is "
                         "executing when you delete it, the execution is stopped first, which "
                         "is why this command can take a moment to return.")
    _ref(p)
    p.add_argument("--yes", action="store_true",
                   help="required: confirms you mean to delete all of it")
    p = _sub(ag, "restore", cmd_automation_restore, "bring an old version back to the top",
             description="Copy an old version back to the top of the history as a new "
                         "version, making it the one that executes from now on."
                         "\n\n"
                         "Nothing is overwritten or lost: the version you were on stays in "
                         "the history, so a restore is itself undoable by restoring back. "
                         "`automation show` lists the versions you can name. Parameter "
                         "values, triggers, and memory are not part of a version and are left "
                         "exactly as they are.")
    _ref(p)
    p.add_argument("version", metavar="vN", help='the version to bring back, like "v3"')
    p = _sub(ag, "execute", cmd_automation_execute, "execute an automation right now",
             description="Execute an automation now, as if a trigger had fired, and print the "
                         "new execution's id. It runs in the background, so the command "
                         "returns immediately; `execution tail` picks up the logs later."
                         "\n\n"
                         "With --follow the logs stream here until it finishes and the exit "
                         "code reports the outcome: 0 if it succeeded, 2 if it ended any "
                         "other way. That is what scripts should branch on. Ctrl-C stops "
                         "watching without stopping the execution."
                         "\n\n"
                         "The current version runs, under the agents and secrets the "
                         "automation is already allowed — executing never widens that. "
                         "--version runs an older version or the unsaved draft this once, "
                         "changing nothing about the automation. If it is already executing, "
                         "the start is refused unless you pass --queue.",
             epilog="Examples:\n"
                    "  autowright automation execute report\n"
                    "  autowright automation execute report --follow\n"
                    "  autowright automation execute report --version v2\n"
                    "  autowright automation execute report --version draft\n"
                    "  autowright automation execute report --queue")
    _ref(p)
    p.add_argument("-f", "--follow", action="store_true",
                   help="stream the logs until it finishes, then exit 0 if it succeeded and "
                        "2 if it did not")
    p.add_argument("--version", metavar="vN|draft",
                   help='execute an old version or the unsaved draft this once, like "v2" or '
                        '"draft" — the automation itself is unchanged, and the grants stay '
                        'the ones it already has')
    p.add_argument("--queue", action="store_true",
                   help="if the automation is already busy, wait for a free slot instead of "
                        "refusing to start")
    p = _sub(ag, "export", cmd_automation_export, "write an automation to a shareable file",
             description="Write an automation to a portable .autowright file: its current "
                         "version, spec, steps, triggers, and parameter definitions, ready to "
                         "import on another machine or to keep as a backup."
                         "\n\n"
                         "Secret values are never exported. Only the names travel, so the "
                         "machine importing it matches them against its own secrets and tells "
                         "you what it could not find. The same goes for agents."
                         "\n\n"
                         "Your parameter values do travel, so that an export works on "
                         "arrival — pass --no-values when the file is going to someone else "
                         "and the values are yours alone. Execution history and memory are "
                         "never included.",
             epilog="Examples:\n"
                    "  autowright automation export report\n"
                    "  autowright automation export report ~/Desktop/report.autowright\n"
                    "  autowright automation export report --no-values")
    _ref(p)
    p.add_argument("path", nargs="?",
                   help="file to write (default: <automation name>.autowright, in the "
                        "current directory)")
    p.add_argument("--no-values", action="store_true",
                   help="export the parameter definitions without your values")
    p = _sub(ag, "import", cmd_automation_import, "import an automation from a file or link",
             description="Import an automation someone exported, from a file on disk or "
                         "straight from a link. Typing the command is taken as your go-ahead, "
                         "so it imports without asking to confirm."
                         "\n\n"
                         "The agents and secrets the archive names are matched against what "
                         "this machine has, by name. Matches are printed, and anything "
                         "unmatched is listed for you to fix in the app before the automation "
                         "can execute. A name already in use here gets a number appended "
                         "rather than replacing what you have, and an automation built on a "
                         "different operating system says so, because its steps may need "
                         "rewriting."
                         "\n\n"
                         "Its triggers arrive switched off, so nothing starts executing until "
                         "you turn them on with `trigger on`. Packages it declares are "
                         "installed afterwards.",
             epilog="Examples:\n"
                    "  autowright automation import ./report.autowright\n"
                    "  autowright automation import https://example.com/report.autowright\n"
                    "  autowright automation import https://github.com/someone/some-repo")
    p.add_argument("path", metavar="file-or-link",
                   help="a .autowright file on disk, a direct https link to one, or a "
                        "github.com repository or release page holding one")

    pg = _sub(ag, "param", None, "read and set an automation's parameter values",
              description="Parameters are the values an automation's steps read when they "
                          "execute — an address to send to, how many items to fetch. The "
                          "automation's version defines them; the values are yours, so they "
                          "stay put across new versions and never travel in an "
                          "export.").add_subparsers(dest="verb2", required=False,
                                                    metavar="VERB")
    p = _sub(pg, "list", cmd_param_list, "list the parameters and their current values",
             json_flag=True,
             description="One row per parameter: its name, its kind, and the value in effect "
                         "right now."
                         "\n\n"
                         "A parameter with no value of its own shows the default its version "
                         "declared. The kind column is what `param set` parses against.")
    _ref(p)
    p = _sub(pg, "set", cmd_param_set, "set one or more parameter values",
             description="Set parameter values. They take effect on the next execution, "
                         "including one a trigger starts, and stay set across new versions."
                         "\n\n"
                         "Each value is read according to that parameter's kind, listed "
                         "below. A name no parameter has, or a value that doesn't fit its "
                         "kind, is refused naming the form it wanted, and nothing in the "
                         "command is applied.",
             epilog="Examples:\n"
                    "  autowright automation param set report RECIPIENT=me@example.com\n"
                    "  autowright automation param set report RETRIES=3 VERBOSE=on\n"
                    "  autowright automation param set report TAGS=urgent,daily\n"
                    "  autowright automation param set report HEADERS='{\"X-Key\": \"abc\"}'\n"
                    "\n"
                    "What each kind accepts:\n"
                    "  toggle   on | off | true | false\n"
                    "  number   a whole number\n"
                    "  text     the string as typed\n"
                    "  list     comma-separated values, or a JSON array\n"
                    "  kv       k=v,k=v pairs, or a JSON object")
    _ref(p)
    p.add_argument("values", nargs="+", metavar="NAME=VALUE",
                   help="one or more parameter assignments, as printed by `param list`")

    tg = _sub(ag, "trigger", None, "list and edit what makes an automation execute",
              description="Triggers are what start an automation on their own: a schedule, a "
                          "one-off time, every app launch, or an incoming Discord or iMessage "
                          "message. An automation with no triggers executes only when you ask "
                          "it to. The verbs below take the 1-based numbers `trigger list` "
                          "prints.").add_subparsers(dest="verb2", required=False,
                                                    metavar="VERB")
    p = _sub(tg, "list", cmd_trigger_list, "list the triggers, numbered", json_flag=True,
             description="Every trigger this automation has, numbered, in plain words, with "
                         "(off) on any that are switched off."
                         "\n\n"
                         "Those numbers are what `trigger on`, `trigger off`, and `trigger "
                         "remove` take, and they renumber when one is removed, so list again "
                         "between edits. An automation with no triggers executes only when "
                         "you ask it to.")
    _ref(p)
    p = _sub(tg, "add", cmd_trigger_add, "add a trigger",
             description="Add a trigger. With no flags, the argument is a cron expression and "
                         "you get a repeating schedule. --at makes it happen once at a given "
                         "time, --app-start every time the app launches, and --discord / "
                         "--imessage make an incoming message start it."
                         "\n\n"
                         "New triggers arrive switched on, so an automation can start "
                         "executing as soon as this returns. Schedules and one-off times run "
                         "in this machine's timezone unless --timezone says otherwise, and a "
                         "one-off time in the past is refused."
                         "\n\n"
                         "A trigger added here belongs to you, not to the automation's "
                         "version: pushing a new version leaves it alone, where a schedule "
                         "written into the manifest is replaced by what the manifest says.",
             epilog="Examples:\n"
                    "  autowright automation trigger add report \"0 8 * * *\"\n"
                    "      every day at 08:00\n"
                    "  autowright automation trigger add report \"0 8 * * 1-5\" "
                    "--timezone Europe/Berlin\n"
                    "      weekdays at 08:00 Berlin time\n"
                    "  autowright automation trigger add report --at 2026-09-01T09:00\n"
                    "      once, then never again\n"
                    "  autowright automation trigger add report --app-start\n"
                    "      every time Autowright launches\n"
                    "  autowright automation trigger add report --discord 1234567890 "
                    "--secret DISCORD_TOKEN --mention\n"
                    "      when someone mentions the bot in that channel\n"
                    "  autowright automation trigger add report --imessage +15551234567 "
                    "--pattern status\n"
                    "      when that number texts something containing \"status\"")
    _ref(p)
    p.add_argument("expression", nargs="?", metavar="cron",
                   help='a cron expression for a repeating schedule, like "0 8 * * *" for '
                        "every day at 08:00 (leave it out when using one of the flags below)")
    p.add_argument("--at", metavar="TIME",
                   help='run once at this local time, then never again — "2026-09-01T09:00"')
    p.add_argument("--app-start", action="store_true",
                   help="run every time the Autowright app launches")
    p.add_argument("--discord", metavar="CHANNEL_ID",
                   help="run when a message arrives in this Discord channel, by numeric "
                        "channel id (needs --secret)")
    p.add_argument("--secret", metavar="NAME",
                   help="with --discord: the name of the stored secret holding the bot token")
    p.add_argument("--imessage", metavar="FROM",
                   help="run when this person sends an iMessage — a phone number in "
                        "+15551234567 form, or an email address")
    p.add_argument("--pattern", metavar="TEXT",
                   help="with --discord or --imessage: only run when the message contains "
                        "this text")
    p.add_argument("--mention", action="store_true",
                   help="with --discord: only run when the message mentions the bot")
    p.add_argument("--author", metavar="USER_ID", action="append",
                   help="with --discord: only run for these senders, by numeric user id like "
                        "234567890123456789 (repeat the flag, or comma-separate several)")
    p.add_argument("--timezone", metavar="ZONE",
                   help='which timezone the schedule or one-off time is in, as an IANA zone '
                        'like "Europe/Berlin" (default: this machine\'s timezone)')
    p.add_argument("--no-run-if-missed", action="store_true",
                   help="with a cron schedule or --at: if this machine sleeps through the "
                        "scheduled time, skip it instead of running once on wake "
                        "(default: run once on wake)")
    p = _sub(tg, "on", cmd_trigger_toggle, "switch a trigger back on",
             description="Switch a trigger back on, so it starts firing again. The trigger "
                         "itself is unchanged — this is the exact reverse of `trigger off`."
                         "\n\n"
                         "A schedule that came due while it was switched off does not fire "
                         "retroactively; the next matching time is the next one it fires.")
    _ref(p)
    p.add_argument("index", metavar="N",
                   help="which trigger, by the number `trigger list` prints")
    p.set_defaults(fn_enabled=True)
    p = _sub(tg, "off", cmd_trigger_toggle, "switch a trigger off without removing it",
             description="Stop a trigger from firing, keeping it in the list so you can "
                         "switch it back on later with `trigger on`."
                         "\n\n"
                         "This is the safe way to pause an automation you are editing, or one "
                         "that is failing: it stays exactly as it is, and you can still "
                         "execute it by hand while it is off. Switching every trigger off is "
                         "what puts (off) beside it in `automation list`.")
    _ref(p)
    p.add_argument("index", metavar="N",
                   help="which trigger, by the number `trigger list` prints")
    p.set_defaults(fn_enabled=False)
    p = _sub(tg, "remove", cmd_trigger_remove, "delete a trigger",
             description="Remove a trigger from the automation for good. To stop it "
                         "temporarily instead, use `trigger off`."
                         "\n\n"
                         "The remaining triggers renumber immediately, so run `trigger list` "
                         "again before removing another. Removing a schedule the automation's "
                         "manifest declares only lasts until the next push, which puts the "
                         "manifest's schedules back.")
    _ref(p)
    p.add_argument("index", metavar="N",
                   help="which trigger, by the number `trigger list` prints")

    mg = _sub(ag, "memory", None, "read and clear what an automation remembers",
              description="Each automation has its own folder of files that survive between "
                          "executions — where it got to last time, what it has already seen. "
                          "The steps write it; these verbs let you read it and wipe it. It is "
                          "never sent to an AI agent while an automation is being "
                          "written.").add_subparsers(dest="verb2", required=False,
                                                     metavar="VERB")
    p = _sub(mg, "show", cmd_memory_show, "list the memory files, or print one of them",
             json_flag=True,
             description="With no file, list what is in the automation's memory: each file's "
                         "path, size, and when it last changed. With a file, print that "
                         "file's text."
                         "\n\n"
                         "Read-only either way, and safe to run while the automation is "
                         "executing — a read never catches a half-written file. A file that "
                         "isn't text is not printed; the message points at where it lives on "
                         "disk instead.")
    _ref(p)
    p.add_argument("file", nargs="?",
                   help="which file to print, by the path `memory show` lists (default: list "
                        "the files instead of printing one)")
    p = _sub(mg, "clear", cmd_memory_clear, "empty an automation's memory",
             description="Delete everything in the automation's memory, so its next execution "
                         "starts with nothing remembered — a fresh start for one that has got "
                         "itself into a bad state."
                         "\n\n"
                         "A snapshot is taken first automatically whenever there was anything "
                         "to clear, so `snapshot list` and `snapshot restore` can bring it "
                         "back. Expect the automation to redo work it had already done.")
    _ref(p)

    sg = _sub(ag, "snapshot", None, "save and restore copies of an automation's memory",
              description="A snapshot is a copy of an automation's memory as it was at one "
                          "moment. Take one before anything risky; restore it to put the "
                          "memory back the way it was. Restoring snapshots the current memory "
                          "first, so it is itself undoable.").add_subparsers(
        dest="verb2", required=False, metavar="VERB")
    p = _sub(sg, "list", cmd_snapshot_list, "list the memory snapshots", json_flag=True,
             description="Every snapshot of this automation's memory: short id, when it was "
                         "taken, why, which version was current, its size, and its label."
                         "\n\n"
                         "Those short ids are what `snapshot restore` and `snapshot delete` "
                         "take. The reason column says whether you asked for it or the app "
                         "took it on your behalf before clearing or restoring.")
    _ref(p)
    p = _sub(sg, "create", cmd_snapshot_create, "snapshot the memory as it is now",
             description="Copy the automation's memory as it stands right now, so you can put "
                         "it back later. Worth doing before a version that changes how the "
                         "automation uses its memory."
                         "\n\n"
                         "Give it a --name to recognize it in the list; without one it is "
                         "identified by its id and the time it was taken.")
    _ref(p)
    p.add_argument("--name", metavar="TEXT",
                   help="a label to recognize this snapshot by later")
    p = _sub(sg, "restore", cmd_snapshot_restore, "put the memory back to a snapshot",
             description="Replace the automation's memory with a snapshot's contents, putting "
                         "it back the way it was when the snapshot was taken."
                         "\n\n"
                         "The memory as it is now is snapshotted first, so this is undoable "
                         "too. Nothing else about the automation changes: the version, its "
                         "triggers, and its parameter values are all left alone.")
    _ref(p)
    p.add_argument("snapshot", help="which snapshot, by the short id `snapshot list` prints")
    p = _sub(sg, "delete", cmd_snapshot_delete, "delete a memory snapshot",
             description="Delete one snapshot for good."
                         "\n\n"
                         "The automation's current memory is untouched — this only removes "
                         "the saved copy, and the other snapshots stay where they are.")
    _ref(p)
    p.add_argument("snapshot", help="which snapshot, by the short id `snapshot list` prints")

    # ---- execution
    eg = _sub(top, "execution", None, "watch and control individual executions",
              description="An execution is one run of one automation — started by a trigger, "
                          "by the app, or by `automation execute`. These verbs let you see "
                          "what ran, watch what is running, and step in while it does. The "
                          "ones that act on a single execution take its id, or default to the "
                          "most recent execution when you leave it "
                          "out.").add_subparsers(dest="verb", required=False, metavar="VERB")
    p = _sub(eg, "list", cmd_execution_list, "list recent executions, newest first",
             json_flag=True,
             description="Recent executions, newest first: when each started, which "
                         "automation and version ran, how it ended, how long it took, what "
                         "started it, and its short id."
                         "\n\n"
                         "Every automation's executions are mixed together unless you narrow "
                         "with --automation. The short ids are real references: pass one to "
                         "`execution show`, `tail`, `retry`, or `result`."
                         "\n\n"
                         "How far back this reaches depends on your retention setting — see "
                         "`settings show`, keys days and keepForever.",
             epilog="Examples:\n"
                    "  autowright execution list\n"
                    "  autowright execution list -n 50\n"
                    "  autowright execution list --automation report\n"
                    "  autowright execution list --status failed")
    p.add_argument("-n", type=int, default=20, metavar="COUNT",
                   help="how many to print (default: 20)")
    p.add_argument("--automation", metavar="AUTOMATION",
                   help="only executions of one automation, named as anywhere else: its "
                        "name, a unique part of its name, its id, or an id prefix")
    p.add_argument("--status", metavar="STATUS",
                   choices=["queued", "executing", "succeeded", "failed",
                            "cancelled", "skipped", "interrupted", "finished"],
                   help="only executions in this state: queued (waiting for a free slot), "
                        "executing, succeeded, failed, cancelled, skipped, interrupted, "
                        "or finished (any of the last five)")
    p = _sub(eg, "show", cmd_execution_show, "print one execution's steps, error, and result",
             json_flag=True,
             description="One execution in detail: how it ended and how long it took, every "
                         "step with its own status and duration, the error if it failed, the "
                         "message that started it if a message did, and the files it "
                         "produced."
                         "\n\n"
                         "This is the record, not the log. For the log lines a step printed "
                         "as it ran, use `execution tail`; for a file it produced, "
                         "`execution result`. A Discord or iMessage trigger's message is "
                         "shown with its sender and time, never the token behind it.")
    _exec_ref(p)
    p = _sub(eg, "tail", cmd_execution_tail, "follow an execution's logs as it runs",
             description="Stream an execution's logs until it finishes, then print how it "
                         "ended. On one that has already finished it prints the whole log and "
                         "returns, so it works as a log reader too."
                         "\n\n"
                         "It exits 0 if the execution succeeded and 2 if it ended any other "
                         "way, so a script can branch on the exit code without reading the "
                         "output. An execution still waiting for a free slot is followed "
                         "through to its real ending, not reported the moment it starts."
                         "\n\n"
                         "Ctrl-C stops watching and exits 1. It does not stop the execution — "
                         "`execution cancel` does that.")
    _exec_ref(p)
    p = _sub(eg, "cancel", cmd_execution_cancel, "stop an execution that is running",
             description="Stop a running execution. The step it is on is killed, along with "
                         "any process that step started, and the steps after it never run."
                         "\n\n"
                         "Steps that already finished keep their results, and the execution "
                         "is recorded as cancelled so you can see later why it stopped short. "
                         "To skip past one stuck step and let the rest of the automation "
                         "continue, use `execution skip` instead. A cancel that arrives after "
                         "the last step has finished changes nothing.")
    _exec_ref(p)
    p = _sub(eg, "retry", cmd_execution_retry, "run a failed execution's unfinished steps again",
             description="Pick a failed execution back up in place: the steps that already "
                         "succeeded keep their results and are not re-run, and everything "
                         "from the failure onward runs again."
                         "\n\n"
                         "This continues the same execution rather than starting a new one, "
                         "so its id and its history stay the same and the earlier attempts "
                         "remain on the record. Use it after fixing whatever the step needed "
                         "— a secret, a parameter value, something the machine was missing. "
                         "To run the whole automation from the top instead, use `automation "
                         "execute`."
                         "\n\n"
                         "With --follow the logs stream here and the exit code reports the "
                         "outcome, exactly as `automation execute --follow` does.")
    _exec_ref(p)
    p.add_argument("-f", "--follow", action="store_true",
                   help="stream the logs until it finishes, then exit 0 if it succeeded and "
                        "2 if it did not")
    p = _sub(eg, "skip", cmd_execution_skip, "give up on the current step and move on",
             description="Abandon the step a running execution is stuck on and let it "
                         "continue with the next one. Use it when one step is hanging on "
                         "something that isn't coming and the rest is still worth running."
                         "\n\n"
                         "The skipped step produces no result, and any step after it that "
                         "needed that result will likely fail. A step waiting to retry is "
                         "skipped just the same. If the step finishes on its own before the "
                         "skip lands, nothing is skipped and its result stands.")
    _exec_ref(p)
    p = _sub(eg, "result", cmd_execution_result,
             "list the files an execution produced, or print one",
             description="With no file name, list the files this execution produced, with "
                         "their sizes. With a name, write that file to stdout."
                         "\n\n"
                         "The file goes out byte for byte, binary included, so redirect to a "
                         "file for anything that isn't text. This is how you get an "
                         "execution's output out of Autowright and into the rest of a shell "
                         "pipeline.",
             epilog="Examples:\n"
                    "  autowright execution result                       "
                    "list the latest run's files\n"
                    "  autowright execution result a1b2c3d4              list that run's files\n"
                    "  autowright execution result a1b2c3d4 report.csv   print one\n"
                    "  autowright execution result a1b2c3d4 chart.png > chart.png")
    _exec_ref(p)
    p.add_argument("name", nargs="?",
                   help="which file to print, by the name `execution result` lists (default: "
                        "list the files instead of printing one)")

    # ---- secret / agent / settings / service
    store_name = paths.secret_store_name()
    scg = _sub(top, "secret", None, "store the passwords and keys your automations use",
               description="Secrets are the passwords, tokens, and API keys automations need. "
                           f"Values live in your {store_name}, and an automation can only "
                           "read the ones it has been granted. A value is never printed, "
                           "logged, or passed on a command line — only the names "
                           "are.").add_subparsers(dest="verb", required=False, metavar="VERB")
    _sub(scg, "list", cmd_secret_list, "list the stored secret names", json_flag=True,
         description="Every stored secret by name, with the automations allowed to use it."
                     "\n\n"
                     "Values are never shown, by this or any other command. A secret marked "
                     "(not set) has a name and grants but no value behind it yet, which is "
                     "what an automation imported from elsewhere leaves you to fill in. "
                     "\"not used yet\" means no automation has been granted it.")
    p = _sub(scg, "set", cmd_secret_set, f"store or replace a secret in your {store_name}",
             description="Store a secret under this name, or replace the value if the name is "
                         "already taken."
                         "\n\n"
                         "The value never goes on the command line, where it would land in "
                         "your shell history and be visible to every process on the machine. "
                         "It is asked for without echoing, or read from standard input with "
                         "--stdin for scripts and pipes."
                         "\n\n"
                         "Storing a secret does not let anything use it: an automation reads "
                         "it only once it has been granted, with --grant-secret on "
                         "`automation create` or `automation push`, or on the automation's "
                         "edit page in the app.",
             epilog="Examples:\n"
                    "  autowright secret set MAIL_PASS                     asks for the value\n"
                    "  pbpaste | autowright secret set MAIL_PASS --stdin   from the clipboard\n"
                    "  autowright secret set MAIL_PASS --stdin < key.txt   from a file")
    p.add_argument("name", help="the name steps refer to this secret by, like MAIL_PASS")
    p.add_argument("--stdin", action="store_true",
                   help="read the value from standard input instead of asking for it, for "
                        "scripts and pipes")
    p = _sub(scg, "delete", cmd_secret_delete, "remove a stored secret",
             description="Remove a secret and its value."
                         "\n\n"
                         "Automations granted it keep the grant but find nothing behind it, "
                         "so a step that needs it will fail on its next execution. Nothing "
                         "warns you first; check `secret list` for what uses it."
                         "\n\n"
                         f"--all removes every Autowright secret from your {store_name} at "
                         "once. That is not undoable and so needs --yes; with `service stop` "
                         "it is how you clear this machine down from a terminal.",
             epilog="Examples:\n"
                    "  autowright secret delete MAIL_PASS\n"
                    "  autowright secret delete --all --yes")
    p.add_argument("name", nargs="?", help="which secret, by name (leave out with --all)")
    p.add_argument("--all", action="store_true",
                   help="delete every stored secret instead of one — takes no name, and "
                        "needs --yes")
    p.add_argument("--yes", action="store_true",
                   help="required with --all: confirms you mean to delete all of them")

    agg = _sub(top, "agent", None, "inspect the AI agents configured in the app",
               description="Agents are the AI coding tools Autowright drives — the ones that "
                           "write automations for you, and the ones your steps can call. "
                           "Adding, editing, and signing into them is done in the app; from "
                           "here you can see what is configured and whether it is ready to "
                           "run.").add_subparsers(dest="verb", required=False, metavar="VERB")
    _sub(agg, "list", cmd_agent_list, "list the configured agents", json_flag=True,
         description="Every configured agent: its name, which tool it drives, and which model "
                     "it uses."
                     "\n\n"
                     "The default agent is marked with a *; that is the one a step gets when "
                     "it asks for an agent without naming one. \"default model\" means "
                     "Autowright passes no model at all and the tool uses whatever it is set "
                     "up with. These names are what --grant-agent and `agent check` take.")
    p = _sub(agg, "check", cmd_agent_check, "check whether an agent is ready to run",
             description="Ask an agent whether it can actually run right now: its tool "
                         "installed and reachable, and signed in where that is needed."
                         "\n\n"
                         "This is the first thing to try when an automation fails on a step "
                         "that uses an agent. It reports what it found as JSON. Fixing what "
                         "it reports — installing the tool, signing in, choosing another "
                         "model — is done in the app.")
    p.add_argument("agent", help="which agent, by the name `agent list` prints")

    stg = _sub(top, "settings", None, "read and change the app's settings",
               description="The same settings the app's Settings page shows, readable and "
                           "settable from here so a headless machine can be configured "
                           "without opening the app. Changes apply "
                           "live.").add_subparsers(dest="verb", required=False, metavar="VERB")
    _sub(stg, "show", cmd_settings_show, "print the current settings", json_flag=True,
         description="Every setting and its current value, one per line."
                     "\n\n"
                     "These are the keys `settings set` takes, plus two it does not: dataSize "
                     "is how much execution data is on disk, and appPath is where automations "
                     "and settings live, which is fixed.")
    p = _sub(stg, "set", cmd_settings_set, "change one or more settings",
             description="Change settings. They apply immediately, with no restart, and the "
                         "app picks them up whether or not it is open."
                         "\n\n"
                         "Several can be set in one command. An unrecognized key, or a value "
                         "that doesn't fit its setting, is refused and the ordinary settings "
                         "in that command are left unchanged. Booleans take on or off "
                         "(true/false, 1/0, and yes/no work too); a typo is rejected rather "
                         "than quietly read as off.",
             epilog="Examples:\n"
                    "  autowright settings set login=on\n"
                    "  autowright settings set days=30 keepForever=off\n"
                    "  autowright settings set notifications=all\n"
                    "  autowright settings set dataPath=/Volumes/Work/autowright\n"
                    "\n"
                    "Settings and what they take:\n"
                    "  login=on|off                 start Autowright when you log in\n"
                    f"  menuBarIcon=on|off           {_menu_bar_icon_help()}\n"
                    "  keepAwake=on|off             stop this machine sleeping, so schedules\n"
                    "                               keep firing (the display can still sleep);\n"
                    "                               works best on an always-on desktop; a\n"
                    "                               laptop that is asleep would not trigger\n"
                    "                               automations\n"
                    "  automaticUpdateCheck=on|off  check once a day for a newer version\n"
                    "  notifications=attention|all  notify only when something needs you, or\n"
                    "                               after every execution\n"
                    "  days=N                       how long to keep execution history, in\n"
                    "                               days (at least 1, default 90)\n"
                    "  keepForever=on|off           keep execution history forever, ignoring\n"
                    "                               days\n"
                    "  developerMode=on|off         log every backend and AI request,\n"
                    "                               prompts included, to the backend log\n"
                    "  cliEnabled=on|off            whether you want this `autowright`\n"
                    "                               command available\n"
                    "  dataPath=PATH                where execution data is stored; the old\n"
                    "                               directory is left where it is")
    p.add_argument("values", nargs="+", metavar="KEY=VALUE",
                   help="one or more settings to change, by the key names `settings show` "
                        "prints")

    _add_service(top)
    return ap


def main() -> None:
    args = build_parser().parse_args()
    c = Client() if args.client else None
    try:
        args.fn(c, args)
    except KeyboardInterrupt:
        # §20/§3 guidance style: Ctrl-C anywhere (a slow delete, a hung
        # request) is a quiet exit 1, never a traceback. The follow loop's
        # own handler still exits with the execution's code first.
        sys.exit(1)


if __name__ == "__main__":
    main()
