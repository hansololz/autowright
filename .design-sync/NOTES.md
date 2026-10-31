# design-sync notes — autowright

- App repo, not a DS package: bundle entry is `app/ds-entry.ts` (re-exports `src/ui.tsx` + pure
  `Markdown`/`SpecMarkdown` from `src/result.tsx`). Never synth-entry from all of `src/` —
  `main.tsx` mounts the whole app at import time.
- `cssEntry` is `app/.ds-css/tokens.css`, a generated esbuild flatten of `src/tokens.css`
  (inlines @fontsource IBM Plex + Font Awesome and copies font binaries). Regenerate with
  `cfg.buildCmd` before every converter run — it is gitignored.
- TypeScript in `.ds-sync` must stay on 5.x — TS 7 (native) breaks validate's `.d.ts` parse
  check silently ("typescript not in node_modules").
- Playwright chromium: cache had empty husk dirs; `npx playwright install chromium` from
  `.ds-sync` (playwright@1.61.1 to match the repo's playwright-core pin) fixed it.
- Preview authoring pattern: card cells have a white background; any component with light text
  or transparent background must be framed in a dark wrapper
  (`background: 'var(--bg-window)'`, padding, borderRadius). Fixed-position components
  (Modal, ConfirmModal, Toast) additionally need the wrapper sized explicitly with
  `transform: 'translateZ(0)'` so the fixed overlay is contained and centered in it.
- Import previews from `'autowright'`. Controlled components (Toggle, RadioRing) take static
  props (`onChange={() => {}}`) — no hooks in preview files.
- Preview sizing gotchas: ProgressBar needs an explicit-width inner wrapper (~320px) or a
  fit-content frame collapses it; PageTitle needs `width: 500` frame + `marginBottom: 0`;
  MenuRow compositions use `{...menuStyle, position: 'static'}` inside a ~280px-wide frame.
- `SpecBlock` type is not re-exported by `app/ds-entry.ts` — previews inline
  `type SpecBlock = { k: 'h1' | 'h2' | 'p' | 'li'; text: string }`.
- Toast/Modal/ConfirmModal have `cardMode: single` + viewport overrides in config; their
  previews use the sized `translateZ(0)` dark frame (Toast h≈160, Modal h≈360).

## Re-sync risks

- `app/.ds-css/tokens.css` (cssEntry) is generated and gitignored — run `cfg.buildCmd` before
  every converter run or the build fails/ships stale CSS. It silently goes stale when
  `src/tokens.css` or the @fontsource/fontawesome versions change.
- `app/ds-entry.ts` must be extended by hand when new primitives are added to `src/ui.tsx`
  (plus a `componentSrcMap` entry) — nothing detects a missing re-export.
- Conventions header (`.design-sync/conventions.md`) enumerates tokens/classes from
  `src/tokens.css` — re-validate names against the fresh `_ds_bundle.css` on every re-sync.
- Toolchain assumptions: node 26, npm ci in `app/`, esbuild+ts-morph+typescript@5.x+
  playwright@1.61.1 installed in `.ds-sync/` (gitignored — reinstall on fresh clone), chromium
  via `npx playwright install chromium`.
- Verification anchor lives in the uploaded `_ds_sync.json`; grades in gitignored
  `.design-sync/.cache/` — a fresh clone re-verifies only what the anchor can't vouch for.
