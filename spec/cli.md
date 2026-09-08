# Autowright SPEC — CLI

Part of the Autowright spec. Index and § map: [SPEC.md](../SPEC.md). § numbers are global across spec files.

## 20. CLI (decided)

> **Status: enabled.** The full surface is registered (`cli.CLI_ENABLED = True`). The condition
> for enabling was settling the grant model, and it is settled — see **Grant model** below: no
> all-on secret seed on `create`, no silent grant widening on `push`; every grant is an explicit
> repeatable flag on the command line. This is the enforcement the disabled era lacked: an agent
> driving the CLI cannot acquire a secret without the grant being visible in the very command it
> runs, and the §17 skill requires showing that command — grant flags included — in the pre-save
> summary. Headless mode (§3) un-parks with it.

`autowright` (`backend/autowright/cli.py`) is the second client of the §19 API — full UI parity
for headless and agent-driven use. It ships only inside the app bundle (§3 CLI-on-PATH shim;
never pip). Design goals: every UI operation reachable, output parseable by an AI agent, and
automation authoring done through **real files on disk** so an agent edits spec/steps the same
way it edits any code. The §17 `skills/autowright/` agent skill is the primary consumer.

**Command structure** — noun-verb groups (full words per the §2 naming rule):

```
autowright status                       backend health, version, entity counts
autowright instructions                 §8 framework + build instruction files, verbatim
autowright automation <verb> …          list · show · pull · push · create · delete · restore ·
                                        execute [--version vN|draft] [--queue] · export · import ·
                                        param list|set · trigger list|add|on|off|remove ·
                                        memory show|clear · snapshot list|create|restore|delete
autowright execution <verb> …           list · show · tail · cancel · retry · skip · result
autowright secret list|set|delete
autowright agent list|check
autowright settings show|set
autowright service install|uninstall|status|restart|stop   (§3 — the only group that needs no backend)
```

The `service` group is a thin wrapper over `service.py` — the same functions the app's
ensure-backend step runs via `python -m autowright.service` (§3). Dependency direction is
one-way: the CLI calls the backend API and the service module; **the UI and the backend never
invoke the CLI** (§3) — the app installs the CLI shim but never executes it.

- **References:** automations resolve by id, unique id prefix, exact name (case-insensitive),
  or unique name substring; executions and snapshots by id prefix. Every short id the CLI
  prints (the 8-character `[abcd1234]` forms in list/create/ambiguity output) must therefore
  resolve when passed back. Ambiguity or no match exits with the candidate list.
- **`--json`** on every read verb (`status`, `instructions`, `automation list|show`,
  `param list`, `trigger list`, `memory show`, `snapshot list`, `execution list|show`,
  `secret list`, `agent list`, `settings show`) prints the raw API JSON instead of the human
  columns — the machine mode agents parse.
- **Help text:** `--help` is the CLI's own documentation — the §17 skill's agent discovers the
  surface by reading it, not by reading this spec. Every parser in the tree carries a
  `description` (the prose `<command> --help` prints, distinct from the one-line `help` its
  parent's command list shows), and every positional and option carries a `help` naming what it
  accepts, with a `metavar` wherever the placeholder name isn't self-describing. Fixed
  vocabularies are **enumerated in the help itself**, never referenced by § number:
  `execution list --status` registers the §4.6 statuses plus the §19 `finished` group value
  (any terminal status) as argparse `choices` and repeats (each sent as one §19 `status`
  value - rows in any of them match; an unlisted value
  is a usage error rather than a silently empty list — the §19 filter is an equality match, so
  nothing outside the vocabulary could ever have matched); `--automation` repeats (each
  resolved by the usual reference rules and sent as one §19 `automation` value), and
  `--since` / `--until` take a local `YYYY-MM-DD` or `YYYY-MM-DDTHH:MM` (`--until` with a
  bare date means the end of that day) and ride as the inclusive §19
  `startedFromMs` / `startedToMs` - a value in neither form is a usage error; `-n` rides to the server as the §19
  `limit`, so only the printed rows cross the wire, while reference resolution still reads
  the uncapped list — §19), `settings set` lists its keys with
  each one's value form, and `param set` lists the per-kind value forms. Every `<automation>`
  positional carries the compact reference forms from one shared helper (name · part of a name ·
  id · id prefix), and the full rule — case-insensitivity, and that every `[abcd1234]` the CLI
  prints resolves back — is stated once, in the description of each group whose verbs take one,
  rather than repeated down a listing. Every optional `<execution>` positional says that
  omitting it means the latest execution.
  Commands whose input shape isn't obvious from the flags alone — `automation
  pull|push|create|execute|export|import`, `param set`, `trigger add`, `execution list|result`,
  `secret set|delete`, `settings set`, `service` — carry an `epilog` of concrete example command
  lines. Only commands carry one: the parsers that hold subcommands (the top level and every
  group) show their listing and stop, so nothing repeats above a listing the reader hasn't
  reached yet. Help prose
  is written for whoever is at the terminal: no § references, and a flag's stated accepted values
  are exactly the values the command parses. `Help` (an `argparse.HelpFormatter` subclass) wraps
  descriptions the ordinary way while leaving those example blocks line-for-line as written —
  argparse's own raw formatter is all-or-nothing and would leave the prose unwrapped too, and
  `_fill_text` is the single hook both run through, so the `Examples:` opener every example
  epilog carries is what tells them apart. Descriptions are written as **paragraphs**: the
  first one says what the command does and is what listings print, the ones after it carry the
  detail, caveats, and what happens next. `_fill_text` wraps each paragraph separately rather
  than collapsing them into one block.
- **A bare invocation prints help:** `autowright` with no command prints the full top-level
  help — the same text `--help` prints, expanded listing included — and exits **0**. It is not a
  usage error: typing the bare command is how someone finds out what the CLI does, and answering
  with a one-line usage stub wastes the one moment they asked. The same holds at every level, so
  `autowright automation` prints the automation listing and `autowright automation trigger` the
  trigger one. This is why no `add_subparsers` in the tree is `required`: a parser with no
  command of its own defaults `fn` to printing its own help, with `client` false so a bare
  invocation never needs a reachable backend (`autowright` must answer on a machine whose
  backend is down — that is when someone is most likely to type it). Everything else stays a
  usage error exiting 1: an unknown command, an unknown flag, a missing **positional**, a bad
  choice.
- **Expanded command listings:** argparse's stock listing prints a bare name and one-liner per
  subcommand, so the flags of a command are reachable only by running its own `--help`. `Parser`
  overrides `format_help` to replace that listing with an expanded one, generated from the parser
  tree so it can never drift from what the CLI actually accepts. Each entry under the
  `COMMANDS:` / `VERBS:` heading (the subparsers `metavar`, pluralized) is a **signature line** —
  the command name followed by every positional in `<angle brackets>` (`[optional]`, `...` when
  repeatable) and every flag in `[square brackets]` with its value placeholder — then the first
  paragraph of its description, then a blank line, then a labelled block of one indented row per
  positional and flag carrying that argument's own help. The label is `arguments:` under a
  command and `verbs:` under a group, and the rows indent two past it. Prose and rows must not
  run together at the same indent: the blank line and the label are what stop a listing reading
  as one undifferentiated column, and the label's wording is the second half of that — it says
  which kind of block this is without the reader inferring it from the shape of the rows.
  A subcommand that is itself a group lists its verb names with their
  one-liners instead of expanding them, which bounds the depth: any `--help` prints its own level
  in full and names the level below. The heading closes with a line pointing at
  `<prog> <command> --help` for the full description and examples. The generator is the single
  source of these listings — a new command is documented by giving its parser a description and
  its arguments help, never by editing a listing by hand.
  A parser holding subcommands ends there: `format_help` drops the help action along with the
  subparsers action, which leaves argparse's `options:` section holding nothing and therefore
  omitted. `-h` is not news to anyone who just typed a command to find out what it does, and a
  section listing only itself is the kind of noise that teaches people to stop reading help. A
  command's own `--help` still shows its options as usual, because there the section carries
  the command's real flags.
- **Needs fixing parity (§4.1 `problems`):** `automation list`'s human rows carry a plain
  `needs fixing` marker, after the status column and immediately before the result chip,
  when an automation's `problems` list is non-empty, and
  `automation show` prints a `needs fixing:` block — one indented line per problem label,
  in §4.1 order. `--json` carries the serialized `problems` field as always.
- **Memory inspection** (`automation memory show <ref> [file]`) — the authoring surface's
  only read access to §6 memory contents (§8 drafting calls never carry them). With no
  `file`, lists the memory directory's files — memory-relative path, size, updated — via §19
  `GET .../memory/files` ("memory is empty" when none); with a `file` argument (a listed
  relative path), prints that file's text verbatim via §19 `GET .../memory/files/{name}`.
  Read-only and lock-free — §6 atomic commit means a read never sees a partial file. A
  non-UTF-8 (binary) file is not printed: the 422 message points at the memory directory
  on disk instead.
- **Trigger message parity (§7):** `execution show`'s human output prints a
  `trigger message:` line when the record carries the §4.5 `triggerPayload` — the same input
  the UI's TRIGGER MESSAGE block shows. Kind-aware, like the UI: Discord prints
  sender · `#channelName · guildName` (raw channel id when the names are null, guild part
  omitted when null) · time; iMessage prints sender · time (an iMessage payload has no
  channel — it must not KeyError on one). Then the text, indented. The `secret` is never
  printed. `--json` carries the raw payload (and list rows the §4.5 `triggerSender`) as always.
- **Exit codes:** 0 success · 1 any error (connection, HTTP, validation, bad reference,
  a file the command cannot read or write —
  message on stderr, §3 guidance style, never a traceback; an HTTP error prints the API's
  `detail` message, never the raw JSON body, and a list-shaped validation `detail` (the
  pydantic 422 form) prints as the first error's field path and message) · 2 from `automation execute -f`,
  `execution retry -f`, and `execution tail` when the followed execution ends in any
  terminal status other than
  `succeeded` — so a harness can branch on the exit code without parsing prose.
  **2 is exclusively that follow-failure signal**, so nothing else may return it: a usage
  error (unknown command, missing argument, bad flag) exits **1** with the usage message on
  stderr, overriding argparse's own default of 2. Interrupting a follow with Ctrl-C is an
  error too, not a crash: it exits 1 after a plain `interrupted` line, never a
  `KeyboardInterrupt` traceback.
- **HTTP timeouts:** every backend request runs with a 30 s timeout, except the three calls
  that legitimately take long — package install (the §6.2 ensure runs pip), URL import (a
  remote download rides the request), and automation delete (§19 waits for cancelled engine
  threads) — which get 600 s.
- **Follow semantics** (`execution tail`, `automation execute -f`): a `queued` record (§6
  firing queue) is not terminal — the follow loop keeps polling while the execution is
  `executing` **or** `queued`, so a followed queued firing is watched through promotion to
  its real end (or its `skipped` settle), never reported the moment it is admitted.

**Workdir (the authoring format).** `automation pull <ref> [dir]` materializes an automation
into a directory (dir defaults to the automation's name); `automation push <ref> <dir>
[--note] [--grant-agent NAME]…
[--grant-secret NAME]…` validates it and saves vN+1; `automation create <dir> [--name]
[--agent] [--grant-agent NAME]… [--grant-secret NAME]…` validates and creates v1 — push and
create take the workdir as a required positional; only pull's is optional. Files:

- `spec.md` — the spec as markdown (§4.1 blocks ↔ markdown via `specmd`).
- `manifest.yaml` — the §8 call-2 manifest shape verbatim, plus the identity fields the §8
  sync manifest never carries: `name` and `description` are written by pull and **applied
  by push** — a changed `name` rides the version save (the §19 name patch; the §4.1
  uniqueness 422 applies) and a changed `description` lands via the §19 PATCH right after
  the save, so a manifest edit takes effect like any other workdir edit. An absent or
  blank value leaves the stored one unchanged (clearing a description is the §19 PATCH's
  affair, never push's). The remaining keys: `note`, `triggers`
  (the §8 rule-9 dialect — cron / imessage / discord / app_start entries; `pull` writes the
  stored crons only), `params` (full §4.2 definitions with
  defaults, **value fields stripped** — values are user-owned operational state, set via
  `param set`, never round-tripped through versions), `packages`, `steps` (file, name, description,
  `agent`/`why`/`agents`, `secrets`, `packages`, `timeout`/`no_timeout`,
  `retries`/`infinite_retries`: the per-step §4.1 keys, snake_case where the API spells
  them camelCase, written by `pull` and accepted by `push`).
- `NN-name.py` — one file per step, matching `steps[].file`.
- `instructions.md` — the version's build instructions (`instructions`), when present.
- `notes.md` — the version's §4.1 notes document, when nonempty; push saves it verbatim.

`pull` owns the workdir's managed files: it rewrites every file above and removes the
managed files it did not write this time — a `notes.md` or `instructions.md` the version no
longer carries, and any `NN-name.py` not in the manifest — so a re-pull into the same
directory never resurrects deleted content on the next push. Files it doesn't manage (a
README, a virtualenv) are left alone.

Push/create run the **same §8 validators the drafting pipeline uses**
(`drafting.validate_spec` + `validate_steps` — schema, param kinds/defaults, step-file 1:1 and
ordering, `ast.parse`, the §6.2 import allowlist, timeout rules, trigger dialect). Validation
errors print one per line and exit 1 with nothing written — agents iterate on the files until
clean. This pre-save pass exists only for the friendlier errors: the backend runs the same
validators server-side on every `POST /automations` / `POST .../versions` (§19) and rejects
an invalid draft with 422, so the enforcement never depends on the client. Existence context for the validators is all configured agents plus all stored secrets,
so a manifest naming an unknown agent/secret fails with the §8 message; which of the *known*
names the automation may actually use is the grant model's job, below.

**Declared packages install at save.** After a successful `create`, `push`, or `import`, the
CLI runs the §6.2 ensure (`POST /packages/install`) for the saved version's declared packages
and prints one line per package — `package <pip> <version> installed`, or a
`warning: package <pip> failed to install — <error>` line on failure. A failure warns and
exits 0: the save already landed, and the §8 rule applies — an install failure never fails a
build; the engine's pre-execution ensure (§7) retries it before anything runs. No declared
packages → nothing printed. This mirrors the §8 drafting pipeline's install stage: the user
learns about an install failure at build time, not when a trigger fires.

**Grant model (settled).** Grants are explicit on the CLI — no all-on seed, no silent widening:

- `automation create` saves `stepAgents`/`allowedSecrets` as exactly the grants passed via the
  repeatable `--grant-agent NAME` / `--grant-secret NAME` flags. Each flag must name a
  configured agent / stored secret; an unknown name exits 1 with the candidate list.
  Both flags take **names** — the human-friendly surface — and the CLI maps each to its id on
  save: `--grant-agent` the agent's §8 grant name (case-insensitive — safe, §4.7 uniqueness),
  `--grant-secret` the secret's exact name (§4.8: canonical uppercase, unique); `stepAgents`
  stores agent ids and `allowedSecrets` stores secret ids (§4.1 — the enablement lists the
  engine and UI resolve by id).
- `automation push` saves the automation's stored lists plus any `--grant-agent`/
  `--grant-secret` flags. Stored grants never shrink on push (they are user-owned state, like
  param values); the UI edit page remains the place to revoke.
- After validation, the CLI computes the ids the workdir actually needs — per-step `agents`
  entry ids,
  per-step `secrets` entry ids, plus the code-referenced `secretReferences` (§8 — ids) — and
  any needed id outside the
  saved grants exits 1 listing the exact flags to add (ids mapped back to their names for
  the flag suggestions), nothing written. The needed-vs-granted comparison is id-set
  against id-set — names appear only in the printed flags. An `agent: true` step with no `agents:` list runs on the first
  enabled agent (§6), so it needs at least one granted agent: when none is granted, the save
  exits 1 asking for a `--grant-agent` flag with the configured-agent candidates. A
  granted-but-unused name is fine (a deliberate pre-grant).
- Why this shape: the grant for a secret must appear in the command line the user (or the
  agent the user is watching) runs — never implied by the workdir's content alone, which an
  agent authors. The §17 skill's pre-save summary must show the full command including grant
  flags. `execute` runs the stored version under the stored grants by default;
  `execute --version <vN|draft>` selects an old version or the draft for that one run
  (forwarded as the §19 execute body's `version` field) — the grants stay the stored ones.
  `execute --queue` forwards the §19 `queue: true` field: with every slot busy the start
  joins the §6 queue instead of failing ("queued — execution `<id>` (waiting for a free
  slot)"; `-f` follows through promotion per the follow semantics above). Without the flag
  a busy automation stays a plain refusal (409).

**`automation import`** takes a `.autowright` file path or an HTTPS URL (§5.2 rules — a
direct `*.autowright` link on any host, or a `github.com` repo/release page resolved to its
archive asset). A URL goes through §19 `POST /automations/import/url` and confirms
immediately — the typed command is the user's explicit action, so no interactive preview;
when GitHub resolution changed the URL, the resolved source is printed. A file path POSTs
`/automations/import` unchanged. Both paths print the same summary lines, in order: a
summary carrying `renamedFrom` (§5.1 name dedupe) prints
`renamed from "<renamedFrom>" - that name already exists`; one carrying `osMismatch`
(§5.1) prints `built on <OS> - its steps may need rewriting on this machine`; then
`secrets matched:` and `agents matched:` (each §19 match as its archive name, with
` -> <matchedTo>` appended only when the local name differs; a matched agent whose harness
isn't ready - summary `ready: false`, §19 - is marked "(needs setup)"); then
`no match on this machine: secret MAIL_PASS, agent Coder` for the summary's `unresolved`
list. After those, the CLI runs
the package ensure (the §19 import already started the same ensure in the background,
§5.1 - the foreground run is idempotent and serializes on the same pip lock, so it either
shows the install progress or returns immediately). When `unresolved` is non-empty the
CLI closes with `this automation needs attention - open it and fix the highlighted agents
and secrets` before the triggers-off line. `<OS>` is the §4.1 `os-mismatch` display name (macOS / Windows / Linux;
an unrecognized token shows verbatim), never the raw §5.1 lowercase platform token: the CLI
and the UI name a platform the same way. Every CLI surface that prints a platform follows
this rule.

**Trigger semantics on push** — the §4.3 trigger merge, performed client-side exactly
like the editor: the manifest's cron entries are matched against the stored list on
(`expression`, `timezone`) — matches keep their `id`, `enabled` state, and `source`, and
take the manifest entry's `run_if_missed` (absent = true, §4.3), new
entries arrive enabled with `source: spec`, stored
**spec-sourced** crons the manifest no longer lists are dropped (`source: user` crons
always survive, §4.3); the manifest's `discord`/`imessage`/
`app_start` entries add only when no stored trigger matches their §4.3 identity fields; and
stored non-cron triggers always survive untouched. `pull` writes the stored crons into the
manifest (`cron`, `timezone` when set, `run_if_missed: false` when off), so an untouched
manifest round-trips the schedule unchanged. Between pushes, `trigger add` (cron by default,
`--at` for a §4.3 one-shot — both take `--timezone <zone>`, an IANA zone, stored on the
entry, and `--no-run-if-missed`, storing §4.3 `runIfMissed: false`; the flag with any other
trigger kind exits 1 - `--app-start`, `--discord <channel> --secret <name>
[--pattern <text>] [--mention] [--author <user-id>[,<user-id>…]]…` for a §4.3 discord
trigger (`--secret` takes the secret's **name** — the human surface, like the grant
flags — and the CLI maps it to the stored secret's §4.8 id, which is what the trigger
stores; an unknown name exits 1 with the candidate list) (`--author` repeats and each value may be comma-separated — all ids collect into
the trigger's one `author` list), or `--imessage <from>
[--pattern <text>]` for a §4.3 imessage trigger), `trigger on|off <n>`, and
`trigger remove <n>`
edit the stored list directly (1-based indexes as printed by `trigger list`, whose
empty-list line names the §13 surface per the §9 per-OS copy rule — "… or the menu bar"
on macOS, "… or the tray" on Windows, and no surface clause on Linux — and which appends
" (no catch-up)" to a row whose `runIfMissed` is false, after the " (off)" marker when both
apply) via the §19
PATCH; a cron minted by `trigger add` lands `source: user` (§4.3 — user-minted crons
survive later syncs and pushes).

**Param values** — `param set <ref> NAME=VALUE …` PATCHes `paramValues`, parsed by the
definition's kind: toggle `on|off|true|false` → bool · number → int · text → string · list →
JSON array or comma-separated · kv → JSON object or `k=v,k=v`. An unknown name or unparseable
value exits 1 naming the expected form.

**Secret values never ride argv** — `secret set NAME` takes no value argument: it prompts
(`getpass`, no echo), or reads the **whole** of stdin with `--stdin` for scripted use (a multi-line value such as
a PEM key lands intact; only the trailing newline is trimmed). A value
passed as an argument would land in shell history and in every local process's view of the
process list - the kind of secret-value exposure Autowright otherwise keeps out of scripts,
logs, and argv (§1 core promise, §4.8).
Names are the CLI's secret surface; the CLI maps them to the §19 id routes: `secret set`
with a name no stored secret holds creates via `POST /secrets`, with an existing name edits
via `PUT /secrets/{id}` (the upsert feel is CLI-side; the API itself is split, §19).
`secret delete NAME` resolves the name to its id (unknown name exits 1 with the candidates)
and calls `DELETE /secrets/{id}`. `secret delete --all` (mutually exclusive with a name;
requires `--yes`, the destructive-guard rule below) calls `DELETE /secrets` (§19) and prints
the deleted count — with `service stop` and deleting the §5 roots by hand, it is the
headless half of the §3 reset flow.

**Destructive guard** — `automation delete` and `secret delete --all` require `--yes` (the
first removes every version and its history; the second removes every stored secret and its
Keychain value); everything else is either reversible (versions, snapshots) or already the
§19 single-call semantics the UI uses.

**Review semantics in CLI terms:** a CLI save or
execute is user-reviewed by definition — the user (or the agent the user is driving) authored
the exact files being saved; there is no unreviewed AI output between the workdir and the
version. The §17 skill's instructions still require presenting a summary before
`create`/`push` + `execute` — including the full command with its `--grant-*` flags, per the
grant model above.

**`instructions`** prints the §8 `framework-instructions.md` (and `--json` both files) so an
agent authoring step code reads the engine contract — the step SDK, curated imports, policy
sections — from the same canonical file the drafting pipeline sends to harness agents.

Old flat commands (`autowright list`, `execute`, `executions`, `tail`, `secrets`, `agents`,
`export`, `import`) are replaced by the groups above — no aliases, no back-compat (the CLI
command surface is out of the §21 compat scope, §21.3).
`service` keeps its §3 verbs unchanged.

**Deferred (not yet in the CLI):** agent add/edit/delete/install/login (interactive TUI flows,
§19), Ollama management, settings `dataPath` picker parity beyond `settings set dataPath=`,
the §8 drafting jobs (`POST /drafts` — the skill's agent drafts directly instead), and §11
test executions (`POST /tests`).
