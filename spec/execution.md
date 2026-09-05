# Autowright SPEC — Execution lifecycle

Part of the Autowright spec. Index and § map: [SPEC.md](../SPEC.md). § numbers are global across spec files.

## 7. Execution lifecycle

- At most `maxParallel` executions at a time per automation (§4.1, default 1). A plain manual
  start (§19 `queue` absent/false) with every slot taken is refused (409), never silently
  queued — but the user can **choose** to queue it: §19 `queue: true` joins the §6 firing
  queue (manual-admission rules there: free slot starts immediately, Draft never queues, full
  queue answers 409 "the queue is full (N waiting)", and manual entries have no TTL).
  Surfaces: the §9.2 detail page's Execute now opens the
  **capacity popup** (§9.2) instead of firing blind whenever another execution is live — the
  popup is where Run now / Queue / capacity-full are offered. Every other surface (the §9.1
  inline execute button, the §13 menu bar, the execution page's Execute again, and any raced
  409 behind the popup) keeps the busy **toast**: "Already executing — one execution at a
  time. A trigger firing now would be skipped." at the default (`maxParallel` 1, no queue);
  when `maxParallel > 1` or `maxQueued > 0` the toast says what actually happens next: "The
  slot is busy" (`maxParallel` 1) or "All N slots are busy" (`maxParallel` > 1), followed by
  "A trigger firing now would be queued." when `maxQueued > 0`, else "A trigger firing now
  would be skipped."
- Start: execution record created with all steps queued; automation gets live id, lastStatus
  executing, lastExecutionLabel "executing…"; the execution appears at top of Executions; sidebar counts
  and menu-bar rows update live.
- Before step 1 the engine ensures the version's declared packages (§6.2): the fast
  installed-check costs milliseconds when everything is present; anything missing installs with
  a sys log line ("installing packages: `pandas`…"). An install failure fails the
  execution before any step with the package category below.
- Streaming: each step queued → executing → terminal status with duration. Executing a step
  appends an **attempt** (its `number` = the last attempt's `number` + 1 — monotonic per step,
  never re-derived from list length, since the §4.5 prune drops old entries) to that step; the
  step's status always equals its
  latest attempt's status. Each attempt streams into its own log file (§5) — the step's own
  output, its timeout/cancel/skip lines, and the
  automatic step-retry marker (emitted after the new attempt opens, so it lands in the new
  attempt's file) all land
  there. The engine writes **no opener line** when an attempt starts: the file begins with
  the step's first line of output (or the first engine event), never a synthetic
  "Step N: name" header, since the §7 LOGS pane already names the selected step in its header,
  and a step that prints nothing shows the "No log lines here." empty state rather than a
  header-only log. Execution-level lines (package installs, secret failures, the manual in-place retry
  marker, the final failure line) go to `logs/execution.ndjson`. Then the execution gets its final status,
  duration, result object; automation gets latest/resultChip/lastExecutionLabel "Today"; toast
  summarizes. An execution whose steps include `skipped` ones but no failures finishes
  `succeeded`.
- Cancel: kills timers/processes; execution cancelled, the executing attempt and its step
  cancelled, queued steps cancelled, sys log "execution cancelled by you — nothing else will
  happen". At finalize the cancel flag marks the execution `cancelled` **only when at least
  one step was actually cancelled or left non-terminal** — a cancel that lands after the
  last step already succeeded changes nothing and the record finishes `succeeded`: the
  status reports what happened to the steps, not that a button was pressed too late.
- **Kill semantics:** each step's executor runs in its own process group
  (`start_new_session`), and timeout/cancel/skip signal the whole group — a step's children
  (Playwright browsers, subprocesses) die with it, are never orphaned, and can never hold the
  engine's log pipe open past the kill (which would strand the automation "executing").
  One child deliberately escapes that group: a §6.1 runtime agent call's harness CLI spawns
  in its **own** session (so the call's idle-window watchdog can kill the CLI and its helpers
  without killing the step). The executor therefore reports that child's group id to the
  engine over the event stream when the call starts and retracts it when the call returns;
  the engine keeps the live set in the execution's kill state and persists it on the record
  (`agentPgids`, §4.5), so cancel/timeout/skip kill the agent group(s) right after the step
  group, and §3 orphan recovery sweeps them with the same pid-reuse guard as `pgid`. Without
  this, killing the step would orphan a running harness CLI mid-call.
  On Windows the executor spawns via the console interpreter with a hidden console (§2 spawn
  policy: `paths.console_python()` + `CREATE_NO_WINDOW`), so a step's console-subsystem
  children inherit the invisible console instead of each opening a terminal window under the
  §3 `pythonw.exe` service.
- **Skip step:** while a step is executing, the user can skip it (§19
  `POST /executions/{id}/skip-step` with the step index — 409 unless that exact step is the
  one currently executing, closing the finished-while-clicking race). The engine kills the
  step's subprocess, marks the attempt and step `skipped` (no error recorded), writes the sys
  line "step skipped by you — continuing with the next step" to the attempt log, and
  continues with the next step. If the process exited successfully before the kill landed,
  the step stays `succeeded` (sys line "skip arrived after the step finished"). A cancel
  arriving with a pending skip wins. Skipped steps are terminal — a later retry never
  re-executes them.
- **Step retry (automatic):** a failed step attempt whose step carries a §4.1 retry budget
  (`retries: N`, or `infiniteRetries: true`) is re-executed **immediately** — the engine
  appends the next attempt and re-runs the same script; the execution and the automation stay
  `executing` throughout (the step reads `failed` only for the instant between attempts, per
  the latest-attempt rule §4.5), so nothing terminal flickers and no repeat toasts fire. The
  failed attempt keeps its error and log file; a sys line in the new attempt's log says
  "attempt N failed — retrying (M of K)" ("retrying (attempt N+1)" under `infiniteRetries`).
  The budget counts **per execution pass**: `retries: N` allows N automatic re-attempts of
  that step per pass, and a manual in-place retry (below) starts a fresh pass with a fresh
  budget — automatic and manual attempts never share a counter. `infiniteRetries` retries
  until the attempt succeeds or the user cancels/skips; its consecutive attempts are spaced
  ≥ 1 s apart (`AUTOWRIGHT_STEP_RETRY_PAUSE_S`, §15 — a deterministically-crashing script
  must not hot-loop process spawns), while finite retries run back-to-back. Cancel and Skip
  step win over a pending retry exactly as they win over a running attempt — skip marks the
  step `skipped` and moves on, spending nothing further. Only step failures retry: the
  pre-step gates (missing secret, package install) and engine-level failures are not attempts
  and never retry. Attempts beyond the newest 20 are pruned with their log files (§4.5/§5).
- **Failure diagnostics:** when a step fails, the executor reports the exception as a structured
  control event (exception type + message) alongside the traceback err lines; the engine stores
  §4.5 `error` on the record — the failing step's name, the message ("`ExcType: message`",
  redacted like any log line), and a plain-word **possible reason** when the failure matches a
  known category, null otherwise. Categories (deterministic, from exit code / exception type /
  message — never an agent call): step timed out ("The step hit its `N` s time limit.") ·
  disallowed import ("The step imports a package outside the allowed list.") · package install
  failed ("A required package couldn't be installed — check your connection, then execute
  again or retry from the edit page.") · missing secret — the §6 pre-step gate sets one of
  "A step references a secret this automation isn't allowed to use.", "A step references a
  secret whose value hasn't been added yet.", "A step references a secret that isn't in your
  Keychain.", or — a §4.1 secret id matching no stored record — "A step references a secret
  that no longer exists (`<short id>`)."; a step whose code reads a nonexistent secret at
  runtime gets "The script
  references a secret that doesn't exist." · undeclared secret — the step read
  a secret the automation allows but the step never declared or referenced, so it wasn't
  injected (§6 step scoping; the executor's message says so: "secret `NAME` wasn't injected
  into this step — steps only receive the secrets they reference in code or declare in the
  step's `secrets` list"; reason "The step reads a secret it doesn't declare — add it to the
  step's `secrets` list.") · agent call failed ("The step's agent
  call failed — the agent may be unreachable or misconfigured.") · network failure —
  connection, DNS, timeout ("A network request failed — the site may be down, blocking, or
  unreachable."; the §6 `fetch_page` refusal messages — "couldn't fetch `<url>`: …",
  "robots.txt disallows fetching `<url>`" — classify here too) · HTTP error status ("The site answered with
  an error (HTTP `nnn`)."; when no 3-digit code can be extracted from the message, the
  codeless fallback "The site answered with an error.") ·
  unexpected data shape — KeyError/IndexError/AttributeError ("The data didn't have the
  expected shape — a page or file layout may have changed."). Engine-level failures (missing
  script file, agent step with no agent) set `error` the same way. Shown on the automation
  detail page (§9.2) and the execution page.
- **Retry (in place):** a failed execution can be retried — the same execution record
  re-executes from the failed step; no new execution is created. The failed step's status
  flips back to `queued` (its attempt history stays), the execution goes `status: executing`
  with `finished_at`/`error`/chip cleared, and the engine re-enters the step loop, which
  executes exactly the steps still `queued` — succeeded and skipped steps are never
  re-executed and keep their attempts. Each executed step appends the next attempt. Same
  workspace (earlier steps' outputs are already there — nothing is copied), same result dir
  (a failed pass's stale result files may remain until steps overwrite them), accumulated
  duration (`duration_ms` sums the passes; `started_at` never changes). `execution.finished` fires
  again per pass, so the end-of-execution toast repeats — intended. Retry is allowed only on
  terminal `failed` executions and answers 409 when the automation is at `maxParallel`
  capacity (§6 — with a free slot a retry is admitted beside a live execution), when the
  version no longer resolves, or — for a Draft execution — when the draft's steps changed
  since the record (a re-saved draft would pair old step statuses with new scripts; execute
  it fresh instead). Manual retries are uncapped and are the **only** execution-level retry —
  nothing retries a terminal execution automatically (§6); each manual pass grants the steps
  a fresh automatic step-retry budget (above).
- **Header actions** on the execution page: while executing, **Skip step** (quiet bordered,
  tooltip "Skip this step — kills it and continues with the next one"; skips the currently
  executing step) beside **Cancel**. A failed execution
  gets a quiet bordered "Execute again" (tooltip "Executes the automation again from the
  start" — a plain fresh execution) and, rightmost per the §9 header-action order, a primary
  accent **Retry** (tooltip "Retries this execution from the failed step. Steps that already
  succeeded keep their results.").
  Succeeded / cancelled / interrupted executions get only the quiet "Execute again".
- Trigger labels (derived at serialization from the stored §4.5 kinds): Manual, Menu bar
  (Tray on Windows and Linux — the §9 per-OS copy rule; the app reads the label off the
  machine that serializes it),
  Cron, Once, App start, Discord, iMessage. `interrupted` covers e.g. "Mac went to sleep" — applied
  by startup recovery when a restarted backend finds stale `executing` executions; recovery
  first SIGKILLs the record's persisted step process group (`pgid`, §4.5) when that group
  still exists, so an orphaned step can't keep executing beside the record it lost. A sleep the
  backend process survives simply resumes the execution. `skipped`/`cancelled` executions may carry a
  note ("previous execution still in progress", "the queue was full (N waiting)", "waited too
  long in the queue" — never on a §6 manual queue entry, which has no TTL —
  "cancelled before it ran", "backend restarted before this ran",
  "version vN no longer exists" — a queued entry whose admitted version is gone by its turn,
  and equally a trigger firing whose `currentVersion` doesn't resolve: it leaves this
  skipped record rather than vanishing, since an automation whose current version is gone
  would otherwise fire and disappear silently every occurrence forever, with §4.1 `overdue`
  never flagging it). The
  first two are different problems and must not share a note: "still in progress" is the
  configured skip-on-busy behaviour, while a full queue is a capacity limit the user fixes by
  raising `maxQueued` (§4.1) — the note names the cap so the row says which knob to turn. A `queued` execution (§6 firing queue) has no steps and no
  duration until it is promoted; its page shows the waiting state, its `triggerPayload`, and a
  Cancel action (see *Queued execution page* below), and promotion turns it into an ordinary
  executing record in place.
- **Queued execution page** — a `queued` record is addressable by id like any other execution
  and its page is the waiting state, not an empty version of the normal one. The header keeps
  the title row and the Queued badge, and its only action is **Cancel** (quiet danger,
  §19 `POST /executions/{id}/cancel` — the same endpoint as a running execution; the entry
  leaves the queue and finishes `skipped`). The mono metadata line drops duration and reads
  full id (copyable) · trigger · version · queued `<time>` · waiting `<elapsed>`, the elapsed
  value ticking every second while the page is open. In place of the RESULT card and the
  steps/logs card the body is one **waiting card**: the headline "Waiting for a free slot",
  its queue position among that automation's `queued` records ("2nd of 3 waiting", oldest
  first — the drain order), and the line "Every slot is busy. This runs as soon as one frees
  up." Below it, when the record carries a §4.5 `triggerPayload`, the **TRIGGER MESSAGE**
  block (shared with the ordinary execution page — see its full shape there). Promotion replaces
  the whole body with the ordinary execution page in place — no navigation, the record and the
  URL never change, and the trigger message stays visible (the ordinary page renders the same
  block), so the input that fired the run doesn't vanish the moment it starts.

**Execution page:** back link, title row with status badge and the header actions above — the
row never wraps: the automation name is a single line that shrinks with ellipsis (full name in
its tooltip), so the actions always sit on the title line at the same height as every other
page's header buttons (same rule as the §11 Review title);
below the title a mono metadata line: full execution id (copyable) · trigger · version ·
started · duration. A §4.5 `test` execution additionally shows a **"Draft test"** `MetaChip` in the
title row, never shows the "(deleted)" marker (a create-mode test has no automation by
design), and hides Retry and Execute again — iteration on a draft happens from the editor's
TEST card and test-run modal; Cancel and Skip step still work while it is live. Body stacks top to bottom: the
failure notice (failed executions only), a full-width **RESULT card**, then — on executions
carrying a §4.5 `triggerPayload` — the **TRIGGER MESSAGE** block (the same block the queued
page shows), one card:
- **header line** — sender, then for Discord the origin: `in #channelName · guildName` when
  the §4.5 names are present, falling back to the raw channel id when `channelName` is null
  (the `· guildName` part is simply omitted when null — never a literal "null"/"undefined");
  an iMessage payload shows the sender alone (it has no channel). The payload's `secret` is
  never displayed.
- right-aligned on the header line, Discord only: an **"Open in Discord"** external link
  styled as a button — the same bordered ghost treatment (`.ad-btn-ghost`) as the page's other
  actions (Retry / Execute again), never plain link text or the link underline — opening
  `https://discord.com/channels/<guildId>/<channel>/<messageId>` (`@me` in place of the guild
  id when `guildId` is null — the DM form), so the raw ids earn their keep as a deep link
  instead of being printed. Omitted entirely when `messageId` is null — a §4.5 mocked-test
  payload has no real message to open.
- below, the **message time** (mono, faint), then the **message text** (mono, wrapped).

The message is the
run's input — steps read it via §6.1 — so the page keeps it visible below the outcome and above
the machinery. Next, on executions whose snapshot carries any param definitions, the
**PARAMETERS card** — the run's other input, so it sits with the trigger message above the
machinery rather than beside the logs. It is a **collapsible view card** in the RESULT
section's idiom (§14 collapsible header: `Caret` + mono 12.5/500 title, `.ad-btn-bare
.ad-hover-row .ad-focus-inset`), titled "PARAMETERS · N" (N = param count, the FILES footer's
counter pattern, so the closed header still says what is inside) and **collapsed by default**
— the values are reference data, read on demand; collapse state is per-session only (never
persisted), like the result views'. Open, the body holds one **settings row** (§14: `15px 18px`)
per param — 13.5/600 label, its help description 12/1.55 muted beneath, and the §4.2 one-line
summary value right-aligned (12.5/500 `--text-2`, wrapping within the row's right half; never
a control, this is a read-only snapshot) — rows divided by `--hairline-dim` (one above the
first row too, under the header), and a closing footer line "Values as used by this
execution." (11.5/1.5 muted, `10px 18px 12px`, hairline-dim above).
The card is omitted entirely when the execution has no params — never an empty card.
Then a single
**execution card** that joins the **STEPS rail** (left) and the **LOGS pane** (right) with an
internal divider — one card, since the rail's selection drives the pane. The rail and pane are
one shared **execution view** (`executionView.tsx`: the rows, the selection and auto-follow
rules, the lazy log fetch, the live auto-scroll and cap) that the §11 test-run modal renders
inside its frame — one run UI, two homes; the rail holds only the step rows in both homes (it
is a selector, never a home for reference data), the modal adds its toolbar controls to the
pane. Last, below the execution card, the **WORKSPACE card** (`data-testid="workspace-card"`;
omitted when the record carries no `workspace` path — a pre-workspace record): the same
collapsible view card, titled "WORKSPACE" and **collapsed by default** (state per-session
only). Open, a `0 18px 14px` body whose first row is the §5 `workspace/` path in faint mono
(ellipsized from the left like the FILES footer's result-dir path) with a `.ad-btn-ghost` §9
reveal button ("Show in Finder", the per-OS §9 label, folder icon) at its right, and beneath
it a muted 11.5/1.5 line "The scratch directory the steps ran in. Shared across steps and
retries, and deleted with the execution." The card sits at the page's bottom, closed, on
purpose — the scratch dir is for inspecting what a run left behind, and its reveal button
must never compete with the RESULT card's Show in Finder, which is the user-facing output.
The STEPS rail's rows are **selectable**: each row shows the status dot (pulsing
while executing), name, a right-aligned attempt-count `MetaChip` ("×2" — only when the
step has more than one attempt; the count is the latest attempt's `number`, which survives the
§4.5 prune) and the latest attempt's duration — rows carry no actions;
skipping lives in the header's Skip-step button. Above step 1 sits a **"Setup log"**
pseudo-row (terminal icon in place of a status dot) selecting the execution-scoped log.
Selecting any row changes which log the LOGS pane shows. The **← / → arrow keys** move the
selection too, one row at a time through the rail's order (Setup log, then step 1 … N — ← from
step 1 lands on the Setup log; no wrap, a no-op at either end and while nothing is selected),
the same flip keys as the §9.2 step-script modal: they ignore every editable target, they are
inert while a modal covers the rail (the page's rail yields whenever any modal is open; the §11
test-run modal's rail yields while its card is closing), and a key flip is the user's own
selection — it ends the live auto-follow exactly like a click. The rail draws **no focus
ring** (the §9.2 rule, applied here): the selected row is a plain, unfocusable block
(`aria-current`, text-selectable), the other rows are buttons, a clicked row unmounts as it
becomes the selected block, and a key flip drops focus from whatever holds it — so the 2 px
accent bar and the faint fill alone mark the selection, never a box around a row. While the
execution is live the selection auto-follows the executing step until the user selects a row
themselves (reset when navigating to another execution); when a failed execution loads, the
failed step's latest attempt is auto-selected. On a failed
execution a **failure notice** sits above the RESULT card: red-tinted card, "Failed at step
“`<name>`”" (the step name in curly quotes; "Execution failed" when `error.step` is null —
a pre-step failure), the §4.5 possible reason as plain text when present, and the error
message in mono.
On a failed non-test execution whose automation still exists, the notice also carries a
quiet **"Fix with AI"** button: it opens the automation's §11 editor, seeds the chat thread
with a system entry naming the failure ("Execution failed at step `<name>` — `<message>`"),
and sends the §11 canned analyze chat message as a §8 chat job carrying this execution's id
as the §19 `executionId` — the RECENT EXECUTIONS context includes the run's error and log tails, and
the agent's answer, rewrites, and follow-up actions land in the thread (§11).
Test executions never show it — draft iteration already lives in the editor.
The LOGS pane shows the selected step's log. Its header opens with a faint mono
**"LOG k OF n"** eyebrow (k = the selected step's 1-based position, n = the execution's step
count, the same counter idiom as the §9.2 step-script modal's "STEP N OF M" toolbar) followed
by the step's name in dimmer mono (the modal's filename treatment; ellipsized, never wrapped);
the Setup log pseudo-row is not one of the n logs, so its header is the plain "Setup log"
eyebrow with no counter. A live attempt appends " · LIVE" to the eyebrow. On the page the
rail's STEPS header and the pane's header share one 38 px minimum height (8 px of vertical
padding under that floor) with vertically centered content, so their eyebrows and
bottom hairlines always align: the taller name text, attempt pills, and chips fit inside
that height, and only wrapping at a narrow width grows the pane header (the modal layout
pins both to its 44 px toolbar instead). The header also
carries the redaction note "secrets redacted: `<name>`"; when the selected step has
more than one attempt, a segmented **attempt control** sits in the header — one status-tinted
pill per retained attempt ("Attempt 2 · Failed · 3s", pills labeled by attempt `number` — after the
§4.5 prune the earliest pills are simply gone), latest selected by default. The pane is the
color-coded log view (kinds sys/out/wrn/err); logs load lazily per selected step/attempt
(§19) and live lines stream in over WS (deduped by `sequence`), with live auto-scroll and the
blinking cursor on the live attempt. Empty states: "No logs — this execution never
started." when the execution has no steps; an empty Setup log shows "No setup events —
installs, retries, and failures would appear here."; an empty step attempt shows "No log
lines here."
The pane is **capped at the last 2000 lines** of the selected log, the same cap the §7 text
preview uses: the lazy fetch asks the §19 logs endpoint for that tail (`tail`), and live WS
lines trim the kept buffer back to it, so a chatty run can never grow the view without bound.
Whenever lines were dropped — the initial fetch came back full, or live appends trimmed — a
note in the §7 truncation style reads "Truncated — showing the last 2000 lines. The full log
is on disk." It sits **above** the kept lines, since the dropped ones are the oldest and the
pane's live auto-scroll owns the bottom. The complete log always stays in the §5 execution
directory.
The RESULT card, when the execution has no result, is an `EmptyNotice` ("No result") with a
status-specific reason (still executing / failed before a result was built / cancelled / no
result produced); with a result it is a collapsible **Results section** holding a stack of individually
collapsible **result views**, each with a chevron + title header and right-aligned mono meta
("4.1 KB") — every view expanded by default on this page (§9.2's LATEST RESULT card trims the
same stack down), collapse state per-session only (never persisted). The section header row carries the result chip when the execution set one — tinted
by its chip status (changes = accent, ok = green, attention = orange); an execution that set no chip
gets no chip here — plus metadata chips; the execution's
own status badge stays in the page title row, never here. View order: one **file view** per renderable
file in alphabetical order (`.md` markdown, `.html` sandboxed iframe, images inline; titled
by filename), then a collapsible **FILES footer** ("FILES · N" header, **collapsed** by default, like the
PARAMETERS and WORKSPACE cards):
the result-dir path in mono, every file as a row, and a "Show in Finder" button opening the dir
in Finder. Rows are name + size, and a **previewable** file's row is itself expandable — chevron
at the left, the file's content rendering inline below it when opened. Previewable covers the
three renderable kinds plus **text** (`csv json txt yaml yml log tsv xml`), shown as mono plain
text, horizontally scrollable, capped at 200 KB / 2000 lines with a trailing "Truncated — use
Show in Finder for the full file." note. Anything else (zip, pdf, xlsx, …) gets no chevron and a
faint `no preview` `MetaChip`. Every row body starts **collapsed** regardless of the surface, and its
bytes are fetched lazily on first open — expanding the footer itself costs no requests.
Files present but none renderable → the section is just the footer, **expanded** (the only surface
with no view to show would otherwise be a lone collapsed row).
No files at all → the whole view stack (footer included) is replaced by an
`EmptyNotice`: "The latest execution didn't produce any result files."
Deleted-automation handling: historical name, marked deleted.

**Executions list:** all executions across automations, §4.5 `test` executions included — a
draft test lands here like any other (the §11 test-run modal's View execution button stays as a
shortcut). A test row reads like any row: `automationName` is the §11 shadow record's name (the
automation's; in create mode the draft's name, "New automation" fallback), never marked
"(deleted)" (a create-mode test has no automation by design), and its trigger column prints
"Test" **once** — the §4.5 trigger and versionLabel labels are both "Test", and the row never prints
the redundant pair (a mocked sender still appears between: "Test · Dave"). Test rows share
the record's draft-scoped lifetime (§11 keep-latest): starting the next test replaces the
previous row, and a settling draft removes its rows. Three sections, top to bottom —
active work, then what it is holding up, then history: **Executing** (`executing` rows, newest
start first), **Queued** (§6 firing-queue `queued` rows, oldest wait first — the drain order,
so the next one to run reads top), **Finished** (newest start first, by §4.5 `startedMs`, id
ascending on ties: the one canonical order the §19 `/state` window, the `GET /executions`
keyset, and the §5 `(started_at DESC, id)` index all share, which is what lets paged fetches
line up seamlessly with the live window. `endedMs` plays no part in ordering, so a finishing
execution slides down into the position its start time earns, not automatically to the top of
Finished; a long execution lands below everything that started after it, matching how Executing
itself is ordered).
Executing and Queued each render only when they hold rows, and a promoted firing moves itself
from Queued to Executing with no refetch. Queued rows get their own section rather than sitting
in Executing because their columns differ and because "waiting on a slot" is a different question
from "executing now". With nothing live or queued
the page stays a single unlabeled table; as soon as either section exists, every rendered
section gets a small mono label (EXECUTING / QUEUED / FINISHED) and an empty Finished section
shows the filter's empty-state card. That card's title: "No `<filter>` executions" ("No
succeeded executions", "No executing executions" - the filter name lowercased) under a
filter; on All, "No finished
executions yet" when sections are labelled, else "No executions yet". Body: "Executions
matching this filter will appear here." under a filter; on All, "Finished executions will
appear here." when labelled, else "Execute an automation — every execution will appear
right here." The status filter is the page title's segmented control: **All · Executing ·
Queued · Succeeded · Failed · Cancelled · Skipped · Interrupted** - single-select, in the
sections' own order. The three-section stack belongs to **All** alone; every other segment
shows exactly one table with no sections stacked above it (and no mono section label - a
single table needs none). A terminal segment shows that status's finished rows. **Executing**
shows just the `executing` rows (normal columns) and **Queued** just the `queued` rows (its
own columns, below); segment labels are the section names, which are the §4.6 words
capitalized ("Executing", "Queued") - execution terminology everywhere, never "Running" or
"run" wording on this page. Both live segments read entirely from the §19 window - every
live row always rides it - so neither ever fetches, pages, or renders the pager. The Queued
table swaps
the last two columns for **QUEUED FOR** (elapsed since §4.5 `queuedMs`, ticking every second)
and **QUEUED AT**; a queued row has no duration and has not started, so showing either would be
a lie. Each row shows the automation name with
the short execution id (mono, first 8 characters — the same short form the detail page's
RECENT EXECUTIONS rows use; the full id lives on the execution page's metadata line) on a
second line beneath it, status badge, a trigger column combining trigger and version
("Manual · v3"; a message-triggered row puts the §4.5 `triggerSender` between them —
"Discord · Dave · v3"), timestamps, durations. Rows carry no note
text — skipped/cancelled notes appear on the detail page's RECENT EXECUTIONS rows and on the
execution page.

**Finished paging.** Retention (§5) defaults to 90 days and `keepForever` turns cleanup off
entirely, so finished history has no upper bound; it moves in pages of **50 rows** and never
rides into the renderer whole. §19 `GET /state` ships a **window**, not the full list: every
`queued` and `executing` header, the 50 newest finished headers (exactly one page), and
`executionsTotal` (the count of every header the backend holds, §4.5 test rows included - the
§9 sidebar pill's number). The page derives Executing, Queued, and the first Finished page from
that window, so it opens with no fetch of its own. Deeper history and the terminal filters
come from §19 `GET /executions` (the Executing and Queued segments never fetch - the window is
already complete for live rows): picking a terminal filter fetches that status's newest page
(`?status=<status>&limit=50`; while the fetch is in flight the section shows the window's
matching rows). **Paging applies to finished rows alone**: the finished table renders one
50-row page at a time behind a quiet text **pager** under it - under the FINISHED section on
All (the EXECUTING and QUEUED sections above always render every live row in full), under the
single table on a terminal segment. The pager reads **"Prev · 1–50 of 1,240 · Next"**: the
range is the rows on screen, the total is the filter's server `total` (on All,
`executionsTotal` minus the live rows), thousands-separated and trued up by every `/state`
refresh; Prev and Next disable at their edges. The pager renders only when that total
exceeds 50 - one page needs no controls, and a short table looks exactly as it did before
paging existed. Rows fetched so far accumulate in the page component, merged with the window
by id (window wins - it is fresher) and sorted in the canonical order; the visible page is
that merged finished list's slice at `page × 50`. **Prev** - and any page whose rows are
already in hand - re-slices with no request; **Next** past the rows in hand fetches the next
page with the keyset cursor - `beforeStartedMs`/`beforeId` from the last finished row in
hand, `status=finished` when the filter is All - and advances only when it lands. A failed
page fetch surfaces the standard error toast and stays on the current page, pager in place.
An execution finishing mid-view lands at the top of Finished via its §19 event and can push the
current slice's rows down by one - the canonical order shared by window, keyset, and index
keeps the pages seamless either way. The accumulated set also **absorbs** the window's
finished rows on every window change: a `/state` refresh replaces the window wholesale, and
new finishes push old rows out of it - a row that leaves the window mid-session must survive
in the accumulated set, or the page the user is on silently loses it and every deeper page
shifts against the readout. A terminal segment whose first page fetch is still in flight
never shows the "No <status> executions" empty card - the card means the server answered
empty, not that the answer hasn't arrived. Fetched pages and the page number are view state only,
held by the page component: they reset when the page unmounts and whenever the filter
changes (each filter change starts from its own fresh first page), and are never stored or
synced. Executing and Queued are never capped or paged.
Both are naturally small (`AUTOWRIGHT_QUEUE_TTL_S`, §15, caps a wait at 120 s by default) and
both are the live rows the page exists to surface. The pill's `executionsTotal` is kept in
step by the §19 execution events (a header id the store has never seen counts as one more)
and trued up by every `/state` refresh; a §5 retention sweep announces nothing, so the count
can run slightly stale between refreshes, never wrong by more than the swept rows.

