# Marketplace

## 22. Marketplace (decided, preview behind Developer mode)

A marketplace is a browsable set of shareable automations, described by one YAML file (the
**marketplace catalog**, canonically named `marketplace-catalog.yaml`) listing `.autowright` archives with a title, a description, and a preview image.
Anyone can write one by hand and host it anywhere (a GitHub repository, a web server, a
folder on disk). The user adds a catalog to the app by link or by file; the app saves the
catalog and where it came from, so **Refresh** re-reads the origin and shows new entries.
Installing an entry is the §5.1/§5.2 import, unchanged: the archive is fetched at install
time, previewed, and confirmed through the same two-phase flow, so every §5.1 guarantee
holds (triggers land off, no records are ever created, only matched records are granted).

**Visibility (preview gate).** The Marketplace page and its nav row render only while the
§4.9 `developerMode` setting is on. That is the only thing the setting gates here: the §19
routes, the §5 store, and the §20 CLI group are always live, in every mode - the §2 rule
that developer and production mode run the same code, with no dev-only paths. Turning
Developer mode off while the page is open navigates to Automations (§22.3). The gate is
lifted by removing the condition, nothing else; the feature is built as a normal surface
that happens to be hidden.

### 22.1 Catalog format

One YAML document. `format_version` is the only hard gate, exactly like the §5.1 archive:
a catalog whose `format_version` is not `1` is rejected ("this marketplace catalog is format
`<n>`; this version of Autowright reads format 1"). Unknown keys at any level are ignored
so the format can grow inside one version (unlike archives, a catalog carries no code and
nothing that could land silently wrong).

```yaml
format_version: 1
name: "Community automations"        # optional (max 80 chars): the source's title
description: "Automations I use."    # optional (max 500 chars)
entries:                             # required list, may be empty, max 200 entries
  - title: "Manga chapter watcher"   # required, non-empty, max 120 chars
    description: "Checks the series you follow every morning at 8."  # optional, max 1000
    path: automations/manga.autowright   # required: https URL or a relative reference
    image: images/manga.png              # optional: https URL or a relative reference
```

- `name` defaults, when absent or blank, to the catalog file's stem (`shelf` for
  `shelf.yaml`; for a link, the last path segment's stem) - except for the canonical
  `marketplace-catalog.yaml`, whose stem would name every unnamed catalog alike: then a
  file source takes its folder's name and a link source its host name. Strings are stripped; over-long
  strings are rejected, not truncated.
- `entries` keep catalog order and are addressed by **index** (0-based position) on every
  served surface. Entries carry no id: a catalog is a plain hand-written list, and the
  page re-reads it whole after every refresh, so positions are always current.
- `path` must end in `.autowright` (after any query string is dropped). `image` must end in
  `.png`, `.jpg`, `.jpeg`, `.webp`, or `.gif` (case-insensitive). Any other form is a
  validation error naming the entry index.
- **Reference resolution** depends on where the catalog came from (§22.2 `kind`):
  - A link source resolves a relative reference with RFC 3986 `urljoin` against the
    catalog's URL; the result must be `https://` (an absolute `http://` reference, or a
    catalog served from a URL whose join lands off https, is rejected).
  - A file source resolves a relative reference against the catalog file's directory. The
    resolved path (symlinks followed) must stay **inside** that directory - `../` escapes
    and absolute filesystem paths are rejected naming the entry, so a catalog can never
    point the app at an arbitrary file on the machine. An absolute `https://` reference is
    fine in a file source (mixed catalogs are legal); a reference with any other scheme
    (`http://`, `file://`, …) is rejected naming the entry - treating it as a relative
    path would resolve to nonsense silently.
- Validation covers the shape and the *form* of every reference at add and refresh time.
  Whether an archive actually exists is checked when the user installs it (§22.4 preview:
  a missing or invalid archive answers the ordinary §5.2 422); whether an image exists is
  checked at refresh, and a missing or oversized image simply leaves that entry without a
  preview (logged, never a refresh failure).
- Caps (untrusted input): a catalog is at most 1 MB; an image at most 5 MB; the archive
  itself is capped by §5.1 (64 MB) at install time. Link fetches use the §5.2 headers
  (`User-Agent: autowright/<version>`), a 30-second per-read timeout, and a 60-second
  whole-download deadline for catalogs and images (they are small; the §5.2 10-minute
  deadline is for archives), HTTPS only with redirects re-checked to stay on https.

### 22.2 Sources store

A **source** is one catalog the user added. Sources live under the §5 data root:

```
marketplace/
  sources.yaml                  # [{id, kind: url | file, origin, added_at, refreshed_at, error}]
  <source-id>/
    marketplace-catalog.yaml    # the last successfully fetched catalog, byte for byte
    images/<index>.<ext>        # one cached preview image per entry that has one
```

- `id` is a uuid (§4 id rule). `kind` is `url` (origin is the https link exactly as
  pasted, stripped) or `file` (origin is the catalog's absolute path). `added_at` and
  `refreshed_at` are §5 UTC timestamps; `refreshed_at` is the last *successful* fetch
  (null until one succeeds - impossible after add, which fetches first). `error` is the
  last refresh failure's message, null after every success.
- The source's `name`, `description`, and `entries` are **derived from the cached
  `marketplace-catalog.yaml` at load** - never duplicated into `sources.yaml`, so there is one truth.
  A source whose cache is missing or fails §22.1 validation still lists (zero entries, the
  error "the saved copy couldn't be read - refresh to fetch it again") rather than
  vanishing; §5 lenient load applies to `sources.yaml` itself (an entry missing `id`,
  `kind`, or `origin`, or with an unknown `kind`, skips with a warning).
- **Add** fetches and validates first; any failure answers 422 and stores nothing. The same
  origin (exact string equality after stripping; a file origin compares by resolved path)
  cannot be added twice - 409 "that marketplace is already added". Add is a refresh that
  creates the record.
- **Refresh** (one source, or all in listing order) re-reads the origin: a link source
  downloads it, a file source reads the file. On success the catalog is written to a temp
  file and renamed into place, the images directory is rebuilt (every image the new
  catalog lists is fetched fresh into a temp directory that then replaces `images/`),
  `refreshed_at` is stamped and `error` cleared. On failure (unreachable, over the cap,
  invalid) nothing in the cache changes, `refreshed_at` keeps its old value, and `error`
  records the message - the page keeps showing the last good copy with the error beside
  it. A single-source refresh through the API answers 200 with the source either way
  (`error` says what happened) so a refresh-all never stops at the first bad source. One
  refresh runs at a time per backend (a store-wide lock); a refresh runs on the threadpool.
- **Remove** deletes the record and its directory. Automations installed from it are
  ordinary automations and are untouched.
- The store is loaded once at backend startup into memory and rewritten whole on every
  change, like agents and secrets. Nothing here is ever executed: a catalog is data, an
  archive lands only through import.
- Compatibility: a new additive store (§21.4 entry, 2026-09-11). `format_version: 1` on
  the catalog is the hard gate for the file people share; `sources.yaml` follows the §5
  lenient-load rule with no version marker.

### 22.3 Marketplace page

**Nav.** A "Marketplace" row (icon `fa-store`) sits between Secrets and Settings in the §9
rail, rendered only while `settings.developerMode` is true; it carries no count pill. The
`Page` union gains `marketplace`. When the setting turns off while `page` is `marketplace`
(the Settings toggle, or a §20 `settings set` seen through the store refresh), an effect
in the app shell calls `go('automations')` - the same shape as the §9.3 overlay closing
itself when the setting drops.

**Page.** Title "Marketplace" with header actions: a ghost **Refresh all** (rendered only
when at least one source exists; the §9 busy spinner while running) and the accent **Add
marketplace…** button. The page fetches §19 `GET /marketplace` on mount and after each of
its own actions, and refetches when the §19 `marketplace.changed` WebSocket event arrives
(a §20 CLI change shows without a reload); it shows the §14 `PageLoading` line until the
first answer.

**Empty state** (no sources): the §14 `EmptyState` with a bold first line "No marketplaces yet" over the body
"Add a marketplace catalog someone shared - from a link, or a file on this Mac - to browse
the automations it lists." (the machine noun through the §9 per-OS copy rule) and an "Add
marketplace…" button. Below it, one **MAKE YOUR OWN** section: the eyebrow, one muted line
"A marketplace catalog is a YAML file that lists .autowright archives. Save it as
marketplace-catalog.yaml next to the archives it points to, then add it here.", and the
§22.1 example catalog in a mono code box
(`.ad-card` padding 14, `pre` at 12 px `--mono`, selectable, horizontal scroll inside the
box). The section renders only in the empty state - once a source exists the user has seen
the shape.

**Per source** - one section each, in store order:
- Header row: the source name (600, 15 px), a §14 `MetaChip` naming the origin (link icon +
  hostname for a link source, file icon + file name for a file source; the full origin in
  the `title` attribute), and a muted line "Refreshed <date label>" (the §4.1 shared
  date-label scheme, e.g. "Refreshed Today, 8:00 AM"; omitted while `refreshedAt` is null) or, while `error` is set, an amber `Notice` beneath the header: "Couldn't
  refresh: <error>. Showing the last copy." (for a source with no readable cache - `cached` false: "Couldn't
  load: <error>."). Two quiet square icon buttons on the right (`.ad-btn-ghost.icon`; Remove adds
  `.danger` so it reads red - never the accent-filled §12 execute shape, two orange
  squares beside a title read as a call to action):
  **Refresh** (`fa-rotate`, spinner while running, `aria-label` "Refresh") and **Remove**
  (`fa-trash`, `aria-label` "Remove") - Remove opens a danger `ConfirmModal`, title "Remove
  "<name>"?", body "Automations you already installed from it stay. You can add the
  marketplace again later.", confirm label "Remove". Removing refetches the list; no toast.
  The Refresh all, Add, and Install buttons read "Refreshing…" / "Adding…" /
  "Installing…" beside the §9 spinner while busy.
- The description, muted, when present.
- The **entry grid**: `grid-template-columns: repeat(auto-fill, minmax(220px, 1fr))`, gap
  14. Each entry is an `.ad-card` with zero padding and `overflow: hidden`: a 16:9 image
  area at the top (`object-fit: cover`; when the entry has no cached image, a
  `--bg-inset` placeholder with a faint centered `fa-bolt`), then a 14 px padded body
  holding the title (600, 13.5 px), the description (muted, 12.5 px, clamped to three
  lines with an ellipsis), and a footer row with the accent **Install** button. A source
  whose catalog lists no entries shows the §14 `EmptyLine` "This marketplace lists no
  automations yet."
- Images load through the authenticated §19 image route (the renderer talks to the backend
  with a bearer header, so a plain `<img src>` can't carry it): the page fetches the bytes
  and shows a blob URL, cached per (source id, index, `refreshedAt`) for the session and
  revoked when the page unmounts. A failed image fetch shows the placeholder.

**Install.** Clicking Install POSTs §19 `…/entries/{index}/preview`; while in flight the
button shows the busy spinner. A 422 (unreachable archive, invalid archive) toasts the
reason. Success opens the §9.1 **import modal directly on its preview step** - the modal
takes an optional initial preview, skipping the input step - with the source row reading
"<marketplace name> · <entry title>" behind a store icon. Its Back button closes the modal
(there is no input step to return to). Import confirms the token and hands off to the §9.1
summary modal exactly as a URL import does.

**Add marketplace modal.** The §9.1 import modal's input-step shape, retitled: title "Add
marketplace", intro "Browse automations someone published - from a link, or a marketplace
catalog on this Mac.", FROM A LINK field (mono, placeholder `https://… link to a
marketplace-catalog.yaml file`, caption "An https link to a marketplace-catalog.yaml
file."), the OR divider, and a dashed "Choose a marketplace catalog on this Mac…" button
(native open dialog through the §3 main-process `open-catalog` IPC, filtered to `yaml`/`yml`; the IPC answers `{ path }` or
null on cancel - the backend reads the file itself, so only the path travels). Footer:
quiet Cancel / accent **Add** (disabled while the field is empty; Enter submits). Add
POSTs §19 `POST /marketplace/sources`; a 422 or 409 shows inline in red under the field or
the button that produced it. Success closes the modal, refetches, and toasts "Added
<name>."

### 22.4 Backend API (§19 addendum)

All routes authenticated like the rest of §19. `Source` is `{ id, kind, origin, name,
description, addedAt, refreshedAt, error, cached, entries: [{ index, title, description,
archive, image }] }` - `archive` the resolved archive reference (the https URL, or the
absolute path for a file-relative reference), `image` a boolean saying whether a cached
preview exists, `cached` whether a readable cached catalog exists (false only for the
§22.2 unreadable-cache case; an empty catalog is still cached).

- `GET /marketplace` → `{ sources: [Source] }` in store order.
- `POST /marketplace/sources` `{ url }` or `{ path }` (exactly one, non-empty) → `Source`.
  Fetches and validates first (§22.2 add): any failure answers 422 with the reason and
  stores nothing; an origin already added answers 409. A `path` must be an absolute path
  to an existing readable file.
- `POST /marketplace/sources/{id}/refresh` → `Source` (200 even when the refresh failed;
  `error` carries the reason and the cache is unchanged). Unknown id → 404.
- `POST /marketplace/refresh` → `{ sources }` after refreshing every source in order.
- `DELETE /marketplace/sources/{id}` → `{ ok: true }`; unknown id → 404.
- `GET /marketplace/sources/{id}/entries/{index}/image` → the cached image bytes with the
  content type matching its extension; 404 when the source, entry, or image doesn't exist.
- `POST /marketplace/sources/{id}/entries/{index}/preview` → `{ token, preview }` exactly
  as `POST /automations/import/url` (§19): the archive is fetched (`transfer.fetch_archive`
  for an https reference, a plain read for a file reference - which must still pass the
  §22.1 inside-the-directory check against the catalog's directory at fetch time), fully
  validated, and parked under a §5.2 token; `preview.sourceUrl` and `preview.resolvedUrl`
  both carry the resolved archive reference. Any failure answers 422; the confirm is the
  ordinary `POST /automations/import/confirm`.
- WebSocket event `marketplace.changed` (no payload) after every add, refresh, and remove.

Request models (§19 `models.py`): `MarketplaceAdd { url?: str, path?: str }` with the
exactly-one rule validated in the route (422 "give a link or a file path, not both" / "give
a link or a file path").

### 22.5 CLI (§20 addendum)

```
autowright marketplace list                    every source with its entries
autowright marketplace add <url-or-path>       add a marketplace by https link or file path
autowright marketplace refresh [<source>]      refresh one source, or all
autowright marketplace remove <source>         remove a source (installed automations stay)
autowright marketplace install <source> <n>    install entry n (1-based, as `list` prints it)
```

`<source>` resolves like every other §20 reference: an id, an unambiguous id prefix, or an
exact name (case-insensitive); ambiguity and no-match are the standard §20 errors. A file
path given to `add` is made absolute against the current directory before it travels.
`list` prints one block per source - `<name> [<id8>]  <origin>` then `  refreshed <when>`
(or `  couldn't refresh: <error>`) then each entry as `  <n>. <title> - <description>`
(1-based; the description omitted when empty; "  no automations listed" for an empty
catalog). `add` prints `added <name> [<id8>] - <count> automation(s)`; `refresh` prints one
`refreshed <name> - <count> automation(s)` or `couldn't refresh <name>: <error>` line per
source (exit 1 when any failed); `remove` prints `removed <name>`. `install` checks `<n>` against the
source's entry count first (`entry numbers start at 1 - see \`autowright marketplace list\`` /
`'<name>' lists <count> automation(s) - there is no entry <n>`, exit 1), then previews the
entry, confirms immediately (the typed command is the user's go-ahead, §20 import rule),
and prints exactly the §20 `automation import` summary lines (the two commands share one
printer), including the foreground package ensure.

### 22.6 Tests

- Backend (`tests/test_marketplace.py`): §22.1 validation (format gate, caps, extension
  rules, index-naming errors), URL and file reference resolution including the
  inside-the-directory rejection and the https-only join, refresh keeping the cache on
  failure, images rebuilt on success and skipped on failure, lenient `sources.yaml` load,
  and every §22.4 route with the network monkeypatched (add 422/409, refresh 200-with-error,
  image 404, entry preview producing a confirmable token).
- Renderer (`app/tests/marketplace-page.render.test.tsx`, `settings-gating` style): the nav
  row hidden while `developerMode` is false and shown when true, the redirect to Automations
  when the setting drops mid-page, the empty state with the example catalog, a source with
  entries rendering its grid, Install opening the import modal on the preview step, and the
  add modal's inline 422.
- e2e: one drive with a file-based source under the test data root (a catalog plus one
  archive exported in the same test), asserting the page lists it and Install lands the
  automation with its triggers off.
