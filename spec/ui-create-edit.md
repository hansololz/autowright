# Autowright SPEC — Create / edit flow

Part of the Autowright spec. Index and § map: [SPEC.md](../SPEC.md). § numbers are global across spec files.

## 11. Create / edit flow

Entry: New button, Edit on a detail page, or a §7/§9.2 **Fix with AI**
button on a failed execution. If no agents exist, redirect to Agents with toast "No agent
yet — add one here first. Creating and editing automations needs an AI."

**Editor layout — chat pane + review grid.** The editor is **one screen from birth to
save**: there is no separate Ask screen and no building screen. A **floating chat panel**
sits at the left edge of the content area, matching the §9 nav rail's panel language and
vertical rhythm: `clamp(340px, 26vw, 420px)` wide (420 px on wide windows, shrinking with
the window so the review grid's two columns keep room on small ones), top edge matching the
§9 rail's per-OS offset on the frameless platforms (46 px on macOS, 53 px on Windows — 12 px
below the §9 title bar's 41 px extent, matching the rail) and
bottom edge 12 px above the window
bottom (sticky — it never scrolls with the review pane), 12 px radius on all four corners,
card background with a 1 px `--border-card` border, own scroll, and a 12 px gap on its left
from the rail's 58 px reserve (the top offset clears the traffic lights / the Windows title
bar, so the panel needs no
header padding and no `no-drag` handling — it starts below the §9 drag strips, like the
rail). The review
pane's content background shows around the panel. Beside it the **review pane** holds the
Review grid (1800 px max-width, below) and scrolls independently. One gap value between
content columns: the chat pane → grid gap equals the grid's own 18 px column gap (the
review pane's left padding), while the pane keeps 30 px right-edge window padding. The chat pane is the
editor's only conversational surface — agent-mediated spec edits, questions, blockers,
failure analyses, and drafting progress all render in its thread; direct manipulation (the
spec card's Edit, grants, sync, test) stays in the cards. The §9 collapsible sidebar rule
applies unchanged; the chat pane never collapses.

**Chat pane anatomy.**

- **No header:** the pane has no header row — the thread starts at the top of the card
  and the composer carries the pane's identity (the drafting-agent picker below). No
  `CHAT` label anywhere; the thread and input make the pane self-evident.
- **Thread:** scrolling body, newest at the bottom, auto-scrolled on new content. The
  thread reads as a user ↔ agent conversation: **user** entries are the only bubbles —
  quiet, right-aligned — and **every agent-side entry renders left-aligned in the
  Claude-output style**: full-width markdown/prose blocks, no bubbles, no centered text
  anywhere in the thread. Agent-side entries split into **two visual families**, each
  with one fixed layout — no entry hand-rolls its own shape:
  - **Operation blocks** (`activity`, `rewrite`, `system`, the transient progress
    entry) — the record of what the agent **did**. Layout: a glyph in a 13 px box
    beside a one-line title, and beneath them the entry's description lines as
    `• `-prefixed bullets (bullet glyph then a 2-space gap before the text) running
    the pane's full width **flush left with the glyph —
    never indented under the title** (the activity-feed flush rule, now for every
    operation block). A bullet may close with a right-aligned duration stamp — the
    per-step durations under the activity entry kind below — while a header row never
    carries one; no other trailing decoration exists. The feed sits flush beneath the header row — no extra gap
    below the title, only the lines' own line-height. An `activity` block's header
    row (and the transient progress entry's — the same layout live) carries a small
    **3 px top padding**, so an action block's title stands slightly off whatever
    precedes it even when operation blocks chain flush; the one-line chips
    (`rewrite`, `system`) stay unpadded and keep chaining flush. An entry with no description renders the header row alone.
  - **Message blocks** (`answer`, `blockers`, `error`) — the agent **talking to the
    user**: questions, plans, blockers, failures. Layout: a header row — glyph in the
    same 13 px box beside a 13 px/600 title — then the body as full-width paragraphs at
    the body scale, flush left (no indent under the header).
  The redesign removes or replaces nothing: every entry kind keeps rendering, and a
  block once shown stays in the thread (dismissed blockers still collapse to their
  one-line summary — a collapse, not a removal). Suggested next steps render as
  icon-led `.ad-btn-pill.action` pills in the **turn action row** (below); only a
  blockers entry's Dismiss / "Apply to the spec & sync" keep their ghost/primary
  weight — they are the entry's real resolution controls, not suggestions. One
  response may land several consecutive agent blocks. Entry
  kinds (persisted shapes per §4.4; progress is transient editor state):
  - **user** — the message as a quiet right-aligned bubble (inset background, hairline
    border, ~92% max width).
  - **answer** — the agent's reply, a message block: a header row then the reply
    rendered through the shared §4.5 Markdown renderer
    (its compact `.ad-md-sm` variant — thread type scale below). The header names what
    kind of message it is, stamped on the entry at creation (§4.4 `icon` + `title`)
    from the response that produced it: a reply arriving **with** rewrites or actions
    is the agent's plan — `fa-list-check`, "The plan"; a reply the agent declared a
    question (the §8 `===QUESTION===` marker, riding the §19 payload as
    `answerKind: "question"`) — `fa-circle-question`, "Question for you" (the §8
    prompt has the agent lead such a reply with the ask, so the title's promise is
    met in the first line even when the reply also answers something); any other
    reply — `fa-message`, "From your AI". Never inferred from the text — a reply that
    merely ends with a courtesy question stays "From your AI". An entry persisted
    before the fields existed derives the plain "From your AI" header at render
    time — old threads gain the layout, nothing is dropped. **The plan lands at the
    flip:** the moment a chat job flips to "Updating the documents", its §19 `plan` —
    the prose streamed before the first rewrite marker — lands as this entry (the
    "The plan" header: rewrites follow by definition), beneath the just-settled
    deciding block and above the restarted live entry, so the user reads what the
    agent decided while the documents are still being written. The settle dedups
    against it (turn order below: no second entry; an in-place text update when a
    repair round changed the prose), and once shown it stays through every outcome —
    a cancel or a late blocker included — per the never-removed rule.
  - **activity** — a settled stage's record: **one entry per §8 pipeline stage** the job
    passed through, persisted the moment the stage finishes — mid-job when the next stage
    begins (that stage's label and feed must survive the transition, never be replaced by
    the next stage's), and for the last stage when the job ends `done`, `blocked`, or
    `failed`. Each carries the stage's §11 label (`title`) plus the stage's own slice of
    the §8 `events` feed (grouped by each event's `stage` stamp). A cancelled job leaves
    no entry for its in-flight stage — its request text returns to the input instead —
    but stages that already settled stay in the thread. **A shown block is never
    removed:** the neutral "Working on the request" stage settles on every chat turn,
    feed or no feed — the live entry displays that title from the moment
    of send (the pre-poll default), so the editor seeds the stage into the settle list
    even when the backend flipped before the first poll observed it. **And a block is
    never a bare title:** a stage whose stream left no milestones settles with its
    canned description bullet — "Choosing what to do" (Working on the request) /
    "Writing the documents" (Updating the documents) / "Building the steps from the
    spec" (Syncing the workflow) — so every block says what its phase was doing
    (buffered harnesses and early failures are how feeds end up empty), and the flip
    reads as the next block starting beneath the last, never as a replacement. Renders exactly like the live
    progress entry it replaces — the stage label kept, an **outcome glyph** in the
    spinner's 13 px box (same size, so the text never shifts when the spinner settles): a
    green check for a finished stage (every non-final stage, and the final one of a job
    that ended `done`), an amber check when the job ended `blocked` at that stage (the
    trail finished; the blockers entry beneath asks for input), a red X (`fa-xmark`) when
    it ended `failed` there — the dim single-line-ellipsized feed beneath as the
    operation block's bullets — so the full
    trail of what the agent did, stage by stage, survives the job, and a failed job never
    leads with a green check. The entry's §4.4 `outcome` field carries the status; an
    entry persisted before the field existed renders as done, and a terminal job payload
    carrying no stage at all (belt-and-braces — the backend always sets one) settles as
    one entry with the job's live stage label and the whole feed. **Per-step durations:**
    every feed bullet closes with its duration — a right-aligned quiet mono stamp in the
    §14 row-level mono size (11.5 px), formatted like the §7 duration labels ("0.4s" /
    "1.4s" / "2m 13s") — while the header row stays a pure label, never stamped. The
    values are the §4.4 `eventDurationsMs` array the editor derives from the §8
    stage-timing stamps at the moment the stage settles: the frozen forms of the ticking
    stamps the live entry showed (whole seconds settling into the precise label — live
    durations under the thread progress entry below), so no stamp appears or disappears
    at settle. The stage's time is fully accounted by its bullets, and the waiting line
    keeps its one identity — the stage's canned description bullet, exactly the line the
    live block ticked (the waiting-line rule under the thread progress entry below),
    never relabeled at settle: a stage whose first
    milestone landed a material beat after the stage began (≥ 1 s — a flip-entered stage
    starts on its first milestone and stays clean) settles that canned bullet as
    its first timed line carrying the leading gap, and for an empty feed the same canned
    bullet stands alone carrying the stage's whole span — it is the stage's only action. A
    line whose duration is unknown (`null` — e.g. a stage-stampless event from an older
    payload) and an entry persisted before the field existed render without a stamp —
    the layout is otherwise unchanged.
    Excluded from the
    agent's §8 CONVERSATION context (operational noise, §8).
  - **rewrite** — a "Spec updated." receipt, rendered exactly like a system chip: faint
    `fa-file-pen` glyph beside the chip text "Spec updated." in the system-chip role
    (11.5 px/400 `--text-faint`, the secondary role — see the thread type scale below). The §4.4 entry still stores the user's request text —
    it feeds the agent's §8 CONVERSATION summary — but the thread no longer echoes it
    (the user bubble above already shows the request), and old persisted rewrite
    entries render the same chip way. The entry keeps anchoring the draft-undo
    snapshot and the turn's out-of-sync state. The amber out-of-sync note lives off
    the entry: while the newest post-boundary rewrite's workflow is still out of sync
    (out of sync + dirty, no §8 job in flight, not viewing an old version), the thread
    renders one **derived** amber chip line "The workflow is out of sync — sync the
    steps before saving." at the thread's end, closing the turn's workflow chip group
    (chip groups under the chat choreography below) — never persisted, gone the moment
    a sync lands. **Sync now** stays on the turn action row (below), and undo stays
    there too — the restore covers the whole draft, not just the spec (Draft undo
    below).
  - **blockers** — the §8 blocker list rendered as agent output (Blockers below).
  - **system** — a quiet one-line left-aligned status chip, rendered as an operation
    block: a per-operation glyph (faint) beside the chip's text as its title, set in the
    secondary role (11.5 px/400 `--text-faint`) so receipts read quieter than the stage
    titles that anchor the feed. One chip opts out of the demotion: a §4.4 boundary
    marker keeps the operation-title role (12.5 px/500 `--text-muted`) — a structural
    milestone, not a receipt, and its explainer bullet must stay subordinate to it. No
    description bullets, with one exception: that same boundary marker renders the derived
    history explainer as a dim bullet beneath its title, and — only when an entry
    follows it — closes with a full-width 1 px `--hairline` divider rule beneath the
    group: the marker ends the history it describes, and the rule sits between that
    settled conversation and the next one. A marker that is the thread's last entry
    shows no rule — there is nothing to fence off yet; the rule appears when the next
    session's first entry lands (spacing in the thread-spacing rule below; Thread
    lifetime below). The glyph is stamped on the entry at creation (§4.4 `icon`, a
    Font Awesome class); an entry without one (older persisted threads, backend-seeded
    entries) falls back to `fa-circle-info`. The map, by operation: sync
    ("Steps synced with the spec.", "Test skipped — the steps aren't in sync with the
    spec.") `fa-rotate`; instructions ("Build
    instructions updated.") `fa-list-check`; notes ("Notes updated.")
    `fa-note-sticky`; identity ("Renamed to `<name>`.", "Description updated.")
    `fa-pen`; parameters ("Parameter "X" staged — applies when you save.", "Value for
    "X" dropped — no such parameter after the rebuild.") `fa-sliders`; concurrency
    ("Concurrency staged — applies when you save.") `fa-layer-group`; triggers (the
    trigger-op chips, "That trigger already exists.", the trigger-setup reminder under
    the TRIGGERS card below) `fa-clock`; Draft undo ("Last change undone — the rewrites
    above no longer apply.", "Nothing to undo.") `fa-rotate-left`; tests (the
    run-settled entries below) `fa-vial`; the §7 Fix-with-AI failure seed
    `fa-circle-exclamation`; the §4.4 boundary markers ("Draft saved as vN." /
    "Changes saved — no new version." / "Draft discarded." / "Created as v1." —
    backend-appended on settle, Thread lifetime below) `fa-flag-checkered`.
  - **error** — a failure entry (a failed §8 job's message, the
    Failures paragraph below), a message block: `fa-circle-xmark` red glyph beside the
    13 px/600 title "Something went wrong", the failure message as body prose beneath —
    no action pills (retrying is resending, and the composer owns that).
    Persisted like the other kinds (§4.4), so it survives a
    reload and
    reaches the agent's CONVERSATION context.
- **Thread type scale** — one scale across every entry kind, three roles (all sans;
  §14 tokens for the colors):
  - **Body prose** — 12.5 px / 1.6 `--text-2`: user bubbles, answer markdown (the §4.5
    compact variant pins its paragraphs and list items here), the blockers explainer,
    blocker Reason / How to
    fix / Details bodies, and error entry text. Markdown headings stay within
    12.5–13.5 px (the compact variant's cap).
  - **Entry titles** — one-line, beside the entry's glyph. Message-block headers are
    the thread's loudest lines at 13 px / 600 `--text` (the blockers headline, the
    answer header, the error "Something went wrong"). Operation-block titles:
    activity/progress stage titles and the boundary-marker chip are
    12.5 px / 500 `--text-muted` — these anchor the feed, so result receipts do not
    share the role.
  - **Secondary & feed** — the system and rewrite chips (11.5 px / 400 `--text-faint`;
    the chip's text is its title, demoted beneath the stage titles so receipts like
    "Description updated." never read as the next stage), and the operation blocks'
    bullets and supporting prose (the
    dismissed-blockers summary, "Previously resolved")
    is 11.5 px / 1.5–1.6, `--text-muted` or `--text-faint` by weight of the information;
    activity/progress feed lines are all 11.5 px — history lines `--text-faint`, the live
    detail line `--text-muted` (color, not size, marks the working line).
  No entry hand-picks sizes outside these roles — a new entry kind joins one of the
  three.
- **Thread spacing — one group gap.** A uniform **12 px** separates any two distinct
  groups: turns (any gap touching a user bubble), operation block ↔ message block in
  either order, two consecutive message blocks, and every gap around a §4.4 boundary
  marker — 12 px above its chip (overriding the family gap), 12 px between the marker
  group (chip + explainer bullet) and its divider rule when one renders (entry-kind
  list above), and 12 px between the rule and the entry after it. Consecutive
  **operation blocks** chain **flush (0 px)** — one response often lands several (stage
  trails, the rewrite chip, the "Renamed to …" / "Description updated." system chips),
  and they read as one continuous block, exactly like an activity entry's own feed
  lines, not as blank-line-separated paragraphs (each entry's internal line-height and
  its own top padding, where a kind has one, provide the breathing room). The transient
  progress entry is an operation block and follows the same rules: flush when it
  restarts beneath any operation-family entry — a just-settled activity trail, a
  rewrite chip, or a system chip (the turn's operation stack chains as one block) —
  and 12 px after a bubble or a message block. A boundary marker never chains flush — it is
  its own group even among operation blocks. The turn action row sits 10 px beneath
  the entry it follows — attached to the group it closes, deliberately tighter than
  the 12 px group gap.
- **Fixed internal values** (the rebuild-from-spec numbers): the thread body pads
  14 px vertical / 16 px horizontal, and the composer 12 px vertical / 14 px
  horizontal above its 1 px hairline top border; every entry header row sets glyph
  box and title 10 px apart; a message block's body starts 6 px below its header
  row; action rows space their pills 8 px apart. Inside a blockers entry: the
  explainer sits 10 px above the first blocker, each markdown body carries 3 px
  above / 8 px below, a "BLOCKER n" eyebrow 10 px above / 6 px below, the
  "PREVIOUSLY RESOLVED" list 12 px above, and the resolution row 12 px above.
- **Turn action row** — the thread's one suggestion surface: a standalone left-aligned
  wrapping pill row (icon-led `.ad-btn-pill.action` pills — the composer pills' small
  look in the §14 sans action face) rendered beneath the thread's **last agent-side
  entry**, only while no §8 job is in flight, no draft test is executing, and no old
  version is viewed. It holds every applicable pill, in this order, and hides entirely
  when none applies:
  - **Undo this change** (`fa-rotate-left`) — while the Draft-undo snapshot exists and
    its anchor is the thread's last agent-side entry. When later answer-only turns have
    landed beneath the anchor, the undo pill instead renders as its own row 10 px
    beneath the anchor — the Draft-undo anchor rule (below) is unchanged, so the pill
    always sits below everything the request changed. It never shows without a
    snapshot: a response that changed nothing offers no undo.
  - **Sync now** (`fa-rotate`) — while the workflow is dirty and out of sync, sync is
    not disabled, and no pending sync is armed; the same §8 sync call and gating as the
    BUILD card's button (this pill replaces the one the rewrite entry used to carry).
  - **Test draft** (`fa-vial`) — while the workflow is in sync, steps exist, and
    nothing is drafting; it **starts a draft test immediately** — the same run the
    test-run modal's Run test starts, using the TEST card's current setup values (the
    seeded values — drafted §8 `test_values` included — when the modal was never
    opened) — and opens the test-run modal on the live run. Unlike the TEST card's
    same-named launcher (which only opens the modal), the pill runs the test; the
    modal's setup phase stays where fine-tuning lives.
  - **Analyze failure** (`fa-magnifying-glass`) — while the draft's tracked test
    settled failed; sends the canned analyze chat message exactly like the TEST card's
    button, with the same gating.
- **Input:** pinned footer composer, two stacked rows. Top row: a full-width auto-growing
  textarea — the **ask-box pattern** referenced throughout this spec: sized to its
  content, never scrolls, no manual resize handle, Enter sends (the primary send path),
  Shift+Enter inserts a newline. The box is sized the moment it mounts (not only when the
  text changes), so the first keystroke never shifts its height. Bottom row: the
  **drafting-agent picker** on the left, a send button on the right. The picker pill
  (`name · model`, menu opening **upward** over the thread, left-aligned so it stays
  inside the pane; the pill shrinks and truncates its label with an ellipsis rather
  than overlap the send button when the agent name is long) lives in the composer — the agent is a property of the message being
  sent (it answers chat, writes the spec, generates the steps), so it is chosen where the
  message is written. The picker disables while a rewrite is in flight (a §8 sync or
  chat job — the inputs lock below).
  Otherwise picking an agent shows the confirmation toast "`<name> · <model>` now writes
  the spec and steps here." and, in edit mode, marks the draft touched. The send button is
  a quiet secondary affordance (Enter is primary): borderless, pill-height — the same
  height as the picker pill, in the §14 `.ad-btn-pill.action` sans face (an action word;
  only the picker's `name · model` metadata wears the mono face) —
  always labeled "Send", disabled while the input is
  disabled or holds only whitespace. Right of the picker — grouped with it on the
  toolbar's left, away from Send — sits **Clear chat**, an icon-only dim button
  (`fa-eraser` glyph, tooltip and aria-label "Clear chat"): it opens a confirm dialog ("Clear this
  conversation?" — body noting the
  draft itself is untouched), and on confirm empties the thread (Thread lifetime below).
  It disables while any §8 job is in flight, while a test executes, while viewing an old
  version, and while the thread is empty. Left side = conversation meta (picker, clear);
  right side = the send/cancel action alone. Placeholder "Describe the job — one sentence is
  enough." while the draft has no spec (fresh create), else "Change something, or ask a
  question…" — except when the thread's newest entry is a "Question for you" answer
block, where it reads "Answer here…" (the reply goes through the ordinary send; the
prompt reverts as soon as any entry follows the question); while viewing an old version the input is disabled with the placeholder
  "Back to the draft to edit or ask." (and while a test executes, "Wait for the test to
  finish." — the busy hint below). Placeholders stay on one line — they truncate with an
  ellipsis rather than wrap when the pane is at its narrow end (the auto-grow sizes to
  typed content only, so a wrapped placeholder would clip). **Every send starts a §8
  `chat` job** — one process, no separate create pipeline: a fresh draft's first message
  simply travels with an empty draft, and the §8 new-automation rule has the agent write
  the spec, name the automation through the `name`/`description` actions, and normally
  chain the first steps build with `sync: true`. The job carries the in-editor draft as `current`
  (spec + steps + instructions + notes), the in-editor grant arrays, and the recent thread
  (§19 `chat` — only entries after the newest §4.4 boundary marker: a settled draft
  session's conversation never reaches the agent, §8) — answers and rewrites match what's
  on screen, unsaved edits included; the
  backend adds the §8 RECENT EXECUTIONS and PACKAGES context itself, so the agent reads test and
  execution output (success and failure) without any extra ceremony. The
  response decides the outcome (§8) — one response may combine an answer with rewrites and
  actions, applied in this order:
  - an **answer** appends an answer entry (first, when rewrites follow) — unless the
    job's mid-flight `plan` (§8/§19) already landed it: the settle then appends nothing,
    updating the shown entry's text in place when a repair round changed the prose;
  - a **spec rewrite** replaces the spec exactly like a manual spec edit — out-of-sync
    marking, toast "Spec updated — the workflow is out of sync. Sync the steps before
    saving.", the full-draft undo snapshot stashed (one snapshot for the whole response —
    Draft undo below) — and appends the rewrite chip ("Spec updated.", entry kinds
    above; the toast is
    skipped when the response's actions immediately sync);
  - an **instructions rewrite** replaces the Build-instructions text like a manual
    instructions Save (same dirty gating) and appends a system entry ("Build instructions
    updated.");
  - a **notes rewrite** replaces the §4.1 notes document (never dirties the workflow) and
    appends a system entry ("Notes updated.");
  - **actions** (§8 `actions.yaml`) run after the rewrites land: `name`/`description` apply like
    the pencil edits (create: the draft's fields; edit: the immediate §19 PATCH) with a
    system entry; a `name` that collides with another automation's (case-insensitive, §4.1 -
    create: checked against the store's automation list; edit: the §19 PATCH's 422) is a
    **no-op** with the system entry "Rename to “X” skipped — an automation with that name
    already exists." (a backstop like the duplicate-trigger add below - the automation keeps
    its current name); `param_values` stages stored values (§4.2): entries naming a current
    param (name + kind, the §4.2/§5 matching rule, values coerced like `test_values`)
    land in the draft's staged map — one system entry per applied name, "Parameter
    “url” staged — applies when you save." — and the Parameters card shows the staged
    summary; when the response also rebuilds the steps, the whole map stays staged and
    re-checks after the sync lands — names no drafted param matches are then dropped
    with the system entry "Value for “X” dropped — no such parameter after the rebuild.";
    `triggers` ops edit the editor's trigger list (the TRIGGERS card) exactly like the
    §4.3 staged state a sync produces — applied in op order, indexes always meaning the
    CURRENT-triggers numbering the agent saw (an earlier `remove` never shifts a later
    op onto a neighbor; an op naming an already-removed entry is inert; a re-attach
    apply first proves the base list is still the one the agent saw — the §19
    `sentTriggers` guard under Background continuation & re-attach below), each with a
    system entry
    ("Discord trigger added." / "Cron trigger 2 updated." / "Cron trigger 3 removed." /
    "Cron trigger 1 turned off." — the leading word is the trigger's kind from a fixed
    display map, cron → Cron, time → One-time, app_start → App-start,
    discord → Discord, imessage → iMessage: display words only, never §4.3 label math —
    details live on the card, which re-labels through §19 `/triggers/preview`);
    an `add` whose entry matches an existing trigger on the §4.3 identity fields is a
    **no-op** with the system entry "That trigger already exists." (a backstop, not a
    repair round — the §8 policy has the agent answer in prose instead); ops touch only
    the entries they name — nothing else in the list moves. `concurrency` stages the §4.1
    settings (§8 — partial `{ max_parallel?, max_queued? }`): the sent keys merge over
    the editor's staged concurrency object and the CONCURRENCY card shows the staged
    values, with one system entry "Concurrency staged — applies when you save."; a key
    matching the current effective value (stored in edit mode, the 1/0 defaults in
    create) still stages — staging an explicit value is harmless and simpler than
    diffing. All staged kinds mark the
    draft touched, never out of sync, and land only at save (§4.4). **Workflow chip
    group — hold-and-flush:** a turn's chips split into two groups mirroring the page.
    The document chips (the rewrite chip, "Build instructions updated.", "Notes
    updated.", "Renamed to …", "Description updated.") land at apply time. The
    staged-change chips (parameter staged, the trigger-op chips, concurrency staged)
    are the **workflow group**: when the response arms a sync (`sync: true`, or
    `test: true` on an out-of-sync draft) the editor **holds** them and flushes them
    into the thread when that sync job settles — on **any** outcome (done, failed,
    blocked before its own entries, cancelled) and likewise when the old-version
    watcher clears the pending sync — so they sit contiguously beneath the sync trail
    after "Steps synced with the spec.", with the drop chips following them (a value
    staged this turn that the rebuild then dropped reads staged → dropped). The
    staging itself still
    happens at apply time (the cards show staged values immediately — the chips are
    receipts, and a cancelled sync never swallows one). A session that leaves the
    editor or settles while chips are still held resolves them the same way: the
    keep and settle paths that carry the session forward (Keep draft, leaving the
    editor, Save, Create) flush the held chips into the thread before its write,
    while Start over and Discard draft drop them with the session's staging
    (receipts for discarded staging never reach a later session's thread). A
    response arming no sync
    lands them at apply time right after the document chips, and when that response
    also rewrote the spec the derived amber out-of-sync line (entry kinds above)
    closes the group. The draft-undo anchor follows the flush: it re-anchors below
    the last flushed chip;
    `sync: true` arms a **pending sync**: a watcher fires it as soon as no
    §8 job or draft test is running — immediately when nothing runs (exactly as if
    the user pressed Sync now), otherwise automatically the moment the running work
    finishes. While the user is viewing an old version the watcher instead clears both
    pendings (sync and test) **silently** — no system entry, no toast; the discard is
    deliberate, since an old version must never be synced or tested;
    `test: true` arms a **pending test** that starts the moment the workflow is in sync —
    immediately when it already is, after the chained sync succeeds otherwise (`test: true`
    implies the sync when the workflow is out of sync, §8) — using `test_values` as the
    test's §19 `paramValues` (they also pre-fill the test-run modal's test-value editors;
    absent `test_values`, the test runs on the TEST card's seeded values like the
    Test-the-draft pill — drafted §8 manifest `test_values` included);
    the pending test is dropped, with the system entry "Test skipped — the steps aren't in
    sync with the spec.", when the sync fails, blocks, or is
    cancelled, or when anything else rewrites the workflow first. `undo: true` (§8:
    always alone — no rewrites or other actions beside it) runs the Draft-undo restore
    exactly like the undo row's button — same full restore, rollback chip, and toast;
    when no snapshot exists the system chip "Nothing to undo." lands instead. Grants and
    Save/Create
    are never agent actions (§8): the chat can walk the draft all the way to green, but
    permissions and the final commit stay user clicks;
  - a **blocked** job appends a blockers entry (source: chat).

  **Thread progress entry — the page's only live job surface.** While any §8 job is in
  flight (chat or sync, however started), a **transient agent-activity entry**
  renders at the bottom of the thread, styled as a left-aligned agent block: a spinner,
  the job's stage label — the §8 unified stage set, "Working on the request…" /
  "Updating the documents…" (the mid-job marker flip) / "Syncing the workflow…" — each
  job showing only the phases it runs (§8: a chat job runs the first two;
  sync opens at the third; package installs are bullets under the third, never a
  stage of their own); no title ever carries agent · model attribution —
  the composer's picker names the agent), and an **activity feed**
  beneath it — the **current stage's** §8 `events` lines as dim history (oldest first,
  each single-line with an ellipsis; the §8 per-job event cap bounds the list) above the
  live §8 `detail` line; when `detail` extends the newest
  event (same message, growing ` · N lines` count) that event shows only as the live line,
  never twice. **The waiting line has one identity:** from the moment the stage starts
  until its first milestone, the stage's canned description bullet (the per-stage map
  under the activity entry kind) holds the feed's place, ticking its elapsed from the
  stage's start — a live block never renders as a bare title, and the backend's leading
  `Thinking…` `detail` (§8) never renders in the thread: the canned line subsumes it, so
  the waiting line is never relabeled mid-tick. When the first milestone lands, the
  canned line **freezes in place** as the feed's first bullet carrying the leading gap
  when the gap was material (≥ 1 s), or leaves the feed on a sub-second gap — the one
  sub-second exception to lines settling in place, matching the settled shape (a
  flip-entered stage never shows it at all: its first event arrives with the stage).
  **Live durations:**
  every feed line above the newest carries its event's settled duration (the span to the
  next milestone — §8 stage timing) as a right-aligned mono stamp (the same style the
  settled activity entry uses, entry kinds above); the newest line — the live `detail`
  line when present, else the newest event, else the ticking waiting line above — ticks
  its own elapsed instead, so the live block always
  shows exactly one ticking stamp. The stage title row beside the spinner is never
  stamped — the newest line's tick is the stage's pulse, and a title stamp would only
  double it. The
  ticking values advance client-side once per second from the §19 `stageTimes` and event
  stamps and read whole seconds (the §7 QUEUED FOR treatment — the 700 ms poll is never
  the tick source, and tenths would only flicker); at settle they freeze into the
  activity entry's persisted durations in the precise label ("12s" settles as "12.3s").
  The feed and detail lines render as the operation-block bullets
  (`• `-prefixed, 2-space gap after the glyph) and run the pane's full width, flush left with the
  spinner — only the stage label sits beside the spinner, the lines below are not
  indented under it. The entry is **derived editor state, never a persisted thread entry**
  (§4.4 `chat` never carries it): it appears when the job starts and disappears when the
  job settles. When the job's stage changes mid-flight, the finished stage settles
  immediately into the thread as a persisted **activity** entry (its label plus its
  events, entry kinds above) and the live entry restarts with the new stage's label over
  an empty feed — every stage's title and details are preserved, none replaced by the
  next. When the job ends, the outcome lands as ordinary thread entries in the live
  entry's place — led by the final stage's **activity** entry, so the label and detail
  lines outlive the spinner, which settles into a
  same-size outcome glyph (entry kinds above). The thread
  auto-pins to the bottom when the entry appears; while the feed grows it follows only
  when the user is already at (or near) the bottom — a user who scrolled up is never
  yanked back down. Meanwhile the composer keeps its two-row shape — the textarea stays
  visible but disabled, the agent picker stays in place — and in the toolbar row the send
  button is replaced by a **Cancel** button at the same pill height
  (`DELETE /drafts/{jobId}`; cancelling a chat job returns the request text to the
  input for editing, sync-cancel semantics under Dirty gating below). A composer cancel
  (the button or Esc) also moves focus to the re-enabled input with the caret at the end,
  and the restored text sizes the box through the ask-box auto-grow — editing the request
  picks up exactly where it left off. Pressing **Esc**
  anywhere on the page fires the same cancel while a §8 job is in flight — it is a
  keyboard shortcut for this Cancel button, nothing more (it never cancels a draft test,
  whose Cancel lives in the TEST card and the test-run modal), and it yields to surfaces that own Esc
  while open: the modal stack and the §9.3 developer log overlay. **Settles cancel;
  leaves don't:** Discard draft and Start over cancel an in-flight chat job (client-side,
  beside the §19 owner cancel), but merely leaving the editor — navigation anywhere,
  Keep draft, closing the window — never does: the job keeps building in the background
  (§19 background continuation; its lifetime is the draft's, the same rule as the draft
  test) and re-entering the editor re-attaches to it (Background continuation &
  re-attach below). When a settle path does cancel, the pending user entry stays in the
  persisted thread with no composer left to return the request to, so every settle flush
  that cancels an in-flight chat job appends the system chip "Edit
  stopped — the spec is unchanged." (the composer cancel's toast copy, icon `fa-ban`) right
  after it, so a kept thread never resumes on a request that looks unanswered and a later
  §8 CONVERSATION context sees the turn was cancelled. Every other place on the page
  shows only static text while a job runs — no second spinner, live `detail` line, or
  Cancel anywhere. The draft **test** is not a §8 job and never appears here: while a test
  is executing the input stays, disabled with the hint "Wait for the test to finish." (a
  rewrite would pull the workflow out from under the running test), and the test's live
  controls (progress, Cancel, Open test) stay in the TEST card and the test-run modal.
- **Create empty state:** headline "What should Autowright do for you?" over the thread
  area, the sub-line "Describe the job in plain words. Your AI writes it as scripts — you
  review everything before it executes." beneath it, then an "OR START FROM AN EXAMPLE"
  eyebrow over icon-led example chips (fa icon +
  label; accent-tinted border/background on hover, 1 px press-down on :active): Track manga
  chapters (fa-book-open) / Back up a folder every night (fa-box-archive) / Email me a
  weekly report (fa-envelope) / Watch a product's price (fa-tag) / Tidy my screenshots
  folder (fa-broom) / Log ideas from Discord (fa-brands fa-discord — the one brand icon;
  the others are fa-solid). The Discord chip promotes the §4.3 Discord trigger: its text
  describes a message-triggered automation. Clicking a chip fills the input (it never sends). Footer reassurance
  line: "Your AI writes the steps — Autowright still executes everything on this Mac."
  Both empty states render only while the thread is empty: a kept thread (Thread
  lifetime below — after an in-session Start over, or when the slot's draft resumes)
  shows the thread instead, the composer placeholder ("Describe the job — one
  sentence is enough." while no spec) carrying the prompt. **A new automation always
  opens on this empty state:** entering the create flow when the pending slot holds
  no draft to resume — and no building or held §19 job (a slot that owns one is a
  session to resume, never cleared over) — discards any leftover slot thread (the §4.4
  fresh-entry clear) — a settled session's conversation never replays over the
  suggestions.
  Edit-mode empty state (no stored thread): "Ask anything, or describe a change — your AI
  answers here and rewrites the spec when you ask for changes."
- **Thread lifetime:** the thread **outlives the draft** (§4.4 thread lifetime; §5
  `chat.jsonl` at the container root; §19 `GET/PUT /chat/{owner}`): the editor loads it on
  open, persists it with its own debounced PUT (independent of the draft persist — a pure
  Q&A keeps no draft but still keeps its thread), and **settling the draft (discard, save,
  Create, Start over) keeps the thread** — the settle endpoint appends the §4.4 **boundary
  marker**, an ordinary system chip (`fa-flag-checkered`, `boundary: true`; "Draft saved as
  vN." / "Changes saved — no new version." / "Draft discarded." / "Created as v1.") that
  splits the thread into history and the current draft session. The marker is the one
  system chip with a description bullet: beneath its title it renders the derived (never
  persisted) explainer "The messages above are from that draft — your AI no longer
  reads them." — the reader learns in place that the history above belongs
  to a saved or discarded draft. It is also the one chip with chrome: the fixed gaps
  around it and — once the next session's first entry lands — the divider rule beneath
  its group (entry-kind list and thread-spacing rule above) make the split between
  conversations visible at a glance. Everything at or before
  the newest marker stays visible in the thread but never reaches the agent (§8
  CONVERSATION clips there — the composer sends only post-boundary entries), and the
  marker stamps open blockers entries `dismissed` (they describe a settled draft, so they
  collapse to their summaries and stop counting as open clarifications). **History is
  inert**: entries at or before the newest marker offer no actions — the settled draft
  they belonged to can't be acted on. A history blockers entry renders as its dismissed
  one-line summary whatever its stored flag says (belt-and-braces over the marker's
  dismiss stamp), and the turn action
  row never renders when the thread's last agent-side entry is a boundary marker (no
  Undo/Sync/Test/Analyze pills dangle under a settled session — they return with the
  next current-session entry); the rewrite entry's out-of-sync note likewise anchors to
  the last **post-boundary** rewrite only. Create migrates
  the pending slot's thread onto the new automation, so the conversation continues on its
  edit page; the settle paths that stay in the editor (Start over) or re-enter it in edit
  mode (reopening after a discard or save) show the kept thread with the marker as its
  last entry. **Fresh create entry clears the slot thread:** opening the create flow when
  the pending slot holds no draft to resume discards any leftover slot thread — the
  editor holds the stored-thread merge until the §19 `GET /draft/pending` answer, and
  with nothing to resume it drops the fetched thread and PUTs `[]` (unlinking
  `chat.jsonl`) instead of rendering it — so a new automation always opens on the create
  empty state; the slot thread survives entry only beside a kept draft **or a building
  or held §19 job** (the `GET /draft/pending` `job` ref counts as something to resume —
  Background continuation & re-attach below). The
  thread is otherwise deleted only by **Clear chat**, with its automation, or by the §9.1
  discard-and-start-new confirm (which clears the pending slot's thread right after the
  discard, so the fresh create flow opens with an empty thread) — never by any other
  settle.
  Any settle-armed editor write (the settle flows await the in-flight thread PUT, exactly
  like the draft's `putInFlight` rule) lands before the marker, never after it. The
  thread progress entry is transient editor state,
  never persisted. **Clear chat** (the composer button above) empties the thread only:
  the thread PUTs as `[]`, which unlinks §5 `chat.jsonl`. It also
  clears the Draft-undo snapshot (its anchor row leaves with the thread — a dangling
  snapshot would allow an invisible undo, Draft undo below) and takes open blockers
  entries with it; the draft documents, the dirty/out-of-sync state, and the session's
  "Previously resolved" list are untouched, so no sync or save gate is bypassed.

**Background continuation & re-attach.** A §8 job's lifetime is the draft's, never the
page's — the same rule the draft test already follows, so everything the editor starts
survives leaving it and dies when the draft settles. Leaving the editor while a job is
building (navigation, Keep draft, closing the window) detaches the UI and nothing more:
the job keeps building in the background (§19 background continuation) and no "Edit
stopped" chip is appended — only the settle paths cancel. Editor-armed state does **not**
travel: a pending sync or test that hasn't fired yet is dropped by the leave (the
persisted out-of-sync state and the Sync now button remain, so nothing dead-ends — and
nothing is rebuilt while the user is away), and a background-settled response's own
`sync: true` / `test: true` actions arm only when its outcome is applied on return —
apply points are editor-owned, so the pipeline pauses between calls until the user is
back. Re-entering the editor (either mode) reads the owner's §19 `job` ref, after the
stored thread and draft load and before any queued send (the Fix-with-AI send waits for
this reconciliation like it waits for the thread), and reconciles:

- **Building** — the editor re-attaches its poll and rebuilds the live progress entry at
  the job's current stage. Stages that finished while away settle into the thread first
  as ordinary activity entries — each stage's slice of the stage-stamped §8 `events`
  feed, the seeded "Working on the request" stage and the canned bullet for an empty
  slice included — deduped by stage title against the current turn's entries (those after
  the thread's last user entry), so a stage that settled live never lands twice; a §19
  `plan` that streamed unobserved lands its "The plan" answer entry exactly as at a live
  flip (skipped when it already landed). From there the inputs lock, the composer's
  Cancel, and every other in-flight rule apply as if the editor had never been left.
- **Settled (a held outcome — done, blocked, or failed)** — the editor first lands the
  stage trail exactly as above, then applies the whole outcome exactly like a live
  settle — same entry order, same apply order, same hold-and-flush — and then consumes
  the job (§19 ack; the editor acks every settled job it applies, live settles exactly
  the same way — an unacked outcome would resurface as held on the next entry), so a
  crash between
  apply and ack re-applies on the next entry instead of losing the outcome (rewrites are
  full-document replacements — a re-apply costs at worst a duplicate receipt chip).
  Actions the apply arms (a chained sync, a pending test) fire under the same watcher
  gating as a live session, evaluated with whatever is running at return time — a draft
  test that outlived the visit included. One guard is stricter than a live apply:
  `triggers` ops apply only when the editor's base trigger list equals the job's §19
  `sentTriggers` echo — the list the agent's indexes refer to; on any difference (a §9.2
  trigger edit while the job ran unattended) every `triggers` op in the response is
  dropped with the one system chip "Trigger changes dropped — the triggers changed while
  your AI worked. Ask again." (`fa-clock`, like the other trigger chips). A live apply
  never hits this: the inputs lock keeps the base list still.
- **No job ref, but the thread's current session ends on an unanswered user entry** — the
  job vanished without a trace (a backend restart lost the in-memory record, §19): the
  editor appends the "Edit stopped — the spec is unchanged." chip after the orphaned user
  entry, so the thread never resumes on a request that looks unanswered.

**The first build on Review.** A fresh draft's first chat message is an ordinary §8 chat
turn — the new-automation rule: the chat job lands the spec rewrite, the `name` /
`description` actions, and normally arms the chained sync that builds the steps, with the
same apply order and hold-and-flush choreography as every other turn. There is no create
job and no separate drafting state — while the first turn runs:

- **Title row** — name shows the placeholder "New automation…" until the draft holds a
  spec, then the spec's `#` title as the provisional name; the response's `name` action
  replaces it (the §8 new-automation rule has the agent set one). The Start over ghost
  (disabled while a §8 job runs — the inputs lock below; the running job's only live
  control is the composer's Cancel) deletes the pending slot's draft (the thread stays, behind
  the "Draft discarded." boundary marker — Thread lifetime above; the editor refetches it
  so the marker shows), and returns the editor to the create empty state with the
  description back in the input.
- **Review cards** — while a §8 job is in flight and the draft holds no steps yet, the
  right-column cards (steps, triggers, parameters, packages) show static placeholder
  lines: plain text, no spinner and no stage label, one line per card — "Steps appear here
  once the build finishes." / "Triggers appear here once the build finishes." /
  "Parameters appear here once the build finishes." / "Packages appear here once the
  build finishes.". The spec card keeps its ordinary states: empty until the chat settle
  lands the rewrite, then rendered — readable and editable — while the chained sync
  builds the steps.
- **Live progress** — the thread progress entry (Input above) is the only live drafting
  surface: the §8 stage labels with the activity feed beneath (recent §8 `events` as dim
  history over the live `detail` line), so a minutes-long call never looks stuck and web
  reads / retries stay visible. No detail (a non-streaming harness) leaves just the
  stage label. The BUILD and TEST cards never move during the first turn: the chat job,
  the chained sync it arms, and the sync's landing all keep BUILD on its quiet one-line
  row (the static "Syncing the steps with the spec…" text while the sync is armed or
  running — never a spinner or dot; BUILD card below) and TEST on its
  one-line gate text (BUILD card and TEST card below), so the right column's spacing holds
  from send to done and the live progress is read in the thread, never in the cards.
- **Failures** — a `failed` job means a harness error or crash (§8: a validation
  double-failure never ends `failed` — it settles `blocked` with diagnosed blockers, handled
  under Blockers below). A failed chat job renders in the thread as a red-tinted error
  entry with the §8 failure message — the user resends or rephrases from the composer. A
  failed chained sync leaves the landed spec with the workflow out of sync — the BUILD card's
  Sync now rebuilds the steps (an empty-steps draft can always rebuild; never a dead end).
  The editor's job poll tolerates transient fetch errors: it keeps the job tracked
  and gives up (with the failure entry) only after three consecutive poll failures.
- **Saving** — blocked while any §8 job is in flight (Dirty gating below); a create draft
  cannot save until steps exist and are in sync.

**Blockers.** When a §8 job ends `blocked`, the
blockers render as **one thread entry** — never a modal, never inline in a card — a
message block headed by an amber `fa-ban` glyph (the block icon) beside the 13 px/600
headline. Headline:
"Your AI hit a blocker" ("Your AI hit N blockers" when several); a job carrying
`diagnosed: true` (§8 build-diagnosis blockers — the build failed validation rather than
the agent refusing) instead headlines "The build failed — your AI suggests these fixes";
an entry whose blockers are all §8 `kind: user-action` instead headlines "Your AI needs
you to do something first".
Beneath it an explanatory line at the body scale, flush left (a message-block
paragraph, never indented under the header), by source:
chat — "Reply below — your answer is sent back and the spec is rewritten."; sync —
"It couldn't sync the steps with the spec." (user-action entries drop the source explainer —
the blocker text itself says what to do; entries persisted by the removed create pipeline —
source `spec` or `steps` — render with the chat and sync explainers respectively). The blockers themselves render as **agent output, not editable cards**:
per blocker, the **Reason** / **How to fix** / **Details** texts render through the
shared §4.5 Markdown renderer (compact variant, thread type scale above) under small
eyebrow labels — install instructions read as
prose and download links are clickable — and only when the list has several blockers does
each block carry a "BLOCKER N" eyebrow header. There are no editable fields: the user
answers through the composer like any other message. Each blockers entry closes with a
left-aligned resolution row (ghost/primary buttons — the entry's real controls, exempt
from the turn action row's pill styling) — a quiet **Dismiss** plus, by source and kind:

- **Chat** — the clarification case: no primary button. The
  user replies in the composer; **sending any message auto-dismisses the entry** (the
  question is answered — stated gating below). The reply goes out as an ordinary chat
  message — the thread
  already carries the blocker entry into the agent's §8 CONVERSATION context, so the
  agent rewrites with the answer in hand (a fresh draft's blocked first message works
  the same way — the reply completes the request through the CONVERSATION context).
- **Blocked `sync`** — primary **"Apply to the spec & sync"**
  (disabled while any §8 job is in flight or an old version is viewed; Dismiss is never
  gated). It writes each blocker into the
  in-editor spec under a `## Constraints & resolutions` section (created on first use,
  extended after), one bullet per blocker — "`reason` — `fix`" — then runs a §8 `sync`
  against the amended spec and the thread progress entry re-enters "Syncing the workflow"
  (the BUILD card shows its static syncing line — a sync never animates a card). The
  resolutions live in the spec document itself, so they survive later edits and syncs and
  version like any spec text. If the rebuild blocks again the new entry carries a muted
  "Previously resolved" list of this session's earlier resolutions, so a fix that didn't
  take is visible.
- **Any blocker with §8 `kind: user-action`** — the Mac isn't ready, the automation is
  fine: the entry offers **Dismiss only** (never "Apply to the spec & sync", even on a
  sync block — there is nothing to amend). The blocker body carries the agent's
  instructions (what to install or start, why, a clickable download link, the offer of
  step-by-step help); the user acts on them, then replies or re-runs. A mixed entry
  (user-action and ordinary blockers together) keeps the source's primary button — it
  applies only the ordinary blockers' resolutions.

Auto-dismiss on reply: sending a message from the composer marks open
**chat-source** blockers entries dismissed — the reply answers the clarification
(deliberately even when the message is unrelated: Dismiss is non-destructive and the
entry stays readable as its one-line summary). Sync entries stay open — their
Apply button remains useful until a sync lands.

Dismiss collapses the entry to a one-line muted summary ("N blockers — dismissed";
singular "1 blocker — dismissed"; led by a faint `fa-ban` glyph, left-aligned like all
agent output) and, for
sync blocks, leaves the workflow out of sync with the spec editable and the BUILD card
showing out of sync. A completed sync collapses any pending blockers entry the same way —
its blockers describe steps that no longer exist. No automatic loop cap — the cycle is
user-driven and Start over/Dismiss always exits.

A blocked job whose payload carries `draft.notes` (the §8 blocker response's optional
`notes.md`) applies the notes exactly like a chat notes rewrite — the "Notes updated."
system chip lands right after the blockers entry, and notes never mark the workflow out
of sync (§4.1). Everything else the blocked job leaves untouched.

**Review.** 1800 px max-width page. Title row: name (single line, shrinks with ellipsis so a long name never pushes the
buttons out of the window), version dropdown (edit mode; §4.4 rows — the current version is
only an inert header, never a selectable option; older rows carry the delete-version trash
affordance, which the Draft row and that header never show), Start over ghost
(edit: "Discard draft"), a "Keep draft" ghost (edit mode only, rendered once the draft is
touched or a stored draft exists — leaves the editor on the §4.4 draft-keep path, toast
"Draft kept — resume it from this automation anytime."), primary Create/Save — labeled "Create automation" in create mode,
"Save as vN+1" in edit mode, and "Restore vX as vN+1" while viewing an old version. Save
and Create send the chat-staged `param_values` map and the staged `concurrency` object
beside the draft (§19) so staged values
land with the version; a save whose versioned content is unchanged (only staged
values/triggers/concurrency/grants moved) still goes through the same button and endpoint — the
backend applies the operational state without minting a version (§4.4), announced by
the toast "Changes saved — triggers and values updated, no new version needed."
(recognized by the response returning the unchanged version number) — and a
touched-but-in-sync draft (staged changes only) saves without any sync. The title is the plain automation name in both
modes — never an "Edit …" framing. It is editable in place: a small pencil sits beside it,
always visible on this page (no hover reveal — `.ad-title-rename.always`); clicking the
pencil — only the pencil, never the title text itself — swaps the title to a single-line
input holding the draft's name; Enter or blur
commits, Esc cancels, and a blank result keeps the old name. A result that collides with
another automation's name (case-insensitive, §4.1) doesn't commit: the input stays open with
the inline error "An automation named `<name>` already exists - pick a different name." (red
input border, clears on typing - the §12 agent-form treatment). Create mode guards
client-side against the store's automation list; edit mode surfaces the §19 PATCH's 422
identically. Create mode: a rename updates
the draft's `name` — it persists with the §4.4 pending slot and lands on Create. Edit mode: a
rename applies immediately through the §19 PATCH —
it is independent of the draft (Discard draft never undoes it; the vN+1 draft doesn't carry a
name). Either way a rename never marks the workflow out of sync or gates Save: name is
user-owned identity (§4.1), not versioned content. The rename affordance hides while a
drafting/sync/agent-rewrite job is in flight and, in edit mode, while viewing anything but
the draft in the Version menu (Restore never renames). In create mode the usual
provisional-name flow (spec `#` title, replaced by the response's `name` action) still
runs — renaming is available whenever no §8 job is in flight.
Lede line, under the title: the automation's `description` (§4.1). (The drafting-agent picker
lives in the chat pane composer, not here.) The description is editable in place with the same
pattern as the name (always-visible pencil as the only click target, single-line input,
Enter/blur commits, Esc cancels) and the same gating (hidden while a job runs or while
viewing an old version). The lede row is height-stable: the rendered text and the in-place
input live in one fixed-height row, so entering or leaving description edit never shifts the page
below. A
blank commit clears the description (it is optional); with no description the line shows the
muted empty state "No description yet — press the pencil to add one." Create mode: until the
draft holds a spec, the lede instead reads the static drafting lede "Read what your AI wrote. Change
anything — nothing executes until you create it."; once a spec lands it becomes the
editable description (the §8 `description` action normally fills it on the first turn). Edit mode: a description edit applies immediately through the §19 PATCH (like
the name — independent of the draft); create mode: it updates the draft's `description`, persists
with the §4.4 pending slot, and lands on Create. Sync never touches name or description (§8: both
are create-only manifest keys). When an execution is live during an edit, a cyan pulsing banner
shows: "An execution is happening right now on vN. Saving won't interrupt it — that execution finishes on vN.
vN+1 takes over from the next execution (`<short label of the next trigger>`)." Sections (left column: spec, notes,
agents, secrets, instructions, framework — the spec and the agent's working notes on top, the
grant cards under them, the standing-rules cards last: build instructions second-last, the
read-only framework reference closing the column; right column: the BUILD card, then the TEST
card, then steps, triggers, parameters, concurrency, packages). Motion on this page follows §14: every collapsible card
animates open/closed through the Collapse primitive — the body **and** the collapsed hint,
which hand off as a crossfade per the §14 collapsible motion (content fades while the rows
resize; open decelerates at `--t-enter`, close accelerates at `--t-exit`) — never clipped
text or two competing height animations; **every** collapsible card header on this page (spec, build
instructions, notes, agents, secrets, framework, packages) follows the framework-card pattern:
the whole header row toggles the card and is an `.ad-hover-row` hover surface (no card is
ever held open by a manual edit: the three document editors open in the document-editor
modal below, over the page, never inside the card). The step rows and the agent/secret checklist rows are `.ad-hover-row`
surfaces too. The card-header action — one **Edit** button on the spec, notes and build-instructions
cards — is a compact borderless muted text button at the small text-button size, never a
bordered or filled box: chat is the primary way to change these documents, so the manual
control stays quiet (the document-editor modal's Cancel / Reset to default, faint, and
Save, link-styled in accent, keep the same faces). Its line box is
tightened (line-height 1) so it never exceeds the eyebrow line: every card header — with or
without an action, open or collapsed — is exactly the framework card's header height. The six
left-column cards render through **one shared card template** (header row, collapsed hint,
body top-hairline), so the treatment cannot drift per card; the collapsed hints and the
in-card empty states share one text style (11.5px/1.5 faint sans) **and one left edge**
(the card's 18 px inset plus the caret column, flush with the eyebrow's first character), so an empty card's text stays put when the card opens — a
card's description never changes size or position between its collapsed and open states.
The collapsed line is **status-aware**: a card holding content shows a one-line preview of
that content (single line, ellipsized — notes and build instructions show their first
meaningful text line with markdown markers stripped; agents shows the enabled agent names,
secrets the allowed secret names, packages the package names, each " · "-joined), while an
empty card shows its explainer sentence — the explainer teaches exactly when there is
nothing to preview, and a collapsed filled card reads as a summary of this automation
instead of repeated manual text. Two cards always show their explainer: the spec card (its
first line would duplicate the page title) and the framework card (a static built-in
document). Rendered-markdown card bodies
(spec, build instructions, notes, framework) share **one markdown body wrapper** too: same
padding, same 440 px max height with inner scroll (§14 overlay scrollbar), same full-bleed
table allowance — markdown looks identical in every card. Clicking a header
action never toggles the card it sits in;
in-card notices (grant warnings, failed-test status) and the expanded test-parameter
editors enter with
`.ad-anim-item`; thread entries enter the same way:
- **Spec** — collapsible card (fully-clickable header row like the other cards; defaults open on create — it is
  the drafting surface — and on edit; the Edit button + body hide when collapsed; collapsed,
  a faint one-line hint shows in their place — "What the automation should do, in plain words. The AI regenerates the steps from
  this document when it changes." — and clicking it expands the card, same as the other
  collapsed-section hints on this page). Open, the header is the same bare row as every other
  card — caret + `SPEC` eyebrow + the Edit button, no inline subtitle; the explanatory line
  lives only in the collapsed hint. Editable as markdown-ish text (`#`, `##`, `-`,
  plain ↔ h1/h2/li/p blocks) in the **document-editor modal** (below); the card body is the
  view state only, rendered through the shared §4.5 Markdown renderer, sized to its content
  under the 440 px max height with inner scrolling.
  Scrollable card bodies use no edge-fade mask — content clips plainly at the padding edge;
  their scrollbars follow the §14 overlay scrollbar style (trackless thin thumb). The card carries **no ask box** —
  agent-mediated edits happen through the chat pane: a §8 chat rewrite (the chosen drafting
  agent — the automation's agent, falling back to the default agent — receiving the
  in-editor draft and grants) replaces the spec and marks the workflow out of sync exactly
  like a manual spec edit (toast "Spec updated — the workflow is out of sync. Sync the steps
  before saving."), and the BUILD card's "Sync now" rebuilds the steps later; while
  the chat job is in flight the Save hint shows its live §8 stage title ("Working on the
  request…" / "Updating the documents…"), and cancelling it
  from the composer's Cancel button leaves the draft untouched (toast "Edit stopped — the
  spec is unchanged."). On failure the §8 error renders as a thread error entry; a `blocked`
  outcome renders a thread blockers entry (source: chat) — either way the draft is
  untouched, except a blocked payload's `draft.notes` (§8 blocker notes), applied like
  any notes rewrite (Blockers above). Manual spec/instruction edits are mutually exclusive (one edit at a time), and
  both are locked while a chat/sync job runs (inputs lock below). Sending a chat
  message or starting a sync while a manual spec / build-instructions / notes edit
  holds unsaved changes first asks through the editor's discard confirm — the same
  alert the document-editor modal's own Cancel / Escape / backdrop raise (below):
  ("Discard your spec edits?" / "Discard your instruction edits?" / "Discard your
  notes edits?", body naming the editor whose text is lost, confirm label "Discard
  edits"): confirming discards the unsaved edits and the send or sync proceeds;
  cancelling aborts it, with the composer text kept. An open editor holding no
  changes never asks; it closes silently as before.
- **Draft undo** — one-level **full-draft snapshot** per agent request: when a chat
  response changes the draft, the editor first stashes the draft **whole** — spec, steps,
  parameter definitions, packages, triggers, the staged `param_values` map (§4.2), the
  staged `concurrency` object (§8), the
  drafted test-value map (`testValues`, test-setup section below),
  build instructions, notes, and the dirty flag
  of that moment (an answer-only response leaves the existing snapshot untouched; grants
  and name/description are user-owned, never agent-rewritten, and stay out — chat-staged
  trigger ops and values are draft state, so the restore covers them). One ghost **Undo**
  restores it all, so the draft looks **exactly as it did before that request** — including
  steps a chained `sync: true` action rewrote, which is why a completed sync does **not**
  clear the snapshot. The Undo is the quiet **"Undo this change"** pill on the **turn
  action row** (Thread spacing above): an `.ad-btn-pill.action` (the
  composer pills' small look in the §14 sans action face — an action phrase, not
  metadata, so never the mono face) led by a rotate-left glyph — first on the row, the
  escape hatch ahead of the suggested next steps. Its position follows
  the **last** thread entry the request produced (the snapshot's anchor): the response's
  final rewrite/system chip — doc rewrites, the "Renamed to …" / "Description updated."
  chips, and the staged parameter/trigger/concurrency chips included — and, when a sync lands while the snapshot exists (chained or manual),
  the anchor moves below that sync's "Steps synced with the spec." / "Notes updated." /
  drop chips and any workflow-group chips the sync's settle flushed (hold-and-flush
  above), so the pill always sits **below everything the request changed**. It is
  deliberately a row-level pill, never an action inside the
  rewrite chip: the restore covers the whole draft, not just the spec. The pill
  is
  the page's only undo affordance; it renders only while the snapshot exists and hides
  while any §8 job is in flight, while viewing an old version, and while a test executes.
  The restore is also agent-reachable: a chat response may carry the §8 `undo: true`
  action ("undo that" typed at the composer), which the editor executes identically —
  "Nothing to undo." when no snapshot exists.
  Restoring puts every
  snapshotted field back, clears the snapshot, toasts "Last change undone.", and appends
  the system entry "Last change undone — the rewrites above no longer apply." — the thread
  persists (§4.4), so the agent's §8 CONVERSATION context learns the rollback and never
  assumes its earlier rewrites still stand. The
  snapshot is single-level — each new draft-changing response replaces it — and it clears
  on any manual document Save (spec, build instructions, or notes — an undo would silently
  destroy the newer manual work), on a repair-block spec amend, on Clear chat (the anchor
  row leaves with the thread), and on loading a version
  from the Version menu. It lives only in
  editor state: it is not part of the serialized draft and does not survive leaving the page.
  There is deliberately **no multi-level revert history**: chat can walk any change back, and
  durable rollback is the version menu.
- **BUILD INSTRUCTIONS** — collapsible card sitting second-last in the left column, directly
  above the Framework-instructions card (the two standing-rules documents close the column
  together); holds the §4.1 `instructions` free text; its Edit button opens the
  document-editor modal (below); defaults collapsed in create and edit mode alike (standing rules are rarely touched);
  collapsed with content it shows the first-rule preview (status-aware rule above); empty, the
  explainer: "Standing rules your AI follows every time it writes or edits this automation."; the view state renders the text as markdown (same renderer as the Spec and
  Framework-instructions cards), first prefixing every bare line — one that starts no markdown
  block (heading, list item, table row, code fence) and sits outside any fence — with "- " so
  plain one-rule-per-line text still renders as a bullet list instead of collapsing into one
  paragraph; the view caps at the Spec card's 440 px max height and scrolls internally
  (§14 overlay scrollbar) past it, so a long rule list never swallows the column; the
  modal's edit placeholder "Markdown — one rule per line: “Prefer
  Python.” “Never delete files — move them to the Trash.”", empty state "No instructions yet —
  press Edit to add standing rules." While editing, a ghost **Reset to default** button sits
  left of Cancel in the modal's toolbar: it fills the editor with the app's current §8
  `default-build-instructions.md` text (from `GET /instructions`), and is disabled while the
  editor already holds that text (or the file hasn't loaded yet). It changes only the unsaved
  draft — Save applies it like any manual instruction edit (same dirty gating and toast),
  Cancel discards it. In
  create mode the card arrives pre-filled with the app's default best-practice rules (§8) —
  edit or delete them freely before saving.
- **NOTES** — collapsible card below the Spec card holding the §4.1 agent-owned notes
  document; bare header like the other cards; collapsed with content it shows the
  first-line preview (status-aware rule above). View state renders the markdown (shared §4.5 renderer, same
  max-height + inner scroll as the spec card); Edit opens the same document-editor modal as
  Build instructions (Cancel / Save in its toolbar) so the user can prune stale or wrong lines —
  but the document is normally agent-written: §8 chat and sync responses may carry a
  `notes.md` rewrite, which replaces the text and lands a quiet "Notes updated." system
  entry. A notes change (manual or agent) marks the draft touched but **never** marks the
  workflow out of sync and never gates Save (§4.1: notes are advisory input to the next
  sync). Defaults collapsed; collapsed-empty hint and in-card empty state: "No notes yet —
  your AI records what it learns (page quirks, dead ends, fixes) as you build and test." Notes
  version with the automation and ride drafts and §5.1 archives like spec and
  instructions.
- **Document-editor modal** — the one editing surface for the three documents. Pressing Edit
  on the Spec, Notes, or Build-instructions card opens it over the page; the card stays in
  its view state behind the backdrop, and no card body ever turns into a textarea. It is the
  §9.2 step-script modal's code pane on its own: a §14 `Modal` card `min(860px, 92vw)` wide,
  `overflow: hidden`, no header row and nothing that scrolls the card as a whole, on the
  `--bg-code` ground. Its height is fixed for the life of the open modal — `clamp(440px,
  <toolbar + footer + the opened text's lines at the editor's 12px/1.65 mono rhythm + its
  padding + six spare lines>, 82vh)` — so a short document gets a card that fits it with room
  to write and a long one caps at the viewport; text that outgrows the frame scrolls inside
  the editor and never resizes the card. A fixed 44 px **toolbar** (hairline bottom border)
  carries, left, the faint mono eyebrow naming the document ("SPEC" / "NOTES" / "BUILD
  INSTRUCTIONS") followed by the document's §4.1 version-folder filename in dimmer mono
  (`spec.md` / `notes.md` / `instructions.md`) and, right, a live "`<n>` lines" count of the
  editor's current text ("1 line" in the singular, "0 lines" for an empty editor; a trailing
  final newline is not counted) and the control cluster: for build instructions the ghost
  **Reset to default** (rule above), then **Cancel** (faint) and **Save** (accent link,
  disabled while the text equals what the modal opened with). There is no ✕ — Cancel is the
  close. Below the toolbar the **editor** is one textarea filling the pane at full height:
  transparent on the pane's ground, borderless, no focus ring (the card is the focus),
  12px/1.65 mono `--text-2` (the step-script modal's rhythm), soft-wrapping, padded 14 px top, 18 px left and bottom, and a
  28 px right inset so no line runs under the thumb; it is its own §14 overlay-scrollbar pane
  with the thumb inset to `right: 6px` like the step-script modal (same 12 px corner
  reason). It is focused on open with the caret at the end of the text. Placeholders: spec
  "Markdown — what the automation should do, in plain words."; notes and build instructions
  keep the placeholders their cards named above. A 36 px **footer** (hairline top border, the
  cards' 11.5 sans muted hint style) states what Save does — spec: "Saving rewrites the
  steps to match the new spec."; build instructions: "Saving marks the workflow out of sync
  — sync the steps before saving the automation."; notes: "Notes guide the next sync —
  saving never marks the workflow out of sync." **Save** applies the document exactly as
  the cards' Save did (spec: the text parsed back to blocks, dirty, toast "Spec saved — the
  workflow is out of sync. Sync the steps before saving."; instructions: dirty, toast
  "Instructions saved — the workflow is out of sync. Sync the steps before saving."; notes:
  touched only), clears the draft-undo snapshot, and closes the modal; ⌘S / Ctrl+S while
  the modal is open saves too (swallowed with no effect while Save is disabled). **Cancel**,
  Escape, and a backdrop click all close the modal the same way: silently when the text is
  unchanged, otherwise through the editor's discard confirm above ("Discard your spec
  edits?" / "Discard your instruction edits?" / "Discard your notes edits?", confirm label
  "Discard edits"), which stacks above the editor card so Escape closes only the confirm;
  confirming discards the typed text and closes, cancelling returns to the editor with the
  text intact (the §14 `Modal` close guard). The edits stay mutually exclusive — one modal,
  one document at a time — and the drafts behind them (`specText` / `instrDraft` /
  `notesDraft`) remain editor state, never serialized into the §4.4 draft.
- **Dirty gating** — any spec/instruction/chat-rewrite change marks the workflow out of sync and
  **blocks saving** until the BUILD card's "Sync now" button makes one §8 `sync` call
  regenerating the steps ("Steps synced with the spec — review them, then save."). The
  out-of-sync state persists with the draft (§4.4 `outOfSync`) and is restored on resume —
  keeping a locked draft and reopening it must not unlock Save around the gate. Grant
  toggles (agent enablement, secret allowance) never mark the workflow out of sync by
  themselves — grants are permissions (§5), not versioned content. Instead, grant sync state is
  **derived** from steps vs grants, matched by id: the workflow is out of sync exactly while
  some step needs a
  grant it doesn't have — an agent step whose listed agent id (or, listing none, any agent
  at all)
  isn't enabled, or a step referencing a stored secret's id that isn't allowed. Consequences:
  checking a grant no step uses, or unchecking an unused grant, leaves the workflow in sync and
  saves directly; check-then-uncheck is a no-op; unchecking a grant steps use locks saving, and
  either re-checking it (instant, no sync) or a sync (steps rewritten without it) unlocks.
  Checking an agent shows a passive hint toast ("`<agent>` is now available to steps — Sync
  with spec if the steps should be rewritten to use it."). While viewing an
  old version, grant gaps never lock Restore — permissions are not versioned (§5) and a vX step
  needing a now-revoked grant fails at execution time instead; the cards still warn. An old
  version is browsed read-only: the spec card's Edit button and the thread's undo row
  disable while viewing
  one (like the sync button) — editing there would mark the workflow dirty and lock Restore
  behind a sync button that is itself disabled, a dead end; Restore first, then edit.
  The chat thread is one live surface across views: entries that land while an old
  version is viewed (a settling test's run chip, flushed workflow receipts) stay in
  the thread when the user returns to the draft; a view switch never removes a
  shown block. Sync
  state lives in the **BUILD card** (its own section below) at the top of the right
  column, **above** the Steps card rather than inside it, because a sync rewrites the steps and
  the parameter definitions, not just the step list. Outside a sync the card's sync button is disabled
  (never hidden) while any other §8 job is in flight, while viewing an old
  version, while a draft test is executing (below), and while the steps list AND the spec
  are both empty — a spec-only draft (steps
  generation was cancelled by an edit, or a resumed spec-only pending draft) must always be
  able to rebuild its steps through this button; an empty-steps state must never dead-end. **Inputs lock while rewriting** — while a sync
  or a chat job is in flight, every input on the review screen is disabled:
  the spec Edit button and the thread's undo row, the chat input (its own busy hint above), the
  agent-enablement and secret-allowance checkbox rows (and the missing-secret add row and the
  Secrets card's New secret button), the
  build-instructions Edit button, the TEST card's Test draft button and the test-run modal's
  test-value editors and Run test, the version menu, the drafting-agent picker, and Discard draft / Start over. The only
  live control is the running job's Cancel button (the composer's). **Rewrites
  lock while a test
  executes** — while a draft test is executing, every affordance that would rewrite the
  workflow under the running test disables: the BUILD card's sync button, the spec card's Edit,
  the thread's undo row, the chat input, and the build-instructions Edit.
  Grant toggles, test-parameter editors, and navigation stay live — the test's inputs were
  snapshotted at start (a grant change surfaces through the ordinary out-of-sync state,
  where the live test keeps its Cancel). Every disabled control shares one look:
  45 % opacity, default cursor, no hover response. The step list dims to the same 45 % opacity
  whenever it can't be trusted as-is: while the workflow is out of sync, while a sync is
  rewriting the steps, and while an agent spec rewrite is in flight. The Steps card header carries no in-sync badge (no "in sync with
  spec" check) — sync state lives only in the BUILD card. The composer's
  **Cancel** button cancels the in-flight sync (`DELETE /drafts/{jobId}`) no matter
  how it was started (the BUILD card, a repair-block apply, a chat-armed pending sync): the steps and spec
  are left untouched and the workflow returns to its pre-sync state, announced by a toast
  (never a system chip) — "Sync stopped — the workflow is still out of sync." when it was
  out of sync when the sync started, "Sync stopped — nothing changed." when it wasn't (an
  in-sync "Sync spec" run). A `blocked` sync renders a thread
  blockers entry (source: sync; Blockers above): its
  primary amends the in-editor spec (same `## Constraints & resolutions` rule) and
  repeats the sync; dismissing it leaves the workflow out of sync with
  the BUILD card still showing it. Disabled Save shows an amber hint ("Sync and review the steps before saving" /
  the running job's live §8 stage title —
  "Working on the request…" / "Updating the documents…" / "Syncing the workflow…"); saving is also
  blocked while any §8 job is in flight, and the BUILD card's sync button disables while one is.
  Disabling an enabled agent that steps still call locks saving
  through the derived grant gap above (toast "Steps X, Y are out of sync — `<agent>` is no
  longer available here. Re-enable it or sync the steps before saving."). The out-of-sync
  reason line names the cause: an agent gap ("steps call an agent that isn't enabled"), a
  secret gap ("steps use a secret that isn't allowed"), or a spec change ("these steps still
  match the old spec").
- **TRIGGERS** card — the editor's trigger list as §4.3 **long-label** chips, so the details are
  visible in the editor: cron/time show their schedule words; message triggers show their detail
  fields — "Discord · `<channel>`[ · “pattern”]", "iMessage · `<from>`[ · “pattern”]". A message
  trigger missing its detail field (no channel / no sender) renders the placeholder "missing" in
  its place — surfacing a broken trigger before a save can 422 on it. A disabled trigger
  (`enabled: false`, §4.3) renders its chip grayed out — faint text on the dim hairline
  background (`--text-faint` on `--hairline-dim`) instead of the accent pair, matching the
  §9.2 off-state — so the editor shows which triggers won't fire. Chips keep the footer
  "Executes even when
  the app is closed. Ask the AI in chat to change these, or use the automation page —
  chat changes apply when you save." No hands-on editing here (the detail page keeps the
  §9.2 editor): the list shows, in create mode, the chained sync's drafted triggers plus any chat
  ops applied on top (the ones
  v1 gets); in edit mode the saved triggers until a chat op or sync changes them, then
  the staged list — the §4.3 trigger-merge
  preview after a sync (drafted crons over the spec-sourced cron subset, drafted
  message/app-start entries added when
  new, stored non-cron and `source: user` triggers surviving), with chat `triggers` ops
  applied in place (§8) — what saving will store. Empty: "No triggers —
  executes only via Execute now and the menu bar." **Trigger-setup reminder:** when a
  settled sync leaves the workflow reading the trigger message with no message
  trigger to deliver one — some step's code references `trigger_payload` while this card's
  trigger list holds no discord/imessage entry (§8 rule 9: the agent never invents a
  channel id or sender handle; it omits the trigger, and the user adds it on the
  automation page or hands the chat the details, §8 `triggers` ops) — the thread gets the
  **system** entry "The steps read the trigger
  message, but no message trigger is set up — tell your AI the channel or sender details,
  or add one on the automation page after
  saving." right after the job's outcome entries. It appends only when the settling job
  introduced the gap (the pre-job draft didn't qualify — a fresh draft's first build always counts),
  so repeated syncs over an unchanged gap never repeat the reminder.
- **PARAMETERS** card — display-only in **both** create and edit
  mode (no read-only tag, the plain title carries it): each row shows the draft
  parameter's name, description, and a read-only **value summary** (the §4.2 one-line summary,
  right-aligned, ellipsized) — never inline editors. The summary's source: in create mode the
  drafted definition's default (the initial values v1 seeds — e.g. a URL the AI captured from
  the prompt); in edit mode the automation's live value, matched by name and kind (§5), so a
  drafted param without a stored match falls back to its default — and in both modes a
  chat-staged value (§8 `param_values`, matched name + kind) overrides the summary, with a
  small "staged" hint beside it so an unsaved value is never mistaken for a stored one.
  Footer: "Values
  aren't part of a version — set them on the automation page, or ask your AI here (staged
  values apply when you save). For a test, set
  test-only values in the test-run modal — or ask your AI, which can also change the
  parameter definitions and set test values when it runs a test." (The AI changes
  definitions only through the spec + sync, and never sets a test trigger message — the
  §8 actions carry `test_values` / `param_values`; the message mock stays a modal-only
  input.)
  Value input lives on the §9.2 detail page
  (§4.2 edit behaviors) and, test-only, in the test-run modal opened from the TEST card.
  Empty state:
  "No settings needed — your AI didn't ask for any."
- **CONCURRENCY** card — display-only in both modes, directly below the Parameters card: the
  §4.1 settings as two summary rows in the §9.2 card's language — **"Max parallel executions"**
  (`maxParallel`) and **"Max queued executions"** (`maxQueued`), each with the value
  right-aligned like the Parameters card's value summaries and the §9.2 caption below the
  label (same help text as the detail page's rows, same secondary style as the Parameters
  card's per-row descriptions). The value's source: in edit
  mode the automation's stored settings, in create mode the defaults (1 / 0) — and in
  both modes a chat-staged value (§8 `concurrency` action) overrides its row, with the
  same small "staged" hint the Parameters card uses, so an unsaved value is never
  mistaken for a stored one. No inline editors (the §9.2 detail page keeps the number
  inputs). No footer text. The card is not collapsible and has
  no empty state — the two rows always render.
- **Steps** — readable scripts with per-step read-only tags (same tag language as the §9.2
  detail page — never menus, every tag carries a plain-language tooltip — the §14 Tag
  tooltip bubble, custom, not the native `title`; every tooltip follows one shape — what
  the tag is, then " — `<why>`" appended when a why exists, never a why alone): an agent step
  shows one microchip-icon tag per entry in its `agents`
  list — the entry's id resolved to the LIVE agent (name and model update on rename, §4.1) —
  (tooltip "This step calls `<name>` · `<model>` mid-execution", with " — `<why>`"
  appended — the entry's §4.1 role note, falling back to the step's own `why`; a tag turns
  red when its
  id matches an agent that isn't enabled — red tooltip "`<name>` isn't enabled for steps —
  this step
  would fail" — and renders the red deleted state when the id matches no
  agent at all: the archive record's NAME when the automation's §4.1
  `unresolvedReferences` carries the id (red tooltip "This step calls `<NAME>` from the
  imported file. No agent on this Mac matched it, so this step would fail."), else the
  short id prefix — red tooltip "This step calls an agent that no longer exists — this step
  would fail"; an empty list shows one tag
  naming the automation's first enabled agent (step-`why` tooltip rule), and reads "no agent"
  in red when none is
  enabled — red tooltip "No agent is enabled for steps — this step would fail"), a step shows
  one key-icon tag per secret it uses (its `secrets` entries' ids unioned with
  the literal `secrets["<id>"]` references in its code, each resolved to the live §4.8
  secret's name — a dangling id renders the red deleted state: the archive record's NAME
  when §4.1 `unresolvedReferences` carries the id (red tooltip "This step uses `<NAME>`
  from the imported file. No secret on this Mac matched it, so this step would fail."),
  else its short id prefix;
  same §9.2 secret tooltip — "This step uses the
  `<NAME>` secret from your Keychain", with " — `<why>`" appended when the declared entry
  carries its §4.1 per-use note), a step shows one box-icon tag per declared §6.2 package
  it uses (its `packages` entries' imports unioned with the declared `import` names appearing
  in its code), labeled with the import name — tooltip "This step uses the `<name>` Python
  package, version `<x.y.z>` — `<why>`", with the installed version when the §6.2 install
  check has reported one (else without the version clause) and `why` the step entry's §4.1
  per-use note, falling back to the package declaration's §6.2 general `why` (no why at
  all — old data — drops the clause) — and every step
  shows the §9.2 clock-icon time-limit tag and, when its §4.1 retry budget is set, the §9.2
  rotate-icon retry tag (same labels, tooltips, and hide-at-zero rule). Which agents a step calls is decided by
  the drafting agent per the §8 selection rule — changing it happens through the spec or
  build instructions plus sync (or the agent-enablement card), not per step. Step rows open the same §9.2 step-script modal (whole-row click; unlike the
  detail page's rows the editor's carry **no** right-edge expand glyph — the row's hover
  fill is the only affordance, and the button's "View script" `title` tooltip is kept on
  the row itself; one step shows at a
  time, with the modal's step navigator, previous / next, and the arrow keys flipping inside
  it). The step number
  prefixes the title ("1. Fetch page" — faint mono numeral, title styling unchanged) instead
  of occupying a left gutter column, keeping the row's left edge free on narrow windows.
  The editor's modal draws its §9.2 package facts from the draft's declared packages
  (the detail page's modal reads the automation record's §6.2 list instead), its §9.2
  change badge from the viewed revision against the stored versions (the Draft against
  the current version, an old version against its predecessors; none on a create-flow
  draft with no stored automation), and renders
  `code` falling back to "# script not written yet" for a step whose script hasn't been
  generated. A sync or undo that swaps the
  steps closes an open step-script modal (same reason the old open set reset: the index
  no longer names the same step). The modal renders the step's
  `code` with Python syntax highlighting — a self-contained tokenizer (`PyCode` in `ui.tsx`, no
  dependency) coloring keywords, constants, strings, numbers, comments, decorators, builtins,
  `def`/`class` names, and call names over the base mono `pre`. Language is always
  Python (§15); the same `PyCode` renders the detail page and the draft/create step editor. Agent steps listing no agent
  and no enabled agent show a red warning ("Step N needs an agent, but none is enabled — the
  execution would fail there. Enable one below."). A step whose entry id resolves to an
  agent that isn't
  enabled warns ("Step N calls <Agent>, but it isn't enabled here — the execution would fail
  there. Enable it below.") — a grant gap (Dirty gating above), locking Save until the agent is
  re-enabled or a sync rewrites the steps. A step whose entry id matches no agent at all
  warns ("This step calls an agent that no longer exists — the execution would fail at
  step N.") - except an id carried by §4.1 `unresolvedReferences`, which warns with the
  imported name instead ("Step N calls `<NAME>` from the imported file, which has no
  match on this Mac - pick an agent or ask your AI to fix it."). All three derivations compare ids, never names — a rename changes nothing here. Per-automation
  agent enablement list with "X of Y enabled" — one row per agent (name + model sub-line),
  the whole row a `role="checkbox"` button leading with the §14 `CheckBox` glyph; agents called by steps — including
  named-but-disabled ones — show a "called by step N" note. Agents created anywhere else
  (Agents page) arrive unchecked in edit mode — stored grants never widen silently, same rule
  as secrets. The agents card is collapsible,
  defaults collapsed — the header's "X of Y enabled" count stays visible either way; the
  collapsed line lists the enabled agent names (explainer when none is enabled, status-aware
  rule above) — and is
  forced open while its warning shows (the same collapsed-when-healthy, forced-open-on-problem
  pattern as the Packages card).
- **Secrets** — card eyebrow "SECRETS · ALLOWED FOR STEPS". Step code is scanned for literal
  `secrets["<id>"]` subscripts (unioned with the declared entry ids); secrets that exist but
  aren't allowed, and
  referenced ids matching no stored secret, each produce warnings with fix affordances - a
  missing id carried by §4.1 `unresolvedReferences` warns with the imported name
  ("`<NAME>` came from the imported file and has no match on this Mac - pick one of your
  secrets or ask your AI to fix it.") instead of the short id. The
  card's checkboxes (one `role="checkbox"` row per secret, the §14 `CheckBox` glyph then the
  mono name — the same row anatomy as the Agents card) toggle secret **ids** in `allowedSecrets` (§4.1); all matching is by id. A used-but-not-allowed
  secret is a grant gap (Dirty gating above): it locks saving until the secret is re-allowed or a
  sync rewrites the steps. A missing-from-Keychain secret only warns — adding the value through the
  fix row also allows it. "X of Y allowed". **Default state: on a new automation (create mode)
  every Keychain secret starts allowed** — the same all-on seed as agent enablement; the user
  unchecks what a workflow shouldn't reach. Edit mode restores the stored grants (and a resumed
  draft its own selections, §4.4). The expanded card closes with a quiet **New secret** button
  (`.ad-btn-accent-ghost.small`, the §9.2 trigger-editor size) on its own row above the footer
  note, opening the shared §4.8 secret add modal. **A secret saved from this button is
  auto-allowed** — appended to `allowedSecrets` (checked) the moment it saves — because adding
  it from this page is an explicit grant for this automation; in edit mode that marks the draft
  touched, same as toggling a checkbox. Secrets created anywhere else (Secrets page, another
  automation) arrive unchecked in edit mode — stored grants never widen silently. The empty
  state points at the button: "No secrets in your Keychain yet — press New secret."
  Collapsible card, defaults collapsed — the header's "X of Y allowed" count stays visible
  either way; the collapsed line lists the allowed secret names (explainer when none is
  allowed, status-aware rule above) — forced open while a warning shows (same pattern as the
  agents and Packages cards).
- **PACKAGES** card — in the **right column**, below the Concurrency card: display-only like
  Triggers and Parameters — the drafting pipeline owns the list; the user's only write is the
  §6.2 package update below.
  One row per §6.2 declared package — the distribution name in mono, followed by the
  **installed version** (from the §19 check — the real version in the shared directory, never
  a manifest value) in faint mono, plus a status chip:
  **installed** (green check) · **installing** (static faint "installing…" text, no
  spinner) · **not installed** (amber — a
  saved automation whose packages went missing, found by the §19 check on page load) ·
  **failed** (red; the plain-word error beneath in mono, e.g. the §7 category wording with the
  pip stderr tail). Beneath each row, the package's `why` — the drafting agent's one-line
  purpose (§8 rule 5) — in faint text, so the card explains every install it asks the user
  to trust. Header counts "N of M installed" (no count when the list is empty). Amber
  and red rows share one **"Install" / "Retry"** button (the §19 install call; rows show
  the installing text while it runs). Collapsible: defaults collapsed when everything is installed,
  forced open while any row is installing, not installed, or failed; the collapsed line lists
  the package names (status-aware rule, §11 card template — the card only collapses when the
  list is non-empty). Footer: "Your AI picked
  these Python packages for the steps. They install automatically — nothing for you to run."
  Empty state (like the Parameters card's): "No extra packages — the steps use only the
  built-in libraries." While drafting, the card shows its static placeholder line like
  Triggers/Parameters (The first build on Review, above). In edit mode the page checks statuses once on load
  (§19 `POST /packages/check`); during a sync job the card fills from the job's draft
  payload statuses (§8). An install failure never blocks saving — executions self-heal (§7) —
  so the card carries the warning without gating Save.
  **Updates (§6.2 semantics):** on load the page also asks PyPI once per package list
  (§19 `POST /packages/outdated`, advisory — a failure leaves badges off; the comparison
  baseline is the installed version). An outdated row shows an accent-tinted "→ x.y.z" badge
  after the installed version and an **Update** button on the row; two or more
  outdated rows add an **Update all** row above the footer. The header appends "· K updates"
  while any row is outdated (count hidden at zero). Clicking updates via §19
  `POST /packages/update` — `pip install --upgrade` in the shared directory, no manifest
  writes; the affected rows show the installing text, then the fresh installed version and
  status. Since the directory is shared, the new version applies to every automation using
  the package. Updates never force the card open and never gate Save.
- **Framework instructions** — read-only card showing `framework-instructions.md` **rendered
  as markdown** (the shared §4.5 Markdown component — full GFM; the shared 440 px markdown
  body, §14 overlay scrollbar style). The file content itself is untouched —
  what is rendered is byte-for-byte what the agent receives. Content comes from §19
  `GET /instructions` (fetched once per app session and cached); the same response carries
  `default-build-instructions.md` as the fallback pre-fill for the Build instructions card.
  Collapsed hint and footer copy: built-in instructions the AI reads before writing anything,
  word for word — they update with the app, nothing for the user to maintain.
- **BUILD card and TEST card** — the top two cards of the right column, 14 px apart (the
  §14 stacked-card gap), BUILD above TEST: build, then test, read top to bottom. They
  replaced the earlier merged "BUILD & TEST" panel once the test run moved into the
  test-run modal (below): with no live run surface left in the column, each card holds a
  single-purpose status row, and one card per concern reads cleaner than two zones under
  one eyebrow. Both use the §14 card-header idiom — the header row holds only the
  eyebrow (`BUILD` / `TEST`), never a button — and neither card ever disappears; only its
  body changes with state. **Posture: quiet when fine, loud only when blocking.** Chat is
  the primary way to build and test — a chat message can request the sync and the test
  through the §8 actions — so the cards stay status surfaces with one-click escape
  hatches and shout only when saving is genuinely blocked. Concretely: **no green state**
  in either card (an in-sync workflow shows no indicator dot at all — the dot is amber
  while out of sync, absent otherwise — never a spinner, never faint, never green: a
  status that asks nothing must not draw the eye), and at most one accent-primary button
  across both cards: **Sync now** while out of sync. Every other card button is a compact
  borderless **text button** (the card-header treatment above — never a bordered or filled
  box): a state's main action (Test draft, Open test, a live test's Cancel) muted, every
  other action (Sync spec, Analyze failure) faint — a failed test never blocks saving, so
  testing never shouts. **Each card body is exactly one row** — status text on the left,
  buttons on the right — so the two cards read as two status lines over the Steps card:
  the text is a single line that shrinks with ellipsis (full text in its tooltip) and the
  buttons never wrap or clip; anything longer than a line (explainers, side-effect
  warnings, the full outcome) lives in the test-run modal's footer or the Save hint, never
  in the cards. Both rows sit on the card's single 18 px horizontal inset (§14). **A job in flight never animates a card** — neither a
  chat job nor a sync: the cards have no drafting state, no spinner, no live detail. During a
  chat job they keep their current state with their controls disabled per the inputs
  lock; **while a sync runs or is armed** (a §8 `sync` job in flight however started —
  Sync now, Sync spec, a repair-block apply, a chat-armed pending sync — or a pending sync
  waiting to fire) the BUILD card shows its **syncing row** (state 2 below): the same quiet
  shape as the in-sync row with the static text "Syncing the steps with the spec…" — no
  dot, no spinner, no stage or detail — and controls disabled per the inputs lock, so the
  card says a sync is underway without ever claiming the steps already match. The **TEST
  card treats a running or armed sync exactly like out of sync** (its "Sync the steps
  before testing." row, below): the steps are about to be rewritten, so nothing about them
  is testable yet. The sync's live surface is the thread progress entry alone — its
  "Syncing the workflow…" title, the live `detail` line, and the event feed — with the
  **Cancel** in the composer (cancel semantics under Dirty gating above). So a sync
  started from the in-sync row (Sync spec, or the first turn's chained sync) only swaps
  the row's text for the static syncing line, and one started by Sync now trades the
  out-of-sync row for the syncing row at the click, not at the landing. When the sync
  lands the in-sync row renders; when it fails, blocks, or is cancelled the workflow is
  out of sync again and the out-of-sync row renders then.
  **BUILD card** — the workflow's sync state, one row, first match wins:
  1. **Out of sync** (and no sync running or armed): the amber dot + the status text
     naming the cause (Dirty gating above: "Out of sync — steps still match the old spec."
     / "Out of sync — a step's agent isn't enabled." / "Out of sync — a step's secret isn't
     allowed."; the row's tooltip carries the text plus the longer
     explainer — "Sync the steps to the new spec, then review them. Saving is locked until
     you do — nothing ships unreviewed." / "Re-enable the agent, or sync the steps so they
     only call agents available here. Saving is locked until you do." / "Re-allow the
     secret, or sync the steps so they only use secrets allowed here. Saving is locked
     until you do."), and the accent-primary **Sync now** on the right — the row-height
     `.ad-btn-primary.small` (§14), so the out-of-sync row is exactly as tall as every
     other card row — disabled per Dirty gating, never hidden. The Save button's amber hint says saving is locked; the
     card does not repeat it.
  2. **Syncing** (a sync running or armed, however started): the muted static status text
     "Syncing the steps with the spec…" — no dot, no spinner, no stage or detail line (the
     thread says what is happening) — with the faint **Sync spec** text button on the
     right, disabled for the duration (never hidden, never a Cancel — that lives in the
     composer).
  3. **In sync**: the muted status text "In sync with the spec." — no dot — with the faint
     **Sync spec** text button on the right (the same §8 `sync` call on demand; disabled
     per Dirty gating — e.g. while a test executes — never hidden).
  **TEST card** — the draft test's launcher and last outcome; the run itself lives in the
  test-run modal. One row, status text left and buttons right, first match wins:
  1. **Test executing** (a tracked live test, however started — also while the workflow
     is out of sync: a live test is never left uncancellable): a spinner + "Executing —
     step 2 of 5 · <step name>" (the step name takes the ellipsis; the step count is the
     row's only progress — the modal has the bar), and on the right muted **Open test**
     (opens the modal on the live run) then muted **Cancel**.
  2. **Out of sync, or a sync running or armed**: the muted text "Sync the steps
     before testing." with the disabled muted **Test draft** on the right — a test always
     runs steps that match the spec, never stale ones, and while a sync is rewriting them
     the row holds this gate text rather than flashing an old outcome.
  3. **Steps changed since the last test** (a previous outcome — tracked record or
     resumed summary — whose steps a sync has since rewritten): the muted text "Test the
     new changes." (tooltip: "The steps were rewritten after this test — its outcome no
     longer applies.") with **Test draft** on the right, which opens the modal on the
     **setup** phase for the new steps, never on the old run. **An outcome belongs to the
     steps it ran against:** the card fingerprints the draft's steps (files + code) when a
     test starts (sent along as §19 `stepsFingerprint`) and treats the outcome as stale once
     the fingerprint differs — a landed sync, a draft undo, a resumed draft whose steps
     moved on. Grant toggles never change
     the fingerprint, so re-checking a grant brings the outcome back. The old run stays a
     real record (Executions list) until the next test replaces it; the stale outcome never
     offers Analyze failure. A new test start resets the fingerprint.
  4. **Test settled** (a tracked record): the outcome — the `StatusLine` icon + text
     "Test succeeded." green / "Test failed." amber (ellipsized like the row's other
     texts), or the faint "Test cancelled." (the full outcome
     wording, memory note and failure message included, is the modal footer's) — with
     muted **Test draft** (opens the modal on the settled run) and, on failure, faint
     **Analyze failure** (sends the canned analyze chat message, below) on the right.
  5. **Last test** (a resumed draft's persisted summary, below, with no tracked record):
     "Last test succeeded — <when>." green / "Last test failed — <when>." amber, with
     **Test draft** on the right — which opens the modal on that run while its record
     still exists and on the setup phase otherwise. The summary carries the steps
     fingerprint the test ran against (§19 `stepsFingerprint` — the renderer's opaque hash
     over the steps' files and code, sent with the test start and stored verbatim), so a
     resumed draft whose steps were re-synced after the test lands in state 3 instead; a
     summary without one (§21 old shape) is never stale.
  6. **Never tested**: the muted text "Not tested yet." with **Test draft** on the right.
     The side-effects warning ("Real steps execute on this Mac — emails send, files move;
     memory is a scratch copy.") is the modal footer's, read before Run test.
  Test draft never starts a test — it opens the modal; only the modal's Run test does.
  It disables under the inputs lock (a §8 job in flight), while an old version is viewed
  (like the sync button: an old version is never synced or tested), and while the draft
  has no steps. Open test and Cancel stay live under every lock.
  **Test** — executes the draft's **real steps** as a **test execution record** (§4.5:
  `test: true`, `versionLabel: "Test"`, `trigger: "Test"`) through the exact engine path a real
  execution takes (there is no simulation mode): the record and its `steps/` (the sent
  draft's scripts), `workspace/`, `result/`, and per-step-attempt logs all live under
  `executions/<uuid>/`, progress streams over the ordinary `execution.*` WS events, and the
  result, failure diagnostics, and secret redaction work exactly as in §7. The launcher
  always reads **"Test draft"** — the label never changes once a test outcome exists (a
  live test shows Open test + Cancel in its place) — and the modal's run button reads
  **"Run test"** — never "Execute", which is reserved for real executions (§9.2 "Execute
  now", §7 "Execute again"). A test uses: in-editor param
  values and grants (never the stored automation's), and **scratch memory** — copied to a
  temp dir from the draft container's `memory/` when it exists (edit mode falls back to
  the automation's memory dir; create mode to empty) and discarded when the test ends, so
  a test can never poison the memory the deployed version reads (§4.1). What distinguishes
  a test record from a real execution: it never touches the automation's derived display
  state or the one-execution-at-a-time gate (§5), it lists in the Executions list like any
  run but its row lives and dies with the record (§7),
  it cannot be retried or re-executed from its execution page,
  and its lifetime is the draft's — starting a new test deletes the previous test record
  (one per draft container, and one **live** test per container: §19 answers 409), and a
  settled draft (discard, save as vN+1, Create, Start over) deletes its test records.
  A test **still executing** when the draft settles is **cancelled by the settle** (§19:
  every draft-settle endpoint cancels the container's live test first), and the cancelled
  record deletes itself once it lands — it never survives the draft and never writes a
  last-test summary into the settled container, so a discarded draft leaves no test residue.
  The same settle also cancels any **still-building §8 drafting job** stamped with the
  container as its owner and drops its held outcome (§19 — the cancel kills the harness
  process): a settled draft
  leaves no agent process running and no unconsumed outcome behind. The editor cancels
  its own in-flight job
  client-side too (Discard draft / Start over — belt-and-braces beside the server-side
  owner cancel; merely leaving the page cancels nothing, §19 background continuation).
  Deleting the automation deletes them too.
  **Side effects** outside memory are real (emails send, files move, notifications post per
  settings) and the modal's setup footer says so plainly. Navigating away from the editor
  never cancels a live test — it is a real record, visible and cancellable from its
  execution page; re-entering the editor re-attaches the TEST card to a still-executing
  test. **The outcome is never thrown away with the editing session:** a finished test
  writes the last-test summary `test.yaml` (§5 — status succeeded | failed, finished-at,
  and the test execution's id) into the draft container, wiped at the next test start and
  deleted with the draft. It rides the draft payload as `test` ({ status, when: §4.1
  started-label, executionId }) — on the automation's `draft` object and on `GET
  /draft/pending` — and a resumed draft's TEST card renders it as state 4 above (the
  modal opens on the run while the record still exists — retention may outlive it). A live
  test always takes over the card. **When a test settles the thread hears about it:** the
  editor appends a run-settled **system** entry — "Test succeeded." / "Test failed at step
  `<name>` — `<message>`." — so follow-up messages have an anchor and the agent's
  CONVERSATION context names the run. **On failure nothing analyzes by itself:** the card
  (and the modal's toolbar) offer **"Analyze failure"** — it sends the **canned analyze
  chat message** "The test failed at step `<name>` — figure out why. If the automation is
  at fault, fix it; if it's something I need to do on this Mac, tell me what to do and
  how instead." as an ordinary §8 chat job (gated exactly like the chat input, so it
  disables while any §8 job is in flight): the §8 RECENT EXECUTIONS context carries the
  failing run's error and log tails, and the response — an explanation, a spec rewrite,
  actions that resync and retest — lands in the thread like any chat outcome. Build-time
  blockers and execution-time failures stay one convergent repair loop in one place, the
  chat thread. Advisory: a failed test never blocks saving.
- **Test-run modal** — the one surface for setting up, watching, and reading a draft
  test; the TEST card only launches it and reports the outcome. It is the §9.2
  step-script modal's two-column frame carrying the §7 execution page's **LOGS rail +
  LOGS pane** (one shared execution view, §7 — the modal is not a second, smaller run
  UI): a §14 `Modal` card `min(1120px, 92vw)` wide, `overflow: hidden`, no header row and
  nothing that scrolls the card as a whole, height fixed for the life of the open modal
  at `clamp(440px, 680px, 82vh)` (logs stream, so the frame follows the viewport, never
  the content). **Opening:** Test draft / Open test on the TEST card, and the thread's
  Test-the-draft pill (which starts the test and opens the modal on the live run — the
  user asked to watch it). A chat-armed `test` action (§8) never opens it — the agent's
  answer is being read; the card shows the run with Open test. **Phases**, the modal's
  own state: **setup** (no tracked test record, or after Run again) and **run** (a
  tracked record exists — live or settled; opening with a record lands here). The
  **left rail** (280 px, dim-hairline right border) heads with the `LOGS` eyebrow (the §7 rail's own
  header: each row opens one log, so the rail is named for what it holds) and a
  right-aligned mono step count; in the setup phase it lists the draft's steps as inert
  rows (a faint dot + the name — what will run, in order), in the run phase it is the §7
  rail as-is: the "Setup log" pseudo-row over the selectable step rows (status dot
  pulsing while executing, name, attempt-count chip, duration), auto-following the live
  step until the user selects a row, auto-selecting the failed step on a failed run
  (§7 selection rules). The **right pane** sits on `--bg-code` with a fixed 44 px
  **toolbar** (hairline bottom border) and the body beneath:
  - **Setup phase.** Toolbar: the `TEST DRAFT` eyebrow left; right, the accent-link
    **Run test** — the only control that starts a test, and the modal's only accent
    control — then the ✕ `.ad-btn-icon` close. Run test is gated on steps existing, the
    workflow being in sync, no §8 job in flight, no live test, and no old version viewed
    (its tooltip names the reason while disabled). Body (its own §14 overlay-scrollbar
    pane): the this-test-only note first, worded to what the draft has — "Values and the
    message apply to this test only — nothing is saved." (params and message triggers
    both), "These values apply to this test only — nothing is saved." (params only), "The
    message applies to this test only — nothing is saved." (message triggers only) — then
    every test option at once, nothing behind a second click:
    - When the draft has params: the `PARAMETER VALUES · THIS TEST ONLY` eyebrow, then one
      editor per param (§4.2 kinds; settings-row geometry for toggle and number, stacked
      label + help over the control for list, kv and text), prefilled in edit mode with
      the automation's current values (draft default when a param is new) and in create
      mode with the draft defaults — and, over that base in both modes, the draft's
      **drafted test values** (the §8 sync-call manifest `test_values`, riding the sync
      payload as `testValues`): the agent that just built the steps entered them, so they
      are the freshest signal, and the user edits them freely like any prefill. The values
      ride the §19 `paramValues` body field and apply to this test only — nothing is
      stored, and the read-only Parameters card is untouched. Untouched prefills send the
      same values a never-opened modal would use — the seeded values above (stored values /
      draft defaults, drafted test values on top), exactly like executing the draft.
      **Drafted test values are draft state:** kept on the editor's working copy, replaced
      whenever a later sync payload carries a `testValues` map (kept when it doesn't — a
      name no current param matches simply seeds nothing), covered by the Draft-undo
      snapshot, persisted with the draft as the §4.4 draft-only `test_values` key (a kept
      draft resumes with them), and gone when the draft settles — they ride the draft,
      never the automation.
    - When the editor's trigger list (the TRIGGERS card list) holds a message trigger
      (§4.3 discord/imessage, `enabled` state irrelevant): the `TRIGGER MESSAGE · THIS
      TEST ONLY` eyebrow, a trigger picker when the list holds several message triggers
      (the §4.3 long labels; single-trigger lists skip the picker), a **From** field
      (prefilled with the trigger's `from` for iMessage, "Test" for Discord; switching the
      picked trigger re-prefills it), and a **Message** text field (empty,
      placeholder-hinted). The mock rides the §19 `triggerMock` body field **only when the
      message text is nonempty** — left empty the test runs without a payload; nothing is
      ever stored, and the trigger list is untouched. The sub-block's footer says so and
      names the reply behavior plainly: applies to this test only; a step's `reply()`
      posts to the **real** Discord channel, and an iMessage reply can't send from a
      mocked message (§6.1). The built §4.5 payload is snapshotted on the test record like
      a real firing's, so the run shows the message and sender like any message
      execution's; the record's trigger label stays "Test".
    - Neither: the `EmptyLine` "No parameters or message triggers — the test runs the
      steps as they are."
    The entered values are TEST-card state, not modal state: closing the modal keeps
    them and reopening shows them again; seeding happens only when the modal first opens
    without prior values. A change to the draft's param definitions or trigger list drops
    them. A chat-armed test with values (§8 actions) pre-fills the param editors, so
    opening the modal afterwards shows what ran. The resolved values are snapshotted on
    the test record, so its execution page shows them like any execution's. Under the
    inputs lock (a §8 job in flight) the editors and Run test disable in place; the modal
    stays open.
  - **Run phase.** Toolbar: left, the selected log's name as the eyebrow ("Setup log", or
    the step name, suffixed " · LIVE" while that step's latest attempt is executing), the
    §7 attempt pills when the selected step retried, and the §7 redaction chip; right, the
    phase's controls then ✕: while the test **executes**, faint **Skip step** (the §7
    skip of the live step) and muted **Cancel** (`DELETE`s the run like the execution
    page's Cancel); once **settled**, the accent-link **Run again** (returns the modal
    to the setup phase with the entered values intact — the settled record stays until
    the next Run test replaces it), faint **View execution** (closes the modal and opens
    the run's §7 execution page — the full result views live there), and, on failure,
    faint **Analyze failure** (sends the canned analyze chat message above and closes
    the modal, so the thread it lands in is on screen). Body: the §7 LOGS pane at full
    pane height — the color-coded log view with the 2000-line cap and truncation note,
    lazy per-selection fetch, live streaming with auto-scroll and the blinking cursor,
    and the §7 empty states. Nothing else from the execution page renders here — no
    RESULT card, no failure notice, no parameters block: the modal is for watching the
    steps run; reading the result is one click away.
  A 36 px **footer** spans the whole card (hairline top border, the cards' 11.5 sans
  muted style) and is the run's status line: setup — "Real steps execute on this Mac —
  emails send, files move; memory is a scratch copy."; executing — a pulsing accent dot,
  "Executing — step 2 of 5 · <step name>", and a 160 px progress bar right-aligned
  (terminal steps over total); settled — the `StatusLine` outcome "Test succeeded — the
  memory copy was discarded." (green) / "Test failed at step “<name>” — <message>."
  (amber; "Test failed — <message>." when the error names no step) / "Test cancelled."
  (faint) / "Test <status>." for any other terminal status; a record still loading —
  "Loading the test…". **Closing** (✕, Escape, a backdrop click) never cancels a test —
  the TEST card shows the run and reopening re-attaches to it; the modal owns Esc while
  open like every modal (the page-level Esc shortcut yields, Input above). Starting a
  test from the modal keeps it open: the pane flips from the setup form to the live log
  at the click, the rail rows gain their status dots as the record lands, and the footer
  turns into the progress line. The modal never renders during the create empty state
  (there is no draft to test) and closes itself when the draft settles (Discard draft,
  Start over, save, Create), since the run it shows is deleted with the draft.

**Fix-with-AI entry (§7/§9.2).** Opening the editor through a failed execution's **Fix with
AI** button behaves as: open edit mode on that automation (resuming a stored draft when one
exists, else seeding the editor from the current version as usual), append a **system**
thread entry naming the failure ("Execution failed at step `<name>` — `<message>`"), and
immediately send the canned analyze chat message "This execution failed — figure out why.
If the automation is at fault, change it so it won't happen again; if the fix is
something I need to do on this Mac (install or start an app, sign in), tell me what to do
and how instead." (the step is not repeated — the
system entry directly above already names it) as a §8 chat job
carrying the execution's id as the §19 `executionId`, so the RECENT EXECUTIONS context includes that
run in full detail however old it is. The send waits for the stored thread to load
(Thread lifetime above), so the job's CONVERSATION context carries the kept history
and the seeded failure entry. The outcome lands like any chat outcome. While
another §8 job is already in flight the message is not sent — only the system entry is
appended, and the user asks when the job settles.

**Settled runs seed the thread.** Beyond the test entries above, entering the editor in
edit mode appends a run-settled system entry ("Draft execution failed at step `<name>` —
`<message>`." / "Draft execution succeeded." — both end with a period, like the test
chips) when the automation's newest settled Draft
execution (§4.5 kind `draft`) finished after the thread's last entry — a draft iterated
via the §19 execute API picks the conversation up where the run left off. Duplicate
seeds are suppressed the same way (only runs newer than the last thread entry qualify).

Create (new) → version 1, `lastStatus: none`, navigate to detail, toast "Created — nothing has
executed yet. Press Execute now when you're ready." Save (edit) → §4.4.

