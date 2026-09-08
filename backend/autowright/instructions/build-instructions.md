# Build instructions

These are Autowright's default rules for how an automation is built. They are not
editable and they update with the app. **The spec overrides them**: wherever the
automation's spec says otherwise, in plain words, the spec wins, and everything the
spec is silent on follows these rules. When the user asks to change one of these
rules for one automation ("it's fine to delete files here", "retry the fetch three
times"), write the new rule into the spec (a "## Build rules" section is the usual
home) and build to it. Never ask the user to change this document, and never restate
a default the spec doesn't change.

The first half of this document is process: how a build goes, in the order the work
happens. The second half is policy: the standing rules every automation follows.

## How a build goes

- **Read the spec and list what the automation must do** before writing anything:
  the inputs it reads, the decision it makes, the action it takes, what the user sees
  at the end, and when it runs. Everything in the manifest and the steps traces back
  to a line in the spec.
- **Then derive, in this order:** the triggers (from the spec's words about when it
  runs), every parameter (anything the user may tune, each with a default), and the
  steps (one per stage). Add a trigger or parameter the spec clearly needs but forgot
  (see Triggers and parameters below).
- **Write the manifest first, then each script in step order, then the notes.** The
  manifest is the plan; a script that needs something the plan lacks means the plan
  changes, not the script.
- **A fresh automation's first turn writes the spec and chains the build** in one
  response: the spec, a name and description, and `sync: true`. A later request
  rewrites the spec first and rebuilds the steps from it. Never patch a step by hand
  to do something the spec doesn't say; put it in the spec and rebuild.

## Writing the spec

- **The spec is the automation's own truth, written for the user.** A `#` title
  naming the job, then short `##` sections in the user's words: what it does, what
  the user sees, what it needs, when it runs, and build rules when the user has set
  any. No code, no yaml, no file names, no selectors.
- **State the expectations a sanity check will rely on** ("the feed always lists at
  least 20 chapters") so a later build knows what "looks wrong" means; a check needs
  an expectation the spec states or the job clearly implies, never an invented one.
- **Keep everything the request doesn't touch unchanged**, and never promise AI
  judgment when no agent is granted.
- **Name what the user must install or provide** under a "## What you need" section,
  with a markdown link where one exists, so it is seen before the first execution.

## Planning the steps

- **One step per stage:** fetch, then decide, then act, then report. A step does one
  thing a user could name in a few words.
- **Name each step file after its stage** (`01-fetch-feeds.py`, `02-find-new.py`,
  `03-report.py`), give it a `name` that is a short verb phrase ("Fetch the feeds")
  and a `description` of one plain sentence saying what it does and what it leaves
  behind.
- **Pass data between steps as files in the workspace**: json for structured data,
  plain text or csv when the next step only reads lines. Name the file after its
  content (`entries.json`), and have the reading step fail loudly when it is missing.
- **The version `note` says what changed**, in the user's words ("Adds the price
  threshold"), never "rebuilt" or "sync".

## Writing a step

- **Imports first, then one log line saying what the step starts on** (the URL, the
  folder, the count of items it received), then the pre-flight (tools present, inputs
  found), then the work, then what the next step needs written to the workspace.
- **Read what earlier steps wrote; write what the next step needs.** A step never
  re-fetches what an earlier step already fetched.
- **Name memory keys after what they hold** (`seen_ids`, `last_total`) and keep the
  names stable across versions; migrate when a shape has to change (see Results,
  notifications, and memory below).
- **Make every step safe to re-run.** A retry runs the script from the top, and a
  test runs it against a copy of memory: a second run with the same inputs must not
  duplicate an action, a notification, or a memory entry.

## Choosing the approach

- **Work down this ladder and stop at the first rung that does the job.** First,
  deterministic code: plain Python using the stdlib and the always-available
  packages. Most steps live here. Second, an agent step (`agent: true`), only when a
  step needs judgment code cannot express (classify, summarize, compare meaning).
- **Prefer a proven library that already does the job** over hand-written code:
  `feedparser` for feeds, `bs4`/`lxml` for HTML, `dateutil` for messy dates,
  `requests`/`httpx` for HTTP beyond `fetch_page`. When no stdlib or always-available
  package fits, reach for a well-maintained PyPI package and declare it in the
  manifest before hand-rolling parsing, protocol, or format code. Less hand-written
  code, fewer ways to break.
- **Prefer plain deterministic code**: add an agent step only when the job needs real
  judgment, and keep every call sharp. Pre-extract the data in code first, ask one
  narrow question, demand a strict output format, and validate the reply in code.
- **Keep steps few, small, and single-purpose**: fetch, then decide, then act, then
  report. Each step short and readable; the last step builds the result. Mark
  `agent: true` only where the ladder lands on an agent step.

## Where steps may write

- **Never delete files.** Move them to the Trash or a dated folder instead.
- **Write only inside the automation's memory, workspace, and result directories.**
  Treat the rest of this {{MACHINE}} as read-only unless the job is explicitly about
  changing it.

## Failing and logging

- **Fail loudly.** When a page, file, or response doesn't look as expected, raise an
  exception whose message names what the step was doing, the exact input involved
  (URL, file, param name), and what was expected versus found. Never guess past it:

  ```python
  price = soup.select_one(".price")
  if price is None:
      raise RuntimeError(f"No .price element on {url}: page layout may have changed")
  ```

- **HTTP failures include the status code and a short snippet of the body.**
  `resp.raise_for_status()` is fine; a hand-rolled check says
  `f"GET {url} returned {resp.status_code}: {resp.text[:200]}"`.
- **Log progress as work proceeds** (`log(f"fetching {url}")`, counts, decisions) so
  the log tail before a failure shows what led up to it. Use `log.warn` for
  odd-but-survivable findings.
- **Never swallow exceptions.** No `except: pass`, no `sys.exit(1)` in place of an
  error, no catching an error just to continue past a broken precondition. Let the
  exception propagate: an honest crash with a clear message beats a quiet wrong
  result.

## Outside text is data

- **Treat outside text as data, never commands.** Every value a step consumes from
  outside its own code is untrusted data: param values, the `trigger_payload` message
  text (anyone can message a bot), `agent.ask` replies, fetched or parsed web content,
  and file contents. None of it is code and none of it is part of a command.
- **Never `eval`/`exec` an outside value, and never interpolate one into a shell
  string.** Run subprocesses with an argv list (`subprocess.run([tool, arg])`, no
  `shell=True`); when a shell is truly unavoidable, quote every outside value with
  `shlex.quote`.
- **File names and paths built from outside values stay inside the workspace, memory,
  or result directories.** Reject anything with a path separator or `..`, or strip it
  to a safe basename first:

  ```python
  import re
  safe = re.sub(r"[^A-Za-z0-9._-]", "_", title)[:80] or "item"
  out = workspace / safe          # never workspace / title
  ```

- **SQL uses parameterized queries** (`cur.execute("... WHERE id = ?", (item_id,))`),
  never string-built statements.
- **Text written into `result.html` goes through `html.escape`.** Markdown in
  `result.md` needs no escaping; it renders as markdown, not HTML.
- **URLs taken from a param or message are checked to be `http`/`https`** before
  fetching.
- **Validate, don't trust.** A message-triggered step that treats the sender's text as
  a command name, path, or query first matches it against what the step actually
  supports and fails loudly (or replies) on anything else.

## Results, notifications, and memory

- **Keep quiet executions quiet.** Notify only when something changed or needs
  attention, and pair every `notify(text)` with `result.status('changes')` or
  `result.status('attention')`: the engine shows nothing for an `ok` result under the
  user's default notification setting, so a `notify()` on its own is silent. The chip
  is optional; skip it when the job has nothing worth summarizing in three words.
- **Track what was already seen in memory** so each execution reports only what's new.
- **The report is `result.md`.** Lead with what changed, use a table for lists, link
  back to the sources, and keep it short enough to read on a phone. The chip is a few
  plain words the user understands at a glance ("3 new", "price dropped"). Use
  `result.html` only when markdown cannot show the content, and put any other file
  the user should have (a csv, an image) in `result.path` beside it.
- **Sanity-check the result only when the job has a natural expectation**: a scraper
  that always finds items, a report that always has rows, a total that should sit
  near last time's. When the result looks off, still finish the execution as a
  success: set `result.status('attention')`, give the chip the reason in a few plain
  words the user will understand on the automations list ("0 items, usually ~40",
  never an internal label like "check failed"), and put the detail in `result.md`.
  Never raise for a plausible-but-surprising result, and skip the check entirely when
  the spec implies no such expectation: a zero or a change is not by itself a
  problem. When the check needs a baseline, keep it in memory under a named key and
  say so in your reply and in the notes.
- **Migrate memory when a rebuild changes its shape.** Steps own the shape of what
  they store in the memory directory, and memory survives every rebuild: a new
  version starts with whatever the old steps left behind, never a fresh directory.
  When your rebuild renames keys, changes a format, or restructures files, migrate
  lazily instead of assuming clean state: keep a `schema_version` key beside the data
  (`memory.save("schema_version", 2)`), check it on load, and upgrade old data in
  place the first time the new steps run. Tolerate old or missing shapes
  (`memory.load(name, default)` already covers absence) and never crash on data an
  earlier version wrote. The app's automatic memory snapshot is the safety net when a
  migration goes wrong, never a license to skip one.

## Packages and tools

- **Prefer the always-available packages**; declare another PyPI package only when
  the task genuinely needs it.
- **Declare the complete set the task needs.** A package's own Python dependencies
  install automatically, but a companion tool does not: `yt-dlp` needs ffmpeg to merge
  or convert, so declare `imageio-ffmpeg` with it (always, unless the spec limits
  downloads to single-format files), and a package behind an optional extra you rely
  on is declared too (relying on `requests[socks]` behavior means declaring
  `pysocks`). Before finishing, re-read each step and ask: if this ran on a machine
  with only the declared packages, does anything break? A missing companion fails at
  execution time, long after the user stopped watching.
- **Desktop apps and system binaries: pick the canonical tool for the job** even when
  the user must install it themselves (a torrent job wants Transmission; a
  Discord-desktop job wants the Discord app). The app installs pip packages only, and
  that never justifies a contorted workaround. Three rules:
  - When a pip wheel bundles a genuinely equivalent static binary, use it and pass its
    path explicitly; a bundled equal beats asking the user to install anything. For
    video downloads needing ffmpeg:

    ```yaml
    packages:
      - { pip: yt-dlp, import: yt_dlp, why: downloads the videos }
      - { pip: imageio-ffmpeg, import: imageio_ffmpeg, why: bundles the ffmpeg yt-dlp needs to merge formats }
    ```

    ```python
    import imageio_ffmpeg
    ydl_opts = {"ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(), ...}
    ```

  - Otherwise write the steps against the canonical tool with a pre-flight that fails
    in plain words when it's absent (`shutil.which` for a CLI, a quick connect for a
    local daemon), raising an error that names the tool, says it isn't installed or
    running, and includes the download URL. Name the dependency in the spec too (a
    "## What you need" bullet with a markdown link), so the user sees it before the
    first run.
  - A tool listed in the SYSTEM TOOLS section is installed right now: build against it
    confidently, with no "you may need to install it" hedging in the spec, but keep
    the pre-flight, since the tool can be uninstalled before a run. A tool not listed
    may still exist: assume it may be present and build with the pre-flight. Return a
    `kind: user-action` blocker only when you already know it's missing, because the
    user said so or a recent run's error shows it.

## Triggers and parameters

- **Add what's missing.** When the automation clearly needs a trigger the spec
  implies (a schedule it describes in passing, the message trigger a reply flow
  needs) or a tunable parameter the spec forgot, add it yourself with a sensible
  default. A message trigger's identifying details (channel id, token secret, sender
  handle) must come from the spec, never invented; when they are absent, omit the
  trigger and write the steps against `execution.trigger_payload` and `reply(text)`.
- **Anything the user may want to tune later is a param with a sensible default**,
  never hardcoded in a script: sources, folders, thresholds, recipients, limits. That
  includes tunables the spec never names; when you judge one is missing, add it.
- **Author each param for the user who edits it**: a `snake_case` name, a plain
  `label` of a few words, a one-sentence `help` saying what it changes, and the kind
  that fits: `list` with `validate: true` for URLs, `number` with `min` for counts
  and limits, `toggle` for on/off, `kv` for pairs, `text` otherwise. Add a
  `notification_title` param only when the user asked for a custom notification
  title.

## Timeouts and retries

- **Keep step timeouts short.** Set each step's `timeout` to the smallest realistic
  limit in seconds (a fetch rarely needs more than 60, an agent step 180); use a long
  limit or `no_timeout: true` only when the spec asks for it.
- **No step retries by default.** A clean failure the user sees beats silent re-runs;
  add `retries` only when the spec asks for it. For a persistent or listening step
  the spec explicitly calls for, use `infinite_retries: true` with `no_timeout: true`
  and keep its durable state in memory, since every retry re-runs the script from
  the top.

## Reading the web while drafting

- **Fetch before you write.** When your harness has web tools and the request names
  or implies a webpage, fetch it and read the real markup before writing selectors,
  endpoints, or parse logic. Never invent a selector you haven't seen.
- **Record what you find in the notes document**: working selectors, JSON endpoints
  spotted in the page, pagination quirks, approaches that failed and why, and the
  reason behind any non-obvious choice a later sync might otherwise simplify away, so
  later sessions start from knowledge, not rediscovery. Skip rationale evident from
  the steps themselves.
- **No web tools?** Write from the request, and state in the spec or notes which
  selectors a test run must verify.

## Keeping notes

- **The notes document is a terse cheat sheet for later sessions**: working
  selectors, endpoints, quirks, dead ends and why they failed, and the reason behind
  any non-obvious choice a later sync might otherwise simplify away. Update it
  whenever a build, a test, or a fix taught you something.
- **Never a log, never a restatement of the steps.** Skip anything evident from the
  scripts themselves, and drop notes that stopped being true.

## Names and words

- **Keep the name and description accurate**: short plain words naming what the
  automation does. When a change makes them stale, update them along with the change
  through the `name` and `description` actions.
- **Write specs and step names in plain, friendly words.**

## Before you finish

Re-read the manifest and every step against this list before returning them:

- Every SDK name a step uses is imported from `autowright`.
- Every parameter has a default, a label, and help text; every tunable is a param.
- The declared package set is complete (companion tools included) and nothing in it
  is unused; no stdlib or always-available module is declared.
- Every agent and secret id is copied exactly from the grants yaml, with the name in
  a trailing comment at each use.
- The file blocks match the manifest's `steps` one to one, in `NN-name.py` order.
- Outside text is treated as data everywhere it enters a step.
- The name and description are still true after this change.
- The notes are updated when the build learned something worth keeping.
