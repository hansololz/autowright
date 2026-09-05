# Autowright SPEC — Design tokens

Part of the Autowright spec. Index and § map: [SPEC.md](../SPEC.md). § numbers are global across spec files.

## 14. Design tokens (authoritative — `app/src/tokens.css` implements them)

- Dark theme only. Fonts: IBM Plex Sans 400/500/600 (all UI text; `--sans`; no 700 anywhere), IBM Plex Mono
  400/500/600 (timestamps, version labels, chips, eyebrows, counts, technical metadata;
  `--mono`), bundled via `@fontsource`. `-webkit-font-smoothing: antialiased`.
- Type scale — one size per role, no in-between values (base UI 13 px):
  - page title 20 px/600 letter-spacing `-.01em` (`.ad-h1`; 26 px/`-.02em` in onboarding);
    page subtitle 13 px/1.6 `--text-muted`; modal title 15 px/600; modal subtitle
    12.5 px/1.6 `--text-muted`; confirm body 13 px/1.6 `--text-muted`.
  - card title 15 px/600; card description 12.5 px/1.55 `--text-muted`; body prose
    13 px/1.6 `--text-2`.
  - settings-row label 13.5 px/600 + 12 px/1.55 `--text-muted` description; list-row title
    13 px/600 (a technical label — secret name, model id — is 12.5 px/500 mono) + 11.5 px/1.45
    `--text-muted` sub-line.
  - in-card secondary copy (notes, placeholders, status lines, checklist rows) 11.5 px;
    empty lines 12.5 px `--text-muted`. There is no 11 px sans and no 12 px sans in cards —
    a line is 11.5 or 12.5.
  - mono metadata: row-level (durations, timestamps, versions, chips) 11.5 px; identifiers,
    counts and line numbers 11 px; nothing below 10 px except the §14 eyebrow.
  - eyebrows: 10 px/600 mono uppercase, letter-spacing `.09em`, `--text-faint` — the
    `Eyebrow` primitive, one size everywhere (card headers, section labels, table headers,
    form labels, modal toolbars; never a hand-typed 9.5/10.5 or `.08em` variant).
  Headings tighten letter-spacing (`-.01em` to `-.02em`).
- Neutral ramp: all background and text neutrals derive from one oklch formula — hue 262,
  chroma 0.016 (backgrounds) / 0.006–0.02 (text) — so lightness steps are perceptually even.
  Tokens are declared as `oklch()` in `tokens.css`; the hexes below are the sRGB equivalents
  for mirrors that need literals (landing page, `result.tsx`, Electron `backgroundColor`).
- Backgrounds (oklch L at chroma 0.016 hue 262): window 0.16 `#090d14` (`--bg-window`),
  content 0.175 `#0d1118`, sidebar 0.155 `#090c13`, title bar 0.21 `#141820`
  (`--bg-titlebar` — the §9 Windows-only full-width top bar; also the Windows
  `titleBarOverlay` `color`, so the OS button cluster blends into the bar),
  cards 0.23 `#191d25` (`--bg-card`;
  selectable/hovered cards 0.243 `#1c2028` `--bg-card-sel`), inset/result wells 0.17
  `#0c0f16` (`--bg-inset`), popover menus 0.26 `#20242c` (`--bg-menu`), toast 0.285
  `#262a32` (`--bg-toast`), code wells 0.14 `#060910` (`--bg-code`). Card-vs-content
  separation is deliberate: ΔL 0.055 (1.12:1) so cards read without leaning on the border.
  Menu-bar panel: `rgba(32,36,44,.94)` (the menu tone), 334 px wide, radius 12,
  border `rgba(255,255,255,.1)`.
- Text — five levels, no more (oklch hue 262): primary 0.94 `#e9ebef` (`--text`), secondary
  0.845 `#c8ccd4` (`--text-2` — body/notice copy, emphasized values), muted 0.715 `#9da3af`
  (`--text-muted` — descriptions, placeholders, secondary labels), faint 0.625 `#828893`
  (`--text-faint` — eyebrows, table headers, metadata; ≥4.5:1 on every surface), decorative
  0.51 `#606672` (`--text-deco` — non-informational glyphs only: list bullets, resting ✕,
  carets; never running text).
- Borders (white at alpha): hairlines `.06` (`--hairline`), cards `.07`, inputs `.10`,
  buttons `.11`, hover `.25` (`--border-hover` — buttons and neutral controls), dashed
  placeholders/add-rows `.12` (`--border-dashed` — empty states, draft banner,
  `.ad-btn-dashed`). Clickable cards hover to an accent-tinted border instead:
  `oklch(0.74 0.155 52 / .3)` (`--border-card-hover` — `.ad-card-click:hover`).
  Selected/active rows and tabs share one background wash: `rgba(255,255,255,.07)`
  (`--bg-active` — devlog rows/tabs, executions filter, selected step row). The sidebar
  nav's active row uses the accent wash instead (`--accent-hint-bg`).
- Accent (brand orange): `oklch(0.74 0.155 52)`; hover `oklch(0.79 0.155 52)`; tint backgrounds
  `--accent-bg` `/ .15`, `--accent-chip-bg` `/ .13`, `--accent-hint-bg` `/ .08`; text on accent
  `#16100a` (`--on-accent`); link hover `oklch(0.82 0.14 60)`; `::selection` accent `/ .35`;
  keyboard focus ring `oklch(0.74 0.155 52 / .55)` (`--focus-ring`) — a global
  `:focus-visible` rule draws a 2 px outline (offset 2) on every focusable element except
  `.ad-input` fields, which keep their border ring.
- Status colors (oklch; tint backgrounds at the alpha shown): green `oklch(0.76 0.15 150)`
  `/ .13`, cyan `oklch(0.78 0.12 210)` `/ .13`, red `oklch(0.7 0.19 25)` `/ .13`, amber
  `oklch(0.8 0.13 85)` `/ .14`, magenta `oklch(0.72 0.16 340)` `/ .13`, gray `oklch(0.72 0.015 262)` `/ .13`. One extra chip color: orange `oklch(0.72 0.15 60)` `/ .13` for
  attention-flavored result chips (e.g. "5 of 6 checked").
- Syntax palette (script/log wells; oklch, same family as the status set): keyword
  `oklch(0.72 0.13 310)` (`--syn-keyword`), string `oklch(0.86 0.11 135)` (`--syn-string`),
  const/number `oklch(0.74 0.14 45)` (`--syn-const`), def/decorator `oklch(0.85 0.11 85)`
  (`--syn-def`), builtin/call `oklch(0.73 0.11 265)` (`--syn-builtin`), comment
  `oklch(0.54 0.02 262)` (`--syn-comment`). `PY_COLOR` in `ui.tsx` references these tokens —
  no literal syntax colors at call sites.
- Status/error text: static error and validation copy always uses `--red-text` (never `--red`
  or `--red-hover`, which stay for icons/dots and hover states). Invalid text fields always use
  the `.ad-input.invalid` class — no inline red borders or glows.
- Tinted notice banners (red/amber/accent/cyan) share one slim geometry: radius 10, padding
  `11px 14px`, tint background at `/ .07`, border at `/ .3`, 7 px leading dot. The tints are
  tokens — `--notice-red-bg`/`--notice-red-border` and likewise `-amber-`, `-accent-`,
  `-cyan-` — never hand-written oklch at call sites. The `Notice` primitive (`ui.tsx`;
  `tone` red/amber/accent/cyan, optional `dashed` border) is the one renderer of the slim
  banner — the §9.2 concurrency note, the §11 gating and warning banners, the create-flow
  version notices. Card-sized notices (`Notice size="card"` — `FailureNotice` composes it;
  the §9.2 needs-fixing and draft banners) are radius 12, padding `14px 18px`, no leading
  dot, a 13 px/600 title in the tone's text color. Notice body text is `--text-2`.
- Radii: buttons/inputs 8 px, chips 6–7 px, cards 12 px (`.ad-card` and `.ad-card-click`
  both own the radius — call sites never re-declare it, and a plain card is never radius
  10), inset wells 8 px, pills 16–20 px, popover menus and tooltips 10 px, chat bubbles
  10 px, toast 9 px. Cards stay in-plane (no drop shadow) but carry a 1 px
  inset top highlight — `inset 0 1px 0 rgba(255,255,255,.035)` on `.ad-card`/`.ad-card-click`
  — as a tactile edge cue. Floating surfaces are the only drop-shadow tier —
  popovers `0 18px 44px rgba(0,0,0,.5)`, toast `0 10px 30px rgba(0,0,0,.4)`,
  modal `0 24px 60px rgba(0,0,0,.5)`, menu-bar panel `0 18px 50px rgba(0,0,0,.55)`,
  hovered nav rail `10px 0 36px rgba(0,0,0,.38)` (§9).
- Selection: all text is selectable by default — every piece of information on screen can be
  highlighted and copied. Only buttons and the drag region are `user-select: none`.
  `.ad-drag` marks the hidden-title-bar drag region
  (interactive children opt out with `no-drag`).
- All hover and focus states are CSS classes in `tokens.css` — never JS mouse-state (a JS hover
  flag sticks when a re-render or layout shift moves the node under the cursor). Buttons:
  `.ad-btn-primary`, `.ad-btn-ghost`, `.ad-btn-soft`, `.ad-btn-text`[`.dim`], `.ad-btn-pill`,
  `.ad-btn-dashed`, `.ad-btn-x`, `.ad-btn-accent-ghost` (accent-tinted ghost: Add trigger,
  agent chips), `.ad-btn-danger-ghost` (red-tinted confirm), `.ad-btn-text.danger` (red text
  button), `.ad-btn-link` (accent link-styled button), `.ad-btn-tab`, `.ad-attempt-pill`,
  `.ad-chip-btn`, `.ad-menu-row`.
  Surfaces: `.ad-hover-row` (clickable list/table rows), `.ad-card-click` (clickable cards),
  `.ad-link-title` (clickable titles), `.ad-title-rename` (click-to-edit automation title/description
  on the §11 Review page — the pencil is the only click target (the text itself is inert);
  the `.always` modifier keeps the pencil visible), `.ad-nav-row` (sidebar nav). Text fields use `.ad-input`
  (border + accent focus ring, also on `:focus-within` so grouped multi-field inputs like the
  §9.2 segmented time entry ring as one control; `.amber` variant on amber notice cards;
  `.invalid` variant — a red border that holds through focus — for live-invalid values, e.g.
  the §9.2 trigger editor inputs; `.oneline-ph` variant — the placeholder truncates with an
  ellipsis instead of wrapping (the `text-overflow` sits on the field itself: Blink drops it
  from `::placeholder`), for ask-box textareas that size to typed content only — the §11 chat
  composer — and single-line inputs whose placeholder outruns the field, e.g. the §12 secret
  description). Classes own colors,
  interaction, **and size** — call sites never override button padding/font-size/radius inline
  (layout-only styles such as `flex`, `whiteSpace`, margins are fine). Button classes render
  identically on `<a>` link-as-buttons: `a.ad-btn-ghost` and `a.ad-btn-primary` rules suppress
  the global link hover (color + underline) so an anchor styled as a button keeps the class's
  own text color in every state (§7 Open in Discord, §9.5 Open GitHub issue) — call sites never
  patch this inline. All action buttons share
  one size: 13 px font, radius 8 px, padding 8 px 15 px on bordered buttons (`.ad-btn-ghost`,
  `.ad-btn-soft`, `.ad-btn-dashed`, `.ad-btn-accent-ghost`, `.ad-btn-danger-ghost`) and
  9 px 16 px on the borderless filled `.ad-btn-primary` — same rendered box. Borderless text
  buttons (`.ad-btn-text`, `.ad-btn-link`) are 500 13 px with 6 px 4 px padding. Dense in-card
  editors get sanctioned compact sizes, still class-owned: `.ad-btn-primary.small` (6 px
  12 px, radius 7 — the row-height primary that sits level with `.ad-btn-text` in a one-line
  card row: the §11 BUILD card's Sync now), `.ad-btn-accent-ghost.small`
  (500 11.5 px mono, 5 px 11 px, radius 7 — the §9.2 trigger editor's Add/Save, New secret, and
  permission-checklist buttons), `.ad-btn-text.small` (11.5 px, no padding — the §9.2 setup
  guide disclosure toggles and §11 card-header actions), and `.ad-btn-link.small` (11.5 px,
  no padding — the §11 card-header Save); no other ad-hoc button sizes. Button labels
  never wrap (`white-space: nowrap` on all action/text button classes) — a tight flex row must
  yield elsewhere, never by squeezing a button onto two lines. Non-action
  controls keep their own scale: `.ad-btn-pill` (mono metadata pill — pickers and version
  chips whose label is technical metadata: `name · model`, timezone, secret name, `vN`; its
  one variant `.ad-btn-pill.action` swaps the type to 500 11 px sans for pill-height
  buttons whose label is a plain-language action — the §11 chat composer's Send/Cancel and
  the thread's "Undo this change" tag — action words never render in the mono metadata
  face), `.ad-chip-btn`
  (example-prompt chip), `.ad-btn-x` (row-remove ✕), `.ad-btn-exec` (square icon button),
  `.ad-btn-icon` (the 26×26 borderless icon-only row action — secrets edit/delete, trigger
  edit — `.ad-btn-text` colors in a fixed square box; call sites never rebuild it from
  `.ad-btn-text` with inline size overrides), `.ad-btn-tab` (mono tab chip — 500 12 px mono,
  3 px 9 px, radius 6, white-wash background; `aria-pressed` swaps the active tab to accent
  text on `--accent-chip-bg` — the §11 trigger-mock picker tabs; never rebuilt from
  `.ad-btn-text` with inline size overrides), and `.ad-attempt-pill` (the §7 execution-page
  attempt switcher — 600 10 px mono, 2 px 8 px, radius 6; the class owns the resting/hover
  neutrals, and the active pill pins its status badge color + tint background inline, which
  takes precedence over the hover state). `.ad-btn-bare` resets button chrome (no
  background/border/padding, inherited font/color/text-align, full-width) so clickable
  cards and rows can be real `<button>`s (§9 keyboard convention) while their surface class
  (`.ad-card-click`, `.ad-hover-row`) keeps owning the visuals. `.ad-seg` is the segmented
  filter group (hairline border, radius 8, overflow hidden) of `.ad-seg-btn` segments
  (13 px, 7 px 14 px, hairline left divider between segments, `--bg-active` wash +
  `--text` on the active segment via `aria-pressed`, `--t-hover` transition) — the
  Executions All / Succeeded / Failed control; never hand-rolled from `.ad-btn-text`.
  **Checkbox** — one look everywhere, owned by a single `tokens.css` rule; there is no
  other checkbox rendering in the app (never the browser's native box, never a per-page
  glyph). Geometry: 15×15 px, radius 4, `1px solid var(--border-hover)` on a transparent
  ground; hover (unchecked only) lifts the border to `--text-muted`; checked fills
  `--accent` with an `--accent` border and a 9 px `--on-accent` tick (a CSS-mask check
  glyph, the same shape in every instance); background and border-color transition at
  `--t-hover` `--ease-enter`. The rule targets two forms that render pixel-identically:
  (1) every native `input[type='checkbox']`, restyled through `appearance: none` so it stays
  a real input — labels, `checked`, keyboard, and the global focus ring all work natively —
  used wherever the checkbox is its own click target, always wrapped in a `<label>` (the
  §9.2 trigger editor's mention and "Catch up if missed" boxes; the read-only `.ad-md` task-list
  boxes, which add `pointer-events: none` and a 6 px right margin and sit inline at
  `vertical-align: -3px`); and (2) the `.ad-check` span, the shared `CheckBox` glyph in
  `ui.tsx`, for rows where the whole row is the button (the §11 agent-enablement and
  secret-allowance rows): `data-on` marks the checked state, the hosting row carries
  `role="checkbox"` + `aria-checked` (§9), and the row's `.ad-hover-row` hover drives the
  glyph's hover border. Native checkboxes get no per-site class or inline sizing — the
  global rule is the styling. `Toggle` is a different control (a switch for settings-style
  on/off rows, §9) and never stands in for a checkbox.
  `.ad-focus-inset` flips the global focus ring inside the element (outline-offset −2 px)
  for controls clipped by an `overflow: hidden` card (§9 focus convention). Every pickable
  control (harness cards, model/radio rows, kind chips, attempt pills, tabs) carries a
  hover treatment and transitions its state swap at `--t-hover` — no inert pickers, no
  instant background jumps.
  Sanctioned variants for the odd shapes — never inline overrides: `.ad-btn-ghost.icon`
  (the bordered icon-only overflow ellipsis, `8px 11px`), `.ad-btn-soft.armed` (accent
  border + `--text`, the §9.4 update-ready button), `.ad-btn-exec.small` (24 px, radius 6,
  the §13 panel rows), `.ad-btn-tab` for every pickable chip (trigger kinds, dev-log
  tabs), `.ad-btn-icon` for every icon-only row action (dev-log close, chat clear).
  `.ad-btn-pill` and `.ad-chip-btn` never wrap; `a.ad-chip-btn` suppresses the link
  underline like the other link-as-button rules.
  `.ad-btn-amber` is the amber filled action button (Ollama sign-in/install CTAs): primary
  geometry (600 13 px, 9 px 16 px, radius 8), `--amber` fill, `--on-accent` text,
  hover `oklch(0.84 0.13 85)`. `.ad-btn-primary.looks-disabled` renders the disabled
  primary treatment while staying clickable (validation-on-click forms). Static cards use
  `.ad-card` (`--bg-card`, `--border-card`, radius 12) — the non-clickable sibling of
  `.ad-card-click`.
- Derived tokens beyond the base palette: `--red-hover`, `--red-text`, `--accent-sel`
  (selected-card border), `--hairline-dim` (in-card row dividers; `--hairline` stays for card
  borders/headers), `--bg-code` + `--code-text` (script/log wells), `--wash-hover`
  (`rgba(255,255,255,.03)` — hover rows and the version-menu header wash), `--wash-track`
  (`rgba(255,255,255,.08)` — progress-bar track, disabled primary), `--spinner-track`
  (`rgba(255,255,255,.15)`), `--backdrop` (`rgba(5,7,10,.6)` — every modal/overlay
  scrim), the floating-surface shadows `--shadow-menu` / `--shadow-toast` /
  `--shadow-modal` / `--shadow-panel` / `--shadow-rail` (values above), the §13 panel
  ground `--bg-panel` + `--border-panel`, and the find-in-script highlights `--find-bg`
  (`--accent` / .22) + `--find-active-bg` (/ .5). No `rgba`/`oklch` literal survives at a
  call site; `result.tsx`'s iframe base sheet is the one sanctioned hex mirror. Recurring fragments are
  `ui.tsx` primitives: `MiniBadge` (uppercase mono chip; status `Badge` maps onto it),
  `ProgressBar` (`percent: number | null` — null renders the indeterminate `adBarSlide` bar; the
  only progress bar, never hand-rolled; a percent label beside it renders only when `percent`
  is a number — an indeterminate bar never shows "0%"),
  `CommandBlock` (`command: string` — the copyable shell-command row: mono inset box holding
  the command text plus a right-side `ad-btn-soft` "Copy" button that writes the exact command
  to the clipboard and shows the "Copied to clipboard." toast; clipboard failures are silent.
  The one primitive for every copy-a-command surface: the §4.9 CLI PATH row and the §9.4
  Homebrew update notice),
  `Tag` (the small mono info tag on step rows — radius 6, padding 2 px 8 px, 10 px mono
  500, `--bg-inset` background, hairline border; optional leading icon; one primitive for
  the create-flow review, detail-page STEPS, and import-preview step tags alike. Its
  `title` prop renders the **Tag tooltip** — a custom hover bubble, never the native
  `title` attribute (Chromium's native tooltip needs a ~1 s stationary hover and is
  suppressed after clicks, after scrolls, and while the window is unfocused — it read as
  broken on the step rows): shown after a 200 ms hover delay, hidden on mouse-leave,
  mousedown, or any scroll; portal-rendered to `document.body` (`role="tooltip"`,
  pointer-events none, z-index 120) fixed 7 px above the tag's horizontal center, flipped
  to 7 px below when fewer than 46 px of viewport remain above the tag, and clamped so it
  never comes within 8 px of the window's left/right edges; bubble: `--bg-menu`
  background, `--border-input` border, radius 8, the menu shadow, padding 6 px 10 px,
  400 11.5 px/1.5 sans `--text-2`, max-width 320 px, `adFadeUp` entrance
  (`--t-enter`/`--ease-enter`). The tag span also carries the tooltip text as its
  `aria-label`), `GreenCheck`, `Spinner` (optional `color`; inline
  next-to-label size is 13), `PageTitle`, `Eyebrow`, `EmptyState` (dashed-card empty state
  with CTA — automations/agents/secrets lists), `EmptyNotice` (dashed card, title 13.5/500 +
  12.5 muted body — executions list, execution page, detail page), `LoadingRow` (Spinner 13 +
  500 12.5 `--text-muted` label, gap 9), `BackLink` (`.ad-btn-text` + 10 px chevron-left),
  `Caret` (default 10 px).
- Icons: Font Awesome 6.5.2. App mark: the AW monogram — rounded square filled with the app
  accent (`#f68b43`, the sRGB hex of `--accent` oklch(0.74 0.155 52)) carrying a continuous
  zigzag AW ligature (the A's right leg doubles as the W's first stroke) plus the A crossbar,
  stroked in `--on-accent` `#16100a` at width 70 of the 1024 canvas (round caps/joins).
  Source of truth is `app/electron/icon/icon.svg`; the `Logo`
  primitive (`ui.tsx`) renders it full-bleed (the SVG's transparent canvas margin — the mark
  spans 824 of the 1024 canvas — is cropped at display time; the asset is untouched) and is
  the mark's only in-app renderer: sidebar, loading screen, onboarding, and the §13 menu-bar
  panel rows' execute buttons (instead of a play glyph). Other checked-in assets in
  `app/electron/icon/`: `icon.png` (1024 px raster; dock icon set at startup via
  `app.dock.setIcon` in `app/electron/main.cjs` so dev sessions don't show the default
  Electron icon) and `icon.icns` (the bundle icon §18 prod.sh packages with). On Linux the
  SVG itself is also the launcher icon: the §3 desktop-integration reconcile copies it to
  `~/.local/share/icons/hicolor/scalable/apps/ai.autowright.app.svg`, so the mark must stay
  a complete, self-contained icon (plate included) rather than a bare glyph. Both are
  derived from `icon.svg` by `scripts/gen_icon.cjs` (Electron render → `icon.png`,
  then sips + iconutil → `icon.icns`; run from `app/` as
  `./node_modules/.bin/electron ../scripts/gen_icon.cjs`) — rerun it whenever the SVG
  changes so the three assets never drift.
  The dev app *name* (menu bar / dock / Cmd-Tab — macOS reads the running bundle's
  `CFBundleName`, which `app.setName` cannot override) is branded by `app/brand-electron.cjs`,
  an npm `postinstall` step: on macOS it sets `CFBundleName`/`CFBundleDisplayName` to
  "Autowright" in `node_modules/electron/dist/Electron.app/Contents/Info.plist` and ad-hoc
  re-signs the bundle (a modified plist would otherwise invalidate the signature and the kernel
  would kill the app). Idempotent; re-runs automatically when a new Electron version is
  installed. Release builds get the name from `@electron/packager` (§18 prod.sh), which
  excludes `brand-electron.cjs` from the bundle.
- Motion (keyframes in `tokens.css`: `adFadeUp`, `adFadeOutDown`, `adFadeIn`, `adFadeOut`,
  `adSpin`, `adPulse`, `adBlink`, `adBarSlide`). One timing system, tokenized in `tokens.css`:
  - **Duration tokens:** `--t-exit` 120 ms (all exits), `--t-hover` 150 ms (hover/control state
    changes: buttons, toggles, radios, carets, hover rows), `--t-enter` 200 ms (element
    entrances: menus, modals, toasts, in-page items, collapsibles, sidebar collapse,
    determinate progress-bar width), `--t-page` 360 ms (page-root entrances). Call sites never
    hand-write durations.
  - **Easing tokens:** `--ease-enter` `cubic-bezier(0.16, 1, 0.3, 1)` (decelerate — all
    entrances and state transitions), `--ease-exit` `cubic-bezier(0.4, 0, 1, 1)` (accelerate —
    all exits). Continuous loops keep their own curves: spinner `.85s linear`, pulse/bar-slide
    `ease-in-out`.
  - **Entrance utility classes** (the only way call sites apply entrances):
    `.ad-anim-page` (fade-up, `--t-page`) on every page root; `.ad-anim-item` (fade-up,
    `--t-enter`) on elements appearing inside a mounted page (notices — `FailureNotice`
    carries it itself — chat rows, checklist rows, footer buttons, inline editors like the
    §9.2 trigger editor); `.ad-anim-fade` (fade-in, `--t-enter`) for opacity-only entrances
    (boot splash, overlays, in-place control-cluster swaps like the §9.2 memory-card
    confirm/rename rows — keyed remounts, so the row never jumps).
  - **Two-way surfaces:** `Modal`, popover menus (`PopMenu` in `ui.tsx` — wraps `menuStyle`,
    used by every `usePopover` consumer) and `Toast` all enter at `--t-enter` and play a
    `--t-exit` fade-down before unmounting, on every dismissal path.
  - **Collapsibles:** the `Collapse` primitive in `ui.tsx` (`.ad-collapse` grid-rows
    `0fr`→`1fr`; content stays mounted) animates every expand/collapse section (the §11
    review cards — body and collapsed hint alike — §9.2 step rows and setup-guide
    disclosures, result view cards). Motion is asymmetric per the duration tokens — rows
    open at `--t-enter`/`--ease-enter` and close at `--t-exit`/`--ease-exit` — and the
    region's content crossfades with the resize: the inner wrapper sits at opacity 0 while
    closed (fading out at exit timing on close) and fades in on open at enter timing with a
    40 ms delay so the height change leads. Collapsing text fades instead of visibly
    clipping, and paired regions (a §11 card's collapsed hint vs its body) hand off as one
    crossfade rather than two competing height animations.
    Header carets are a single glyph rotated via `transform` transition (`--t-hover`) — never
    an icon-class swap.
  - **Executing pulse:** one value app-wide — `adPulse 1.4s ease-in-out infinite`, exported as
    the `PULSE` const in `ui.tsx`; all executing badges/dots use it. The log cursor blink is
    likewise the `BLINK` const (`adBlink 1s step-end infinite`) — never typed at a call site.
  - **Press feedback:** all bordered/filled action buttons get `:active` `translateY(1px)`
    (`.ad-btn-exec` keeps its `scale(.94)`; `.ad-chip-btn` its `translateY(1px)`).
  - **Reduced motion:** a global `@media (prefers-reduced-motion: reduce)` rule collapses all
    animation/transition durations to .01 ms (loops stop; exit-then-unmount flows still fire
    their `animationend`).
  - Modals (shared `Modal` shell in `ui.tsx`: backdrop + card, used by the secret add/edit
    modal and `ConfirmModal`) animate both ways, and every dismissal path (backdrop click,
    Escape, Cancel, save/confirm) plays the exit before unmount; confirm actions fire after
    the exit finishes. A modal may guard its escape paths (`guardClose`): Escape and a
    backdrop click first ask it whether the dismissal may proceed, so an editor holding
    unsaved text raises its discard confirm instead of closing — the confirm stacks above
    it and Escape closes only the top-most card. The one blocking exception is `BlockingOverlay` (the §4.9 reset
    progress overlay): the same backdrop + card and both-ways motion, but no user dismissal
    path at all — no Escape, no backdrop click, no buttons; it closes only programmatically
    (its `open` prop, which plays the exit before unmount).
- Layout: sidebar rail 58 px, expanding to 212 px on hover (§9). **Surface anatomy** —
  one frame, one rhythm, one card, one row family, one field family, app-wide:
  - **Page frame:** gutter `26px 30px 70px`; pages that open with a `BackLink` use top 20
    (the link supplies the rest). Max width 1200 px (Review page 1800 px, forms 720 px,
    Settings/About 640 px); every frame is horizontally centered in the pane (`margin: 0 auto`),
    so a narrow page reads as a centered column rather than hugging the sidebar rail. Onboarding
    is its own full-height frame (`30px 32px 60px` body on both steps).
  - **Page header:** the `PageTitle` primitive on every page — `.ad-h1` title, `right`
    slot holding a `HeaderActions` cluster (§9), optional `sub` subtitle (13 px/1.6
    `--text-muted`, 6 px under the title). 20 px below the header (title or subtitle,
    whichever is last); pages whose title row carries inline metadata (rename pencil,
    version pill — §9.2, §7, §11) pass that row as `children` and keep the same `.ad-h1`
    and margins. A lede/meta row under the header sits 20 px above the first section.
  - **Section rhythm:** eyebrow-labelled sections are 26 px apart; the section eyebrow sits
    10 px above its card; cards stacked inside one section (execution page, result views,
    review grid columns) are 14 px apart. Grid cards (Automations §9.1, Agents §12) keep
    their 14 px grid gap.
  - **Card:** `.ad-card`/`.ad-card-click` chrome only (never inline `--bg-card` +
    `--border-card`). One horizontal inset — **18 px** — for everything inside a card:
    headers, rows, bodies, markdown (the §4.5 renderer's full-bleed tables assume it).
    Grid cards pad `18px`; prose bodies `16px 18px`; markdown bodies `14px 18px 16px`.
    Two header idioms, each consistent: a **section label** (eyebrow *above* the card,
    10 px gap — Settings, About, the §9.2 detail sections) or a **card header** (eyebrow
    *inside* the card, `12px 18px`, `--hairline` bottom divider when a body follows; an
    optional right slot holds a mono count 500 10.5 px `--text-muted` or a
    `.ad-btn-text.small`/`.ad-btn-link.small` action; collapsible headers add the `Caret`
    and are `.ad-btn-bare .ad-hover-row .ad-focus-inset` — or the §9 `role="button"`
    carve-out when they nest actions — the §11 review cards, the §9.2 SPEC card, result
    view cards, the execution page's log header). A page never mixes the two idioms for
    sibling cards.
  - **Rows** (three tiers, nothing else): **settings row** `15px 18px`, 13.5/600 label +
    12/1.55 description (3 px under), control right, 20 px gap (Settings, About, the
    §9.2 parameter and concurrency rows); **list row** `12px 18px`, 13 px gap, 13/600 title
    (or 12.5/500 mono for technical labels) + 11.5/1.45 sub-line 1 px under, trailing
    actions as `.ad-btn-icon`/`.ad-btn-x` (secrets, steps, triggers, agents, params, memory
    snapshots, menu-bar automations); **table row** `9px 18px` mono/sans cells at the
    table scale (executions tables). In-card row dividers are `--hairline-dim` and are
    suppressed on the last row; card borders and card headers use `--hairline`. Every
    clickable row is `.ad-btn-bare .ad-hover-row` (+ `.ad-focus-inset` inside an
    `overflow: hidden` card); a selected row is the `--bg-active` wash plus an
    `inset 2px 0 0 var(--accent)` bar — never a border-left with asymmetric padding.
  - **Fields:** `.ad-input` owns geometry as well as chrome — default `11px 14px` padding,
    13 px sans, line-height 1.5 (forms, modals, the chat composer); `.compact` `7px 10px`
    12 px (in-card editors: trigger editor, parameter editors, memory rename, find bar);
    `.row` `7px 14px` with a 19 px line-height at the default type (35 px tall with its
    border, the standalone primary button's height), for a field that shares a flex row with
    a standard button (the §12 agent form's model-download field) so the pair sits at the
    button's own height instead of the button stretching to the taller default field;
    `.mono` swaps the family (secret values, cron expressions, model ids). Call sites set
    only width/height/rows/resize/text-align — never padding or font. The one form-label
    idiom is `Eyebrow` 8 px above the field; fields in a form are 16 px apart.
  - **Chips** (four primitives, four jobs): `MiniBadge` (uppercase status), `Tag` (step-row
    info tag with tooltip), `MetaChip` (lowercase mono metadata — 500 11 px mono, `3px 8px`,
    radius 6, `--hairline-dim` ground, `--text-muted`; optional `c`/`bg` for the tinted
    result chips "5 of 6 checked" — trigger chips on the list and detail lede, result
    chips on cards and in result views, attempt/outcome chips on the execution page),
    `CountPill` (nav counts). No hand-built chip spans; `.ad-chip-btn` is the example-prompt
    chip, with `.static` for the inert used-by chips on agent cards.
  - **Loading and empty:** `PageLoading` (centered Spinner 24 in an 80 px well) for a page
    or modal body still fetching; `LoadingRow` inside cards and rows; `EmptyState`
    (with CTA) and `EmptyNotice` for page-level and card-level empties; `EmptyLine`
    (12.5 px `--text-muted`, `14px 18px`) for an empty list *inside* a card that also has
    content around it (no triggers, no snapshots, no files, no matching steps). Bare
    `Spinner`s and ad-hoc "Loading…" text are not loading states.
  - **Modals:** the `Modal` card pads `22px 24px` by default (`cardStyle` zeroes it for the
    full-bleed §9.2/§11 viewer, editor and test-run modals); title 15/600, 6 px above the subtitle; footer
    buttons in a right-aligned flex row, gap 10, 18 px above. `DocModal` (`ui.tsx`) is the
    one document viewer (§9.4 About documents and the What's new modal).
  - **Icons:** trailing row chevrons 10 px, back-link chevron 10 px, status dots 7 px,
    icons inside text buttons 10–11 px, `.ad-btn-icon` glyphs 12 px (the class), `.ad-btn-x`
    glyphs 12 px (the class). Never re-sized inline on a class that owns its glyph size.
- Scroll chaining: inner scroll panels embedded in the page flow (spec viewer, execution log
  pane, etc.) chain to the page — reaching their bottom continues scrolling the page (browser
  default; no `overscroll-behavior: contain`). Only floating surfaces (popovers, dropdowns,
  modals) may contain overscroll.
- Scroll chrome: overlay scrollbars everywhere — they draw on top of the content and take
  zero layout space, so content never shifts when one appears. Electron main enables
  Chromium's `OverlayScrollbar` feature (`app.commandLine.appendSwitch('enable-features',
  'OverlayScrollbar')`) — required because macOS "Automatic"/"Always" system scrollbar
  settings otherwise force classic space-taking bars. No `::-webkit-scrollbar` styling
  anywhere (custom rules would force classic bars back). The root declares
  `color-scheme: dark` so the overlay thumb renders light on the dark background.
  Overlay scrollbar (app-wide — **every** vertical scroll pane, including the main
  content/page scroller: create/edit chat thread, spec view/editor and
  framework-instructions scrollers, execution log pane, onboarding body, About
  licenses list, menu-bar panel list, automation-page inner scrollers, dev-log
  overlay panes): the native bar is hidden (`scrollbar-width: none`) and the pane
  draws its own thin thumb absolutely **over the content** — no track background and
  zero layout space, so content never shifts when it appears. The thumb (5 px wide,
  white 25 %, min 24 px tall, right inset 3 px) is **always visible** while the pane
  has overflow — no hover/scroll fade — and is indicator-only — it is not draggable;
  scrolling stays wheel/trackpad/keyboard. Implemented once as the
  shared `ScrollArea` wrapper / `useOverlayThumb` hook (ui.tsx); textareas wire the
  hook directly since they can't host the thumb node. Horizontal scrollers (markdown
  code blocks, full-bleed tables) keep the native overlay bar — the primitive is
  vertical-only. `scrollbar-color`/`scrollbar-width: thin` styling is banned along
  with `::-webkit-scrollbar` — either one forces classic space-taking bars.
  Textarea resize grip (`::-webkit-resizer`) is an inline-SVG grip icon — two rounded
  diagonal strokes, white 28 % — so it stays crisp instead of WebKit's light default square.

