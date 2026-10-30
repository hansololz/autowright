"""Agent drafting pipeline (§8): one conversational pipeline, two call shapes.

The `chat` call is every editor turn (§11 chat column): framework instructions +
grants + build instructions + the agent's NOTES, the recent CONVERSATION, the
RECENT EXECUTIONS (test/draft/version output with log tails, assembled by the API
layer), the package install state, and the current draft (spec + steps) — and
the RESPONSE SHAPE decides the outcome: any subset of spec.md / notes.md /
actions.yaml blocks is a rewrite-plus-actions (validated per block),
plain prose is an answer, a blocker envelope blocks. A fresh draft's first
message is a chat call like any other — the §8 new-automation rule has the agent
write the spec, set name/description actions, and chain the build with
`sync: true`. The `sync` call builds the steps from the provided spec:
framework instructions + build instructions + the spec → manifest.yaml (params,
triggers) + step files. There is no create mode. Each envelope-shaped call is
followed by deterministic validation with up to AUTOWRIGHT_REPAIR_ROUNDS (§15,
default 1) automatic repair rounds — chat repairs are per-block: valid blocks are
kept and only the failed ones are re-asked-for, merged latest-wins; a valid
===BLOCKED=== envelope instead ends the job in the terminal `blocked` state with
the agent's blocker list (§8).
"""
from __future__ import annotations

import ast
import logging
import os
import re
import signal
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import yaml

from . import harness, packages as pkglib, paths, reqlog, triggers as triggerlib
from .events import hub
from .imports_check import ALLOWED_IMPORTS, disallowed_imports
from .specmd import blocks_to_md, md_to_blocks
from .storage import SECRET_REF_RE, step_json
from .yamlio import dump_yaml

log = logging.getLogger("autowright.drafting")

PARAM_KINDS = {"toggle", "list", "kv", "number", "text"}
# §4.2 param definition fields — a drafted param entry is normalized to exactly
# these (§8: every other key drops silently).
PARAM_FIELDS = ("name", "kind", "label", "help", "default", "min",
                "placeholder", "validate")
# §8 validator key rule: every key a manifest may carry — the §8 sync
# manifest's own plus the §20 workdir identity fields `pull` writes (`name`,
# `description`). Any other top-level key is a validation error.
MANIFEST_KEYS = ("name", "description", "note", "params", "test_values",
                 "packages", "triggers", "steps")
# §8 validator key rule: every key a step entry may carry (the per-step §4.1
# keys, snake_case). Any other step key is a validation error.
STEP_KEYS = ("file", "name", "description", "agent", "why", "agents", "secrets",
             "packages", "timeout", "no_timeout", "retries", "infinite_retries",
             "authored_by", "locked")
# §8 envelope shape constants — canonical in harness.py (its recombiner and
# scratch watcher need them, and harness never imports drafting); aliased
# here for the parsers and progress scanners below.
STEP_FILE_RE = harness.STEP_FILE_RE
FILE_MARK_RE = harness.FILE_MARK_RE
# §8 chat-call question type: a leading ===QUESTION=== line declares the
# answer prose a question to the user (stripped; rides the payload as
# answerKind). Anywhere else in the text it is ordinary prose.
QUESTION_MARK_RE = re.compile(r"^===QUESTION===[ \t]*\n?")


def split_answer_kind(prose: str) -> tuple[str, str | None]:
    """§8: strip a leading ===QUESTION=== from answer prose → (text, kind) —
    kind "question" when the marker was present, else None."""
    m = QUESTION_MARK_RE.match(prose)
    if m:
        return prose[m.end():].strip(), "question"
    return prose, None
BLOCKED_MARK_RE = harness.BLOCKED_MARK_RE
# §8: trailing \r tolerated — a harness on a CRLF stream (or an agent
# echoing one) must not read as a truncated response.
END_MARK_RE = re.compile(r"^===END===[ \t\r]*$", re.M)


# One process-wide lock for job event appends: appends touch one small list
# and never nest inside another lock, so contention across jobs is nil.
_EVENTS_LOCK = threading.Lock()


class _StreamScanner:
    """Incremental ===FILE:/===BLOCKED=== scanner for the §8 progress
    callbacks — O(chunk) per chunk, not O(accumulated text). Re-scanning the
    whole stream on every chunk went quadratic: measured ~26 s of CPU across
    a 240 KB Claude Code response, burned on the harness stdout read-loop
    thread, which stops draining the child's pipe and slows the whole call.
    Only a line-sized overlap window is re-scanned, so a marker split across
    chunk boundaries is still found. A match ending at the text's current
    end (no newline yet) is provisional: it is re-verified on the next feed
    and replaced or dropped as its line grows. Blocked detection is
    line-anchored (BLOCKED_MARK_RE), matching the recombiner — a quoted
    mid-line marker no longer mislabels the stream.

    Neither the accumulated text nor its line-start alignment is re-walked per
    chunk: the stream is kept as chunks and joined only when a caller reads
    `text`, and the scan works on a materialized tail (the overlap window
    extended back to its line start), so no step of a feed is O(stream)."""

    _OVERLAP = 1024  # > any marker line (===FILE: <name>=== + whitespace)
    # Cap on the retained tail — a stream with no newline at all would
    # otherwise grow the tail to the whole response. Past the cap the tail
    # starts mid-line, so a marker line longer than this is not re-found (no
    # valid marker line is anywhere near it).
    _MAX_TAIL = 64 * 1024

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._length = 0
        self._joined: str | None = ""
        # The scan window, materialized: text[_tail_start:], where _tail_start
        # is a line start (_tail_aligned says so — the cap above can cut one
        # mid-line).
        self._tail = ""
        self._tail_start = 0
        self._tail_aligned = True
        self.marks: list[tuple[str, int, int]] = []  # (name, start, end)
        self.blocked_at = -1
        self._blocked_end = -1

    @property
    def text(self) -> str:
        """Everything fed so far — joined on demand and cached until the next
        feed. `self.text += chunk` per chunk re-copied the whole buffer every
        time, the same quadratic cost the incremental scan removed."""
        if self._joined is None:
            self._joined = "".join(self._parts)
        return self._joined

    def feed(self, chunk: str) -> None:
        prev = self._length
        self._parts.append(chunk)
        self._length = prev + len(chunk)
        self._joined = None
        base = self._tail_start
        tail = self._tail + chunk

        def reverify(pattern, at: int) -> tuple[re.Match | None, int]:
            """Re-match `pattern` at absolute offset `at` — (match, the offset
            its positions are relative to). The tail carries it whenever it
            reaches back that far; a provisional match whose start ran off the
            front of the tail (a marker line trailed by more blank lines than
            the overlap window) falls back to the joined text."""
            if at >= base:
                return pattern.match(tail, at - base), base
            return pattern.match(self.text, at), 0

        # Re-verify provisional matches that touched the old end-of-text.
        if self.marks and self.marks[-1][2] >= prev:
            _, s, _ = self.marks[-1]
            m, origin = reverify(FILE_MARK_RE, s)
            if m:
                self.marks[-1] = (m.group(1).strip(), s, origin + m.end())
            else:
                self.marks.pop()
        if self.blocked_at >= 0 and self._blocked_end >= prev:
            m, origin = reverify(BLOCKED_MARK_RE, self.blocked_at)
            if m:
                self._blocked_end = origin + m.end()
            else:
                # The provisional blocked line grew into something else. An
                # even earlier genuine blocked line (fake-then-real) is not
                # re-found — label-only cosmetics; the settle parser decides
                # the real outcome.
                self.blocked_at = self._blocked_end = -1
        cut = max(0, prev - self._OVERLAP)
        # Align ^ to a line start, searching the tail only: the tail itself
        # begins at a line start, so no newline in it before `cut` means the
        # whole text has none there either and the tail start IS the alignment.
        offset = tail.rfind("\n", 0, cut - base) + 1
        start = base + offset
        aligned = self._tail_aligned or offset > 0
        if cut - start > self._MAX_TAIL:
            start, aligned = cut - self._MAX_TAIL, False
        region = tail[start - base:]
        for m in FILE_MARK_RE.finditer(region):
            if m.start() == 0 and not aligned:
                continue  # region[0] is mid-line: ^ would anchor on nothing
            s = start + m.start()
            if not self.marks or s > self.marks[-1][1]:
                self.marks.append((m.group(1).strip(), s, start + m.end()))
        for m in BLOCKED_MARK_RE.finditer(region):
            if m.start() == 0 and not aligned:
                continue
            s = start + m.start()
            if s > self.blocked_at:
                self.blocked_at, self._blocked_end = s, start + m.end()
        self._tail, self._tail_start, self._tail_aligned = region, start, aligned

# §8 file-writing delivery: the OUTPUT section appended to every drafting
# prompt on a file-writing harness (harness.writes_files) — the ONE
# per-harness difference in a prompt. The agent writes its response
# documents as real files in its cwd (the per-call scratch dir) so the
# scratch watcher can report them live; prose and blockers stay on stdout.
FILE_OUTPUT_BLOCK = """\
=== OUTPUT ===
Delivery override for this environment — this changes HOW you return files, nothing else:
- WRITE every file the TASK asks you to return as a real file in your current working
  directory, using the exact file name the TASK names (for example spec.md, manifest.yaml,
  01-fetch.py). Flat files only — no subdirectories.
- Do NOT print ===FILE: blocks or ===END=== to stdout. Print only your prose there (the
  answer or plan that would accompany the files).
- Write each file as soon as it is ready, one after another — do not batch them at the end.
- If you are blocked, print the blocker envelope (===BLOCKED=== … ===END===) to stdout
  exactly as the instructions specify; the ONLY file you may still write then is notes.md
  (the full updated notes) — write no other file.
- When asked to resend corrected files, follow the VALIDATION ERRORS note. If it lists
  blocks as kept, write only the failed files — the kept ones are held by the backend
  exactly as you wrote them and must not be resent. If it asks for the full envelope and
  lists nothing kept, write EVERY file again — the corrected and the unchanged ones
  alike. Each attempt starts with an empty working directory: on a full-envelope round
  a file you skip is lost."""
# Canonical in harness (the recombiner needs it too; drafting imports
# harness, never the reverse) — aliased so every drafting reference stays.
FENCE_OPEN_RE = harness.FENCE_OPEN_RE

# §8 prompt texts live as markdown next to the code so they can be read and
# edited without touching Python: framework-instructions.md travels with EVERY
# drafting call (role, envelope, SDK, §6 policies); build-instructions.md is the
# app's build policy, read here for every drafting call, never sent by a client
# and never returned by one. The per-call TASK directives below stay in Python
# because they define the exact envelope the validators parse.
_INSTRUCTIONS_DIR = Path(__file__).parent / "instructions"
CONTRACT_PREAMBLE = (_INSTRUCTIONS_DIR / "framework-instructions.md").read_text(encoding="utf-8")
BUILD_INSTRUCTIONS = (_INSTRUCTIONS_DIR / "build-instructions.md").read_text(encoding="utf-8")


def _per_os(text: str) -> str:
    """§8/§9: the checked-in instruction markdown names the user's machine via
    the literal {{MACHINE}} placeholder and the OS itself via {{OS}}; every
    consumer resolves both with the per-OS forms at read time — neither ever
    reaches a prompt or the UI."""
    return (text
            .replace("{{MACHINE}}", paths.machine_noun())
            .replace("{{OS}}", paths.os_display_name(paths.current_os())))


def contract_preamble() -> str:
    return _per_os(CONTRACT_PREAMBLE)


def build_instructions() -> str:
    return _per_os(BUILD_INSTRUCTIONS)


# §8: every prompt section opens with a `=== NAME ===` header — one dialect
# throughout, visually distinct from the envelope's ===FILE:/===END=== markers.
def _framework_section() -> str:
    return "=== FRAMEWORK INSTRUCTIONS ===\n" + contract_preamble()

# ---------- prompts ----------

STEPS_TASK = """=== TASK ===
Build the automation that implements the SPEC below, following the BUILD INSTRUCTIONS. Derive the triggers, every parameter (each with a default), and the steps from the SPEC — and add any trigger or parameter you judge the automation is missing (see Triggers and Parameters above; message-trigger details come from the SPEC, never invented). Return manifest.yaml plus one file block per step (none for a step listed under LOCKED STEPS below) — no spec.md (and no name/description keys — identity changes only through the chat call's actions). The skeleton below shows the shape; the Manifest, Triggers, Parameters, Timeouts, and Retries sections above hold the rules for every key:

===FILE: manifest.yaml===
note: One-line version note for the history menu
params:                                # each param MUST carry a default (kinds: see Parameters above)
  - { name: snake_case_name, kind: toggle|list|kv|number|text, label: ..., help: ..., default: ... }
test_values:                           # OPTIONAL: best-effort values for the user's first draft test;
  snake_case_name: value               # only params you can set confidently from the SPEC — omit the
                                       # rest (never guess, never passwords or tokens)
packages:                              # PyPI packages beyond the allowed list; omit when none are needed
  - { pip: pandas, import: pandas, why: one line — what the steps use the package for }
triggers:                              # see Triggers above; omit the whole key when the automation needs none
  - cron: "0 8 * * *"
  - every: "PT6H"                      # an interval: that long after the automation's last run
  - { discord: "1234567890", secret: 9b2f4e12-8c3d-4f6a-9e01-2b7c5d8a1f34 }   # details from the SPEC only
steps:                                 # ordered; every key is described in the Manifest section above
  - { file: 01-fetch.py, name: ..., description: ..., timeout: 60,
      secrets: [{ id: 9b2f4e12-8c3d-4f6a-9e01-2b7c5d8a1f34, why: authenticates the feed fetch }],   # API_TOKEN
      packages: [{ import: pandas, why: parses the fetched price tables }] }
  - { file: 02-judge.py, name: ..., description: ..., timeout: 180, agent: true,
      why: one line — why judgment is needed,
      agents: [{ id: 7c9e6679-7425-40de-944b-e07fc1f90ae7 }] }   # the granted agent's id
===FILE: 01-fetch.py===
(python source)
===END===

- Step names are non-blank and unique: no two steps share a name.

Return every file named in steps, except the locked ones."""

# §8 rule 8: the lock contract heading the LOCKED STEPS section of the sync
# prompt (present only when the draft holds a §4.1 locked step).
LOCKED_STEPS_CONTRACT = (
    "=== LOCKED STEPS (frozen by the user) ===\n"
    "The steps below are frozen by the user. List each one in `steps` at the position "
    "the pipeline needs it as `{ file: NN-<its stored file suffix>, name: <its name>, "
    "locked: true }` and nothing else (every other key is filled from the stored step), "
    "and return NO file block for it. Write the rest of the steps to work with the "
    "locked ones exactly as they are: their inputs, outputs, files, and memory keys are "
    "facts. When the SPEC cannot be implemented without changing, dropping, or "
    "duplicating a locked step — or without breaking a rule the SPEC states under "
    "`## Build rules` — do not build around it quietly and do not return steps: return "
    "a constraint blocker instead (the blocker envelope with `kind: constraint`, naming "
    "the step in `step` or quoting the rule in `rule`), explaining why, and proposing "
    "in `workaround` the best build that keeps the constraint. The user then decides: "
    "accept the workaround, or lift the constraint. Mere inconvenience is not a "
    "constraint blocker — a locked step that still works with the spec is simply kept.")

# §8 call-2 closing section: restates the response shape after the (possibly
# long) context so the format sits at the end of the prompt as well as in STEPS_TASK.
STEPS_REMINDER = ("=== RESPONSE REMINDER ===\n"
                  "Respond with manifest.yaml plus one file block per unlocked step "
                  "(none for a LOCKED step, no spec.md), ending with ===END=== exactly.")

# §9 per-OS copy rule: the diagnosis rule names the user's machine, but this
# literal can't be an f-string (it is full of yaml braces), so it carries the
# {{MACHINE}} placeholder that `_chat_task()` swaps for `paths.machine_noun()`
# — the same noun the SYSTEM TOOLS header uses. Never append CHAT_TASK to a
# prompt directly; always go through `_chat_task()`.
CHAT_TASK = """=== TASK ===
Decide what the USER REQUEST above needs:

- A question → answer it in plain markdown prose written for the user — no file blocks, no envelope, no yaml. Ground the answer in the SPEC, the CURRENT steps, and the RECENT EXECUTIONS shown above; when something isn't decided there, say so plainly. When your reply's PURPOSE is to ask the user for something you need to proceed, begin the response with ===QUESTION=== on its own line and lead with the ask — the question comes first, any explanation or answering after it; the app then presents it as a question awaiting their reply. A closing courtesy question ("does that answer it?", "want me to fix it?") is not a question response — no marker for those.

- A NEW automation (the SPEC above is empty) → the USER REQUEST is the automation's description: return the FULL spec.md written from it (don't promise AI judgment unless the enabled-agents list is nonempty), plus actions.yaml carrying `name`, `description`, and `sync: true` so the steps build in the same turn — unless the request is a question or asks you to hold off. Clarifications work like any other turn: when something only the user can supply is missing, ask in plain prose instead. A fresh spec's shape and tone, for example:

===FILE: spec.md===
# Track new manga chapters

## What it does
- Every morning at 8, check each manga page on my list for a new chapter.
- Only genuinely new chapters count — reprints and reissues don't.

## What I see
- A notification naming the new chapters, only on days something new appeared.
- The result lists each new chapter with its title and date.
===END===

- A change to the automation → return file blocks, any subset of these three (prose before the first block is shown to the user as your message):

===FILE: spec.md===
The FULL updated spec — markdown (# title first, then ## sections, - bullets, paragraphs) written for the user in plain words, no code, no yaml, no file names. Keep everything the request doesn't touch unchanged. Never return step files — the steps are rebuilt from the spec later. When the user asks to change how the automation is built (a standing rule), write the rule into spec.md in plain words (a "## Build rules" section is the usual home) and request sync; there is no build-instructions rewrite.
===FILE: notes.md===
The FULL updated notes — your own working knowledge for this automation: selectors, endpoints, quirks, approaches that failed and why, and the reason behind any non-obvious choice a later sync might otherwise simplify away (skip rationale evident from the steps themselves). Update it whenever you learn something a later build or fix should know; keep it a terse cheat sheet, not a log.
===FILE: actions.yaml===
sync: true                  # rebuild the steps from the spec right away (after your rewrites apply)
test: true                  # run a draft test once the steps match the spec (implies sync when they don't)
test_values: { url: "…" }   # parameter values for that test only (name → value, names from CURRENT parameters)
param_values: { url: "…" }  # stage stored values (same names rule) — they apply when the user saves
triggers:                   # stage trigger edits — applied when the user saves; ops touch only what they name
  - add: { cron: "0 9 * * *" }             # a Triggers-dialect entry; { time: "2026-07-20T15:00" } allowed here
  - add: { every: "PT6H" }                 # an interval: that long after the automation's last run
  - edit: { index: 1, cron: "30 8 * * *" } # replace entry 1's fields (id and on/off state kept)
  - enable: { index: 2, enabled: false }   # flip an entry on/off
  - remove: { index: 3 }                   # delete an entry (indexes from CURRENT triggers)
concurrency: { max_parallel: 2, max_queued: 5 }  # stage concurrency settings (one or both keys) — applied when the user saves
name: New automation name   # rename the automation (current name under AUTOMATION above)
description: One-line description  # rewrite its one-line description
undo: true                  # restore the draft to before the last request — exact revert, one level
===END===

- A change missing something only the user can supply (a channel id, a sender handle, which secret holds a token, which account or folder is meant) → ask for it in plain prose beginning with ===QUESTION=== on its own line, leading with the ask itself — any explanation follows the question; no file blocks, no actions, no blocker. Never guess the missing piece; ask for everything missing in one message, and the user's next message completes the request.

Only the keys shown are valid in actions.yaml; include only what the request calls for, and omit the block when no action is needed. When the user asks you to fix, change and verify, or "make it work" and the automation itself is at fault, prefer returning the rewrite together with `sync: true` (and `test: true` when a test would prove it) so the user doesn't have to press the buttons. Use `param_values` only for a value the user explicitly stated — never guessed, and never a password or token (those belong in secrets — say so in prose). Use `triggers` ops only on an explicit trigger request; before an `add`, check CURRENT triggers — if a matching trigger already exists, answer in prose with no op (if it exists but is off, return the `enable` op instead). A pure schedule change (cron or interval) is a `triggers` op alone — no spec rewrite, no sync; message-trigger details (channel id, which secret holds the token, sender handle) may come from the spec or from what the user typed in this conversation, never invented — a discord op's `secret` is that secret's id, copied exactly from the grants yaml (never its name). Use `concurrency` only when the user explicitly asks for parallel runs or queueing ("let two run at once", "queue messages when it's busy") — never speculatively; the defaults (max_parallel 1, max_queued 0) stay unless the user names different numbers or words you can map to them ("a couple at once" → 2). Staged values, trigger edits, and concurrency changes land when the user saves — say so ("staged — takes effect when you save"); for immediate effect point at the automation page. When the user asks to undo or revert your last change ("undo that", "put it back"), return `undo: true` ALONE — no other action keys and no rewrite blocks (an accompanying prose message is fine); the editor restores the draft exactly, and tells the user when there is nothing left to undo — never hand-rewrite the documents back from memory instead. You cannot enable agents or secrets, and you cannot save or create the automation — suggest those in prose; the user does them.

- A failure the user can't fix by changing the automation → when the RECENT EXECUTIONS show the failure comes from the user's {{MACHINE}}, not the steps — a missing desktop app, a daemon that isn't running (a pre-flight error, ConnectionRefusedError to a local service, "command not found") — do NOT rewrite the automation. Return a `kind: user-action` blocker: what to install or start, why the automation needs it, a markdown download link, and an offer of step-by-step install instructions.

Use the blocker envelope when a requested change is genuinely impossible, or when the real fix is user action outside this app (`kind: user-action`). A request that needs a step marked locked by the user changed is answered with a `kind: constraint` blocker naming that step (`step:`), never with a `sync` — only the user can unlock it."""


def _chat_task() -> str:
    """CHAT_TASK with the §9 per-OS machine noun substituted in — the single
    door onto the chat TASK section, so no caller can ship the placeholder."""
    return CHAT_TASK.replace("{{MACHINE}}", paths.machine_noun())


def spec_as_md(current: dict | None) -> str:
    """The spec may arrive as §5 blocks (stored versions) or as a raw markdown
    string (the §19 `spec` body field / in-editor draft) — yield markdown either way."""
    spec_val = (current or {}).get("spec") or []
    return spec_val if isinstance(spec_val, str) else blocks_to_md(spec_val)


def _grants_yaml(entries: list[dict]) -> str:
    """§8: grant lists render as yaml (agents: id/name/description/harness/model,
    secrets: id/name/description) so the authoring agent can weigh each entry when
    deciding which agents and secrets the automation should use — and copy the
    exact ids its manifest entries and code subscripts must carry."""
    if not entries:
        return "none"
    return yaml.safe_dump(entries, sort_keys=False, allow_unicode=True).strip()


def _common_context(current: dict | None, grants: dict) -> list[str]:
    """Grants + build instructions + system tools — the context stack both
    call shapes share."""
    parts = [
        "=== GRANTS FOR THIS AUTOMATION ===\n"
        "Enabled agents (yaml: id, name, description, harness, model; agent: true steps "
        "allowed only if nonempty; manifest agents: entries and agents[\"<id>\"] code "
        "subscripts carry the id, copied exactly):\n"
        f"{_grants_yaml(grants.get('agents', []))}\n"
        "Allowed secrets (yaml: id, name, description; reference in code by "
        "secrets[\"<id>\"] — the id copied exactly, always a literal quoted string, never "
        "a variable — with the secret's name in a trailing comment: "
        "secrets[\"<id>\"]  # NAME):\n"
        f"{_grants_yaml(grants.get('secrets', []))}\n"
        "One rule decides which agents and secrets each step uses: a choice the SPEC "
        "names wins; failing that, a choice the BUILD INSTRUCTIONS name; otherwise pick "
        "the most appropriate entries yourself."
    ]
    # §8/§5.1: the import's no-match map, right after the grants context —
    # the steps still carry placeholder ids for these, and a fix means
    # replacing each with a granted record (or dropping the reference).
    unresolved = (current or {}).get("unresolved_references") or {}
    parts.append(
        "=== IMPORTED REFERENCES THAT NEED FIXING ===\n"
        "These references came from an imported file and matched nothing on "
        f"this {paths.machine_noun()}. The steps still carry placeholder ids "
        "for them; when the user asks to fix the automation, replace each with "
        "a granted record from the lists above (or remove the reference).\n"
        + (yaml.safe_dump(
            [{"kind": e.get("kind"), "name": e.get("name"),
              "description": e.get("description") or ""}
             for e in unresolved.values()],
            sort_keys=False, allow_unicode=True).strip()
           if unresolved else "none"))
    # §8: the app's build instructions travel with every call — read here from
    # the shipped document, never sent by a client and never returned by a call.
    parts.append("=== BUILD INSTRUCTIONS (the app's default rules for building an "
                 "automation; never returned by any call; the SPEC overrides them "
                 "wherever it says otherwise) ===\n" + build_instructions())
    # §8 SYSTEM TOOLS: the §6 installed-tools probe, so the agent designs
    # against CLIs that really exist on this machine instead of hedging.
    tools = harness.probe_tools()
    parts.append(
        f"=== SYSTEM TOOLS (CLIs installed on this {paths.machine_noun()} — "
        "probed just now against "
        "the PATH steps run with) ===\n"
        "A listed tool is installed right now: steps may call it via subprocess "
        "(argv list), and the spec needn't hedge about installing it — but keep "
        "the shutil.which pre-flight, since a tool can be uninstalled before a "
        "run. The list is curated, not exhaustive: a tool not listed here may "
        "still exist — assume it may be present and build with the pre-flight "
        "as usual.\n"
        + (yaml.safe_dump(tools, sort_keys=False).strip() if tools else "none"))
    return parts


def step_authored_by(s: dict) -> str:
    """§4.1 `authoredBy` of a current step — `agent` when absent. Read in both
    spellings: the API key rides the §19 request as-is, the internal one is
    the §5 snake_case."""
    value = s.get("authoredBy", s.get("authored_by"))
    return "user" if value == "user" else "agent"


def step_locked(s: dict) -> bool:
    """§4.1 user-only lock of a current step (absent = false)."""
    return s.get("locked") is True


def locked_steps(current: dict | None) -> list[dict]:
    """The draft's §4.1 locked steps, in step order."""
    return [s for s in (current or {}).get("steps") or []
            if isinstance(s, dict) and step_locked(s)]


def locked_manifest_entry(s: dict, file: str | None) -> dict:
    """A stored step → its §8 manifest entry (snake_case dialect) under `file`:
    the locked-step reference the sync prompt shows and the splice that
    replaces the agent's entry whole (§8 rule 3). Reads the step flags in both
    spellings (API camelCase, internal snake_case)."""
    entry: dict = {"file": file, "name": s.get("name", ""),
                   "description": s.get("description", "")}
    if s.get("agent"):
        entry["agent"] = True
        entry["why"] = s.get("why", "")
        if s.get("agents"):
            entry["agents"] = [dict(x) for x in s["agents"] if isinstance(x, dict)]
    for key in ("secrets", "packages"):
        if s.get(key):
            entry[key] = [dict(x) for x in s[key] if isinstance(x, dict)]
    if s.get("timeout"):
        entry["timeout"] = s["timeout"]
    if s.get("no_timeout") or s.get("noTimeout"):
        entry["no_timeout"] = True
    if s.get("retries"):
        entry["retries"] = s["retries"]
    if s.get("infinite_retries") or s.get("infiniteRetries"):
        entry["infinite_retries"] = True
    if step_authored_by(s) == "user":
        entry["authored_by"] = "user"
    entry["locked"] = True
    return entry


def _step_head(s: dict) -> str:
    """CURRENT-step section header — carries the step's §4.1 time limit and
    retry budget so a sync rewrite can preserve a deliberately long/unlimited
    or retrying step."""
    extra = (", no timeout" if s.get("no_timeout")
             else f", timeout: {s['timeout']}s" if s.get("timeout") else "")
    extra += (", infinite retries" if s.get("infinite_retries")
              else f", retries: {s['retries']}" if s.get("retries") else "")
    return f"{s.get('file')} ({s.get('name')}{extra})"


def _conversation_lines(chat: list | None) -> str:
    """§8 chat-call CONVERSATION section: the most recent §11 thread entries as
    context — user, answer, and error text (clipped), one-line summaries for
    rewrite/blockers/system entries (a blocker keeps its clipped details, so a
    build-diagnosis failure's specifics reach later chats). Transient progress
    entries never travel, and §11 `activity` entries (a settled job's event
    feed) are deliberately skipped — operational noise, not conversation.
    Everything at or before the newest §4.4 boundary marker is a settled draft
    session's history and NEVER reaches the agent — clipped here again even
    though the editor already sends only post-boundary entries, so the
    guarantee holds against any client."""
    entries = [e for e in (chat or []) if isinstance(e, dict)]
    for i in range(len(entries) - 1, -1, -1):
        if entries[i].get("boundary"):
            entries = entries[i + 1:]
            break
    lines: list[str] = []
    for e in entries[-20:]:
        kind = e.get("kind")
        text = str(e.get("text") or "").strip()
        if kind == "user":
            lines.append("user: " + clip_response(text, 2000, 500))
        elif kind == "answer":
            lines.append("assistant: " + clip_response(text, 2000, 500))
        elif kind == "rewrite":
            lines.append("[spec updated] " + text)
        elif kind == "blockers":
            bl = "; ".join(
                ("(needs user action) " if b.get("kind") == "user-action"
                 else "(constraint) " if b.get("kind") == "constraint" else "")
                + f"{b.get('reason', '')} — {b.get('fix', '')}"
                + (f" ({clip_response(str(b['details']).strip(), 400, 0)})"
                   if b.get("details") else "")
                for b in (e.get("blockers") or []) if isinstance(b, dict))
            lines.append("[blockers] " + (bl or text))
        elif kind == "system":
            lines.append("[status] " + text)
        elif kind == "error":
            lines.append("[error] " + clip_response(text, 2000, 500))
    return "\n".join(lines)


def _notes_section(current: dict | None, hint: str) -> str | None:
    """§8 NOTES — the §4.1 agent-owned working-knowledge doc, sent on every
    call that has one so later work doesn't retry disproved approaches."""
    notes = ((current or {}).get("notes") or "").strip()
    if not notes:
        return None
    return ("=== NOTES (notes.md — your own working knowledge from earlier sessions, "
            "dead ends included; trust it before rediscovering" + hint + ") ===\n" + notes)


def build_chat_prompt(user_text: str | None, current: dict | None,
                      grants: dict, chat: list | None = None,
                      executions: str | None = None,
                      pkg_state: list[dict] | None = None) -> str:
    """§8 chat call — the ordinary context stack (framework + grants + build
    instructions) plus the agent's NOTES, the recent CONVERSATION, the RECENT
    RUNS (assembled by the API layer — test/draft/version output with log
    tails), the declared-package state, the AUTOMATION identity (name + description,
    editable only via actions.yaml), the in-editor spec, the CURRENT parameters
    (the names test_values keys must use), and every current step, closed by
    the USER REQUEST and the shape-deciding TASK."""
    parts = [_framework_section(), *_common_context(current, grants)]
    if notes := _notes_section(current, " — you may return an updated notes.md"):
        parts.append(notes)
    convo = _conversation_lines(chat)
    if convo:
        parts.append("=== CONVERSATION (recent messages in this editing session — "
                     "context only, never returned) ===\n" + convo)
    if executions:
        parts.append("=== RECENT EXECUTIONS (newest first — test/draft/version executions of this "
                     "automation with their output; an execution marked 'ran older steps' predates "
                     "the current draft) ===\n" + executions)
    if pkg_state:
        parts.append("=== PACKAGES (declared §6.2 packages and their install state) ===\n"
                     + yaml.safe_dump(pkg_state, sort_keys=False, allow_unicode=True).strip())
    # §8 AUTOMATION — the §4.1 user-owned identity, so a rename/redescribe
    # action edits what is really there (never returned as a file; actions only).
    name = str((current or {}).get("name") or "").strip()
    desc = str((current or {}).get("description") or "").strip()
    if name or desc:
        parts.append("=== AUTOMATION (current name and description — change them only "
                     "via actions.yaml `name` / `description`) ===\n"
                     + yaml.safe_dump({"name": name, "description": desc},
                                      sort_keys=False, allow_unicode=True).strip())
    parts.append("=== SPEC (spec.md — overrides the BUILD INSTRUCTIONS above wherever it "
                 "says otherwise) ===\n" + spec_as_md(current))
    # §8 CURRENT parameters — definitions + in-editor values; the only names
    # actions.yaml `test_values` / `param_values` keys may use.
    if params := (current or {}).get("params"):
        parts.append("=== CURRENT parameters (definitions and values — actions.yaml "
                     "test_values and param_values keys must be these names) ===\n"
                     + yaml.safe_dump(params, sort_keys=False, allow_unicode=True).strip())
    # §8 CURRENT triggers — indexed: the handle actions.yaml `triggers` ops
    # name entries by; edits are staged and land when the user saves.
    trigs = (current or {}).get("triggers")
    if trigs is not None:
        parts.append(
            "=== CURRENT triggers (the schedule and message triggers as they exist "
            "today, `index` first — the 1-based handle actions.yaml `triggers` ops "
            "name; `off` and one-shot `time` entries marked; staged edits land when "
            "the user saves) ===\n"
            + (yaml.safe_dump([{"index": i, **_trigger_ref(t)}
                               for i, t in enumerate(trigs, 1)],
                              sort_keys=False, allow_unicode=True).strip()
               if trigs else "none"))
    # §8 CURRENT concurrency — the §4.1 maxParallel/maxQueued pair, editable
    # only via the actions.yaml `concurrency` key; edits are staged until save.
    conc = (current or {}).get("concurrency") or {}
    parts.append(
        "=== CURRENT concurrency (how many executions may run at once and how "
        "many may queue when busy — change only via actions.yaml `concurrency`; "
        "edits are staged and land when the user saves) ===\n"
        + yaml.safe_dump({"max_parallel": conc.get("maxParallel", 1),
                          "max_queued": conc.get("maxQueued", 0)},
                         sort_keys=False).strip())
    for s in (current or {}).get("steps", []):
        # §8/§4.1: a locked or user-written step says so, so a chat answer
        # never promises a change a sync cannot make.
        note = (": locked by the user — a sync keeps it byte for byte" if step_locked(s)
                else ": written by the user" if step_authored_by(s) == "user" else "")
        parts.append(f"=== CURRENT step {_step_head(s)}{note} ===\n{s.get('code', '')}")
    parts.append("=== USER REQUEST ===\n" + (user_text or "").strip())
    parts.append(_chat_task())
    return "\n\n".join(parts)


def _trigger_ref(t: dict) -> dict:
    """Stored §4.3 trigger → the §8 rule-9 drafted dialect, for the sync
    reference. `time` renders as { time: at } and `off` is marked — both are
    context only, never part of what the agent may draft."""
    k = t.get("kind")
    if k == "cron":
        d = {"cron": t.get("expression"), **({"timezone": t["timezone"]} if t.get("timezone") else {})}
    elif k == "interval":
        d = {"every": t.get("every")}
    elif k == "imessage":
        d = {"imessage": t.get("from"),
             **({"pattern": t["pattern"]} if t.get("pattern") else {})}
    elif k == "discord":
        d = {"discord": t.get("channel"), "secret": t.get("secret"),
             **({"pattern": t["pattern"]} if t.get("pattern") else {}),
             **({"mention": True} if t.get("mention") else {}),
             **({"author": t["author"]} if t.get("author") else {})}
    elif k == "app_start":
        d = {"app_start": True}
    else:
        d = {"time": t.get("at")}
    if not t.get("enabled", True):
        d["off"] = True
    return d


def build_steps_prompt(spec_md: str, current: dict | None,
                       grants: dict) -> str:
    """§8 sync call — framework + build instructions + spec → manifest + step
    files. The draft's current implementation travels as reference when it
    holds any (a fresh draft's first build has none and simply omits it)."""
    parts = [_framework_section(), STEPS_TASK, *_common_context(current, grants)]
    if notes := _notes_section(
            current, " — context; you may return an updated notes.md beside the manifest"):
        parts.append(notes)
    has_ref = bool(current and (current.get("params") or current.get("steps")
                                or current.get("triggers")))
    if has_ref:
        parts.append("=== MODE ===\nsync — the CURRENT files below are today's implementation; "
                     "rewrite them to match the SPEC, changing no more than the spec demands.")
        parts.append("=== CURRENT param definitions ===\n"
                     + yaml.safe_dump(current.get("params", []), sort_keys=False))
        trigs = current.get("triggers")
        if trigs is not None:
            # §8: reference only — the §4.3 merge keeps user-added entries;
            # the agent drafts against this to see what already exists.
            parts.append(
                "=== CURRENT triggers (reference — user-owned; your drafted crons "
                "replace the spec-derived cron entries below (schedules the user set "
                "by hand survive), message/app-start entries only add "
                "when not already present; `time` and `off` entries are context, "
                "never drafted) ===\n"
                + (yaml.safe_dump([_trigger_ref(t) for t in trigs],
                                  sort_keys=False, allow_unicode=True).strip()
                   if trigs else "none"))
        # §8: the stored package declarations — the manifest must re-declare
        # every package a kept or locked step imports, and only the stored
        # `pip` name gets it right (`cv2` is `opencv-python`).
        pkgs = [{"pip": p.get("pip"), "import": p.get("import"), "why": p.get("why") or ""}
                for p in current.get("packages") or []
                if isinstance(p, dict) and p.get("pip") and p.get("import")]
        if pkgs:
            parts.append("=== CURRENT packages (declared today — re-declare each one a "
                         "kept or locked step still imports, under this stored pip name) ===\n"
                         + yaml.safe_dump(pkgs, sort_keys=False, allow_unicode=True).strip())
        for s in current.get("steps", []):
            # §8 rule 7: a user-written step is kept unless the SPEC requires a
            # change; a locked one is frozen (the LOCKED STEPS section below).
            note = (": locked by the user — see LOCKED STEPS below" if step_locked(s)
                    else ": written by the user — keep it unless the SPEC requires a "
                         "change, and say so in notes when you change it"
                    if step_authored_by(s) == "user" else "")
            parts.append(f"=== CURRENT step {_step_head(s)}{note} ===\n{s.get('code', '')}")
    # §8 rule 8: the draft's §4.1 locked steps and the lock contract, right
    # before the SPEC.
    if locked := locked_steps(current):
        parts.append(LOCKED_STEPS_CONTRACT)
        for s in locked:
            parts.append(
                f"=== LOCKED step {s.get('file')} ({s.get('name')}) ===\n"
                "manifest entry:\n"
                + yaml.safe_dump(locked_manifest_entry(s, s.get("file")),
                                 sort_keys=False, allow_unicode=True).strip()
                + "\nscript:\n" + str(s.get("code") or ""))
    parts.append("=== SPEC (spec.md — implement this exactly; it overrides the BUILD "
                 "INSTRUCTIONS above wherever it says otherwise) ===\n"
                 + (spec_md or "").strip())
    parts.append(STEPS_REMINDER)
    return "\n\n".join(parts)


DIAGNOSE_TASK = """=== TASK ===
Your previous response failed validation{attempts} — the VALIDATION ERRORS above are what the backend rejected. Diagnose why this automation could not be built as specified and respond with exactly one blocker envelope — no file blocks. For each blocker, `reason` names what went wrong in plain words and `fix` is the spec change or clarification that would let the build succeed:

===BLOCKED===
blockers:
  - reason: One sentence naming the problem.
    fix: The suggested resolution, in plain words.
    details: Optional longer explanation.
===END===
"""


def repair_rounds() -> int:
    """§8/§15 AUTOWRIGHT_REPAIR_ROUNDS: maximum automatic repair rounds per
    drafting call (default 1, clamped 0–5; 0 = no repair — an invalid response
    goes straight to the build diagnosis). Read per call, so a running backend
    picks up changes."""
    try:
        n = int(os.environ.get("AUTOWRIGHT_REPAIR_ROUNDS", "1"))
    except ValueError:
        return 1
    return max(0, min(5, n))


_TRY_ORDINALS = ("Second", "Third", "Fourth", "Fifth", "Sixth")


def try_prefix(round_no: int) -> str:
    """§8 progress prefix for repair round `round_no` (1-based): `Second try — `,
    `Third try — `, … (the rounds clamp keeps the ordinal table sufficient)."""
    return f"{_TRY_ORDINALS[min(round_no, len(_TRY_ORDINALS)) - 1]} try — "


def attempts_phrase(attempts: int) -> str:
    """§8 diagnosis wording for the total invalid-attempt count: '' for one,
    ' twice' for two, ' N times' beyond — appended to 'failed validation' /
    'didn't validate'."""
    return {1: "", 2: " twice"}.get(attempts, f" {attempts} times")


def clip_response(text: str, head: int = 60_000, tail: int = 20_000) -> str:
    """§8: repair/diagnosis prompts embed the previous raw response clipped —
    a huge bad response must not blow the model's context on the retry. The
    §5 app-log framing always logs it whole."""
    if len(text) <= head + tail:
        return text
    omitted = len(text) - head - tail
    return text[:head] + f"\n… [{omitted} chars omitted] …\n" + text[-tail:]


# ---------- envelope + validation ----------

def _strip_fence(content: str) -> str:
    """§8: a block whose whole content sits inside one markdown code fence loses
    the fence lines — models love wrapping step code in ```python. Split on
    "\n" only, never `splitlines()`: that also breaks on U+2028/U+2029/U+0085
    and \x0b/\x0c/\x1c-\x1e, and the rejoin would rewrite those code bytes
    (a string literal or a form feed) as newlines."""
    lines = content.split("\n")
    body = [i for i, l in enumerate(lines) if l.strip()]
    if len(body) < 2:
        return content
    first, last = body[0], body[-1]
    if FENCE_OPEN_RE.fullmatch(lines[first].strip()) and lines[last].strip() == "```":
        return "\n".join(lines[first + 1:last]).strip("\n")
    return content


# §8 shape-aware envelope start — canonical in harness (see
# file_mark_outside_fences there); aliased so the parse below and the
# recombiner can never disagree about where an envelope begins.
_file_mark_outside_fences = harness.file_mark_outside_fences


def parse_envelope(text: str) -> dict[str, str]:
    """Blocks by filename. Prose before the first marker is ignored. A block runs to
    the next ===FILE: marker or a line-anchored ===END===, whichever comes first —
    the canonical envelope closes once at the very end, but per-block ===END===
    terminators (and prose between blocks) parse identically (§8). No ===END=== at
    or after the last block → truncated.

    §8: the envelope starts at the first ===FILE: line OUTSIDE a markdown
    code fence — a reply whose every marker is fenced holds no blocks at all.
    Past that start every marker counts: a block's own content may be fenced
    (`_strip_fence`), so fence state is consulted only for the start."""
    if not END_MARK_RE.search(text):
        raise ValueError("response is truncated — no ===END=== marker")
    first = _file_mark_outside_fences(text)
    marks = list(FILE_MARK_RE.finditer(text, first.start())) if first else []
    if not marks:
        raise ValueError("no ===FILE: blocks in the response")
    if not END_MARK_RE.search(text, marks[-1].end()):
        raise ValueError("response is truncated — no ===END=== marker")
    files: dict[str, str] = {}
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        endm = END_MARK_RE.search(text, m.end(), end)
        if endm:
            end = endm.start()
        content = _strip_fence(text[m.end():end].strip("\n"))
        files[m.group(1).strip()] = content + "\n"
    return files


# §8 shape-aware blocker detection — canonical in harness (see
# blocked_mark_outside_fences there); aliased so drafting's parse and the
# recombiner can never disagree about what counts as a blocker envelope.
_blocked_mark_outside_fences = harness.blocked_mark_outside_fences


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)$")


def build_rules_bullets(spec_md: str | None) -> list[str]:
    """§8: the bullets under the SPEC's first h2 titled "Build rules" (case
    ignored, a trailing colon ignored), up to the next heading of the same or
    a higher level — the texts a constraint blocker's `rule` must quote. An
    indented non-bullet line continues the bullet above it; fenced code is
    never a heading or a bullet."""
    bullets: list[str] = []
    inside = False
    fenced = False
    current: str | None = None
    for line in (spec_md or "").split("\n"):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        heading = _HEADING_RE.match(line)
        if heading:
            level, title = len(heading.group(1)), heading.group(2).strip()
            if inside and level <= 2:
                break
            if not inside and level == 2 and title.rstrip(":").strip().lower() == "build rules":
                inside = True
            continue
        if not inside:
            continue
        bullet = _BULLET_RE.match(line)
        if bullet:
            if current is not None:
                bullets.append(current)
            current = bullet.group(1).strip()
        elif current is not None and line.strip() and line[:1].isspace():
            current += " " + line.strip()
        elif current is not None:
            bullets.append(current)
            current = None
    if current is not None:
        bullets.append(current)
    return [b for b in bullets if b]


def parse_blockers(text: str, locked_names: set[str] | None = None,
                   build_rules: list[str] | None = None
                   ) -> tuple[list[dict] | None, str | None]:
    """§8 blocker envelope → (blockers, notes). (None, None) when the response
    isn't one; the parsed nonempty blocker list when it is, plus the optional
    notes.md block's text (§8: a blocker response may carry ONE notes.md beside
    the envelope — the agent's working knowledge must survive a blocked build;
    any other file block stays forbidden); ValueError when it is a blocker but
    malformed (which sends it through the normal repair round like any invalid
    response). `locked_names` is the draft's §4.1 locked step names: a
    `kind: constraint` entry's `step` must be one of them (None = the set is
    unknown, so the check is skipped); names compare trimmed. `build_rules`
    is the SPEC's `## Build rules` bullets (`build_rules_bullets`): a
    constraint entry's `rule` must be a case-insensitive substring of one of
    them, so §11 "Remove the rule" can find it (None = skipped)."""
    if locked_names is not None:
        locked_names = {str(n).strip() for n in locked_names}
    m = _blocked_mark_outside_fences(text)
    if not m:
        return None, None
    endm = END_MARK_RE.search(text, m.end())
    if not endm:
        raise ValueError("blocker response is truncated — no ===END=== marker")
    notes = None
    # §8: only a file block *beside* the envelope counts - a ===FILE: line
    # quoted inside the yaml body is body text, not a block, and (rule 1) a
    # marker the prose fences is quoted prose, so a blocker that SHOWS an
    # envelope carries no notes rather than failing the response.
    remainder = text[:m.start()] + text[endm.end():]
    if _file_mark_outside_fences(remainder):
        # The envelope's own span is cut out, so the remainder must parse as a
        # plain file envelope holding exactly the optional notes.md.
        try:
            files = parse_envelope(remainder)
        except ValueError:
            raise ValueError("a blocker envelope may carry only one notes.md block "
                             "beside it — close the block with its own ===END===")
        extras = sorted(f for f in files if f != "notes.md")
        if extras:
            raise ValueError("a blocker envelope must not carry file blocks "
                             f"(only an optional notes.md) — got {extras}")
        notes = (files.get("notes.md") or "").strip() or None
    try:
        data = yaml.safe_load(text[m.end(): endm.start()])
    except (yaml.YAMLError, ValueError, RecursionError) as e:
        raise ValueError(f"blocker envelope doesn't parse as yaml: {e}")
    blockers = data.get("blockers") if isinstance(data, dict) else None
    if not isinstance(blockers, list) or not blockers:
        raise ValueError("the blocker envelope needs a nonempty `blockers` list")
    out = []
    for b in blockers:
        if not isinstance(b, dict) or not str(b.get("reason") or "").strip() \
                or not str(b.get("fix") or "").strip():
            raise ValueError("every blocker needs a nonempty reason and fix")
        entry = {"reason": str(b["reason"]).strip(), "fix": str(b["fix"]).strip(),
                 "details": str(b.get("details") or "").strip()}
        kind = b.get("kind")
        if kind is not None:
            if kind not in ("user-action", "constraint"):
                raise ValueError("a blocker's `kind`, when present, must be the literal "
                                 "user-action or constraint")
            entry["kind"] = kind
        # §8 constraint blocker: the SPEC conflicts with a §4.1 locked step or a
        # stated build rule — exactly one of `step` / `rule`, plus an optional
        # `workaround`; the three keys belong to that kind alone.
        extra = [k for k in ("step", "rule", "workaround") if b.get(k) is not None]
        if kind != "constraint":
            if extra:
                raise ValueError(f"a blocker's {', '.join(f'`{k}`' for k in extra)} "
                                 "is only valid on a `kind: constraint` blocker")
        else:
            step = str(b.get("step") or "").strip()
            rule = str(b.get("rule") or "").strip()
            if bool(step) == bool(rule):
                raise ValueError("a constraint blocker needs exactly one of a nonempty "
                                 "`step` (the locked step's name) or `rule` (the build "
                                 "rule's text, quoted from the SPEC)")
            if step and locked_names is not None and step not in locked_names:
                raise ValueError(
                    f"a constraint blocker's `step` must name a locked step — `{step}` "
                    "isn't one (locked: "
                    + (", ".join(f"`{n}`" for n in sorted(locked_names)) or "none") + ")")
            if rule and build_rules is not None and not any(
                    rule.lower() in b.lower() for b in build_rules):
                raise ValueError(
                    f"a constraint blocker's `rule` must quote a bullet under the SPEC's "
                    f"`## Build rules` — `{rule}` isn't part of one"
                    + ("" if build_rules else " (the SPEC has no Build rules bullets)"))
            workaround = str(b.get("workaround") or "").strip()
            if b.get("workaround") is not None and not workaround:
                raise ValueError("a constraint blocker's `workaround`, when present, "
                                 "must be nonempty")
            if step:
                entry["step"] = step
            if rule:
                entry["rule"] = rule
            if workaround:
                entry["workaround"] = workaround
        out.append(entry)
    return out, notes


def validate_spec(files: dict[str, str]) -> tuple[dict, list[str]]:
    """§8 spec-document validation (chat rewrites, CLI workdirs). Returns
    ({md, blocks}, errors)."""
    errors: list[str] = []
    if "spec.md" not in files:
        errors.append("spec.md is missing")
    extras = sorted(f for f in files if f != "spec.md")
    if extras:
        errors.append(f"the spec call must return spec.md and nothing else (got {extras})")
    if errors:
        return {}, errors
    md = files["spec.md"]
    blocks = md_to_blocks(md)
    if not blocks or blocks[0].get("kind") != "h1" or not blocks[0].get("text", "").strip():
        errors.append("spec.md must start with a # title")
    if not any(b.get("kind") in ("p", "li") for b in blocks):
        errors.append("spec.md has no body — describe the automation")
    if errors:
        return {}, errors
    return {"md": md, "blocks": blocks}, []


CHAT_FILES = ("spec.md", "notes.md", "actions.yaml")


def parse_dialect_entry(t, allow_time: bool = False, *,
                        cron_source: str) -> tuple[dict | None, str | None]:
    """One §8 rule-9 dialect entry → (normalized §4.3 stored trigger, None) or
    (None, error). Shared by the manifest's `triggers` key (schedules land
    `source: spec`) and the chat call's `triggers` ops (schedules land
    `source: user`; `allow_time` admits the `{ time: at }` form the user may
    ask for directly — never drafted by judgment, §8 rule 9). `cron_source`
    is required — §4.3 provenance is stamped at every ingest."""
    if not isinstance(t, dict):
        return None, f"triggers entry {t!r} must be an object"
    keys = set(t)
    if keys == {"cron"} or keys == {"cron", "timezone"}:
        entry = {"kind": "cron", "expression": str(t["cron"]).strip(), "enabled": True,
                 "source": cron_source}
        if "timezone" in t:
            entry["timezone"] = str(t["timezone"])
            if err := triggerlib.timezone_error(entry["timezone"]):
                return None, f"triggers: {err}"
        try:
            triggerlib.parse_cron(entry["expression"])
        except triggerlib.CronError as e:
            return None, f"triggers: {e}"
        return entry, None
    if keys == {"every"}:
        # §4.3 interval: a duration, no timezone. Canonicalized HERE, not
        # only at store time: the draft payload rides to the renderer's §4.3
        # trigger merge, which compares `every` strings — a manifest PT360M
        # must match a stored PT6H as one trigger.
        entry = {"kind": "interval", "every": str(t["every"]).strip(), "enabled": True,
                 "source": cron_source}
        if err := triggerlib.validate_trigger(entry):
            return None, f"triggers: {err}"
        entry["every"] = triggerlib.canonical_duration(triggerlib.parse_duration(entry["every"]))
        return entry, None
    if allow_time and "time" in keys and keys <= {"time", "timezone"}:
        entry = {"kind": "time", "at": str(t["time"]).strip(), "enabled": True,
                 **({"timezone": str(t["timezone"])} if t.get("timezone") else {})}
        if err := triggerlib.validate_trigger(entry):
            return None, f"triggers: {err}"
        return entry, None
    if "imessage" in keys and keys <= {"imessage", "pattern"}:
        entry = {"kind": "imessage",
                 "from": triggerlib.normalize_handle(str(t["imessage"])), "enabled": True,
                 **({"pattern": str(t["pattern"]).strip()} if t.get("pattern") else {})}
        if err := triggerlib.validate_trigger(entry):
            return None, f"triggers: {err}"
        return entry, None
    if "discord" in keys and keys <= {"discord", "secret", "pattern", "mention", "author"}:
        # author: scalar accepted as shorthand for a one-element list (§8)
        au = t.get("author")
        au = au if isinstance(au, list) else [au] if au else []
        entry = {"kind": "discord", "channel": str(t["discord"]).strip(),
                 "secret": str(t.get("secret", "")).strip(), "enabled": True,
                 **({"pattern": str(t["pattern"]).strip()} if t.get("pattern") else {}),
                 **({"mention": True} if t.get("mention") is True else {}),
                 **({"author": triggerlib.normalize_authors(au)} if au else {})}
        if err := triggerlib.validate_trigger(entry):
            return None, f"triggers: {err}"
        return entry, None
    if t == {"app_start": True}:
        return {"kind": "app_start", "enabled": True}, None
    return None, (
        f"triggers entry {t!r} must be {{ cron: expression[, timezone] }}, "
        "{ every: duration }, "
        + ("{ time: local-ISO-timestamp[, timezone] }, " if allow_time else "")
        + "{ imessage: handle[, pattern] }, "
        "{ discord: channel-id, secret: <granted secret id>[, pattern, mention, author] }, "
        "or app_start: true")


def validate_actions(text: str, param_names: list[str] | None = None,
                     triggers_count: int | None = None) -> tuple[dict, list[str]]:
    """§8 actions.yaml — the chat call's follow-up actions. Returns the
    validated mapping in the §4.1 camelCase serialization, or errors. Grants
    and save/create are never actions (§8 hard boundaries) — an unknown key is
    a validation error, not a silent drop. `param_names` (when given) are the
    only keys `test_values`/`param_values` may use — a misremembered name is a
    validation error that feeds the repair round, never a test that silently
    runs with defaults. `triggers_count` is the CURRENT triggers list's length
    — the range `triggers` op indexes must fall in."""
    try:
        data = yaml.safe_load(text)
    except (yaml.YAMLError, ValueError, RecursionError) as e:
        # §5: PyYAML raises ValueError on an impossible date (2026-13-45)
        # and RecursionError on a deeply nested document — both are agent
        # output errors that feed the repair round, never a 500.
        return {}, [f"actions.yaml doesn't parse as yaml: {e}"]
    if not isinstance(data, dict):
        return {}, ["actions.yaml must be a yaml mapping"]
    errors: list[str] = []
    out: dict = {}
    for k in data:
        if k not in ("sync", "test", "test_values", "param_values", "triggers",
                     "concurrency", "name", "description", "undo"):
            errors.append(f"actions.yaml: unknown key {k!r}")
    # §8: undo is exclusive — undoing and acting/rewriting in one response is
    # contradictory (the rewrite-block half is enforced in validate_chat_files)
    if "undo" in data and len(data) > 1:
        errors.append("actions.yaml: undo must be the only key — it cannot be "
                      "combined with other actions")
    for k in ("sync", "test", "undo"):
        if k in data:
            if data[k] is not True:
                errors.append(f"actions.yaml: {k} must be true when present")
            else:
                out[k] = True
    # A response that also requests a sync may name params the rebuild will
    # create — only a map applied against today's params is checkable (§8).
    checkable = param_names is not None and data.get("sync") is not True
    for key, dest in (("test_values", "testValues"), ("param_values", "paramValues")):
        if key in data:
            if not isinstance(data[key], dict):
                errors.append(f"actions.yaml: {key} must be a mapping of param name → value")
            else:
                if checkable:
                    bad = sorted(str(k) for k in data[key] if k not in param_names)
                    if bad:
                        errors.append(
                            f"actions.yaml: {key} names unknown params {bad} — "
                            f"the automation's params are {sorted(param_names) or 'none'}")
                out[dest] = data[key]
    if "triggers" in data:
        ops, errs = _validate_trigger_ops(data["triggers"], triggers_count or 0)
        errors += errs
        if not errs:
            out["triggers"] = ops
    if "concurrency" in data:
        conc, errs = _validate_concurrency(data["concurrency"])
        errors += errs
        if not errs:
            out["concurrency"] = conc
    for k in ("name", "description"):
        if k in data:
            if not isinstance(data[k], str) or not data[k].strip():
                errors.append(f"actions.yaml: {k} must be a nonempty string")
            else:
                out[k] = data[k].strip()
    if not errors and not out:
        errors.append("actions.yaml carries no actions — omit the block instead")
    return ({}, errors) if errors else (out, [])


def _validate_concurrency(raw) -> tuple[dict, list[str]]:
    """§8 `concurrency` action — a mapping holding one or both of
    `max_parallel` (int ≥ 1) and `max_queued` (int ≥ 0) and nothing else.
    Returns the §4.1 camelCase object, or the errors feeding the repair round."""
    if not isinstance(raw, dict) or not raw:
        return {}, ["actions.yaml: concurrency must be a mapping with "
                    "max_parallel and/or max_queued"]
    errors: list[str] = []
    out: dict = {}
    floors = {"max_parallel": 1, "max_queued": 0}
    dests = {"max_parallel": "maxParallel", "max_queued": "maxQueued"}
    for k, v in raw.items():
        if k not in floors:
            errors.append(f"actions.yaml: unknown concurrency key {k!r} — use "
                          "max_parallel and/or max_queued")
        elif not isinstance(v, int) or isinstance(v, bool) or v < floors[k]:
            errors.append(f"actions.yaml: concurrency {k} must be an integer "
                          f"≥ {floors[k]}, got {v!r}")
        else:
            out[dests[k]] = v
    return ({}, errors) if errors else (out, [])


def _validate_trigger_ops(raw, count: int) -> tuple[list[dict], list[str]]:
    """§8 `triggers` action — a list of single-op mappings (add / edit /
    enable / remove), indexes 1-based over the CURRENT triggers list. Returns
    the ops with their dialect entries normalized to the §4.3 stored shape
    (crons and intervals land `source: user` — user-asked schedules survive
    later syncs, §4.3), or the validation errors that feed the repair round."""
    if not isinstance(raw, list) or not raw:
        return [], ["actions.yaml: triggers must be a nonempty list of "
                    "add/edit/enable/remove ops"]
    errors: list[str] = []
    ops: list[dict] = []

    def op_index(val) -> int | None:
        if not isinstance(val, int) or isinstance(val, bool) or not 1 <= val <= count:
            errors.append(
                f"actions.yaml: triggers op index {val!r} is out of range — the "
                f"CURRENT triggers list has {count} "
                + ("entry" if count == 1 else "entries"))
            return None
        return val

    for op in raw:
        if not isinstance(op, dict) or len(op) != 1:
            errors.append(f"actions.yaml: triggers entry {op!r} must be exactly one of "
                          "add: {…}, edit: {…}, enable: {…}, remove: {…}")
            continue
        (name, val), = op.items()
        if name == "add":
            entry, err = parse_dialect_entry(val, allow_time=True, cron_source="user")
            if err:
                errors.append(f"actions.yaml: {err}")
            else:
                ops.append({"op": "add", "trigger": entry})
        elif name == "edit":
            if not isinstance(val, dict) or "index" not in val:
                errors.append(f"actions.yaml: triggers edit {val!r} needs an index "
                              "beside the trigger fields")
                continue
            idx = op_index(val["index"])
            entry, err = parse_dialect_entry({k: v for k, v in val.items() if k != "index"},
                                             allow_time=True, cron_source="user")
            if err:
                errors.append(f"actions.yaml: {err}")
            elif idx is not None:
                ops.append({"op": "edit", "index": idx, "trigger": entry})
        elif name == "enable":
            if not isinstance(val, dict) or set(val) != {"index", "enabled"} \
                    or not isinstance(val.get("enabled"), bool):
                errors.append(f"actions.yaml: triggers enable {val!r} must be "
                              "{ index: N, enabled: true|false }")
                continue
            if (idx := op_index(val["index"])) is not None:
                ops.append({"op": "enable", "index": idx, "enabled": val["enabled"]})
        elif name == "remove":
            if not isinstance(val, dict) or set(val) != {"index"}:
                errors.append(f"actions.yaml: triggers remove {val!r} must be {{ index: N }}")
                continue
            if (idx := op_index(val["index"])) is not None:
                ops.append({"op": "remove", "index": idx})
        else:
            errors.append(f"actions.yaml: unknown triggers op {name!r} — use "
                          "add, edit, enable, or remove")
    return (ops, []) if not errors else ([], errors)


def validate_chat_files(files: dict[str, str],
                        param_names: list[str] | None = None,
                        triggers_count: int | None = None
                        ) -> tuple[dict, list[str], set[str]]:
    """§8 chat-call blocks → (payload sans answer, errors, failed block names).
    Every error attributes to exactly one block — an unknown block name to
    itself, the undo-with-rewrite conflict to actions.yaml — so the per-block
    repair can keep the valid blocks and re-ask only for the failed ones.
    `param_names` gates actions.yaml test_values/param_values keys;
    `triggers_count` the triggers-op index range."""
    errors: list[str] = []
    bad: set[str] = set()
    extras = sorted(f for f in files if f not in CHAT_FILES)
    if extras:
        # §8: name what actually arrived — a stray document (an
        # `instructions.md` rewrite, a manifest) is not a step file, and the
        # per-block repair has to know which block to drop.
        errors.append("a chat response may only return spec.md, notes.md, and "
                      + (f"actions.yaml — never step files (got {extras})"
                         if all(STEP_FILE_RE.match(f) for f in extras)
                         else f"actions.yaml — not {', '.join(extras)}"))
        bad.update(extras)
    payload: dict = {}
    if "spec.md" in files:
        spec, errs = validate_spec({"spec.md": files["spec.md"]})
        errors += errs
        if errs:
            bad.add("spec.md")
        else:
            payload["spec"] = spec["blocks"]
    if "notes.md" in files:
        payload["notes"] = files["notes.md"].strip()
    if "actions.yaml" in files:
        # A spec rewrite means the params will be re-derived — test_values
        # keys are only checkable when today's params stay authoritative.
        actions, errs = validate_actions(files["actions.yaml"],
                                         None if "spec.md" in files else param_names,
                                         triggers_count)
        errors += errs
        # §8: undo is exclusive of rewrites too — restoring the draft and
        # rewriting it in one response is contradictory.
        conflict = actions.get("undo") and any(
            f in files for f in ("spec.md", "notes.md"))
        if conflict:
            errors.append("actions.yaml: undo cannot be combined with spec.md "
                          "or notes.md rewrites")
        if errs or conflict:
            bad.add("actions.yaml")
        else:
            payload["actions"] = actions
    if errors:
        return {}, errors, bad
    return payload, [], set()


# §8 rules 6/7: the validation scan for code subscripts matches ANY quoted
# literal, not only uuid-shaped ones — `secrets["API_TOKEN"]` (the name where
# the id belongs, the likeliest slip) must read as the not-allowed validation
# error here, never sail through to a version that fails at runtime. Local to
# drafting: storage's SECRET_REF_RE / AGENT_REF_RE stay uuid-only, because the
# engine and §20 transfer resolve real references through them.
SECRET_SUBSCRIPT_RE = re.compile(r"\bsecrets\[\s*[\"']([^\"']+)[\"']\s*\]")
AGENT_SUBSCRIPT_RE = re.compile(r"\bagents\[\s*[\"']([^\"']+)[\"']\s*\]")


def _granted_list(names: dict) -> str:
    """§8 error copy: the granted entries as `Name (id)` — the agent thinks in
    names but must copy ids."""
    return ", ".join(f"{n} ({i})" for i, n in names.items()) or "none"


def _imported_no_match_copy(unresolved: dict | None, kind: str, entry_id: str,
                            pick: str) -> str | None:
    """§8/§5.1: the specialized copy for an id the import minted for a
    reference with no local match — the same words the §9.2/§11 red
    surfaces use, so the user and the agent see one explanation."""
    entry = (unresolved or {}).get(entry_id)
    if entry and entry.get("kind") == kind:
        return (f"this step still uses {entry['name']}, which came from the "
                f"imported file and has no match on this {paths.machine_noun()}. "
                f"Pick one of your {pick} or remove the reference.")
    return None


def validate_step_refs(s: dict, code: str, declared_agent_ids: set[str],
                       declared_package_imports: list[str],
                       allowed_secret_names: dict | None = None,
                       unresolved: dict | None = None) -> list[str]:
    """§8 rules 5-7, the per-step reference checks shared by `validate_steps`
    (the save and sync path) and the §5.1 import: the code's
    `secrets["<id>"]` literals against the allowed secrets
    (`allowed_secret_names`, id → name; skipped when None), its
    `agents["<id>"]` literals against the step's OWN declared agent ids, and
    the step's `packages` entries against the manifest's declared package
    imports, each with its per-use why. Not the §6.2 import allowlist — that
    stays `validate_steps`'s own. Returns the error lines in order."""
    errors: list[str] = []
    secret_names = allowed_secret_names
    declared_agents = declared_agent_ids
    pkg_imports = declared_package_imports

    def _granted(names: dict) -> str:
        return _granted_list(names)

    def _imported_no_match(kind: str, entry_id: str, pick: str) -> str | None:
        return _imported_no_match_copy(unresolved, kind, entry_id, pick)

    # §8 rule 6: every secrets["<id>"] literal in code must be an allowed
    # secret's id — at runtime it would raise, so it fails validation here.
    for ref in sorted(set(SECRET_SUBSCRIPT_RE.findall(code))
                      if secret_names is not None else ()):
        if ref not in secret_names:
            errors.append(f"step {s.get('name')}: "
                          + (_imported_no_match("secret", ref, "secrets")
                             or f"code subscripts secrets[{ref!r}], which isn't among "
                                f"the allowed secrets — allowed: {_granted(secret_names)}"))
    # §8 rule 7: agents["<id>"] in code only resolves against the step's OWN
    # declared entries at runtime — the runtime container holds nothing
    # else. The scan runs on EVERY step, beside the secrets one: a step
    # that declares no entries (an agent step with no `agents` list, or a
    # plain step that is not an agent step at all) dangles the same way,
    # and only this scan catches it.
    for ref in sorted(set(AGENT_SUBSCRIPT_RE.findall(code))):
        if ref in declared_agents:
            continue
        errors.append(
            f"step {s.get('name')}: code subscripts agents[{ref!r}], "
            "which isn't among this step's declared agents entries"
            if declared_agents else
            f"step {s.get('name')}: code subscripts agents[{ref!r}] but the "
            "step declares no agents entries")
    pkgs = s.get("packages")
    if pkgs is not None:
        if (not isinstance(pkgs, list)
                or not all(isinstance(x, dict) and isinstance(x.get("import"), str) for x in pkgs)):
            errors.append(f"step {s.get('name')}: packages must be a list of "
                          "{ import, why } declared-package entries")
        else:
            for x in pkgs:
                if x["import"] not in pkg_imports:
                    errors.append(f"step {s.get('name')}: package {x['import']!r} isn't among "
                                  "the manifest's declared packages")
                # §8 rule 5: every per-step entry carries its per-use note —
                # one package can serve different jobs in different steps.
                if not str(x.get("why") or "").strip():
                    errors.append(f"step {s.get('name')}: package {x['import']!r} needs a why — "
                                  "one line on what this step uses it for")
    return errors


def validate_steps(files: dict[str, str], grants: dict | None = None,
                   trigger_secret_ids: set[str] | None = None,
                   unresolved: dict | None = None,
                   locked_steps: list[dict] | None = None,
                   current_steps: list[dict] | None = None) -> tuple[dict, list[str]]:
    """§8 sync-call validation. Returns (draft dict sans spec, errors). `grants`
    holds the call's agent/secret grant entries — per-step `agents`/`secrets`
    lists must name entries from them. `trigger_secret_ids` are the CURRENT
    triggers' token-secret ids: a drafted discord trigger's `secret` must be a
    granted secret's id OR one of these — re-emitting an existing trigger
    through the §4.3 merge must never fail on a token that was (correctly)
    never step-granted. `unresolved` is the automation's §4.1
    unresolved_references map when one exists: an ungranted id it carries gets
    the §8 imported-file error copy instead of a raw id, so the user and the
    repair round see what the §5.1 import wanted.

    `locked_steps` / `current_steps` mark a §8 sync call (the §19 save gate
    and the §20 CLI pass neither): `locked_steps` is the draft's §4.1 locked
    steps (the reference set of the rule-3 lock checks — each is spliced back
    in whole), `current_steps` every current step (the §4.1 keep-`user`
    authorship rule). Outside a sync, manifest `authored_by` / `locked` ride
    through as written."""
    errors: list[str] = []
    syncing = locked_steps is not None or current_steps is not None
    files = dict(files)
    if "manifest.yaml" not in files:
        errors.append("manifest.yaml is missing")
    if "spec.md" in files:
        errors.append("the steps call must not return spec.md — the spec is already settled")
    if errors:
        return {}, errors
    try:
        manifest = yaml.safe_load(files["manifest.yaml"]) or {}
    except (yaml.YAMLError, ValueError, RecursionError) as e:  # same set as actions.yaml
        return {}, [f"manifest.yaml doesn't parse: {e}"]
    if not isinstance(manifest, dict):
        return {}, ["manifest.yaml must be a mapping"]

    # §8 key rule: an unknown manifest key is an error, never dropped — a
    # misspelled `trigger:` must not save as "no triggers" without a word.
    for key in manifest:
        if key not in MANIFEST_KEYS:
            errors.append(f"unknown manifest key: {key}")

    # §8 rule 3: the manifest's text fields are checked, not assumed — YAML
    # 1.1 turns `note: on` into a boolean and a mis-indented block into a
    # mapping, and either would land verbatim in the version and break the
    # §9.2 page and the §5.1 importer, which already rejects them.
    if not isinstance(manifest.get("note", ""), str):
        errors.append("manifest `note` must be a string")

    params = manifest.get("params") or []
    if not isinstance(params, list):
        errors.append("params must be a list of param entries")
        params = []
    norm_params: list[dict] = []
    seen_params: set[str] = set()
    reported_params: set[str] = set()
    for p in params:
        if not isinstance(p, dict) or "name" not in p or "kind" not in p:
            errors.append(f"param entry malformed: {p!r}")
            continue
        # §8 rule 3: a param name is a nonempty string — nothing else names a
        # §4.2 param, and the checks below read it as text.
        if not isinstance(p["name"], str) or not p["name"].strip():
            errors.append(f"param entry malformed: {p!r} — `name` must be a "
                          "nonempty string")
            continue
        # §8 rule 3: two entries of one name would silently collapse in the
        # editor — one error per repeated name feeds the repair round.
        if p["name"] in seen_params and p["name"] not in reported_params:
            reported_params.add(p["name"])
            errors.append(f"param `{p['name']}` is declared twice")
        seen_params.add(p["name"])
        if not isinstance(p["kind"], str):
            errors.append(f"param {p['name']}: `kind` must be a string")
            continue
        if p["kind"] not in PARAM_KINDS:
            errors.append(f"param {p['name']}: unknown kind {p['kind']}")
        if "default" not in p:
            errors.append(f"param {p['name']}: missing default")
        if p["kind"] == "number" and "min" not in p:
            p["min"] = 0
        # §8: unknown param keys drop silently (unlike unknown manifest and
        # step keys, which are errors) — the entry is normalized to the §4.2
        # definition fields, so a stray key never reaches a version file or
        # a §5.1 archive.
        norm_params.append({k: p[k] for k in PARAM_FIELDS if k in p})

    # §8: optional best-effort draft-test values — keys must name manifest
    # params (a misremembered name is a repair-round error, never a silent
    # test with defaults); values ride the payload untouched, the editor
    # coerces per §4.2 kind like the chat call's test_values.
    test_values = manifest.get("test_values")
    if test_values is not None:
        if not isinstance(test_values, dict):
            errors.append("test_values must be a mapping of param name → value")
            test_values = None
        else:
            names = {p.get("name") for p in params
                     if isinstance(p, dict) and isinstance(p.get("name"), str)}
            bad = sorted(str(k) for k in test_values if k not in names)
            if bad:
                errors.append(f"test_values names unknown params {bad} — "
                              "use names from this manifest's params")

    # §6.2/§8: declared packages — {pip, import, why}, bare distribution name,
    # beyond stdlib/curated only. Their import names extend the step allowlist below.
    raw_pkgs = manifest.get("packages") or []
    norm_pkgs: list[dict] = []
    if not isinstance(raw_pkgs, list):
        errors.append("packages must be a list of { pip, import, why } entries")
        raw_pkgs = []
    for e in raw_pkgs:
        if not isinstance(e, dict) or not e.get("pip") or not e.get("import"):
            errors.append(f"packages entry malformed: {e!r} — need {{ pip: name, import: module, why: purpose }}")
            continue
        name, imp = str(e["pip"]).strip(), str(e["import"]).strip()
        why = str(e.get("why") or "").strip()
        if not pkglib.PIP_NAME_RE.match(name):
            errors.append(f"packages: {name!r} must be a bare distribution name — no version specifier")
        if not imp.isidentifier():
            errors.append(f"packages: import {imp!r} isn't a valid module name")
        elif imp in ALLOWED_IMPORTS:
            errors.append(f"packages: {imp} is already available — don't declare it")
        if not why:
            errors.append(f"packages: {name} needs a why — one line on what the steps use it for")
        norm_pkgs.append({"pip": name, "import": imp, "why": why})
    pkg_imports = [p["import"] for p in norm_pkgs]

    steps = manifest.get("steps") or []
    # Set when a step list or file name is the wrong type: already reported
    # once, so the file-matching checks below (which read file names) skip.
    bad_file = False
    if not isinstance(steps, list):
        errors.append("steps must be a list of step entries")
        steps = []
        bad_file = True
    elif not steps:
        errors.append("steps must be nonempty")
    # A non-dict entry (a bare `- 01-fetch.py` string is a plausible agent
    # shorthand) would otherwise be dropped by every filter below and produce
    # a validated draft with zero steps — reject it so the repair round fires.
    # A present `file` that isn't a string (`file: 1`, `file: [a]`, a blank
    # `file:`) is reported here once, and the step drops out of every check
    # below that reads it as a file name.
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            errors.append(f"steps entry must be a mapping with file/name/description — got {s!r}")
            continue
        # §8 key rule: an unknown step key is an error — a misspelled
        # `timeout_seconds` must not save as the default timeout.
        for key in s:
            if key not in STEP_KEYS:
                errors.append(f"step {i}: unknown key {key}")
        if "file" in s and not isinstance(s["file"], str):
            errors.append(f"step {i}: `file` must be a string naming its NN-name.py file")
            bad_file = True
        # §4.1 authorship and lock: typed like every other step field.
        if "authored_by" in s and s["authored_by"] not in ("agent", "user"):
            errors.append(f"step {i}: `authored_by` must be agent or user")
        if "locked" in s and not isinstance(s["locked"], bool):
            errors.append(f"step {i}: `locked` must be true or false")
    # §4.1 / §8 rule 3: step names are non-blank and unique (compared after
    # trimming) — every by-name match (the lock splice below, the §11 chips,
    # the §19 diff) rests on it, so this runs before the lock checks.
    name_counts: dict[str, int] = {}
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            continue
        name = s.get("name")
        if name is not None and not isinstance(name, str):
            continue  # reported below as "`name` must be a string"
        if not (name or "").strip():
            errors.append(f"step {i} needs a name")
            continue
        name_counts[name.strip()] = name_counts.get(name.strip(), 0) + 1
    for name, n in name_counts.items():
        if n > 1:
            errors.append(f"step names must be unique: `{name}` appears "
                          + ("twice" if n == 2 else f"{n} times"))
    if syncing and not bad_file:
        steps = _splice_locked_steps(steps, files, locked_steps or [], errors)
    listed = [s.get("file", "") for s in steps
              if isinstance(s, dict) and isinstance(s.get("file", ""), str)]
    # §8: the sync call may return an optional notes.md beside the manifest — the
    # agent's updated working-knowledge doc, excluded from step-file matching.
    blocks = [f for f in files if f not in ("manifest.yaml", "notes.md")]
    if not bad_file and sorted(listed) != sorted(blocks):
        errors.append(f"steps[].file and file blocks don't match 1:1 (manifest: {listed}, blocks: {blocks})")
    for i, fname in enumerate([] if bad_file else listed, 1):
        m = STEP_FILE_RE.match(fname or "")
        if not m:
            errors.append(f"step file {fname!r} doesn't follow NN-name.py")
        elif int(m.group(1)) != i:
            errors.append(f"step file {fname!r} out of order — expected {i:02d}-…")
    # §8 rule 7/6: per-step agents/secrets carry granted entry IDS — the grants
    # yaml is what the agent chose from, so anything else is a typo. Errors
    # list the granted entries as `Name (id)` because the agent thinks in
    # names but must copy ids.
    agent_names = {g.get("id"): g.get("name") for g in (grants or {}).get("agents", [])}
    secret_names = {g.get("id"): g.get("name") for g in (grants or {}).get("secrets", [])}

    def _granted(names: dict) -> str:
        return _granted_list(names)

    def _label(names: dict, entry_id: str) -> str:
        return names.get(entry_id) or entry_id

    def _imported_no_match(kind: str, entry_id: str, pick: str) -> str | None:
        return _imported_no_match_copy(unresolved, kind, entry_id, pick)

    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict) or not isinstance(s.get("file", ""), str):
            continue
        # §8 rule 3: a step's text fields are checked, not assumed — YAML 1.1
        # turns `name: on` into a boolean and a mis-indented block into a
        # mapping. Positional, because a bad `name` can't label its own error.
        bad_fields = [f for f in ("name", "description", "why")
                      if f in s and not isinstance(s[f], str)]
        for field in bad_fields:
            errors.append(f"step {i}: `{field}` must be a string")
        if bad_fields:
            # Everything below reads these as text — the repair round fixes
            # the shape first.
            continue
        if s.get("agent") and not (s.get("why") or "").strip():
            errors.append(f"step {s.get('name')}: agent: true requires a why")
        ags = s.get("agents")
        declared_agents: set[str] = set()
        if ags is not None:
            if not s.get("agent"):
                errors.append(f"step {s.get('name')}: agents is only valid on agent: true steps")
            elif (not isinstance(ags, list)
                  or not all(isinstance(x, dict) and isinstance(x.get("id"), str)
                             and "name" not in x for x in ags)):
                errors.append(f"step {s.get('name')}: agents must be a list of "
                              "{ id, why? } granted-agent entries (ids from the grants yaml, "
                              "no name key)")
            else:
                seen_ids: set[str] = set()
                for x in ags:
                    if x["id"] not in agent_names:
                        errors.append(f"step {s.get('name')}: "
                                      + (_imported_no_match("agent", x["id"], "agents")
                                         or f"agent id {x['id']!r} isn't among the granted "
                                            f"agents — granted: {_granted(agent_names)}"))
                    if x["id"] in seen_ids:
                        errors.append(f"step {s.get('name')}: agent "
                                      f"{_label(agent_names, x['id'])!r} is listed twice")
                    seen_ids.add(x["id"])
                    # §8 rule 7: with several agents, one shared step `why`
                    # can't tell their jobs apart — each entry names its role.
                    if len(ags) > 1 and not str(x.get("why") or "").strip():
                        errors.append(f"step {s.get('name')}: agent "
                                      f"{_label(agent_names, x['id'])!r} needs a why — "
                                      "one line on its role (required when a step lists several agents)")
                declared_agents = seen_ids
        # §8 rule 8: short explicit timeout, or the explicit no-limit marker —
        # never both, never a sentinel value.
        t = s.get("timeout")
        if t is not None and (isinstance(t, bool) or not isinstance(t, int) or t <= 0):
            errors.append(f"step {s.get('name')}: timeout must be a positive integer of seconds")
        nt = s.get("no_timeout")
        if nt is not None and not isinstance(nt, bool):
            errors.append(f"step {s.get('name')}: no_timeout must be true")
        if t is not None and nt:
            errors.append(f"step {s.get('name')}: timeout and no_timeout can't be combined")
        # §8 rule 8: the retry pair mirrors the timeout pair — a capped positive
        # count, or the explicit never-stop marker, never both.
        r = s.get("retries")
        if r is not None and (isinstance(r, bool) or not isinstance(r, int)
                              or r <= 0 or r > 10):
            errors.append(f"step {s.get('name')}: retries must be an integer from 1 to 10")
        ir = s.get("infinite_retries")
        if ir is not None and not isinstance(ir, bool):
            errors.append(f"step {s.get('name')}: infinite_retries must be true")
        if r is not None and ir:
            errors.append(f"step {s.get('name')}: retries and infinite_retries can't be combined")
        secs = s.get("secrets")
        if secs is not None:
            if (not isinstance(secs, list)
                    or not all(isinstance(x, dict) and isinstance(x.get("id"), str)
                               and "name" not in x for x in secs)):
                errors.append(f"step {s.get('name')}: secrets must be a list of "
                              "{ id, why } allowed-secret entries (ids from the grants yaml, "
                              "no name key)")
            else:
                seen_sec: set[str] = set()
                for x in secs:
                    if x["id"] not in secret_names:
                        errors.append(f"step {s.get('name')}: "
                                      + (_imported_no_match("secret", x["id"], "secrets")
                                         or f"secret id {x['id']!r} isn't among the allowed "
                                            f"secrets — allowed: {_granted(secret_names)}"))
                    if x["id"] in seen_sec:
                        errors.append(f"step {s.get('name')}: secret "
                                      f"{_label(secret_names, x['id'])!r} is listed twice")
                    seen_sec.add(x["id"])
                    # §8 rule 6: every declared secret carries its per-use note.
                    if not str(x.get("why") or "").strip():
                        errors.append(f"step {s.get('name')}: secret "
                                      f"{_label(secret_names, x['id'])!r} needs a why — "
                                      "one line on why the step uses it")
        errors.extend(validate_step_refs(
            s, files.get(s.get("file", ""), ""), declared_agents, pkg_imports,
            secret_names, unresolved))

    norm_steps = []
    for s in steps:
        if not isinstance(s, dict) or not isinstance(s.get("file", ""), str):
            continue
        code = files.get(s.get("file", ""), "")
        try:
            ast.parse(code)
            for mod in disallowed_imports(code, pkg_imports):
                errors.append(f"{s.get('file')}: import {mod} isn't allowed")
        except SyntaxError as e:
            errors.append(f"{s.get('file')}: syntax error — {e.msg} (line {e.lineno})")
        t = s.get("timeout")
        r = s.get("retries")
        # §8 rule 3: a scalar `secrets` / `packages` / `agents` is already an
        # error line from the type checks above — only a list is iterated
        # here, so a stray `secrets: 3` never escapes as a TypeError.
        ags, secs, pkgs = s.get("agents"), s.get("secrets"), s.get("packages")
        entry = {
            # §4.1: names are compared trimmed (uniqueness, the lock splice),
            # so they are stored trimmed.
            "file": s.get("file"), "name": str(s.get("name") or "").strip(),
            "description": s.get("description", ""),
            "agent": bool(s.get("agent")), "why": s.get("why", ""),
            "agents": [{"id": x["id"],
                        **({"why": str(x["why"]).strip()} if str(x.get("why") or "").strip() else {})}
                       for x in ags
                       if isinstance(x, dict) and isinstance(x.get("id"), str)]
                      if s.get("agent") and isinstance(ags, list) else [],
            "secrets": [{"id": x["id"], "why": str(x.get("why") or "").strip()}
                        for x in (secs if isinstance(secs, list) else [])
                        if isinstance(x, dict) and isinstance(x.get("id"), str)],
            "packages": [{"import": x["import"], "why": str(x.get("why") or "").strip()}
                         for x in (pkgs if isinstance(pkgs, list) else [])
                         if isinstance(x, dict) and isinstance(x.get("import"), str)],
            **({"timeout": t} if isinstance(t, int) and not isinstance(t, bool) and t > 0 else {}),
            **({"no_timeout": True} if s.get("no_timeout") is True else {}),
            **({"retries": r} if isinstance(r, int) and not isinstance(r, bool)
               and 0 < r <= 10 else {}),
            **({"infinite_retries": True} if s.get("infinite_retries") is True else {}),
            **({"authored_by": "user"} if _output_authored_by(s, code, syncing, current_steps)
               == "user" else {}),
            **({"locked": True} if s.get("locked") is True else {}),
            "code": code,
        }
        if syncing and s.get("locked") is not True and entry.get("authored_by") == "user":
            _carry_kept_user_settings(entry, code, current_steps)
        norm_steps.append(entry)
    trigs = manifest.get("triggers") or []
    norm_trigs: list[dict] = []
    if not isinstance(trigs, list):
        errors.append("triggers must be a list of trigger entries (see the Triggers section)")
    else:
        for t in trigs:
            # §8 rule 9 dialect: cron / interval / imessage / discord /
            # app_start — one-shot `time` triggers are never drafted; drafted
            # schedules land `source: spec` (§4.3 provenance — the merge's
            # replaceable subset).
            entry, err = parse_dialect_entry(t, cron_source="spec")
            if err:
                errors.append(err)
            elif (entry["kind"] == "discord" and entry["secret"] not in secret_names
                  and entry["secret"] not in (trigger_secret_ids or set())):
                # §8 rule 9: the token secret must be a granted secret's id
                # (copied from the grants yaml — same rule as a step's
                # `secrets:` entry, rule 6) or an existing trigger's token
                # (the CURRENT triggers context the agent re-emits through
                # the §4.3 merge).
                errors.append(f"triggers: discord secret id {entry['secret']!r} isn't among "
                              f"the granted secrets — granted: {_granted(secret_names)}")
            elif entry["kind"] == "app_start" and any(
                    x["kind"] == "app_start" for x in norm_trigs):
                errors.append("triggers: only one app_start entry")
            else:
                norm_trigs.append(entry)
    if errors:
        return {}, errors
    # No triggers key -> no triggers (manual / menu bar only).
    draft = {
        "triggers": norm_trigs,
        # §8: name/description are never manifest keys - identity changes only
        # through the chat call's actions, so a manifest that smuggles them in
        # is ignored rather than forwarded.
        "note": manifest.get("note", ""),
        "params": norm_params,
        "packages": norm_pkgs,
        "steps": norm_steps,
        "secretReferences": sorted({m for st in norm_steps for m in SECRET_REF_RE.findall(st["code"])}),
        **({"testValues": test_values} if test_values else {}),
    }
    if (files.get("notes.md") or "").strip():
        draft["notes"] = files["notes.md"].strip()
    return draft, []


LOCKED_PREFLIGHT_FIX = "Unlock the step so the next sync can rewrite it, or restore what it needs"
LOCKED_DUPLICATE_NAME_FIX = "Rename or unlock one of them"


def locked_step_preflight(current: dict | None, grants: dict | None,
                          unresolved: dict | None = None) -> list[dict]:
    """§8 locked-step preflight: the grant and import checks (rules 4, 6, 7)
    over the draft's §4.1 locked steps as stored, before a sync's agent call —
    the agent may not touch a locked step, so a failure here could never be
    repaired. Returns one `kind: constraint` blocker per failing step (empty
    when every locked step passes). Each step runs through `validate_steps`
    alone, under a position-1 filename (so the ordering rule stays quiet);
    only its own `step <name>:` / `<file>:` lines count, file names reported
    as stored. A locked name shared by two current steps returns the
    duplicate-name blockers alone, before any validator runs."""
    # §8: a locked step whose name another current step shares (a
    # pre-uniqueness version) blocks first — the by-name splice would keep
    # only one of them. One blocker per such name, no agent call.
    name_counts: dict[str, int] = {}
    for s in (current or {}).get("steps") or []:
        if isinstance(s, dict):
            key = str(s.get("name") or "").strip()
            name_counts[key] = name_counts.get(key, 0) + 1
    duplicate_blockers: list[dict] = []
    for s in locked_steps(current):
        name = str(s.get("name") or "").strip()
        if name_counts.get(name, 0) > 1 and not any(
                b["step"] == name for b in duplicate_blockers):
            duplicate_blockers.append({
                "reason": f"two steps are named `{name}` — the lock splice can't tell them apart",
                "fix": LOCKED_DUPLICATE_NAME_FIX,
                "details": "", "kind": "constraint", "step": name})
    if duplicate_blockers:
        return duplicate_blockers
    packages = [dict(p) for p in (current or {}).get("packages") or []
                if isinstance(p, dict)]
    blockers: list[dict] = []
    for s in locked_steps(current):
        name = str(s.get("name") or "")
        stored_file = str(s.get("file") or "")
        probe_file = f"01-{_stored_suffix(stored_file)}"
        manifest = {"steps": [locked_manifest_entry(s, probe_file)], "packages": packages}
        files = {"manifest.yaml": dump_yaml(manifest),
                 probe_file: str(s.get("code") or "")}
        _, errors = validate_steps(files, grants, unresolved=unresolved)
        # §8: every validator line about the locked step counts — its named
        # and file-labelled lines, its positional `step 1` lines (a blank
        # name), and the NN-name.py file-name rule (a bad or missing file).
        # Only lines about the draft's shared `packages` list stay out.
        lines = []
        for e in errors:
            if e.startswith(f"step {name}: "):
                lines.append(e[len(f"step {name}: "):])
            elif e.startswith(f"{probe_file}: "):
                lines.append(f"{stored_file}: " + e[len(f"{probe_file}: "):])
            elif e.startswith("step 1: "):
                lines.append(e[len("step 1: "):])
            elif e == "step 1 needs a name":
                lines.append("the step needs a name")
            elif e.startswith(f"step file {probe_file!r} "):
                lines.append(f"step file {stored_file!r} " + e[len(f"step file {probe_file!r} "):]
                             if stored_file else "the step's file is missing")
        if lines:
            blockers.append({"reason": "; ".join(lines), "fix": LOCKED_PREFLIGHT_FIX,
                             "details": "", "kind": "constraint", "step": name.strip()})
    return blockers


def _stored_suffix(stored_file: str) -> str:
    """§8 rule 3: the part of a stored step file the lock splice keeps behind
    the agent's `NN-` position prefix — what follows a leading digits-and-dash
    prefix only; a name without that prefix is kept whole."""
    return re.sub(r"^\d+-", "", stored_file, count=1)


def _same_code(a: str, b: str) -> bool:
    """§8 byte-identity of a step script, modulo the envelope's own newline
    framing (`parse_envelope` trims a block's surrounding blank lines and ends
    it with one newline)."""
    return a.strip("\n") == b.strip("\n")


def _splice_locked_steps(steps: list, files: dict[str, str],
                         locked: list[dict], errors: list[str]) -> list:
    """§8 rule 3 lock checks + splice, on a sync's manifest steps: every
    locked step's name exactly once with `locked: true`, `locked: true` on no
    other name, and a returned file block for a locked step (under the
    listed or the stored filename) only when byte-identical to the stored
    script. Each locked entry is then replaced
    whole by the stored step (its manifest fields, `authored_by`, `locked`),
    keeping the agent's `NN-` position prefix with the stored file's suffix;
    `files` gets the stored code under the spliced name, so every later check
    reads the spliced script. Mutates `files` and `errors`; returns the
    spliced step list."""
    # §4.1: names compare trimmed, like the uniqueness rule.
    by_name = {str(s.get("name") or "").strip(): s for s in locked}
    counts: dict = {}
    for s in steps:
        if isinstance(s, dict) and isinstance(s.get("name"), str):
            counts[s["name"].strip()] = counts.get(s["name"].strip(), 0) + 1
    for name in by_name:
        n = counts.get(name, 0)
        if n == 0:
            errors.append(f"locked step `{name}` is missing — keep it, or return a "
                          "constraint blocker explaining why the spec can't be met with it")
        elif n > 1:
            errors.append(f"locked step `{name}` is listed twice — list it exactly once")
    # §8: the agent's own returned blocks, snapshotted before any splice
    # writes into `files` — a later locked step whose stored filename equals
    # an earlier step's spliced name must not mistake that splice for a
    # returned block. A block is checked by one locked step only.
    returned = dict(files)
    spliced: set[str] = set()
    out = []
    for s in steps:
        name = s.get("name") if isinstance(s, dict) else None
        if isinstance(name, str):
            name = name.strip()
        if not isinstance(s, dict):
            out.append(s)
            continue
        if not isinstance(name, str) or name not in by_name:
            if s.get("locked") is True:
                errors.append(f"step `{name}`: only the user can lock a step")
            out.append(s)
            continue
        if s.get("locked") is not True:
            errors.append(f"step `{name}` is locked — list it with `locked: true`")
        stored = by_name[name]
        agent_file = s.get("file") or ""
        stored_file = str(stored.get("file") or "")
        suffix = _stored_suffix(stored_file)
        prefix = re.match(r"(\d+)-", agent_file)
        spliced_file = f"{prefix.group(1)}-{suffix}" if prefix else agent_file
        code = str(stored.get("code") or "")
        # The agent may return an identical block under the filename it
        # listed or under the stored filename (§8 rule 3) — unless another
        # step lists the stored filename as its own.
        others = {o.get("file") for o in steps if isinstance(o, dict) and o is not s}
        candidates = (agent_file, spliced_file) + (
            (stored_file,) if stored_file and stored_file not in others else ())
        for fname in dict.fromkeys(candidates):
            if fname in returned:
                if not _same_code(returned.pop(fname), code):
                    errors.append(f"step `{name}` is locked — do not return its file")
                if fname not in spliced:
                    files.pop(fname, None)
        if counts.get(name) == 1:
            files[spliced_file] = code
            spliced.add(spliced_file)
        out.append(locked_manifest_entry(stored, spliced_file))
    return out


def _output_authored_by(s: dict, code: str, syncing: bool,
                        current_steps: list[dict] | None) -> str:
    """§4.1 authorship of a validated step. Outside a sync the manifest's
    `authored_by` rides through. On a sync a locked (spliced) step keeps its
    stored value; any other step is `agent`, unless it keeps a previous
    user-written step's name AND byte-identical code."""
    if not syncing or s.get("locked") is True:
        return "user" if s.get("authored_by") == "user" else "agent"
    return "user" if _kept_user_step(s.get("name"), code, current_steps) else "agent"


def _kept_user_step(name, code: str, current_steps: list[dict] | None) -> dict | None:
    """§4.1 keep-`user` rule: the current user-written step a sync output step
    keeps — same name (compared trimmed) and byte-identical code — or None."""
    key = str(name or "").strip()
    for prev in current_steps or []:
        if (isinstance(prev, dict) and str(prev.get("name") or "").strip() == key
                and step_authored_by(prev) == "user"
                and _same_code(str(prev.get("code") or ""), code)):
            return prev
    return None


def _carry_kept_user_settings(entry: dict, code: str,
                              current_steps: list[dict] | None) -> None:
    """§8 rule 3 keep-`user`: a kept unlocked user-written step keeps the
    stored step's `timeout` / `no_timeout` and `retries` / `infinite_retries`
    pairs and its `description` over the agent's entry — re-emitting the
    script minus a manifest key must not silently drop a setting the user
    made in the §11 editor. `secrets`, `packages` and `agents` stay the
    agent's. Reads the stored flags in both spellings."""
    prev = _kept_user_step(entry.get("name"), code, current_steps)
    if prev is None:
        return
    for key in ("timeout", "no_timeout", "retries", "infinite_retries", "description"):
        entry.pop(key, None)
    t, r = prev.get("timeout"), prev.get("retries")
    if isinstance(t, int) and not isinstance(t, bool) and t > 0:
        entry["timeout"] = t
    if prev.get("no_timeout") is True or prev.get("noTimeout") is True:
        entry["no_timeout"] = True
    if isinstance(r, int) and not isinstance(r, bool) and r > 0:
        entry["retries"] = r
    if prev.get("infinite_retries") is True or prev.get("infiniteRetries") is True:
        entry["infinite_retries"] = True
    entry["description"] = str(prev.get("description") or "")


# ---------- background jobs ----------

class DraftRefused(Exception):
    """§19: `DraftJobs.start`'s `admit` check refused the new job inside the
    registry lock — before it superseded anything. `reason` is the admit
    callable's string ("deleting" | "discarded"), or "stopping" once
    `kill_all_building` has run (the backend is shutting down)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Cancelled(Exception):
    """§8: the job was cancelled. Raised out of `_invoke` (checked before every
    harness spawn and after every return) so cancellation unwinds the whole
    pipeline in one place — `_run` catches it once. No boolean plumbing."""


# §19 background continuation: terminal statuses whose outcome is held for the
# §11 re-attach until consumed (a cancelled job holds nothing to apply).
_HELD_STATUSES = ("done", "blocked", "failed")


class DraftJobs:
    """§19 POST /drafts — the §8 chat/sync calls as background jobs, with
    automatic repair rounds per call (§8). A job's lifetime is its owner
    draft's, never its poller's (§19 background continuation): there is no
    unpolled reap, and a settled job's outcome is held until the §11 editor
    consumes it (ack), the owner's draft settles, or a new job supersedes it."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        # §3 shutdown: set by `kill_all_building`; `start` then refuses.
        self._stopping = False

    @staticmethod
    def _ref(j: dict) -> dict:
        """The §19 `job` ref shape (`GET /draft/{owner}` envelope, `draftjob.changed`)."""
        return {"jobId": j["id"], "status": j["status"], "mode": j.get("mode")}

    @staticmethod
    def _publish(j: dict, status: str | None = None) -> None:
        """§19 `draftjob.changed` — owner-keyed so clients patch their
        `draftJobs` snapshot (`cancelled`/`consumed` remove, the rest upsert)."""
        hub.publish("draftjob.changed", owner=j.get("_owner") or "pending",
                    jobId=j["id"], status=status or j["status"], mode=j.get("mode"))

    def start(self, mode: str, agent: dict, user_text: str | None,
              current: dict | None, grants: dict,
              chat_history: list | None = None, executions: str | None = None,
              pkg_state: list[dict] | None = None,
              owner_id: str | None = None,
              admit: Callable[[], str | None] | None = None) -> str:
        """`admit`, when given, is called inside the registry lock BEFORE the
        owner's building job is cancelled or its held outcome consumed (§19
        "decided inside the registry's own lock, before the new job
        supersedes anything"): a reason string raises `DraftRefused` and
        leaves the registry untouched."""
        job_id = str(uuid.uuid4())
        # §8 unified stage set: every job enters at the phase where its real
        # work starts — sync at the workflow phase, chat at the neutral
        # deciding phase (flipping to the documents phase on the first
        # streamed rewrite marker).
        stage = ("Syncing the workflow" if mode == "sync"
                 else "Working on the request")
        # §19: the owner stamp (automation id, None = the pending slot) lets
        # the draft-settle endpoints cancel the container's building jobs and
        # lets the §11 re-attach find the owner's job again.
        job = {"id": job_id, "status": "building", "stage": stage, "detail": None,
               "events": [],
               # §8 stage timing: one stamp per stage entered plus the settle
               # stamp — the §11 thread derives every per-step duration from
               # these, the backend computes none.
               "stageTimes": [{"stage": stage, "time": time.time()}],
               "endedTime": None,
               "error": None, "draft": None, "mode": mode,
               "_cancel": False, "_proc": {}, "_owner": owner_id}
        if mode == "chat":
            # §19 `sentTriggers`: echo the resolved trigger list the §8
            # CURRENT-triggers section renders from, so a §11 re-attach apply
            # can prove the base list `triggers` ops index is still this one.
            job["sentTriggers"] = list((current or {}).get("triggers") or [])
        # §19: one BUILDING job per owner is a backend invariant, not a
        # client courtesy — a new job for an owner whose previous one is
        # still building cancels it first (its harness is killed, its record
        # settles cancelled), so two agent runs can never write the same
        # draft. The scan, the cancel marks, and the insert below are ONE
        # lock hold: two concurrent starts for one owner could otherwise
        # each scan before the other inserts and both survive building.
        superseded: list[dict] = []
        with self._lock:
            if self._stopping:
                # §3: the backend is shutting down — refused like an admit
                # refusal, before anything is superseded.
                raise DraftRefused("stopping")
            if admit is not None and (reason := admit()):
                raise DraftRefused(reason)
            cancelled = [v for v in self.jobs.values()
                         if v["status"] == "building" and v.get("_owner") == owner_id]
            for v in cancelled:
                self._mark_cancelled_locked(v)
            # §19: one held outcome per owner — a new job for the same owner
            # supersedes (consumes) the previous terminal record, and the
            # job just cancelled above is consumed the same way.
            for k, v in list(self.jobs.items()):
                if v["status"] != "building" and v.get("_owner") == owner_id:
                    superseded.append(self.jobs.pop(k))
            # Finished records nothing will ever consume (`cancelled`) are
            # bounded at the newest 20 so the process doesn't grow for its
            # whole lifetime. §19: a HELD outcome is never dropped by that
            # bound — only its ack, its owner's settle, or a same-owner start
            # consumes it (one held outcome per owner bounds those already).
            finished = [k for k, v in self.jobs.items()
                        if v["status"] != "building" and v["status"] not in _HELD_STATUSES]
            for k in finished[:-20]:
                superseded.append(self.jobs.pop(k))
            self.jobs[job_id] = job
        # Outside the lock: the harness kill (and its term-then-kill thread)
        # never runs while other callers wait on the registry.
        for v in cancelled:
            self._publish(v)
            self._kill_harness(v)
        for v in superseded:
            if v["status"] in _HELD_STATUSES:
                self._publish(v, "consumed")
        self._publish(job)
        t = threading.Thread(target=self._run,
                             args=(job, mode, agent, user_text, current, grants,
                                   chat_history, executions, pkg_state),
                             daemon=True)
        t.start()
        return job_id

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            j = self.jobs.get(job_id)
            if not j:
                return None
            # Copy under the lock — _settle inserts new keys (blockers,
            # errorDetail, …) and iterating the live dict outside it can raise
            # "dictionary changed size during iteration" mid-poll.
            out = {k: v for k, v in j.items() if not k.startswith("_")}
            # The job thread appends to `events` and `stageTimes` while this
            # serializes — copy both.
            out["events"] = list(j["events"])
            out["stageTimes"] = list(j["stageTimes"])
        return out

    @staticmethod
    def _mark_cancelled_locked(j: dict) -> None:
        """Settle a building job cancelled — caller holds self._lock."""
        j["_cancel"] = True
        j["status"] = "cancelled"
        j["endedTime"] = time.time()  # §8 stage timing: bounds the last stage

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            j = self.jobs.get(job_id)
            # A cancel racing completion must not clobber a terminal
            # done/blocked/failed job — the Review page would lose the result.
            if not j or j["status"] != "building":
                return False
            self._mark_cancelled_locked(j)
        self._publish(j)
        self._kill_harness(j)
        return True

    @staticmethod
    def _kill_harness(j: dict) -> None:
        """§8 "cancelling the job kills the harness process" — never under
        self._lock."""
        proc = j["_proc"].get("proc")
        if proc:
            # The whole session group (§8 "cancelling the job kills the harness
            # process") — CLIs spawn helpers that terminate alone won't reach.
            # §8: whether or not the CLI leader itself has already exited — a
            # helper it left behind still holds the group and the pipes.
            harness.kill_group(proc, signal.SIGTERM)
            # §8: and release its pipes and stdin write the way the watchdog
            # kill does, so an escapee holding stdout can't keep the call
            # alive until its idle window or hard cap.
            release = j["_proc"].get("release")
            if release is not None:
                release()

            def _hard_kill(p=proc):
                # §8: a CLI that traps SIGTERM must still die - same
                # term-then-kill escalation as the engine's step groups
                # (sig=None is the §2 kill-hard form; SIGKILL by name does
                # not exist on Windows). The escalation is unconditional:
                # when the leader already exited, wait() returns at once,
                # and a group survivor that trapped the TERM would otherwise
                # never see the KILL (an empty group is a harmless no-op).
                try:
                    p.wait(timeout=5.0)
                except Exception:  # noqa: BLE001 - timeout or a raced reap
                    pass
                harness.kill_group(p)

            threading.Thread(target=_hard_kill, daemon=True,
                             name="ad-draft-kill").start()

    def cancel_for(self, owner_id: str | None) -> None:
        """§19 draft settle: cancel every still-building job stamped with this
        owner (None = the pending slot) and drop its held terminal records, so
        a settled draft never leaves an agent harness process running or an
        unconsumed outcome behind."""
        with self._lock:
            ids = [k for k, v in self.jobs.items()
                   if v["status"] == "building" and v.get("_owner") == owner_id]
            dropped = [self.jobs.pop(k) for k, v in list(self.jobs.items())
                       if v["status"] != "building" and v.get("_owner") == owner_id]
        for k in ids:
            self.cancel(k)
        for v in dropped:
            if v["status"] in _HELD_STATUSES:
                self._publish(v, "consumed")

    def discard(self, job_id: str) -> None:
        """§19 POST /drafts refused at registration ("the draft was
        discarded"): cancel the job if it is still building, else drop its
        record — an outcome that settled before the check (a missing harness
        binary fails at once) must not be held for the fresh draft."""
        if self.cancel(job_id):
            return
        with self._lock:
            j = self.jobs.get(job_id)
            if j is None or j["status"] == "building":
                return
            del self.jobs[job_id]
        if j["status"] in _HELD_STATUSES:
            self._publish(j, "consumed")

    def ack(self, job_id: str) -> str:
        """§19 POST /drafts/{jobId}/ack — the §11 editor consumed a settled
        job's outcome (applied + persisted). Drops the record. Returns
        "ok" | "missing" | "building" for the route's 200/404/409."""
        with self._lock:
            j = self.jobs.get(job_id)
            if not j:
                return "missing"
            if j["status"] == "building":
                return "building"
            del self.jobs[job_id]
        if j["status"] in _HELD_STATUSES:
            self._publish(j, "consumed")
        return "ok"

    def job_for(self, owner_id: str | None) -> dict | None:
        """§19 `GET /draft/{owner}` `job` ref: the owner's building job, else
        its newest held outcome, else None (cancelled jobs hold nothing)."""
        with self._lock:
            building = held = None
            for v in self.jobs.values():  # insertion-ordered → last wins
                if v.get("_owner") != owner_id:
                    continue
                if v["status"] == "building":
                    building = v
                elif v["status"] in _HELD_STATUSES:
                    held = v
            j = building or held
            return self._ref(j) if j else None

    def all_jobs(self) -> list[dict]:
        """§19 GET /state `draftJobs`: every building or held owner-stamped
        job (backs the §9.1 drafting notes)."""
        with self._lock:
            return [{"owner": v["_owner"] or "pending", **self._ref(v)}
                    for v in self.jobs.values()
                    if v["status"] == "building" or v["status"] in _HELD_STATUSES]

    def kill_all_building(self) -> None:
        """§3 shutdown: drafting harnesses die with the backend. Mark every
        building job cancelled and SIGKILL its harness session group outright —
        the process is exiting, so cancel()'s term-then-kill grace thread would
        never get to fire."""
        with self._lock:
            # §3: from here on every `start` is refused — a job admitted
            # after this sweep would run a harness nothing kills.
            self._stopping = True
            live = [j for j in self.jobs.values() if j["status"] == "building"]
            for j in live:
                j["_cancel"] = True
                j["status"] = "cancelled"
        for j in live:
            proc = j["_proc"].get("proc")
            # §8: whether or not the CLI leader has already exited — the
            # group may still hold a helper and the pipes. A finished call
            # never leaves its proc here (`_invoke` clears it), so a held
            # proc is always a live call's.
            if proc:
                harness.kill_group(proc)
                # §8/§3: release the pipes too, so a drafting thread is never
                # left blocked on an escapee's pipe past the shutdown box.
                release = j["_proc"].get("release")
                if release is not None:
                    release()

    def _settle(self, job: dict, status: str, **fields) -> bool:
        """The only terminal transition — building → done/blocked/failed under
        the lock, so a cancel that already won can never be overwritten (and
        vice versa)."""
        with self._lock:
            if job["status"] != "building":
                return False
            job["status"] = status
            job["endedTime"] = time.time()  # §8 stage timing: bounds the last stage
            job.update(fields)
        # §19 draftjob.changed: a held outcome upserts the clients' snapshot
        # (the §9.1 "finished" note) — published outside the lock.
        self._publish(job)
        return True

    def _run(self, job: dict, mode: str, agent: dict, user_text: str | None,
             current: dict | None, grants: dict, chat_history: list | None,
             executions: str | None = None, pkg_state: list[dict] | None = None) -> None:
        try:
            self._pipeline(job, mode, agent, user_text, current, grants,
                           chat_history, executions, pkg_state)
        except Cancelled:
            # cancel() already made the job terminal under the lock — this
            # settle is the no-op belt-and-braces (it never overwrites).
            self._settle(job, "cancelled")
        except harness.HarnessError as e:
            self._settle(job, "failed", error=str(e))
        except Exception as e:  # noqa: BLE001
            # Anything else must still end the job — a thread dying here would
            # leave it "building" forever and the UI spinning.
            log.exception("drafting job %s crashed", job["id"])
            self._settle(job, "failed", error=f"drafting failed unexpectedly: {e}")

    def _pipeline(self, job: dict, mode: str, agent: dict, user_text: str | None,
                  current: dict | None, grants: dict,
                  chat_history: list | None = None, executions: str | None = None,
                  pkg_state: list[dict] | None = None) -> None:
        """Makes the mode's calls; sets job status. A cancel raises `Cancelled`
        out of `_invoke` — caught once in `_run`."""
        if mode == "chat":
            return self._chat_call(job, agent, user_text, current, grants, chat_history,
                                   executions, pkg_state)

        # ---- sync: steps, params, schedule — the provided spec IS the input ----
        spec_md = spec_as_md(current)
        self._stage(job, "Syncing the workflow")
        # §8: a re-emitted existing discord trigger stays valid even when its
        # token secret was never step-granted (validate_steps docstring).
        trig_secrets = {t["secret"] for t in (current or {}).get("triggers") or []
                        if t.get("kind") == "discord" and t.get("secret")}
        # §8 rule 3: the draft's §4.1 locked steps are the lock checks'
        # reference set (spliced back in whole); every current step feeds the
        # keep-`user` authorship rule; a constraint blocker's `step` must name
        # a locked one.
        current_steps = [s for s in (current or {}).get("steps") or [] if isinstance(s, dict)]
        locked = locked_steps(current)
        # §8 locked-step preflight: a locked step that fails the grant or
        # import checks as stored blocks at once — no agent call.
        preflight = locked_step_preflight(
            current, grants, (current or {}).get("unresolved_references"))
        if preflight:
            return self._block(job, "steps", preflight, None)
        draft, _errors, blockers, diagnosed, bnotes = self._call_with_repair(
            job, agent, build_steps_prompt(spec_md, current, grants),
            lambda files: validate_steps(
                files, grants, trig_secrets,
                unresolved=(current or {}).get("unresolved_references"),
                locked_steps=locked, current_steps=current_steps), "steps",
            locked_names={str(s.get("name") or "").strip() for s in locked},
            build_rules=build_rules_bullets(spec_md))
        if blockers:
            # The blocker response's optional notes.md rides the payload (§8) —
            # the caller already holds the spec, a sync never changes it.
            return self._block(job, "steps", blockers,
                               {"notes": bnotes} if bnotes else None,
                               diagnosed=diagnosed)

        if draft.get("packages"):
            # §8: ensure the declared packages right after the steps land — the
            # user learns about an install failure on the edit page, not when a
            # trigger fires. A failure never fails the job (§6.2): the statuses
            # ride the draft payload and render in the §11 Packages card.
            # §8: installs are not a stage — the `Installing …` events land
            # under "Syncing the workflow", where the packages belong.
            self._check_cancel(job)  # a cancel must never start the installs
            try:
                draft["packages"] = pkglib.ensure(
                    draft["packages"],
                    on_progress=lambda spec: self._event(job, f"Installing {spec}…"),
                    should_stop=lambda: bool(job.get("_cancel")))
            except pkglib.PackagesBusy:
                # §19: the bounded pip-lock wait expired — the packages stay
                # as declared (status unknown) and the §11 Packages card's own
                # check reports them; the sync itself still settles.
                self._event(job, "Package install skipped — another install is still running")

        # §19: the job payload is an API payload — its steps leave the
        # manifest's snake_case (`no_timeout` / `infinite_retries`) here, in the
        # same §4.1 serialization `/draft/{owner}` uses, so the editor applies
        # a settled sync's steps unchanged.
        draft["steps"] = [step_json(s) for s in draft.get("steps") or []]
        self._settle(job, "done", draft=draft)

    def _chat_call(self, job: dict, agent: dict, user_text: str | None,
                   current: dict | None, grants: dict,
                   chat_history: list | None, executions: str | None = None,
                   pkg_state: list[dict] | None = None) -> None:
        """§8 chat call: one call whose response shape decides the outcome —
        plain prose is an answer, file blocks are rewrites/actions
        (spec.md / notes.md / actions.yaml — validated, up to
        §15 AUTOWRIGHT_REPAIR_ROUNDS per-block repair rounds, then diagnosis),
        a blocker envelope blocks (blockedAt: chat). Repair is per-block: an
        invalid round's valid blocks are kept as written, the repair round is
        asked only for the failed blocks, and its blocks merge over the kept
        ones (latest wins) before the merged set revalidates as a whole. A
        cancel raises `Cancelled` out of `_invoke`."""
        prompt = build_chat_prompt(user_text, current, grants, chat_history,
                                   executions=executions, pkg_state=pkg_state)
        # §8: actions.yaml test_values/param_values keys must name the draft's
        # params; triggers-op indexes must fall in the CURRENT triggers list.
        pnames = [str(p.get("name")) for p in (current or {}).get("params") or []
                  if p.get("name")]
        tcount = len((current or {}).get("triggers") or [])
        # §8: a constraint blocker's `step` must name a §4.1 locked step.
        lnames = {str(s.get("name") or "").strip() for s in locked_steps(current)}
        # §8: a constraint blocker's `rule` must quote a SPEC Build rules bullet.
        rules = build_rules_bullets(spec_as_md(current))
        on_chunk, on_file = self._chat_cb(job)
        raw = self._invoke(job, agent, prompt, on_chunk=on_chunk, on_file=on_file)
        outcome, payload, kept, answer, failed = self._chat_classify(
            raw, pnames, tcount, locked_names=lnames, build_rules=rules)
        rounds: list[dict] = []
        for i in range(1, repair_rounds() + 1):
            if outcome != "invalid":
                break
            rounds.append({"errors": payload, "response": raw})
            repair = self._chat_repair_prompt(prompt, raw, payload, kept, failed)
            self._event(job, "The response didn't validate — asking for a corrected one…")
            on_chunk, on_file = self._chat_cb(job, prefix=try_prefix(i))
            raw = self._invoke(job, agent, repair,
                               on_chunk=on_chunk, on_file=on_file)
            outcome, payload, kept, answer, failed = self._chat_classify(
                raw, pnames, tcount, kept, answer, locked_names=lnames,
                build_rules=rules)
        if outcome == "invalid":
            diag = self._diagnose(job, agent, prompt, raw, payload,
                                  attempts=len(rounds) + 1, locked_names=lnames,
                                  build_rules=rules)
            self._record_failure(job, agent, "chat", "diagnosed", prompt,
                                 rounds + [{"errors": payload, "response": raw}], diag)
            return self._block(job, "chat", diag, None, diagnosed=True)
        if rounds:
            self._record_failure(job, agent, "chat",
                                 "blocked" if outcome == "blocked" else "repaired",
                                 prompt, rounds, None)
        if outcome == "blocked":
            return self._block(job, "chat", payload["blockers"],
                               {"notes": payload["notes"]} if payload.get("notes") else None)
        if not payload:
            return self._fail(job, "The agent returned an empty answer.", [])
        self._settle(job, "done", draft=payload)

    @staticmethod
    def _chat_classify(raw: str, param_names: list[str] | None = None,
                       triggers_count: int | None = None,
                       kept: dict[str, str] | None = None, answer: str = "",
                       locked_names: set[str] | None = None,
                       build_rules: list[str] | None = None):
        """Classify a chat-call response (§8), merging its blocks over `kept` —
        the valid blocks carried forward from earlier rounds (per-block
        repair). Returns (outcome, payload-or-errors-or-blockers, kept2,
        answer2, failed_names):

        - ("blocked", {blockers, notes?}, …) — a valid blocker envelope,
          terminal; `notes` the optional notes.md riding beside it (§8).
        - ("done", payload, …) — payload the terminal { answer?, spec?,
          notes?, actions? } dict (empty for an empty response).
        - ("invalid", errors, kept2, answer2, failed) — kept2 the valid blocks
          of the merged set (kept as written for the next round), failed the
          block names the errors attribute to (empty when the envelope itself
          didn't parse — a full resend repairs that round).

        The merged set is validated as a whole, so cross-block checks run
        against what will actually be applied. Prose before a round's first
        marker replaces the carried `answer`; a prose-only response with kept
        blocks settles them with that prose as the answer. Round 1 (no kept
        blocks) behaves exactly like the unmerged classification: prose is
        always an answer, never invalid. `locked_names` is parse_blockers'
        constraint-`step` reference set, `build_rules` its constraint-`rule`
        one (the SPEC's Build rules bullets)."""
        kept = kept or {}
        try:
            blockers, bnotes = parse_blockers(raw, locked_names, build_rules)
            if blockers is not None:
                # §8: blocked payload = {blockers, notes?} — the optional
                # notes.md riding the blocker envelope.
                return "blocked", {"blockers": blockers, "notes": bnotes}, kept, answer, []
        except ValueError as e:
            return "invalid", [str(e)], kept, answer, []
        if not _file_mark_outside_fences(raw):
            # §8: every marker fenced (or none) means prose — a chat answer
            # that *shows* an actions.yaml must never arm a sync.
            text = raw.strip()
            if not kept:
                # §8 question type: a leading ===QUESTION=== is stripped and
                # rides the payload as answerKind; a marker-only response is
                # an empty answer. The carried answer keeps the marker so a
                # later round's replacement semantics stay prose-vs-prose.
                atext, akind = split_answer_kind(text)
                payload = ({"answer": atext,
                            **({"answerKind": akind} if akind else {})}
                           if atext else {})
                return "done", payload, {}, text, []
            merged, prose = dict(kept), text
        else:
            try:
                files = parse_envelope(raw)
            except ValueError as e:
                return "invalid", [str(e)], kept, answer, []
            merged = {**kept, **files}
            m = _file_mark_outside_fences(raw)
            prose = raw[:m.start()].strip()
        payload, errors, bad = validate_chat_files(merged, param_names, triggers_count)
        answer = prose or answer
        if errors:
            good = {k: v for k, v in merged.items()
                    if k in CHAT_FILES and k not in bad}
            return "invalid", errors, good, answer, sorted(bad)
        if answer:
            # §8 question type — stripped at payload build so the kind rides
            # with whichever round's prose settled as the answer.
            atext, akind = split_answer_kind(answer)
            if atext:
                payload["answer"] = atext
                if akind:
                    payload["answerKind"] = akind
        return "done", payload, merged, answer, []

    @staticmethod
    def _chat_repair_prompt(prompt: str, raw: str, errors: list[str],
                            kept: dict[str, str], failed: list[str]) -> str:
        """§8 per-block chat repair prompt: when the errors attribute to blocks,
        name the kept blocks ("do not resend them") and ask only for corrected
        versions of the failed ones; without attribution (the envelope itself
        didn't parse) fall back to the full resend."""
        out = prompt + "\n\n=== YOUR PREVIOUS RESPONSE ===\n" + clip_response(raw)
        if failed:
            kept_names = ", ".join(sorted(kept)) or "none"
            return (out
                    + "\n\n=== VALIDATION ERRORS — resend only the failed blocks ===\n- "
                    + "\n- ".join(errors)
                    + "\n\nThese blocks were valid and are kept exactly as you wrote "
                    + f"them — do not resend them: {kept_names}."
                    + "\nResend a corrected version of each failed block "
                    + f"({', '.join(failed)}); omit one to drop it entirely."
                    + "\nAny prose before your first block replaces the accompanying "
                    + "chat message.")
        return (out + "\n\n=== VALIDATION ERRORS — fix these and resend the full envelope ===\n- "
                + "\n- ".join(errors))

    @staticmethod
    def _check_cancel(job: dict) -> None:
        """Raise `Cancelled` when the job's cancel flag is set (§8)."""
        if job["_cancel"]:
            raise Cancelled()

    def _invoke(self, job: dict, agent: dict, prompt: str, on_chunk=None,
                on_file=None) -> str:
        """harness.invoke with the job's proc/cancel wiring and the §8 one-retry
        policy: a transient failure (timeout, nonzero exit that looks transient)
        is retried once after a short pause; a second failure — or a
        non-retryable one (CLI not installed, unknown harness, an obvious
        auth/model-not-found stderr) — propagates. Cancellation raises
        `Cancelled`: checked before every spawn and after every return, so no
        harness call starts after a cancel and no cancelled call's output is
        ever used (a kill-induced nonzero exit after a cancel surfaces as
        Cancelled, never as a failure). Drafting calls run web-enabled (§6
        web-read tools); runtime agent.ask calls never do. On a §8
        file-writing harness the prompt gains the OUTPUT delivery section —
        appended here, at call time, so repair and diagnosis rounds carry it
        too."""
        self._check_cancel(job)
        if harness.writes_files(agent.get("harness") or ""):
            prompt = prompt + "\n\n" + FILE_OUTPUT_BLOCK

        def call() -> str:
            try:
                return harness.invoke(agent, prompt, proc_holder=job["_proc"],
                                      on_chunk=on_chunk, on_tool=self._tool_cb(job),
                                      on_file=on_file,
                                      should_abort=lambda: job["_cancel"],
                                      web=True)
            finally:
                # §8: the harness call is over and its process reaped — a
                # cancel during the package installs or between rounds must
                # never killpg a pgid the OS may already have reused.
                job["_proc"].pop("proc", None)
                job["_proc"].pop("release", None)

        try:
            out = call()
        except harness.HarnessError as e:
            self._check_cancel(job)
            if not getattr(e, "retryable", False):
                raise
            log.warning("agent call failed (%s) — retrying once", e)
            self._event(job, "The agent call failed — retrying once…")
            time.sleep(2)
            self._check_cancel(job)
            # §8: the retry streams into the same callbacks — reset their
            # shared scanner state so attempt 2 isn't parsed as a
            # continuation of attempt 1's stream.
            reset = getattr(on_chunk, "reset", None)
            if reset:
                reset()
            out = call()
        self._check_cancel(job)
        return out

    def _call_with_repair(self, job: dict, agent: dict, prompt: str,
                          validator, call: str, locked_names: set[str] | None = None,
                          build_rules: list[str] | None = None
                          ) -> tuple[dict, list[str], list[dict] | None, bool, str | None]:
        """One harness call + up to §15 AUTOWRIGHT_REPAIR_ROUNDS automatic
        repair rounds against `validator` — each round the same prompt plus the
        newest raw response and its errors — then, when every round is still
        invalid, one §8 build-diagnosis call that turns the failure into
        blockers (returned with diagnosed=True), so a surviving validation
        failure never fails the job. A valid §8 blocker envelope is terminal —
        returned as-is, no repair, its optional notes.md text as the last
        element. A cancel raises `Cancelled` out of `_invoke`
        (checked before every spawn there): a cancel between calls never lets a
        fresh full-timeout harness call start. `call` names the pipeline call
        ("steps") for the §5 build-failure record; `locked_names` is the
        draft's §4.1 locked step names a constraint blocker may name,
        `build_rules` the SPEC's Build rules bullets its `rule` must quote."""
        on_chunk, on_file = self._progress_cb(job)
        raw = self._invoke(job, agent, prompt, on_chunk=on_chunk, on_file=on_file)
        result, errors, blockers, notes = self._parse_validate(
            raw, validator, locked_names, build_rules)
        rounds: list[dict] = []
        for i in range(1, repair_rounds() + 1):
            if not errors:
                break
            rounds.append({"errors": errors, "response": raw})
            repair = (prompt + "\n\n=== YOUR PREVIOUS RESPONSE ===\n" + clip_response(raw)
                      + "\n\n=== VALIDATION ERRORS — fix these and resend the full envelope ===\n- "
                      + "\n- ".join(errors))
            self._event(job, "The response didn't validate — asking for a corrected one…")
            on_chunk, on_file = self._progress_cb(job, prefix=try_prefix(i))
            raw = self._invoke(job, agent, repair,
                               on_chunk=on_chunk, on_file=on_file)
            result, errors, blockers, notes = self._parse_validate(
                raw, validator, locked_names, build_rules)
        if errors:
            diag = self._diagnose(job, agent, prompt, raw, errors,
                                  attempts=len(rounds) + 1, locked_names=locked_names,
                                  build_rules=build_rules)
            self._record_failure(job, agent, call, "diagnosed", prompt,
                                 rounds + [{"errors": errors, "response": raw}], diag)
            return {}, [], diag, True, None
        if rounds:
            # A repair round settled the call — a fixed envelope or a blocker
            # envelope — but the earlier rounds still failed validation:
            # exactly the near-miss the record exists for (§5).
            self._record_failure(job, agent, call,
                                 "blocked" if blockers else "repaired",
                                 prompt, rounds, None)
        return result, errors, blockers, False, notes

    @staticmethod
    def _record_failure(job: dict, agent: dict, call: str, outcome: str, prompt: str,
                        rounds: list[dict], blockers: list[dict] | None) -> None:
        """§5 build-failure record (developerMode-gated, best-effort): one file per
        drafting call whose response failed validation — material for improving
        the §8 agent instructions later."""
        reqlog.write_build_failure(
            reqlog.stamp(), job["mode"], call, agent.get("harness") or "?",
            agent.get("model") or "configured default", outcome, prompt, rounds, blockers)

    def _diagnose(self, job: dict, agent: dict, prompt: str, raw: str,
                  errors: list[str], attempts: int = 2,
                  locked_names: set[str] | None = None,
                  build_rules: list[str] | None = None) -> list[dict]:
        """§8 build diagnosis: one blocker-envelope-only call explaining why the
        build failed validation every round (`attempts` = total invalid
        responses, driving the twice/N-times wording); on any failure of the
        diagnosis itself, a deterministic fallback blocker built from the
        validation errors. `locked_names` is the calling parse's §4.1 locked
        step names — a constraint blocker naming any other step is a parse
        error here too; `build_rules` likewise for a constraint `rule`."""
        self._event(job, f"The response didn't validate{attempts_phrase(attempts)}"
                         " — analyzing what went wrong…")
        diagnose = (prompt + "\n\n=== YOUR PREVIOUS RESPONSE ===\n" + clip_response(raw)
                    + "\n\n=== VALIDATION ERRORS ===\n- " + "\n- ".join(errors)
                    + "\n\n" + DIAGNOSE_TASK.replace("{attempts}", attempts_phrase(attempts)))
        blockers = None
        try:
            # A cancel raises `Cancelled` out of `_invoke` — it is not caught
            # below, so a cancelled diagnosis never settles a fallback blocker.
            # The diagnosis call is never offered the notes.md option — its
            # optional notes are ignored.
            blockers, _notes = parse_blockers(self._invoke(job, agent, diagnose),
                                              locked_names, build_rules)
        except (harness.HarnessError, ValueError) as e:
            log.warning("build-diagnosis call failed: %s", e)
        if not blockers:
            blockers = [{
                "reason": "The draft didn't build — the agent's response failed "
                          f"validation{attempts_phrase(attempts)}.",
                "fix": "Simplify or clarify the spec, or try a different authoring agent, then rebuild.",
                "details": "\n".join(errors[:8]),
            }]
        return blockers

    def _progress_cb(self, job: dict, prefix: str = ""):
        """§8 live progress (sync call): returns (on_chunk, on_file) sharing
        one state — the streamed-text scanner derives `detail` from ===FILE:
        markers (Claude Code), the document handler from scratch `file`
        events (file-writing harnesses) — so the labels read the same either
        way. Shape changes update immediately and append a count-less
        `events` milestone (`Thinking…` never does); line-count growth
        throttles to one update per second, detail-only. The two callbacks
        come from different threads (read loop vs scratch watcher) — one
        lock keeps the shared state coherent."""
        state = {"scanner": _StreamScanner(), "shape": None, "last": 0.0,
                 "total": None, "documents": {}, "seen": set(),
                 "lock": threading.Lock()}

        def show(shape: str, label: str, detail: str) -> None:
            # §8: the waiting placeholder never returns once a real label
            # showed — stdout prose resuming after a document landed must not
            # regress the live line mid-build. It is also never try-prefixed
            # (it isn't a message; the §11 filter matches it exactly).
            if shape == "Thinking…" and state["shape"] not in (None, "Thinking…"):
                return
            if prefix and shape != "Thinking…":
                label = prefix + label[0].lower() + label[1:]
                detail = prefix + detail[0].lower() + detail[1:]
            now = time.monotonic()
            if shape != state["shape"]:
                state["shape"] = shape
                state["last"] = now
                # §8: each shape's milestone lands once per round — a shape
                # re-entered later (the two channels interleave on the
                # file-writing harnesses) updates detail only, so the feed
                # can't ping-pong duplicates and durations stay whole.
                if shape != "Thinking…" and shape not in state["seen"]:
                    state["seen"].add(shape)
                    self._append_event(job, label)
                self._detail(job, detail)
            elif now - state["last"] >= 1.0:
                state["last"] = now
                self._detail(job, detail)

        def should_show(shape: str) -> bool:
            """Cheap pre-filter mirroring show()'s display conditions, so the
            heavy label/line-count derivation runs at most once per second."""
            if shape == "Thinking…" and state["shape"] not in (None, "Thinking…"):
                return False
            return (shape != state["shape"]
                    or time.monotonic() - state["last"] >= 1.0)

        def label_detail(fname: str, lines: int) -> tuple[str, str]:
            """The §8 sync-call label for one response document, shared by
            both channels."""
            if fname == "manifest.yaml":
                label = "Writing the manifest — name, triggers, parameters, step list"
                return label, label
            count = f" · {lines} line{'s' if lines != 1 else ''}" if lines else ""
            if fname == "notes.md":
                # §8: the sync call's notes block reads like the chat call's
                return "Updating the notes", "Updating the notes" + count
            total = state["total"]
            sm = STEP_FILE_RE.match(fname)
            name = (f"step {int(sm.group(1))} of {total} — {fname}"
                    if sm and total else fname)
            return f"Writing {name}", f"Writing {name}" + count

        def cb(chunk: str) -> None:
            if job["_cancel"] or job["status"] != "building":
                return
            with state["lock"]:
                sc = state["scanner"]
                sc.feed(chunk)
                marks = sc.marks
                if sc.blocked_at > (marks[-1][2] if marks else -1):
                    # §8: the agent is writing its blocker envelope — say so
                    # (count-less) instead of mislabeling the stream
                    show("blocked", "Describing a blocker", "Describing a blocker")
                    return
                if not marks:
                    show("Thinking…", "Thinking…", "Thinking…")
                    return
                fname, _, end = marks[-1]
                if not should_show(fname):
                    return
                self._steps_total(state, sc.text, marks)
                lines = (0 if fname == "manifest.yaml"
                         else len(sc.text[end:].strip("\n").splitlines()))
                label, detail = label_detail(fname, lines)
                show(fname, label, detail)

        def file_cb(name: str, content: str) -> None:
            if job["_cancel"] or job["status"] != "building":
                return
            with state["lock"]:
                state["documents"][name] = content
                # §8: `i of n` comes from the manifest document once a later
                # document proves it complete (mirrors the streamed rule of
                # parsing only closed blocks).
                if (state["total"] is None and name != "manifest.yaml"
                        and "manifest.yaml" in state["documents"]):
                    self._manifest_total(state, state["documents"]["manifest.yaml"])
                lines = len(content.strip("\n").splitlines())
                label, detail = label_detail(name, lines)
                show(name, label, detail)

        def reset() -> None:
            # §8 one-retry: attempt 2 must not inherit attempt 1's stream — a
            # carried-over scanner would count lines across both streams,
            # `documents` would hold content from a scratch dir already
            # removed, and `seen` would suppress every fresh milestone.
            with state["lock"]:
                state.update(scanner=_StreamScanner(), shape=None, last=0.0,
                             total=None, documents={}, seen=set())

        cb.reset = reset  # type: ignore[attr-defined] — read by _invoke's retry
        return cb, file_cb

    # §8 chat-call streamed-marker labels — one per allowed block name.
    _CHAT_LABELS = {"spec.md": "Writing the spec",
                    "notes.md": "Updating the notes",
                    "actions.yaml": "Recording the changes — name, description, triggers"}

    # §8 chat-job stage flip: the first streamed rewrite marker moves the job
    # from the neutral deciding stage to the documents stage; answer-only,
    # actions-only, and blocker responses never flip.
    _REWRITE_MARKS = frozenset({"spec.md", "notes.md"})

    def _chat_cb(self, job: dict, prefix: str = ""):
        """§8 chat-call live progress: returns (on_chunk, on_file) sharing one
        state. `Thinking…` until text arrives, then a per-document label
        (`Writing the spec · N lines`, `Updating the notes · N lines`, …)
        once a ===FILE: marker has streamed (Claude Code) or a scratch
        document has landed (file-writing harnesses), else `Writing the
        answer · N lines` — shape changes update immediately and append a
        count-less `events` milestone (`Thinking…` never does), line-count
        growth throttles to one update per second, detail-only. The two
        callbacks come from different threads (read loop vs scratch
        watcher) — one lock keeps the shared state coherent."""
        state = {"scanner": _StreamScanner(), "shape": None, "last": 0.0,
                 "has_text": False, "seen": set(), "lock": threading.Lock()}

        def show(shape: str, label: str, detail: str) -> None:
            # §8: the waiting placeholder never returns once a real label
            # showed, and is never try-prefixed (see _progress_cb's show).
            if shape == "Thinking…" and state["shape"] not in (None, "Thinking…"):
                return
            if prefix and shape != "Thinking…":
                label = prefix + label[0].lower() + label[1:]
                detail = prefix + detail[0].lower() + detail[1:]
            now = time.monotonic()
            if shape != state["shape"]:
                state["shape"] = shape
                state["last"] = now
                # §8: a sub-task line once shown persists — each shape's
                # milestone lands once per round ("Writing the answer"
                # included); a re-entered shape updates detail only, so the
                # interleaved channels of a file-writing harness can't
                # ping-pong duplicate milestones. `Thinking…` stays
                # detail-only.
                if shape != "Thinking…" and shape not in state["seen"]:
                    state["seen"].add(shape)
                    self._append_event(job, label)
                self._detail(job, detail)
            elif now - state["last"] >= 1.0:
                state["last"] = now
                self._detail(job, detail)

        def should_show(shape: str) -> bool:
            """Cheap pre-filter mirroring show()'s display conditions (see
            _progress_cb's twin)."""
            if shape == "Thinking…" and state["shape"] not in (None, "Thinking…"):
                return False
            return (shape != state["shape"]
                    or time.monotonic() - state["last"] >= 1.0)

        def flip_stage(fname: str) -> None:
            """§8 stage flip — checked against the job, not the round's local
            state, so a repair round that first streams a rewrite marker
            still flips, and a flipped job never flips back. The prose
            accumulated so far (before the first marker, or the whole stdout
            prose on a file-writing harness) is the accompanying answer —
            ride it on the job as `plan` (§19) so the §11 thread can land
            "The plan" while the documents phase is still running."""
            if (fname not in self._REWRITE_MARKS
                    or job["stage"] != "Working on the request"):
                return
            text = state["scanner"].text
            first = _file_mark_outside_fences(text)
            plan, _ = split_answer_kind((text[: first.start()] if first
                                         else text).strip())
            if plan:
                # New-key insert from a streaming thread — under the lock,
                # like _settle's, so get()'s items() iteration never sees
                # the dict resize mid-copy.
                with self._lock:
                    job["plan"] = plan
            self._stage(job, "Updating the documents")

        def document_label(fname: str, lines: int) -> tuple[str, str]:
            label = self._CHAT_LABELS.get(fname, f"Writing {fname}")
            if fname == "actions.yaml":
                return label, label
            return label, f"{label} · {lines} line{'s' if lines != 1 else ''}"

        def cb(chunk: str) -> None:
            if job["_cancel"] or job["status"] != "building":
                return
            with state["lock"]:
                sc = state["scanner"]
                sc.feed(chunk)
                marks = sc.marks
                state["has_text"] = state["has_text"] or bool(chunk.strip())
                if sc.blocked_at > (marks[-1][2] if marks else -1):
                    # §8: the blocker envelope is not an answer — label it as
                    # what it is (count-less). Never flips the stage: a
                    # blocker turn lives entirely in the deciding phase.
                    show("blocked", "Describing a blocker", "Describing a blocker")
                elif marks:
                    fname, _, end = marks[-1]
                    flip_stage(fname)
                    if not should_show(fname):
                        return
                    lines = len(sc.text[end:].strip("\n").splitlines())
                    label, detail = document_label(fname, lines)
                    show(fname, label, detail)
                elif state["has_text"]:
                    if not should_show("answer"):
                        return
                    lines = len(sc.text.strip().splitlines())
                    show("answer", "Writing the answer",
                         f"Writing the answer · {lines} line{'s' if lines != 1 else ''}")
                else:
                    show("Thinking…", "Thinking…", "Thinking…")

        def file_cb(name: str, content: str) -> None:
            if job["_cancel"] or job["status"] != "building":
                return
            with state["lock"]:
                flip_stage(name)
                lines = len(content.strip("\n").splitlines())
                label, detail = document_label(name, lines)
                show(name, label, detail)

        def reset() -> None:
            # §8 one-retry: see _progress_cb's twin — a fresh attempt must
            # not compute the plan or line counts over both streams
            # concatenated, and `seen` must not suppress its milestones.
            with state["lock"]:
                state.update(scanner=_StreamScanner(), shape=None, last=0.0,
                             has_text=False, seen=set())

        cb.reset = reset  # type: ignore[attr-defined] — read by _invoke's retry
        return cb, file_cb

    @staticmethod
    def _steps_total(state: dict, text: str, marks: list[tuple[str, int, int]]) -> int | None:
        """Step count from the streamed manifest block, once a later marker
        proves the block is complete. Parsed once, cached; None until then.
        `marks` are the scanner's (name, start, end) tuples."""
        if state["total"] is None:
            for i, (name, _start, end) in enumerate(marks[:-1]):  # only closed blocks
                if name == "manifest.yaml":
                    try:
                        manifest = yaml.safe_load(text[end:marks[i + 1][1]])
                        steps = manifest.get("steps") if isinstance(manifest, dict) else None
                        state["total"] = len(steps) if isinstance(steps, list) and steps else None
                    except (yaml.YAMLError, ValueError, RecursionError):
                        pass
                    break
        return state["total"]

    @staticmethod
    def _manifest_total(state: dict, content: str) -> None:
        """§8 file-writing form of `_steps_total`: step count from the
        manifest document's content (the caller gates on a later document
        proving it complete)."""
        try:
            manifest = yaml.safe_load(content)
        except (yaml.YAMLError, ValueError, RecursionError):
            return
        steps = manifest.get("steps") if isinstance(manifest, dict) else None
        state["total"] = len(steps) if isinstance(steps, list) and steps else None

    def _stage(self, job: dict, label: str) -> None:
        job["stage"] = label
        job["detail"] = None
        # §8 stage timing: the new stage's stamp also bounds the previous one.
        # Exactly one stamp per stage — re-asserting the current label (a sync
        # job's pipeline re-sets its only stage) must not append a duplicate
        # that would zero the stage's span.
        times = job["stageTimes"]
        if not times or times[-1]["stage"] != label:
            times.append({"stage": label, "time": time.time()})

    def _detail(self, job: dict, text: str) -> None:
        job["detail"] = text

    def _event(self, job: dict, text: str) -> None:
        """§8 activity feed: append a milestone and make it the live detail."""
        self._append_event(job, text)
        job["detail"] = text

    @staticmethod
    def _append_event(job: dict, text: str) -> None:
        # Serialized: the tool callback appends from the stdout read-loop
        # thread while the progress callbacks append from the scratch-watcher
        # thread — an unlocked append+trim pair can lose entries at the cap.
        with _EVENTS_LOCK:
            ev = job["events"]
            # §8: stage-stamped so the §11 thread can group the feed by stage
            ev.append({"time": time.time(), "text": text, "stage": job.get("stage")})
            # §8: capped at the newest 200 — a chatty stream must not grow the
            # job (and every poll response) for the call's whole lifetime.
            if len(ev) > 200:
                del ev[: len(ev) - 200]

    def _tool_cb(self, job: dict):
        """§8 activity feed: one event per streamed {name, input} tool use —
        `Reading <url>…` / `Searching the web for “<query>”…` /
        `Running a command — <command>…` / `Using <name>…` (handlers
        normalize their tool names to WebFetch / WebSearch / Shell where
        known)."""
        def one_line(value) -> str:
            # §8: feed entries are single lines — a multiline heredoc command
            # must not spray one bullet per line into the settled feed.
            return " ".join(str(value or "").split())[:120]

        def cb(tool: dict) -> None:
            if job["_cancel"] or job["status"] != "building":
                return
            name = tool.get("name") or ""
            inp = tool.get("input") if isinstance(tool.get("input"), dict) else {}
            url = one_line(inp.get("url"))
            query = one_line(inp.get("query"))
            command = one_line(inp.get("command"))
            if name == "WebFetch" and url:
                text = f"Reading {url}…"
            elif name == "WebSearch" and query.startswith(("http://", "https://")):
                # §8: Codex reports page fetches as web_search items — a URL
                # query is a read, not a search.
                text = f"Reading {query}…"
            elif name == "WebSearch" and query:
                text = f"Searching the web for “{query}”…"
            elif name == "Shell" and command:
                text = f"Running a command — {command}…"
            else:
                text = f"Using {name}…" if name else "Using a tool…"
            self._event(job, text)

        return cb

    def _fail(self, job: dict, msg: str, errors: list[str]) -> None:
        self._settle(job, "failed", error=msg, errorDetail=errors[:8])

    def _block(self, job: dict, at: str, blockers: list[dict], draft: dict | None,
               diagnosed: bool = False) -> None:
        # §8: a valid blocker envelope is its own terminal outcome, not a
        # failure. `diagnosed` marks build-diagnosis blockers (§19) so the UI
        # words the panel as a build failure, not an agent refusal.
        self._settle(job, "blocked", blockedAt=at, blockers=blockers, draft=draft,
                     diagnosed=diagnosed)

    @staticmethod
    def _parse_validate(raw: str, validator, locked_names: set[str] | None = None,
                        build_rules: list[str] | None = None
                        ) -> tuple[dict, list[str], list[dict] | None, str | None]:
        try:
            blockers, notes = parse_blockers(raw, locked_names, build_rules)
            if blockers is not None:
                return {}, [], blockers, notes
            files = parse_envelope(raw)
        except ValueError as e:
            return {}, [str(e)], None, None
        try:
            result, errors = validator(files)
        except (TypeError, AttributeError) as e:
            # §8 "a surviving validation failure never fails the job": a
            # validator tripping over an unexpected shape is one more error
            # line feeding the repair round, never a failed job.
            log.exception("drafting validator crashed on the response")
            return {}, [f"the response has an unexpected shape — {e}"], None, None
        return result, errors, None, None


draft_jobs = DraftJobs()
