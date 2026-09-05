# Autowright SPEC — UI shell: navigation, onboarding, agents & secrets, menu bar

Part of the Autowright spec. Index and § map: [SPEC.md](../SPEC.md). § numbers are global across spec files.

## 9. Navigation & app shell

One 100 vh dark window. Window chrome is per-OS (§2 shell platform layer,
`mainWindowChrome()`): macOS uses hidden-title-bar traffic lights (positions below);
Windows uses `titleBarStyle: 'hidden'` with a native `titleBarOverlay` — the OS draws
minimize/maximize/close at the top-right — **plus a renderer-painted title bar**: the
fixed full-width top strip (below) is, on Windows only, a visible 40 px bar spanning the
entire window top (above sidebar and content), background §14 `--bg-titlebar` `#141820`
with a 1 px `--hairline` bottom border. The overlay's `color` = `--bg-titlebar` `#141820`
so the OS button cluster blends into that bar instead of reading as a floating block
hugging the buttons (the pre-bar design matched the overlay to `--bg-content` and painted
no bar at all); `symbolColor` = the §14 `--text-2` hex, `height` 40 so the overlay
spans exactly the title bar (the bar's hairline sits at y 40–41, below the overlay, so
the border runs unbroken under the buttons too — under the global `border-box` reset that
means CSS height 41, not 40: a 40px box puts its border at y 39–40 where the OS overlay
paints over it and the hairline visibly stops under the button cluster; no macOS-style frameless custom buttons,
and the overlay region needs no `no-drag` handling — the OS owns it). The app-shell root behind the
floating rail is per-OS too: macOS paints `--bg-window`, so the rail's gutter corners (the
58 px column above the rail's top 46 and below its 12 px bottom gap) read as window chrome
around the traffic lights; every other OS paints `--bg-content` — the gate is
`platformOs === 'macos'` on the §9 store token, never a sniff — so the gutter corners and
the content pane read as one uniform surface below the title bar (there are no lights at
the left to justify a darker corner on either OS; on Windows the `titleBarOverlay` sits on
the `--bg-titlebar` bar, not on this surface).
Linux uses the native window frame for v1 (`mainWindowChrome()` returns `{}` — no custom
title bar, no overlay; the OS draws its own bar above the 100 vh dark client area): with
the OS drawing its own bar, nothing justifies a darker gutter, so the two-tone macOS
treatment would read as a mismatched strip above and below the rail. Linux also suppresses Electron's stock application
menu (the File/Edit/View/Window bar the default frame would draw under the title bar —
nothing in the app uses it): the shell calls `Menu.setApplicationMenu(null)` at ready,
gated on the `appMenu` shell capability (false on Linux only — macOS keeps its system menu
bar, which carries the Cmd shortcuts; Windows keeps the hidden default menu, whose bar the
`titleBarStyle: 'hidden'` chrome never draws anyway). The default menu's accelerators
(reload, DevTools, zoom, fullscreen, quit) go with it on Linux; text-editing shortcuts
(Ctrl+C/V/X/A) are Chromium-native and unaffected, and the §9 right-click context menu is
independent of the application menu.
The boot splash is not the app shell and keeps `--bg-window` on every OS (on Windows the
overlay therefore shows briefly as a `--bg-titlebar` block over the splash — knowingly
accepted, as the pre-bar `--bg-content` mismatch was); onboarding paints
the flat `--bg-content` page background on every OS — one background color, no accent glow —
so it matches the content pane. On Windows onboarding's top drag header carries the same
title-bar treatment as the shell (height 40, `--bg-titlebar`, `--hairline` bottom border,
zero vertical padding with centered content) so the overlay blends there too; on macOS the
header keeps its transparent `13px 28px`-padded row.
On the frameless platforms (macOS, Windows) the window drags from its top
edge, Apple
Music-style: a fixed full-width drag strip spans the whole window top (above sidebar and
content, z-index 100) — invisible and 18 px tall on macOS (the traffic-light gutter is the
chrome there), the visible 40 px `--bg-titlebar` title bar described above on Windows —
and the content pane always carries its own 40 px sticky drag strip —
every surface — so page content sits at a constant vertical offset. On Linux **neither shell
strip renders** (gated on the §9 store `platformOs` token, never a sniff): the native title
bar owns window dragging, so the strips were pure dead padding — with them gone, page content
starts at its own top padding (shell top spacing matches shell bottom spacing: zero on both
ends), and a leftover drag strip would sit over the now-risen content and swallow real OS
clicks (the trap below). Both shell strips are pure
OS drag surfaces: they carry `pointer-events: none`, so DOM clicks pass through to whatever
renders beneath them (drag-region collection ignores pointer-events, so window dragging still
works); they must never hold children — the Windows title bar paints a background but is
still childless (any future bar content would need its own `no-drag` handling). Interactive controls inside drag regions stay clickable
(`no-drag` on buttons/links/inputs). Real OS clicks on a button swallowed by a drag region start
a window drag; synthetic/Playwright clicks bypass drag regions entirely and won't catch that
mistake.

**Never paint an unloaded window:** the main window is created hidden (`show: false`) and
shows itself (`show` + `focus`) on the renderer's first successful load. A failed main-frame
load — a dead §15 `AUTOWRIGHT_RENDERER_URL`; a packaged `dist` file load doesn't fail — keeps
the window hidden and retries the load every second until it succeeds, logged to `app.log`
once per failure streak plus a recovery line. Chromium fires `did-finish-load` even for a
failed navigation, so a per-attempt failure flag set by `did-fail-load` (main frame only) is
what separates success from failure. Every other show path — the deep-link/second-instance
`showApp`, the dock/tray `activate` reopen — defers to that first load: a still-loading or
failed window is never shown blank. Two guards keep that rule from stranding a live app
behind no window at all. A renderer process that dies (`render-process-gone` - out of
memory, a crash in the bundle) is logged to `app.log` and reloaded once; a second death
in the same window has nothing left to paint, so the shell shows an error box naming
`app.log` and quits rather than staying resident and invisible. And a watchdog armed when
the window is created fires 15 s later if the window still has not shown: with a renderer
that simply loaded slowly it shows the window anyway, and with nothing ever loaded (every
attempt so far failed) it shows the same `app.log` error box once while the 1 s retry keeps
running behind it. Either way an app that is running is an app the user can see.

**Closing the window (per-OS):** on macOS, `window-all-closed` never quits — the app is a
tray-and-dock app and stays resident. On Windows there is no dock: closing the window keeps
the app resident **only while the §4.9 `menuBarIcon` tray icon is showing**; with the tray
hidden, closing the last window quits the UI (the §3 backend service is unaffected either
way — quitting the UI never stops it; §4.9 `login` still controls the next start). On
Linux there is neither a dock nor a tray (§13 — Linux ships no tray surface), so closing
the last window always quits the UI; automations keep firing in the §3 systemd backend
regardless. The
discriminator is the shell capability `dockIcon`, never a platform sniff: a platform with a
dock stays resident unconditionally; without one, residency requires a **live** tray
reference (the stored setting is never consulted — a tray that failed to create must not
strand an invisible app). The
shell consults its own `plat.capabilities` (§2 shell half — `trayPanel`, `loginItem`,
`dockIcon`, `updates`, `appMenu`, `desktopEntry` — the §3 Linux launcher-entry reconcile)
before wiring each of those surfaces, so a platform module that
declares a capability false is simply never asked for its assets or handlers. The §3
ensure-backend failure detail is per-OS too: the diagnostic line naming Gatekeeper is
macOS copy from the darwin module; Windows shows a plain "the backend service failed to
start" line plus the §2 `serviceDiagnostics` capture.

**Platform gating (§2, renderer half):** the store keeps the backend's `os` token and
`capabilities` flag set from §19 `GET /health`, read at every backend connection (the same
cycle that re-reads `backend.json`, §3 — a reconnect refreshes them). Every surface that
offers an OS-coupled feature gates on these store fields and never sniffs the platform
itself (no user-agent or preload-side `process.platform` checks — the §2 rule). The boot
sequence connects before the app shell mounts, so gated surfaces never render with the flags
unknown. Gated surfaces hide (never disable-with-a-tooltip): the §9.2/§11 iMessage trigger
kind (`imessage`), the §4.9 "Keep this Mac awake" row (`keepAwake`), the §4.9 notifications
setting and every piece of copy promising OS notifications (`notifications`), and the
§10/§12 agent Install/Sign-in actions (`agentInstall` — §19: in their place the card shows
one plain line — for a hidden Install action, "Install <name> by hand, then come back —
Autowright detects it automatically."; for a hidden Sign-in action (the provider is already
installed), "Sign in to <name> from a terminal, then come back — Autowright detects it
automatically." — each linking the provider's name to its vendor install page; detection and
sign-in state keep working unchanged). The vendor install pages, one per provider:
Claude Code → `https://claude.com/product/claude-code`, Gemini CLI →
`https://github.com/google-gemini/gemini-cli`, Codex → `https://developers.openai.com/codex/cli`,
OpenCode → `https://opencode.ai`, Ollama → `https://ollama.com/download`; each opens through
the §9.4 external-URL policy. Defaults before the first `/health` answer are macOS-identical
(every flag true), so a mac render never flickers a row it is about to keep. `agentInstall`
gates only the actions that would run a §19 `/agents/install` or the Terminal sign-in —
the §10 Free local AI card keeps its button while the only missing piece is the model
(an `/ollama/pull`, which this capability does not gate), and shows the line naming the
first missing installable piece otherwise. One Linux nuance on top of the flag (§2):
`agentInstall` is true there, but the Terminal-window sign-in method is macOS-only —
so on `os === 'linux'` the Sign in button renders only for the provider whose §19 method
is `browser` (codex); the TUI providers (claude, gemini, opencode) show the manual
sign-in line (the `ManualInstallLine` signin variant) instead, exactly as an
`agentInstall: false` platform would. Install buttons stay live for all providers.

**Per-OS copy rule:** user-facing copy that names the platform derives from the §9 store's
`os` token (renderer) or `paths.os_display_name` (backend-served strings) — never a second
platform sniff. Wherever quoted copy in this spec uses the macOS form, the Windows and
Linux renders substitute per this table (each entry lists the Windows form, then the Linux
form where it differs), with no other wording change:
`Mac` → `PC` as the machine noun in every inflection (`this Mac`, `your Mac`, `any Mac`) —
Windows and Linux alike — including model-facing §8 prompt text that names the user's
machine (the SYSTEM TOOLS
header, the chat call's diagnosis rule) · `Keychain` → `Credential Manager`, on Linux
`system keyring` (the §1
promise line becomes "Secrets live in your Credential Manager" / "Secrets live in your
system keyring");
the macOS-only remedy clause "— unlock the login Keychain and try again" in the §19
secret-write 503s has no Windows analogue and becomes plain "— try again"; on Linux it
becomes "— unlock your keyring and try again" (the Secret Service store does lock) ·
`Show in Finder` → `Show in Explorer`, on Linux `Show in file manager` (reveal semantics
unchanged; the file-manager noun inside longer phrases is `file manager`) ·
`menu bar` → `tray` on Windows
(the §13 surface's name there — "starts quietly in the tray", "Show in the tray",
"Execute now and the tray still work"). The backend-served strings that name the surface
follow the same token through `paths.tray_surface_name` / `paths.tray_trigger_label`
(the backend half of the renderer's `menuBar` entry, drift-guarded like the other two
helpers, §15): the §4.5 execution trigger label `Menu bar` → `Tray` (derived at
serialization from the running platform, so a record's label always names the surface
of the machine showing it), the §20 CLI `trigger list` empty line "no triggers — executes
only via `automation execute` or the menu bar" → "… or the tray", and the CLI `settings
set` help line "show the menu bar icon" → "show the tray icon"; on Linux there is no
tray surface at all (§13
2026-09-01), so the sentences that name it drop the clause instead of renaming it (the
CLI empty line becomes "no triggers — executes only via `automation execute`", the
settings help line "show the tray icon (Linux has no tray, so this is ignored)" — the key
still parses and stores, §4.9 — and the trigger label stays `Tray`, the only case a
`menubar` record reaches Linux being a data folder carried over from another OS): the
§4.9 login row's sub-copy becomes "Autowright starts when you log in.", the §9.2/§11
manual-surfaces lines become "when you press Execute now" / "via Execute now", and the
recurring closer "Execute now and the menu bar still work." becomes "Execute now still
works." (the §4.9 "Show in the menu bar" row itself hides on Linux, §4.9) · the §4.9 keepAwake row's sleep disclaimer
(`sleepNote`) "Works best on an always-on Mac like a Mac mini or Mac Studio. A MacBook
that is asleep would not trigger the automation." → "Works best on an always-on desktop
PC. A laptop that is asleep would not trigger the automation." on both · the §9.2 trigger
editor's sleep-through note tail (`sleepMissNote`) "This is not an issue on Mac mini or
Mac Studio, but a MacBook that is asleep will not fire on schedule." → "That is not an
issue on an always-on desktop PC, but a laptop that is asleep will not fire on schedule."
on both · model-facing instruction text naming the OS
itself (`macOS`) → the §4.1 os display name via a `{{OS}}` placeholder beside `{{MACHINE}}`
("a macOS app" reads "a Windows app" / "a Linux app" there) · the §4.9 COMMAND LINE
card's install location `~/.local/bin` → `%LOCALAPPDATA%\Autowright\bin` (the §3 per-OS
shim location; Linux keeps `~/.local/bin`) and "the Terminal" (the macOS app) → "a
terminal" on both · the §4.9 PATH command
block's zsh one-liner → a PowerShell one-liner adding `%LOCALAPPDATA%\Autowright\bin` to
the **user** PATH via `[Environment]::SetEnvironmentVariable('Path', …, 'User')` (never
`setx`, which truncates at 1024 chars and bakes the expanded system PATH into the user
value), with "open a new terminal" in the surrounding copy; on Linux a POSIX one-liner
appending the export to `~/.profile` (sourced by desktop sessions and login shells alike —
the same profile rule as the §19 installer's PATH guarantee), with "open a new terminal"
in the surrounding copy · the §9.4 About page's Homebrew fork
never renders on either (no brew channel) · `storage.py`'s §5.1 os-mismatch label already
uses
`os_display_name`. The table is exhaustive by intent: copy with no per-OS form listed here
stays identical everywhere, and a new platform-naming string must extend this table.
One standing exception: iMessage/Messages surfaces (the §9.2 iMessage editor guide,
`imessage.py` copy) keep the mac noun on every platform — they describe an Apple-only
subsystem and are gated off by `capabilities.imessage` anyway; "this PC" there would be
incoherent.

The sidebar is a **hover-expanding floating rail** anchored to the left window edge: a panel
(`position: fixed`, `left: 0, bottom: 12`, per-OS top — macOS `top: 46` (clearing the
traffic lights), Windows `top: 53` (the title bar's 41 px painted extent + the same 12 px
gap as the bottom, so the rail floats evenly below the bar), Linux `top: 12` (no lights and
no shell bar leave nothing to clear): on every OS the gap between the rail and whatever
chrome sits above it matches the 12 px bottom gap, which is what makes the rail read as
evenly floated —
z 50 — above all page content but below
every modal backdrop (z 60+), so an open modal dims and blocks the rail like the rest of the
shell) with square left corners and a 12 px
radius on the right corners (`0 12px 12px 0`), `--bg-sidebar` background and a hairline border.
On macOS its top edge (46 px) sits **below** the traffic lights — the lights are pinned at
`trafficLightPosition: { x: 14, y: 14 }` (`titleBarStyle: 'hidden'`, one fixed position in
every window state) and end around y ≈ 28, so panel and lights never overlap; on
Windows its top edge (53 px) clears the 41 px title bar by the same 12 px as the bottom gap. Collapsed
(default, no hover) the rail is 58 px wide and shows icons only: logo at top, nav icons
(Automations, Executions, Agents, Secrets, Settings), and the About icon pinned at the bottom
below a flexible spacer — About is meta, not a working surface. While the store holds §9.4
`updateAvailable` (the §3 update-available event — a known, not-yet-installed update), an
extra nav row appears directly above About (`data-testid="nav-update"`): fa-download icon
and "Update available" label, both `--accent`-colored so the icon alone signals in the
collapsed rail; no count pill, never in the active state. Clicking it navigates to the About
page, which opens pre-armed with its action button carrying the persistent §9.4
available-state highlight — the download itself still starts from that row's button. The row
follows the §3 clearing rule: gone when a later check answers up-to-date,
otherwise only with the restart that installs. A permanent **Report an issue** row
(`data-testid="nav-report-issue"`) sits directly above About — below the update row when that
one shows (order: update · report issue · About): fa-bug icon, "Report an issue" label, normal
nav-row styling, no count pill, never the active state. Clicking it opens the §9.5 report
modal in place — it is not a page: no `Page` union entry, `go()` untouched, whatever page
was showing stays underneath. On `:hover` the panel's width
animates 58 px → 212 px (200 ms — `var(--t-enter)` — pure CSS on the `.ad-rail` class) **overlaying** the content
pane, which never reflows: the layout reserves a constant 58 px spacer, so the content pane
always spans the rest of the window. Inner sidebar content keeps a fixed 212 px width with
`overflow: hidden` on the panel, so nav rows never reflow or squish mid-animation — the
wordmark ("Autowright"), row labels, and live count pills (Settings and About carry none;
each pill counts exactly the rows its page lists — for Automations, Agents, and Secrets that
is the store list's length, while the Executions pill reads §19 `executionsTotal`, since its
page lists every execution across its §7 pages, §4.5 test executions included, and the store
holds only the §7 window) are
revealed by the widening clip **plus** an opacity fade (`.ad-rail-reveal`, hidden at rest,
shown on rail hover): the clip alone would leave the labels' first characters peeking past
58 px. Icons are horizontally centered in the 58 px rail (rail
center x ≈ 29: nav-group 10 px padding + row 11 px padding + 16 px icon slot; logo 26 px at
16 px left padding). There is no collapse toggle and no persisted sidebar state — hover is the
only mechanism, identical in the app shell and the create/edit shell. The panel sits below the
two drag strips in z-order but both are pointer-transparent, and it starts below their rects
(y 46 > 40), so it needs no `no-drag` handling (on Linux both strips are absent, so the
12 px top needs none either). Navigation is state-driven (`surface` → `page` → detail ids); browser/OS back works,
but once past onboarding back never re-enters it. Page navigation (`go()`) always lands in the app
shell: if the create/edit surface is active, it exits back to `surface: app` — so sidebar tabs work
while editing an automation. Popovers close on outside mousedown. Modals render through a React portal on
`document.body`: page containers animate `transform` (`.ad-anim-page`, fill
both), which makes them the containing block for `position: fixed` — an
in-tree modal would anchor to the scrolled page instead of the viewport and
get dragged offscreen on any page tall enough to scroll. Toasts:
bottom-center, ~2.8 s default (some 2.6–5.8 s). One toast at a time — a new message replaces the
current one and replays the fade-up entrance. Centering must not use `transform` (the fade-up
animation animates `transform` and would knock the toast off-center while it plays); it uses
`left/right: 0` + auto margins + fit-content width.

**Interaction conventions** (every page, both windows):

- Anything clickable is a real `<button>` (or an anchor for links) — cards, list/table rows,
  picker rows, chips, tags. Every interactive surface is reachable with
  Tab and activates with Enter/Space. Row/card buttons reset button chrome
  (`.ad-btn-bare`: no background/border, inherit font/color/text-align, full width) and then
  carry their surface class (`.ad-card-click`, `.ad-hover-row`, …).
  **Sole carve-out — nested controls.** A clickable surface that must nest another
  interactive control cannot be a `<button>` (nested `<button>`s are invalid HTML). It
  renders as a `div` with `role="button"`, `tabIndex={0}`, and an Enter/Space keydown
  handler that ignores key events originating in the nested control — the same
  Tab/Enter/Space guarantee holds. Exactly four surfaces ship this pattern: the
  automations-list card (nests the inline Execute-now button), the agent card (nests the
  overflow-menu button), the menu-bar panel's automation row (nests the inline Execute-now
  button), and the §11 section-card header (nests its `right`-slot action buttons).
  Everything else stays a real `<button>` — no other `div onClick`.
- Icon-only buttons always carry an `aria-label` (the `title` tooltip stays for sighted
  users). `Toggle` renders `role="switch"` + `aria-checked`; radio groups (`RadioRing`
  rows) render `role="radio"`/`aria-checked` inside a `role="radiogroup"`; checkboxes are
  either real `<input type="checkbox">` elements inside a `<label>` or, where the whole row
  is the button (§11 grant rows built from the §14 `CheckBox` glyph), a `role="checkbox"` +
  `aria-checked` row; a segmented
  filter's buttons carry `aria-pressed`.
- The `Modal` shell's card renders `role="dialog"` + `aria-modal="true"`;
  `ConfirmModal` upgrades it to `role="alertdialog"` labelled by its title. This also
  gives tests an unambiguous scope — a confirm button whose label matches a row action
  (e.g. "Delete secret") is queried inside the dialog role.
- The §14 focus ring must never be clipped: controls inside an `overflow: hidden` card use
  the inset variant (`.ad-focus-inset` — outline-offset −2 px) so the ring draws inside the
  clip instead of being cut.
- A page whose data hasn't loaded yet renders `PageLoading` (the §14 centered spinner well) — never a blank pane.
- A render failure is **contained to the page it happened in**: the content region is wrapped
  in an error boundary, so a throwing page is replaced by the §14 dashed notice ("Something
  went wrong on this page" over the error message) carrying a "Back to Automations" button
  that clears the failure and navigates, while the rail, toasts, and the rest of the shell keep
  working. The renderer root carries a second boundary as the backstop, so a failure outside
  any page still shows the notice instead of a blank window. This matters because much of what
  the renderer displays is AI-authored or leniently loaded (§4, §8).
- Every "Show in Finder"/reveal action rides one main-process IPC (`reveal-path`) that
  confines the resolved path to the app's own roots — the app-support home, the logs dir,
  and the executions data dir — and no-ops on anything else, so renderer state (much of it
  AI-authored, per the boundary rationale above) can never open an arbitrary filesystem
  location.
- One-click destructive actions (delete, remove trigger, clear) always confirm first —
  `ConfirmModal` or the row's inline confirm swap; never a bare instant delete.
- Popover menus with unbounded content scroll inside (`ScrollArea`) — rows never render
  past the window edge: the version menus cap at 60 vh; the timezone picker's list scrolls
  in a fixed 240 px `ScrollArea` (its filter input sits above, outside the scroll).
- Modal cards cap at 84 vh and scroll inside (`ScrollArea`, built into the `Modal` shell) —
  content and footer buttons can never render off-screen. (The §9.4 doc modal keeps its
  tighter 62 vh body.)
- Buttons that fire a multi-request commit disable **and** show busy feedback (spinner or
  label swap) while in flight; sibling actions that would double-fire the commit disable
  with them.

Text selection: all text is selectable by default — any piece of information on screen
(titles, badges, chips, labels, list rows, logs, paths, parameter values, scripts) can be
highlighted and copied. The only unselectable elements are buttons and the title-bar drag
region (`.ad-drag`). The
sandboxed result iframe is selectable (its own document). Copying is native: highlight, then
right-click — the Electron main process shows a context menu with Copy on any selection (both
windows); text fields get Cut/Copy/Paste/Select All. There are no in-UI copy buttons.

Boot gate: until the renderer connects to the backend and loads the state snapshot, only the
plain window background renders. If boot is still pending after 300 ms, a centered logo +
spinner appears with "Connecting…" (or "Waiting for the Autowright backend…" once a connection
attempt has failed; boot retries every 1.2 s). Fast boots therefore show no splash flash.
If connection attempts keep failing for 15 s, a second muted line appears under the first:
"Still waiting — quitting and reopening Autowright restarts the backend service." (reopening
re-runs the §3 ensure-backend step). Retrying never stops.
While waiting, the splash polls the §3 ensure-backend status (`backend-status` IPC, every 2 s);
if it reports `failed`, the second line shows the failure detail instead (e.g. "The backend
service was registered but never started — macOS Gatekeeper may be blocking an unsigned build.
Details in app.log."). Connection retries continue even in this state.

**Page-header actions.** Every page's top-right header actions render in one shared cluster
(`HeaderActions` — flex row, 10 px gap, vertically centered), always in the shared
`PageTitle`'s right slot (§14: every page renders its title row through `PageTitle`; pages with
inline title metadata pass a `raw` row). Order left → right by rising prominence:
dim text buttons, then ghost, then danger-ghost, then the single accent primary — the primary
is always rightmost, with one exception: an icon-only overflow ellipsis (⋯) sits at the far
right edge, after the primary. At most one primary per header, and a list page's main create
action is that primary (New automation, Add agent, Add secret). Icons appear only on stateful
primaries (e.g. Execute now / Executing…) and icon-only buttons — text secondaries carry no
icons. Filters (the Executions page's segmented All / Succeeded / Failed control) are not
actions and sit alone in the right slot.

### 9.1 Automations list

1200 px page, "Automations" title + New button. When the §4.4 pending create-mode slot
holds a draft (`pendingDraft` on `GET /state`) — or owns a building or held §19 drafting
job (`draftJobs`, the `pending` owner: a first message still in flight has landed no
draft yet, but the session is resumable all the same) — the header shows two buttons: a
bordered
**Resume draft** button (opens the create flow, which resumes the slot straight on Review)
to the left of the primary **New automation** button — which then starts fresh: a danger
confirm (title "Start a new automation?", body "Your unsaved draft “`<name>`” will be
discarded. This can't be undone." — the draft's name in curly quotes, omitted when the
draft has none; confirm button "Discard and start new") deletes the slot
(`DELETE /draft/pending`) and then clears the slot's chat thread (`PUT /chat/pending`
with `[]` — the one discard that deletes the thread, §4.4 thread lifetime) before opening
the create flow, so the fresh session starts with an empty thread. Without a
pending draft, the single New automation button opens the create flow directly. Left of
these sits a ghost **Import** button: it opens the **import modal**
(§5.2 two-phase import). Input step: title "Import automation" over a one-line muted intro
("Add an automation someone shared — from a link, or a file on this Mac."), an
eyebrow-labeled URL field (FROM A LINK; mono text, placeholder
`https://github.com/… or a direct .autowright link`) with a faint caption underneath
("A GitHub repository page, a release, or any https link to an .autowright file."), a
centered hairline OR divider, and a full-width dashed choose-file button (`.ad-btn-dashed`,
file-import icon, "Choose an .autowright file on this Mac…" — native open dialog,
main-process IPC, filtered to `.autowright`). Footer: quiet Cancel / accent **Import**
(disabled while the field is empty; Enter submits). The URL POSTs to §19
`/automations/import/url`; a chosen file's bytes to `/automations/import/preview`; while in
flight the buttons disable. A 422 shows inline in red — under the field for URL failures,
under the dashed button for file failures — never as a toast. Success swaps the modal to
the **preview step**: the automation's landing name + description (the title is the
preview's `landsAs` — what the import will actually create; when it differs from the
archive's `name`, a faint caption under the source row says
"An automation named "`<name>`" already exists, so this one arrives as "`<landsAs>`"."),
a source row (inset box — link or
file-zipper icon, mono text: the resolved URL, or the chosen file's name), then only the
sections that apply — TRIGGERS as §4.3 `triggerLabel` chips, STEPS as numbered rows (faint
mono index, step name, accent AGENT mini-badge where `agent`), SECRETS and AGENTS (one
row per archive record, mini-badged by the §19 preview's dry-run match: gray ON THIS MAC
when `matchedTo` equals the archive name, gray USES `<matchedTo>` when the match renamed,
amber NO MATCH when `matchedTo` is null - that reference will land needing attention) —
and a hairline-divided footer note:
when the preview carries `osMismatch`, an amber first line "Built on `<OS>` — its steps
may need rewriting before they run on this Mac." (`<OS>` per the §4.1 display-name rule),
when any row is NO MATCH an amber line "Some agents or secrets have no match on this
Mac - the automation arrives needing attention.",
then the packages count when any, plus "Its triggers arrive off — review the scripts in the
editor before enabling them." Footer: quiet **Back** (returns to the input step) / accent
**Import** — POSTs `/automations/import/confirm` with the preview's token, closes the
modal, and opens the **import summary modal** (a 404 — expired token — surfaces inline on
the preview step).
The summary modal: title "Imported "`<name>`"" (the landed — possibly deduped — name), a
fixed muted intro line under it ("Its
triggers are off until you enable them."), when the summary carries `renamedFrom` a
second muted line ("Renamed from "`<renamedFrom>`", which already exists on this Mac."),
and when it carries `osMismatch` an amber line ("Built on `<OS>` — its steps may need
rewriting before they run on this Mac.", the §4.1 display-name rule — the same warning
persists on the automation as the §4.1 os-mismatch problem),
then only the sections that apply — "Matched on this Mac" (one row per §19
`secretsMatched`/`agentsMatched` entry: the archive name, with "uses `<matchedTo>`"
appended when the match renamed; a matched agent whose `ready` is false shows the §12
Needs setup badge), "Needs attention" (one row per `unresolved` entry: kind icon + the
archive name + its description as a muted sub-line, over an amber caption "No match was
found on this Mac - pick a replacement on the edit page."), and a packages
note ("`<n>` packages are installing in the background", §5.1 background ensure) when the
manifest declares any.
Footer: accent **Open automation** (navigates to the new detail page) / quiet Close. One card per automation: name, description,
status badge, trigger chip (`triggerChip`, plus an OFF tag when `allTriggersOff`), an amber
**Needs fixing** chip when the automation's §4.1 `problems` list is non-empty (the §7
attention tint; its tooltip lists every problem label, one per line — the full detail
lives in the §9.2 banner), result-summary chip when
the last execution set one (tinted by `resultStatus` with the §7 chip colors — same tint as the detail
and execution pages), and
a per-card `.ad-btn-exec` **inline execute button** (the §14 square icon button — the class owns fill, hover and size; rounded square, solid accent/orange
background with a dark play icon — same fill treatment as the primary button; hover brightens;
while that automation is executing it swaps to a spinner, dims, and is disabled — tooltip
explains why). The card carries no last-execution label — `lastExecutionLabel` appears on the detail page and in the
menu bar. The card name stays on one line — ellipsized with the full name as a `title`
tooltip (same treatment as the detail-page title), so long names never wrap and desync card
heights across a grid row. **Drafting notes** (§19 background continuation — background
work is never invisible): while an automation's draft container owns a **building** §19
drafting job (`draftJobs` on `GET /state`, kept current by the `draftjob.changed` event)
its card carries the faint one-line note "Your AI is drafting…", and while it owns a
**held** outcome (the job settled unobserved) the note reads "Your AI finished — reopen
the draft to review." — faint text only, never a spinner (§11 owns live progress). The
header's Resume draft button carries no such note: for the `pending` owner the button
itself is the only surface (the create flow shows the job's state on entry). Empty state (dashed card):
"No automations yet. Describe a job in plain words — your AI writes it as scripts you can read,
and Autowright executes them on your schedule." with accent CTA "Create your first automation" —
the CTA behaves exactly like the header New automation button: with a pending draft it shows
the same discard confirm (delete slot + clear chat) before opening the create flow.

### 9.2 Automation detail

Back link ("‹ Automations"), title row: name (single line, shrinks with ellipsis, full name in
its tooltip — the row never wraps; read-only here — renaming lives on the §11 edit page).
Under the title row, a lede row: the automation's `description` (§4.1) as a muted single-line lede
(ellipsis on overflow, full text in its tooltip); read-only — editing lives on the §11 edit
page — and beside it, on the same row, the §4.3 detail-page trigger status chip (never
shrinks; the description ellipsizes first). When the description is empty the description text is omitted
and the chip stands alone on the row.
Then the version chip dropdown (§4.4 read-only history + footer
explainer), status badge, then the §9 header-action cluster: Edit (ghost), Execute now (accent
primary), ellipsis menu at the far right edge (**Export…**, then Delete
automation… in red). Export… opens a small modal — "Export "`<name>`"" with one toggle row,
"Include parameter values" (on; help: "Your saved parameter values travel with the file — turn
this off when sharing with someone else."), footer note "Secret values and memory are never
included in the file", accent Export / quiet Cancel — then a native save dialog (main-process IPC, default
name `<name>.autowright` in Downloads) writes the §19 export response; success toasts
"Exported to `<file>`."

**Capacity popup** — pressing Execute now while **any**
execution of this automation is live never fires blind: the click opens a modal decided by
the store's state at click time (`live` count, the automation's own non-test `queued`
records = waiting, `maxParallel`, `maxQueued`). Nothing live → no popup, executes as always.
Three cases:

1. **Free slot** (live > 0, live < `maxParallel`) — confirm parallel run. Title "Already
   executing", body "N of M slots are busy. This runs now, in parallel with the execution
   already running." Accent **Run now** / quiet Cancel. No Queue option: beside a free slot
   a queued entry would promote immediately, so offering "queue" would be a lie.
2. **Slots full, queue has room** (live ≥ `maxParallel`, `maxQueued` > 0, waiting <
   `maxQueued`) — offer the queue. Title "Already executing", body "The slot is busy."
   (`maxParallel` 1) or "All N slots are busy." (`maxParallel` > 1), then "Queue this
   execution? It runs as soon as a slot frees up, and waits until you cancel it." and the
   faint hint "Raise Max parallel in Settings to allow more at once." Accent **Queue** /
   quiet Cancel. Confirm sends §19 `queue: true`; success toasts "Queued — runs as soon as a slot frees up." (or, when the raced
   response started instead of queueing, no toast — the live UI already shows it running).
3. **Slots full, queue full** (live ≥ `maxParallel`, waiting ≥ `maxQueued` — `maxQueued` 0
   included) — nothing can be offered. Title "Execution and queue capacity is full", body
   "N executing, M waiting." (just "N executing." when `maxQueued` is 0) then "Raise Max
   parallel or Max queued in Settings below to allow more." Single quiet **OK** — no run
   option, per the §7 capacity rules.

A 409 racing any choice (capacity changed between render and click) falls back to the §7
busy toast — same pattern as a snapshot row's raced 409. The popup lives on the detail page
only; the §9.1 inline execute button, the §13 menu bar, and the execution page keep the
plain toast (§7).
Sections top to bottom:

- Optional **Needs fixing banner** — shown first, whenever the §4.1 `problems` list is
  non-empty: a card-sized amber `Notice` (§14 — radius 12, `14px 18px`), title "This automation
  needs fixing", then one row per problem in §4.1 order — the problem's `label` as the row
  text, with a quiet right-aligned action link per kind: `secret-unset` → **Open Secrets**
  (navigates to the Secrets page); `secret-missing`, `secret-unresolved`,
  `secret-ungranted`, `agent-missing`, `agent-unresolved`,
  `agent-ungranted`, and `os-mismatch` → **Edit** (opens the §11 edit page, where steps
  are rewritten and grants are set on save); `package-missing` → no action (its label
  already says it installs on the first execution); `overdue` → no action (informational —
  it clears by the automation running again, or by its triggers changing). The banner is pure §4.1 `problems`
  rendering — no probe, no dismiss state: it disappears by the problems being fixed.
- Optional **Draft banner** (§4.4), then **LATEST RESULT** card — the execution's chip (if it set one)
  + metadata chips, then a **trimmed** version of the §7 result view stack for the latest
  execution: one file view for `result.md` (that exact name) expanded, and nothing else in the
  top slot no matter how many renderable files the run wrote — then the §7 **FILES footer**,
  collapsed as on the execution page, its "FILES · N" count covering every file in the dir including `result.md`. A run
  that wrote no `result.md` gets no top view and the footer **expanded** instead, so the card is
  never blank; its rows still start collapsed and still expand to the same previews. Chip rules,
  per-session collapse state, and the no-files dashed placeholder are §7's, unchanged. The full
  every-file stack stays one click away on the execution page. The card is **live**: on
  `execution.finished` the page refetches the full record (§19 client rule), so the finished
  run's result replaces the previous one — and replaces the no-executions empty state after a
  first run — without leaving the page. With parallel executions the card shows the most
  recently **started** run that has finished with a result (§4.1 `latest` ordering — the same
  run the status chip reflects), so an older run finishing later never replaces a newer run's
  result. When the latest
  execution **failed**, the card opens with a red-tinted **failure notice** ahead of any result
  views: "Failed at step “`<name>`”" (the step name in curly quotes; "Execution failed" when
`error.step` is null), the §4.5 possible reason as plain text when present, the
  error message in mono, a "View execution" link to the execution page, and the §7 quiet
  **"Fix with AI"** button (same behavior: opens the editor, seeds the chat thread, sends
  the §11 canned analyze chat message). No-executions empty
  state (dashed
  card): "No executions yet / Press Execute now — the first result will appear right here."
- **TRIGGERS** card — one row per trigger (kind icon — fa-clock for
  cron, fa-calendar-day for time, fa-rocket for app start, fa-brands fa-discord for discord,
  fa-comment for imessage;
  §4.3 `label`, followed by a muted **NO CATCH-UP** `MiniBadge` (the §9.1 OFF-badge treatment) when
  the trigger's §4.3 `runIfMissed` is false; a fa-pen **edit** button — every kind except app start, which has nothing to
  edit; per-row on/off toggle;
  remove × — removing confirms first (`ConfirmModal`: "Remove this trigger?" /
  "`<label>` is removed from this automation. Its settings are gone — add it again to get
  it back." / Cancel / red Remove trigger)), the §4.3
  status line beneath the rows, and an **"+ Add trigger"** button opening an inline editor.
  The editor fades up on entry (`.ad-anim-item`) — both from Add trigger and from a row's
  edit swap; a row's connecting/error status line enters the same way.
  Unlike the other detail-page cards this card does not clip its overflow (it has no
  full-bleed hover rows to mask) — the editor's popovers (timezone picker, secret picker)
  must overhang the card edge rather than be cut off.
  Pressing a row's edit button swaps that row for the same inline editor pre-filled with the
  trigger's values (kind picker included; the mention checkbox reflects the stored value, not
  the checked-by-default for new triggers); the submit button reads **Save** instead of Add,
  and saving replaces the trigger in place — `id` and on/off state kept — via the same §19
  PATCH, toast "Trigger updated — `<short>`."; Cancel restores the row unchanged. A cron
  added or edited here lands `source: user` (§4.3 — hand-set schedules survive later
  syncs). The
  editor:
  kind picker (Cron / One time / App start / Discord / iMessage — each chip leads with the
  same kind icon its trigger row uses; the iMessage chip renders only while the §9 store's
  `capabilities.imessage` is true — absent, not disabled, on every other platform) then
  either a
  cron-expression input
  with a live preview line (the humanized label
  when simple, plus "next: `<time>`"; an invalid expression gets the red input border and
  blocks Add) or the One time pair: a native date input (Chromium's calendar popup — date
  only, so no AM/PM field) beside a **segmented 24-hour time group** — one `ad-input`-styled
  box holding three two-digit mono fields with colon separators (muted placeholders
  HH/MM/SS; aria-labels hours/minutes/seconds; seconds pre-filled `00` for a new trigger).
  Segment behavior: digits only; focusing a segment selects its content; a segment completed
  to two digits auto-advances focus to the next; ↑/↓ step the value with wrap (hours 0–23,
  minutes/seconds 0–59; from empty the first press lands on `00`); Backspace in an empty
  segment jumps back; a lone digit zero-pads on blur ("9" → "09"); pasting a full time into
  any segment distributes the digit pairs across it and the following segments. The three
  segments and the date combine into the stored `at`. Every trigger field box — the cron-expression input,
the native date input, the time group, and the Discord/iMessage text inputs — shares
one fixed 30 px height, so fields sitting side by side align exactly. An out-of-range time reddens the group
  with preview "Hours go 0–23, minutes and seconds 0–59"; a complete pair in the past
  reddens date and group, and the preview shows the §19 `/triggers/preview` error verbatim —
  the copy comes from the backend: "the time must be in the future";
  either state blocks Add. Both the cron and One time forms end with the timezone picker,
  then a **"Catch up if missed"** checkbox (the §14 checkbox, a native input in a `<label>`
  that hugs its content like the Discord mention box; the §4.3 `runIfMissed` field; checked by
  default for a new trigger, the stored value on an edit swap) and, under it, one muted
  note line flush with the form's left edge (the §3 sleep disclaimer, same muted-hint style
  as the Discord sender-filter note) whose first sentence follows the checkbox: checked →
  "If this `<machine>` sleeps through a scheduled time, the automation executes once when
  it wakes. `<§9 sleepMissNote>`"; unchecked → "If this `<machine>` sleeps through a
  scheduled time, that time is skipped. `<§9 sleepMissNote>`" - the machine noun and the
  sleepMissNote sentence follow the §9 per-OS table. App start shows no
  input — just the preview line "On app start — executes when you launch the app", and its
  picker chip renders disabled (title "Already added") while the list holds one. A discord or
  imessage
  trigger row whose §4.3 `connection` state is `error` shows the error as a red mono line under the
  row; state `connecting` shows a muted "connecting…" line; `connected` shows nothing. The
  **Discord editor**: a channel-id input (ASCII digits; red border otherwise), then a secret
  row — a **secret picker** for the bot token (the app's standard popover pattern: an
  `ad-btn-pill` trigger button — fa-key icon, the selected secret's mono name or the muted
  placeholder "Choose the bot-token secret…", fa-caret-down — opening a `PopMenu` with one
  row per stored secret: accent check column for the selected one, mono name with the §12
  amber NOT SET badge when the secret is a placeholder, the secret's `description` as a muted
  sub-line when present; picking closes the menu; always rendered, even when no secrets
  exist — the empty menu shows the muted note "No secrets yet — press New secret."). Picking
  stores the secret's §4.8 **id** (the §4.3 `secret` field); the pill always renders the
  live name resolved from the stored id, and an id matching no stored record (the secret was
  deleted since) renders a short id prefix in the §9.2 deleted-red treatment — the row's
  `connection` error line explains the breakage. Beside the picker sits a
  quiet **New secret** button (the row hugs left — the pill and the button
  sit together, not pushed to opposite edges), opening the shared §12 secret modal in add
  mode; saving it
  auto-selects the new secret in the picker — no hint text under the row; the picker
  placeholder and the setup guide carry the explanation. A
  **setup guide** disclosure sits directly below the kind-picker chip row while Discord is the
  selected kind, above the editor inputs (quiet toggle link "New to Discord bots? Step-by-step
  setup", chevron flips open/closed, the list animating through the §14 Collapse primitive —
  both setup guides) expanding to a numbered list that
  assumes no prior Discord-bot knowledge: (1) open
  discord.com/developers/applications — an external link (target _blank, opens in the default
  browser), sign in — skip any onboarding page Discord shows first — press New Application and
  name it; (2) on its Bot tab press Reset Token and copy the token — it shows only once; (3)
  still on the Bot tab, under Privileged Gateway Intents turn on Message Content Intent —
  without it the bot can't read messages; (4) press the New secret button below, paste the
  token as the value and Save to Keychain — the secret is selected automatically; (5) in the
  portal's left sidebar click OAuth2 and scroll to the OAuth2 URL
  Generator section; (6) tick the `bot` scope — a Bot Permissions grid appears below — tick
  View Channels there (if an Integration Type selector shows, leave it on Guild Install); (7)
  copy the Generated URL at the bottom, open it in the browser, pick the server, press
  Continue then Authorize — needs Manage Server on that server; the bot showing offline is
  fine; (8) in Discord open User Settings (gear icon), scroll the settings sidebar to the
  bottom, click Developer and turn on Developer Mode; close settings, right-click the
  channel → Copy Channel ID; (9) paste
  the channel id below, choose the bot-token secret, press Add. The editor then has an
  optional message-filter text input (§4.3 `pattern`; placeholder "Message filter — only
  messages containing… (optional)", title "Fires only when the message contains this text —
  case-insensitive, plain substring"), an optional sender-filter text input (§4.3 `author`;
  placeholder "Sender filter — only messages from these user ids (optional)"; accepts
  comma-separated ids, each digits only, same invalid styling as the channel input; directly
  below it a visible helper line (11.5px, `--text-muted`): "Fires only on messages from
  these Discord users — comma-separate several ids. A user id is a long number like
  234567890123456789 — right-click their name → Copy User ID (needs Developer Mode, enabled
  in step 8)."), and an
  "Only when the bot
  is mentioned" checkbox (the §14 checkbox, a native input in a `<label>`; §4.3 `mention`) — checked by default, so a fresh trigger fires only
  on @-mentions unless the user unticks it; the label hugs its content (`width: fit-content`
  — not `align-self`, which is inert outside a flex parent) so the click target doesn't span
  the editor's full width; preview line "On Discord message in `<channel>`";
  Add stays disabled until the channel is digits, a secret is chosen, and the sender filter
  is empty or a comma-separated list of digit ids. The
  **iMessage editor**: while iMessage is the selected kind, a **setup guide** disclosure sits
  directly below the kind-picker chip row (where the Discord setup guide sits; same pattern:
  quiet toggle link "How iMessage triggers work"): (1) this Mac's
  Messages account is the identity — no bot, no token: when the sender below texts it, the
  automation executes; (2) loop-safety note — messages this Mac sends never trigger, and
  iMessage can't text yourself anyway (your own messages come from the same Apple ID), so the
  sender must be someone else; to trigger it yourself, either create a new Apple ID, sign
  Messages on this Mac into it, and text that account — or use a Discord trigger instead,
  where a bot in your own server can receive your own messages; (3) grant the two permissions
  below; (4) enter the sender below exactly as Messages knows them — phone numbers in
  international form (`+1…`), or an email; to see the stored handle, open the conversation in
  Messages and press the ⓘ info button (formatting like spaces and dashes is fine — it's
  stripped automatically). Below the guide a **permission checklist** — two rows, each an
  icon + name + live status + action button:
  - **Full Disk Access** — status from §19 `GET /imessage/permissions` (`fullDisk`), re-polled
    every 3 s while the checklist is visible: granted → green check, "Granted"; missing →
    amber dot, "Needed — Autowright reads incoming messages from the Messages database" and
    an **Open System Settings** button (opens
    `x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles` externally,
    like every external link), so the status flips to granted moments after the user toggles
    it there.
  - **Messages automation** — the same endpoint's `automation`: granted → green check;
    denied → amber dot, "Denied — turn it on in System Settings → Privacy & Security →
    Automation" (plain text, no deep link — macOS has no pane-level URL for one app's
    Automation row); unknown → muted dot, "Not asked yet — Autowright sends replies through
    Messages" and a **Grant** button calling §19 `POST /imessage/permissions/automation-probe`
    (spinner while it blocks on the macOS prompt; the row re-renders from the result).
  Neither state blocks Add — a trigger saved without permissions parks in the §4.3 `connection`
  error state and heals when granted. Then the editor
  inputs: a **sender input** (placeholder "Sender — +15551234567 or an email"; §4.3 `from`;
  red border while invalid per the §4.3 rule — email, or `+`-prefixed phone after
  formatting strips; the invalid-input preview line reads "Needs a country code (+1…) or an
  email") and the same optional message-filter
  input as Discord (§4.3 `pattern`). Preview line "On iMessage from `<from>`" showing the
  normalized handle; Add stays
  disabled until the sender is valid. Cron and
  One time add a
  **timezone picker** below the input — the app's standard popover pattern (an `ad-btn-pill`
  trigger button: fa-globe icon, the chosen zone's mono name or the muted default "Local
  time", fa-caret-down — opening a `PopMenu`): a filter input at the top (placeholder "Filter
  timezones…", auto-focused, cleared on every open) narrows the list by case-insensitive
  substring; below it a scrollable list — "Local time" first (the default; stores no `timezone`;
  shown only while the filter is empty), then every IANA zone
  (`Intl.supportedValuesOf('timeZone')`), the current choice marked active; picking closes
  the menu. A non-local choice is
  stored as the trigger's §4.3 `timezone`, and the preview line (labels and "next:") reflects it,
  with "next:" always shown in local time. Empty list renders a
  an `EmptyLine` ("No triggers" — the §14 in-card empty line). Trigger edits apply immediately (§19 PATCH) — no version, no AI.
  No Execute-now button here — manual execution lives in the title row and the menu bar.
- **PARAMETERS** — directly editable here per the §4.2 edit behaviors; caption "Changes apply on
  the next execution." Row layout splits by control size:
  `toggle` and `number` rows keep label + control on one line — the label side flexes to the
  available width, the control sits vertically centered at the row's right edge, and the help
  text runs below the label at full width. `text`, `list`, and `kv` rows stack — label (with
  the amber NOT SET `MiniBadge` when a text param has no value) and full-width help on top, the editor
  underneath spanning the full card width (text inputs capped at 520px).
- **CONCURRENCY** card — the §6 settings, rendered for every automation (manual executions
  can run in parallel and queue via the §9.2 capacity popup, so the card is never inert). Two `number`
  rows using the §9.2 row layout: **"Max parallel executions"** (`maxParallel`, min 1) with caption "How many
  executions of this automation may run at the same time." and **"Max queued executions"**
  (`maxQueued`, min 0) with caption "How many executions wait for a free slot. Incoming
  messages beyond this are answered with a busy notice instead." Both PATCH immediately like parameters —
  no version, no AI. Through the PATCH round-trip the input keeps showing the committed
  number (never flashing back to the old value while the refresh is in flight); only a
  failed PATCH reverts it, alongside the error toast. Below the rows, when at least one firing is waiting, a live line "N waiting"
  with a quiet **Clear queue** button (§19 queue-clear; confirm copy "Cancel N waiting message(s)?
  Each sender is told." — the running execution is not affected, which the copy says).
  The waiting line and the memory caution enter with `.ad-anim-item` when they appear.
  N counts the automation's `queued` execution records (§4.6) held client-side — the same
  source the §7 Queued section lists, never a separate count carried on the automation.
  One source is what makes the number right: a promoted entry becomes `executing` on its own
  record the moment §19 `execution.started` arrives, so a running execution can never still be
  counted as waiting, and the line can never disagree with the Queued list.
  Raising `maxParallel` above 1 on an automation whose current version has a step referencing
  memory shows a persistent amber caution under the row, naming those steps: "`<step>` writes
  to memory. Parallel executions share one memory directory (§6), so two runs updating the same
  value can lose one of the updates." No modal and no block — the caution is inline, specific,
  and stays visible while the setting is above 1. An automation whose steps never touch memory
  gets no caution.
- **RECENT EXECUTIONS** — execution history rows (status badge, then the execution id's
  first 8 chars in faint mono — same short id the Executions page rows show — then
  trigger·version — a message-triggered row puts the §4.5 `triggerSender` between them,
  "Discord · Dave · v3" — time, duration, note text when present), linking to execution
  pages. The rows' source - shared with the failure notice's latest-execution lookup above -
  is a per-automation §19 query (`GET /executions?automation=<id>&limit=200`) fetched when
  the page opens, merged by id with the store's §7 window (window wins) so §19 events keep
  the rows live: the window alone may hold none of an automation's rows once 50 newer
  executions of other automations have finished. A failed fetch degrades to the window's
  rows alone - the next execution event or page re-open recovers.
- **MEMORY** card — mono size/updated info line; "Show in Finder", "Snapshot" and "Clear
  memory" buttons. Clear swaps the button row to an inline confirm: "Next execution starts
  fresh, like the first time. Current memory is snapshotted first." (pre-clear toggle off:
  "Next execution starts fresh, like the first time. Automatic snapshots are off — this
  can't be undone.") with red Clear / quiet Keep. Snapshot swaps it to a name input
  (placeholder "Name — optional", Enter saves) with
  Save / quiet Cancel; the button is disabled when memory is empty (title "Memory is empty").
  Below the info row, the §6.3 snapshot list (absent when there are none): one row per
  snapshot — title (the name, else "Snapshot"), mono meta "reason · version · size · files ·
  when", quiet row actions Restore / Rename / Delete. Restore swaps the row to an inline
  confirm "Replaces current memory — the current state is snapshotted first." (pre-restore
  toggle off: "Replaces current memory — automatic snapshots are off, so the current state
  is lost.") (accent
  Restore / quiet Keep; while an execution is live the row's Restore action is `disabled`
  with the tooltip "Blocked while an execution is live" — never a silent no-op click — and
  a raced 409 still surfaces as a toast);
  Rename swaps to a name input (Save / Cancel; empty clears the name back to "Snapshot");
  Delete swaps to "Delete this snapshot?" (red Delete / quiet Keep). Every inline swap —
  the card's button row and the snapshot rows alike — fades in (`.ad-anim-fade`, a keyed
  remount of the row): opacity only, so nothing jumps. The snapshot list itself enters with
  `.ad-anim-item` when the first snapshot lands. Toasts: "Snapshot
  saved." / "Memory restored — the next execution continues from the snapshot." / "Snapshot
  deleted."
  At the card's bottom, the "Automatic snapshots" section — the §6.3 toggles, one `Toggle`
  row per automatic reason, each with a plain-language explanation so users know exactly
  what they're switching off:
  - "Before a new version executes" — "Saves a copy of memory right before the first
    execution of a newly saved version, so you can restore how memory was if the new
    version mishandles it." (pre-version)
  - "Before clearing memory" — "Saves a copy right before Clear memory empties the
    directory, so a clear can be undone." (pre-clear)
  - "Before restoring a snapshot" — "Saves a copy of the current memory right before a
    restore replaces it, so a restore can be undone." (pre-restore)
  Edits apply immediately (§19 PATCH `snapshotSettings`) — no version, no AI.
- **STEPS** card — read-only step rows (number, name, description, tags; the whole row is a
  click target whose only right-edge affordance is an expand glyph (`fa-expand`): no
  "view script" text label, so narrow windows don't crush the row's middle column, and the
  glyph carries a "View script" `title` tooltip; the §11 editor's rows drop the glyph
  entirely). Clicking a row opens the **step-script
  modal**, a large §14 `Modal` card laid out as a two-column viewer: `min(1120px, 92vw)`
  wide, `overflow: hidden`, no header row and nothing that scrolls the card as a whole. Its
  height is fixed for the life of the open modal, so flipping between steps never resizes
  the frame, and sized to the automation's LONGEST script: the code pane's toolbar plus
  every line at its 12px/1.65 mono rhythm plus its padding, floored at 440px (room for the
  navigator) and capped at 82vh — an automation of short steps gets a card that fits them,
  never a mostly empty one. The left
  **step navigator** column (280 px, the card's own `--bg-menu` ground, hairline right
  border) is its own §14 overlay-scrollbar pane: a fixed 44 px header (the code pane's
  toolbar height, dim-hairline bottom border, so the two hairlines join into one line across
  the column divider and the eyebrows sit at one height) carrying, vertically centered, a
  faint mono "STEPS" eyebrow alone (no step count — the pane's "STEP N OF M" already
  carries it); then, directly under the
  hairline with no inset (as in the §7 rail — a row's own padding is its breathing room),
  one row per step in order — the number in faint mono, then
  the name (13/600 `--text` on the viewed row, 500 `--text-muted` on the others, hover
  brightening them). Clicking a row views that step. The unviewed rows are buttons; the
  viewed row is a plain, text-selectable block (`user-select: text`, not focusable) —
  clicking it would do nothing, and a button would stop the user from dragging over its
  name, description, chips and facts to highlight and copy them, which is exactly what a
  host or a file name in the fact list is for. Flipping steps never draws a focus ring:
  the viewed block cannot take focus, a clicked row unmounts as it becomes the viewed
  block (so no ring lingers on a step no longer viewed), and an arrow-key flip drops
  focus from whatever holds it — a chevron, or the page's step row that opened the modal
  and still holds focus behind it (wider than the card, its ring would peek past the
  card's edge) — so the accent bar alone marks the step: no box around a row, a chevron,
  or anything behind the backdrop. The viewed row carries a 2 px accent
  bar down its left edge and a faint fill, and expands beneath its name to hold the
  step's description (11.5 muted — the §14 list-row sub-line) and its **tag row**: the same §14 `Tag` chips the step
  row carries (same variant colors, red states, and tooltip bubbles; agents, secrets,
  packages, time limit, retries), wrapping freely, and then the step's **fact list**: labeled sections
  derived from a literal-only scan of the script — the same kind of scan the secret and
  import tags use — so users can see what a step reaches and touches without reading the
  code. Each section is an `Eyebrow` (the §14 primitive — 10 px/600 mono, `.09em`, `--text-faint`) over
  one bullet per item (a `--text-deco` dot, then 11.5 sans `--text-muted` text with a
  hanging indent); a section with nothing to show is absent, and so is the whole list when
  every section is. In order:
  - **PARAMETERS** — one bullet per distinct `params["<name>"]` / `params.get("<name>")`
    literal, each shown under its §4.2 definition's `label` (the raw name when no
    definition matches). The inputs come first, so the list reads top-down: what the step
    takes in, what it reaches, what it hands on.
  - **WEBSITES** — one bullet per distinct `http(s)://` host literal in the script, in order
    of appearance (an f-string host with an interpolation is not a literal and is skipped).
  - **ASKS THE AGENT** — one bullet per `agent` / `agents["…"]` `.ask` / `.read` / `.write`
    call site whose first string-literal argument is the prompt (adjacent literals joined,
    as Python concatenates them, so a prompt split across lines is never quoted from its
    first piece alone), in curly quotes with whitespace collapsed and, past 72 characters,
    truncated at the last word boundary before that limit with an ellipsis (never
    mid-word); call sites with no literal prompt fold into one trailing
    "`<n>` more call(s)" bullet ("`<n>` call(s)" when no call site has a literal). The user
    sees what is sent to the AI, not just which agent.
  - **FILES** — one bullet per workspace-relative file literal the script opens
    (`open("<f>"[, "<mode>"])`, `Path("<f>").read_*` / `.write_*` / `.open`), a write mode
    (`w`, `a`, `x`) or a `write_*` method marking a write, else a read; absolute, `~`,
    `..`, and interpolated names are skipped. Reads first: "Reads `<f>` from step `<n>`"
    naming the nearest EARLIER step that writes the same file ("Reads `<f>`" alone when
    none does); then writes: "Hands `<f>` to step `<n>`" naming the LATER steps that read
    it ("Hands `<f>` to steps 3 and 4"; "Writes `<f>`" when none does). Together they make
    the pipeline's hand-offs legible.
  - **MEMORY** — "Reads `<key>`" / "Saves `<key>`" bullets for the §6.1
    `memory.load("<key>")` / `memory.save("<key>")` literals.
  - **VERSION** — the one-bullet change badge: the step's script compared by NAME across
    the automation's stored versions (the §4.1 record's current version plus its
    `versions` history): "Unchanged since v`<k>`" (the earliest version in an unbroken run
    of identical scripts back from the viewed one), "Changed in v`<n>`" (identical to none
    of its predecessor's, which does carry a same-named step), "New in v`<n>`" (no
    predecessor carries the name). The §11 editor's modal compares the Draft against the
    current version — "Changed in this draft" / "New in this draft" — and an old version
    viewed from the Version menu against ITS predecessors; a create-flow draft with no
    stored automation shows no VERSION section.
  Bullets are text, not chips, because hosts, prompts and file names are too long for a
  chip; they carry no tooltip — the section label plus the bullet is already the plain
  language. The dots are unselectable, so a copied selection reads as the labels and
  lines alone. Only the
  viewed row expands, so the navigator reads as a table of contents with one open entry. The one difference from
  the rows: package chips appear here in both variants (the §11 editor modal reads the
  draft's declared packages, the detail modal the automation record's §6.2 `packages`
  list) while the detail ROWS still omit them. All detail lives in the chips' §14
  tooltips ("Why an agent" included); beyond the fact list's bullets the modal writes out
  no sentences, and a fact family with nothing to show simply has no chip or section (the
  time-limit chip always renders).
  The right **code pane** fills the rest of the card at full height on the `--bg-code`
  ground, so a short script never leaves empty modal beneath it: a fixed 44 px toolbar
  (hairline bottom border) carries, left, the faint mono "STEP N OF M" eyebrow followed
  by the step's §4.1 version-folder filename in dimmer mono (fallback "script"; the one
  place that filename appears in the UI) and, right, a "`<n>` lines" count ("1 line" in
  the singular; a script's single trailing final newline is neither rendered nor counted)
  and the control cluster — a find button (`fa-magnifying-glass`, "Find in script",
  `aria-pressed` while the find bar is open), previous / next step chevrons as
  `.ad-btn-icon` buttons (disabled at the ends; the ← / → arrow keys navigate too) and a
  close ✕ (Escape and backdrop click also close, standard `Modal` behavior). The toolbar
  never scrolls, so the filename and the controls stay put through a long script and
  never overlap code.
  **Find in script.** (The bar, its hook and its match/marking helpers are one shared
  primitive; the §7 execution view's LOGS pane renders the same bar as "Find in log".)
  The find button, or ⌘F / Ctrl+F while the modal is open, opens a
  36 px **find bar** under the toolbar (hairline bottom border, same ground; it stays open
  and keeps its query across step flips), left-aligned: a 280 px `.ad-input.compact` text field
  (placeholder "Find in script", focused on open — ⌘F on an open bar refocuses and
  selects its text; capped rather than full-width so its focus border never reads as a
  band across the code), a faint mono match counter in a fixed 72 px slot ("2 of 7";
  "No matches" for a query that hits nothing; blank while the field is empty — the slot
  is fixed so a changing count never nudges the field), previous / next match chevrons
  (`fa-chevron-up` / `-down` `.ad-btn-icon`, "Previous match" / "Next match", disabled
  with no matches, wrapping at the ends) and, at the bar's right end, a close ✕ ("Close
  find"). The bar carries no magnifier of its own — the toolbar's pressed find button is
  the state. Matching is a
  case-insensitive substring search over the script's rendered lines; every hit is
  wrapped in a `<mark>` on an accent-tinted ground (accent at 22 %, current match at
  50 %, radius 2, token color kept), and the current match is kept in view: the code pane
  scrolls so it sits mid-pane (never the page). Enter in the field steps to the next
  match, Shift+Enter to the previous; Escape in the field closes the find bar and clears
  the query without closing the modal, and the ← / → keys type in the field instead of
  flipping steps (the flip keys ignore every editable target). Typing a new query or
  flipping steps returns to the first match.
  Below it the script sits in its own overlay-scrollbar pane with a line-number gutter
  (faint mono, right-aligned, unselectable) beside every line and §11 `PyCode`
  highlighting; a long line wraps under its own number rather than scrolling sideways, and
  the code keeps a 28 px right inset so no line ever runs under the overlay thumb. The
  gutter numbers are unselectable and an empty line still carries its newline, so a
  drag-selected stretch of code copies as the script's own text, blank lines included.
  The pane's ground is uniform, so the §14 overlay thumb never crosses a color boundary;
  the modal insets it to `right: 6px` (from the app-wide 3px) so the card's 12px corner
  radius never slices its bottom into a wedge. Switching steps remounts the code pane with
  the §14 keyed fade (which also resets its scroll); the navigator and the toolbar
  cluster stay mounted, so the row under the pointer and the buttons never flash while
  flipping. One step shows at a time; there is no inline expansion. Step tags are
  display-only — never menus, and every tag carries a plain-language tooltip (the §14 Tag
  tooltip bubble — custom, not the native `title`) explaining
  what it shows (one shape everywhere: what the tag is, then " — `<why>`" appended when a
  why exists, never a why alone): an agent step carries one microchip-icon tag per entry in
  its `agents`
  list — the entry's id resolved to the LIVE agent's name, so a rename updates the tag
  immediately; an id matching no stored agent renders the red deleted state — when the
  automation's §4.1 `unresolvedReferences` carries the id, the red tag shows the archive
  record's NAME (tooltip "This step calls `<NAME>` from the imported file. No agent on
  this Mac matched it, so this step would fail."), otherwise a short id
  prefix (tooltip "This step calls an agent that no longer exists — this step would fail") —
  (tooltip "This step calls the `<name>` AI agent", with " — `<why>`" appended — the
  entry's §4.1 per-agent role note, falling back to the step's `why`; an empty list shows a
  single tag naming the automation's first enabled agent, fallback "agent", with the
  step-`why` tooltip rule), a step carries one key-icon tag per secret it
  uses (its `secrets` entries' ids unioned with the literal `secrets["<id>"]` references in
  its code, each resolved to the live §4.8 secret's name — a dangling id gets the same red
  deleted treatment: the archive NAME when `unresolvedReferences` carries the id (tooltip
  "This step uses `<NAME>` from the imported file. No secret on this Mac matched it, so
  this step would fail."), else a short id prefix;
  tooltip "This step uses the `<NAME>` secret from your Keychain", with " — `<why>`"
  appended when the declared entry carries its §4.1 per-use note — code-referenced ids
  with no declared entry have none), and every
  step carries one clock-icon tag showing its §4.1 time limit: the step's `timeout` humanized
  ("60s", "15m", "1h" — hours when divisible by 3600, else minutes when divisible by 60, else
  seconds), the 900 s default ("15m") when the step sets none, "no limit" for `noTimeout`
  steps. Tooltip: "This step is stopped if it runs longer than `<label>`" / no-limit: "No time
  limit — this step runs until it finishes or you cancel or skip it." A step whose §4.1
  retry budget is set carries one rotate-icon tag right after the clock tag: `retries`
  humanized ("1 retry", "`<n>` retries"), "infinite retries" for `infiniteRetries` steps; a
  step with neither (the 0 default) shows no retry tag, so a budget is visible before it is
  ever used, not only as §7 attempt pills after a failure. Tooltip: "If this step fails it
  runs again, up to `<n>` more time(s)" ("1 more time" in the singular) / infinite: "If this
  step fails it runs again until it succeeds, or you cancel or skip it." Agents and
  secrets are changed on the edit page.
- **SPEC panel** — collapsible (expand/collapse header toggle), expanded by default; the automation's spec blocks rendered through the shared §4.5 Markdown renderer, footer: "The AI regenerates the steps from this
  document when you edit it. Every change mints a new version — older ones live in the Version
  menu on the edit page."

**Delete confirm modal** — "Delete this automation?" / "`<name>` will be deleted — its triggers
stop, and its versions and memory go with it. Past results stay in Executions." When an execution is
live an amber line is added: "An execution is in progress — deleting cancels it." (confirming cancels
the execution, then deletes). Buttons Cancel / red "Delete automation".

### 9.3 Developer log overlay

A low-priority debug surface, ComfyUI-style: with the §4.9 `developerMode` setting on, pressing
`` ` `` (Backquote) in the main window toggles a full-window log overlay; Escape also closes it. The
key is ignored while focus is in an editable element (input, textarea, contenteditable) and the
whole feature is inert — no listener effect, overlay never renders — while `developerMode` is off
(turning the setting off closes an open overlay). Main-window surfaces only, never the menu-bar
panel.

The overlay is a fixed panel covering the entire window (full width and height,
z-index above the shell, `--bg-code` well, mono text at 11.5 px,
`pre-wrap`). A slim header row — padded down 38 px so it clears the macOS traffic
lights (pinned at 14,14) and, on Windows, the 40 px native `titleBarOverlay` its close ×
would otherwise sit under; on Linux (native frame, nothing to clear) the padding drops to
10 px, gated on the §9 store `platformOs` token — holds a `requests` tab first, then one
tab per log file —
`app.log`, `backend.out.log`, `backend.err.log`, `vite.log` — file tabs showing only files
that exist, plus a close ×. Active tab persists only for the overlay's open lifetime;
default is the `requests` tab (always present).

Data path (log-file tabs): the renderer polls `window.autowright.tailLogs()` (preload →
`tail-logs` IPC in main) every 1 s while the overlay is open — no file watchers, nothing runs
while closed. Main resolves the logs dir exactly like §5 (`~/Library/Logs/Autowright`, or
`<AUTOWRIGHT_HOME>/logs` when set) and returns `[{ name, text }]` for each existing file of
the four, where `text` is the file's last 64 KiB (partial first line trimmed when the read is
mid-file). The body auto-follows the tail while scrolled to the bottom; scrolling up pauses
the follow until the user returns to the bottom.

**Requests tab** — browses the §5 request-log files (one file per HTTP/agent request under
`<logs dir>/requests/`). Two-pane body: a left column (280 px, scrollable, mono 11 px) lists
the file names **sorted descending** (newest first — the timestamp prefix makes name order
chronological); the right pane renders the selected file's full content with the same
mono styling as the log tabs (no tail-follow — request files are written once,
complete). Clicking a name selects it; the selection persists across list refreshes and is
cleared if its file was pruned. While this tab is active the renderer polls
`window.autowright.listRequestLogs()` (→ `list-request-logs` IPC: sorted-descending name
array, `[]` when the directory is missing) every 1 s; selecting a name fetches it once via
`window.autowright.readRequestLog(name)` (→ `read-request-log` IPC: file text, `null` when
gone; main rejects any `name` that is not a plain basename) — request files are written once,
complete, so no re-fetch is needed. Empty states: "No request logs
yet — make a request with Developer mode on" (empty list) / "Select a request" (no selection
while the list holds files; with an empty list the right pane is blank — the list's own
empty note carries the message).

### 9.4 About page

640 px page (Settings' width), "About" title, reached from the About nav row
pinned at the sidebar bottom (fa-circle-info, no count pill, §9). Three eyebrow
sections with Settings' anatomy (mono eyebrow + card of rows) — **APP**,
**UPDATES**, **LEGAL** — and new about-ish content (credits, support links)
lands in one of these or a new eyebrow here, never on Settings.

Document rows (Privacy policy, Terms of service, Open-source libraries, What's
new) render through the §14 `DocModal` primitive — one **doc modal** pattern:
width 680, `h2` title, body caps at 62 vh and scrolls, content
rendered through the shared §4.5 Markdown renderer, quiet Close in the footer.
Each document loads through a dynamic `?raw` import so it stays out of the main
bundle, fetched once on first open. The fetching body is a `LoadingRow`; a failed load never strands the modal on
"Loading…": the body swaps to a `--red-text` line ("Couldn't load the document.")
with a bordered **Retry** button that re-attempts the import. The three LEGAL
documents share one modal local to this page; the What's-new document renders in
its own instance of the same pattern, mounted at the app shell level so it can
also open itself after an update (below) with the About page nowhere in sight.

**APP**

- **Autowright** — title with the running version beside it in mono (`v<version>` from
  `GET /state`); sub-line "Open source, MIT licensed. The whole app runs on this Mac.";
  right-side "View on GitHub" button-styled link (label followed by the Font Awesome
  `fa-arrow-up-right-from-square` external-link icon, 10 px, never the ↗ text glyph) to
  https://github.com/hansololz/autowright (plain `target="_blank"` anchor — the main
  window's window-open handler denies the popup and routes the URL to
  `shell.openExternal`, so it lands in the default browser).

  **External-URL policy** (both windows — main and the menu-bar panel carry the same handler):
  a window-open URL is opened only when its scheme is `https:`, `http:`, or `mailto:`, plus the
  one `x-apple.systempreferences:` deep link the §9 permission checklist uses; anything else is
  dropped silently. §7 result HTML is AI-authored and may echo attacker-controlled text from an
  incoming Discord/iMessage message, so a `file:` (or other registered-scheme) link in a result
  must not be able to launch a local app on a user click. Both windows also block top-frame
  navigation (`will-navigate` → `preventDefault` for anything but the app's own URL): the
  preload exposes the backend bearer token, which must never be reachable from a remote origin.
- **Website** — sub-line "The project's home page, with a quick tour of what Autowright
  does."; right-side "autowright.ai" link (same icon and external-anchor mechanism) to
  https://autowright.ai (the §17 `docs/` landing page).

**UPDATES**

- **Updates** — checked automatically by default (§4.9 `automaticUpdateCheck`, default
  true; §3 automatic-check bullet); turning the toggle off restores strict manual-only
  checking — no background or launch checks (PRIVACY.md documents both modes).
  Downloads and installs are manual in both modes.
  Everything runs in the Electron main process over the §3 IPC handlers; the renderer
  never talks to GitHub or the feed itself. The row opens **pre-armed** when the store
  holds `updateAvailable` (fed by the §3 update-available event + invoke at store boot;
  set by any check — manual or automatic — that finds a newer version): it renders the
  `available` state below without a button press. While the row is in the `available` state
  the action button carries a persistent selected-style highlight — `border-color:
  var(--accent-sel)` with `--text` label, the same accent border the selected-card pattern
  uses — pointing the eye at "Download update" however the page was reached (sidebar update
  row, direct About visit, or a manual check). The highlight drops the moment the row leaves
  `available` (download starts, up-to-date, error). Manual results feed the same shared
  state: `available` sets `updateAvailable` (the §9 "Update available" nav row appears and
  persists across navigation), `uptodate` clears it, `error` leaves it alone — the §3
  clearing rule.
  The "Check for updates" button calls
  `update-check` (one fetch of the §3 feed) and reads "Checking…" (disabled) while in
  flight. Results render in the row's sub-line: `available` → "Version `<x.y.z>` is
  available." and the button becomes **"Download update"**; `uptodate` → "You're up
  to date."; `error` → "Couldn't reach GitHub. Try again later." The version
  compare (in main) is numeric on dot-split parts, ignoring a leading `v`; a
  malformed version counts as not newer. "Download update" calls `update-download`
  and reads "Downloading…" (disabled); while it runs a §14 `ProgressBar` renders
  under the sub-line, fed by the §3 `update-progress` IPC events — determinate
  percent, or indeterminate when the download size is unknown; after the stream
  finishes it holds 100% while Squirrel verifies and stages the
  update (§3 — on macOS; the other OSes settle when the download finishes). On `{ ok }` the bar goes
  away and the button becomes **"Restart to
  update"** with sub-line "Update downloaded. Only the app restarts, not your
  automations."; on `{ error }` the sub-line shows "Update failed: `<error>`" and
  the button reverts to "Check for updates" (an unsigned dev build always lands
  here — same code path, real Squirrel error). "Restart to update" calls
  `update-install`; a `{ busy }` answer renders "An automation is executing. The
  update installs when you restart after it finishes." and keeps the button;
  otherwise the app quits and relaunches updated (the backend restarts on the next
  launch's §3 version-compare flow). Idle sub-line follows the toggle below: off →
  "Updates are only checked when you ask. Nothing runs in the background."; on →
  "Checks once a day. Downloads still start only when you ask."
  **Homebrew-managed fork:** the page asks §3 `update-brew-managed` at mount and again
  on every "Check for updates" press (so a brew install or uninstall reflects without
  an app restart). When it answers true, only the `available` state changes: sub-line
  "Version `<x.y.z>` is available. This copy is managed by Homebrew. Update it with:"
  followed by a copyable command block (§14 `CommandBlock`) holding
  `brew upgrade --cask autowright` with a Copy button ("Copied to clipboard." toast,
  same pattern as the §4.9 CLI PATH row). The action button stays **"Check for
  updates"** (enabled; re-checking is always allowed), the accent-sel highlight does
  not apply (there is no Download action to point at), and the `downloading` /
  `downloaded` states are unreachable. Every other state, and the whole flow on a
  non-brew copy, is unchanged. The §9 "Update available" nav row behaves identically
  in both modes; in brew mode it is the notice that leads here.
- **Check for updates automatically** — toggle row between Updates and What's new,
  bound to §4.9 `automaticUpdateCheck` (default on). Sub-line "Once a day, ask
  autowright.ai whether a newer version exists. Downloads still start only when you
  ask." Writes PATCH `/settings` like the Settings-page toggles (§4.9 one-apply
  path: the renderer pushes apply-settings on every settings change, and the shell's
  reconcile starts or stops the §3 automatic check — turning it on checks
  immediately). The row lives here, not on Settings — updates are About-page
  territory (§4.9).
- **What's new** — sub-line "What changed in each version of Autowright.";
  right-side "View" button opens the **What's-new modal**: the shell-mounted doc
  modal (title "What's new") rendering `docs/CHANGELOG.md` (§17) — the
  canonical curated release notes, every released version newest-first — through
  the same raw import and first-H1 strip as the privacy policy. The in-app
  changelog is the authoritative user-facing one; the GitHub releases page keeps
  its auto-generated notes and is not linked from here.

  **Post-update: no auto-open.** The modal never opens by itself. The first
  launch after an update lands on the normal home page like any other launch;
  the release notes are reachable only through this row's View button. The
  renderer still keeps the last version it ran under the localStorage key
  `ad-last-seen-version` (beside `ad-onboarded`, §10): at store boot, once
  `GET /state` supplies the running version, the key is silently rewritten to
  it whenever it differs or is missing. Maintaining the key without showing
  anything keeps the ground truth current, so a future re-enable of the
  auto-open would fire only for versions installed after that point, never for
  the backlog accumulated while it was off. The check runs once per app launch,
  not on every `/state` poll. The §13 menu-bar panel window is exempt and does
  not write the key (the main window owns the record of what it has run).

**LEGAL**

- **Privacy policy** — sub-line "What Autowright collects, which is nothing, and where
  your data lives."; right-side "View" button opens the doc modal (title
  "Privacy policy") rendering `docs/PRIVACY.md` (§17) — the canonical
  copy, shipped into the bundle by the raw import, so the same text serves
  GitHub visitors and the app. The file opens with an `# Privacy policy` H1 for
  GitHub; the app strips the first H1 line before rendering (the modal title
  already says it).
- **Terms of service** - sub-line "No warranty, and your automations are your
  responsibility."; right-side "View" button opens the doc modal (title "Terms of
  service") rendering `docs/TERMS.md` (§17) through the same raw import and
  first-H1 strip as the privacy policy.
- **Open-source libraries** — sub-line "Everything Autowright is built on, with
  each project's license."; right-side "View" button opens the doc modal (title
  "Open-source libraries") rendering
  `app/src/acknowledgements.md`. The file is generated — never hand-edited — by
  `scripts/gen_licenses.py` (§17) and checked in; `build.sh` regenerates it on
  every build so it tracks dependency changes. It lists every shipped component —
  the npm production closure (`npm ls --omit=dev --all --json`; platform-independent,
  since every platform-gated npm package in the lockfile is dev-only) plus Electron
  (dev dependency, but its runtime ships in the bundle), and the backend's
  recursive distribution closure of the `autowright` package (dev extras
  excluded) — each entry: name, version, license id, and the package's license
  text when it ships one.

  The Python closure is the union across macOS, Windows, and Linux, so the file
  is a superset identical no matter which platform regenerates it (regenerating
  on one OS must never drop another OS's components). Environment markers are
  evaluated against all three target environments; a marker-gated distribution
  absent from the local venv (e.g. `tzdata` on Windows, the Linux keyring
  stack) is resolved from a downloaded wheel cached under
  `build/license-wheels/` (gitignored; any platform's wheel serves, since the
  license text is platform-independent, and a cached wheel is reused so
  regeneration works offline after the first run). An entry that ships on only
  some platforms carries a platform note in its heading, e.g.
  "(Windows only)" or "(Linux only)", and the intro paragraph says such
  entries ship only in that platform's builds. A distribution that cannot be
  resolved locally or from a wheel aborts generation with an error; it is never
  silently dropped, since silent under-attribution is the failure mode this
  design exists to remove.

The LEGAL card ends with a muted disclaimer paragraph (footer text inside the
card, below the rows): "Autowright is provided as is, without warranty of any
kind (MIT License). Automations execute scripts written by an AI agent. Those
scripts can do anything your user account can do on this Mac. Review every
change before you accept and execute it. You are responsible for what your
automations do; the author accepts no liability for any damage or loss they
cause."

### 9.5 Report issue modal

Reached only from the §9 "Report an issue" nav row. A shared-`Modal` dialog, title "Report
an issue" with a muted sub-line directly under it — "Reports are filed as GitHub issues. A
GitHub account is required." — so the requirement is stated before the user types. Open
state held in one store boolean `reportOpen` (open/close actions) — opening never navigates,
closing restores nothing because nothing changed. Contents, top to bottom:

- **Type toggle** — Bug / Feature request pair, default Bug. Decides the GitHub label
  (`bug` / `enhancement`).
- **Title** — single-line input, placeholder "Summary". Becomes the issue title.
- **Details textarea** — multiline, label and placeholder follow the type toggle. Bug: label
  "What happened?", placeholder "What did you expect, and what happened instead? Please give me as much context as possible so I can reproduce the issue.". Feature
  request: label "What do you need?", placeholder "What would it do, and why do you need
  it?". Text entered survives toggling the type — only label/placeholder swap. Used verbatim
  in the issue body.
- Both fields carry the app's standard text-field dimensions (`.ad-input` +
  the class owns its `11px 14px` padding and 13 px/1.5 type — the modal sets nothing inline) and share one text style —
  so title and body read identically.
- **Include environment info** — toggle (the shared §14 Toggle: this row is a settings-style
  switch, not a §14 checkbox), default on, with the rendered info block visible below it (mono, muted) so the
  user sees exactly what would be included. Block lines — exactly two: app version (store
  `version` from `GET /state`, falling back to the Electron bundle version riding on the
  `platform-info` answer — the line never shows a bare "v") · OS name + release + arch
  (`platform-info` IPC: preload `platformInfo()` → main answers `{ platform, osName,
  release, arch, version: app.getVersion() }` — the renderer has no other OS-details
  source; `osName` is the §2 platform layer's display name — "macOS" / "Windows" /
  "Linux" — with a renderer-side map on `platform` as the fallback, never a hardcoded
  literal; with no platform-info at all the line reads "Unknown OS (version unknown)"). Nothing else — no location, backend, or update state. **Never** in
  the block or the issue body: the backend bearer token, secret names or values, raw log
  contents.
- Footer: quiet **Cancel** (closes) · primary **"Open GitHub issue"** followed by the Font
  Awesome `fa-arrow-up-right-from-square` external-link icon (11 px, not the ↗ text glyph) — an anchor carrying
  `.ad-btn-primary`; the §14 link-as-button rule keeps `--on-accent` text and no underline on
  hover, so it is indistinguishable from a real primary button.

Open action: a plain `target="_blank"` anchor (the §9.4 external-URL policy routes it to the
default browser) to `https://github.com/hansololz/autowright/issues/new` with
`URLSearchParams`-built `labels`, `title`, `body`. The title is the Title field's text and
the body is a heading matching the type (`### What happened` for Bug, `### What do you need`
for Feature request) + the textarea text, then `### Environment` + the info block
(section present only while the toggle is on). GitHub caps prefill URLs around 8 KB, so the
body is clamped to 6 KB before encoding. The repo URL is one shared constant with §9.4 — never two copies. The app itself
sends nothing anywhere: opening the browser is the only outbound action, and the user reviews
the prefilled issue on GitHub before submitting.

## 10. Onboarding (2 steps, step label top-right in mono)

The onboarding root paints the flat `--bg-content` page background (§9) — the same single
color as the app's content pane, with no gradient or glow overlay.
Onboarding shows whenever `ad-onboarded` (§15) is unset — existing agents or automations do NOT
bypass it: step 1 always renders. When prior data exists (any agent or any automation), step 1's
Continue goes straight to the app shell instead of step 2. The step label ("Step 1 of 2" /
"Step 2 of 2") renders only when no prior data exists — with prior data step 1 is the only
screen, so no counter shows. Onboarding itself never installs the CLI shim (a dedicated step
existed and was removed 2026-08-16). Instead, the §3 one-shot first-run install runs silently
at renderer boot regardless of onboarding state — with `cliEnabled` defaulting true (§4.9), a
fresh install has the `autowright` command ready by the time onboarding finishes; the §4.9
COMMAND LINE card stays the only interactive install entry.

**Step 1 — Welcome.** Logo, headline "Recurring jobs, done exactly the same way every time.",
then a live self-check card "Getting Autowright ready" with three steps (Checking your settings,
Loading your automations, Starting the execution engine) with pulsing dots and durations, ending in a "READY / All set"
well with chips (Settings created, Folders in place, plus "Agent found" if an agent is already
configured and "Automations found" if automations already exist). Continue appears only when
done; its label is "Continue →" when prior data exists (going straight to the app), otherwise
"Connect your AI →".

**Step 2 — Connect your AI.** A searching spinner ("Looking for an AI already on this Mac…",
shown ≥1.9 s), then the §19 `GET /agents/detect` result rendered as cards. Detection reports
the four harnesses (Claude Code / Codex / Gemini CLI / OpenCode) with real installed
and signed-in state; installed harnesses render as "FOUND ON THIS MAC" cards (detail line =
real version plus sign-in state, e.g. "1.0.24 · signed in" / "1.0.24 · not signed in yet"),
and every harness that is
**not** installed renders as a suggestion card alongside (the app helps install all four).
Ollama is never a card of its own — the local path lives entirely in the "Free local AI"
card below (a suggestion card, unless every piece is already present — then it renders in
the found section).
Suggestion cards use the same full-width row anatomy as found cards — a single vertical list
(no tile grid), title plus one-line detail on the left, the action slot on the right; busy
states (install/pull progress, sign-in wait, install failure) stack full-width below the title
line. When at least one provider was found, the suggestion list sits behind its own neutral
eyebrow "OR TRY SOMETHING NEW" (neutral text color — accent stays reserved for the detected
section), which acts as a collapse toggle with a chevron icon: the list starts minimized
(collapsed) and clicking the eyebrow expands/collapses it. The expanded/collapsed state
persists across step navigation like the rest of onboarding state. When nothing is detected,
there is no eyebrow and no collapse — the list is always visible, with a note card above it:
"No AI app was found on this Mac — here are some suggestions for moving forward."

Every card resolves inside itself — there is no page-level Continue button, no radio selection,
and no multi-ready banner. All step-2 cards keep the neutral card border in every state —
no accent tint and no "Connected" label on connect; the Use-as-default button alone is the
success signal (the accent "FOUND ON THIS MAC" eyebrow alone marks the detected section). Each card carries a single
action slot that advances through its states in place. All machines are real — backend installs,
real sign-in checks; no simulation in any mode:
- **Found card, signed in** — the connection check runs automatically as soon as
  the cards land; the user never has to ask for it. The card starts on an inline spinner
  "Checking connection…" (real §19 `POST /agents/check-harness`) → a primary
  "Use as default →" button in the same card — one uniform label on every card (the card
  already names the provider); it states the pick's effect (that provider becomes the
  default agent) instead of a bare "Continue". A failed check shows amber
  "Not ready — `<reason>`" with a "Check again" button.
- **Found card, not signed in** — skips the auto-check (it would fail); sign-in help only
  when necessary: amber "Sign in" button →
  §19 `POST /agents/login` → waiting state (amber pulsing dot; copy matches the login method
  the backend reports: browser for Codex — "We opened your browser — sign in there and come
  back. We'll notice on our own."; Terminal for the others — "We opened Terminal — finish
  signing in there and come back. We'll notice on our own."), with "Cancel" returning to idle.
  The UI polls §19 `GET /agents/signin/{id}` every 2 s; once signed in the card runs the
  connection check automatically and lands on Connected + Use as default.
- **Setup status line** — once every found card's auto-check has settled (none still
  checking), a line under the found section says whether the user can move on: "You're
  ready — pick a connected AI as your default, or set up another below." when at least one
  found card is connected, otherwise amber "More setup needed — finish the steps above
  before continuing."
- **Suggestion card** (one per missing harness) — "Claude" ("Set up Claude Code") /
  "Codex" / "Gemini" / "OpenCode" (each "Set up `<name>`"): install via §19
  `POST /agents/install` → labelled progress ("Installing `<name>`…"; determinate bar when the
  `harness.install` stream carries a percent, indeterminate otherwise; the stream's current
  step line renders under the bar — also while the bar is determinate, so post-download steps
  like "Unpacking…" are explained instead of looking stuck) → then the sign-in flow
  above **only if the provider needs an account and isn't signed in** → connected:
  "Use as default →" alone. An install failure shows red
  "Install failed — `<first error line>`" with "Try again". There is no sudo step: every
  install lands in user-writable locations (§19 channels), so macOS never prompts for an
  admin password.
- **Free local AI card** — always shown regardless of what was detected: OpenCode driving a
  local model through Ollama (title "Free local AI"). The card owns three pieces: OpenCode
  installed (from detection), Ollama serving, and a model installed (both from §19
  `GET /ollama/status`) — **any** installed model counts: the first model from
  `GET /ollama/status` becomes the card's model, and `qwen3:8b` is only the download
  fallback when none is installed. The card is the last suggestion card — except when all
  three pieces are already present at detection, in which case it renders as the last
  "FOUND ON THIS MAC" card instead (same card and machine; the found-section status line
  counts it like any found card, and its body reads "OpenCode with Ollama and `<model>` —
  local to this Mac, works offline. Best for simple steps — for authoring automations, a
  cloud option gives stronger results."). Placement is decided once at detection and never
  moves mid-flow — the qwen3:8b recovery download below keeps the card in the found
  section. Every body variant of this card ends with the same fit sentence: "Best for
  simple steps — for authoring automations, a cloud option gives stronger results." With no
  model found the body reads "Sets up
  OpenCode with Ollama and Qwen3 8B. Local to this Mac, works offline." plus the fit
  sentence and the button
  "Download and install · 5.2 GB"; with a model found the body reads "Sets up OpenCode with
  Ollama and `<model>`, already on this Mac. Works offline." plus the fit sentence and the
  button "Set up local AI"
  (only the still-missing pieces install — no model download). When every piece is already
  present as the cards land, the card skips the install button and runs the connection check
  automatically (§19 `POST /agents/check-harness` with harness OpenCode and the card's
  model) → "Use as default →". A failed check shows the amber not-ready line (the
  model-missing reason names the card's model) with "Check again" — plus, when the check ran
  against a found model, a "Download Qwen3 8B · 5.2 GB" button that discards the found model
  and pulls `qwen3:8b` instead (recovery for installed models that can't chat, e.g.
  embedding-only ones). Otherwise the install button runs only the **missing** pieces, in
  order — OpenCode (§19 install), Ollama (§19 install), the model (`POST /ollama/pull` of
  `qwen3:8b`; the bar renders the stream's `percent` field — the §19 single overall pull
  percent, so the bar never resets per layer — with the current output line under it, and
  the download continues in the background) — labelled
  "Step k of n — Installing OpenCode… / Installing Ollama… / Downloading Qwen3 8B…" where n
  counts the missing pieces, then lands on the same connection check → connected. A failure
  at any piece shows red "Install failed — `<first error line>`" with "Try again", which
  resumes at the still-missing pieces.

Clicking a card's Use-as-default button is what picks the provider — it commits the agents and
lands in the app shell. The picked provider becomes the default agent, all
connected/ready cards are committed as agent records — a harness card as
`{ name: null, harness, mode: default, model: null }`, the Free local AI card as
`{ name: null, harness: OpenCode, mode: ollama, model: <the card's model> }` — the found
model, or `qwen3:8b` after a download (a null name always falls
back to the harness name for display, so agent labels read harness · model, e.g.
"OpenCode · qwen3:8b" — never the model twice) — and any existing
automations get the chosen default agent.

The commit is idempotent against the backend's live agents (§4.7 grant-name uniqueness): each
card resolves by its effective grant name (name, else harness — case-insensitively) against a
fresh `GET /agents` read at commit time — never the renderer's snapshot, which can lag a
partially-landed earlier commit — and a match is **reused** instead of re-posted. The picked
card commits first, and the same rule dedupes within the commit itself: when the plain
OpenCode card and the Free local AI card are both connected, both resolve to the grant name
"OpenCode", so only the first in commit order (the picked card, else the found card before
the local card) is created and the other binds to it. A POST that still answers the §4.7
already-exists 422 (an agent of that name landed mid-commit) re-reads the agents and binds to
the existing record. A commit therefore never surfaces "agent names must be unique" — the
failure that used to strand onboarding on step 2, where every retry (and Skip) re-hit the
same 422. While committing, all Use-as-default buttons are disabled
and the pressed one swaps its label for a `LoadingRow`-style spinner + "Setting up…" (§9
busy-commit convention), and "Skip for now" disables with them (it fires the same commit —
an enabled skip would double-fire it). Otherwise "Skip for now" is always
available (commits any connected providers, lands in the app shell). Persistent footer: the
two green-dot promises (§1).

Installs and model downloads run in the backend, so an in-flight model download keeps going
after onboarding hands off to the app — it "finishes in the background" as promised. Agent
installs never need admin rights (§19 channels are all user-writable), so onboarding never
shows an admin prompt.


## 12. Agents & Secrets pages

**Agents.** Page header sub-line: "Used for authoring automations and by automations for non-trivial tasks." Tile grid of agent cards — same grid as the Automations list (§9.1,
`repeat(auto-fill, minmax(310px, 1fr))`), not a vertical list. Cards carry no action row —
only the transient `LoadingRow` (Checking locally… / Reconnecting…) pins to the card bottom
(`margin-top: auto`) while a check is in flight. Badge states Checking (cyan) / Connecting / Ready (green) /
Needs setup (amber). Statuses are cached in the renderer for the app session: each agent is
checked once, staggered, on the first Agents page visit that sees it (new agents get checked on
the next visit); later visits render the cached badge with no re-check. The cache entry for an
agent updates when its edit form saves ("Connecting" until the fresh result lands, §4.7 check
re-run right after the save), when the reconnect flow's check answers (§12 form banner), and
when the edit form's "Check connection" action runs.
Each card shows the agent's `description` detail line — the real §4.7 description only, never
generated marketing copy (the description is drafting input, §8 grants yaml); when the description is empty
the line reads "No description yet. Add one to tell the authoring agent what this agent is
for." —
and a **USED BY** row of clickable automation chips (fallback "Not used by any automation yet.").
USED BY means actual reference, not permission: an automation is listed when the agent is its
writer (`agent_id`) or a current-version step carries the agent's id in its `agents` list. The
`enabled_agents` grant alone never counts — same rule as secrets, whose usage is step-code
references, not `allowed_secrets` (§12 Secrets).
There is no Edit button and no per-card menu — the whole card is
clickable (same hover treatment as the Automations list tiles) and opens the §12 edit form; a
Needs-setup card opens it with the reconnect banner. A USED BY chip never opens the card's
edit form (the click stops there): it navigates to its automation's detail page by the
`usedBy` entry's automation id (§4.7 — never a name lookup, which duplicate names would make
ambiguous); an entry whose id no longer resolves in the loaded list renders as an inert chip.
The agent actions (check connection, make default, remove) live on the edit form's overflow
menu (below), not on the card.
Every card carries one action: a square accent icon button at the title row's right
(`.ad-btn-exec` — the same style as the Automations list's per-card Execute now button — plug
glyph). Clicking it does not navigate; what it does follows the cached check. On a Ready card
it is "Check connection" (title/aria): the same timed §19 check as the edit form's menu row —
badge back to Checking while it runs, success toasts "`<name>` answered in X.X s. Ready.",
failure toasts "`<name>` didn't answer. It needs setup." On a Needs-setup card it is
"Reconnect": the reconnect check (badge flips to Connecting, LoadingRow "Reconnecting…"),
success toasts "Connected. Signed in as you.", failure toasts "Still signed out. Finish
signing in, then try again." While a check is in flight the button renders disabled with a
spinner glyph (title/aria "Checking…"), like the list's executing state. Empty state (dashed card): "No agents yet. Existing automations still execute on
schedule, but you need an agent to create or edit them." + CTA "Add your first agent".

**New / Edit agent** form (720 px, one form — title "Add an agent" and submit "Add agent",
switching to "Edit agent" / "Save changes" when editing). Edit mode is addressed by navigation state: opening a card puts
the agent's id (`agentEditId`) in the nav snapshot, so browser back/forward re-enters the same
edit form — never a blank add form; navigating anywhere else clears the id, and if the agent no
longer exists the form redirects to the Agents page. The reconnect banner keys off the
session-cached agent check being `needs` for that id.
In edit mode only, the title row carries an overflow (ellipsis) menu button at its right;
its popover opens right-aligned. It holds, for ready agents (per the session-cached check),
"Check connection" — a real §19 `/agents/{id}/check` call timed by the renderer: the cached
check returns to Checking while it runs (so the Agents-page badge reflects it), success
toasts "`<name>` answered in X.X s. Ready.", failure flips the cached check to Needs setup,
shows the form's reconnect banner, and toasts "`<name>` didn't answer. It needs setup." — and,
when not default, "Make default" (toast "`<name>` is now the default. New automations use
it.", the name falling back to the harness name; the default flag is read live from the
store, so the row disappears once the change lands). For every agent it holds "Remove agent…"
(red, confirm modal — same title, body, and used-by warning as before); while a check is in
flight only "Remove agent…" is offered. Confirming the removal deletes the agent, drops its
cached check, toasts "Agent removed. Automations it wrote still execute on schedule.", and
returns to the Agents page; a failed delete toasts the error and stays on the form. Default
status is indicated by the absent "Make default" menu row — no chip anywhere.
Fields, top to bottom in rendered
order: name (required, placeholder "Name this agent"), optional description ("What this
agent is for. The authoring agent reads this when picking an agent for non-trivial tasks"), pick harness
(Claude Code / Gemini CLI / Codex / OpenCode — all four selectable, §4.7; each harness card
carries a one-line blurb: Claude Code "Uses your Claude account or a local model managed by Ollama.", Gemini CLI
"Uses your Google account.", Codex "Uses your ChatGPT account or a local model managed by Ollama.", OpenCode
"Open-source. Works with any provider you’ve already set up, or a local model."), then the MODEL
section — the mode rows live inside it (option labels "Default model" / "A specific model"
(note "Type the model this harness should use") / "A local model" — the specific-model
option renders for every harness; the local-model option renders enabled when the harness is
Claude Code, Codex, or OpenCode (§4.7) and carries the note "Pick a model served on this Mac
through Ollama. Best for simple steps"; when the harness is Gemini CLI the local-model row
renders disabled with the note "Gemini CLI can't drive local models." — a disabled row is
never selectable, and switching to Gemini CLI while the local-model mode is picked moves the
selection back to "Default model") — with the model input below (required for specific-model and
local-model modes — the specific-model mode shows a mono free-text input with a per-harness
placeholder: Claude Code "e.g. claude-opus-4-8", Gemini CLI "e.g. gemini-2.5-pro", Codex
"e.g. gpt-5-codex", OpenCode "e.g. anthropic/claude-opus-4-8"; OpenCode expects the
provider/model form).
**Install gating:** the form loads real install state on mount (§19 `GET /agents/detect`; a
failed detect gates nothing). An uninstalled harness's card carries an amber NOT INSTALLED
`MiniBadge` and stays selectable, but while the picked harness is uninstalled the MODEL section
is hidden, saving is gated (submitting toasts "Download and set up `<Harness>` first."), and an
amber notice "`<Harness>` isn't installed on this Mac yet. Autowright can download and set it
up for you." offers **Download & set up** — the real §19 `POST /agents/install`, rendered like
the Ollama install card ("Installing `<Harness>`…", determinate bar only when the
`harness.install` stream carries a percent; failure "Install failed: `<first error line>`" +
Try again; a form that finds the install already running for the picked harness reattaches via
§19 `GET /agents/install/{id}`; the stream's current step line renders under the bar). After a
finished install the form asks §19
`GET /agents/signin/{id}`; when signed out it starts the §19 sign-in help
(`POST /agents/login`) and shows "Finish signing in. Autowright opened Terminal / your
browser. Waiting for the sign-in…" with a **Reopen** button, polling `GET /agents/signin/{id}`
every 2 s. Setup finishes (install done, plus sign-in when it was needed) with a re-detect and
the toast "`<Harness>` is set up. Ready to save.", which ungates the form. An
already-installed but signed-out harness never gates — saving such an agent surfaces through
the Needs setup badge and the reconnect banner, as before. The
submit button renders disabled-styled until valid but stays clickable: submitting with a missing
name shows an inline red error "A name is required. Give this agent a name before saving." (red
input border, clears on typing) and smooth-scrolls the Name field to the center of the view,
focusing the input — the submit button sits at the bottom of a long page, so the error must be
brought on-screen; submitting a name that collides with another agent's effective §4.7 grant
name (case-insensitive, excluding the agent being edited) shows the same inline treatment
with "An agent named `<name>` already exists. Pick a different name.", and a backend 422
from the same rule surfaces identically; an uninstalled picked harness toasts "Download and set up
`<Harness>` first."; missing Ollama toasts "Install Ollama first."; otherwise "Pick
a harness and a model first." Success toasts: "`<name>` added. Ready to write automations." /
"Changes saved. `<name>` is ready." When editing a signed-out agent, the form shows a reconnect
banner: "This agent is signed out. Reconnect it to create or edit automations." + Reconnect
button. The local-model mode is gated on Ollama being
installed and ready: while missing, the notice "Local models need Ollama, which isn't
installed on this Mac yet."; once ready, a green check "Ollama is installed and active."
Inline install flow: button "Install Ollama" starts a real §19
`POST /agents/install` for Ollama; the label "Installing Ollama…" renders a determinate bar
when the `harness.install` stream carries a percent (indeterminate otherwise) with the
stream's current step line under it, and failure
shows "Install failed: `<first error line>`" with the button returning to "Install Ollama".
**LOCAL MODEL** picker: radio list of installed Ollama models with
size metadata, empty state "No local models installed yet. Download one below and it will show
up here." **DOWNLOAD A MODEL**: one flex row of a `.ad-input.row.mono` field (placeholder
"e.g. qwen3-coder:30b", Enter submits) and a primary **Download** button, the two at the
button's height (§14 `.row`). Model pulls: one at a time — the backend streams `ollama.pull` WS events and the UI
renders the event's `percent` field (the §19 single overall pull percent — one continuous bar,
never the raw per-layer numbers, which reset 0–100 per layer; the UI never parses percents out
of `line`). Right column shows "N%"; determinate bar once a percent has arrived, indeterminate
before; the current output line renders under the bar so the post-download steps ("verifying
sha256 digest") are explained instead of looking stuck at 100%. A pull completes when the model
shows up in `GET /ollama/status` (polled every 2 s while a pull runs), matched by the §19 rule
that a bare name without a tag counts its `:latest` variant — Ollama stores `qwen3` as
`qwen3:latest`, and a literal match would leave the download card spinning forever on a
finished pull. The already-installed guard on starting a pull uses the same rule, and the
resolved installed name (e.g. `qwen3:latest`) is what gets selected in the picker and named
in the success toast; suggested-model
chips fill the pull input (placeholder "e.g. qwen3-coder:30b"; they don't start the pull); suggested models qwen3-coder:30b (19 GB,
"Best local coding model"), gemma4:e4b (9.6 GB, "Good local default"), deepseek-coder:6.7b
(3.8 GB, "Light and quick"). A suggestion chip is hidden once that model is installed, and the whole
SUGGESTED section is hidden while any model download is in progress (only one pull runs at a
time and the pull input the chips fill is replaced by the download card, so the chips would be
inert); when no chips remain, the section is hidden too. Below the
pull input: a "Browse more models on Ollama" button-styled link (`.ad-btn-soft`, the same
outbound-link treatment as the §9.4 About page: label followed by the Font Awesome
`fa-arrow-up-right-from-square` icon, 10 px, never the ↗ text glyph; not a suggestion chip, so
it cannot be mistaken for a model) opening https://ollama.com/library in the browser.

**Secrets.** List with add/edit modal, masked values, delete confirm (§4.8 — the confirm
modal is titled "Delete this secret?" with the danger action "Delete secret"). The list's NAME
cell shows the secret's `description` as a muted sub-line when present, and an amber **NOT SET** tag
(the same `MiniBadge` as §9.2's NOT SET param badge) when the secret is a §4.8 placeholder — the tag
clears once a value is saved, and the placeholder's VALUE cell shows a faint "—" instead of
the mask. The name field is a
single-line input (Enter saves, Escape closes); its placeholder is a hint, not a literal example
value: "A short name, like MAIL_PASSWORD or CRM_API_KEY". In edit mode the name is not an
input at all — it renders as a read-only chip (§4.8: names are immutable; only description
and value are editable). Add mode refuses a name that already exists (client-side guard —
"already exists. Edit it from the list instead." — friendlier than surfacing the §19 POST's
422 for the same rule). Add saves via §19 `POST /secrets`, edit via `PUT /secrets/{id}`.
Below the name sits an optional
single-line DESCRIPTION input (placeholder "Where this secret is used, so the authoring agent
knows when to use it"), pre-filled when editing. The value field is a 3-row vertically
resizable textarea (multi-line values allowed, §4.8) masked with `-webkit-text-security` unless
Show is toggled; Enter inserts a newline, Cmd/Ctrl+Enter saves, Escape closes. **Editing a
secret that has a value (`set: true`) never opens on an empty value field**: the API never
returns the stored value (§4.8), and an empty masked textarea with a Show/Hide toggle reads as
"your secret is empty". Instead the VALUE section renders a read-only *kept* row (same chip
style as the read-only name: a mono mask `••••••••••••`, the faint note "Current value is kept secret",
and a text button "Replace value" at the right); no textarea and no Show/Hide exist in this
state, and Save changes with the row untouched is a description-only update (§4.8 blank value
keeps the stored one). Pressing Replace value swaps the row for the textarea (focused,
placeholder "Paste the new value, or leave blank to keep the current one", Show/Hide as
usual) with a "Keep current value" text button under it that returns to the kept row and
discards anything typed. The intro copy in this state: "The stored value stays as it is
unless you replace it. A new value is used from the next execution onward." Editing a §4.8
**placeholder** (`set: false`) shows the textarea directly, because there an empty field is
the truth: the VALUE eyebrow carries the amber NOT SET `MiniBadge`, the placeholder reads "Paste the
password or API key", and the intro copy reads "This secret has no value yet. Automations
that need it fail until you add one." (a blank save still just updates the description).
A new secret saved with a
blank value becomes a §4.8 placeholder (the add modal's value placeholder reads "Paste the
password or API key, or leave blank to add the value later"; the success toast is then
"Saved. Add the value before an automation needs it."). The edit modal is titled
"Edit secret" with submit "Save changes"; add is "New secret" / "Save to Keychain". The
add/edit modal is a shared component (`SecretModal.tsx`) — the §9.2 Discord trigger editor
opens it in add mode from its New secret button and receives the saved secret (the §19 POST
response entity — id included) via an
`onSaved` callback. Toasts:
"Saved to your Keychain." / "Secret updated." / "Removed from your Keychain." When no secrets exist, the table is replaced by an
empty state (dashed card, same pattern as the Automations list): "No secrets yet. Add a password
or API key once, and your automations use it wherever they need it — the value never appears in a script or a
log." with an accent CTA "Add your first secret" that opens the add modal (all three empty-state
CTAs — automations, agents, secrets — are accent-primary; the page-header Add buttons on Agents
and Secrets are accent-primary too — each page's single main create action, §9 — no icons).

## 13. Menu-bar surface

Tray icon (the §14 app mark, inverse: a solid rounded square with the AW ligature knocked out —
monochrome, so the normal state still works as a macOS template image) with red alert dot when
any automation failed **or carries the §4.1 `overdue` problem** (deliberately those two only —
the other `problems` kinds are config nits the in-app amber chip covers; the dot is reserved
for "something isn't running that should be") —
implemented as a second, non-template icon variant (`trayAlert.png`, mid-gray glyph + red dot,
generated by `scripts/gen_tray_icon.py`); the normal state uses the black template image. The dot has two
feeders: the renderer updates it live over IPC whenever it refreshes state, and the main
process itself polls `GET /automations` every 60 s — the app can sit tray-only with zero
renderers alive (window closed, panel never opened), and a scheduled failure — or an
automation quietly going overdue — in that state
must still light the dot (and a later success must clear it). Panel: 334 px translucent
(blur), height grows with content up to the 640 px window cap — past that the automation
rows list scrolls (native overlay scrollbar, per the §14 no-custom-scrollbar rule) while the
header and footer stay pinned. The window tracks the panel's full rendered
(border-box) height, rounded up, via a `ResizeObserver` — a content-only measure
(`scrollHeight`) excludes the 1 px border, and a single measure at first render runs
before fonts finish loading; either way the footer's bottom edge gets clipped. Header row with "AUTOWRIGHT" eyebrow left and aggregate status right (mono 11.5 px; "All good
· N automation(s)" — pluralized by count — or "N need(s) attention" in red; the count is
automations failed or §4.1 overdue, matching the dot), one row per automation (7 px status dot —
pulsing while executing, name, mono sub-line colored by state: cyan "Executing now…" / red when failed
/ accent for a result chip / faint otherwise, relative time right-aligned in a 56 px column, then
the §9.1 square inline execute button (`.ad-btn-exec.small` — the §14 24 px/radius-6 variant that owns the size: solid accent with a play glyph →
spinner + disabled while executing, tooltip explains) at the row's right edge — the same run
button as the Automations list). Row click opens the app on that automation; execute
button triggers a "Menu bar" execution. Footer: accent "Open Autowright" link + version. Click-outside
closes. The panel renders its own `Toast` (the §9 toast, bottom-center of the panel): an
execute that fails (e.g. the §7 409 no-free-slot) toasts the error message — a tray
execute press is never a silent no-op. The panel window is not closable, minimizable, or fullscreenable — the default
application menu stays active, so Cmd+W/Cmd+M must be no-ops for it (a destroyed or
minimized panel would otherwise strand the tray toggle on a dead reference). Belt and
braces: a `closed` handler clears the reference anyway. The panel is visible on all
Spaces including over fullscreen apps (`setVisibleOnAllWorkspaces` with
`visibleOnFullScreen`) — opening it never switches the user out of a fullscreen Space.
Panel placement is per-OS (§2 `panelPosition`): macOS anchors under the menu-bar icon;
Windows anchors the panel's bottom edge just above the taskbar's work area and re-anchors
on **every** `resize-panel` (the §13 height growth), so the panel hugs the taskbar at its
real height instead of assuming the 640 px cap — a taskbar docked to another edge still
gets a fully on-screen panel. The Windows tray icon uses real
colored assets
(`trayWin.png`/`@2x` and the alert variant — light glyph legible on the dark taskbar,
rendered by `scripts/gen_tray_icon.py` beside the mac template PNGs), never the mac
black template images, which disappear on a dark taskbar. **Linux ships no tray surface
at all** (decided 2026-09-01): Electron's tray rides libappindicator/StatusNotifier,
where stock GNOME needs a user extension to render the icon, hosts conventionally
activate through a context menu the app doesn't have, and `click` isn't a reliable
activation event — a tray that may be invisible or unopenable is worse than none.
`capabilities.trayPanel` is false on Linux, so the shell never creates the tray or the
§13 panel and the §4.9 `menuBarIcon` row hides (§4.9); the window plus the §3 systemd
backend service are the whole Linux surface, and closing the last window quits the UI
(§9 close rule). The checked-in `trayLinux.png` pair stays in the repo (rendered by
`scripts/gen_tray_icon.py` with the others) against a future revisit but nothing
consults it.

**Deep-link mechanism:** a row click sends the target `'/app?automation=<id>'` to the main process.
With no main window, the window is created loading that hash and the renderer's boot reads
`automation=<id>` to land on the automation's detail page. With an existing window, main pushes the
target over IPC (`open-target`) and the renderer navigates in place — never a page reload,
which would drop the WebSocket and all renderer state. The footer link sends plain `'/app'`
(focus only). Deep links are ignored while onboarding hasn't completed.

