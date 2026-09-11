"""§22 marketplace: the catalog format (§22.1) and the sources store (§22.2).

A marketplace is one hand-written YAML file listing §5.1 `.autowright` archives.
This module parses and validates it, resolves its references (an https URL for a
link source, a path that must stay inside the catalog's folder for a file
source), keeps the last successfully fetched copy plus its preview images under
the §5 data root, and hands the §19 install route the archive bytes. Nothing
here is ever executed: a catalog is data, and an archive lands only through the
§5.1/§5.2 import.
"""
from __future__ import annotations

import http.client
import logging
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
KINDS = ("url", "file")

# §22.1/§22.2: the catalog's canonical file name - what the cache is saved as,
# and the stem whose default name would collide across every unnamed catalog.
CATALOG_FILENAME = "marketplace-catalog.yaml"
CANONICAL_STEM = Path(CATALOG_FILENAME).stem

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

ARCHIVE_EXTENSION = ".autowright"
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")

CACHE_UNREADABLE = "the saved copy couldn't be read - refresh to fetch it again"


class MarketplaceError(ValueError):
    """Every §22 validation, resolution, and fetch failure - the §19 routes
    answer it as a 422 carrying the message."""


class MarketplaceDuplicate(MarketplaceError):
    """§22.2: the same origin added twice - the §19 route answers 409."""


# ---------- catalog (§22.1) ----------
def default_name(kind: str, origin: str) -> str:
    """§22.1: the source's title when the catalog names none - the catalog
    file's stem (`shelf` for `shelf.yaml`), or the last path segment's stem for
    a link. The canonical `marketplace-catalog.yaml` is the exception: its stem
    would name every unnamed catalog alike, so a file source takes its folder's
    name and a link source its host name."""
    if kind == "url":
        split = urllib.parse.urlsplit(origin)
        stem = Path(split.path).stem
        if stem == CANONICAL_STEM:
            stem = split.hostname or ""
    else:
        path = Path(origin)
        stem = path.stem
        if stem == CANONICAL_STEM:
            stem = path.parent.name
    return stem or "marketplace"


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


def parse_catalog(text: str, *, kind: str, origin: str) -> dict:
    """§22.1: parse and validate one marketplace catalog. Unknown keys at any
    level are ignored so the format can grow inside one version. References are
    checked for *form* here; `resolve_reference` turns them into a URL or a
    path. Errors name the entry index."""
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
    name = _text(raw, "name", "", MAX_NAME) or default_name(kind, origin)
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
        image = _text(item, "image", where)
        if image and _extension(image) not in IMAGE_EXTENSIONS:
            raise MarketplaceError(
                f"{where}`image` must name a {', '.join(IMAGE_EXTENSIONS)} file")
        entries.append({"index": index, "title": title,
                        "description": _text(item, "description", where,
                                             MAX_ENTRY_DESCRIPTION),
                        "path": archive, "image": image})
    return {"name": name, "description": description, "entries": entries}


def resolve_reference(reference: str, *, kind: str, origin: str) -> str:
    """§22.1 reference resolution: the https URL, or the absolute path a file
    source's relative reference names.

    A link source joins with RFC 3986 `urljoin` against the catalog's URL and
    must land on https. A file source resolves against the catalog's directory
    and the result (symlinks followed) must stay inside it, so a catalog can
    never point the app at an arbitrary file on the machine; an absolute https
    reference is fine there too (mixed catalogs are legal)."""
    if kind == "url":
        resolved = urllib.parse.urljoin(origin, reference)
        if urllib.parse.urlsplit(resolved).scheme != "https":
            raise MarketplaceError(f"{reference} doesn't resolve to an https reference")
        return resolved
    if reference.lower().startswith("https://"):
        return reference
    if "://" in reference:
        raise MarketplaceError(
            f"{reference} is neither an https reference nor a relative path")
    if Path(reference).is_absolute():
        raise MarketplaceError(
            f"{reference} is an absolute path - a reference must stay inside the "
            "catalog's folder")
    folder = Path(origin).parent.resolve()
    resolved = (Path(origin).parent / reference).resolve()
    if not resolved.is_relative_to(folder):
        raise MarketplaceError(
            f"{reference} points outside the catalog's folder")
    return str(resolved)


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


def _read_origin(kind: str, origin: str) -> bytes:
    """§22.2 refresh: the catalog bytes - downloaded for a link source, read
    from disk for a file source, both under the §22.1 1 MB cap."""
    if kind == "url":
        return _fetch_url(origin, cap=MAX_CATALOG_BYTES)
    try:
        data = Path(origin).read_bytes()
    except OSError as e:
        raise MarketplaceError(
            f"couldn't read the catalog file - {e.strerror or e}") from None
    if len(data) > MAX_CATALOG_BYTES:
        raise MarketplaceError(
            f"the catalog file is larger than the "
            f"{MAX_CATALOG_BYTES // (1024 * 1024)} MB limit")
    return data


def _decode(data: bytes) -> str:
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise MarketplaceError("the catalog file isn't UTF-8 text") from None


def _read_image_file(path: str) -> bytes:
    """A file source's relative image, copied into the cache under the §22.1
    5 MB cap. OSError is the caller's to log and skip."""
    data = Path(path).read_bytes()
    if len(data) > MAX_IMAGE_BYTES:
        raise MarketplaceError(
            f"the image is larger than the {MAX_IMAGE_BYTES // (1024 * 1024)} MB limit")
    return data


# ---------- sources store (§22.2) ----------
class MarketplaceStore:
    """§22.2: the marketplaces the user added, loaded once at backend startup
    into memory and rewritten whole on every change, like agents and secrets.
    `name`, `description`, and `entries` are never duplicated into
    `sources.yaml` - they are derived from the cached catalog at every
    serialization, so there is one truth."""

    def __init__(self) -> None:
        # §22.2: one lock for every mutation and refresh - one refresh runs at
        # a time per backend.
        self.lock = threading.Lock()
        self.sources: list[dict] = []

    # ---------- paths ----------
    def file(self) -> Path:
        return paths.marketplace_dir() / "sources.yaml"

    def source_dir(self, source_id: str) -> Path:
        return paths.marketplace_dir() / source_id

    def catalog_file(self, source_id: str) -> Path:
        return self.source_dir(source_id) / CATALOG_FILENAME

    def images_dir(self, source_id: str) -> Path:
        return self.source_dir(source_id) / "images"

    # ---------- load / save (§5) ----------
    def load(self) -> None:
        """§5 lenient load: `sources.yaml` is hand-editable and must never
        raise at startup - an entry missing `id`, `kind`, or `origin`, or
        carrying an unknown kind, skips with a warning."""
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
                log.warning("skipping a sources.yaml entry that isn't a mapping")
                continue
            missing = next((k for k in ("id", "kind", "origin") if not entry.get(k)), None)
            if missing:
                log.warning("skipping sources.yaml entry %r - it has no %s",
                            entry.get("id") or entry.get("origin"), missing)
                continue
            if entry["kind"] not in KINDS:
                log.warning("skipping sources.yaml entry %r - %r isn't a marketplace kind",
                            entry["id"], entry["kind"])
                continue
            sources.append({"id": str(entry["id"]), "kind": str(entry["kind"]),
                            "origin": str(entry["origin"]),
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

    # ---------- add / refresh / remove ----------
    def add(self, *, url: str | None = None, path: str | None = None) -> dict:
        """§22.2 add: exactly one of a link or a file path. Add is a refresh
        that creates the record - it fetches and validates first, so any
        failure raises and stores nothing."""
        if url and path:
            raise MarketplaceError("give a link or a file path, not both")
        origin = (url or path or "").strip()
        if not origin:
            raise MarketplaceError("give a link or a file path")
        if url:
            kind = "url"
            if not origin.lower().startswith("https://"):
                raise MarketplaceError("only https:// links can be added")
        else:
            kind = "file"
            candidate = Path(origin)
            if not candidate.is_absolute():
                raise MarketplaceError("give the catalog file's absolute path")
            # §22.2: a file origin compares by resolved path, so the same file
            # reached through a symlink or a `..` hop can't be added twice.
            candidate = candidate.resolve()
            if not candidate.is_file():
                raise MarketplaceError("there's no catalog file at that path")
            origin = str(candidate)
        with self.lock:
            if any(s["origin"] == origin for s in self.sources):
                raise MarketplaceDuplicate("that marketplace is already added")
            source = {"id": new_id(), "kind": kind, "origin": origin,
                      "added_at": timefmt.now_iso(), "refreshed_at": None, "error": None}
            try:
                self._refresh_cache(source)
            except MarketplaceError:
                # Nothing is stored, so nothing may be left on disk either.
                shutil.rmtree(self.source_dir(source["id"]), ignore_errors=True)
                raise
            self.sources.append(source)
            self._save()
            return self.serialize(source)

    def refresh(self, source_id: str) -> dict:
        """§22.2 refresh: re-read the origin. On failure nothing in the cache
        changes, `refreshed_at` keeps its old value, and `error` records the
        message - the page keeps showing the last good copy with the error
        beside it, so a refresh-all never stops at the first bad source."""
        with self.lock:
            source = self._find(source_id)
            try:
                self._refresh_cache(source)
            except MarketplaceError as e:
                source["error"] = str(e)
            self._save()
            return self.serialize(source)

    def refresh_all(self) -> list[dict]:
        """§22.2: every source, in listing order."""
        with self.lock:
            for source in self.sources:
                try:
                    self._refresh_cache(source)
                except MarketplaceError as e:
                    source["error"] = str(e)
            self._save()
            return [self.serialize(s) for s in self.sources]

    def remove(self, source_id: str) -> None:
        """§22.2 remove: the record and its directory. Automations installed
        from it are ordinary automations and are untouched."""
        with self.lock:
            source = self._find(source_id)
            self.sources.remove(source)
            self._save()
        # §6: no rmtree ever runs under a store lock.
        shutil.rmtree(self.source_dir(source_id), ignore_errors=True)

    def _refresh_cache(self, source: dict) -> None:
        """The §22.2 refresh body: read the origin, validate it, resolve every
        reference, then swap the catalog and the images into place. Raises
        MarketplaceError with the cache untouched when anything fails before
        the swap. Caller holds the lock."""
        kind, origin = source["kind"], source["origin"]
        text = _decode(_read_origin(kind, origin))
        catalog = parse_catalog(text, kind=kind, origin=origin)
        # §22.1: validation covers the form of every reference at add and
        # refresh time, so a resolution failure is a refresh failure naming
        # the entry. Whether the archive exists is checked at install.
        images: list[tuple[int, str]] = []
        for entry in catalog["entries"]:
            try:
                resolve_reference(entry["path"], kind=kind, origin=origin)
                if entry["image"]:
                    images.append((entry["index"],
                                   resolve_reference(entry["image"], kind=kind,
                                                     origin=origin)))
            except MarketplaceError as e:
                raise MarketplaceError(f"entry {entry['index']}: {e}") from None
        self.source_dir(source["id"]).mkdir(parents=True, exist_ok=True)
        # §22.2: temp file, then renamed into place (atomic_write_text), so a
        # crash mid-write never leaves half a catalog as the cached copy.
        atomic_write_text(self.catalog_file(source["id"]), text)
        self._rebuild_images(source, images)
        source["refreshed_at"] = timefmt.now_iso()
        source["error"] = None

    def _rebuild_images(self, source: dict, images: list[tuple[int, str]]) -> None:
        """§22.2: every image the new catalog lists is fetched fresh into a
        temp directory that then replaces `images/`. §22.1: a missing or
        oversized image simply leaves that entry without a preview (logged,
        never a refresh failure)."""
        current = self.images_dir(source["id"])
        staging = current.with_name("images.tmp")
        previous = current.with_name("images.old")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)
        for index, reference in images:
            try:
                if reference.lower().startswith("https://"):
                    data = _fetch_url(reference, cap=MAX_IMAGE_BYTES)
                else:
                    data = _read_image_file(reference)
                (staging / f"{index}{_extension(reference)}").write_bytes(data)
            except (MarketplaceError, OSError) as e:
                log.warning("no preview image for entry %s of %s (%s)",
                            index, source["origin"], e)
        shutil.rmtree(previous, ignore_errors=True)
        if current.exists():
            current.rename(previous)
        staging.rename(current)
        shutil.rmtree(previous, ignore_errors=True)

    # ---------- serialization (§22.4) ----------
    def _cached(self, source: dict) -> dict | None:
        """The cached catalog with every archive reference resolved, or None
        when the saved copy is missing or no longer valid (§22.2 - the source
        still lists, with zero entries, rather than vanishing)."""
        try:
            text = _decode(self.catalog_file(source["id"]).read_bytes())
            catalog = parse_catalog(text, kind=source["kind"], origin=source["origin"])
            for entry in catalog["entries"]:
                entry["archive"] = resolve_reference(entry["path"], kind=source["kind"],
                                                     origin=source["origin"])
        except (MarketplaceError, OSError):
            return None
        return catalog

    def serialize(self, source: dict) -> dict:
        """§22.4 `Source`. The entries are derived from the cached catalog
        every time (a small file, parsed on demand), never duplicated into
        `sources.yaml`."""
        catalog = self._cached(source)
        error = source.get("error")
        if catalog is None and not error:
            # A real stored refresh error wins: it says what actually happened.
            error = CACHE_UNREADABLE
        entries = [{"index": e["index"], "title": e["title"],
                    "description": e["description"], "archive": e["archive"],
                    "image": self.image_path(source["id"], e["index"]) is not None}
                   for e in (catalog["entries"] if catalog else [])]
        return {"id": source["id"], "kind": source["kind"], "origin": source["origin"],
                "name": catalog["name"] if catalog
                        else default_name(source["kind"], source["origin"]),
                "description": catalog["description"] if catalog else "",
                "addedAt": source.get("added_at") or "",
                "refreshedAt": source.get("refreshed_at"),
                "error": error, "cached": catalog is not None, "entries": entries}

    # ---------- install (§22.4) ----------
    def image_path(self, source_id: str, index: int) -> Path | None:
        """The cached preview image for one entry, or None when it has none."""
        for extension in IMAGE_EXTENSIONS:
            candidate = self.images_dir(source_id) / f"{index}{extension}"
            if candidate.is_file():
                return candidate
        return None

    def entry_archive(self, source_id: str, index: int) -> tuple[bytes, str]:
        """§22.4 install: the entry's archive bytes plus its resolved
        reference. An https reference goes through the ordinary §5.2 fetch (its
        TransferError is the caller's 422 too); a file reference is resolved
        again here, so the §22.1 inside-the-directory check runs against the
        catalog's directory at fetch time, not only at refresh."""
        with self.lock:
            source = self._find(source_id)
            catalog = self._cached(source)
        if catalog is None:
            raise MarketplaceError(CACHE_UNREADABLE)
        entry = next((e for e in catalog["entries"] if e["index"] == index), None)
        if entry is None:
            raise KeyError(index)
        reference = entry["archive"]
        if reference.lower().startswith("https://"):
            data, _resolved = transfer.fetch_archive(reference)
            return data, reference
        try:
            data = Path(reference).read_bytes()
        except OSError as e:
            raise MarketplaceError(f"couldn't read the archive - {e.strerror or e}") from None
        if len(data) > transfer.MAX_ARCHIVE_BYTES:
            raise MarketplaceError(
                f"the archive is larger than the "
                f"{transfer.MAX_ARCHIVE_BYTES // (1024 * 1024)} MB import limit")
        return data, reference
