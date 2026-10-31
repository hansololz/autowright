# Autowright UI — build conventions

Dark theme ONLY. There is no light mode and no theme provider — components read CSS custom
properties from `styles.css`. No wrapper component is required, but every screen must sit on the
app background: give your root `background: 'var(--bg-window)'` (page content areas use
`var(--bg-content)`, cards `var(--bg-card)`) or components will render light text on white.

## Styling idiom

Inline `style={{…}}` objects with token vars — this DS ships almost no utility classes. Compose
layout with flex; radii 8-12px; body text is 13px. Fonts: `var(--sans)` (IBM Plex Sans, default)
and `var(--mono)` (IBM Plex Mono) — mono is used for timestamps, chips, eyebrows, counts, and
metadata. Icons are Font Awesome classes, already loaded: `<i className="fa-solid fa-play" />`.

Key tokens (all defined in `styles.css` → `_ds_bundle.css`):
- Surfaces: `--bg-window`, `--bg-content`, `--bg-sidebar`, `--bg-card`, `--bg-card-sel`,
  `--bg-inset`, `--bg-menu`, `--bg-toast`, `--bg-code`
- Text: `--text`, `--text-2`, `--text-2em`, `--text-muted`, `--text-faint`, `--text-faintest`
- Borders: `--hairline`, `--border-card`, `--border-input`, `--border-btn`, `--border-hover`
- Color: `--accent` (orange, the brand color), `--accent-bg`, `--accent-hover`, `--on-accent`,
  `--green`/`--green-bg`, `--red`/`--red-bg`, `--amber`, `--cyan`, `--orange`, `--magenta`,
  `--gray` (each with a matching `*-bg` tint for chips/badges)

The class vocabulary that DOES exist (buttons/rows own their hover states — use these instead of
restyling): `ad-btn-primary`, `ad-btn-ghost` (+ `danger`), `ad-btn-soft`, `ad-btn-text`
(+ `dim`, `danger`), `ad-btn-link`, `ad-btn-pill`, `ad-btn-dashed`, `ad-btn-accent-ghost`,
`ad-btn-danger-ghost`, `ad-btn-exec`, `ad-input` (border + focus ring; size it inline),
`ad-menu-row` (+ `active`, `danger`), `ad-card-click`, `ad-hover-row`, `ad-md` (markdown body),
`ad-copy` (opts text into selection). Entry animations: `animation: 'adFadeUp .4s ease'` on
pages; also `adFadeIn`, `adSpin`, `adPulse`.

## Components

Import from `window.AutowrightUI.*`: `Badge` (status chips — pass `status` of queued / executing /
succeeded / failed / cancelled / skipped / interrupted / none), `MiniBadge`, `Chip`, `Logo`,
`BtnPrimary`, `BtnGhost`, `Toggle`, `RadioRing`, `Spinner`, `ProgressBar`, `GreenCheck`,
`Eyebrow`, `CountPill`, `PageTitle`, `MenuRow` (+ `menuStyle` object for the popover container),
`Modal` (children is a render function `(close) => …`), `ConfirmModal`, `Toast`, `FailureNotice`,
`Markdown`, `SpecMarkdown`. Each component's `.d.ts` is the exact prop contract and its
`.prompt.md` shows a verified composition — read them before use. Status colors and labels only
ever come from `Badge`; never hand-roll a status chip.

## Example

```jsx
const { PageTitle, BtnPrimary, Badge, Chip } = window.AutowrightUI;

<div style={{ background: 'var(--bg-window)', minHeight: '100vh', padding: '26px 30px', fontFamily: 'var(--sans)', color: 'var(--text)', fontSize: 13 }}>
  <PageTitle right={<BtnPrimary>New automation</BtnPrimary>}>Automations</PageTitle>
  <div style={{ background: 'var(--bg-card)', border: '1px solid var(--border-card)', borderRadius: 12, padding: '14px 18px', display: 'flex', alignItems: 'center', gap: 12 }}>
    <div style={{ fontWeight: 600, flex: 1 }}>Weekly competitor price check</div>
    <Chip>Mon 09:00</Chip>
    <Badge status="succeeded" />
  </div>
</div>
```
