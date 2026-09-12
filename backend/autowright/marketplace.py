"""§22 marketplace: the catalog format (§22.1) and the catalog table (§22.2).

A marketplace is one hand-written YAML file listing §5.1 `.autowright` archives.
This module parses and validates it (every reference is an https URL or an
absolute local path, taken as it is - nothing is ever resolved against where
the catalog came from), keeps one table row per catalog plus the app's copy of
it under the §5 data root, refreshes that copy from the row's location, reads
preview images on demand, and hands the §19 install route the archive bytes.
Nothing here is ever executed, and nothing but the catalog copy is ever stored:
a marketplace references automations, it never holds them.
"""
from __future__ import annotations

import http.client
import logging
import os
import shutil
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

from . import paths, timefmt, transfer
from .storage import new_id
from .yamlio import atomic_write_text, load_yaml, save_yaml

log = logging.getLogger("autowright.marketplace")

FORMAT_VERSION = 1

# §22.1/§22.2: the catalog's canonical file name - what the copy is saved as,
# and the stem whose default name would collide across every unnamed catalog.
CATALOG_FILENAME = "marketplace-catalog.yaml"
CANONICAL_STEM = Path(CATALOG_FILENAME).stem
KEPT_NAME = "My catalog"

# §22.1 caps (untrusted input): the archive itself is capped by §5.1 at install.
MAX_CATALOG_BYTES = 1024 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_ENTRIES = 200
MAX_NAME = 80
MAX_DESCRIPTION = 500
MAX_TITLE = 120
MAX_ENTRY_DESCRIPTION = 1000

# §22.1: catalogs and images are small, so the §5.2 10-minute archive deadline
# would let a trickling server pin a threadpool worker for ten minutes.
FETCH_DEADLINE_S = 60
_FETCH_CHUNK = 64 * 1024

# §22.2 auto refresh: 30 s after the store loads, then every 6 hours.
AUTO_REFRESH_DELAY_S = 30
AUTO_REFRESH_INTERVAL_S = 6 * 60 * 60

ARCHIVE_EXTENSION = ".autowright"
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

COPY_UNREADABLE_REFRESH = "the saved copy couldn't be read - refresh to fetch it again"
COPY_UNREADABLE_REMOVE = ("the saved copy couldn't be read - remove this marketplace and "
                          "add it again")
NOT_REFRESHABLE = "this catalog has no location to refresh from"
NOT_EDITABLE = "only a catalog on this machine can be edited"
FOLDER_TAKEN = "that folder already holds a marketplace catalog - add it instead"
ALREADY_ADDED = "that marketplace is already added"
NO_COPY_TO_KEEP = "there's no saved copy to keep - refresh first"
AUTO_NEEDS_LOCATION = "auto refresh needs a location"
BAD_LOCATION = "give an https link or an absolute path"
NO_EXPORT_FOLDER = "say where to export the automations you added"


class MarketplaceError(ValueError):
    """Every §22 validation, resolution, and fetch failure - the §19 routes
    answer it as a 422 carrying the message."""


class MarketplaceDuplicate(MarketplaceError):
    """§22.2: a location already in the table - the §19 route answers 409."""


class MarketplaceNotRefreshable(MarketplaceError):
    """§22.2: a refresh of a catalog with no location - the §19 route answers
    409 with the row untouched."""


class MarketplaceNotEditable(MarketplaceError):
    """§22.7: the catalog editor on a link location - the file isn't on this
    machine; the §19 route answers 409."""


# ---------- locations (§22.2) ----------
def kind_of(location: str | None) -> str:
    """§22.2: `kind` is derived from the location, never stored."""
    if location is None:
        return "none"
    return "url" if location.lower().startswith("https://") else "file"


def normalize_location(value: str | None) -> str | None:
    """§22.2/§22.4: a location as the user gave it - blank is `null`, an https
    link is kept as pasted (stripped), a path has `~` expanded and must be
    absolute; anything else is BAD_LOCATION."""
    text = (value or "").strip()
    if not text:
        return None
    if text.lower().startswith("https://"):
        return text
    if "://" in text:
        raise MarketplaceError(BAD_LOCATION)
    expanded = os.path.expanduser(text)
    if not Path(expanded).is_absolute():
        raise MarketplaceError(BAD_LOCATION)
    return str(Path(expanded).resolve())


def default_name(location: str | None) -> str:
    """§22.1: the catalog's title when it names none - the file's stem (`shelf`
    for `shelf.yaml`), or the last path segment's stem for a link, except for
    the canonical `marketplace-catalog.yaml`, whose stem would name every
    unnamed catalog alike: then a path takes its folder's name and a link its
    host name. A catalog kept by the app is "My catalog"."""
    if location is None:
        return KEPT_NAME
    if kind_of(location) == "url":
        split = urllib.parse.urlsplit(location)
        stem = Path(split.path).stem
        if stem == CANONICAL_STEM:
            stem = split.hostname or ""
    else:
        path = Path(location)
        stem = path.stem
        if stem == CANONICAL_STEM:
            stem = path.parent.name
    return stem or "marketplace"


# ---------- catalog (§22.1) ----------
def _extension(reference: str) -> str:
    """A reference's lowercased file extension, with any query string dropped
    first (§22.1) - `manga.autowright?raw=1` is an archive reference."""
    return Path(reference.split("?", 1)[0]).suffix.lower()


def _text(raw: dict, key: str, where: str, limit: int | None = None) -> str:
    """§22.1: strings are stripped; an over-long string is rejected, never
    truncated. `limit` is None for the reference keys, which the spec caps only
    through the catalog's own 1 MB ceiling."""
    value = raw.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise MarketplaceError(f"{where}`{key}` must be text")
    value = value.strip()
    if limit is not None and len(value) > limit:
        raise MarketplaceError(f"{where}`{key}` is longer than {limit} characters")
    return value


def is_reference(value: str) -> bool:
    """§22.1: a reference is an https URL or an absolute local path - nothing
    relative, no other scheme, no `~`."""
    return value.lower().startswith("https://") or (
        "://" not in value and Path(value).is_absolute())


def parse_catalog(text: str, *, location: str | None = None) -> dict:
    """§22.1: parse and validate one marketplace catalog. Unknown keys at any
    level are ignored so the format can grow inside one version (the `url` key
    an older draft carried is one of them). References are checked for form
    here and used as written everywhere else. Errors name the entry index.
    `location` only feeds the default name."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise MarketplaceError(f"the catalog file isn't valid YAML - {e}") from None
    if not isinstance(raw, dict):
        raise MarketplaceError("the catalog file doesn't hold a YAML mapping")
    if raw.get("format_version") != FORMAT_VERSION:
        # §22.1: the only hard gate, exactly like the §5.1 archive.
        raise MarketplaceError(
            f"this marketplace catalog is format {raw.get('format_version')!r}; "
            f"this version of Autowright reads format {FORMAT_VERSION}")
    name = _text(raw, "name", "", MAX_NAME) or default_name(location)
    description = _text(raw, "description", "", MAX_DESCRIPTION)
    listed = raw.get("entries")
    if listed is None:
        raise MarketplaceError("the catalog file has no `entries` list")
    if not isinstance(listed, list):
        raise MarketplaceError("`entries` must be a list")
    if len(listed) > MAX_ENTRIES:
        raise MarketplaceError(
            f"the catalog file lists more than {MAX_ENTRIES} automations")
    entries = []
    for index, item in enumerate(listed):
        where = f"entry {index}: "
        if not isinstance(item, dict):
            raise MarketplaceError(f"{where}it isn't a mapping")
        title = _text(item, "title", where, MAX_TITLE)
        if not title:
            raise MarketplaceError(f"{where}it has no title")
        archive = _text(item, "path", where)
        if not archive:
            raise MarketplaceError(f"{where}it has no `path`")
        if _extension(archive) != ARCHIVE_EXTENSION:
            raise MarketplaceError(f"{where}`path` must name an {ARCHIVE_EXTENSION} file")
        if not is_reference(archive):
            raise MarketplaceError(
                f"{where}`path` must be an https link or an absolute path")
        image = _text(item, "image", where)
        if image and _extension(image) not in IMAGE_EXTENSIONS:
            raise MarketplaceError(
                f"{where}`image` must name a {', '.join(IMAGE_EXTENSIONS)} file")
        if image and not is_reference(image):
            raise MarketplaceError(
                f"{where}`image` must be an https link or an absolute path")
        entries.append({"index": index, "title": title,
                        "description": _text(item, "description", where,
                                             MAX_ENTRY_DESCRIPTION),
                        "path": archive, "image": image})
    return {"name": name, "description": description, "entries": entries}


def dump_catalog(name: str, description: str, entries: list[dict]) -> str:
    """§22.7: the catalog text the editor writes - exactly the §22.1 keys in the
    §22.1 order, optional ones only when non-empty. Unknown keys and comments
    from the previous file don't survive: the editor owns the file."""
    doc: dict = {"format_version": FORMAT_VERSION, "name": name}
    if description:
        doc["description"] = description
    listed = []
    for entry in entries:
        item: dict = {"title": entry["title"]}
        if entry.get("description"):
            item["description"] = entry["description"]
        item["path"] = entry["path"]
        if entry.get("image"):
            item["image"] = entry["image"]
        listed.append(item)
    doc["entries"] = listed
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)


# ---------- fetching (§22.1 caps and headers) ----------
def _fetch_url(url: str, *, cap: int, deadline_s: int = FETCH_DEADLINE_S) -> bytes:
    """§22.1 download: the §5.2 headers and per-read timeout, a whole-download
    deadline, https only with redirects re-checked, and a hard byte cap."""
    request = urllib.request.Request(url, headers=transfer._headers())
    try:
        with urllib.request.urlopen(request, timeout=transfer.FETCH_TIMEOUT) as response:
            # urllib follows redirects - a hop off https would sidestep the
            # §22.1 HTTPS-only rule, so re-check the landing URL.
            if urllib.parse.urlsplit(response.geturl()).scheme != "https":
                raise MarketplaceError("the download redirected off https")
            # The per-read timeout can't catch a server trickling bytes forever;
            # only a whole-download deadline can, and this runs on a threadpool
            # worker the backend needs back.
            deadline = time.monotonic() + deadline_s
            chunks, total = [], 0
            while chunk := response.read(_FETCH_CHUNK):
                if time.monotonic() > deadline:
                    raise MarketplaceError(
                        f"the download timed out after {deadline_s} seconds")
                total += len(chunk)
                if total > cap:
                    raise MarketplaceError(
                        f"the download is larger than the {cap // (1024 * 1024)} MB limit")
                chunks.append(chunk)
    except urllib.error.HTTPError as e:
        raise MarketplaceError(f"download failed - the server answered {e.code}") from None
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as e:
        # HTTPException covers a truncated chunked download (IncompleteRead),
        # which is not an OSError.
        raise MarketplaceError(f"download failed - {getattr(e, 'reason', e)}") from None
    return b"".join(chunks)


def _read_reference(reference: str, *, cap: int, what: str) -> bytes:
    """§22.1: the bytes behind a reference - downloaded for an https link, read
    from disk for a path, both under `cap`."""
    if kind_of(reference) == "url":
        return _fetch_url(reference, cap=cap)
    try:
        data = Path(reference).read_bytes()
    except OSError as e:
        raise MarketplaceError(f"couldn't read the {what} - {e.strerror or e}") from None
    if len(data) > cap:
        raise MarketplaceError(
            f"the {what} is larger than the {cap // (1024 * 1024)} MB limit")
    return data


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise MarketplaceError("the catalog file isn't UTF-8 text") from None


# ---------- catalog table (§22.2) ----------
class MarketplaceStore:
    """§22.2: the catalog table, loaded once at backend startup into memory and
    rewritten whole on every change, like agents and secrets. `name`,
    `description`, and `entries` are never duplicated into the table - they
    are derived from the app's copy at every serialization, so there is one
    truth."""

    def __init__(self) -> None:
        # §22.2: one lock for every mutation and refresh - one refresh runs at
        # a time per backend.
        self.lock = threading.Lock()
        self.sources: list[dict] = []
        self._auto_stop = threading.Event()
        self._auto_thread: threading.Thread | None = None

    # ---------- paths ----------
    def file(self) -> Path:
        return paths.marketplace_dir() / "marketplaces.yaml"

    def source_dir(self, source_id: str) -> Path:
        return paths.marketplace_dir() / source_id

    def catalog_file(self, source_id: str) -> Path:
        return self.source_dir(source_id) / CATALOG_FILENAME

    # ---------- load / save (§5) ----------
    def load(self) -> None:
        """§5 lenient load: `marketplaces.yaml` is hand-editable and must never
        raise at startup - a row missing `id`, or whose `location` is neither
        null, an absolute path, nor an https link, skips with a warning; a
        missing `shown` reads true, a missing `auto_refresh` false."""
        raw = load_yaml(self.file(), {}) or {}
        if not isinstance(raw, dict):
            log.warning("%s doesn't hold a mapping - loading no marketplaces", self.file())
            raw = {}
        listed = raw.get("sources") or []
        if not isinstance(listed, list):
            log.warning("%s: `sources` isn't a list - loading no marketplaces", self.file())
            listed = []
        sources = []
        for entry in listed:
            if not isinstance(entry, dict):
                log.warning("skipping a marketplaces.yaml row that isn't a mapping")
                continue
            if not entry.get("id"):
                log.warning("skipping a marketplaces.yaml row with no id (%r)",
                            entry.get("location"))
                continue
            location = entry.get("location")
            if location is not None:
                if not isinstance(location, str) or not is_reference(location):
                    log.warning("skipping marketplaces.yaml row %r - %r isn't a location",
                                entry["id"], location)
                    continue
            sources.append({"id": str(entry["id"]), "location": location,
                            "shown": entry.get("shown", True) is not False,
                            "auto_refresh": entry.get("auto_refresh") is True
                                            and location is not None,
                            "added_at": entry.get("added_at") or "",
                            "refreshed_at": entry.get("refreshed_at") or None,
                            "error": entry.get("error") or None})
        with self.lock:
            self.sources = sources

    def _save(self) -> None:
        """§22.2: the whole file, rewritten on every change. Caller holds the
        lock."""
        save_yaml(self.file(), {"sources": self.sources})

    def _find(self, source_id: str) -> dict:
        for source in self.sources:
            if source["id"] == source_id:
                return source
        raise KeyError(source_id)

    def _taken(self, location: str, *, except_id: str | None = None) -> bool:
        """§22.2: the 409 duplicate rule - exact string equality (a path is
        already resolved by `normalize_location`). Caller holds the lock."""
        return any(s["location"] == location and s["id"] != except_id for s in self.sources)

    # ---------- add / refresh / settings / remove ----------
    def add(self, *, url: str | None = None, path: str | None = None) -> dict:
        """§22.2 add: a link or a file path. It reads and validates first, so
        any failure raises and stores nothing."""
        if url and path:
            raise MarketplaceError("give a link or a file path, not both")
        given = (url or path or "").strip()
        if not given:
            raise MarketplaceError("give a link or a file path")
        if url:
            if not given.lower().startswith("https://"):
                raise MarketplaceError("only https:// links can be added")
            location = given
        else:
            if "://" in given:
                raise MarketplaceError("give the catalog file's absolute path")
            expanded = os.path.expanduser(given)
            if not Path(expanded).is_absolute():
                raise MarketplaceError("give the catalog file's absolute path")
            candidate = Path(expanded).resolve()
            if not candidate.is_file():
                raise MarketplaceError("there's no catalog file at that path")
            location = str(candidate)
        with self.lock:
            if self._taken(location):
                raise MarketplaceDuplicate(ALREADY_ADDED)
            source = {"id": new_id(), "location": location, "shown": True,
                      "auto_refresh": False, "added_at": timefmt.now_iso(),
                      "refreshed_at": None, "error": None}
            try:
                self._refresh_copy(source)
            except MarketplaceError:
                # Nothing is stored, so nothing may be left on disk either.
                shutil.rmtree(self.source_dir(source["id"]), ignore_errors=True)
                raise
            self.sources.append(source)
            self._save()
            return self.serialize(source)

    def refresh(self, source_id: str) -> dict:
        """§22.2 refresh: re-read the location. A `null` location raises
        MarketplaceNotRefreshable with the row untouched. On failure nothing in
        the copy changes, `refreshed_at` keeps its old value, and `error`
        records the message - the page keeps showing the last good copy with
        the error beside it."""
        with self.lock:
            source = self._find(source_id)
            if source["location"] is None:
                raise MarketplaceNotRefreshable(NOT_REFRESHABLE)
            self._try_refresh(source)
            self._save()
            return self.serialize(source)

    def refresh_all(self) -> list[dict]:
        """§22.2: every catalog with a location, in table order; the others are
        listed as they were."""
        with self.lock:
            for source in self.sources:
                if source["location"] is not None:
                    self._try_refresh(source)
            self._save()
            return [self.serialize(s) for s in self.sources]

    def auto_refresh_sweep(self) -> list[str]:
        """§22.2 auto refresh: every row flagged `auto_refresh` with a location,
        in table order, through the ordinary refresh. Answers the ids it
        refreshed (a failure lands in `error` like a manual one)."""
        with self.lock:
            swept = []
            for source in self.sources:
                if source["auto_refresh"] and source["location"] is not None:
                    self._try_refresh(source)
                    swept.append(source["id"])
            if swept:
                self._save()
            return swept

    def start_auto_refresh(self, on_change) -> None:
        """§22.2: the sweep 30 s after the store loads and every 6 hours after
        that, on a daemon thread off the boot path. `on_change` runs after a
        sweep that touched any row (the §19 `marketplace.changed` event)."""
        if self._auto_thread is not None:
            return
        self._auto_stop.clear()

        def run() -> None:
            wait = AUTO_REFRESH_DELAY_S
            while not self._auto_stop.wait(wait):
                try:
                    if self.auto_refresh_sweep():
                        on_change()
                except Exception:  # noqa: BLE001 - a sweep must never kill the thread
                    log.exception("marketplace auto refresh failed")
                wait = AUTO_REFRESH_INTERVAL_S

        self._auto_thread = threading.Thread(target=run, name="marketplace-auto-refresh",
                                             daemon=True)
        self._auto_thread.start()

    def stop_auto_refresh(self) -> None:
        self._auto_stop.set()
        self._auto_thread = None

    def update_settings(self, source_id: str, *, location: str | None = ...,
                        shown: bool | None = None, auto_refresh: bool | None = None) -> dict:
        """§22.2 settings: only the given fields change; nothing is fetched.
        `location` is the user's text (blank = null) or the `...` sentinel for
        "not given"."""
        with self.lock:
            source = self._find(source_id)
            # Every check first, then every write: a refused patch changes
            # nothing at all.
            new_location = source["location"]
            if location is not ...:
                new_location = normalize_location(location)
                if new_location is not None and self._taken(new_location, except_id=source_id):
                    raise MarketplaceDuplicate(ALREADY_ADDED)
                if new_location is None and self._cached(source) is None:
                    raise MarketplaceError(NO_COPY_TO_KEEP)
            new_auto = source["auto_refresh"] if auto_refresh is None else bool(auto_refresh)
            if new_location is None:
                if auto_refresh:
                    raise MarketplaceError(AUTO_NEEDS_LOCATION)
                new_auto = False
            if location is not ... and new_location != source["location"]:
                source["location"] = new_location
                source["error"] = None
            source["auto_refresh"] = new_auto
            if shown is not None:
                source["shown"] = bool(shown)
            self._save()
            return self.serialize(source)

    def remove(self, source_id: str) -> None:
        """§22.2 remove: the row and its directory (the copy, nothing more).
        Installed automations and referenced archive files are untouched."""
        with self.lock:
            source = self._find(source_id)
            self.sources.remove(source)
            self._save()
        # §6: no rmtree ever runs under a store lock.
        shutil.rmtree(self.source_dir(source_id), ignore_errors=True)

    def _try_refresh(self, source: dict) -> None:
        """One §22.2 refresh with any failure recorded as the row's `error`.
        Caller holds the lock."""
        try:
            self._refresh_copy(source)
        except MarketplaceError as e:
            source["error"] = str(e)

    def _refresh_copy(self, source: dict) -> None:
        """The §22.2 fetch-and-swap: read the location, validate, then rename
        the new copy into place and stamp the row. Raises MarketplaceError
        with the copy and the row untouched when anything fails before the
        swap. Caller holds the lock."""
        location = source["location"]
        text = _decode(_read_reference(location, cap=MAX_CATALOG_BYTES, what="catalog file"))
        parse_catalog(text, location=location)
        self._write_copy(source, text)
        source["refreshed_at"] = timefmt.now_iso()
        source["error"] = None

    def _write_copy(self, source: dict, text: str) -> None:
        """§22.2: temp file, then renamed into place (atomic_write_text), so a
        crash mid-write never leaves half a catalog as the copy."""
        self.source_dir(source["id"]).mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.catalog_file(source["id"]), text)

    # ---------- authoring (§22.7) ----------
    def create_catalog(self, folder: str | None, body: dict | None = None,
                       export=None) -> dict:
        """§22.7 create: the editor's content (or an empty catalog) written to
        a folder the user chose - the row's location is that file - or, with
        no folder, straight to a new row's copy with a `null` location. A
        folder that already holds a catalog is refused before anything is
        written."""
        body = body or {}
        target: Path | None = None
        if (folder or "").strip():
            target = Path(os.path.expanduser(folder.strip()))
            if not target.is_absolute():
                raise MarketplaceError("give the folder's absolute path")
            target = target.resolve()
            if not target.is_dir():
                raise MarketplaceError("there's no folder at that path")
            if (target / CATALOG_FILENAME).exists():
                raise MarketplaceDuplicate(FOLDER_TAKEN)
        location = str(target / CATALOG_FILENAME) if target else None
        with self.lock:
            if location is not None and self._taken(location):
                raise MarketplaceDuplicate(ALREADY_ADDED)
            source = {"id": new_id(), "location": location, "shown": True,
                      "auto_refresh": False, "added_at": timefmt.now_iso(),
                      "refreshed_at": None, "error": None}
            text, writes = self._prepare_catalog(source, body, export)
            try:
                self._commit_catalog(source, text, writes)
            except MarketplaceError:
                shutil.rmtree(self.source_dir(source["id"]), ignore_errors=True)
                raise
            self.sources.append(source)
            self._save()
            return self.serialize(source)

    def _editable(self, source_id: str) -> dict:
        """The row the editor may touch: a path or `null` location, whose
        catalog is on this machine. Caller holds the lock."""
        source = self._find(source_id)
        if kind_of(source["location"]) == "url":
            raise MarketplaceNotEditable(NOT_EDITABLE)
        return source

    def _editor_file(self, source: dict) -> Path:
        """§22.7: what the editor reads and writes - the file at a path
        location, the row's copy for `null`."""
        return Path(source["location"]) if source["location"] else self.catalog_file(source["id"])

    def read_catalog(self, source_id: str) -> dict:
        """§22.7 GET: the catalog as the editor should see it, references
        unresolved."""
        with self.lock:
            source = self._editable(source_id)
        text = _decode(_read_reference(str(self._editor_file(source)), cap=MAX_CATALOG_BYTES,
                                       what="catalog file"))
        catalog = parse_catalog(text, location=source["location"])
        return {"name": catalog["name"], "description": catalog["description"],
                "entries": [{"index": e["index"], "title": e["title"],
                             "description": e["description"], "path": e["path"],
                             "image": e["image"]} for e in catalog["entries"]]}

    def save_catalog(self, source_id: str, body: dict, export) -> dict:
        """§22.7 save: the content prepared and written over the catalog the
        editor sees, then the copy brought up to date."""
        with self.lock:
            source = self._editable(source_id)
            text, writes = self._prepare_catalog(source, body, export)
            self._commit_catalog(source, text, writes)
            self._save()
            return self.serialize(source)

    def _prepare_catalog(self, source: dict, body: dict, export) -> tuple[str, list]:
        """§22.7 steps 1-4. `export(automation_id)` answers `(name, bytes)` for
        an automation in this app - the §5.1 export without parameter values -
        or raises KeyError / transfer.TransferError. An export lands in the
        export folder (beside the catalog for a path location, else the body's
        `exportFolder`) and is listed by its absolute path; an `archiveFile` is
        validated and listed where it is. Everything is checked before
        anything is written. Answers the text plus the archive files to
        write. Caller holds the lock."""
        location = source["location"]
        # 1. the top-level fields, and the export folder when it will be needed
        top = {"name": body.get("name") or "", "description": body.get("description") or ""}
        name = _text(top, "name", "", MAX_NAME) or default_name(location)
        description = _text(top, "description", "", MAX_DESCRIPTION)
        raw_entries = body.get("entries") or []
        needs_folder = any(e.get("automationId") for e in raw_entries)
        folder: Path | None = None
        if needs_folder:
            if location is not None:
                folder = Path(location).parent
            else:
                given = (body.get("exportFolder") or "").strip()
                if not given:
                    raise MarketplaceError(NO_EXPORT_FOLDER)
                folder = Path(os.path.expanduser(given))
                if not folder.is_absolute() or not folder.is_dir():
                    raise MarketplaceError(
                        "the export folder must be an existing folder's absolute path")
                folder = folder.resolve()
        # 2. every entry: the one-of rule, then the bytes it brings
        planned: list[dict] = []
        pending: list[tuple[str, bytes]] = []
        for index, raw in enumerate(raw_entries):
            where = f"entry {index}: "
            item = {"title": raw.get("title") or "",
                    "description": raw.get("description") or "",
                    "image": raw.get("image") or ""}
            title = _text(item, "title", where, MAX_TITLE)
            if not title:
                raise MarketplaceError(f"{where}it has no title")
            entry_description = _text(item, "description", where, MAX_ENTRY_DESCRIPTION)
            image = _text(item, "image", where)
            given = [k for k in ("path", "automationId", "archiveFile") if raw.get(k)]
            if len(given) != 1:
                raise MarketplaceError(
                    f"{where}give one of path, automationId, or archiveFile")
            entry = {"title": title, "description": entry_description, "image": image}
            if given[0] == "path":
                entry["path"] = str(raw["path"]).strip()
            elif given[0] == "automationId":
                try:
                    auto_name, data = export(str(raw["automationId"]))
                except KeyError:
                    raise MarketplaceError(f"{where}no automation has that id") from None
                except transfer.TransferError as e:
                    raise MarketplaceError(f"{where}{e}") from None
                entry["path"] = None
                pending.append((transfer.safe_filename(auto_name), data))
            else:
                archive = Path(str(raw["archiveFile"]).strip())
                if not archive.is_absolute() or not archive.is_file():
                    raise MarketplaceError(f"{where}there's no file at {archive}")
                if archive.suffix.lower() != ARCHIVE_EXTENSION:
                    raise MarketplaceError(
                        f"{where}{archive.name} isn't an {ARCHIVE_EXTENSION} file")
                try:
                    data = archive.read_bytes()
                except OSError as e:
                    raise MarketplaceError(
                        f"{where}couldn't read {archive.name} - {e.strerror or e}") from None
                if len(data) > transfer.MAX_ARCHIVE_BYTES:
                    raise MarketplaceError(
                        f"{where}{archive.name} is larger than the "
                        f"{transfer.MAX_ARCHIVE_BYTES // (1024 * 1024)} MB limit")
                try:
                    transfer.validate_archive(data)
                except transfer.TransferError as e:
                    raise MarketplaceError(f"{where}{e}") from None
                # §22.7: listed where it is, never copied.
                entry["path"] = str(archive)
            planned.append(entry)
        # 3. a free file name in the export folder for every export - never
        #    overwrite; the entry lists the file by its absolute path
        taken: set[str] = set()
        writes: list[tuple[Path, bytes]] = []
        new = iter(pending)
        for entry in planned:
            if entry["path"] is not None:
                continue
            base, data = next(new)
            candidate = f"{base}{ARCHIVE_EXTENSION}"
            n = 2
            while candidate.lower() in taken or (folder / candidate).exists():
                candidate = f"{base} {n}{ARCHIVE_EXTENSION}"
                n += 1
            taken.add(candidate.lower())
            entry["path"] = str(folder / candidate)
            writes.append((folder / candidate, data))
        # 4. the text, through the same parser a fetch runs
        text = dump_catalog(name, description, planned)
        parse_catalog(text, location=location)
        return text, writes

    def _commit_catalog(self, source: dict, text: str, writes: list) -> None:
        """§22.7 steps 5-6: the exports, then the catalog atomically - to the
        location for a path (and the copy refreshed from it), to the row's copy
        for `null`. Caller holds the lock."""
        try:
            for target, data in writes:
                target.write_bytes(data)
            if source["location"] is not None:
                atomic_write_text(Path(source["location"]), text)
        except OSError as e:
            raise MarketplaceError(
                f"couldn't write into the catalog's folder - {e.strerror or e}") from None
        if source["location"] is not None:
            self._refresh_copy(source)
        else:
            self._write_copy(source, text)
            source["error"] = None

    # ---------- serialization (§22.4) ----------
    def _cached(self, source: dict) -> dict | None:
        """The app's copy, or None when it is missing or no longer valid (§22.2
        - the row still lists, with zero entries, rather than vanishing).
        `archive` is the entry's `path` as written."""
        try:
            text = _decode(self.catalog_file(source["id"]).read_bytes())
            catalog = parse_catalog(text, location=source["location"])
        except (MarketplaceError, OSError):
            return None
        for entry in catalog["entries"]:
            entry["archive"] = entry["path"]
        return catalog

    def serialize(self, source: dict) -> dict:
        """§22.4 `Source`. The entries are derived from the copy every time (a
        small file, parsed on demand), never duplicated into the table."""
        catalog = self._cached(source)
        error = source.get("error")
        if catalog is None and not error:
            # A real stored refresh error wins: it says what actually happened.
            error = (COPY_UNREADABLE_REFRESH if source["location"] is not None
                     else COPY_UNREADABLE_REMOVE)
        entries = [{"index": e["index"], "title": e["title"],
                    "description": e["description"], "archive": e["archive"],
                    "image": bool(e["image"])}
                   for e in (catalog["entries"] if catalog else [])]
        return {"id": source["id"], "kind": kind_of(source["location"]),
                "location": source["location"], "shown": source["shown"],
                "autoRefresh": source["auto_refresh"],
                "name": catalog["name"] if catalog else default_name(source["location"]),
                "description": catalog["description"] if catalog else "",
                "addedAt": source.get("added_at") or "",
                "refreshedAt": source.get("refreshed_at"),
                "error": error, "cached": catalog is not None, "entries": entries}

    # ---------- images and install (§22.4) ----------
    def _entry(self, source_id: str, index: int) -> dict:
        """One entry of a row's copy; KeyError for an unknown row or index,
        MarketplaceError for an unreadable copy."""
        with self.lock:
            source = self._find(source_id)
            catalog = self._cached(source)
        if catalog is None:
            raise MarketplaceError(COPY_UNREADABLE_REFRESH)
        entry = next((e for e in catalog["entries"] if e["index"] == index), None)
        if entry is None:
            raise KeyError(index)
        return entry

    def image_bytes(self, source_id: str, index: int) -> tuple[bytes, str] | None:
        """§22.4 image route: the entry's image read on demand by reference
        (downloaded for a link, never stored; read for a path), plus its
        extension. None when the entry lists no image; MarketplaceError when
        the reference can't be read."""
        entry = self._entry(source_id, index)
        if not entry["image"]:
            return None
        return (_read_reference(entry["image"], cap=MAX_IMAGE_BYTES, what="image"),
                _extension(entry["image"]))

    def entry_archive(self, source_id: str, index: int) -> tuple[bytes, str]:
        """§22.4 install: the entry's archive bytes plus its reference. An
        https reference goes through the ordinary §5.2 fetch (its
        TransferError is the caller's 422 too); a local path is read and
        capped here, and the §5.1 validation of the bytes is the caller's."""
        reference = self._entry(source_id, index)["archive"]
        if kind_of(reference) == "url":
            data, _resolved = transfer.fetch_archive(reference)
            return data, reference
        return (_read_reference(reference, cap=transfer.MAX_ARCHIVE_BYTES, what="archive"),
                reference)
