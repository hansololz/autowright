# Marketplace

## 22. Marketplace (decided, preview behind Developer mode)

A marketplace is a browsable set of shareable automations, described by one YAML file (the
**marketplace catalog**, canonically named `marketplace-catalog.yaml`) listing `.autowright` archives with a title, a description, and a preview image.
Anyone can write one by hand and host it anywhere (a GitHub repository, a web server, a
folder on disk). The user adds a catalog to the app by link or by file; the app saves a copy
under its own uuid. A catalog may declare its own published link in an optional `url` key;
when it does, **Refresh** downloads that link and shows new entries, and when it doesn't the
copy is a one-time download (remove and add again to pick up changes). The user can also
**author** a catalog in the app: create one in a folder on this machine, add automations
from this app or from `.autowright` files, and edit it later (§22.7).
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
url: https://example.com/shelf/marketplace-catalog.yaml  # optional: where this file is published, so Refresh can fetch it
entries:                             # required list, may be empty, max 200 entries
  - title: "Manga chapter watcher"   # required, non-empty, max 120 chars
    description: "Checks the series you follow every morning at 8."  # optional, max 1000
    path: /Users/you/Automations/manga.autowright   # required: https URL or absolute local path
    image: /Users/you/Automations/manga.png         # optional: https URL or absolute local path
```

- `name` defaults, when absent or blank, to the catalog file's stem (`shelf` for
  `shelf.yaml`; for a link, the last path segment's stem) - except for the canonical
  `marketplace-catalog.yaml`, whose stem would name every unnamed catalog alike: then a
  file source takes its folder's name and a link source its host name. Strings are stripped; over-long
  strings are rejected, not truncated.
- `url` is optional (max 2000 chars, stripped; blank is the same as absent) and must be an
  `https://` link, else the validation error "`url` must be an https link". It is the
  publisher's statement of where this catalog file lives. It plays no part in adding (add
  reads the link or file the user gave) and is never written by the app; it is what a
  §22.2 refresh downloads, and it makes the source refreshable at all. A catalog without
  `url` is a one-time download: the page offers no Refresh for it.
- `entries` keep catalog order and are addressed by **index** (0-based position) on every
  served surface. Entries carry no id: a catalog is a plain hand-written list, and the
  page re-reads it whole after every refresh, so positions are always current.
- `path` must end in `.autowright` (after any query string is dropped). `image` must end in
  `.png`, `.jpg`, `.jpeg`, `.webp`, or `.gif` (case-insensitive). Any other form is a
  validation error naming the entry index.
- **References.** A `path` or `image` is exactly one of two forms, taken as it is, and a
  catalog may mix them; nothing is ever resolved against where the catalog came from:
  - An `https://` URL.
  - An **absolute local path** on this machine (`/Users/…`, `C:\…`). There is no symlink
    or containment rule: the user chose to add this catalog, and the archive it names
    still has to pass the §5.1 validation at install, so it can only ever land a real
    archive. This is how a catalog references automations kept anywhere on the machine
    or on a shared volume.
  - Anything else - a relative reference, `http://`, `file://`, `~/…` - is the validation
    error "entry <i>: `path` must be an https link or an absolute path" (or `image`).
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
marketplaces/
  sources.yaml                  # [{id, kind: url | file, origin, added_at, refreshed_at, error}]
  <source-id>/
    marketplace-catalog.yaml    # the last successfully fetched catalog, byte for byte
    images/<index>.<ext>        # one cached preview image per entry that has one
```

- `id` is a uuid (§4 id rule) and names the source's directory. `kind` and `origin` say
  where the cached copy came from: `url` (origin is an https link) or `file` (origin is
  the catalog's absolute path). Add sets them to the link exactly as pasted (stripped) or
  the file's resolved path; the first successful refresh moves them to the catalog's
  §22.1 `url` (kind `url`), because from then on the cached copy is the published one.
  `added_at` and `refreshed_at` are §5 UTC timestamps; `refreshed_at` is the last
  *successful* fetch (null until one succeeds - impossible after add, which fetches
  first). `error` is the last refresh failure's message, null after every success.
- The source's `name`, `description`, `url`, and `entries` are **derived from the cached
  `marketplace-catalog.yaml` at load** - never duplicated into `sources.yaml`, so there is one truth.
  A source whose cache is missing or fails §22.1 validation still lists (zero entries, no
  `url`, the error "the saved copy couldn't be read - remove this marketplace and add it
  again") rather than vanishing; §5 lenient load applies to `sources.yaml` itself (an
  entry missing `id`, `kind`, or `origin`, or with an unknown `kind`, skips with a
  warning).
- **Add** reads the link or the file the user gave, validates, and caches; any failure
  answers 422 and stores nothing. The same origin (exact string equality after stripping;
  a file origin compares by resolved path) cannot be added twice - 409 "that marketplace
  is already added". Add is the only time the pasted link or the file is read.
- **Refresh** (one source, or all in listing order) downloads the cached catalog's §22.1
  `url` - never the link or file the source was added from. A source whose catalog declares
  no `url` is **not refreshable**: a single-source refresh answers 409 "this marketplace
  declares no url to refresh from" and touches nothing; a refresh-all skips it. On success
  the catalog is written to a temp file and renamed into place, the images directory is
  rebuilt (every image the new catalog lists is fetched fresh into a temp directory that
  then replaces `images/`), `kind`/`origin` become `url`/that link, `refreshed_at` is
  stamped and `error` cleared. On failure (unreachable, over the cap, invalid, or the
  `url` is already another source's origin - "that link is already added as "<name>"")
  nothing in the cache or the record changes but `error`, which records the message - the
  page keeps showing the last good copy with the error beside it. A refresh takes the
  downloaded catalog as it is: if it declares a different `url`, the next refresh follows
  that one; if it declares none, the source stops being refreshable. A single-source
  refresh of a refreshable source answers 200 with the source either way (`error` says
  what happened) so a refresh-all never stops at the first bad source. One refresh runs at
  a time per backend (a store-wide lock); a refresh runs on the threadpool.
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

**Page.** Title "Marketplace" with header actions: a ghost **Create catalog…** (§22.7; always
rendered), a ghost **Refresh all** (rendered only when at least one source is refreshable,
i.e. carries a `url`; the §9 busy spinner while running) and the accent **Add
marketplace…** button. The page fetches §19 `GET /marketplace` on mount and after each of
its own actions, and refetches when the §19 `marketplace.changed` WebSocket event arrives
(a §20 CLI change shows without a reload); it shows the §14 `PageLoading` line until the
first answer.

**Empty state** (no sources): the §14 `EmptyState` with a bold first line "No marketplaces yet" over the body
"Add a marketplace catalog someone shared - from a link, or a file on this Mac - to browse
the automations it lists." (the machine noun through the §9 per-OS copy rule) and an "Add
marketplace…" button. Below it, one **MAKE YOUR OWN** section: the eyebrow, one muted line
"Create a catalog here: pick a folder, add automations, and share the folder or publish
it at a link. Or write the YAML by hand - list each archive by its full path on this Mac
or an https link, save it as marketplace-catalog.yaml, then add it here. Put the link you
publish it at in `url` so Refresh can fetch what you add later.", a ghost **Create catalog…**
button (the §22.7 create
flow), and the §22.1 example catalog in a mono code box
(`.ad-card` padding 14, `pre` at 12 px `--mono`, selectable, horizontal scroll inside the
box). The section renders only in the empty state - once a source exists the user has seen
the shape.

**Per source** - one section each, in store order:
- Header row: the source name (600, 15 px), a §14 `MetaChip` naming the origin (link icon +
  hostname for a link source, file icon + file name for a file source; the full origin in
  the `title` attribute), and a muted line "Refreshed <date label>" (the §4.1 shared
  date-label scheme, e.g. "Refreshed Today, 8:00 AM"; omitted while `refreshedAt` is
  null) for a refreshable source, or "Added <date label>" (from `addedAt`) for one whose
  catalog declares no `url`. While `error` is set, an amber `Notice` beneath the header:
  "Couldn't refresh: <error>. Showing the last copy." (for a source with no readable
  cache - `cached` false: "Couldn't load: <error>."). Quiet square icon buttons on the
  right (`.ad-btn-ghost.icon`; Remove adds `.danger` so it reads red - never the
  accent-filled §12 execute shape, two orange squares beside a title read as a call to
  action): **Edit** (`fa-pen`, `aria-label` "Edit catalog"; rendered only for a `file`
  source - the catalog is on this machine - and opening the §22.7 editor), **Refresh**
  (`fa-rotate`, spinner while running, `aria-label` "Refresh"; rendered only when the
  source carries a `url`) and **Remove**
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
description, url, addedAt, refreshedAt, error, cached, entries: [{ index, title,
description, archive, image }] }` - `url` the catalog's declared §22.1 link or null (null
means not refreshable), `archive` the entry's `path` as written (an https URL or an
absolute local path), `image` a boolean saying whether a cached
preview exists, `cached` whether a readable cached catalog exists (false only for the
§22.2 unreadable-cache case; an empty catalog is still cached).

- `GET /marketplace` → `{ sources: [Source] }` in store order.
- `POST /marketplace/sources` `{ url }` or `{ path }` (exactly one, non-empty) → `Source`.
  Fetches and validates first (§22.2 add): any failure answers 422 with the reason and
  stores nothing; an origin already added answers 409. A `path` must be an absolute path
  to an existing readable file.
- `POST /marketplace/sources/{id}/refresh` → `Source` (200 even when the refresh failed;
  `error` carries the reason and the cache is unchanged). Unknown id → 404; a source whose
  catalog declares no `url` → 409 "this marketplace declares no url to refresh from", the
  record untouched.
- `POST /marketplace/refresh` → `{ sources }` after refreshing every refreshable source in
  order (the others are listed as they were).
- `DELETE /marketplace/sources/{id}` → `{ ok: true }`; unknown id → 404.
- `GET /marketplace/sources/{id}/entries/{index}/image` → the cached image bytes with the
  content type matching its extension; 404 when the source, entry, or image doesn't exist.
- `POST /marketplace/sources/{id}/entries/{index}/preview` → `{ token, preview }` exactly
  as `POST /automations/import/url` (§19): the archive is fetched
  (`transfer.fetch_archive` for an https reference, a plain read capped at the §5.1 64 MB
  for a local path), fully validated, and parked under a §5.2 token; `preview.sourceUrl` and `preview.resolvedUrl`
  both carry the resolved archive reference. Any failure answers 422; the confirm is the
  ordinary `POST /automations/import/confirm`.
- `POST /marketplace/catalogs` `{ folder, name, description, url, entries }` → `Source`
  (§22.7 create): `folder` must be an absolute path to an existing directory (422
  otherwise); a folder already holding `marketplace-catalog.yaml` → 409 "that folder
  already holds a marketplace catalog - add it instead"; a folder whose catalog path is
  already a source → 409 "that marketplace is already added". The content fields are the
  `PUT …/catalog` body and go through the same §22.7 save steps (422 with the reason and
  nothing written on any failure); then the file is added as a source. Content may be
  omitted (the §22.5 CLI `create` sends none): an empty catalog named after the folder.
- `GET /marketplace/sources/{id}/catalog` → `{ name, description, url, entries: [{ index,
  title, description, path, image }] }` (§22.7): the catalog **file on disk**, references
  as written. 404 unknown id; 409 "only a catalog on this machine can be edited" for a
  `url` source; 422 with the §22.1 message when the file can't be read or parsed.
- `PUT /marketplace/sources/{id}/catalog` `{ name, description, url, entries: [{ title,
  description, path?, image?, automationId?, archiveFile? }] }` → `Source` (§22.7 save).
  404 / 409 as the GET; 422 with the reason and nothing written on any failure.
- WebSocket event `marketplace.changed` (no payload) after every add, refresh, remove,
  create, and save.

Request models (§19 `models.py`): `MarketplaceAdd { url?: str, path?: str }` with the
exactly-one rule validated in the route (422 "give a link or a file path, not both" / "give
a link or a file path"); `MarketplaceCatalogCreate { folder: str } + the save fields`;
`MarketplaceCatalogSave { name: str, description: str, url: str, entries:
[MarketplaceCatalogEntry { title, description, path?, image?, automationId?, archiveFile? }] }`
with the §22.7 exactly-one rule per entry validated in the store.

### 22.5 CLI (§20 addendum)

```
autowright marketplace list                    every source with its entries
autowright marketplace add <url-or-path>       add a marketplace by https link or file path
autowright marketplace refresh [<source>]      refresh one source, or every refreshable one
autowright marketplace remove <source>         remove a source (installed automations stay)
autowright marketplace install <source> <n>    install entry n (1-based, as `list` prints it)
autowright marketplace create <folder>         create a catalog in a folder and add it (§22.7)
autowright marketplace catalog set <source> KEY=VALUE…        name= description= url= (§22.7)
autowright marketplace catalog add <source> <automation-or-file> [--title T] [--description D]
autowright marketplace catalog remove <source> <n>            drop entry n; its archive file stays
```

`<source>` resolves like every other §20 reference: an id, an unambiguous id prefix, or an
exact name (case-insensitive); ambiguity and no-match are the standard §20 errors. A file
path given to `add` is made absolute against the current directory before it travels.
`list` prints one block per source - `<name> [<id8>]  <origin>` then `  refreshed <when>`
for a refreshable source, `  added <when> (no url to refresh from)` for one without a
`url`, or `  couldn't refresh: <error>` when the last refresh failed; then each entry as
`  <n>. <title> - <description>` (1-based; the description omitted when empty; "  no
automations listed" for an empty catalog). `add` prints `added <name> [<id8>] - <count>
automation(s)`; `refresh <source>` on a source without a `url` exits 1 with `'<name>'
declares no url to refresh from`; otherwise `refresh` prints one `refreshed <name> -
<count> automation(s)`, `couldn't refresh <name>: <error>`, or (refresh-all only)
`skipped <name> - no url to refresh from` line per source (exit 1 when any failed; a skip
is not a failure); `remove` prints `removed <name>`. `install` checks `<n>` against the
source's entry count first (`entry numbers start at 1 - see \`autowright marketplace list\`` /
`'<name>' lists <count> automation(s) - there is no entry <n>`, exit 1), then previews the
entry, confirms immediately (the typed command is the user's go-ahead, §20 import rule),
and prints exactly the §20 `automation import` summary lines (the two commands share one
printer), including the foreground package ensure.

The §22.7 authoring verbs go through `GET`/`PUT …/catalog`, so they take only a `file`
source (a `url` source exits 1 with the 409 detail). `create <folder>` makes the path
absolute against the current directory and prints `created <name> [<id8>] at <catalog
path>`. `catalog set` takes `name=`, `description=`, and `url=` (an empty value clears;
any other key exits 1 naming the three) and prints `saved <name>`. `catalog add` takes an
automation reference (resolved like `automation` verbs) or, when the argument ends in
`.autowright` and names an existing file, that archive (made absolute); `--title` and
`--description` override the defaults (the automation's name and description, or the
file's stem and nothing) and it prints `added <title> to <name> - entry <n>`. `catalog
remove <n>` (1-based) prints `removed entry <n> (<title>) from <name> - its archive file
stays`; a number out of range exits 1 like `install`.

### 22.6 Tests

- Backend (`tests/test_marketplace.py`): §22.1 validation (format gate, caps, extension
  rules, index-naming errors, the `url` https rule), the two reference forms (https URL,
  absolute local path) with relative references and other schemes rejected naming the
  entry, refresh
  downloading the catalog's `url` (never the add origin) and moving `kind`/`origin` to it,
  refresh keeping the cache on failure, a source without `url` refusing refresh (409) and
  being skipped by refresh-all, images rebuilt on success and skipped on failure, lenient
  `sources.yaml` load, and every §22.4 route with the network monkeypatched (add 422/409,
  refresh 200-with-error, image 404, entry preview producing a confirmable token).
- Backend authoring (§22.7, same file): create writes the empty catalog and adds the
  source, 409 on a folder that already holds one, 422 on a missing folder; GET answers the
  file as written and 409 for a `url` source; PUT exports an automation without parameter
  values under its safe name beside the catalog and lists it by absolute path (a taken
  name gets ` 2`), validates and references an archive file in place (a non-archive is a
  422 naming the entry; nothing is copied), keeps a `path` entry and its `image` as
  written, rewrites the file with the §22.1 keys only, rebuilds the cache, writes nothing
  on a 422, and leaves the archive file behind when its entry is removed; create takes
  the same content and writes it into a fresh folder, refusing a taken folder before
  writing anything.
- Renderer (`app/tests/marketplace-page.render.test.tsx`, `settings-gating` style): the nav
  row hidden while `developerMode` is false and shown when true, the redirect to Automations
  when the setting drops mid-page, the empty state with the example catalog, a source with
  entries rendering its grid, the Refresh button and Refresh all present only for a source
  with a `url`, Install opening the import modal on the preview step, the add modal's
  inline 422, the Edit button only on a `file` source, the editor opening on the served
  catalog, the picker appending a row, Save sending the §22.7 body, the discard confirm
  on Escape with unsaved edits, and Create catalog… opening the empty editor whose Create
  button stays disabled until Choose folder… picks a location and then POSTs the folder
  with the content.
- e2e: one drive with a file-based source under the test data root (a catalog plus one
  archive exported in the same test), asserting the page lists it and Install lands the
  automation with its triggers off; then, with the native folder dialog stubbed to a temp
  folder, **Create catalog…** opens the editor, **Choose folder…** picks the location,
  **Add automation…** picks the seeded automation, Save lands the catalog and the exported
  archive in that folder (listed by its absolute path), and the new section lists one
  entry.

### 22.7 Catalog authoring

A catalog the user writes in the app is a **folder on this machine** holding
`marketplace-catalog.yaml` plus every archive the app exports for it, written flat beside
it and listed by absolute path (§22.1: references are never relative). Archive files the
user picks from elsewhere are listed where they are. The app creates the folder's
catalog, adds it as an ordinary `file` source (§22.2), and edits it in place. Nothing
here is special at read time: an authored catalog is a §22.1 catalog like any other.

**Create.** **Create catalog…** (page header, and the empty state's MAKE YOUR OWN
section) opens the **catalog editor** modal (below) empty, in create mode: title "Create
catalog", and above the fields a SAVE LOCATION section - the chosen folder's path (mono,
muted; "No folder chosen yet" until one is) beside a dashed **Choose folder…** button
that opens the native folder picker (the §3 `pick-folder` IPC; null cancels), with the
caption "The catalog file and the automations you export land here.". Choosing a folder
fills NAME with the folder's name when NAME is still empty. **Save** (labelled
**Create**, "Creating…" while busy) is disabled until a folder is chosen; it POSTs
`/marketplace/catalogs` with the folder and the editor's content - the backend refuses a
folder that already holds a `marketplace-catalog.yaml` (409 - the user should **Add** it
instead), otherwise writes the catalog and the exported archives there through the
§22.7 save steps and adds the file as a source. Success refetches, closes, and toasts
"Created <name>.". A 422 or 409 shows inline like a save's.

**Edit.** The **Edit** button on a `file` source opens the **catalog editor** modal
(width 640) on `GET …/catalog`, which reads the file on disk (the truth for editing),
not the cache. Title "Edit catalog"; beneath it the catalog file's path (mono, muted,
12 px). Fields, each under a §14 eyebrow: NAME (`ad-input`, placeholder the folder name),
DESCRIPTION (`ad-input`), PUBLISHED LINK (mono `ad-input`, placeholder `https://… where
you publish marketplace-catalog.yaml`, caption "Optional. People who add this catalog can
refresh from here."). Then AUTOMATIONS: one `.ad-card` row per entry holding a title
input (600), a description input, a mono muted line naming the archive (`<file>` for a
saved entry, "Exported on save" for an automation picked from this app, the picked file's
path for a file), and a quiet `fa-xmark` icon button (`aria-label` "Remove entry"); with no
rows, the §14 `EmptyLine` "No automations yet.". Under the list two dashed buttons:
**Add automation…** and **Choose an .autowright file…**. Footer: quiet Cancel / accent
**Save** ("Saving…" beside the §9 spinner while busy); a 422 or 409 shows inline in red
above the footer. Escape or a backdrop click with unsaved edits raises the §14 in-modal
discard confirm ("Discard your catalog edits?" / "The changes you made to this catalog
will be lost."; Discard / Keep editing). Save PUTs `…/catalog`, refetches, closes, and
toasts "Saved <name>.".

- **Add automation…** stacks the **picker** (width 460): title "Add automation", a search
  input (placeholder "Search automations", filters by name substring), and one
  `MenuItemRow`-style row per automation in this app (name 600, description muted, one
  line) - click appends an entry row (title = the automation's name, description = its
  description) and closes the picker. Empty: "No automations yet." / "No automations
  match.". The same automation may be added more than once; the save exports it twice.
- **Choose an .autowright file…** opens the native open dialog through the §3
  `open-archive-path` IPC (filtered to `autowright`; answers `{ path }` or null - the
  backend reads the file itself, only the path travels) and appends a row (title = the
  file's stem, description empty). The file is listed where it is, never copied.
- Images are kept as written on an existing entry and never set by the editor (deferred:
  picking a preview image); reordering entries is deferred too (edit the YAML by hand).

**Save** (`PUT …/catalog`). Each entry names exactly one of `path` (a reference kept as
written), `automationId` (an automation in this app, exported into the folder on save),
or `archiveFile` (an absolute path to an `.autowright` file on this machine, listed as
its `path` after validation); any other mix is a 422 naming the entry. The backend, in
order, so **nothing is written before everything has been checked**:

1. Checks `name`, `description`, `url`, and every title and description to the §22.1
   limits (a blank title is "entry <i>: it has no title").
2. Exports each `automationId` to bytes with the §5.1 export **without parameter values**
   (a marketplace archive is for other people; the §5.1 `--no-values` rule) - an export
   failure (unknown id, a dangling reference) is a 422 "entry <i>: <reason>"; reads each
   `archiveFile` (must be absolute, exist, end in `.autowright`, be at most the §5.1 64 MB,
   and pass the §5.1 archive validation without matching - "entry <i>: <reason>"
   otherwise) and lists it by that path.
3. Plans a file name for each exported archive beside the catalog:
   `transfer.safe_filename` of the automation's name; a name already on disk or already
   planned takes ` 2`, ` 3`, … before the extension. A save **never overwrites** a file
   that exists. The entry's `path` is the file's absolute path.
4. Builds the catalog text with exactly the §22.1 keys, in the §22.1 order
   (`format_version`, `name`, `description` and `url` when non-empty, `entries` with
   `title`, `description` when non-empty, `path`, `image` when non-empty) and runs it
   through the §22.1 parser. Unknown keys and hand-written comments in the previous file
   don't survive a save - the editor owns the file.
5. Writes the exported archives, then the catalog atomically (temp file + rename).
6. Rebuilds the source's cache from the file - the §22.2 fetch-and-swap against the
   source's own origin, which stays the file - and stamps `refreshed_at`.

Removing an entry never deletes its archive file (the folder is the user's; the UI row
says so: a removed entry's file stays in the folder). `PUT` on a `url` source is the
409 above - after a refresh moved a source to its `url`, the local file is no longer what
the page shows, so Edit disappears with it.
