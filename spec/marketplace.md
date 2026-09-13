# Marketplace

## 22. Marketplace (decided, preview behind Developer mode)

A marketplace is a browsable set of shareable automations, described by one YAML file (the
**marketplace catalog**, canonically named `marketplace-catalog.yaml`) listing `.autowright` archives with a title, a description, and a preview image.
Anyone can write one by hand and host it anywhere (a GitHub repository, a web server, a
folder on disk). The user adds a catalog to the app in one of three ways - by dropping or
choosing the file, by typing its path, or by pasting its link - and the app saves a copy
under its own uuid and keeps one row about it in the **catalog table** (§22.2): where the
catalog lives (its **location**: a link, a file path, or nothing), whether it is shown on
the page, and whether it refreshes on its own. **Refresh** re-reads the location; a catalog
with no location is a copy the app holds by itself. The user can also **author** a catalog
in the app: create one (Autowright keeps it and lists it right away), add automations
from this app, from other catalogs, or from `.autowright` files, edit it later, and
**export** its file to share it (§22.7, §22.3 Export).
Installing an entry is the §5.1/§5.2 import, unchanged: the archive is fetched at install
time, previewed, and confirmed through the same two-phase flow, so every §5.1 guarantee
holds (triggers land off, no records are ever created, only matched records are granted).

**Visibility - HIDDEN FOR EVERYONE (2026-09-12).** REMINDER: the marketplace is not
polished and its design is not settled; David parked it to deal with later. Until then
the Marketplace page and its nav row render for nobody, Developer mode or not: one
renderer constant, `MARKETPLACE_HIDDEN` in `App.tsx`, is `true`, and the nav filter and
the redirect below read it. Flipping it to `false` restores the **preview gate**: the page
and its nav row render only while the §4.9 `developerMode` setting is on. That is the only
thing the setting gates here: the §19 routes, the §5 store, and the §20 CLI group are
always live, in every mode - the §2 rule that developer and production mode run the same
code, with no dev-only paths. Turning Developer mode off while the page is open navigates
to Automations (§22.3). The gate is lifted by removing the condition, nothing else; the
feature is built as a normal surface that happens to be hidden. The §22.6 e2e drive is
skipped while the constant is `true` (it reaches the page through the nav row).

### 22.1 Catalog format

One YAML document. `format_version` is the only hard gate, exactly like the §5.1 archive:
a catalog whose `format_version` is not `1` is rejected ("this marketplace catalog is format
`<n>`; this version of Autowright reads format 1"). Unknown keys at any level are ignored
so the format can grow inside one version (unlike archives, a catalog carries no code and
nothing that could land silently wrong).

```yaml
format_version: 1
name: "Community automations"        # optional (max 200 chars): the source's title
description: "Automations I use."    # optional (max 1000 chars)
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
- The catalog says nothing about where it lives: where a copy came from, and where it is
  refreshed from, is the §22.2 catalog table's business, on this machine only. (A `url`
  key an older draft of this format carried is an unknown key now, ignored.)
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
  found out only when the page asks for it (§22.4 image route), and a missing, unreadable,
  or oversized image simply shows the no-image icon - never a refresh failure, never
  stored.
- Caps (untrusted input): a catalog is at most 1 MB; an image at most 5 MB; the archive
  itself is capped by §5.1 (64 MB) at install time. Link fetches use the §5.2 headers
  (`User-Agent: autowright/<version>`), a 30-second per-read timeout, and a 60-second
  whole-download deadline for catalogs and images (they are small; the §5.2 10-minute
  deadline is for archives), HTTPS only with redirects re-checked to stay on https.

### 22.2 Catalog table

Every catalog the app knows is one row in the **catalog table**, `marketplaces.yaml`, under the
§5 data root, with the catalog's own copy in a directory named by the row's id:

```
marketplaces/
  marketplaces.yaml                  # [{id, location, shown, auto_refresh, added_at, refreshed_at, error}]
  <id>/
    marketplace-catalog.yaml    # the app's copy: the last successful read of the location,
                                # or the only copy when the location is null
```

Nothing else ever lives here. A marketplace **references** automations, it never stores
them: every archive an entry names stays where it is (a link, or a file the user owns
somewhere on the machine), and an automation exported for a catalog (§22.7) lands in a
folder the user chose, never under the data root.

| column | values | meaning |
|---|---|---|
| `id` | uuid (§4 id rule) | names the row and its directory |
| `location` | `null`, an absolute file path, or an `https://` link | where the catalog is read from. A path or link is what **Refresh** re-reads. `null` means the app's copy is the only copy: nothing to refresh from, and the copy is what §22.7 edits. |
| `shown` | bool, default `true` | whether the page renders the catalog's entries. A hidden catalog stays in the table and collapses to its header row (§22.3). |
| `auto_refresh` | bool, default `false` | whether the backend refreshes it on its own (below). Meaningless, and kept `false`, while `location` is `null`. |
| `added_at` | §5 UTC timestamp | when the row was made |
| `refreshed_at` | §5 UTC timestamp or `null` | the last *successful* read of the location; `null` for a row that has never been read from a location (a `null` location that was created in the app, not added) |
| `error` | string or `null` | the last refresh failure's message, `null` after every success |

- The catalog's `name`, `description`, and `entries` are **derived from the app's copy at
  load** - never duplicated into the table, so there is one truth. A row whose copy is
  missing or fails §22.1 validation still lists (zero entries, the error "the saved copy
  couldn't be read - refresh to fetch it again" when it has a location, "… - remove this
  marketplace and add it again" when it has none) rather than vanishing. §5 lenient load
  applies to the table itself: a row missing `id` skips with a warning; a `location` that
  is neither `null`, an absolute path, nor an `https://` link skips with a warning; a
  missing `shown` reads `true`, a missing `auto_refresh` reads `false`.
- `kind` is **derived** from `location` wherever a surface needs it: `url`, `file`, or
  `none`. Nothing stores it.
- **Add** takes a link or a file path (the §22.3 modal's three ways - a dropped or chosen
  file, a typed path, a pasted link - all land as one of those two), reads it, validates,
  and caches; any failure answers 422 and stores nothing. The row's `location` is the link
  exactly as pasted (stripped) or the file's resolved path (a leading `~` expanded). The same location (exact string equality; a path compares by
  resolved path) cannot be in the table twice - 409 "that marketplace is already added".
  `shown` starts `true`, `auto_refresh` starts `false`.
- **Refresh** (one row, or every row with a location, in table order) re-reads the
  location: a link is downloaded, a path is read. A row with a `null` location is **not
  refreshable**: a single-row refresh answers 409 "this catalog has no location to refresh
  from" and touches nothing; a refresh-all skips it. On success the catalog is written to a
  temp file and renamed into place, `refreshed_at` is stamped and `error` cleared. Images
  are never fetched here: the page loads them by reference, on demand (§22.3). On failure (unreachable, over the cap,
  invalid) nothing in the copy changes, `refreshed_at` keeps its old value, and `error`
  records the message - the page keeps showing the last good copy with the error beside
  it. A single-row refresh answers 200 with the row either way (`error` says what
  happened) so a refresh-all never stops at the first bad row. One refresh runs at a time
  per backend (a store-wide lock); a refresh runs on the threadpool.
- **Auto refresh.** The backend refreshes every row with `auto_refresh` true and a
  location **at startup** (30 s after the store loads, off the boot path) **and every 6
  hours** after that, in table order, through the same refresh; a failure lands in
  `error` like a manual one, with no notification (the page shows it beside the header).
  A wake from sleep does not trigger one (the next 6-hour tick does). Manual Refresh is
  always available regardless of the flag.
- **Settings** (`PATCH`, §22.4): `location`, `shown`, and `auto_refresh` may be changed
  after the fact. A changed `location` is validated for form only (absolute path, https
  link, or empty for `null`), the 409 duplicate rule applies, and the copy is untouched
  until the next Refresh reads from the new place; setting it to `null` keeps the copy and
  turns `auto_refresh` off; leaving `null` for a path or link makes the row refreshable
  again. A `null` location can only be set on a row whose copy is readable (otherwise
  there would be nothing left).
- **Remove** deletes the row and its directory (the copy of the catalog, nothing more).
  Automations installed from it are ordinary automations, and archive files the catalog
  referenced are the user's files; both are untouched.
- The table is loaded once at backend startup into memory and rewritten whole on every
  change, like agents and secrets. Nothing here is ever executed: a catalog is data, an
  archive lands only through import.
- Compatibility: a new additive store (§21.4 entry, 2026-09-11). `format_version: 1` on
  the catalog is the hard gate for the file people share; `marketplaces.yaml` follows the §5
  lenient-load rule with no version marker.

### 22.3 Marketplace page

**Nav.** A "Marketplace" row (icon `fa-store`) sits between Secrets and Settings in the §9
rail, rendered only while `settings.developerMode` is true **and** `MARKETPLACE_HIDDEN`
is false (§22 visibility - it is `true` for now, so the row renders for nobody); it
carries no count pill. The `Page` union gains `marketplace`. When the row's condition
stops holding while `page` is `marketplace` (the Settings toggle, a §20 `settings set`
seen through the store refresh, or the constant), an effect in the app shell calls
`go('automations')` - the same shape as the §9.3 overlay closing itself when the setting
drops.

**Page.** Title "Marketplace" with header actions: a ghost **Create catalog…** (§22.7;
always rendered), a ghost **Refresh all** (rendered only when at least one catalog is
refreshable, i.e. has a location; the §9 busy spinner while running) and the accent **Add
marketplace…** button. The page fetches §19 `GET /marketplace` on mount and after each of
its own actions, and refetches when the §19 `marketplace.changed` WebSocket event arrives
(a §20 CLI change or an auto refresh shows without a reload); it shows the §14
`PageLoading` line until the first answer.

**Empty state** (no catalogs): the §14 `EmptyState` with a bold first line "No marketplaces
yet" over the body "Add a marketplace catalog someone shared - drop the file, type its
path, or paste its link - to browse the automations it lists." (the machine noun through
the §9 per-OS copy rule) and an "Add marketplace…" button. Below it, one **MAKE YOUR OWN**
section: the eyebrow, one muted line "Create a catalog here and add automations from this
Mac. Or write the YAML by hand - list each archive by its full path on this Mac or an
https link, save it as marketplace-catalog.yaml, then add it here.", a ghost **Create
catalog…** button (the §22.7 create flow), and the §22.1 example catalog in a mono code
box (`.ad-card` padding 14, `pre` at 12 px `--mono`, selectable, horizontal scroll inside
the box). The section renders only in the empty state - once a catalog exists the user
has seen the shape.

**Per catalog** - one section each, in table order:
- Header row: the catalog name (600, 15 px), a §14 `MetaChip` naming the location (link
  icon + hostname for a link, file icon + file name for a path, `fa-box-archive` +
  "Kept by Autowright" for `null`; the full location in the `title` attribute), and a
  muted line "Refreshed <date label>" (the §4.1 shared date-label scheme, e.g. "Refreshed
  Today, 8:00 AM"; omitted while `refreshedAt` is null) for a catalog with a location, or
  "Added <date label>" (from `addedAt`) for one without. A hidden catalog (`shown` false)
  adds a muted `MetaChip` "Hidden" after the location chip. While `error` is set, an
  amber `Notice` beneath the header: "Couldn't refresh: <error>. Showing the last copy."
  (for a catalog with no readable copy - `cached` false: "Couldn't load: <error>.").
  Quiet square icon buttons on the right (`.ad-btn-ghost.icon`; Remove adds `.danger`
  so it reads red - never the accent-filled §12 execute shape, two orange squares beside
  a title read as a call to action): **Edit** (`fa-pen`, `aria-label` "Edit catalog";
  rendered when the location is a path or `null` - the copy is on this machine - and
  opening the §22.7 editor), **Export** (`fa-file-export`, `aria-label` "Export catalog";
  rendered while `cached` is true, for every kind of location - it saves the catalog
  file the app holds: §19 `GET …/file` for the bytes, then the §3 `save-file` dialog with
  the default name `marketplace-catalog.yaml`; a saved file toasts "Exported to
  <path>.", a cancelled dialog does nothing, a failed fetch toasts the reason. This is
  how a catalog created in the app leaves the app: the user puts the file wherever they
  share from), **Refresh** (`fa-rotate`, spinner while running,
  `aria-label` "Refresh"; rendered only when the catalog has a location), **Settings**
  (`fa-gear`, `aria-label` "Catalog settings"; always) and **Remove** (`fa-trash`,
  `aria-label` "Remove") - Remove opens a danger `ConfirmModal`, title "Remove
  "<name>"?", body "Automations you already installed from it stay, and so does every
  archive file it lists. You can add the marketplace again later.", confirm label
  "Remove". Removing refetches the list; no toast. The Refresh all, Add, and Install
  buttons read "Refreshing…" / "Adding…" / "Installing…" beside the §9 spinner while
  busy.
- The description, muted, when present - **only while shown**. A hidden catalog renders
  its header row and nothing else: no description, no grid. Show it again through the
  settings modal.
- The **entry grid** (shown catalogs only): `grid-template-columns: repeat(auto-fill,
  minmax(220px, 1fr))`, gap 14. Each entry is an `.ad-card` with zero padding and
  `overflow: hidden`: a 16:9 image area at the top (`object-fit: cover`; while the image is
  loading, or when the entry lists no image or its image can't be loaded, a `--bg-inset`
  placeholder with a faint centered `fa-image` icon - the no-image icon), then a 14 px
  padded body holding the title (600, 13.5 px), the description (muted, 12.5 px, clamped
  to three lines with an ellipsis), and a footer row with the accent **Install** button. A
  catalog that lists no entries shows the §14 `EmptyLine` "This marketplace lists no
  automations yet."
- Images load **by reference, on demand**, through the authenticated §19 image route (the
  renderer talks to the backend with a bearer header, so a plain `<img src>` can't carry
  it, and a local path can't be loaded from the page at all): for an entry whose `image`
  is set, the page fetches the bytes and shows a blob URL, cached in memory per (catalog
  id, index, `refreshedAt`) for the session and revoked when the page unmounts. Nothing
  is written to disk. A failed fetch (404, 502, network) keeps the no-image icon.

**Catalog settings modal** (width 460, from the gear): title "Catalog settings", the
catalog's name muted beneath it. Three rows under §14 eyebrows:
- LOCATION: a mono `ad-input` holding the location (empty for `null`; placeholder
  `https://… or /path/to/marketplace-catalog.yaml`), caption "Where Refresh reads this
  catalog from. Leave it empty to keep only the copy Autowright has.".
- SHOWN: a §14 `Toggle`, label "Show this marketplace on the page".
- AUTO REFRESH: a §14 `Toggle`, label "Refresh on its own (at launch and every 6 hours)";
  disabled with the caption "Needs a location." while the LOCATION field is empty.
Footer: quiet Cancel / accent **Save** ("Saving…" while busy). Save PATCHes §19
`…/sources/{id}` with the three fields; a 422 or 409 shows inline in red above the
footer. Success closes, refetches, no toast. The location field is validated for form
only - the copy is untouched until the next Refresh.

**Install.** Clicking Install POSTs §19 `…/entries/{index}/preview`; while in flight the
button shows the busy spinner. A 422 (unreachable archive, invalid archive) toasts the
reason. Success opens the §9.1 **import modal directly on its preview step** - the modal
takes an optional initial preview, skipping the input step - with the source row reading
"<marketplace name> · <entry title>" behind a store icon. Its Back button closes the modal
(there is no input step to return to). Import confirms the token and hands off to the §9.1
summary modal exactly as a URL import does.

**Add marketplace modal.** The §9.1 import modal's input-step shape, retitled: title "Add
marketplace", intro "Browse automations someone published - a marketplace catalog file on
this Mac, or one at a link.". Three ways in, two controls:

- A **drop zone**: a dashed `.ad-card` (min-height 96, centered `fa-file-import` icon over
  the line "Drop a marketplace-catalog.yaml here, or click to choose one on this Mac").
  Clicking it opens the native open dialog through the §3 main-process `open-catalog` IPC
  (filtered to `yaml`/`yml`; answers `{ path }` or null on cancel). Dragging a file over it
  highlights the border in the accent; dropping exactly one `.yaml`/`.yml` file adds it -
  the renderer asks the §3 preload `pathForFile(file)` helper (Electron
  `webUtils.getPathForFile`) for the dropped file's path, and only the path travels, the
  backend reads the file itself. Dropping anything else (several files, another
  extension, a folder) shows the inline error "Drop one .yaml file." under the zone and
  adds nothing. While an add is in flight the zone reads "Reading…" and ignores drops.
- The OR divider, then one field, FROM A LINK OR FILE PATH (mono, placeholder `https://…
  or /path/to/marketplace-catalog.yaml`, caption "An https link, or the path of a catalog
  file on this Mac."). A value starting with `https://` is sent as `{ url }`; anything
  else is sent as `{ path }` exactly as typed (the backend expands a leading `~` and
  requires the result to be absolute - "give the catalog file's absolute path"). Enter
  submits.

Footer: quiet Cancel / accent **Add** (disabled while the field is empty; the drop zone
adds on its own). Every way POSTs §19 `POST /marketplace/sources`; a 422 or 409 shows
inline in red under the control that produced it. Success closes the modal, refetches,
and toasts "Added <name>."

### 22.4 Backend API (§19 addendum)

All routes authenticated like the rest of §19. `Source` (one catalog-table row with its
derived content) is `{ id, kind, location, shown, autoRefresh, name, description,
addedAt, refreshedAt, error, cached, entries: [{ index, title, description, archive,
image }] }` - `kind` derived from `location` (`url`, `file`, `none`), `location` the
§22.2 column (null, a path, or a link), `archive` the entry's `path` as written (an https
URL or an absolute local path), `image` the entry's image reference as written (an https
URL or an absolute local path) or `null` when it lists none (the bytes come from the
image route on demand; the reference itself is what the §22.7 picker copies when the
entry is added to another catalog - a boolean before 2026-09-12), `cached` whether a
readable copy exists (false only for the §22.2 unreadable-copy case; an empty catalog is
still cached).

- `GET /marketplace` → `{ sources: [Source] }` in table order, hidden ones included (the
  page collapses them; the CLI marks them).
- `POST /marketplace/sources` `{ url }` or `{ path }` (exactly one, non-empty) → `Source`.
  Fetches and validates first (§22.2 add): any failure answers 422 with the reason and
  stores nothing; a location already in the table answers 409. A `path` may start with
  `~` (expanded to the home directory) and must then be an absolute path to an existing
  readable file.
- `PATCH /marketplace/sources/{id}` `{ location?, shown?, autoRefresh? }` → `Source`
  (§22.2 settings; every field optional, only the given ones change). `location` is a
  string: empty or blank means `null`, otherwise an `https://` link or an absolute path
  (`~` expanded; 422 "give an https link or an absolute path" otherwise); a location
  already on another row → 409 "that marketplace is already added"; `null` on a row
  whose copy is unreadable → 422 "there's no saved copy to keep - refresh first".
  Setting `location` to `null` forces `autoRefresh` false; `autoRefresh: true` with a
  `null` location (after the patch) → 422 "auto refresh needs a location". Unknown id →
  404. Nothing is fetched.
- `POST /marketplace/sources/{id}/refresh` → `Source` (200 even when the refresh failed;
  `error` carries the reason and the copy is unchanged). Unknown id → 404; a `null`
  location → 409 "this catalog has no location to refresh from", the row untouched.
- `POST /marketplace/refresh` → `{ sources }` after refreshing every catalog with a
  location, in order (the others are listed as they were).
- `DELETE /marketplace/sources/{id}` → `{ ok: true }`; unknown id → 404.
- `GET /marketplace/sources/{id}/entries/{index}/image` → the entry's image bytes, read
  **on demand by reference**: an https reference is downloaded with the §22.1 caps,
  headers, and deadline (never stored), a local path is read under the same 5 MB cap; the
  content type matches the reference's extension. 404 when the source or entry doesn't
  exist or the entry lists no image; 502 with the reason when the reference can't be
  read (a missing file, a failing host, over the cap) - the page shows the no-image icon
  either way. Runs on the threadpool.
- `POST /marketplace/sources/{id}/entries/{index}/preview` → `{ token, preview }` exactly
  as `POST /automations/import/url` (§19): the archive is fetched
  (`transfer.fetch_archive` for an https reference, a plain read capped at the §5.1 64 MB
  for a local path), fully validated, and parked under a §5.2 token; `preview.sourceUrl`
  and `preview.resolvedUrl` both carry the archive reference. Any failure answers 422;
  the confirm is the ordinary `POST /automations/import/confirm`.
- `POST /marketplace/catalogs` `{ folder?, exportFolder?, name, description, entries }` →
  `Source` (§22.7 create). With `folder` (an absolute path to an existing directory, 422
  otherwise): a folder already holding `marketplace-catalog.yaml` → 409 "that folder
  already holds a marketplace catalog - add it instead"; a folder whose catalog path is
  already a location → 409 "that marketplace is already added"; the catalog is written
  there and the row's location is that file. Without `folder`: the row's location is
  `null` and the catalog is written straight to the row's copy. The content goes through
  the §22.7 save steps (422 with the reason and nothing written on any failure). Content
  may be omitted (the §22.5 CLI `create` sends none): an empty catalog named after the
  folder (or "My catalog" without one).
- `GET /marketplace/sources/{id}/file` → the app's copy of the catalog, byte for byte
  (`Content-Type: application/yaml`, `Content-Disposition: attachment;
  filename="marketplace-catalog.yaml"`) - the §22.3 Export. Every kind of location: for
  a path the copy mirrors the file, for a link it is the last successful read. 404
  unknown id; 422 with the §22.2 unreadable-copy message when the copy can't be read.
- `GET /marketplace/sources/{id}/catalog` → `{ name, description, entries: [{ index,
  title, description, path, image }] }` (§22.7): the catalog as the editor should see it
  - the file at the location for a path, the copy for `null` - references as written.
  404 unknown id; 409 "only a catalog on this machine can be edited" for a link location;
  422 with the §22.1 message when the file can't be read or parsed.
- `PUT /marketplace/sources/{id}/catalog` `{ exportFolder?, name, description, entries:
  [{ title, description, path?, image?, automationId?, archiveFile? }] }` → `Source`
  (§22.7 save). 404 / 409 as the GET; 422 with the reason and nothing written on any
  failure.
- WebSocket event `marketplace.changed` (no payload) after every add, refresh (manual or
  automatic), settings change, remove, create, and save.

Request models (§19 `models.py`): `MarketplaceAdd { url?: str, path?: str }` with the
exactly-one rule validated in the route (422 "give a link or a file path, not both" / "give
a link or a file path"); `MarketplaceSettings { location?: str, shown?: bool, autoRefresh?:
bool }`; `MarketplaceCatalogSave { exportFolder?: str, name: str, description: str,
entries: [MarketplaceCatalogEntry { title, description, path?, image?, automationId?,
archiveFile? }] }` with the §22.7 exactly-one rule per entry validated in the store;
`MarketplaceCatalogCreate` = the save fields plus `folder?: str`.

### 22.5 CLI (§20 addendum)

```
autowright marketplace list                    every catalog with its entries
autowright marketplace add <url-or-path>       add a marketplace by https link or file path
autowright marketplace set <source> KEY=VALUE… location= shown=on|off autoRefresh=on|off
autowright marketplace refresh [<source>]      refresh one catalog, or every one with a location
autowright marketplace remove <source>         remove a catalog (installed automations stay)
autowright marketplace install <source> <n>    install entry n (1-based, as `list` prints it)
autowright marketplace create [<folder>]       create a catalog (in a folder, or kept by the app) (§22.7)
autowright marketplace catalog set <source> KEY=VALUE…        name= description= (§22.7)
autowright marketplace catalog add <source> <automation-or-file> [--title T] [--description D] [--export-to DIR]
autowright marketplace catalog remove <source> <n>            drop entry n; its archive file stays
```

`<source>` resolves like every other §20 reference: an id, an unambiguous id prefix, or an
exact name (case-insensitive); ambiguity and no-match are the standard §20 errors. A file
path given to `add` is made absolute against the current directory before it travels.
`list` prints one block per catalog - `<name> [<id8>]  <location>` (`(kept by Autowright)`
for `null`), with ` hidden` and/or ` auto-refresh` appended to that line when set; then
`  refreshed <when>` for a catalog with a location, `  added <when> (no location to
refresh from)` for one without, or `  couldn't refresh: <error>` when the last refresh
failed; then each entry as `  <n>. <title> - <description>` (1-based; the description
omitted when empty; "  no automations listed" for an empty catalog). `add` prints `added
<name> [<id8>] - <count> automation(s)`. `set` takes `location=` (empty clears),
`shown=on|off`, and `autoRefresh=on|off` (any other key exits 1 naming the three) and
prints `updated <name>`. `refresh <source>` on a catalog without a location exits 1 with
`'<name>' has no location to refresh from`; otherwise `refresh` prints one `refreshed
<name> - <count> automation(s)`, `couldn't refresh <name>: <error>`, or (refresh-all only)
`skipped <name> - no location to refresh from` line per catalog (exit 1 when any failed; a
skip is not a failure); `remove` prints `removed <name>`. `install` checks `<n>` against
the catalog's entry count first (`entry numbers start at 1 - see \`autowright marketplace
list\`` / `'<name>' lists <count> automation(s) - there is no entry <n>`, exit 1), then
previews the entry, confirms immediately (the typed command is the user's go-ahead, §20
import rule), and prints exactly the §20 `automation import` summary lines (the two
commands share one printer), including the foreground package ensure.

The §22.7 authoring verbs go through `GET`/`PUT …/catalog`, so they take a catalog whose
location is a path or `null` (a link location exits 1 with the 409 detail). `create
[<folder>]` makes the path absolute against the current directory when given and prints
`created <name> [<id8>] at <catalog path>` (or `created <name> [<id8>] (kept by
Autowright)`). `catalog set` takes `name=` and `description=` (an empty value clears; any
other key exits 1 naming the two) and prints `saved <name>`. `catalog add` takes an
automation reference (resolved like `automation` verbs) or, when the argument ends in
`.autowright` and names an existing file, that archive (made absolute); `--title` and
`--description` override the defaults (the automation's name and description, or the
file's stem and nothing); an automation reference on a catalog with a `null` location
needs `--export-to DIR` (exit 1 `'<name>' is kept by Autowright - say where to export
with --export-to` otherwise), which is ignored for a file location; it prints `added
<title> to <name> - entry <n>`. `catalog remove <n>` (1-based) prints `removed entry <n>
(<title>) from <name> - its archive file stays`; a number out of range exits 1 like
`install`.

### 22.6 Tests

- Backend (`tests/test_marketplace.py`): §22.1 validation (format gate, caps, extension
  rules, index-naming errors, a `url` key ignored), the two reference forms (https URL,
  absolute local path) with relative references and other schemes rejected naming the
  entry, the catalog table's lenient load (missing `shown`/`auto_refresh` defaults, a bad
  `location` skipped), add by link and by path (`~` expanded) with the 409 duplicate rule,
  refresh re-reading the location (link downloaded, path read) and keeping the copy on
  failure, a `null` location refusing refresh (409) and being skipped by refresh-all,
  refresh never touching images, settings (location form check, `null` forcing
  auto-refresh off, the unreadable-copy guard, the duplicate rule), the auto-refresh
  sweep refreshing only flagged rows with a location and recording failures in `error`,
  and every §22.4 route with the network monkeypatched (add 422/409, PATCH 422/409,
  refresh 200-with-error, the image route reading a local path and an https reference on
  demand, 404 for no image, 502 for an unreadable one, entry preview producing a
  confirmable token, the file route answering the copy's bytes with the yaml content
  type - 404 unknown, 422 when the copy is unreadable).
- Backend authoring (§22.7, same file): create with a folder writes the catalog there and
  the row's location is that file, 409 on a folder that already holds one, 422 on a
  missing folder; create without a folder writes the copy and the location is `null`; GET
  answers the file (or the copy) as written and 409 for a link location; PUT exports an
  automation without parameter values under its safe name into the export folder (beside
  the catalog for a path location, `exportFolder` for `null` - 422 "say where to export"
  when missing) and lists it by absolute path (a taken name gets ` 2`), validates and
  references an archive file in place (a non-archive is a 422 naming the entry; nothing is
  copied), keeps a `path` entry and its `image` as written, rewrites the file with the
  §22.1 keys only, refreshes the copy, writes nothing on a 422, and leaves the archive
  file behind when its entry is removed.
- Renderer (`app/tests/marketplace-page.render.test.tsx`, `settings-gating` style): the nav
  row hidden while `developerMode` is false and shown when true, the redirect to
  Automations when the setting drops mid-page, the empty state with the example catalog,
  a catalog with entries rendering its grid, the Refresh button and Refresh all present
  only for a catalog with a location, a hidden catalog collapsing to its header with the
  "Hidden" chip, the settings modal PATCHing the three fields with AUTO REFRESH disabled
  while LOCATION is empty, Install opening the import modal on the preview step, the add
  modal's inline 422 for a typed path and a pasted link and its drop zone adding a dropped
  `.yaml` (path through `pathForFile`) and refusing anything else, the Edit button on path
  and `null` locations only, the editor opening on the served catalog with the details
  form viewed (its LOCATION line naming the file) and one navigator row per entry (title over its reference label), clicking
  a row viewing its entry form with the title, description, and reference editable, the
  picker's THIS MAC tab listing and filtering this app's automations, a pick exporting
  the automation without values through `saveFile` and landing in the details step as
  the saved file (a cancelled dialog returns to the list, a failed export shows its
  reason) with the title and description prefilled before Add appends and views the row,
  the A
  CATALOG tab listing the other catalogs' entries (the working catalog and empty ones
  left out) and a pick carrying the entry's `path` and `image` as written, the A FILE tab
  going through `openArchivePath` into the details step with the file's stem, Remove from
  catalog dropping the viewed entry, a blank title and a malformed reference stopping
  Save on the offending entry with the reason in the footer, Save sending the §22.7 body
  (`path` for a kept row, `archiveFile` for an unedited exported or picked file, `image`
  when set), a backend `entry <i>:` 422 viewing that entry, the discard
  confirm on Escape with unsaved edits, Create catalog… opening the empty editor (no
  folder anywhere; LOCATION reads "Kept by Autowright") whose Create button POSTs the
  content alone, and the Export button fetching the file and handing it to `saveFile` as
  `marketplace-catalog.yaml` with the "Exported to <path>." toast (absent while `cached`
  is false).
- e2e: one drive with a file-based catalog under the test data root (a catalog plus one
  archive exported in the same test), asserting the page lists it and Install lands the
  automation with its triggers off; then, with the native save dialog stubbed to
  `Watcher.autowright` in a temp folder, **Create catalog…** opens the editor, **Add
  automation…** picks the seeded automation on the THIS MAC tab (the export lands through
  the stubbed save dialog) and Add accepts the prefilled details, the navigator lists the
  new row, Create lands the catalog as a second section kept by Autowright listing one
  entry by the archive's absolute path; then, with the save dialog re-stubbed to
  `marketplace-catalog.yaml` in that folder, **Export catalog** on the new section writes
  the file beside the archive.

### 22.7 Catalog authoring

A catalog the user writes in the app is a §22.2 row whose copy the app edits in place.
One created in the app has a **`null`** location - Autowright keeps the only copy, lists
it on the page at once, and the §22.3 Export hands the file out whenever the user wants
to share it (the editor never asks where to save; a folder is only ever chosen through
the §22.5 CLI `create <folder>` or the §22.3 settings' LOCATION). A row with a **file
path** location (added from a file, or given one) is edited the same way, with the copy
mirroring the file. Either way the
catalog only **references** archives, by absolute path or https link (§22.1): an archive
file the user picks is listed where it is, and an automation from this app is exported
**where the user saves it** - the picker runs the §9.2 export's native save dialog at
pick time, so the user manages where every archive lives and the editor never chooses a
folder (the EXPORT FOLDER row and the editor's export-on-save were removed 2026-09-12;
the §22.4 `automationId` / `exportFolder` save fields stay for the §22.5 CLI). Nothing
here is special at read time: an authored catalog is a §22.1 catalog like any other. A
catalog with a link location is not editable here (the file isn't on this machine).

**Create** and **Edit** share one surface, the **catalog editor** (redesigned 2026-09-12
as a two-column form; the earlier single-column list of input cards is gone).
**Create catalog…** (page header, and the empty state's MAKE YOUR OWN section) opens it
empty in create mode - no location to choose, the catalog is kept by Autowright; the
**Edit** button on a catalog with a path or `null` location
opens it in edit mode on `GET …/catalog` (the file at the location, or the copy; the §14
`PageLoading` well in the form pane until it lands - a 409 or 422 toasts the reason and
closes). The catalog open in the editor is the **working catalog**.

**The catalog editor** is a §14 `Modal` on the §9.2 step-script modal's two-column frame:
`min(960px, 92vw)` wide, `padding: 0`, `overflow: hidden`, a fixed height of
`min(680px, 82vh)` so viewing a different row never resizes it, no title row (the
navigator header carries the mode), `aria-label` "Create catalog" / "Edit catalog".

- Left, the **catalog navigator** (280 px, `--hairline-dim` right border, its own §14
  overlay-scrollbar pane): a 44 px header (dim-hairline bottom border) holding the eyebrow
  CREATE CATALOG / EDIT CATALOG. Then the **details row**: the catalog's name as typed
  (600 when viewed, 500 muted otherwise; the muted "Untitled catalog" while blank) over a
  muted one-line sub naming where it lives - the catalog file's path for a path location,
  "Kept by Autowright" for `null` and in create mode. Then the eyebrow AUTOMATIONS · <n>
  (`padding: 14px 18px 4px`) and one **entry row** per entry in catalog order: the title
  (the muted "Untitled" while blank) over a muted sub naming the reference - the hostname
  for an https link, the file name for a path, "No archive yet" while the reference is
  blank. Rows are
  the §9.2 navigator's rows: the viewed row is a plain block on the `--bg-active` wash with
  the inset accent bar and `aria-current="true"`, every other row an
  `.ad-btn-bare.ad-hover-row.ad-focus-inset` button that views it. With no entries, the
  §14 `EmptyLine` "No automations yet." under the eyebrow. Pinned under the list (outside
  the scroll pane; `padding: 12px 14px`, dim-hairline top border): a full-width dashed
  **Add automation…** button (`fa-plus`) opening the picker. Arrow keys are not bound
  here: the pane on the right is a form, and ↑ / ↓ belong to its inputs.
- Right, the **form pane** (flex 1): a 44 px toolbar (dim-hairline bottom border) holding
  the eyebrow DETAILS or AUTOMATION <i> OF <n> (1-based) and, at the right, an
  `.ad-btn-icon` Close (`fa-xmark`, `aria-label` "Close"; it goes through the discard
  guard below). Beneath, a scroll pane padded `18px 22px` holding the viewed form, every
  field under a §14 eyebrow with the §14 caption under it where one is named:
  - **Details form** (viewed when the editor opens, and after the last entry is removed).
    First a LOCATION line: the catalog file's full path (mono, muted, 12 px, wrapping -
    the navigator row only has room for its tail) with the caption "The file this editor
    writes." for a path location; otherwise (a `null` location, and always in create
    mode) "Kept by Autowright" with the caption "Autowright keeps the catalog. Export its
    file from the Marketplace page to share it.". Then NAME (`ad-input`, placeholder "My
    catalog") and DESCRIPTION (`ad-input`).
  - **Entry form**: TITLE (`ad-input`), DESCRIPTION (`ad-input`), then AUTOMATION: a mono
    `ad-input` holding the reference exactly as written (placeholder
    `https://…/name.autowright or /path/to/name.autowright`, caption "An https link, or
    the archive's absolute path on this Mac." through the §9 per-OS copy rule) - every
    entry is a reference, whichever way it came in. Then IMAGE: a mono `ad-input` (placeholder
    `Optional: https://… or /path/to/preview.png`, caption "A preview for the marketplace
    page: an https link, or an absolute path to a .png, .jpg, .jpeg, .webp, or .gif."). Under
    the fields, 18 px down, a quiet danger **Remove from catalog** text button
    (`fa-trash`; `aria-label` "Remove entry") with the muted caption "Its archive file
    stays where it is." beside it. Removing views the entry after it (the one before when
    it was last; the details form when none is left).
- **Footer**, full width under both columns (dim-hairline top border, `padding: 14px
  22px`): at the left the inline error (red, 12.5 px) when one is set; at the right quiet
  **Cancel** / accent **Save** (**Create** in create mode; "Saving…" / "Creating…" beside
  the §9 spinner while busy). Before sending, the
  editor checks every entry itself and, at the first problem, views that entry and puts
  the reason in the footer: a blank title ("Give this automation a title."); a reference
  that is neither an `https://` link nor an absolute path, or doesn't end in `.autowright`
  ("Give an https link or an absolute path to an .autowright file."); a non-empty image of
  the wrong form ("Give an https link or an absolute path to a .png, .jpg, .jpeg, .webp,
  or .gif image."). A 422 or 409 from the backend shows in the footer as it comes, and one
  whose message starts `entry <i>:` views entry `i` as well. Create POSTs
  `/marketplace/catalogs` with the content alone (never a `folder` - that option is the
  §22.5 CLI's): the backend writes the catalog to the new row's copy through the save
  steps and adds the row; success refetches (the new catalog is on the page at once),
  closes, and toasts "Created <name>.". Save PUTs `…/catalog`,
  refetches, closes, and toasts "Saved <name>.". Escape, a backdrop click, the Close
  button, or Cancel with unsaved edits (any field, folder, or entry differs from what the
  editor opened on) raises the §14 in-modal discard confirm ("Discard your catalog
  edits?" / "The changes you made to this catalog will be lost."; Discard / Keep editing).

**Add automation picker** (stacked over the editor, width 520, z 80): title "Add
automation" (15/600). Beneath it a row of `.ad-btn-tab` chips (`aria-pressed` on the
active one, gap 6): **This Mac**, **A catalog**, **A file**; This Mac is active when the
picker opens.

- THIS MAC: a search input (placeholder "Search automations"; filters by name substring)
  over a `.ad-card` list (padding 4, max-height 320, scrolling) of one `MenuItemRow` per
  automation in this app (name, description muted on one line). Empty: "No automations
  yet." / "No automations match.". Clicking a row **exports the automation now**: the §19
  export **without parameter values** (a marketplace archive is for other people, the §5.1
  `--no-values` rule) and then the §3 `save-file` dialog with the default name
  `<transfer.safe_filename(name)>.autowright` - the same dialog the §9.2 Export… uses, so
  the user chooses where the archive lives. While the export runs the list is replaced by
  the line "Exporting <name>…" beside the §9 spinner. Cancelling the dialog returns to the
  list with nothing written; a failed export shows its reason in red under the list. On
  save the picker goes to the details step with the saved file as the pick.
- A CATALOG: the same search (placeholder "Search catalogs"; matches an entry's title or
  its catalog's name) over a list grouped by catalog in table order: an inert `header`
  `MenuItemRow` per catalog (its name, sub the §22.3 location label - hostname, file name,
  or "Kept by Autowright") followed by one pickable row per entry (title, description).
  Hidden catalogs are listed like any other; the working catalog (edit mode) and a
  catalog with no entries are left out. Empty: "No other catalogs list automations yet."
  (create mode: "No catalogs list automations yet.") / "No automations match.".
- A FILE: a dashed full-width **Choose an .autowright file…** (`fa-file-import`) opening
  the native open dialog through the §3 `open-archive-path` IPC (filtered to
  `autowright`; answers `{ path }` or null on cancel - the backend reads the file itself,
  only the path travels), caption "The file is listed where it is, never copied.".

Picking (a row, or a file) turns the picker to its **details step**: beneath the title a
muted line naming the pick - the saved or chosen file's path (mono) for a This Mac export
or a file, "<entry title> · <catalog name>" for a catalog entry - then TITLE (`ad-input`,
prefilled with the automation's name / the entry's title / the file's stem) and
DESCRIPTION (`ad-input`, prefilled with the automation's description / the entry's
description / empty). Footer: quiet **Back** (returns to the list with the tab kept; an
exported archive stays where it was saved) / accent **Add** (disabled while TITLE is
blank; Enter in either field adds). Add appends the entry to the end of the working
catalog, views it in the editor, and closes the picker: an exported automation and a
chosen file carry the file's path; a catalog entry carries its `path` and `image` exactly
as the other catalog lists them (never fetched or copied - a local path in someone else's
catalog is listed as it is). The same automation may be exported and added more than
once. Reordering entries is deferred (edit the YAML by hand).

**Save body.** Each entry sends its title, description, and image (when non-empty), plus
exactly one of: `archiveFile` (a file exported or picked in this editing session whose
AUTOMATION field still holds that path - the backend then checks it is a real archive)
or `path` (everything else, as written: a saved entry, a catalog pick, or a reference the
user typed or edited). The editor never sends `automationId` or `exportFolder`.

**Save** (`PUT …/catalog`). Each entry names exactly one of `path` (a reference kept as
written), `automationId` (an automation in this app, exported on save), or `archiveFile`
(an absolute path to an `.autowright` file on this machine, listed as its `path` after
validation); any other mix is a 422 naming the entry. The backend, in order, so **nothing
is written before everything has been checked**:

1. Checks `name`, `description`, and every title and description to the §22.1 limits (a
   blank title is "entry <i>: it has no title"). Settles the **export folder**: the
   catalog file's folder for a path location, else `exportFolder` (`~` expanded; must be
   an absolute path to an existing directory) - required only when some entry is an
   `automationId` (422 "say where to export the automations you added" otherwise).
2. Exports each `automationId` to bytes with the §5.1 export **without parameter values**
   (a marketplace archive is for other people; the §5.1 `--no-values` rule) - an export
   failure (unknown id, a dangling reference) is a 422 "entry <i>: <reason>"; reads each
   `archiveFile` (must be absolute, exist, end in `.autowright`, be at most the §5.1 64 MB,
   and pass the §5.1 archive validation without matching - "entry <i>: <reason>"
   otherwise) and lists it by that path.
3. Plans a file name for each exported archive in the export folder:
   `transfer.safe_filename` of the automation's name; a name already on disk or already
   planned takes ` 2`, ` 3`, … before the extension. A save **never overwrites** a file
   that exists. The entry's `path` is the file's absolute path.
4. Builds the catalog text with exactly the §22.1 keys, in the §22.1 order
   (`format_version`, `name`, `description` when non-empty, `entries` with `title`,
   `description` when non-empty, `path`, `image` when non-empty) and runs it through the
   §22.1 parser. Unknown keys and hand-written comments in the previous file don't
   survive a save - the editor owns the file.
5. Writes the exported archives, then the catalog atomically (temp file + rename): to the
   location for a path, to the row's copy for `null`.
6. For a path location, refreshes the copy from the file (the §22.2 refresh) and stamps
   `refreshed_at`; for `null`, the copy is what was just written.

Removing an entry never deletes its archive file (the UI row says so: a removed entry's
file stays where it is). `PUT` on a link location is the 409 above.
