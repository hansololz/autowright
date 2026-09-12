---
name: autowright
description: >
  Drive Autowright — the desktop app for recurring personal automations — end-to-end from the
  command line: create and edit automations as real files (pull/push workdirs), execute and
  follow them, and manage executions, parameters, triggers, secrets, agents, and settings.
---

# Autowright

Autowright runs the user's personal automations on a schedule, entirely on this computer. Each
automation is: a **spec** (plain-markdown description of what it does), ordered **step
scripts** (Python, executed by Autowright's engine), **parameters** (user-editable values),
**triggers** (cron / interval / one-shot / on-app-start), per-automation **memory** (files kept between
executions), and versioned history. You interact through the `autowright` CLI — every
operation the app's UI offers is available there.

## Ground rules

1. **Read the contract first.** Before writing or editing any step code, run
   `autowright instructions` and follow it — it defines the step SDK (`autowright` module:
   `secrets["<id>"]`, `params`, `memory`/`workspace` dirs, `result.chip()`, `agent.ask()` /
   `agents["<id>"]`), the
   allowed imports (stdlib + curated packages + declared packages), and the engine policies.
   Do not guess the SDK from memory. Every SDK name a step uses must be imported
   (`from autowright import params, log, result`) — nothing is a global.
2. **Review before it runs.** Before `automation create`, `automation push`, or the first
   `automation execute` after a change, show the user a short summary of what you built or
   changed (spec gist, steps, triggers, secrets used) **and the exact command you will run,
   including every `--grant-agent`/`--grant-secret` flag**, and get their go-ahead. Grants
   are explicit (spec §20): create grants nothing by default, push never widens stored grants
   on its own — a needed-but-ungranted name makes the command exit 1 naming the flags to add.
   Never add a grant flag the user hasn't seen.
3. **Never invent secret values.** `secret set NAME` prompts, or ask the user for the value.
   Reference them in code as `secrets["<id>"]  # NAME` — the id comes from
   `autowright secret list --json`, always a literal quoted string with the name in a
   trailing comment; never hardcode credentials.
4. **Destructive actions** (`automation delete --yes`, `secret delete`, `snapshot delete`,
   `memory clear`) only on the user's explicit request.
5. If the backend is unreachable, the CLI's error message says how to start it
   (`autowright service install` / `service restart`). Don't debug beyond that message —
   relay it.
6. **If `autowright` isn't found on PATH**, the CLI is disabled or not installed. Stop and
   ask the user to enable it — never talk to the backend's HTTP API directly, and never
   install or move files yourself. Tell them: open **Autowright → Settings → COMMAND LINE**
   and turn on "The `autowright` command" (installs to `~/.local/bin` on macOS and Linux,
   `%LOCALAPPDATA%\Autowright\bin` on Windows; no password needed).
   If the toggle is already on, the card shows either a **Reinstall** button (the command
   file is missing) or an **Add it to your PATH** row with a copyable command — the card
   shows the exact command for this machine, so have the user copy it from there rather
   than guessing one, then open a fresh shell and retry.

## Orientation

```
autowright status                      # backend up? how many automations/executions?
autowright automation list             # names, schedules, last status
autowright automation show <name>      # spec, steps, triggers, params, history
autowright automation show <name> --json   # full record, machine-readable
```

Every read verb takes `--json`. Automations are addressed by name (case-insensitive,
unique substring works) or id (a unique prefix works — the 8-char ids the CLI prints
resolve); executions and snapshots by id prefix.

## Creating an automation

1. `autowright instructions` — read the framework contract.
2. Make a workdir and author the files:
   - `spec.md` — markdown, `# Title` first, plain words describing behavior, schedule,
     parameters. The spec is the source of truth the user reads.
   - `manifest.yaml`:
     ```yaml
     name: Manga updates
     description: Checks followed manga for new chapters
     triggers:                    # omit if the automation needs no trigger
       - cron: "0 8 * * *"        # optional timezone: Asia/Tokyo
       - every: "PT6H"            # an interval, measured from the automation's last run
       # also: { imessage: "+15551234567" } / { discord: "<channel>", secret: <secret uuid> }
       # (details from the spec only; the discord secret is the token secret's id from
       # `autowright secret list --json`; optional pattern) / app_start: true
       # one-shot `time` triggers: never here — use `trigger add --at`
     params:                      # each with a default; kinds: toggle|list|kv|number|text
       - { name: sources, kind: list, label: Manga URLs, help: One URL per line,
           validate: true, default: [] }
     packages: []                 # beyond-curated pip packages: {pip, import} — see below
     steps:                       # files NN-name.py, two-digit, gapless order
       - { file: 01-fetch.py, name: Fetch pages, description: Download each source,
           timeout: 60 }          # secrets: [{ id: <secret uuid>, why: one line }] when used
       - { file: 02-report.py, name: Write report, description: Diff against memory, timeout: 60 }
     ```
   - `NN-name.py` — one file per step. Steps small and single-purpose; deterministic code
     over AI; fail loudly naming what was expected vs found. An `agent: true` step (requires
     `why`, optional `agents: [{ id: <agent uuid>, why? }]` — ids from
     `autowright agent list --json`) may call `agent.ask()` for judgment; `agents["<id>"]`
     addresses a specific declared entry. Step manifests and code reference agents and
     secrets by their uuids — the grant FLAGS still take names.
   - **Dependencies:** any PyPI package beyond the curated list is fine — declare it under
     `packages` as `{ pip: <bare distribution name>, import: <module> }` (no versions; the app
     manages them). Autowright installs declared packages automatically on `create`/`push`
     (per-package status prints; a `warning:` line means the install failed — relay it to the
     user, the save still stands) and self-heals before every execution. Wheels only: a
     source-only distribution won't install. Never write pip/install code in steps.
   - Build rules are the app's (`autowright instructions --json` prints them under `build`);
     a rule this automation needs changed goes into `spec.md` in plain words (a
     "## Build rules" section), never into a workdir file.
3. `autowright automation create <dir> [--name "..."] [--agent "..."]
   [--grant-agent NAME]… [--grant-secret NAME]…` — validates everything (schema, param
   defaults, step order, syntax, import allowlist, trigger dialect) and creates v1. Grants
   are only what the flags name; steps that use an ungranted agent/secret fail validation
   naming the flags to add — show them to the user before adding them. On validation errors
   it prints one per line and writes nothing: fix the files and rerun.
4. Summarize for the user, then `autowright automation execute <name> -f` to run it live.

## Editing an automation

```
autowright automation pull <name> [dir]        # materialize current version into files
# … edit spec.md / manifest.yaml / steps …
autowright automation push <name> <dir> --note "what changed"   # validate, save as vN+1
# push keeps the stored grants; new agents/secrets need explicit --grant-agent/--grant-secret
autowright automation execute <name> -f
```

Pull/push round-trips faithfully: param *definitions* travel in the manifest (values are
user state — see Parameters), and the manifest's triggers merge into the stored list: crons
and intervals replace the stored schedules (matching entries keep their on/off state),
message/app-start entries add only when not already present, and stored non-schedule
triggers always survive.
Mistake in a new version? `autowright automation diff <name> --from v3` shows what changed
since v3 (file by file, `--to vN` for another pair), and `autowright automation restore
<name> v3` brings any old version back as the next version.

## Executing and results

```
autowright automation execute <name> -f            # run now, stream logs
autowright automation execute <name> --version v2  # or "draft"
autowright execution list [-n 20] [--automation <name>]... [--status failed]... [--since 2026-09-01] [--until 2026-09-06T18:00]
autowright execution show [<id>]                   # steps, error, result files (default: latest)
autowright execution tail [<id>]                   # full logs (streams while live)
autowright execution result [<id>] [file]          # list result files / print one to stdout
autowright execution cancel|retry|skip [<id>]
```

`execute -f` and `tail` exit **2** when the execution ends other than succeeded — branch on
the exit code. To debug a failure: `execution show` for the failing step and error (often
with a diagnosed reason), `execution tail` for the log lead-up, then fix via pull/push and
`execution retry -f` or a fresh execute.

## Parameters, triggers, secrets

```
autowright automation param list <name>
autowright automation param set <name> sources='["https://…"]' notify=on retries=3
    # toggle: on|off · number: int · text: string · list: JSON array or a,b,c ·
    # kv: JSON object or k=v,k=v
autowright automation trigger list <name>
autowright automation trigger add <name> "0 8 * * *" [--timezone Asia/Tokyo]
autowright automation trigger add <name> --every PT6H              # interval, since the last run
autowright automation trigger add <name> --at 2026-08-01T09:00     # one-shot
autowright automation trigger add <name> --app-start
autowright automation trigger add <name> --discord <channel> --secret NAME
    [--pattern TEXT] [--mention] [--author ID,ID…]
autowright automation trigger add <name> --imessage <from> [--pattern TEXT]
autowright automation trigger on|off|remove <name> <index>
autowright secret list · secret set NAME [--stdin] · secret delete NAME
autowright agent list · agent check <name>        # AI agents available to agent: true steps
```

## Memory, sharing, settings

- Memory persists files between executions (e.g. "seen items"). `automation show` reports
  its size; `automation memory show <name> [file]` lists the memory files or prints one
  file's text (read-only — use it before writing steps that migrate or reshape stored
  state, and when diagnosing a failure that hinges on what memory actually holds);
  `automation memory clear <name>` resets it; `automation snapshot
  list|create|restore|delete <name>` manages point-in-time copies (automatic snapshots are
  taken before clears/restores/new versions).
- When a push changes the shape of what steps store in memory, the old data survives the
  save — write the new steps to migrate lazily (a `schema_version` key, `memory.load`
  defaults for missing shapes) rather than assuming a fresh dir; check the real shape
  first with `memory show`.
- `automation export <name> [file.autowright]` / `automation import <file-or-https-url>`
  share automations as archives (secrets travel as names and descriptions only, never
  values; imported triggers arrive off). Import matches referenced agents and secrets
  against the user's existing records and never creates any; a reference with no local
  match lands the automation needing attention - the user fixes it in the app's editor.
  Export includes the user's param values — `--no-values` leaves
  them out when the archive is for someone else.
- `marketplace list` / `marketplace add <link-or-file>` / `marketplace refresh [<name>]` /
  `marketplace remove <name>` / `marketplace install <name> <n>` work with marketplaces -
  catalogs someone published listing shareable automations. Each catalog has a location (a link,
  a file path, or none when Autowright keeps the only copy); `refresh` re-reads that location.
  `marketplace set <name> location=… shown=on|off autoRefresh=on|off` changes where it is read
  from, whether the page shows it, and whether it refreshes on its own (at launch and every 6
  hours). `list` numbers each marketplace's entries; `install` takes that number and lands the
  automation through the ordinary import (agents and secrets matched by name, triggers off).
- `marketplace create [<folder>]` creates a catalog (in a folder on this machine, or kept by
  Autowright without one) and adds it; `marketplace catalog add <name>
  <automation-or-.autowright-file> [--export-to DIR]` exports an automation (no parameter
  values) beside the catalog, or into DIR for a catalog kept by Autowright, or lists an existing
  archive where it is (catalog entries are absolute paths or https links, never relative; a
  marketplace never stores archives itself); `marketplace catalog set <name> name=…
  description=…` edits the catalog's fields; `marketplace catalog remove <name> <n>` drops entry
  n (its archive file stays). These work on catalogs on this machine only (a link location is
  read-only).
- `settings show` / `settings set days=30 notifications=all developerMode=on dataPath=/path`.
