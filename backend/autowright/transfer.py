"""Transfer archives (§5.1): export an automation to a `.autowright` zip and
import one on any machine.

References plus safe metadata travel; credentials, grants, uuids, and local
state never do. Import validates the whole archive first (`TransferError` →
§19 422) and writes nothing on failure.
"""
from __future__ import annotations

import ast
import http.client
import io
import json
import logging
import re
import urllib.error
import urllib.request
import zipfile
import zlib
from datetime import datetime
from urllib.parse import urlsplit

import yaml

from . import __version__, harness, paths, timefmt, triggers as triggerlib
from .drafting import STEP_FILE_RE
from .specmd import blocks_to_md, md_to_blocks
from .storage import AGENT_REF_RE, SECRET_REF_RE, Store, new_id, strip_param_values

FORMAT_VERSION = 2
SECRET_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
# §5.1 identity translation: inside an archive, numeric REFS are the reference
# format (uuids are install-local and never travel; names ride agents.yaml /
# secrets.yaml as match material). Export rewrites the §4.1/§6.1 id
# references - step `agents:`/`secrets:` entries, the `secrets["<id>"]` /
# `agents["<id>"]` code subscripts, and a discord trigger's `secret` - to
# refs; import resolves each ref through the §5.1 match ladders and rewrites
# it to the matched local record's id, or to a freshly minted unresolved id.
# These regexes match the archive's ref-form code subscripts; the _ANY_
# variants match every subscript key, so validation catches uuid-form and
# name-form leftovers in one scan.
ARCHIVE_SECRET_REF_RE = re.compile(r"\bsecrets\[\s*[\"']([0-9]+)[\"']\s*\]")
ARCHIVE_AGENT_REF_RE = re.compile(r"\bagents\[\s*[\"']([0-9]+)[\"']\s*\]")
_ANY_SECRET_SUBSCRIPT_RE = re.compile(r"\bsecrets\[\s*[\"']([^\"'\n]*)[\"']\s*\]")
_ANY_AGENT_SUBSCRIPT_RE = re.compile(r"\bagents\[\s*[\"']([^\"'\n]*)[\"']\s*\]")
# §5.1 ref rule: decimal strings assigned per kind in listing order; a
# hand-written YAML integer reads as its string form.
_REF_RE = re.compile(r"^[0-9]+$")
PARAM_KINDS = ("text", "number", "toggle", "list", "kv")
MODES = ("default", "ollama", "custom")
# Any well-formed uuid passes the §4.3 field validator — the archive form
# carries the secret's NAME instead, checked separately (§5.1).
_PROBE_SECRET_ID = "00000000-0000-4000-8000-000000000000"


class TransferError(Exception):
    """Archive rejected — the message is the §19 422 detail."""


def safe_filename(name: str) -> str:
    """§19: the automation name sanitized for a filesystem filename."""
    cleaned = re.sub(r'[/\\:*?"<>|\x00-\x1f]+', " ", name).strip().strip(".")
    return cleaned or "automation"


# ---------- export ----------
class _ExportRefs:
    """§5.1 export-side id → ref translation. Every resolution failure is a
    TransferError naming what dangles — a reference with no stored record must
    be repaired before the automation can travel (§5.1). Refs are assigned per
    kind in listing order once the referenced sets are collected."""

    def __init__(self, store: Store, unresolved: dict | None = None):
        self.secrets_by_id = {s["id"]: s for s in store.secrets}
        self.agents_by_id = {g["id"]: g for g in store.agents}
        # §5.1/§4.1: the automation's unresolved_references map — a dangling id
        # it carries gets the import's own error copy, not the deleted-record
        # wording (the record never existed on this machine).
        self.unresolved = unresolved or {}
        self.secret_ref_by_id: dict[str, str] = {}
        self.agent_ref_by_id: dict[str, str] = {}

    def _dangling(self, kind: str, rid: str, where: str, noun: str) -> TransferError:
        entry = self.unresolved.get(rid)
        if entry and entry.get("kind") == kind:
            return TransferError(
                f"{where} still uses {entry['name']} from the imported file, which has "
                f"no match on this {paths.machine_noun()} - fix it in the editor "
                "before exporting")
        return TransferError(f"{where} references {noun} that no longer exists "
                             f"({rid[:8]}…) — repair it before exporting")

    def secret_record(self, sid: str, where: str) -> dict:
        s = self.secrets_by_id.get(sid)
        if s is None:
            raise self._dangling("secret", sid, where, "a secret")
        return s

    def agent_record(self, aid: str, where: str) -> dict:
        g = self.agents_by_id.get(aid)
        if g is None:
            raise self._dangling("agent", aid, where, "an agent")
        return g

    def assign(self, secret_ids: list[str], agent_ids: list[str]) -> None:
        self.secret_ref_by_id = {sid: str(i) for i, sid in enumerate(secret_ids, 1)}
        self.agent_ref_by_id = {aid: str(i) for i, aid in enumerate(agent_ids, 1)}

    def code(self, code: str) -> str:
        """Rewrite the §6.1 id subscripts to the archive's ref form. Existing
        trailing `# NAME` comments travel verbatim — they name the record's
        name at export time (import rewrites them after a renaming match)."""
        code = SECRET_REF_RE.sub(
            lambda m: f'secrets["{self.secret_ref_by_id[m.group(1)]}"]', code)
        return AGENT_REF_RE.sub(
            lambda m: f'agents["{self.agent_ref_by_id[m.group(1)]}"]', code)


def _referenced_secret_ids(refs: _ExportRefs, ver: dict,
                           triggers: list[dict] | None = None) -> list[str]:
    """Union of every step's `secrets:` entry ids and code-referenced ids plus
    every discord trigger's token secret — a dangling id raises; the archive
    listing order is by record name (§5.1)."""
    ids: set[str] = set()
    for s in ver.get("steps", []):
        where = f"step {s.get('name')!r}"
        for sid in ({e["id"] for e in s.get("secrets") or [] if e.get("id")}
                    | set(SECRET_REF_RE.findall(s.get("code", "")))):
            refs.secret_record(sid, where)
            ids.add(sid)
    for t in triggers or []:
        if t.get("kind") == "discord":
            refs.secret_record(t["secret"], "a Discord trigger")
            ids.add(t["secret"])
    return sorted(ids, key=lambda sid: refs.secrets_by_id[sid]["name"])


def _referenced_agent_ids(store: Store, refs: _ExportRefs, a: dict, ver: dict) -> list[str]:
    """The authoring agent + every step-referenced agent (manifest entry ids
    and agents["<id>"] code subscripts, §4.1) — deduped by record id, archive
    order stable (drafting first, then per-step sorted ids)."""
    out: list[str] = []
    drafting = next((g for g in store.agents if g["id"] == a["agent_id"]), None)
    if drafting:
        out.append(drafting["id"])
    for s in ver.get("steps", []):
        where = f"step {s.get('name')!r}"
        ids = {e["id"] for e in s.get("agents") or [] if e.get("id")}
        ids |= set(AGENT_REF_RE.findall(s.get("code", "")))
        for aid in sorted(ids):
            refs.agent_record(aid, where)
            if aid not in out:
                out.append(aid)
    return out


def export_automation(store: Store, a: dict, include_values: bool = True) -> bytes:
    """The §5.1 archive for an automation's current version, as zip bytes."""
    with store.lock:
        ver = a["versions"][a["current_version"]]
        refs = _ExportRefs(store, a.get("unresolved_references"))
        # §5.1: collect the referenced sets first (dangling ids raise here,
        # nothing half-built), then assign the per-kind refs in listing order.
        secret_ids = _referenced_secret_ids(refs, ver, a["triggers"])
        agent_ids = _referenced_agent_ids(store, refs, a, ver)
        refs.assign(secret_ids, agent_ids)
        manifest: dict = {
            "format_version": FORMAT_VERSION,
            "exported_at": timefmt.now_iso(),
            "app_version": __version__,
            # §5.1: the exporting machine's platform token — import stamps it
            # as §4.1 originOs and flags a mismatch, never a rejection.
            "os": paths.current_os(),
            "name": a["name"],
        }
        drafting = next((g for g in store.agents if g["id"] == a["agent_id"]), None)
        if drafting:
            manifest["agent"] = refs.agent_ref_by_id[drafting["id"]]
        # §5.1: cron, app_start, discord, and imessage — one-shot `time`
        # triggers are moments in time; no ids, no enabled state. A discord
        # trigger's §4.3 secret id becomes the token secret's secrets.yaml
        # REF (the archive reference format) — never the value.
        triggers = []
        for t in a["triggers"]:
            if t["kind"] == "cron":
                triggers.append({"kind": "cron", "expression": t["expression"],
                                 **({"timezone": t["timezone"]} if t.get("timezone") else {}),
                                 # §4.3: additive, written only when the cron opted out
                                 **({"run_if_missed": False}
                                    if t.get(triggerlib.RUN_IF_MISSED) is False else {})})
            elif t["kind"] == "app_start":
                triggers.append({"kind": "app_start"})
            elif t["kind"] == "discord":
                triggers.append({"kind": "discord", "channel": t["channel"],
                                 "secret": refs.secret_ref_by_id[t["secret"]],
                                 **({"pattern": t["pattern"]} if t.get("pattern") else {}),
                                 **({"mention": True} if t.get("mention") else {}),
                                 **({"author": t["author"]} if t.get("author") else {})})
            elif t["kind"] == "imessage":
                triggers.append({"kind": "imessage", "from": t["from"],
                                 **({"pattern": t["pattern"]} if t.get("pattern") else {})})
        manifest["triggers"] = triggers
        if include_values:
            manifest["param_values"] = {
                k: v for k, v in a["param_values"].items()
                if any(p.get("name") == k for p in ver.get("params", []))}
        # §5.1: definitions only — a version written before the §4.2 save-side
        # strip may still hold resolved values; they must never leave the
        # machine outside the include-values gate.
        meta: dict = {"description": a.get("description", ""),
                      "params": strip_param_values(ver.get("params"))}
        pkgs = [{"pip": p.get("pip"), "import": p.get("import"),
                 **({"why": p["why"]} if p.get("why") else {})}
                for p in ver.get("packages", []) or []]
        if pkgs:
            meta["packages"] = pkgs
        steps = []
        for s in ver["steps"]:
            entry = {"file": s["file"], "name": s.get("name", ""), "description": s.get("description", "")}
            if s.get("agent"):
                entry["agent"] = True
                entry["why"] = s.get("why", "")
                if s.get("agents"):
                    # §5.1: id entries translate to the archive's { ref, why? }
                    # form — dangling ids raised during collection above.
                    entry["agents"] = [
                        {"ref": refs.agent_ref_by_id[e["id"]],
                         **({"why": e["why"]} if e.get("why") else {})}
                        for e in s["agents"] if e.get("id")]
            if s.get("secrets"):
                entry["secrets"] = [
                    {"ref": refs.secret_ref_by_id[e["id"]],
                     **({"why": e["why"]} if e.get("why") else {})}
                    for e in s["secrets"] if e.get("id")]
            if s.get("packages"):
                entry["packages"] = list(s["packages"])
            # §4.1 per-step time limits travel — a no_timeout long-runner must
            # not silently regain the default watchdog on another Mac.
            if s.get("timeout"):
                entry["timeout"] = int(s["timeout"])
            if s.get("no_timeout"):
                entry["no_timeout"] = True
            # §4.1 retry pair travels the same way (§8: same shape rules as the
            # timeout pair) — an infinite_retries listener must not become
            # single-attempt on another Mac.
            if s.get("retries"):
                entry["retries"] = int(s["retries"])
            if s.get("infinite_retries"):
                entry["infinite_retries"] = True
            steps.append(entry)
        meta["steps"] = steps
        agents = [{"ref": refs.agent_ref_by_id[aid],
                   "name": harness.grant_name(g := refs.agents_by_id[aid]),
                   "description": g.get("description") or "",
                   "harness": g.get("harness"), "mode": g.get("mode", "default"),
                   "model": g.get("model")} for aid in agent_ids]
        secrets = [{"ref": refs.secret_ref_by_id[sid],
                    "name": refs.secrets_by_id[sid]["name"],
                    "description": refs.secrets_by_id[sid].get("description") or ""}
                   for sid in secret_ids]
        files: list[tuple[str, str]] = [
            ("manifest.yaml", yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)),
            ("automation/automation.yaml", yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)),
            ("automation/spec.md", blocks_to_md(ver.get("spec", []))),
        ]
        if ver.get("instructions"):
            files.append(("automation/instructions.md", ver["instructions"].strip() + "\n"))
        if (ver.get("notes") or "").strip():
            files.append(("automation/notes.md", ver["notes"].strip() + "\n"))
        for s in ver["steps"]:
            # §5.1: the code's id subscripts travel in ref form.
            files.append((f"automation/{s['file']}", refs.code(s.get("code", ""))))
        files.append(("agents.yaml", yaml.safe_dump({"agents": agents}, sort_keys=False, allow_unicode=True)))
        files.append(("secrets.yaml", yaml.safe_dump({"secrets": secrets}, sort_keys=False, allow_unicode=True)))
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, text in files:
            z.writestr(path, text)
    return buf.getvalue()


# ---------- import ----------
# Imported archives are untrusted input (§5.1) — cap the decompressed sizes so
# a crafted member can't balloon into memory. zipfile bounds each read by the
# declared file_size, so checking the directory up front is sufficient.
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024        # the upload itself
_MAX_MEMBER_BYTES = 32 * 1024 * 1024        # one member, decompressed
_MAX_TOTAL_BYTES = 256 * 1024 * 1024        # whole archive, decompressed


def _check_sizes(z: zipfile.ZipFile) -> None:
    total = 0
    for info in z.infolist():
        if info.file_size > _MAX_MEMBER_BYTES:
            raise TransferError(f"{info.filename} in the archive is unreasonably large")
        total += info.file_size
    if total > _MAX_TOTAL_BYTES:
        raise TransferError("the archive decompresses far beyond any real automation")


def _read_member(z: zipfile.ZipFile, path: str) -> bytes:
    """One archive member's bytes; every way a hostile or truncated zip can
    fail the read (CRC mismatch, unknown compression method, encrypted
    member, a decompressor error) is a §5.1 422, never a 500. KeyError
    (missing member) stays the caller's to handle."""
    try:
        return z.read(path)
    except KeyError:
        raise
    except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError, zlib.error) as e:
        raise TransferError(f"the archive's {path} can't be read: {e}") from None


def _yaml_or_reject(z: zipfile.ZipFile, path: str, required: bool = True) -> dict:
    try:
        raw = _read_member(z, path)
    except KeyError:
        if required:
            raise TransferError(f"the archive is missing {path}") from None
        return {}
    try:
        data = yaml.safe_load(raw.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        raise TransferError(f"{path} isn't valid YAML: {e}") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise TransferError(f"{path} must hold a YAML mapping")
    return data


def _text(z: zipfile.ZipFile, path: str, required: bool = True) -> str | None:
    try:
        return _read_member(z, path).decode("utf-8")
    except KeyError:
        if required:
            raise TransferError(f"the archive is missing {path}") from None
        return None
    except UnicodeDecodeError:
        raise TransferError(f"{path} isn't valid UTF-8") from None


def _entry_ref(g: dict) -> str | None:
    """The §5.1 ref of an archive entry: a decimal string ("1", "2", …); a
    hand-written YAML integer reads as its string form. None when malformed."""
    r = g.get("ref")
    if isinstance(r, bool):
        return None
    if isinstance(r, int):
        r = str(r)
    if isinstance(r, str) and _REF_RE.match(r.strip()):
        return r.strip()
    return None


def _validate(z: zipfile.ZipFile) -> dict:
    """Parse + validate everything up front; returns the parsed archive."""
    _check_sizes(z)
    manifest = _yaml_or_reject(z, "manifest.yaml")
    if manifest.get("format_version") != FORMAT_VERSION:
        # §5.1/§21.3: the numeric-reference break carried no migration — any
        # other format (the retired format 1 included) gets re-export guidance.
        raise TransferError(f"unsupported archive format {manifest.get('format_version')!r} - "
                            f"this app reads format {FORMAT_VERSION}; re-export the "
                            "automation with the current version and import that file")
    name = manifest.get("name")
    if not isinstance(name, str) or not name.strip():
        raise TransferError("the manifest has no automation name")
    # §5.1: the optional platform token — unrecognized values are legal (they
    # store and compare as-is, §4.1 originOs), so a newer platform token never
    # blocks an import.
    os_token = manifest.get("os")
    if os_token is not None and (not isinstance(os_token, str) or not os_token.strip()):
        raise TransferError("the manifest's os must be a non-empty string")
    os_token = os_token.strip() if os_token else None
    triggers_in = manifest.get("triggers") or []
    if not isinstance(triggers_in, list):
        raise TransferError("manifest triggers must be a list")
    triggers = []
    for t in triggers_in:
        if not isinstance(t, dict) or t.get("kind") not in ("cron", "app_start",
                                                            "discord", "imessage"):
            raise TransferError(f"unsupported trigger in the archive: {t!r} — "
                                "only cron, app_start, discord, and imessage travel")
        if t["kind"] == "app_start" and any(x["kind"] == "app_start" for x in triggers):
            raise TransferError("the archive holds more than one app_start trigger")
        if t["kind"] == "discord":
            # §5.1: the archive's `secret` is the token secret's secrets.yaml
            # REF (local ids never travel) — checked here; the §4.3 uuid rule
            # applies only after import resolves it to a local id. A YAML
            # integer reads as its string form (§5.1 ref rule).
            sec = t.get("secret")
            if isinstance(sec, int) and not isinstance(sec, bool):
                sec = t["secret"] = str(sec)
            if not isinstance(sec, str) or not _REF_RE.match(sec.strip()):
                raise TransferError("invalid trigger in the archive: a Discord trigger "
                                    "needs the numbered reference of the secret holding "
                                    "the bot token")
        probe = ({"kind": "discord", "channel": t.get("channel"), "secret": _PROBE_SECRET_ID,
                  "pattern": t.get("pattern"), "mention": t.get("mention", False),
                  "author": t.get("author")}
                 if t["kind"] == "discord"
                 else {"kind": "imessage", "from": t.get("from"),
                       "pattern": t.get("pattern")}
                 if t["kind"] == "imessage"
                 else {"kind": t["kind"], "expression": t.get("expression"),
                       "timezone": t.get("timezone"), "source": "spec",
                       **({triggerlib.RUN_IF_MISSED: t["run_if_missed"]}
                          if "run_if_missed" in t else {})})
        if err := triggerlib.validate_trigger(probe):
            raise TransferError(f"invalid trigger in the archive: {err}")
        # §5.1: archives carry no cron `source` — import stamps `spec` (the
        # archive travels with its spec, §4.3), so the §4.3 merge treats the
        # imported schedule as spec-derived.
        triggers.append({"kind": t["kind"],
                         **({"expression": t["expression"], "source": "spec"}
                            if t["kind"] == "cron" else {}),
                         **({"timezone": t["timezone"]} if t.get("timezone") and t["kind"] == "cron" else {}),
                         **({triggerlib.RUN_IF_MISSED: False}
                            if t["kind"] == "cron" and t.get("run_if_missed") is False else {}),
                         **({"channel": t["channel"].strip(), "secret": t["secret"].strip(),
                             **({"pattern": t["pattern"].strip()} if t.get("pattern") else {}),
                             **({"mention": True} if t.get("mention") else {}),
                             **({"author": triggerlib.normalize_authors(t["author"])}
                                if t.get("author") else {})}
                            if t["kind"] == "discord" else {}),
                         # §4.3: normalize like every other ingest path — a
                         # formatted number stored verbatim would never match
                         # chat.db's E.164 handles and the trigger would
                         # silently never fire.
                         **({"from": triggerlib.normalize_handle(t["from"]),
                             **({"pattern": t["pattern"].strip()} if t.get("pattern") else {})}
                            if t["kind"] == "imessage" else {})})
    values = manifest.get("param_values") or {}
    if not isinstance(values, dict):
        raise TransferError("manifest param_values must be a mapping")

    meta = _yaml_or_reject(z, "automation/automation.yaml")
    if meta.get("description") is not None and not isinstance(meta["description"], str):
        # §5.1: the whole archive validates up front — a non-string
        # description would 500 out of the similarity tokenizer instead.
        raise TransferError("the automation description must be text")
    params = meta.get("params") or []
    if not isinstance(params, list):
        raise TransferError("param definitions must be a list")
    for p in params:
        if not isinstance(p, dict) or not p.get("name") or p.get("kind") not in PARAM_KINDS:
            raise TransferError(f"invalid parameter definition: {p!r}")
    # §5.1: definitions only — an archive exported before the §4.2 save-side
    # strip can carry resolved values inside its definitions; they never land.
    params = strip_param_values(params)
    packages = meta.get("packages") or []
    if not isinstance(packages, list) or any(
            not isinstance(p, dict) or not p.get("pip") or not p.get("import")
            for p in packages):
        raise TransferError("invalid packages declaration")
    steps_meta = meta.get("steps") or []
    if not isinstance(steps_meta, list) or not steps_meta:
        raise TransferError("the archive holds no steps")
    steps = []
    seen_files: set[str] = set()
    for i, s in enumerate(steps_meta, 1):
        if (not isinstance(s, dict) or not isinstance(s.get("file"), str)
                or not s["file"] or not s.get("name")):
            raise TransferError(f"invalid step manifest entry: {s!r}")
        if ("/" in s["file"] or "\\" in s["file"] or s["file"].startswith(".")
                or s["file"] in ("automation.yaml", "spec.md", "instructions.md", "notes.md")):
            # Reserved names would let a step's code overwrite (or be
            # overwritten by) the version folder's own files at write time.
            raise TransferError(f"invalid step filename: {s['file']!r}")
        # §5.1/§8: the NN-name.py rule in listed order, like every other ingest
        # path — a looser name would land a version the app's own save
        # endpoints later 422 on (and `automation pull` would silently drop).
        m = STEP_FILE_RE.match(s["file"])
        if not m or int(m.group(1)) != i:
            raise TransferError(
                f"step filename {s['file']!r} must follow NN-name.py in listed order ({i:02d}-…)")
        if s["file"] in seen_files:
            raise TransferError(f"duplicate step filename: {s['file']!r}")
        seen_files.add(s["file"])
        code = _text(z, f"automation/{s['file']}")
        # §5.1: syntactically broken code answers 422 here — the app's own
        # save endpoints would otherwise reject the user's first edit of code
        # they never wrote. The §6.2 allowlist is deliberately not enforced
        # (curated growth within a format version; the executor backstops).
        try:
            ast.parse(code or "")
        except SyntaxError as e:
            raise TransferError(f"step {s['file']!r} isn't valid Python "
                                f"(line {e.lineno}): {e.msg}") from None
        entry = {"file": s["file"], "name": s["name"], "description": s.get("description", ""),
                 "code": code}
        # A scalar where a list belongs (hand-written YAML `agents: 5`) must
        # answer 422, not iterate into a TypeError 500.
        for key in ("agents", "secrets", "packages"):
            if s.get(key) is not None and not isinstance(s[key], list):
                raise TransferError(f"step {s['file']!r} {key} must be a list")
        # §5.1: agent grants travel as {ref, why?} entries — the §4.1 id
        # form and the retired name form are rejected (local ids never
        # travel); other malformed foreign entries are dropped, not
        # imported. Checked whether or not the step is flagged `agent`, so
        # a uuid/name-form list is never silently dropped.
        if any(isinstance(g, dict) and ("id" in g or "name" in g)
               for g in s.get("agents") or []):
            raise TransferError(f"step {s['file']!r} lists agents by id or name - "
                                "archives carry numbered references; re-export the "
                                "automation with the current version")
        if s.get("agent"):
            entry["agent"] = True
            entry["why"] = s.get("why", "")
            entry["agents"] = [
                {"ref": r,
                 **({"why": str(g["why"]).strip()} if str(g.get("why") or "").strip() else {})}
                for g in (s.get("agents") or [])
                if isinstance(g, dict) and (r := _entry_ref(g)) is not None]
        if s.get("secrets"):
            # §5.1: like agents, secret grants travel as {ref, why?} entries —
            # the id and name forms are rejected; malformed foreign entries
            # are dropped.
            if any(isinstance(g, dict) and ("id" in g or "name" in g) for g in s["secrets"]):
                raise TransferError(f"step {s['file']!r} lists secrets by id or name - "
                                    "archives carry numbered references; re-export the "
                                    "automation with the current version")
            entry["secrets"] = [
                {"ref": r,
                 **({"why": str(g["why"]).strip()} if str(g.get("why") or "").strip() else {})}
                for g in s["secrets"]
                if isinstance(g, dict) and (r := _entry_ref(g)) is not None]
        if s.get("packages"):
            # §5.1: per-step package notes travel as {import, why} entries —
            # malformed foreign entries are dropped, not imported.
            entry["packages"] = [
                {"import": g["import"],
                 **({"why": str(g["why"]).strip()} if str(g.get("why") or "").strip() else {})}
                for g in s["packages"]
                if isinstance(g, dict) and isinstance(g.get("import"), str)]
        t = s.get("timeout")
        if t is not None:
            if not isinstance(t, int) or isinstance(t, bool) or t <= 0:
                raise TransferError(f"invalid step timeout: {t!r}")
            entry["timeout"] = t
        if s.get("no_timeout"):
            # §5.1: steps obey the §8 bounds — an archive can't land a step no
            # drafting call could produce.
            if t is not None:
                raise TransferError("a step can't combine timeout and no_timeout")
            entry["no_timeout"] = True
        r = s.get("retries")
        if r is not None:
            if not isinstance(r, int) or isinstance(r, bool) or not 1 <= r <= 10:
                raise TransferError(f"invalid step retries: {r!r}")
            entry["retries"] = r
        if s.get("infinite_retries"):
            if r is not None:
                raise TransferError("a step can't combine retries and infinite_retries")
            entry["infinite_retries"] = True
        steps.append(entry)

    agents = _yaml_or_reject(z, "agents.yaml", required=False).get("agents") or []
    if not isinstance(agents, list):
        raise TransferError("agents.yaml agents must be a list")
    for g in agents:
        if not isinstance(g, dict) or (ref := _entry_ref(g)) is None:
            raise TransferError(f"invalid agent in the archive: {g!r} - every entry "
                                "needs a numbered ref")
        g["ref"] = ref
        if (not isinstance(g.get("name"), str) or not g["name"]
                or g.get("harness") not in harness.HARNESS_ID):
            raise TransferError(f"invalid agent in the archive: {g!r}")
        if g.get("description") is not None and not isinstance(g["description"], str):
            raise TransferError(f"invalid agent in the archive: {g!r} - the "
                                "description must be text")
        mode = g.get("mode", "default")
        if mode not in MODES:
            raise TransferError(f"invalid agent mode {mode!r}")
        if mode == "ollama" and g["harness"] not in harness.LOCAL_MODEL_HARNESSES:
            raise TransferError(
                "a local-model agent needs Claude Code, Codex, or OpenCode")
        if mode != "default" and not g.get("model"):
            raise TransferError(f"agent {g['name']!r} needs a model for mode {mode!r}")
    agent_refs = [g["ref"] for g in agents]
    if len(set(agent_refs)) != len(agent_refs):
        raise TransferError("duplicate agent refs in the archive - refs must be unique")
    secrets = _yaml_or_reject(z, "secrets.yaml", required=False).get("secrets") or []
    if not isinstance(secrets, list):
        raise TransferError("secrets.yaml secrets must be a list")
    for s in secrets:
        if not isinstance(s, dict) or (ref := _entry_ref(s)) is None:
            raise TransferError(f"invalid secret in the archive: {s!r} - every entry "
                                "needs a numbered ref")
        s["ref"] = ref
        if (not isinstance(s.get("name"), str)
                or not SECRET_NAME_RE.match(s["name"])):
            raise TransferError(f"invalid secret in the archive: {s!r}")
        if s.get("description") is not None and not isinstance(s["description"], str):
            raise TransferError(f"invalid secret in the archive: {s!r} - the "
                                "description must be text")
    secret_refs = [s["ref"] for s in secrets]
    if len(set(secret_refs)) != len(secret_refs):
        raise TransferError("duplicate secret refs in the archive - refs must be unique")

    # §5.1: every ref must resolve against the archive's own agents.yaml /
    # secrets.yaml — step grant entries, each discord trigger's token secret,
    # and every code subscript key alike (one scan, which is also what catches
    # uuid-form and name-form leftovers), so import can never land code whose
    # references only fail later at execution time.
    agent_ref_set = set(agent_refs)
    secret_ref_set = set(secret_refs)
    for t in triggers:
        if t["kind"] == "discord" and t["secret"] not in secret_ref_set:
            raise TransferError(f"a Discord trigger references secret {t['secret']} "
                                "that isn't listed in the archive's secrets.yaml")
    for s in steps:
        for g in s.get("agents") or []:
            if g["ref"] not in agent_ref_set:
                raise TransferError(f"step {s['file']!r} references agent {g['ref']} "
                                    "that isn't listed in the archive's agents.yaml")
        for g in s.get("secrets") or []:
            if g["ref"] not in secret_ref_set:
                raise TransferError(f"step {s['file']!r} references secret {g['ref']} "
                                    "that isn't listed in the archive's secrets.yaml")
        for m in _ANY_AGENT_SUBSCRIPT_RE.finditer(s["code"]):
            if m.group(1) not in agent_ref_set:
                raise TransferError(f"step {s['file']!r} subscripts agents[{m.group(1)!r}], "
                                    "which is not one of the archive's numbered "
                                    "references - re-export the automation with the "
                                    "current version")
        for m in _ANY_SECRET_SUBSCRIPT_RE.finditer(s["code"]):
            if m.group(1) not in secret_ref_set:
                raise TransferError(f"step {s['file']!r} subscripts secrets[{m.group(1)!r}], "
                                    "which is not one of the archive's numbered "
                                    "references - re-export the automation with the "
                                    "current version")

    agent_ref = manifest.get("agent")
    if isinstance(agent_ref, int) and not isinstance(agent_ref, bool):
        agent_ref = str(agent_ref)
    if agent_ref is not None:
        if not isinstance(agent_ref, str) or not _REF_RE.match(agent_ref.strip()):
            raise TransferError("the manifest's agent must be a numbered ref")
        agent_ref = agent_ref.strip()
        if agent_ref not in agent_ref_set:
            raise TransferError(f"the manifest's agent {agent_ref} isn't listed in the "
                                "archive's agents.yaml")

    spec_md = _text(z, "automation/spec.md")
    instr = _text(z, "automation/instructions.md", required=False)
    notes = _text(z, "automation/notes.md", required=False)
    return {"name": name.strip(), "agent": agent_ref, "os": os_token,
            "triggers": triggers, "param_values": values,
            "description": meta.get("description", ""), "params": params, "packages": packages,
            "steps": steps, "spec": md_to_blocks(spec_md), "instructions": (instr or "").strip() or None,
            "notes": (notes or "").strip(),
            "agents": agents, "secrets": secrets}


def _open_archive(data: bytes) -> zipfile.ZipFile:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise TransferError("the archive is larger than the 64 MB import limit")
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise TransferError("not a valid .autowright archive") from None


# ---------- §5.1 match ladders ----------
# Import never creates agent or secret records: every archive ref resolves
# against the existing local records through a deterministic ladder, or lands
# unresolved. The similarity rung is pinned in §5.1 so every build matches
# identically.
_CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_NONWORD_RE = re.compile(r"[^A-Za-z0-9]+")
_STOPWORDS = frozenset((
    "a agent an and api at auth be bot by cli credential credentials default "
    "for from id in is it key local main model my new of on or our pass "
    "password secret that the this to token use used uses using value with "
    "your").split())
_ACCEPT_SCORE = 0.60
_MARGIN = 0.15


def _tokens(text: str | None) -> frozenset[str]:
    """§5.1 tokenizer: split camelCase boundaries and non-alphanumeric runs,
    lowercase, drop tokens shorter than 2 characters and the stopwords.
    Non-string input tokenizes empty (validation rejects it upstream; this
    backstop keeps a stray shape from 500ing the match pass)."""
    if not isinstance(text, str):
        return frozenset()
    parts = _NONWORD_RE.split(_CAMEL_SPLIT_RE.sub(" ", text or ""))
    return frozenset(p.lower() for p in parts
                     if len(p) >= 2 and p.lower() not in _STOPWORDS)


def _similarity(a: frozenset, b: frozenset) -> float:
    """Jaccard overlap; 0 when either set is empty (§5.1)."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _score(archive_name: str, archive_desc: str | None,
           local_name: str, local_desc: str | None) -> tuple[float, float]:
    """(score, name similarity): the name similarity alone when either side's
    description tokenizes empty, else 0.7 x name + 0.3 x description (§5.1)."""
    n = _similarity(_tokens(archive_name), _tokens(local_name))
    da, db = _tokens(archive_desc), _tokens(local_desc)
    if not da or not db:
        return n, n
    return 0.7 * n + 0.3 * _similarity(da, db), n


def _best(archive_name: str, archive_desc: str | None,
          candidates: list[tuple[dict, str, str]]) -> dict | None:
    """The §5.1 similarity acceptance rule over (record, name, description)
    candidates: order by (score desc, name casefold, id); the head matches
    only when score >= 0.60, its name similarity is > 0 (a description-only
    match never wires a credential), and it is alone or leads the second-best
    score by >= 0.15."""
    scored = sorted(
        ((*_score(archive_name, archive_desc, lname, ldesc), lname, rec)
         for rec, lname, ldesc in candidates),
        key=lambda x: (-x[0], x[2].casefold(), x[3]["id"]))
    if not scored:
        return None
    score, name_sim, _, rec = scored[0]
    if score < _ACCEPT_SCORE or name_sim <= 0:
        return None
    if len(scored) > 1 and score - scored[1][0] < _MARGIN:
        return None
    return rec


def _effective_model(g: dict):
    """§4.7: `model` compares as null when mode is `default`."""
    return g.get("model") if g.get("mode", "default") != "default" else None


def match_archive(store: Store, arch: dict) -> dict:
    """§5.1: resolve every archive ref against the local records — one entry
    point for preview (dry) and landing, so the two can never disagree.
    Pass-based with the claim rule: a local record is matched by at most one
    archive ref; within a pass, refs resolve in listing order, and a claimed
    record leaves the candidate pool for every later pass."""
    secret_res: dict[str, dict | None] = {e["ref"]: None for e in arch["secrets"]}
    secret_how: dict[str, str] = {}
    unclaimed_s = {s["id"]: s for s in store.secrets}
    by_name = {s["name"]: s for s in store.secrets}
    for e in arch["secrets"]:                      # pass 1: exact name
        rec = by_name.get(e["name"])
        if rec is not None and rec["id"] in unclaimed_s:
            secret_res[e["ref"]] = rec
            secret_how[e["ref"]] = "name"
            del unclaimed_s[rec["id"]]
    for e in arch["secrets"]:                      # pass 2: similarity
        if secret_res[e["ref"]] is not None:
            continue
        rec = _best(e["name"], e.get("description"),
                    [(s, s["name"], s.get("description") or "")
                     for s in unclaimed_s.values()])
        if rec is not None:
            secret_res[e["ref"]] = rec
            secret_how[e["ref"]] = "similarity"
            del unclaimed_s[rec["id"]]

    agent_res: dict[str, dict | None] = {e["ref"]: None for e in arch["agents"]}
    agent_how: dict[str, str] = {}
    unclaimed_a = {g["id"]: g for g in store.agents}
    for e in arch["agents"]:                       # pass 1: exact name + config
        target = (e["name"].casefold(), e["harness"],
                  e.get("mode", "default"), _effective_model(e))
        rec = next((g for g in unclaimed_a.values()
                    if (harness.grant_name(g).casefold(), g.get("harness"),
                        g.get("mode", "default"), _effective_model(g)) == target), None)
        if rec is not None:
            agent_res[e["ref"]] = rec
            agent_how[e["ref"]] = "name"
            del unclaimed_a[rec["id"]]
    for e in arch["agents"]:                       # pass 2: configuration
        if agent_res[e["ref"]] is not None:
            continue
        cfg = (e["harness"], e.get("mode", "default"), _effective_model(e))
        cands = [g for g in unclaimed_a.values()
                 if (g.get("harness"), g.get("mode", "default"),
                     _effective_model(g)) == cfg]
        if cands:
            # §5.1 tie-break: higher similarity score, then the local default
            # agent, then name casefold, then id.
            rec = min(cands, key=lambda g: (
                -_score(e["name"], e.get("description"),
                        harness.grant_name(g), g.get("description") or "")[0],
                0 if g["id"] == store.default_agent_id else 1,
                harness.grant_name(g).casefold(), g["id"]))
            agent_res[e["ref"]] = rec
            agent_how[e["ref"]] = "configuration"
            del unclaimed_a[rec["id"]]
    for e in arch["agents"]:                       # pass 3: similarity
        if agent_res[e["ref"]] is not None:
            continue
        rec = _best(e["name"], e.get("description"),
                    [(g, harness.grant_name(g), g.get("description") or "")
                     for g in unclaimed_a.values()])
        if rec is not None:
            agent_res[e["ref"]] = rec
            agent_how[e["ref"]] = "similarity"
            del unclaimed_a[rec["id"]]

    # §5.1: the authoring agent resolves through the same ladder; unresolved or
    # absent falls back to the local default agent. The fallback claims no
    # record and never stands in for the same ref's step references.
    drafting = agent_res.get(arch["agent"]) if arch.get("agent") else None
    if drafting is None:
        drafting = next((g for g in store.agents
                         if g["id"] == store.default_agent_id), None)
    return {"secrets": secret_res, "agents": agent_res,
            "secret_how": secret_how, "agent_how": agent_how,
            "drafting": drafting}


def preview_archive(store: Store, data: bytes) -> dict:
    """§5.2 preview: validate fully, write nothing, run the §5.1 match ladders
    dry — matchedTo/matchedBy are null for a reference that would land
    unresolved (best-effort: confirm re-runs the ladders on the store then)."""
    with _open_archive(data) as z:
        arch = _validate(z)
    with store.lock:
        m = match_archive(store, arch)
        secrets = []
        for e in arch["secrets"]:
            rec = m["secrets"][e["ref"]]
            secrets.append({"name": e["name"], "description": e.get("description") or "",
                            "matchedTo": rec["name"] if rec else None,
                            "matchedBy": m["secret_how"].get(e["ref"])})
        agents = []
        for e in arch["agents"]:
            rec = m["agents"][e["ref"]]
            agents.append({"name": e["name"], "harness": e["harness"],
                           "mode": e.get("mode", "default"),
                           "model": _effective_model(e),
                           "matchedTo": harness.grant_name(rec) if rec else None,
                           "matchedBy": m["agent_how"].get(e["ref"])})
        # §5.2: the §4.1 name dedupe run dry — the name the import will land
        # under (best-effort: confirm re-runs the dedupe on the store then).
        lands_as = store.free_automation_name(arch["name"])
    return {"name": arch["name"], "landsAs": lands_as, "description": arch["description"],
            "steps": [{"name": s["name"], "description": s.get("description", ""),
                       "agent": bool(s.get("agent"))} for s in arch["steps"]],
            "params": [{"name": p["name"], "kind": p["kind"]} for p in arch["params"]],
            "triggers": arch["triggers"], "packages": arch["packages"],
            "agents": agents, "secrets": secrets,
            # §5.1: the archive's platform token + mismatch flag — same rule
            # as the import summary (absent token: null, never a mismatch).
            "os": arch["os"],
            "osMismatch": bool(arch["os"]) and arch["os"] != paths.current_os()}


def import_automation(store: Store, data: bytes) -> tuple[dict, dict]:
    """Validate and land a §5.1 archive; returns (automation, summary).
    Import creates no agent or secret records, so there is nothing to roll
    back: the only writes are the automation creation and its param values."""
    with _open_archive(data) as z:
        arch = _validate(z)
    with store.lock:
        a, m = _land_archive(store, arch)
    # §5.1 summary: each matched agent carries `ready` — the one §19
    # check-ready rule, run at import time outside the store lock (it may
    # spawn a status subprocess) and memoized per harness config so agents
    # sharing one harness check once.
    ready_memo: dict[tuple, bool] = {}

    def _ready(rec: dict) -> bool:
        key = (rec.get("harness"), rec.get("mode", "default"), rec.get("model"))
        if key not in ready_memo:
            # The automation already landed — a raising readiness probe must
            # degrade to "not ready" in the summary, never turn a completed
            # import into a 500.
            try:
                ready_memo[key] = harness.check_ready(rec.get("harness"), rec.get("model"),
                                                      rec.get("mode", "default"))
            except Exception:  # noqa: BLE001
                ready_memo[key] = False
        return ready_memo[key]

    secrets_matched = [{"name": e["name"], "matchedTo": rec["name"],
                        "matchedBy": m["secret_how"][e["ref"]]}
                       for e in arch["secrets"]
                       if (rec := m["secrets"][e["ref"]]) is not None]
    agents_matched = [{"name": e["name"], "matchedTo": harness.grant_name(rec),
                       "matchedBy": m["agent_how"][e["ref"]], "ready": _ready(rec)}
                      for e in arch["agents"]
                      if (rec := m["agents"][e["ref"]]) is not None]
    # §5.1: archive order, secrets before agents.
    unresolved = ([{"kind": "secret", "name": e["name"],
                    "description": e.get("description") or ""}
                   for e in arch["secrets"] if m["secrets"][e["ref"]] is None]
                  + [{"kind": "agent", "name": e["name"],
                      "description": e.get("description") or ""}
                     for e in arch["agents"] if m["agents"][e["ref"]] is None])
    summary = {"secretsMatched": secrets_matched,
               "agentsMatched": agents_matched,
               "unresolved": unresolved,
               "packages": arch["packages"],
               # §5.1: the archive's name when the §4.1 dedupe renamed the
               # landed automation, else None.
               "renamedFrom": arch["name"] if a["name"] != arch["name"] else None,
               "os": arch["os"],
               "osMismatch": bool(arch["os"]) and arch["os"] != paths.current_os()}
    return a, summary


def _ref_substituter(kind_word: str, id_by_ref: dict[str, str],
                     archive_names: dict[str, str], target_names: dict[str, str]):
    """§5.1 import-side code rewrite for one kind: the ref subscript becomes
    the resolved local id, and an immediately-trailing `#` comment whose text
    equals the archive ref's name is rewritten to the matched record's name
    when the match renamed — the §6.1 comment stays truthful. Any other
    comment is left alone."""
    pattern = re.compile(
        rf"\b{kind_word}\[\s*[\"']([0-9]+)[\"']\s*\](?:([ \t]*#[ \t]*)([^\n]*))?")

    def repl(m: re.Match) -> str:
        ref = m.group(1)
        out = f'{kind_word}["{id_by_ref[ref]}"]'
        if m.group(2) is not None:
            comment = m.group(3)
            if (comment.strip() == archive_names[ref]
                    and target_names[ref] != archive_names[ref]):
                comment = target_names[ref]
            out += m.group(2) + comment
        return out

    return lambda code: pattern.sub(repl, code)


def _land_archive(store: Store, arch: dict) -> tuple[dict, dict]:
    """The §5.1 import's write half — caller holds store.lock. Never creates
    agent or secret records: refs resolve through the §5.1 match ladders, and
    an unresolved ref lands as a freshly minted local id (matching no record)
    plus a §4.1 unresolved_references entry, so the automation arrives
    needing attention instead of failing. Returns (automation, match)."""
    m = match_archive(store, arch)
    unresolved: dict[str, dict] = {}
    secret_id_by_ref: dict[str, str] = {}
    secret_name_by_ref = {e["ref"]: e["name"] for e in arch["secrets"]}
    secret_target_name: dict[str, str] = {}
    for e in arch["secrets"]:
        rec = m["secrets"][e["ref"]]
        if rec is not None:
            secret_id_by_ref[e["ref"]] = rec["id"]
            secret_target_name[e["ref"]] = rec["name"]
        else:
            mid = new_id()
            secret_id_by_ref[e["ref"]] = mid
            secret_target_name[e["ref"]] = e["name"]
            unresolved[mid] = {"kind": "secret", "name": e["name"],
                               "description": e.get("description") or ""}
    agent_id_by_ref: dict[str, str] = {}
    agent_name_by_ref = {e["ref"]: e["name"] for e in arch["agents"]}
    agent_target_name: dict[str, str] = {}
    for e in arch["agents"]:
        rec = m["agents"][e["ref"]]
        if rec is not None:
            agent_id_by_ref[e["ref"]] = rec["id"]
            agent_target_name[e["ref"]] = harness.grant_name(rec)
        else:
            mid = new_id()
            agent_id_by_ref[e["ref"]] = mid
            agent_target_name[e["ref"]] = e["name"]
            unresolved[mid] = {"kind": "agent", "name": e["name"],
                               "description": e.get("description") or ""}

    sub_secrets = _ref_substituter("secrets", secret_id_by_ref,
                                   secret_name_by_ref, secret_target_name)
    sub_agents = _ref_substituter("agents", agent_id_by_ref,
                                  agent_name_by_ref, agent_target_name)

    steps = []
    for s in arch["steps"]:
        entry = dict(s)
        entry["code"] = sub_agents(sub_secrets(entry.get("code", "")))
        if entry.get("agents"):
            # Belt and braces under the claim rule: two refs can't resolve to
            # one record, so the dedupe below should never drop anything —
            # but a step listing one ref twice must still land it once.
            seen: set[str] = set()
            out = []
            for g in entry["agents"]:
                lid = agent_id_by_ref[g["ref"]]
                if lid in seen:
                    continue
                seen.add(lid)
                out.append({"id": lid, **({"why": g["why"]} if g.get("why") else {})})
            entry["agents"] = out
        if entry.get("secrets"):
            seen = set()
            out = []
            for g in entry["secrets"]:
                lid = secret_id_by_ref[g["ref"]]
                if lid in seen:
                    continue
                seen.add(lid)
                out.append({"id": lid, **({"why": g["why"]} if g.get("why") else {})})
            entry["secrets"] = out
        steps.append(entry)
    ver = {"description": arch["description"], "note": "Imported", "params": arch["params"],
           "packages": arch["packages"], "steps": steps,
           "spec": arch["spec"], "instructions": arch["instructions"], "notes": arch["notes"]}
    triggers = [{"id": new_id(), "enabled": False,
                 **(t if t.get("kind") != "discord"
                    else {**t, "secret": secret_id_by_ref[t["secret"]]})}
                for t in arch["triggers"]]
    # §5.1 grants — auto-grant every match: the matched secrets, and the
    # matched step agents plus the resolved authoring agent (drafting first
    # when it was not already among them, so a bare `agent: true` step's
    # first-enabled-agent fallback lands on the authoring agent). Passed
    # directly into the creation call as its grant lists (one write) — no
    # post-create grant patch, so no window ever exists in which the
    # automation is stored with different grants. Nothing unresolved is ever
    # granted (an explicit empty list also overrides create_automation's
    # drafting-agent fallback).
    drafting = m["drafting"]
    enabled_agents: list[str] = [drafting["id"]] if drafting else []
    for e in arch["agents"]:
        rec = m["agents"][e["ref"]]
        if rec is not None and rec["id"] not in enabled_agents:
            enabled_agents.append(rec["id"])
    allowed_secrets: list[str] = []
    for e in arch["secrets"]:
        rec = m["secrets"][e["ref"]]
        if rec is not None and rec["id"] not in allowed_secrets:
            allowed_secrets.append(rec["id"])
    a = store.create_automation(ver, name=arch["name"],
                                agent_id=drafting["id"] if drafting else None,
                                triggers=triggers,
                                enabled_agents=enabled_agents,
                                allowed_secrets=allowed_secrets,
                                # §5.1: the manifest's platform token
                                # stamps §4.1 originOs — a mismatch flags
                                # the os-mismatch problem, never rejects.
                                origin_os=arch["os"],
                                unresolved_references=unresolved or None)
    if arch["param_values"]:
        # Values are the one manifest field creation can't seed. The
        # automation already landed — a failing values write degrades to an
        # import without them (the user re-enters values on the detail page)
        # rather than answering 500 for an automation that exists.
        try:
            store.patch_automation(a, {"paramValues": arch["param_values"]})
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception(
                "import of %r landed but its param values didn't apply", a.get("name"))
    return a, m


# ---------- URL import (§5.2) ----------
FETCH_TIMEOUT = 30                          # seconds, connect + read
_FETCH_CHUNK = 256 * 1024

_GH_REPO_RE = re.compile(r"^/([^/]+)/([^/]+?)(?:\.git)?(?:/releases/latest)?$")
_GH_TAG_RE = re.compile(r"^/([^/]+)/([^/]+)/releases/tag/([^/]+)$")


def _headers() -> dict:
    return {"User-Agent": f"autowright/{__version__}"}


def _github_api(path: str):
    """Unauthenticated GitHub API GET; None on 404, TransferError otherwise."""
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={**_headers(), "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code in (403, 429):
            raise TransferError("GitHub rate-limited the lookup — try again in a "
                                "few minutes, or paste the direct .autowright link") from None
        raise TransferError(f"GitHub answered {e.code} for {path}") from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
        raise TransferError(f"couldn't reach GitHub — {getattr(e, 'reason', e)}") from None
    except (json.JSONDecodeError, UnicodeDecodeError):
        # A captive portal / proxy answering HTML where the API's JSON belongs.
        raise TransferError("GitHub's answer wasn't readable — check the network "
                            "and try again") from None


def _release_asset(release) -> str | None:
    for a in (release or {}).get("assets") or []:
        if isinstance(a, dict) and str(a.get("name", "")).endswith(".autowright"):
            return a.get("browser_download_url")
    return None


def resolve_url(url: str) -> str:
    """§5.2: turn a pasted URL into a direct archive URL, or reject with 422."""
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise TransferError("only https:// URLs can be imported")
    if parts.path.endswith(".autowright"):
        return url
    if parts.hostname != "github.com":
        raise TransferError("the URL doesn't point to an .autowright archive — paste a "
                            "direct link to one, or a github.com repository page")
    path = parts.path.rstrip("/")
    if m := _GH_TAG_RE.match(path):
        owner, repo, tag = m.groups()
        asset = _release_asset(_github_api(f"/repos/{owner}/{repo}/releases/tags/{tag}"))
        if not asset:
            raise TransferError(f"release {tag!r} of {owner}/{repo} has no "
                                ".autowright asset")
        return asset
    if not (m := _GH_REPO_RE.match(path)):
        raise TransferError("unrecognized github.com URL — paste the repository page, a "
                            "release, or a direct .autowright link")
    owner, repo = m.groups()
    if asset := _release_asset(_github_api(f"/repos/{owner}/{repo}/releases/latest")):
        return asset
    listing = _github_api(f"/repos/{owner}/{repo}/contents/")
    files = sorted((f["name"], f.get("download_url"))
                   for f in (listing if isinstance(listing, list) else [])
                   if isinstance(f, dict) and f.get("type") == "file"
                   and str(f.get("name", "")).endswith(".autowright"))
    if files and files[0][1]:
        return files[0][1]
    raise TransferError(f"{owner}/{repo} has no .autowright archive — checked the latest "
                        "release's assets and the repository root")


def fetch_archive(url: str) -> tuple[bytes, str]:
    """§5.2 download: returns (archive bytes, resolved URL)."""
    resolved = resolve_url(url)
    req = urllib.request.Request(resolved, headers=_headers())
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            # urllib follows redirects — a hop off https would sidestep the
            # §5.2 HTTPS-only rule, so re-check the landing URL.
            if urlsplit(r.geturl()).scheme != "https":
                raise TransferError("the download redirected off https")
            chunks, total = [], 0
            while chunk := r.read(_FETCH_CHUNK):
                total += len(chunk)
                if total > MAX_ARCHIVE_BYTES:
                    raise TransferError("the download is larger than the 64 MB import limit")
                chunks.append(chunk)
    except urllib.error.HTTPError as e:
        raise TransferError(f"download failed — the server answered {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
        # HTTPException covers a truncated chunked download (IncompleteRead),
        # which is not an OSError.
        raise TransferError(f"download failed — {getattr(e, 'reason', e)}") from None
    return b"".join(chunks), resolved
