# Autowright SPEC — Data model

Part of the Autowright spec. Index and § map: [SPEC.md](../SPEC.md). § numbers are global across spec files.

## 4. Data model

**Identity rule: every entity id (automation, execution, agent — any `id` field anywhere) is a
UUID (v4, lowercase hyphenated string). No sequential or slug-derived ids.** Version numbers
(`v1`, `v2`…) are labels, not ids, and stay integers.

Single central model drives everything. Top-level:

```
surface: onboard | app | create | menubar
page: automations | automation | executions | execution | agents | agentNew | secrets | settings | about
automationId, executionId: current selections
automations[], executions[], agents[], secrets[], settings, onboarding state, create state, transient UI state
```

The on-disk representation of these entities is §5.

### 4.1 Automation

```
id: uuid
name, description: strings — both are user-owned identity (§5: top-level automation.yaml, never
  versioned): the §8 create manifest seeds them, and after create they change only through
  the user — name via click-to-edit on the §11 Review title, description via click-to-edit on the
  §11 Review lede line; both also via §19 PATCH. Sync ignores the
  manifest's name and description. Edits never mark the workflow out of sync. A blank name is
  ignored (never cleared); a blank description clears it (description is optional).
  Names store **trimmed** (surrounding whitespace stripped at every write path; a
  whitespace-only name is a blank name) and are **unique** across automations, compared
  case-insensitively (the §4.7/§4.8 rule):
  the §19 rename paths reject a collision with 422 ("an automation named X already exists -
  automation names must be unique"), while the paths whose incoming name the user didn't just
  type - §19 create and §5.1 import - dedupe it by appending the smallest integer ≥ 2 that
  frees the name ("Name 2", "Name 3"), so create and import never fail on a name collision.
  Enforcement is write-time only - duplicates already on disk still load (the §4.7 rule).
  Ids stay the only binding; uniqueness exists for unambiguous display and the §20
  exact-name/substring resolution.
version: int (current)
triggers: ordered trigger list (§4.3) — user-owned, never versioned; the draft's spec-derived
  triggers are merged in when an edit is saved (§4.3 trigger merge)
triggerChip: derived chip string (§4.3): one trigger → its short label, several → "N triggers",
  empty → "No triggers"
allTriggersOff: bool — derived: the list is nonempty and every trigger is off (drives the OFF tag)
nextAtMs: epoch ms of the next enabled occurrence across all triggers (§4.3) | null
instructions: optional multiline free-text user instructions to the agent
notes: agent-owned working-knowledge document (markdown string, may be empty) — selectors and
  short HTML excerpts, API endpoints and quirks, approaches that failed and why, environment
  facts the drafting agent discovered while building and testing, and the reason behind any
  non-obvious choice a later sync might otherwise simplify away (rationale evident from the
  steps themselves is skipped). Written only by §8 agent
  responses (a chat or call-2 `notes.md` block — the agent keeps it a terse cheat sheet);
  user-readable and prunable in the §11 NOTES card. Versioned like spec/instructions, and sent back
  to the agent on every §8 chat and steps call so later syncs don't retry dead ends. A notes
  change never marks the workflow out of sync (§11): notes are advisory input to the next
  sync, not a contract the steps must match
lastStatus: succeeded | executing | failed | cancelled | interrupted | none — derived from the
  latest execution that actually ran; `skipped` and `queued` records never count (§6)
live: execution ids currently in progress, newest last — empty when idle. A list, not a single
  id: `maxParallel` may allow several at once.
maxParallel: int ≥ 1 (default 1) — how many executions of this automation may run at once
  (§6). User-owned and never versioned (§5 top-level `automation.yaml`), like `triggers`.
  Raising it above 1 is opt-in per automation because `memory/` is shared across concurrent
  executions (§6) — the §9.2 card cautions when the automation's steps actually touch memory.
maxQueued: int ≥ 0 (default 0) — how many message firings may wait when every slot is taken
  (§6 firing queue). 0 (the default) is skip-on-busy; queueing is opt-in per automation.
  Both concurrency fields change on the §9.2 card, or staged through the §11 chat (§8
  `concurrency` action — applied when the user saves, like `param_values`).
resultChip: short summary chip ("2 new chapters") | null — the chip is optional: null when the
  last successful execution never called result.chip(); failed automations synthesize "Needs attention"
resultStatus: changes | ok | attention | null — tints resultChip with the §7 chip colors
  everywhere it appears (list rows included); null whenever resultChip is null; "attention" for
  failed automations
lastExecutionLabel: shared time label (below) | "executing…"
  Every relative time label in the app uses one shared scheme: "Today" | "Yesterday" | full
  weekday name ("Thursday", 2–6 days back) | the date in the user's locale format (year,
  month, day — e.g. "7/18/2026"). Labels that carry a clock time append it: "Today, 8:00 AM".
latest: last execution's result object + when-label + executionId (links the detail page's
  result card to the execution page), for the detail page
params: parameter list (§4.2)
memory: { size, updated, path } — per-automation memory directory between executions (any
  files/formats): size a humanized byte label ("empty" when nothing is stored), updated the
  shared time label ("never written" before the first write), path the directory's absolute
  path — backs the memory card's Show in Finder (§4.9 Show-in-Finder rule)
snapshots: [{ id, name, reason, when, version, size, files }] — the §6.3 memory snapshots,
  newest-first; name = user label | null, reason ∈ manual | pre-clear | pre-version |
  pre-restore, when = humanized time label, version = "vN" current at capture (pre-version:
  the version about to execute), size = humanized byte label, files = file count
snapshotSettings: { preVersion, preClear, preRestore } — booleans, the §6.3 automatic-snapshot
  toggles (all default true)
steps: [{ name, file, description, code, agent?, why?, agents?, secrets?, packages?, timeout?,
  noTimeout?, retries?, infiniteRetries? }] — file is the version-folder script filename (§5 NN-name.py);
  code is
  human-readable script; agent
  marks a step that makes query-only runtime model calls (§6) — the script itself still does any
  changes. agents (agent steps only): ordered list of §8 grants the step may call, as
  { id, why? } entries - id is a §4.7 agent uuid, the binding a rename can never repoint.
  The first entry is what the bare `agent` handle is bound to; the others are addressable
  via `agents["<id>"]` (§6.1); empty/absent falls back to the automation's first enabled
  agent. An entry's
  why is that agent's role note (appended to its §9.2/§11 tag tooltip); §8 validation requires one on every
  entry when the step lists two or more agents. secrets: §8 grants the step uses, as
  { id, why } entries - id is a §4.8 secret uuid; why is the per-use note (§8 rule 6,
  required on every declared
  entry) appended to the key tag's tooltip (§9.2). A step's effective secrets are these ids
  unioned with the literal `secrets["<id>"]` subscripts in its code; a code-referenced id
  with no
  declared entry carries no why and its tooltip states only what the tag is. Display
  surfaces resolve entry ids to the LIVE agent/secret name (a rename updates every tag and
  tooltip immediately); an id matching no stored record renders a red deleted state showing
  a short id prefix. packages: §6.2 declared
  packages the step uses, as { import, why } entries — import names a declared package's
  module (§8 validation rejects an import the version's packages list doesn't declare) and
  why is the per-step note (§8 rule 5, required on every declared entry: what THIS step uses
  the package for — the same package can serve different jobs in different steps), shown in
  the box tag's tooltip (§11). A step's effective packages are these entries unioned with the
  declared imports appearing in its code; a code-matched import with no declared entry falls
  back to the package declaration's why; with no why at all the tooltip drops its why clause. All three lists are chosen
  by the drafting agent per the §8 selection rule (the SPEC and build instructions win when they
  name a choice; the drafting agent's own judgment otherwise). timeout: optional per-step time
  limit in seconds (positive int) enforced by the §6 watchdog; noTimeout: true removes the limit
  entirely (never combined with timeout — §8 validation); absent → the 900 s engine default (§6).
  Both are written by the drafting agent per the §8 timeout rule (short by default; long or
  unlimited only when the user asked). retries: optional per-step automatic retry budget
  (positive int ≤ 10): a failed attempt of the step is re-executed immediately, up to that
  many extra attempts per execution pass, before the step (and execution) fails (§7 step
  retry). infiniteRetries: true removes the budget — the step retries until it succeeds or
  the user cancels/skips (never combined with retries — §8 validation; the persistent-
  automation shape, usually together with noTimeout). Both absent → 0: first failed attempt
  fails the step, and there is no automatic execution-level retry (§6). Like the timeout
  pair, both are written by the drafting agent per the §8 retry rule. On disk and in the §8
  manifest the keys are spelled `no_timeout`, `infinite_retries` (§5 yaml is snake_case); the
  API serialization is `noTimeout`, `infiniteRetries` in every payload that carries steps,
  the §19 draft-job result included
spec: block list [{ kind: h1|h2|p|li, text }] — the human-readable spec. The §5 spec.md
  conversion parses `#`/`##`/`- ` prefixes into h1/h2/li and merges other consecutive
  lines into one `p` - except numbered-list lines (`1. `-style), which each keep their own
  `p` block so an agent-written numbered list survives the round trip readable
specMeta: "v3 · updated Yesterday" (shared time label)
packages: [{ pip, import, why }] — the current version's §6.2 declared packages ([] when
  none); why is the drafting agent's one-line GENERAL purpose (§8 rule 5 — required), shown
  under the package's row on the §11 Packages card (per-step purposes live on the steps'
  own packages entries, above); versioned like spec/steps — each version
  entry below carries its own list
versions: [{ version, when, note, spec, steps, instructions, notes, params, packages }] — prior-version
  history, newest-first (the current version is not repeated in this list)
draft: unsaved edit snapshot (create-flow shape) | null
agentId: agent that writes/edits this automation
stepAgents, allowedSecrets: string[] — per-automation enablement (set on save); both are id
  lists: stepAgents holds §4.7 agent uuids, allowedSecrets holds §4.8 secret uuids
originOs: macos | windows | linux | absent — the platform the automation was exported from,
  stamped only by §5.1 import (the archive manifest's `os`, when present; an unrecognized
  token stores as-is). Stored top-level (§5 automation.yaml, snake_case `origin_os`), never
  versioned, not serialized (`problems` below carries its user-facing form). Cleared by the
  next edit save on this machine (§4.4 save-new-version) — a local rework supersedes "built
  elsewhere"; a version restore keeps it (restoring is not a rework) — and set by no other
  write path.
unresolvedReferences: { id: { kind: secret|agent, name, description } } — the §5.1 archive
  references import could not match to a local record: id is the fresh uuid import minted
  into the step entries / code subscripts / trigger secret (it matches no stored record by
  construction), and name + description are the archive record's, so the UI and the §8
  drafting context can say what was wanted. Stored top-level (§5 automation.yaml,
  snake_case `unresolved_references`, absent when empty), never versioned, written only by
  §5.1 import. Pruned - never grown - by the §4.4 save-new-version write and by a trigger
  replace: entries whose id no longer appears in the current version's effective
  references (the step manifest ∪ code-subscript union above, plus discord trigger
  secrets) are dropped, so a fixed reference stops carrying its label while the rest keep
  theirs; a version restore keeps the map (restoring is not a rework, the `originOs`
  rule). Serialized as `unresolvedReferences` ({} when none), filtered to
  still-referenced ids. Loading is lenient like every §5 read: entries that are not a
  uuid-keyed mapping with a valid kind and a string name are dropped with a logged
  warning, never fatal.
problems: [{ kind, label }] — derived at serialization, never stored: the "would this fire
  successfully — and is it firing at all" audit backing the §9.1 Needs fixing chip, the
  §9.2 banner, and §20 output.
  Computed from stored facts (the execution index and the clock included) plus one §6.2
  installed-check — never a Keychain read or a
  harness probe (deliberate exclusions: §12 owns probe-based harness readiness, and a set
  secret whose Keychain entry vanished is caught by the §7 pre-step gate). The
  installed-check is served from a cached scan of the §6.2 environment, refreshed when that
  environment changes, so the audit never re-walks site-packages per automation. Each
  condition mirrors a real §7 pre-step gate or a §6.2/§5.1 fact (an unresolvable agent has
  no pre-step gate - it fails its step at step time); `overdue` alone mirrors the §6
  scheduling reality instead — its claim that scheduled moments passed with no run is
  checked against the execution record, so the chip still never cries wolf. Kinds,
  in serialized order (each `label` is the exact UI copy):
  - `overdue` — the schedule is being missed: some **enabled cron** trigger has had **two
    consecutive occurrences** pass since its baseline with no real run. The baseline is
    **per trigger**: the later of the automation's run baseline - the latest real
    execution's start (the `lastStatus` population: `skipped`/`queued`/test records
    excluded), falling back to the automation's `created_at` if it never ran - and that
    trigger's §4.3 `enabledAt` stamp; overdue iff the second §4.3 next-occurrence after
    that baseline (the same DST-aware math as `nextAtMs`) is already in the past. The stamp
    is what keeps a re-enable honest: occurrences that passed while the trigger was off
    are ignored even after it comes back on, so turning a cron on again after a week
    away (or adding a cron to an automation created long ago) starts counting from that
    moment - the same rule the §6 scheduler fires by, which never fires an occurrence
    that passed while the trigger was off. A trigger stored without the stamp counts from
    the run baseline alone, exactly as before (§4.3 - no compat shim, nothing healed).
    Two missed moments, not one, is the grace:
    a single occurrence legitimately skipped (§6 busy-skip, a restart at the wrong
    minute) never flags. Cron triggers only — one-shots are consumed by the §4.3 spent
    rule, and app-start/message triggers have no schedule; disabled triggers never count,
    and neither does a cron with `runIfMissed: false` (§4.3): a sleeping Mac is the one
    way an awake-and-running scheduler misses a moment, and that trigger opted out of
    chasing exactly those, so its misses are chosen, not a problem - the §6 drop record
    already shows each one.
    This is the one failure class nothing else surfaces: a silently dead automation
    (backend down at every scheduled moment, a wedged execution starving every firing)
    produces no record, no failed status, and no notification — this kind, the §13 tray
    dot, and the §6 overdue notification are its discovery path. "Scheduled executions
    are being missed — it last ran <§4.1 date label>." / never ran: "Scheduled
    executions are being missed — it has never run."
  - `secret-unresolved` — an effective step secret or a discord trigger's token secret
    references an id in `unresolvedReferences` (kind secret): the §5.1 import found no
    match for it, and the id matches no stored record by construction, so this kind and
    `secret-missing` are mutually exclusive per id and share the missing slot in the
    precedence below. "Imported secret NAME has no match on this Mac. Pick one of your
    secrets on the edit page." / trigger case: "A trigger needs the imported secret NAME,
    which has no match on this Mac."
  - `secret-missing` — an effective step secret (manifest entries ∪ code subscripts) or a
    discord trigger's token secret references an id no stored record holds (and no
    `unresolvedReferences` entry names). "A step
    references a deleted secret." / "A trigger references a deleted secret."
  - `secret-ungranted` — an effective step secret exists but isn't in `allowedSecrets`
    (discord trigger secrets are not grant-gated, §4.3). "Secret NAME isn't allowed for
    this automation yet — grant it on the edit page."
  - `secret-unset` — a referenced secret (step or discord trigger) whose record has
    `set: false`. "Secret NAME has no value yet — add it on the Secrets page."
  - `agent-unresolved` — an effective step agent references an id in
    `unresolvedReferences` (kind agent): the §5.1 import found no match for it (mutually
    exclusive with `agent-missing` per id, same slot in the precedence). "Imported agent
    NAME has no match on this Mac. Choose one of your agents on the edit page."
  - `agent-missing` — an effective step agent (manifest `agents:` entries ∪ code
    subscripts, the same union as secrets above) references an id no stored record holds
    (and no `unresolvedReferences` entry names). "A step references a deleted agent."
  - `agent-ungranted` — an effective step agent that exists but isn't in `stepAgents`.
    "Agent NAME isn't enabled for this automation yet — enable it on the edit page."
  - `package-missing` — a current-version declared package whose distribution the §6.2
    fast installed-check doesn't find (the softest condition: ensure self-heals it before
    step 1; it is listed because a failed install then blocks the run, and import is the
    one path that lands declared packages uninstalled). "Package NAME isn't installed
    yet — it installs on the first execution."
  - `os-mismatch` — `originOs` present and ≠ the running platform. "Built on <OS> — its
    steps may need rewriting before they run on this Mac." (<OS> is the display name:
    macOS / Windows / Linux; an unrecognized stored token shows verbatim and always
    mismatches.)
  A secret or agent yields at most one entry — precedence (unresolved | missing) >
  ungranted > unset, the order the §7 gates fail in; unresolved and missing are one slot,
  told apart by the `unresolvedReferences` map — and entries dedupe per referenced record
  (a secret three steps use is one row), sorted by name within a kind. Empty list =
  nothing to fix.
```

### 4.2 Parameter kinds

| kind | fields | one-line summary | edit behavior |
|---|---|---|---|
| `toggle` | label, help, on | "On"/"Off" | switch |
| `list` | label, help, validate, lines[] | validate → "N links" (valid-URL count), else "N entries" | one input per line, add/remove; per-line URL validation (red border plus a red "NOT A VALID LINK" tag on an invalid non-empty line when validate — detail page and editor alike); info line "N lines · G valid links[ · B needs attention]" |
| `kv` | label, help, rows[{key,value}] | "N entries" | key/value pairs, add/remove |
| `number` | label, help, value, min | value | digits-only; empty/below-min clamps to min |
| `text` | label, help, value, placeholder? | value or "Not set" | plain input |

Every edit saves automatically — there is no save or done action. Typing commits on a short
debounce (and on blur); toggle flips, row/line removals, and additions commit immediately. On
the automation detail page the `list`/`kv` editors are always fully shown — no
collapse/expand toggle (the one-line summary column still serves the execution page's
values-as-used block).

URL validity: `/^https?:\/\/\S+\.\S+/`.

Every definition carries a default: `toggle` → off, `number` → its `min`, `text`/`list`/`kv` →
empty. Definitions are versioned with the automation; values live in the top-level
`automation.yaml` and are matched by name and kind at execution/restore time (§5). The
§11 editor's chat can **stage** stored-value changes (§8 `param_values` action): the staged
name → value map rides the draft as the draft-only `param_values` key (§4.4) and is applied
to the automation's stored values only at save/create (§19 — matched by name **and kind**
against the landing version's definitions, unmatched entries dropped); until then the
automation's values are untouched, and Discard draft drops the staged map. `test_values`
stays test-only (§8 — the chat action and call 2's drafted manifest key alike; the drafted
map rides the draft as the draft-only `test_values` key, §4.4/§11, never the stored
values). The
value-merged serialization (the automation JSON's `params`, execution records) is the full
definition — `default` included — plus the resolved value field, so definitions survive a
round-trip through the editor (edit mode seeds the draft's params from the automation JSON;
a stripped default would make a §11 test resolve an unset param to empty instead of its
default).

### 4.3 Triggers

An automation carries an ordered list of **triggers** — independent conditions that each start
an execution. Triggers are user-owned operational state (§5): editing them on the detail page
never mints a version and never involves the AI. In the §11 editor the chat can edit the
**editor's** trigger list through §8 `triggers` ops (add/edit/enable/remove) — staged like
any editor trigger change, landing only when the draft saves. **Cron provenance
(`source`)**: a cron trigger carries `source: "spec" | "user"` — **required**: `spec` for
crons the §8 sync derived from the spec (and §5.1 imports — the archive travels with its
spec), `user` for crons the user minted directly (the §9.2 detail-page editor, a §8 chat
`triggers` op, §20 `trigger add`). A cron without the field is invalid — the API answers
422 (§19) and a stored one is dropped at load like any malformed trigger (§5 lenient
load). Only cron triggers carry `source`; the field round-trips through the API and drafts
like any stored field. The list additionally follows the spec via
the **§4.3 trigger merge** — saving an edit (§4.4) merges the draft's spec-derived triggers
(§8 rule 9) into the stored list:

- **Crons replace the spec-sourced cron subset**: a drafted cron matching a stored one
  (either source) on (`expression`, `timezone`)
  keeps that trigger's `id`, `enabled` state, `source`, and `runIfMissed` — except on a §20
  CLI push, where the workdir manifest round-trips `run_if_missed` explicitly, so a matched
  cron takes the manifest entry's value (absent = true) instead of keeping the stored one
  (§20 push rules); the §8 sync manifest never carries the key, so the app's merge always
  keeps the stored value; other drafted
  crons arrive enabled with fresh ids, `source: spec`, and the default `runIfMissed`; **`source: spec`** crons the draft no longer
  derives are dropped, while **`source: user`** crons always survive — a schedule the user
  set by hand (detail page, chat op, CLI) is never silently removed by a sync.
- **Message and app-start entries are additive**: a drafted `discord`/`imessage`/`app_start`
  entry matching a stored trigger of the same kind on its identity fields (discord:
  `channel`, `secret`, `pattern`, `mention`, `author`; imessage: `from`, `pattern`; app_start: the kind
  alone) leaves the stored trigger as is; an unmatched one is appended enabled with a fresh
  id. Stored message/app-start triggers the draft doesn't mention always survive — a sync
  never drops one.
- `time` one-shots are never drafted (§8) and always survive a save untouched. (An elapsed
  one is dropped by the save's validation instead - the spent-drop rule under one-shot
  semantics below.)

Manual starts (Execute now, the menu bar, CLI) are
not triggers in this list — they always work, whatever the list holds.

Trigger shape: `{ id: uuid, kind, enabled: bool, enabledAt?, …kind fields }` plus the
backend-derived display
strings `label` and `short`. The backend assigns `id` to entries that arrive without one. Kinds:

| kind | fields | fires | label / short |
|---|---|---|---|
| `cron` | `expression`: 5-field cron expression · optional `timezone` · optional `runIfMissed`: bool, default true (below) · `source`: `"spec"` \| `"user"` (provenance, above; required) | at every match | humanized when simple (below), else the raw expression in mono |
| `time` | `at`: wall-clock ISO timestamp ("2026-07-20T15:00"), seconds allowed ("2026-07-20T15:00:15") · optional `timezone` · optional `runIfMissed`: bool, default true (below) | once, then the trigger is consumed | "Once at Jul 20, 3:00 PM" / "Once Jul 20 15:00"; non-zero seconds append to the time in both strings: "Once at Jul 20, 3:00:15 PM" / "Once Jul 20 15:00:15" |
| `app_start` | — | at every desktop-app launch (§6 firing path) | "On app start" / "App start" |
| `discord` | `channel`: Discord channel id (ASCII digits) · `secret`: id of the §4.8 secret holding the bot token (a secret uuid) · optional `pattern`: text filter · optional `mention`: bool · optional `author`: sender filter, a list of Discord user ids (ASCII digits) | at every matching Discord message (rules below) | "Discord · `<channel>`" (+ " · “`<pattern>`”" when set) / "Discord" |
| `imessage` | `from`: sender handle (E.164 phone or email) · optional `pattern`: text filter | at every matching iMessage on this Mac (rules below) | "iMessage · `<from>`" (+ " · “`<pattern>`”" when set) / "iMessage" |
| `pubsub` | — | future message trigger | — |

**Enable stamp (`enabledAt`)** - the moment the trigger last became live: a §5 stored
timestamp (UTC ISO-8601 with offset), written when the trigger is created enabled and again
on every off-to-on transition. It is **backend-owned at every write path that replaces an
automation's trigger list** (the §19 PATCH, create, an edit save, the §20 CLI - all of them
whole-list replaces): the incoming list is reconciled against the stored one by `id`, the
stamp carried forward for a trigger that was already on and minted fresh for one that
arrives enabled and was not. A client can never set it - a sent value is discarded. Turning
a trigger off keeps the old stamp (the next on-transition overwrites it), and an edit that
leaves an already-on trigger on never re-stamps it: changing a live cron's expression is
not a re-enable. It rides in the stored trigger and round-trips through the API like any
other trigger field. Its one use is the §4.1 `overdue` baseline. A trigger on disk without
the field keeps the plain baseline (§5 lenient load) - never stamped on load or save, no
compat shim; an unreadable value reads as absent.

**Timezone (`timezone`)** — optional IANA zone name (e.g. `Asia/Tokyo`) on `cron` and `time`
triggers. Absent → the machine's local time (labels unchanged). Present → `expression` matches and
`at` reads as wall clock **in that zone** (DST rules below apply in that zone); occurrences
convert to local time for `nextAtMs`, countdowns, and the scheduler. An unknown zone name is
rejected at the API (422), never stored. When `timezone` is set, both display strings append the
zone's city — the last `/` segment of the IANA name, `_` → space — in parentheses:
"Daily at 8:00 (Tokyo)" / "Daily 8:00 (Tokyo)"; the raw-expression fallback and one-shot
labels get the same suffix.

**Run if missed (`runIfMissed`)** - optional bool on `cron` and `time` triggers, **default
true**. It decides what happens when the scheduler notices an occurrence late because the
Mac slept through it (the §6 missed-executions rule; the backend was alive but suspended):
true → the occurrence fires once on wake, exactly the §6 one-catch-up-per-wake behavior;
false → the slept-through span is dropped, nothing fires, and the trigger simply waits for
its next occurrence. The field covers sleep only: a backend that was not running when the
moment passed never catches up whatever the value (§6 - no startup catch-up queue), and a
past one-shot found on disk at load is consumed unfired either way. Storage: written to
`automation.yaml` only when false - an absent key reads as true, so every trigger stored
before the field existed keeps today's behavior (§21). The API serializes it explicitly on
every cron/time trigger (`runIfMissed: true | false`) and accepts it on the same two kinds;
a non-boolean value answers 422, and on any other kind it is ignored and never stored,
exactly like `timezone`. The §4.3 trigger merge carries it like `enabled` (a matched cron
keeps it; a freshly derived cron gets the default), the §8 `triggers` `edit` op keeps it
like `id` and `enabled` (the rule-9 dialect cannot set it - it is the user's operational
choice, set on the §9.2 editor or the §20 CLI), and it rides the §5.1 archive and the §20
manifest as `run_if_missed: false` (absent = true).

`pubsub` is a reserved kind only: the API rejects writing it with 422; the UI does not
surface it. Nothing else about it is specified yet.

**Discord triggers** — the user supplies their own Discord bot: an application created in the
Discord developer portal with the **Message Content intent** enabled, invited to the server
whose channel the trigger watches. The bot token is stored as an ordinary §4.8 secret and
referenced by id via the trigger's `secret` field (the §4.8 uuid — the same reference identity
steps bind by; display surfaces resolve it to the live name) — the token lives in the
Keychain, never in the trigger. Firing rules, applied by the §6 listener manager to every gateway
`MESSAGE_CREATE` in the trigger's `channel`:

- messages authored by **any bot** (including the listening bot itself) never fire — a
  `reply()` (§6.1) can never trigger the automation it came from;
- `mention: true` → only messages that @-mention the bot fire. Both mention forms count: the
  bot **user** (`mentions` carrying the bot's user id) and the bot's **managed role**
  (`mention_roles` carrying a role whose `tags.bot_id` is the bot) — typing `@BotName` in a
  server often inserts the role mention Discord created for the bot, not the user mention.
  `@everyone`/`@here` do not count;
- `author` → only messages whose author's user id is in the list fire — the authorization
  filter for shared channels: without it, any channel member who passes the other
  filters can start the automation;
- `pattern` → only messages containing the pattern fire (case-insensitive substring).

All present filters must pass (AND).

A firing starts an execution with trigger label "Discord" and the §4.5 `triggerPayload`; the
§6 one-execution-at-a-time skip applies like any trigger. Like
`app_start`, a discord trigger has no computable next occurrence — `nextAtMs` ignores it, and a
list whose only enabled triggers are message triggers shows the listening status line below.
Validation (§19, 422 otherwise): `channel` a nonempty ASCII-digit string, `secret` a uuid
string (lowercase hyphenated, the §4 id form; it need not resolve to a stored secret — a
deleted, dangling, or valueless secret surfaces as a `connection` error, not a 422, so a
mid-edit secret deletion can never block a save), `pattern` when present a
nonempty string, `mention` a bool, `author` when present a nonempty list of nonempty
ASCII-digit strings (Discord user ids, like `channel`); its entries are trimmed, deduped,
and sorted at save, so element order never distinguishes two triggers — the trigger-merge
identity compares the normalized list. The serialized trigger of
kind `discord` additionally carries **`connection`** — derived, never stored: the listener
manager's connection state for the trigger's token,
`{ state: connected | connecting | error, error? }` (`error` is the plain-word failure, e.g.
"secret DISCORD_BOT_TOKEN has no value yet" or Discord's close reason; error copy resolves
the secret id to its live name, falling back to a short id prefix when no stored record
matches — the §4.8 ids-bind-names-display rule).

**iMessage triggers** — the Mac's own Messages account is the identity: no bot, no secret.
The §6 listener manager watches the Messages database (`chat.db`) while at least one enabled
`imessage` trigger exists. The watcher reads **only** rows the enabled triggers could fire
on — filtered in the query itself to the configured senders, incoming, plain messages (§6
data minimization); conversations no trigger watches are never read or decoded. The firing
rules, applied to every row the watcher reads:

- messages sent **by this Mac's account** (`is_from_me`) never fire — the loop-safety analog
  of Discord's bot rule: a §6.1 `reply()` (or the §6 busy notice) can never trigger an
  automation. Consequence, stated wherever the trigger is explained (§9 setup guide): texting
  *yourself* from another device on the same Apple ID cannot trigger — the sender must be a
  different handle (another person, or a dedicated Apple ID signed into Messages on this Mac);
- the sender's handle must equal the trigger's `from` (case-insensitive exact match — phones
  are matched in the E.164 form Messages stores, e.g. `+15551234567`);
- messages in **group chats** never fire — direct (1:1) conversations only; group triggers
  are future work;
- `pattern` → only messages containing the pattern fire (case-insensitive substring, same as
  Discord);
- tapbacks/reactions, edits of earlier messages, and messages with no decodable text never
  fire.

A firing starts an execution with trigger label "iMessage" and the §4.5 `triggerPayload`;
queueing, skip, and the busy notice behave exactly as for Discord (§6). Like every message
trigger it has no computable next occurrence — `nextAtMs` ignores it. Validation (§19, 422
otherwise): `from` is either an **email** (contains `@`, no whitespace) or an **E.164
phone** — `+` then 3–15 digits, matching the form Messages stores; obvious phone formatting
(spaces, dashes, dots, parentheses) is stripped at save, so `+1 (555) 123-4567` stores as
`+15551234567`, but a number without the leading `+`/country code is rejected — it could
never match a stored handle, and a trigger that silently never fires is the worst failure
mode. `pattern` when present a nonempty string. Serialized `imessage` triggers carry the same derived **`connection`** field as Discord —
all of them share the one §6 watcher, so they all report its state; the plain-word errors
name permissions where relevant (e.g. "needs Full Disk Access — grant it in System Settings →
Privacy & Security → Full Disk Access").

**Cron dialect** (implemented in `triggers.py` — the one trigger-math implementation, no new
dependency; the renderer has none and previews via §19 `POST /triggers/preview`): five
whitespace-separated
fields — minute, hour, day-of-month, month, day-of-week (0–6, Sun = 0) — each `*` or a comma
list of numbers, ranges (`a-b`), and steps (`*/n`, `a-b/n`). Numbers only: no month/day names,
no `@daily` macros, no seconds field. Standard Vixie rule: when day-of-month and day-of-week are
both restricted, a date matching either one fires. Times are wall clock in the trigger's zone
(`timezone`, default local); an occurrence erased by DST (spring forward) still fires, shifted
forward by the gap width (a "2:30" on the day the clock jumps 2:00→3:00 fires at 3:30 — the
erased wall time read with the pre-transition offset), and one repeated by
fall-back fires once. Invalid expressions are rejected at the API (422), never stored.
(A system-timezone change that rewinds the wall clock is indistinguishable from fall-back
and is handled the same conservative way — occurrences in the rewound span do not re-fire;
a rewind larger than any DST shift logs a scheduler warning so a reported miss is
diagnosable.)

**Humanized cron labels** — exactly two shapes get words; everything else displays the raw
expression:
- `M H * * *` → "Daily at 8:00" / short "Daily 8:00"
- `M H * * D` (single day) → "Mondays at 9:00" / short "Mon 9:00"

The day field only humanizes as a single digit `0`–`6` — anything else (`7`, `07`, `12`) falls
back to the raw expression. The fallback shows the expression trimmed of surrounding
whitespace. One implementation: the backend (`triggers.cron_display`) — serialized triggers
carry the derived `label`/`short`, and the editors label unsaved entries through §19
`POST /triggers/preview`, so no second implementation exists to drift.

**One-shot semantics** (`time`): `at` must be strictly in the future when saved (the check reads
`at` in the trigger's `timezone`). A brand-new entry - one arriving without an `id` - answers
422 when `at` has passed. An entry that arrives *with* an `id` the automation does not currently
store is the spent case: a staged one-shot (chat op in a draft, §8) whose moment passed before
the save landed, or a stored one the scheduler consumed mid-edit. The save **drops it
silently** - not stored, not a 422, the rest of the list saves normally - so an elapsed staged
one-shot can never block a create, version save, or trigger PATCH. (A client-fabricated id
therefore still cannot store a past time: the entry stores nothing at all.) An id the
automation does store revalidates leniently and survives the save.
The trigger is consumed — removed from the list — when it fires, and equally when its moment is
skipped (backend down when it passed, superseded mid-execution, or dropped by `runIfMissed:
false` after a sleep, §6). It never lingers spent.

**App-start semantics** (`app_start`): fires when the desktop app launches — the Electron
process starting (§6 firing path), not a window reopening from the tray. No fields, no `timezone`.
An automation holds at most one: a list carrying a second `app_start` answers 422 and nothing
is stored. It has no computable next occurrence — it never contributes to `nextAtMs` — and it
survives an edit save (the §4.3 trigger merge never drops it — a drafted `app_start` merely
matches the stored one).

**Next occurrence:** each enabled (`enabled: true`) trigger computes its own next time — cron: the
next expression match strictly after now; time: `at`. The automation's `nextAtMs` is the minimum
across them, null when no enabled trigger has one. The countdown renders "next in Xd Xh" /
"Xh Xm" and refreshes every 30 s.

**Derived display:** `triggerChip` — one trigger in the list → its short label; several →
"N triggers"; empty list → "No triggers". `allTriggersOff` — nonempty list, every entry off; list
rows add an OFF tag to the chip (§9.1).

Detail-page trigger status line (under the §9.2 TRIGGERS rows):
- executing → "Executing now… the triggers are unchanged." (spinner icon); the chip reads
  "`<triggerChip>` · executing now"
- no triggers → "No triggers set — executes only when you press Execute now or use the menu
  bar." (pause icon)
- all off → "All triggers are off — won't execute on its own. Execute now and the menu bar
  still work." (pause icon); the chip reads "`<triggerChip>` · triggers off"
- `nextAtMs` null but an enabled message trigger (`discord`/`imessage`) exists → "Listening for
  `<what>` — executes when a matching message arrives. Execute now and the menu bar still
  work." (clock icon), `<what>` being "Discord messages", "iMessages", or "messages" when both
  kinds are enabled; the chip shows just `triggerChip`
- `nextAtMs` null but an enabled `app_start` exists → "Executes when this app next starts —
  Execute now and the menu bar still work." (clock icon); the detail-page trigger chip reads
  "`<triggerChip>` · on app start"
- `nextAtMs` null otherwise (e.g. an elapsed enabled one-shot not yet consumed) → "No upcoming
  occurrence — Execute now and the menu bar still work."; the chip shows just `triggerChip`,
  never a dangling countdown
- else → "Next execution in `<countdown>` (`<short label of the next trigger>`) · executes even
  when the app is closed." (clock icon); the chip reads "`<triggerChip>` · next in
  `<countdown>`"

### 4.4 Versions and drafts

- Saving an edit creates version N+1 (on disk: a fresh `versions/vN+1/` folder, then the
  `current_version` pointer flip, per §5), applies spec/steps/instructions/stepAgents/allowedSecrets/
  agentId, merges the draft's trigger list into the automation's (§4.3 trigger merge —
  triggers themselves stay unversioned), applies the draft's staged `param_values` to the
  automation's stored values (§4.2 — name+kind matched against the landing version's
  definitions, unmatched entries dropped), applies the draft's staged `concurrency`
  (§8 action — partial `{ maxParallel?, maxQueued? }` over the stored §4.1 fields, like
  the §19 PATCH), sets `specMeta` to "vN · updated Today".
  Prior versions are untouched. **Operational-only save skips the version mint**: when the
  sent draft's versioned content — spec, steps, instructions, notes, param definitions,
  packages — equals the current version's (compared over the stored serialization, ids and
  timestamps aside), the save applies only the operational state (trigger list, staged
  param values, grants, identity patch) and mints no version — a chat session that only
  changed a schedule or a value must not litter the Version menu with identical versions.
  Staged concurrency counts as operational state, like the trigger list and staged param
  values. The response is the same automation JSON either way.
- Leaving the editor with unsaved touched changes snapshots a **draft** onto the automation
  (toast: "Draft kept — resume it from this automation anytime."). Every exit path
  persists it — the header back button, system back/forward navigation, anything that closes
  the editor — never just the header button. Discard draft and Save as vN+1 settle the draft:
  leaving after either writes nothing (a discarded or saved draft is never resurrected).
  The Draft view is the only editable view (§11: every listed vN row is read-only history —
  the current version is not a selectable view, below); touched Draft edits persist on every
  draft-keep path (leaving the editor, switching views in the Version menu), never silently
  discarded. (Defensive: should the working state ever record the current version as the
  view, its touched edits still count as draft edits — the keep paths must not drop them.)
- The draft snapshot carries the **full working state**: spec, steps, instructions, notes
  (§4.1), params,
  packages, the editor's trigger list (stored as a draft-only `triggers` key — the §4.3
  merged preview, so a resumed draft keeps a synced schedule change), the chat-staged
  stored-value map when nonempty (§4.2 — stored as a draft-only `param_values` key, so a
  resumed draft keeps staged values), the chat-staged concurrency object when nonempty
  (§8 `concurrency` action → payload `concurrency` → draft-only `concurrency` key, so a
  resumed draft keeps a staged concurrency change), the drafted test-value map when the pipeline
  delivered one (§8 call-2 manifest `test_values` → payload `testValues` → draft-only
  `test_values` key, so a resumed draft still seeds its test setup, §11), the editor's
  step-agents + allowed-secrets grant selections (stored as
  draft-only `step_agents` / `allowed_secrets` keys in `draft/automation/automation.yaml`, §5),
  and the §11 out-of-sync state (`outOfSync` on the payload → draft-only `out_of_sync` key —
  a kept draft whose steps lag its spec must resume with saving still locked, §11 dirty
  gating). The §11 chat thread is **not** part of the draft payload: it persists on its
  own, decoupled from the draft's lifetime (thread lifetime below).
  Persisted thread entries: `{ id: uuid, kind: user | answer | activity | rewrite |
  blockers | system
  | error, text?, title?, icon?, outcome?, boundary?, blockers?, source?, diagnosed?, dismissed?, resolved?, eventDurationsMs?, at }` —
  `icon` an optional Font Awesome class stamped at creation, driving the §11 block
  glyph on system and answer entries (`title` doubles as the §11 answer header on
  answer entries); entries without them fall back per §11 —
  `user` a
  message, `answer` the agent's markdown reply, `activity` a settled §8 job's record
  (`title` = its final stage label, text = the stage's settled feed lines (one per line —
  the composition under `eventDurationsMs` below), `outcome` = the job's
  settled status — done | blocked | failed — driving the §11 outcome glyph; an entry
  persisted before the field existed has none and renders as done; `eventDurationsMs` =
  one duration per `text` line, parallel by index with `null` where no stamp bounds the
  line, derived by the editor from the §8 stage-timing stamps at settle — the lines are
  the stage's settled feed: a leading canned-description line when the stage's pre-first-
  milestone gap was material, then one line per event, or an empty feed's canned
  description line alone carrying the stage's whole span (§11); entries persisted before the
  field existed carry none and render without durations — additive, §21.4; §11),
  `rewrite` a spec-updated event (text = one-line summary), `blockers` a §8 blocker list —
  each blocker `{ reason, fix, details?, kind? }`, `kind` only ever the literal
  `user-action` (§8 blocker response) —
  (`source`: chat | sync — which call produced it; `error` entries may carry a `source`
  the same way), `system` a
  quiet status chip, `error` a red failure entry (a failed §8 job's message, §11) — persisted
  so a later chat's CONVERSATION context still names the failure. The §11 thread progress
  entry (live job progress) is editor state only, never persisted.
- **Thread lifetime & boundary markers.** The thread lives at the container root
  (`chat.jsonl` beside `automation.yaml`; for the create-mode slot, at the slot root — §5),
  read and written through the §19 `GET/PUT /chat/{owner}` surface (owner: automation id or
  `pending`), decoupled from the draft: **settling the draft never deletes the thread** —
  it is deleted only by §11 Clear chat (empty list unlinks the file), with its
  automation, by the §9.1 discard-and-start-new confirm (which follows the slot's
  draft DELETE with a `PUT /chat/pending` of `[]`, so the fresh create session opens
  with an empty thread), or by the §11 **fresh-entry clear**: opening the create flow
  when the pending slot holds no draft to resume discards the leftover slot thread
  (the editor drops the fetched thread and PUTs `[]`) — a new automation always opens
  on the §11 create empty state, and the slot thread survives entry only beside a
  kept draft or a building or held §19 drafting job (§11 background continuation —
  a slot that owns one is a session to resume, never cleared over). Instead, every settle **appends a boundary marker** — a `system` entry with
  `boundary: true`, `icon: fa-flag-checkered`, and the settle's text ("Draft saved as vN." ·
  "Changes saved — no new version." for the §4.4 operational-only save · "Draft discarded." ·
  "Created as v1.") — written **backend-side by the settle endpoint itself** (§19: save,
  draft-container DELETE, create), never left to the client, so a crashed or stale editor
  can't leave a settled draft's conversation unmarked. Appending a marker also stamps
  `dismissed: true` on the thread's open `blockers` entries (they describe a draft that no
  longer exists) and is skipped when the thread is empty or its last entry is already a
  boundary marker. Everything at or before the newest boundary marker is **history**: still
  rendered in the thread, but never sent to the agent (§8 CONVERSATION clips there) — a new
  draft session's AI starts clean. Create migrates the pending slot's thread into the new
  automation's container (marker appended after the move), so the conversation continues on
  that automation's edit page. Restore-as-vN+1 is not a settle (the stored draft survives
  it, §11) and appends no marker.
  Resuming restores the grant checkboxes from the draft; the automation's live
  stepAgents/allowedSecrets stay untouched until the draft is saved as vN+1. A Draft
  execution honors the draft's grants when present, not the live ones.
- Draft persistence is **continuous, not exit-only**: once a draft holds anything worth
  keeping (touched edit-mode changes; a landed create-mode spec or steps), the editor
  writes it with a debounced PUT (~1 s after the last change) as the state evolves, and the
  exit paths write one final time. A quit, force-quit, or crash therefore loses at most the
  debounce window — never the draft (unmount cleanup alone doesn't run when the app
  quits). Settling (discard, save, Create, Start over) stops the debounced writer before
  deleting, so a trailing write can't resurrect a settled draft. A §8 drafting job is
  **runtime state, never part of the serialized draft**: like the §11 draft test it
  outlives the editor page — leaving the editor keeps it building in the background
  (§19 background continuation; the §11 re-attach reconciliation picks it back up) — but
  it does not survive a backend restart, and settling the draft cancels it and drops any
  held outcome (§19).
- **Create-mode drafts persist too**, in the single pending slot `<root>/draft/` (§5).
  Opening the create flow creates the slot's container first — `draft/` with an empty
  `memory/` (`POST /draft/pending/open`, §19) — before any drafting; §11 create-mode tests execute
  as test execution records in the executions tree, not inside the slot. Leaving the create
  flow after a draft has landed (spec or steps present) keeps the full
  working state there — the same serialization as an edit-mode draft (the agent and secret
  grant selections ride the same draft-only `step_agents` / `allowed_secrets` keys), plus
  the identity fields no automation record exists to hold yet (name, description, chosen agent,
  triggers). Opening the create flow while the slot exists resumes it straight on the
  Review page (toast: "Resumed your unsaved draft — Start over discards it."); the §9.1
  list header surfaces the slot as a Resume draft button, and its New automation button
  (and the empty-state CTA) confirms then deletes the slot's draft **and clears its chat
  thread** to start fresh. Start over
  (and Back to Ask) deletes the slot's draft. Create consumes it: `versions/v1` is written
  from the sent draft and `<root>/draft/` is emptied — a settled draft is never
  resurrected. In every settle case the slot's `chat.jsonl` survives per the thread-lifetime
  rules above (Create moves it to the new automation; the others leave it in the slot
  behind a boundary marker — a slot holding only `chat.jsonl` reads as "no pending draft"),
  with one exception: the §9.1 discard-and-start-new confirm clears the slot's thread
  right after the discard, so the next create session starts empty.
  One pending draft at a time: every keep overwrites the slot. Leaving with nothing
  landed just leaves the empty container behind; the next open reuses it.
- In edit mode the review footer shows a **Keep draft** bordered button placed directly to
  the left of the Save as vN+1 button (only while there is something to keep: touched
  changes or a stored draft). It leaves the editor through the same keep path as the header
  back button — so keeping the draft is a visible choice, not an accident of which button
  you noticed.
- Editor version menu lists: Draft ("your working copy — unsaved"), then each older vN
  (date, always with the year · note). **The current version is never a selectable
  option** — the Draft *is* the working copy of it, so a current row would only duplicate
  Draft (or, with edits pending, show a state Save is about to replace). It appears only
  as an inert header at the top of the menu — "vN · current" with the explainer "Your
  draft builds on this — Save lands it as vN+1." — the same header treatment as the
  detail-page menu (hidden as an option, not disabled). Loading an old version shows a banner: "Loaded vX from history.
  Saving restores it as vN+1 — your draft stays in the Version menu." with a bordered
  **Back to draft** button; Save label becomes "Restore vX as vN+1".
- **Deleting old versions** (editor version menu only): every *older* row carries a trash
  icon button (`.ad-btn-icon.danger`) on its right. The Draft row and the current-version
  header never show it — **hidden, not disabled**: deleting the current version is
  structurally impossible (restore another version first and it stops being current), and
  permanently inapplicable actions hide rather than grey out — the same rule that keeps
  the current version out of both menus' selectable rows. The trash opens a danger ConfirmModal ("Delete v X? · v X is deleted
  from the version history. This can't be undone. Past executions of v X stay in
  Executions."); confirming calls the §19 DELETE, reloads the automation, and toasts
  "v X deleted." If the deleted version was the one being viewed, the editor jumps back to
  the Draft view (the loaded-from-history banner leaves with it). Deletion is
  irreversible — no snapshot, no undo. The backend refuses deleting the current version
  (400) and a version with a live or queued execution on it (409 — an in-flight Execute
  once must not lose its content mid-run); a failed execution whose version was deleted
  can no longer Retry (its §7 retry answers 404), and its stored record is untouched.
- Detail page: the version menu is **read-only history** — older rows show version, note, and
  date and carry no actions. There is no Execute once in the UI: re-executing an older version
  from the app was removed (params are current stored values and old versions can hold stale
  assumptions); executing an old version stays possible through §19 execute `version` and the
  §20 CLI's `execute --version`. The menu's footer explainer: "Triggers and Execute now always
  use the current version. To make an older version current, open Edit and restore it from
  the Version menu." Draft banner offers Resume editing / Discard — the UI has no
  Execute-draft action; draft iteration happens through the editor's §11 Test.
- **A Draft execution executes on the draft's own memory** (`draft/memory/`, §5). Draft
  executions start only from the §19 execute API (`version: "draft"`) — no UI surface offers
  one. The memory is seeded as a copy
  of the automation's live memory the first time the draft executes, then reused by every later
  Draft execution — so a draft iterates on one stable memory — and kept across draft re-saves
  from the editor. It is deleted with the draft (discard, or save as vN+1: the new version
  continues from the live memory, which no Draft execution ever wrote).

### 4.5 Execution (the stored record of one occurrence of an automation)

```
id: uuid, automationId: uuid | null (null on a create-mode test — no automation record exists yet),
automationName: automation name — serialized live from the automation while it exists, else the §5
  execution-time snapshot (a deleted automation's executions keep rendering their historical
  name),
automationDeleted: bool — derived: automationId names an automation that no longer exists (false when
  automationId is null — a create-mode test never had one to lose),
kind: version | draft | test — what was executed (§11 test executions are kind `test`), status,
version: int | null — the executed version number; null unless kind is `version`. The API
  serialization derives the display pair from these two: `versionLabel` ("v3", "Draft", "Test") and
  `test` (kind == test) — neither is stored. Test executions appear in the Executions list
  (§7) but are excluded from the detail page's RECENT EXECUTIONS and an automation's
  execution-derived display state (lastStatus / latest result / live); deleted when the
  draft settles and by starting the next test — the list row disappears with the record
trigger: manual | menubar | cron | time | app_start | discord | imessage | test (future:
  pubsub) — the machine kind of what started the execution; stored as data, never the UI
  copy. The serialized `trigger` is the derived display label (manual → "Manual",
  menubar → "Menu bar" on macOS and "Tray" on Windows and Linux (the §9 per-OS copy rule —
  the one label that names a platform surface), cron → "Cron", time → "Once", app_start → "App start",
  discord → "Discord", imessage → "iMessage", test → "Test", and the reserved
  pubsub → "Pub/Sub" — present in the backend label map for §4.3's reserved kind only; the
  API refuses to store pubsub triggers, so no record ever carries it and the renderer's
  trigger-label union omits it), and §19 execute requests
  send the kind
queuedAt: ISO timestamp | null — set when a §6 firing-queue entry is admitted, kept after
  promotion so the record shows how long it waited; null on every execution that started
  immediately. `startedAt` is (re)stamped when the record actually begins executing, so a
  promoted entry's duration measures execution, not waiting.
triggerPayload: message-trigger context | null (every non-message execution) — for Discord:
  { kind: "discord", text, sender (the author's display name), channel, channelName | null,
  guildName | null (both resolved best-effort from the §6 gateway guild cache at firing
  time — null for DMs or a cache miss; displays fall back to the raw channel id), messageId,
  guildId | null (null for DMs), secret (the trigger's token-secret id, §4.8 uuid — reply
  routing, §6.1; never displayed by any surface), at (message ISO timestamp) }; for iMessage: { kind: "imessage", text, sender (the
  sender's handle — E.164 phone or email; no Contacts lookup), chat (the Messages chat guid —
  reply routing, §6.1), messageId (the message guid), at (message ISO
  timestamp) }. Persisted on the record, snapshotted at start —
  reply() keeps working on an in-place §7 retry even if the trigger was edited since.
  Exposed to steps via §6.1 (`execution.trigger_payload`, `AUTOWRIGHT_TRIGGER_PAYLOAD`).
  A §4.5 `test` execution also carries a payload when the test request mocked one (§19
  `triggerMock`, §11 test trigger message): same shapes, with the fields the backend can't
  truthfully supply null — discord `channelName`/`guildName`/`guildId`/`messageId`,
  iMessage `chat`/`messageId` — and `at` set to the test start; the trigger kind stays
  `test`
triggerSender: string | null — the payload's `sender`, lifted onto every execution row
  (list JSON carries no payload; the full payload is full-record-only). Lets the §7 and
  §9.2 trigger columns read "Discord · Dave · v3" without fetching the full record;
  null on non-message executions. Persisted in the §5 header index so it survives restart
duration, started ("Today, 8:00 AM"), startedMs, endedMs (0 while live and on rows whose
  `finished_at` was never set, e.g. §3 interrupted) — duration accumulates across in-place retry
  passes (§7); started never changes on retry
queuedMs: epoch ms of `queuedAt`, 0 on every execution that never waited — what the §7
  executions list ticks its QUEUED FOR column from
steps: [{ name, file, status, duration, attempts: [{ number, status, duration, startedMs }] }] — file is the
  version-folder script filename (keys the per-attempt log files, §5). `file` is
  record-only: the API's full-record serialization emits only name/status/duration/attempts, and
  the §19 log endpoint addresses attempt files by step index, which is how the renderer keys
  them. A step's status equals
  its latest attempt's status, or queued when it has no attempts yet; attempt statuses use the
  step vocabulary (§4.6); duration is the latest attempt's duration. `number` is monotonic per step,
  never re-derived from list length: only the latest 20 attempts are retained — appending an
  attempt past that prunes the oldest entry and its log file (§5) — so an `infiniteRetries`
  step (§4.1) can't grow the record without bound; the true attempt count is the latest
  attempt's `number`, which is what the §7 ×N chip and attempt control read. On disk each step also stores
  `agent` (bool — the §4.1 agent-step flag, snapshotted at execution start) and
  `sha`, a short hash of the script as executed — the §7 Draft-retry drift check compares it,
  since a re-saved draft can change a step's code without changing its name or file
result: result object | null
workspace: string — full-record-only: absolute path of the execution's `workspace/` dir (§5),
  backing the §7 workspace link ("Show workspace in Finder", §4.9 Show-in-Finder rule)
logs: string — full-record-only: absolute path of the execution's `logs/` dir (§5), backing
  the §7 LOGS pane's "Show logs in Finder" button (§4.9 Show-in-Finder rule; the dir exists
  from record creation, so the button never has to guess)
redactedSecrets: secret names redacted in logs (a list) | null when none — display surfaces join it
params: the execution's snapshot of the automation's param definitions + resolved values — the
  §4.2 value-merged serialization, taken at execution start; stored in execution.yaml (§5),
  full-record-only in the API
note: optional note ("previous execution still in progress", "the queue was full (N waiting)",
  "Mac went to sleep") | null
error: { step | null, message, reason | null } | null — failed executions only: the failing
  step's name — null when the execution failed before any step ran (the pre-step secret
  checks, a package-install failure; the §7/§9.2 failure headline then reads "Execution
  failed") — its error message (redacted), and a plain-word possible reason when the engine
  can classify the failure (§7 failure diagnostics). The same error is also stored on the failing
  attempt ({ message, reason }); the execution-level field mirrors the latest failing attempt
  and is cleared by a retry pass that succeeds (attempt history keeps the old error)
pgid: int | null — on-disk only (never in list/full JSON): the process-group id of the live
  step's executor subprocess (each step runs in its own session, §7), stamped when the step
  spawns and cleared when the execution finishes. §3 startup recovery uses it to SIGKILL a
  step group orphaned by a backend crash — after checking the group still contains an
  `autowright.executor` process (pid-reuse guard) — before marking the record interrupted, so
  an orphan can't keep writing `memory/` while the next execution starts. Backend shutdown
  hard-kills every live step group the same way (an interrupted record must not leave its
  processes running)
agentPgids: int list — on-disk only (never in list/full JSON), default empty: the process-group
  ids of any §6.1 runtime agent call's harness CLI currently in flight (each spawns in its own
  session, §7 kill semantics), stamped when the executor reports the call starting and removed
  when the call returns. Cancel/timeout/skip kill these groups right after the step group, and
  §3 startup recovery sweeps any left behind (same pid-reuse guard as `pgid`); absent in
  pre-existing records, which load as empty
```

Logs are not part of the record payload: they live as per-step-attempt NDJSON files in the
execution directory (§5) and are fetched lazily per selected step/attempt (§19); the record
carries only the `logs` dir path above, so the §7 pane can reveal the files themselves.

Result object:
```
{ chip?, chipStatus?: changes|ok|attention — both only when the execution set a chip,
  files: [{ name, size }] — every file in the result dir, plus the dir
        path for the "Show in Finder" button }
```

The chip is optional — an automation may choose not to use one. It is stored on the execution
record itself (`chip` + `chip_status` columns in `executions.db`, §5): the engine copies
`result.chip(...)`'s text and the execution's `result.status(...)` (default `ok`) onto the record at
execution end, with no synthesized fallback text — an execution that never calls `result.chip()` shows no
chip anywhere.

On disk the rest of the result is a directory: the execution writes its output files
directly into `result/` (result.md, result.html, images, CSVs, …). There is no manifest — the
file list is the directory listing. Renderable files get their own result views (§7): `.md`
rendered as GitHub-flavored markdown — one shared Markdown component (react-markdown +
remark-gfm, app-styled; output is React elements, never injected HTML, so no sanitizer is
needed) used everywhere the app renders markdown, with one standard styling for every
surface: result views, the Build-instructions and Framework-instructions cards, and the
Spec cards (create flow and automation page — no spec-specific look; markdown renders the
same there as anywhere else). The component offers exactly one **compact variant**
(`small` prop → `.ad-md-sm`) for the §11 chat thread, the app's one dense narrow
markdown surface: the same look scaled to the thread's type scale — body and list text
12.5 px with list items in the same `--text-2` as paragraphs (the standard styling's
brighter `--text` list items would read as random emphasis inside a conversation),
headings capped at 13.5 px, code wells one step smaller, and the full-bleed table wrap
matched to the pane's 16 px padding instead of the result card's 18 px. No other
per-surface markdown styling exists — `.html` in a sandboxed iframe (no
scripts, no remote loads — preserves the §6 no-exfiltration guarantee) with the app's base
result stylesheet injected, so plain semantic HTML renders in app typography and colors (a
page's own inline CSS overrides it), images inline; every other format appears only in the
file list. Tables are markdown tables inside result.md — there is no bespoke table renderer.
Files are part of the execution record — deleted with it by retention, never required for
list rendering (loaded only when the execution is opened).

### 4.6 Statuses (single badge vocabulary, executions and steps)

queued (gray) · executing (cyan) · succeeded (green) · failed (red) · cancelled (gray) ·
skipped (gray) · interrupted (magenta) · none → "Not executed yet" (gray).

The same vocabulary applies to executions, steps, and step attempts. `skipped` on an
execution means the whole occurrence was skipped by the scheduler (§6); on a step it means
the user skipped that step mid-execution (§7). `queued` on an execution means a §6 firing-queue
entry waiting for a slot; on a step it means the step hasn't started yet. **An execution that
never reached `executing` never counts as the automation's latest** (§4.1 `lastStatus`,
`resultChip`) — `skipped` and `queued` both mean exactly that. A §6 queue entry that is
cancelled before its turn therefore finishes **`skipped`**, not `cancelled`, with the note
saying it was cancelled: it never ran, and a status is the only thing an index header row
carries to decide this by.

### 4.7 Agent

```
{ id: uuid, name, description, harness: Claude Code | Gemini CLI | Codex | OpenCode,
  mode: default | ollama | custom, model }
```
`description` is an optional free-text description ("What this agent is for — shown on the Agents
page and given to the drafting agent"), rendered as the detail line on the agent card and
carried into the §8 grants yaml so the drafting agent knows what each enabled agent is for.
`model` is null when `mode` is `default` and required otherwise. Mode `custom` is valid with
every harness: the user types the model as a free-text string and the app passes it verbatim
to the harness CLI as `--model <model>` (§6, §19); the string is never validated by the app —
a wrong name surfaces as a harness error at invoke time. Mode `ollama`: `model` names the
local Ollama model. Mode `ollama` is valid with **Claude Code, Codex, and OpenCode** — Ollama
is not a harness of its own; it is the single local-model runtime every local-model agent
drives, and each harness connects to it through that harness's own supported mechanism
(§6 invocation, §19 readiness): Claude Code through its custom-endpoint env vars against
Ollama's Anthropic-compatible API, Codex through its official `--oss --local-provider ollama`
flags, OpenCode through its provider config (`opencode run --model ollama/<model>`). Mode
`ollama` is **not** valid with Gemini CLI — the stock CLI speaks only the Gemini wire format
and has no local or OpenAI-compatible endpoint support (documented limitation; the backend
rejects it with 422 and the UI shows the option disabled with the reason). A null model means the app never picks or passes a model — the harness uses whatever
model it is already configured with. Display shows "Default model" when the model is null. One agent is
the app default: a single `default_agent` id pointer in `agents.yaml` (§5) — never a
per-record flag, so "exactly one default" holds structurally; the API serializes each
agent's derived `default` bool and its derived `usedBy` — the automations that use the
agent, as their drafting agent or via a current-version step's `agents:` entry ids (§4.1),
each entry `{ id, name }` (id is the automation's uuid — what the §12 chips navigate by;
name is display). Deleting the default agent repoints the pointer and warns
which automations use it.
**Grant-name uniqueness:** an agent's effective §8 grant name (`name`, falling back to the
harness name when unnamed) is unique across agents, compared case-insensitively. Agent
names store trimmed (surrounding whitespace stripped on write; a whitespace-only name is
an unnamed agent), so padding can't dodge the check. The API
rejects a create, and a rename that would change the effective grant name into a collision,
with 422 ("an agent named X already exists - agent names must be unique"); the §12 form
shows the same rule inline. Enforcement is write-time only - duplicates already on disk
still load. A stored entry without an `id` cannot be referenced and is skipped at load
with a warning, like any record that fails to resolve (§5 lenient load) — never healed,
never fatal. Steps bind agents by id (§4.1), so a rename never repoints a step; uniqueness
exists for the §8 grants yaml, the §20 case-insensitive name flags, and unambiguous display.
Import (§5.1) never creates an agent record: an archive reference either matches an
existing agent or lands unresolved (§4.1 `unresolvedReferences`).
All four harnesses are selectable. The app can install any of them (plus Ollama, for the
local-model mode) and help the user sign in when the harness needs an account (§10 step 2,
§19 install/login endpoints).

### 4.8 Secret

`{ id, name, description, set, usedBy }` — the value itself is never part of the entity (Keychain-only,
below). `id` is a uuid minted when the secret is created; a stored entry without one cannot be
referenced and is skipped at load with a warning, like any record that fails to resolve
(§5 lenient load) — never healed, never fatal. **The id is the
reference identity everywhere**: steps bind secrets by it (§4.1), discord triggers reference
it (§4.3), the §19 routes address it (`PUT`/`DELETE /secrets/{id}`; only `POST /secrets`
carries a name — creation), and the Keychain entry is keyed by it (the keyring account is
the secret's id, so the stored value survives nothing being named after it and never needs
touching on metadata edits). `usedBy` is the list of automations whose current version uses
the secret, each entry `{ id, name }` (the UI joins the names; empty list renders "Not used
yet"). Names uppercase, `[A-Z][A-Z0-9_]*` — sanitization (uppercase,
invalid chars → `_`) is UI input behavior; the backend validates strictly and rejects nonconforming
names with HTTP 422. Names are **unique** — enforced at create (§19 `POST /secrets` answers
422 on a name another secret already holds) — and **immutable** - no rename path exists
anywhere (`PUT /secrets/{id}` edits only value/description and carries no name field; the
§12 edit modal renders the name read-only). Uniqueness exists for the §8 grants yaml, the
§20 name flags, and unambiguous display — ids are the binding, names are the display
(same rule as §4.7 agents). `description` is an optional free-text description ("What this secret is for — shown
on the Secrets page and given to the drafting agent"), stored next to the name in `secrets.yaml`
(never in the Keychain) and carried into the §8 grants yaml so the drafting agent knows which
secret to use. Values are arbitrary strings and may be multi-line (e.g. a PEM key). Values stored
in macOS Keychain, masked at rest; the API never returns secret values — show/hide applies to the
value being typed in the add/edit modal, not to stored values (so the §12 edit modal shows a
set secret as a masked "kept" row with a Replace value action, never an empty value field). `set` is a backend-maintained
boolean in `secrets.yaml`: creating a secret (§19 `POST /secrets`) with a blank value creates
a **placeholder**
(`set: false`) — the name and description exist, no Keychain entry does; the Secrets page and
grant surfaces show an amber "Not set" tag until a value is saved. Writing any nonblank value
stores it in the Keychain and flips `set` to true; editing a secret (§19 `PUT /secrets/{id}`)
with a blank value
keeps the stored state (a set secret keeps its value, an unset one stays unset) and updates only
the description. An execution needing an unset secret fails before any step with "secret `NAME`
has no value yet — add it on the Secrets page" (same pre-step gate as a missing Keychain entry,
§7). Import (§5.1) never creates a secret record: an archive reference either matches an
existing secret or lands unresolved (§4.1 `unresolvedReferences`).
Step scripts reference them by id subscript with the name in a trailing comment
(`secrets["<id>"]  # NAME`, §6.1 — always a literal quoted id); values are injected at
runtime and redacted from logs. Redaction labels, error copy, and `redactedSecrets` stay
names — ids are the binding, names are the display. Because log lines are
redacted one at a time, each non-blank line of a multi-line value is redacted individually as well,
and the §6 agent-prompt scan likewise checks every non-blank line of a multi-line value, not just
the whole string. Deleting a secret in use warns: the automation "uses it and will stop
working."

### 4.9 Settings

```
login: bool        — "Launch at login" ("Autowright starts quietly in the menu bar.")
menuBarIcon: bool       — "Show in the menu bar" ("The quickest way to execute an automation.")
  Both are OS-side effects owned by the Electron shell, reconciled from these stored values —
  at startup and on the shell's periodic backend poll (so a tray-only app follows §20 CLI
  changes), plus a renderer push on every settings change: `login` registers/unregisters the
  OS login item through the §2 shell module's `applyLoginItem` seam — the macOS/Windows
  login item via Electron, on Linux a marker-carrying `.desktop` file reconciled in
  `~/.config/autostart/` (written on enable — rewritten when its Exec line drifts, e.g. a
  moved AppImage — deleted on disable; same never-touch-foreign-files ownership rules as
  the §3 CLI shim) — the default true registers on first launch. Reconcile rules, per OS
  by how the OS names the registration. Everywhere: an unpackaged (dev-harness) run never
  registers, because the OS would enroll the bare Electron dev binary as the login item
  rather than Autowright; and a packaged run asserts off unconditionally on every
  reconcile, never guarded by the OS's own reading (which can be stale), while on writes
  only when the OS view differs. On macOS the registration is named per-binary, so a dev
  run also asserts off — that can only ever clear a stale registration for its own
  binary. On Windows and Linux the registration lives under one shared Autowright-owned
  name (the HKCU Run value named by the §3 AUMID `ai.autowright.app`; the
  `ai.autowright.app.desktop` autostart file), so there a dev run must not touch the
  canonical registration in either direction — a dev off would delete the installed
  app's registration, the dev guard could never write it back, and the toggle would read
  on while nothing launches. A dev run's whole reconcile is self-cleanup: on Linux it
  deletes the marker-carrying autostart file only when its Exec line references the
  running dev binary (a pre-guard dev leftover); on Windows it runs only the legacy
  sweep. Windows legacy sweep (every run, packaged or dev, once per process, best-effort
  via reg.exe): builds before the AUMID let Electron name the Run value
  `electron.app.<name>` — slots nothing reconciles anymore, so a leftover keeps
  launching the app with the toggle off. The sweep deletes `electron.app.Autowright`
  outright (it can only ever be Autowright's own stale slot) and `electron.app.Electron`
  only when its command references the running binary (the generic name may belong to
  another app's dev shell). `menuBarIcon` creates or
  destroys the tray icon live (no restart; hiding it also hides an open §13 panel). The
  row renders only where the shell has a tray at all — the §9.4 platform-info bridge
  carries the shell's `trayPanel` capability, and on Linux (no tray surface, §13
  2026-09-01 decision) the row hides; the stored value stays for §20 CLI parity and is
  inert there.
keepAwake: bool (default true) — "Keep this Mac awake" ("Prevents this Mac from sleeping so
  schedules and message triggers keep firing. The display can still sleep. Works best on an
  always-on Mac like a Mac mini or Mac Studio. A MacBook that is asleep would not trigger
  the automation.") — while on, the backend holds a permanent idle-sleep power assertion (§3 sleep
  bullet); the trailing sleep disclaimer is the §9 table's per-OS `sleepNote` sentence, the
  one honest line about what the assertion cannot do (forced sleep, §3); applied live on
  settings change, no restart. Row sits in the GENERAL card below "Show in the menu bar".
  The row renders only while the §9 store's `capabilities.keepAwake` is true (§2 gating):
  the setting itself stays stored and CLI-visible everywhere, but the card never promises an
  assertion the OS can't hold.
automaticUpdateCheck: bool (default true) — "Check for updates automatically" ("Once a day,
  ask GitHub whether a newer version exists. Downloads still start only when you ask.")
  — on by default (PRIVACY.md names the daily check and its off switch; existing installs
  gain the key as true through the defaults merge). Turning it off restores strict
  manual-only checking. Stored here for §20 CLI parity; consumed by the Electron shell's §3
  automatic-check machinery through the same reconcile path as `login`/`menuBarIcon`. Its
  toggle row lives on the About page's UPDATES card (§9.4), not Settings.
notifications: attention | all — "Only when something needs attention" / "After every execution"
  — the row renders only while the §9 store's `capabilities.notifications` is true (§2
  gating); the stored value keeps §20 CLI parity everywhere.
days: int ≥ 1 (default 90) — history retention; keepForever: bool disables cleanup
developerMode: bool (default false) — "Developer mode" ("Logs every backend request and every AI
  request — including the full prompt — to the backend log. Press `` ` `` to show the logs
  panel.") — gates request logging, the per-request log files under `<logs>/requests/` (§5),
  the §5 build-failure records under `<logs>/build-failures/`, and the `` ` ``-key log
  overlay (§9.3)
cliEnabled: bool (default true) — whether the user wants the `autowright` command available;
  drives the COMMAND LINE card's toggle (below) and the §3 one-shot first-run install. On by
  default: fresh installs and pre-key upgrades resolve to true through the defaults merge,
  while any stored value is kept as written, including the materialized `false` that
  0.3.4/0.3.5 saves wrote for users who never touched the toggle (accepted: no migration - a
  standing pre-policy decision recorded in the §21.4 log). The §3 shim
  files on disk stay the truth about what's actually installed — this key only records the
  user's choice (stored backend-side like every setting, §20 CLI parity included)
dataPath (default ~/Library/Application Support/Autowright/executions), dataSize
imessageAutomation: granted | denied | unknown — hidden stored value, no settings row and no
  default entry (absent reads as unknown): the §19 remembered macOS Automation-permission
  state (Apple Events control of Messages). macOS offers no prompt-free read, so the backend
  keeps the result of its most recent Messages send / §19 automation probe, updated whenever
  the observed state changes (best-effort — an unwritable store logs and keeps the value in
  memory only, per the §5 read-only degradation).
appPath — derived, serialization-only: the fixed automations-and-settings root
  (~/Library/Application Support/Autowright) — backs the ON THIS MAC card's
  "Automations & settings" Show in Finder button (below)
```
Show in Finder (everywhere it appears) opens the target directory itself in Finder when the
path is an existing directory (e.g. Execution data opens the executions dir, not its parent), and
falls back to selecting the item in its parent folder otherwise.
Execution-data section: Change then Show in Finder; Change opens the native macOS folder picker and the chosen
directory simply becomes the execution-data location — no move/cancel UI and no data migration: all
execution state lives inside the executions dir, so changing the path just points Autowright at
the new location (the old dir stays where it was).
The "Keep executions for" days row is hidden (not just disabled) while "Keep execution history forever" is
on. One **ON THIS MAC** card holds two rows: **"Automations & settings"** (the fixed path
`~/Library/Application Support/Autowright` with its own Show in Finder button — this location
is not changeable) above the **Execution data** row. A **COMMAND LINE** card sits below ON THIS
MAC: one row titled "The `autowright` command" with a **Toggle** bound to the stored
`cliEnabled` setting (above; default true, installed by the §3 one-shot first-run install:
user-local only, no password anywhere). Disk state still comes from the §3 `cli-status` preload IPC (`{state,
path, onPath}`, re-read on every Settings visit — the shim files are the truth about what's
installed; the setting only records the user's choice). Turning the toggle **on** patches
`cliEnabled: true` and fires §3 `cli-install` (silent ~/.local/bin write, no dialog); a
failed install patches the setting back to false — the toggle just returns, never an error
banner. Any successful card install (toggle-on or the Reinstall button) also sets the §3
`ad-cli-installed` first-run marker, so a later hand-deletion is never undone by the
launch-time one-shot. Turning it **off** deletes the command too: when an ours-marker shim is
on disk (`installed`) the flip first opens a danger ConfirmModal — title "Turn off
the `autowright` command?", body "This also deletes the command file from this Mac. Your
automations, settings, and executions aren't affected.", confirm label "Turn off and
delete" — and only confirming patches false and fires the §3 `cli-uninstall` IPC (a
failed delete comes back as a toasted error message;
the setting still turns off). Cancel leaves the toggle on and touches nothing. With no shim
on disk (`missing`) the flip just patches false — no modal, nothing to delete. Detail line +
extra action by
setting × disk state: on+`installed` → "Installed at `<path>`" (`onPath` no longer affects
the card — the PATH help lives in the PATH row below, shown for every on+`installed`); on+`missing` (the user deleted the file by hand) → "Not
installed — manage automations from the Terminal."; off+`installed` → "Still installed at `<path>` — turn on to keep it up to
date."; off+`missing` → "Not installed — manage automations from the Terminal. Turning
this on installs to ~/.local/bin — no password needed."; `foreign` (either setting) → "A
different `autowright` is already at `<path>` — Autowright won't touch it.", no toggle, no
buttons. There is no standalone Delete button — removal rides the disable confirm above
(an off+`installed` leftover, possible after a failed uninstall, is removed by turning the
toggle on and off again). The card can grow **one
second row** below the toggle row, separated by the standard hairline divider — exactly one
of:
- **Missing-warning row** — toggle on but no working user-local install (on+`missing`):
  amber title "The `autowright` CLI is missing", description "autowright wasn't found in
  ~/.local/bin — it may have been deleted or moved. Reinstall it to keep using it from the
  Terminal.", with a "Reinstall" button firing §3 `cli-install` (silent, ~/.local/bin — once
  the §3 first-run marker is set the app never re-creates on its own; the row is the explicit
  ask). The row-1 Install button is gone — reinstall lives only here.
- **PATH row** — toggle on and the shim installed (on+`installed`, regardless of `onPath`):
  title "Add it to your PATH", description "If your Terminal can't find autowright, add
  ~/.local/bin to your PATH:", followed by the **PATH command block** — the §14
  `CommandBlock` primitive holding the exact command
  `echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zprofile && source ~/.zprofile`
  (appends to `~/.zprofile` so the change persists — the login-shell init macOS Terminal
  reads, matching the §3 login-PATH probe) with its "Copy" button that writes the command to
  the clipboard and toasts "Copied to clipboard." The command wraps instead of truncating —
  it must stay fully readable at any card width.
Reinstall (and the uninstall behind the disable confirm) show the §9 busy-commit spinner
while running, then the card re-reads `cli-status`. A **DEVELOPER**
card sits last on the page with
the single **Developer mode** toggle row (developerMode above). A **QUIT** card sits below
DEVELOPER, rendered only when the preload bridge exists (like
COMMAND LINE; no stored setting). One row titled "Quit Autowright entirely", detail "Stops the
background service too — schedules and message triggers pause until you next log in or open
Autowright.", with a "Quit…" button (ellipsis: a confirm follows). The button opens a danger
ConfirmModal — title "Quit Autowright entirely?", body restating the pause-until-next-launch
consequence, confirm label "Quit Autowright". Confirming raises the **quit overlay** (the
same §14 `BlockingOverlay` as RESET below: full-window, portalled above every surface, no
user dismissal path) holding the §9 busy spinner, the title "Quitting Autowright…", and the
muted line "Stopping everything…" (static — quit is a single stop step, no stage pushes),
then fires the §3 `quit-all` IPC. Busy (live execution, only possible without `force`) →
the overlay drops and a second danger ConfirmModal opens — the **force-confirm modal**,
title "An automation is executing", body "Shut down everything and quit? The running
automation will be killed.", confirm label "Shut down and quit" — whose confirm re-raises
the overlay and re-fires `quit-all` with `force: true` (skips the gate; §3 quit-entirely).
Error → the overlay drops and the error text toasts. Success → the overlay stays up until
the app exits (backend stopped and strays swept first — §3 explicit-quit exception; after
it, the app bundle can be deleted immediately). The stop typically takes a few seconds and
is bounded at ~20 s (deregistration wait plus the stray-process sweep); the overlay blocks
every interaction for the whole run.

A **RESET** card sits at the very bottom of the page (below QUIT), rendered only when the
preload bridge exists (like QUIT; no stored setting), gated on no live executions through
its §3 IPC (busy → toast "An automation is executing — reset when it finishes." and the row
resets; unlike QUIT there is no force path), and showing the §9 busy spinner on
the row button while its flow runs: one row titled "Delete all data and quit app", detail "Erases every
automation, execution, agent, secret, and setting from this Mac, then Autowright quits.
The next launch starts as new.", with a "Reset…" button (ellipsis: a confirm follows). The button opens a danger
ConfirmModal — title "Delete all data and quit app?", body "Every automation, execution,
agent, and setting on this Mac is deleted, and every secret is removed from your Keychain.
Autowright then quits, and the next launch starts as if newly installed. This can't be
undone.", confirm label
"Delete everything". Confirming fires the §3 `reset-all` IPC and immediately raises the
**reset progress overlay** (`BlockingOverlay` in `ui.tsx`, §14): a full-window blocking
surface — the `Modal` backdrop + card, portalled to body above every other surface
(z-index 120) — holding the §9 busy spinner, the title "Deleting all data…", and a muted
stage line driven by the §3 `reset-progress` pushes: "Preparing…" until the first push,
then `secrets` → "Removing secrets…", `service` → "Stopping the background service…",
`data` → "Deleting data…", `quit` → "Quitting…". The overlay is deliberately
non-dismissable — no close button, no backdrop-click, no Escape (it is not the shared
`Modal`, which bakes both in) — so nothing in the window is reachable while data is being
erased; it still enters and exits on the §14 fade tokens like any two-way surface. The
row button reads "Resetting…" (§9 busy spinner) beneath it. Then, per the IPC result:
busy (live execution) → the overlay closes, toast "An automation is executing — reset
when it finishes." and the row resets; error → overlay closes, toast the error text, row
resets; success → the overlay stays up until the app quits (§3 reset flow: no relaunch,
and the next launch runs §10 onboarding as on a fresh install; the service registration,
the CLI shim, and the app itself deliberately survive; only data is erased). There is no
in-app uninstall: removing the app itself is the OS's (or Homebrew's `zap`) territory.

Version, updates,
GitHub links, licenses, and the disclaimer live on the About page (§9.4), not here.

