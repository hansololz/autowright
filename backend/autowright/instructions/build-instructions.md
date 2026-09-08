# Build instructions

These are Autowright's default rules for how an automation is built. They are not
editable and they update with the app. **The spec overrides them**: wherever the
automation's spec says otherwise, in plain words, the spec wins, and everything the
spec is silent on follows these rules. When the user asks to change one of these
rules for one automation ("it's fine to delete files here", "retry the fetch three
times"), write the new rule into the spec (a "## Build rules" section is the usual
home) and build to it. Never ask the user to change this document, and never restate
a default the spec doesn't change.

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

## Names and words

- **Keep the name and description accurate**: short plain words naming what the
  automation does. When a change makes them stale, update them along with the change
  through the `name` and `description` actions.
- **Write specs and step names in plain, friendly words.**
