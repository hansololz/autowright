"""Marketplace (§22): catalog validation, reference resolution, the sources
store's refresh/image cache, and the §22.4 routes with the network stubbed."""
import io
import zipfile

import pytest
import yaml
from conftest import make_version

from autowright import marketplace, paths, transfer
from autowright.marketplace import MarketplaceError, MarketplaceStore

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def catalog_text(entries, **top) -> str:
    body = {"format_version": 1, **top, "entries": entries}
    return yaml.safe_dump(body, sort_keys=False)


def write_catalog(tmp_path, entries, filename="marketplace.yaml", **top):
    """A file-kind catalog beside the archives and images it lists (§22.2)."""
    f = tmp_path / filename
    f.write_text(catalog_text(entries, **top), encoding="utf-8")
    return f


# §22.2: what a refresh downloads - the `url` a test catalog declares.
CATALOG_URL = "https://x.test/shelf/marketplace-catalog.yaml"


def serve(monkeypatch, answers) -> list:
    """§22.2 refresh: what the declared `url` (and any image reference in the
    catalog it serves) answers with - bytes, or an exception to raise. Returns
    the list of urls asked for, in order."""
    asked = []

    def fetch(url, *, cap, deadline_s=marketplace.FETCH_DEADLINE_S):
        asked.append(url)
        answer = answers[url]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(marketplace, "_fetch_url", fetch)
    return asked


@pytest.fixture()
def market(home):
    """A store over this test's AUTOWRIGHT_HOME (§5 root)."""
    s = MarketplaceStore()
    s.load()
    return s


# ---------- §22.1 catalog validation ----------
def test_format_version_is_the_only_hard_gate():
    with pytest.raises(MarketplaceError) as e:
        marketplace.parse_catalog("format_version: 2\nentries: []\n",
                                  kind="file", origin="/m/marketplace.yaml")
    assert "format 1" in str(e.value)
    # unknown keys at any level are ignored so the format can grow in place
    m = marketplace.parse_catalog(
        catalog_text([{"title": "T", "path": "a.autowright", "future": 1}], future="x"),
        kind="file", origin="/m/marketplace.yaml")
    assert m["entries"][0]["title"] == "T"


def test_name_defaults_to_the_origin_stem():
    m = marketplace.parse_catalog(catalog_text([]), kind="file",
                                  origin="/m/marketplace.yaml")
    assert m["name"] == "marketplace"
    m = marketplace.parse_catalog(catalog_text([]), kind="url",
                                  origin="https://x.test/lists/mine.yaml")
    assert m["name"] == "mine"
    # a blank name is the same as none; a given one is stripped
    m = marketplace.parse_catalog(catalog_text([], name="  Community  "),
                                  kind="file", origin="/m/community.yaml")
    assert m["name"] == "Community"
    m = marketplace.parse_catalog(catalog_text([], name="   "), kind="file",
                                  origin="/m/community.yaml")
    assert m["name"] == "community"


def test_the_canonical_stem_names_the_folder_or_the_host():
    """§22.1: `marketplace-catalog` is every catalog's stem, so it would name
    them all alike - a file source falls back to its folder's name, a link
    source to its host name."""
    assert marketplace.default_name(
        "file", "/shelves/community/marketplace-catalog.yaml") == "community"
    # a catalog at the filesystem root has no folder name to take
    assert marketplace.default_name("file", "/marketplace-catalog.yaml") == "marketplace"
    assert marketplace.default_name(
        "url", "https://x.test/lists/marketplace-catalog.yaml") == "x.test"
    # any other stem still names the source
    assert marketplace.default_name("file", "/shelves/shelf.yaml") == "shelf"


def test_string_limits_reject_rather_than_truncate():
    for top, limit in (("name", marketplace.MAX_NAME),
                       ("description", marketplace.MAX_DESCRIPTION)):
        with pytest.raises(MarketplaceError) as e:
            marketplace.parse_catalog(catalog_text([], **{top: "x" * (limit + 1)}),
                                      kind="file", origin="/m/marketplace.yaml")
        assert f"{limit} characters" in str(e.value)


def test_url_must_be_an_https_link():
    """§22.1: `url` is the publisher's statement of where this catalog lives -
    https only, stripped, blank the same as absent."""
    def parse(**top):
        return marketplace.parse_catalog(catalog_text([], **top), kind="file",
                                         origin="/m/marketplace.yaml")

    assert parse()["url"] is None
    assert parse(url="  https://x.test/shelf/marketplace-catalog.yaml  ")["url"] == \
        "https://x.test/shelf/marketplace-catalog.yaml"
    assert parse(url="   ")["url"] is None
    with pytest.raises(MarketplaceError) as e:
        parse(url="http://x.test/m.yaml")
    assert str(e.value) == "`url` must be an https link"
    with pytest.raises(MarketplaceError) as e:
        parse(url="https://x.test/" + "y" * marketplace.MAX_URL)
    assert f"{marketplace.MAX_URL} characters" in str(e.value)


def test_entry_rules_name_the_entry_index():
    def parse(entries):
        return marketplace.parse_catalog(catalog_text(entries), kind="file",
                                         origin="/m/marketplace.yaml")

    ok = {"title": "Fine", "path": "a.autowright"}
    with pytest.raises(MarketplaceError) as e:
        parse([ok, ok, {"path": "b.autowright"}])
    assert str(e.value).startswith("entry 2: ")
    with pytest.raises(MarketplaceError) as e:
        parse([ok, {"title": "No archive", "path": "b.zip"}])
    assert str(e.value) == "entry 1: `path` must name an .autowright file"
    with pytest.raises(MarketplaceError) as e:
        parse([{"title": "Bad image", "path": "b.autowright", "image": "b.bmp"}])
    assert str(e.value).startswith("entry 0: `image` must name a ")
    with pytest.raises(MarketplaceError) as e:
        parse([{"title": "x" * (marketplace.MAX_TITLE + 1), "path": "b.autowright"}])
    assert str(e.value).startswith("entry 0: `title` is longer than ")
    # a query string is dropped before the extension is read
    assert parse([{"title": "T", "path": "a.autowright?raw=1",
                   "image": "i.PNG?v=2"}])["entries"][0]["index"] == 0


def test_entries_cap_and_shape():
    with pytest.raises(MarketplaceError) as e:
        marketplace.parse_catalog(
            catalog_text([{"title": "T", "path": "a.autowright"}]
                         * (marketplace.MAX_ENTRIES + 1)),
            kind="file", origin="/m/marketplace.yaml")
    assert "more than 200" in str(e.value)
    with pytest.raises(MarketplaceError):
        marketplace.parse_catalog("format_version: 1\n", kind="file",
                                  origin="/m/marketplace.yaml")
    with pytest.raises(MarketplaceError):
        marketplace.parse_catalog("- a\n- b\n", kind="file",
                                  origin="/m/marketplace.yaml")
    with pytest.raises(MarketplaceError):
        marketplace.parse_catalog("format_version: 1\nentries: [1, 2]\n", kind="file",
                                  origin="/m/marketplace.yaml")


# ---------- §22.1 reference resolution ----------
def test_url_references_join_and_must_stay_https():
    origin = "https://x.test/lists/marketplace.yaml"
    assert marketplace.resolve_reference("a/manga.autowright", kind="url",
                                         origin=origin) == \
        "https://x.test/lists/a/manga.autowright"
    assert marketplace.resolve_reference("https://other.test/m.autowright", kind="url",
                                         origin=origin) == "https://other.test/m.autowright"
    with pytest.raises(MarketplaceError):
        marketplace.resolve_reference("http://other.test/m.autowright", kind="url",
                                      origin=origin)


def test_file_references_resolve_against_the_folder_or_stand_alone(tmp_path):
    """§22.1: a relative reference resolves against the catalog's directory
    (`..` hops included); an https URL or an absolute local path is taken as it
    is, in a file source and a link source alike; any other scheme is refused."""
    origin = str(tmp_path / "shelf" / "marketplace.yaml")
    (tmp_path / "shelf").mkdir()
    assert marketplace.resolve_reference("a/manga.autowright", kind="file",
                                         origin=origin) == \
        str(tmp_path / "shelf" / "a" / "manga.autowright")
    assert marketplace.resolve_reference("../x.autowright", kind="file",
                                         origin=origin) == str(tmp_path / "x.autowright")
    # an absolute https reference is fine in a file source (mixed catalogs)
    assert marketplace.resolve_reference("https://x.test/m.autowright", kind="file",
                                         origin=origin) == "https://x.test/m.autowright"
    # an absolute local path stands alone - the user chose to add this catalog
    elsewhere = str(tmp_path / "elsewhere" / "kept.autowright")
    assert marketplace.resolve_reference(elsewhere, kind="file", origin=origin) == elsewhere
    assert marketplace.resolve_reference(elsewhere, kind="url",
                                         origin="https://x.test/shelf/marketplace.yaml") \
        == elsewhere
    with pytest.raises(MarketplaceError) as e:
        marketplace.resolve_reference("file:///x.autowright", kind="file", origin=origin)
    assert "neither an https reference nor a path" in str(e.value)


def test_an_absolute_local_path_installs_from_anywhere(market, tmp_path):
    """§22.1/§22.4: an entry naming an archive elsewhere on the machine reads
    that file at install; a missing one is the ordinary 422-class error."""
    kept = tmp_path / "elsewhere"
    kept.mkdir()
    (kept / "kept.autowright").write_bytes(b"not really a zip")
    shelf = tmp_path / "shelf"
    shelf.mkdir()
    f = write_catalog(shelf, [{"title": "Kept", "path": str(kept / "kept.autowright")},
                              {"title": "Gone", "path": str(kept / "gone.autowright")}])
    source = market.add(path=str(f))
    assert [e["archive"] for e in source["entries"]] == \
        [str(kept / "kept.autowright"), str(kept / "gone.autowright")]
    data, reference = market.entry_archive(source["id"], 0)
    assert data == b"not really a zip" and reference == str(kept / "kept.autowright")
    with pytest.raises(MarketplaceError) as e:
        market.entry_archive(source["id"], 1)
    assert "couldn't read the archive" in str(e.value)


# ---------- §22.2 store ----------
def test_add_file_source_caches_catalog_and_images(market, tmp_path):
    (tmp_path / "manga.autowright").write_bytes(b"zip")
    (tmp_path / "cover.png").write_bytes(PNG)
    f = write_catalog(tmp_path, [{"title": "Manga", "description": "Checks it",
                                  "path": "manga.autowright", "image": "cover.png"}],
                      name="Mine")
    source = market.add(path=str(f))
    assert source["name"] == "Mine" and source["kind"] == "file"
    assert source["error"] is None and source["refreshedAt"]
    assert source["entries"] == [{"index": 0, "title": "Manga", "description": "Checks it",
                                  "archive": str(tmp_path / "manga.autowright"),
                                  "image": True}]
    cached = market.catalog_file(source["id"]).read_text(encoding="utf-8")
    assert cached == f.read_text(encoding="utf-8")  # byte for byte (§22.2)
    assert market.image_path(source["id"], 0).read_bytes() == PNG
    # §22.2: the record is on disk under the §5 root, derived fields are not
    stored = yaml.safe_load((paths.marketplace_dir() / "sources.yaml").read_text())
    assert list(stored["sources"][0]) == ["id", "kind", "origin", "added_at",
                                          "refreshed_at", "error"]


def test_add_rejects_bad_origins_and_duplicates(market, tmp_path):
    with pytest.raises(MarketplaceError) as e:
        market.add(url="http://x.test/m.yaml")
    assert str(e.value) == "only https:// links can be added"
    with pytest.raises(MarketplaceError):
        market.add(url="https://x.test/m.yaml", path=str(tmp_path / "m.yaml"))
    with pytest.raises(MarketplaceError):
        market.add()
    with pytest.raises(MarketplaceError):
        market.add(path="relative/marketplace.yaml")
    with pytest.raises(MarketplaceError):
        market.add(path=str(tmp_path / "nothing.yaml"))
    f = write_catalog(tmp_path, [])
    market.add(path=str(f))
    with pytest.raises(marketplace.MarketplaceDuplicate):
        market.add(path=str(tmp_path / "." / "marketplace.yaml"))  # compares resolved


def test_add_stores_nothing_when_validation_fails(market, tmp_path):
    f = tmp_path / "marketplace.yaml"
    f.write_text("format_version: 9\nentries: []\n", encoding="utf-8")
    with pytest.raises(MarketplaceError):
        market.add(path=str(f))
    assert market.sources == []
    assert not (paths.marketplace_dir() / "sources.yaml").exists()
    # a reference with a scheme that isn't https is a validation failure
    # naming its entry (a `..` hop or an absolute local path is legal, §22.1)
    bad = write_catalog(tmp_path, [{"title": "Scheme", "path": "file:///x.autowright"}],
                        filename="scheme.yaml")
    with pytest.raises(MarketplaceError) as e:
        market.add(path=str(bad))
    assert str(e.value).startswith("entry 0: ")
    assert market.sources == []


def test_refresh_keeps_the_cache_and_records_the_error(market, tmp_path, monkeypatch):
    f = write_catalog(tmp_path, [{"title": "One", "path": "one.autowright"}],
                      url=CATALOG_URL)
    source = market.add(path=str(f))
    first_refreshed = source["refreshedAt"]
    serve(monkeypatch, {CATALOG_URL: b"format_version: 1\nentries: [oops\n"})
    after = market.refresh(source["id"])
    assert after["error"] and "YAML" in after["error"]
    assert after["refreshedAt"] == first_refreshed          # last *successful* fetch
    assert [e["title"] for e in after["entries"]] == ["One"]  # last good copy
    # §22.2: a failed refresh leaves where the cached copy came from alone
    assert after["kind"] == "file" and after["origin"] == str(f)
    # a success clears the error and stamps a new time
    serve(monkeypatch, {CATALOG_URL: catalog_text(
        [{"title": "Two", "path": "two.autowright"}], url=CATALOG_URL).encode()})
    fixed = market.refresh(source["id"])
    assert fixed["error"] is None
    assert [e["title"] for e in fixed["entries"]] == ["Two"]


def test_refresh_downloads_the_declared_url_not_the_add_origin(market, tmp_path,
                                                               monkeypatch):
    """§22.2: a refresh downloads the cached catalog's `url` - never the link
    or the file the source was added from - and from then on the cached copy is
    the published one (`kind`/`origin` move to it)."""
    f = write_catalog(tmp_path, [{"title": "One", "path": "one.autowright"}],
                      name="Shelf", url=CATALOG_URL)
    source = market.add(path=str(f))
    assert source["kind"] == "file" and source["url"] == CATALOG_URL
    # the file on disk is never read again: what lands is what the url served
    f.write_text(catalog_text([{"title": "From the file", "path": "f.autowright"}],
                              name="Stale", url=CATALOG_URL), encoding="utf-8")
    served = catalog_text([{"title": "Two", "path": "two.autowright",
                            "image": "img/cover.png"}], name="Shelf", url=CATALOG_URL)
    asked = serve(monkeypatch, {CATALOG_URL: served.encode(),
                                "https://x.test/shelf/img/cover.png": PNG})
    after = market.refresh(source["id"])
    assert asked == [CATALOG_URL, "https://x.test/shelf/img/cover.png"]
    assert after["kind"] == "url" and after["origin"] == CATALOG_URL
    assert after["url"] == CATALOG_URL and after["name"] == "Shelf"
    assert [e["title"] for e in after["entries"]] == ["Two"]
    assert after["entries"][0]["archive"] == "https://x.test/shelf/two.autowright"
    assert market.catalog_file(source["id"]).read_text(encoding="utf-8") == served
    assert market.image_path(source["id"], 0).read_bytes() == PNG


def test_a_downloaded_catalog_without_a_url_stops_being_refreshable(market, tmp_path,
                                                                    monkeypatch):
    """§22.2: a refresh takes the downloaded catalog as it is - one that
    declares no `url` leaves the source a one-time download."""
    f = write_catalog(tmp_path, [{"title": "One", "path": "one.autowright"}],
                      url=CATALOG_URL)
    source = market.add(path=str(f))
    serve(monkeypatch, {CATALOG_URL: catalog_text(
        [{"title": "Last", "path": "last.autowright"}]).encode()})
    after = market.refresh(source["id"])
    assert after["url"] is None
    assert [e["title"] for e in after["entries"]] == ["Last"]
    with pytest.raises(marketplace.MarketplaceNotRefreshable):
        market.refresh(source["id"])


def test_a_source_without_a_url_is_not_refreshable(market, tmp_path, monkeypatch):
    """§22.2: a catalog that declares no `url` has nothing to refresh from - a
    single-source refresh refuses with the record untouched, a refresh-all
    skips it and still lists it."""
    plain = write_catalog(tmp_path, [{"title": "Plain", "path": "p.autowright"}],
                          filename="plain.yaml")
    linked = write_catalog(tmp_path, [{"title": "Linked", "path": "l.autowright"}],
                           filename="linked.yaml", url=CATALOG_URL)
    one = market.add(path=str(plain))
    two = market.add(path=str(linked))
    assert one["url"] is None and two["url"] == CATALOG_URL
    with pytest.raises(marketplace.MarketplaceNotRefreshable) as e:
        market.refresh(one["id"])
    assert str(e.value) == marketplace.NOT_REFRESHABLE
    assert market.sources[0]["refreshed_at"] == one["refreshedAt"]
    assert market.sources[0]["error"] is None
    # §22.2 refresh-all: the skipped source is listed as it was
    serve(monkeypatch, {CATALOG_URL: catalog_text(
        [{"title": "Linked", "path": "l.autowright"}], url=CATALOG_URL).encode()})
    sources = market.refresh_all()
    assert [s["id"] for s in sources] == [one["id"], two["id"]]
    assert sources[0]["refreshedAt"] == one["refreshedAt"]
    assert sources[0]["error"] is None and sources[0]["kind"] == "file"
    assert sources[1]["refreshedAt"] != two["refreshedAt"]


def test_refresh_refuses_a_url_another_source_already_holds(market, tmp_path,
                                                            monkeypatch):
    """§22.2: the swap would leave two records for one link - the refresh fails
    naming the other marketplace and the cache stays as it was."""
    served = catalog_text([{"title": "Linked", "path": "l.autowright"}],
                          name="Linked", url=CATALOG_URL)
    serve(monkeypatch, {CATALOG_URL: served.encode()})
    market.add(url=CATALOG_URL)
    f = write_catalog(tmp_path, [{"title": "Mine", "path": "m.autowright"}],
                      name="Mine", url=CATALOG_URL)
    second = market.add(path=str(f))
    after = market.refresh(second["id"])
    assert after["error"] == 'that link is already added as "Linked"'
    assert after["kind"] == "file" and after["origin"] == str(f)
    assert [e["title"] for e in after["entries"]] == ["Mine"]


def test_refresh_all_never_stops_at_the_first_bad_source(market, tmp_path, monkeypatch):
    good_url = "https://x.test/good/marketplace-catalog.yaml"
    bad_url = "https://x.test/bad/marketplace-catalog.yaml"
    good_dir, bad_dir = tmp_path / "good", tmp_path / "bad"
    good_dir.mkdir()
    bad_dir.mkdir()
    good = write_catalog(good_dir, [{"title": "Good", "path": "g.autowright"}],
                         url=good_url)
    bad = write_catalog(bad_dir, [{"title": "Bad", "path": "b.autowright"}], url=bad_url)
    market.add(path=str(good))
    bad_source = market.add(path=str(bad))
    serve(monkeypatch, {
        good_url: catalog_text([{"title": "Good", "path": "g.autowright"}],
                               url=good_url).encode(),
        bad_url: MarketplaceError("download failed - the server answered 404")})
    sources = market.refresh_all()
    assert sources[0]["error"] is None
    assert sources[1]["id"] == bad_source["id"] and sources[1]["error"]
    assert [e["title"] for e in sources[1]["entries"]] == ["Bad"]


def test_images_are_rebuilt_and_skipped_on_failure(market, tmp_path, monkeypatch):
    (tmp_path / "cover.png").write_bytes(PNG)
    f = write_catalog(tmp_path, [
        {"title": "Has one", "path": "a.autowright", "image": "cover.png"},
        {"title": "Missing", "path": "b.autowright", "image": "gone.png"},
        {"title": "Too big", "path": "c.autowright", "image": "huge.png"},
    ], url=CATALOG_URL)
    (tmp_path / "huge.png").write_bytes(b"0" * (marketplace.MAX_IMAGE_BYTES + 1))
    source = market.add(path=str(f))
    assert [e["image"] for e in source["entries"]] == [True, False, False]
    # a refresh rebuilds the directory: the dropped image's cache file goes
    serve(monkeypatch, {CATALOG_URL: catalog_text(
        [{"title": "Has one", "path": "a.autowright"}], url=CATALOG_URL).encode()})
    after = market.refresh(source["id"])
    assert after["entries"] == [{"index": 0, "title": "Has one", "description": "",
                                 "archive": "https://x.test/shelf/a.autowright",
                                 "image": False}]
    assert market.image_path(source["id"], 0) is None
    assert not market.images_dir(source["id"]).with_name("images.tmp").exists()


def test_url_source_fetches_catalog_and_images(market, monkeypatch):
    origin = "https://x.test/shelf/marketplace.yaml"
    served = {
        origin: catalog_text([{"title": "Manga", "path": "a/manga.autowright",
                               "image": "img/cover.png"}], name="Shelf").encode(),
        "https://x.test/shelf/img/cover.png": PNG,
    }
    monkeypatch.setattr(marketplace, "_fetch_url",
                        lambda url, *, cap, deadline_s=60: served[url])
    source = market.add(url=origin)
    assert source["name"] == "Shelf"
    assert source["entries"][0]["archive"] == "https://x.test/shelf/a/manga.autowright"
    assert source["entries"][0]["image"] is True
    assert market.image_path(source["id"], 0).read_bytes() == PNG


def test_unreadable_cache_still_lists_the_source(market, tmp_path, monkeypatch):
    f = write_catalog(tmp_path, [{"title": "One", "path": "one.autowright"}],
                      name="Shelf", url=CATALOG_URL)
    source = market.add(path=str(f))
    market.catalog_file(source["id"]).write_text("format_version: 9\n", encoding="utf-8")
    listed = market.serialize(market.sources[0])
    # §22.2: the source still lists, with zero entries and the origin-derived name
    assert listed["entries"] == [] and listed["name"] == "marketplace"
    assert listed["error"] == marketplace.CACHE_UNREADABLE
    assert listed["cached"] is False
    # §22.2: an unreadable cache declares no `url` either - nothing to refresh from
    assert listed["url"] is None
    with pytest.raises(marketplace.MarketplaceNotRefreshable):
        market.refresh(source["id"])
    # a real stored refresh error wins over the cache-read one
    market.catalog_file(source["id"]).write_text(
        catalog_text([{"title": "One", "path": "one.autowright"}], url=CATALOG_URL),
        encoding="utf-8")
    serve(monkeypatch, {CATALOG_URL: MarketplaceError(
        "download failed - the server answered 404")})
    failed = market.refresh(source["id"])
    assert "the server answered 404" in failed["error"]
    market.catalog_file(source["id"]).write_text("format_version: 9\n", encoding="utf-8")
    assert market.serialize(market.sources[0])["error"] == failed["error"]


def test_remove_drops_the_record_and_its_directory(market, tmp_path):
    f = write_catalog(tmp_path, [])
    source = market.add(path=str(f))
    directory = market.source_dir(source["id"])
    assert directory.is_dir()
    market.remove(source["id"])
    assert market.sources == [] and not directory.exists()
    with pytest.raises(KeyError):
        market.remove(source["id"])


def test_sources_yaml_lenient_load(market, home, caplog):
    """§21.4 fixture: the old-shape/hand-edited `sources.yaml` load (§5 lenient
    rule). An entry missing id, kind, or origin, or with an unknown kind, skips
    with a warning; the good entries still load."""
    paths.marketplace_dir().mkdir(parents=True, exist_ok=True)
    (paths.marketplace_dir() / "sources.yaml").write_text(
        "sources:\n"
        "- id: keep-me\n"
        "  kind: file\n"
        "  origin: /m/marketplace.yaml\n"
        "  added_at: '2026-09-11T00:00:00.000000+00:00'\n"
        "  refreshed_at: null\n"
        "  error: null\n"
        "- kind: file\n"
        "  origin: /m/no-id.yaml\n"
        "- id: no-kind\n"
        "  origin: /m/no-kind.yaml\n"
        "- id: no-origin\n"
        "  kind: file\n"
        "- id: alien\n"
        "  kind: ftp\n"
        "  origin: ftp://x.test/m.yaml\n"
        "- just a string\n", encoding="utf-8")
    with caplog.at_level("WARNING"):
        market.load()
    assert [s["id"] for s in market.sources] == ["keep-me"]
    assert market.sources[0]["refreshed_at"] is None
    assert len(caplog.records) == 5
    # a file that isn't a mapping at all loads as no marketplaces, never raises
    (paths.marketplace_dir() / "sources.yaml").write_text("- a\n- b\n", encoding="utf-8")
    market.load()
    assert market.sources == []


def test_entry_archive_rechecks_the_folder_at_fetch_time(market, tmp_path):
    (tmp_path / "manga.autowright").write_bytes(b"archive-bytes")
    f = write_catalog(tmp_path, [{"title": "Manga", "path": "manga.autowright"}])
    source = market.add(path=str(f))
    data, reference = market.entry_archive(source["id"], 0)
    assert data == b"archive-bytes"
    assert reference == str(tmp_path / "manga.autowright")
    with pytest.raises(KeyError):
        market.entry_archive(source["id"], 7)
    with pytest.raises(KeyError):
        market.entry_archive("nope", 0)
    # the cached catalog is re-resolved on every fetch, so an escape edited
    # into the cache since the refresh is still rejected
    market.catalog_file(source["id"]).write_text(
        catalog_text([{"title": "Manga", "path": "../escape.autowright"}]),
        encoding="utf-8")
    with pytest.raises(MarketplaceError):
        market.entry_archive(source["id"], 0)


def test_entry_archive_fetches_an_https_reference(market, tmp_path, monkeypatch):
    f = write_catalog(tmp_path, [{"title": "Web", "path": "https://x.test/w.autowright"}])
    source = market.add(path=str(f))
    monkeypatch.setattr(transfer, "fetch_archive",
                        lambda url: (b"from-web", url))
    data, reference = market.entry_archive(source["id"], 0)
    assert data == b"from-web" and reference == "https://x.test/w.autowright"


# ---------- §22.4 routes ----------
def _export(client) -> bytes:
    """A real §5.1 export, so the entry preview below lands through import."""
    from autowright.storage import new_id, store

    ver = {"description": "Shared thing", "params": [],
           "steps": [{"name": "Go", "description": "", "code": "print(1)\n"}],
           "spec": [{"kind": "h1", "text": "T"}], "instructions": None}
    a = store.create_automation(ver, name="Shared", agent_id="mock",
                                triggers=[{"id": new_id(), "kind": "cron", "enabled": True,
                                           "expression": "0 9 * * *"}])
    return client.get(f"/automations/{a['id']}/export").content


def test_routes_add_list_refresh_and_remove(client, tmp_path, monkeypatch):
    (tmp_path / "one.autowright").write_bytes(b"zip")
    (tmp_path / "cover.png").write_bytes(PNG)
    f = write_catalog(tmp_path, [{"title": "One", "path": "one.autowright",
                                  "image": "cover.png"}], name="Shelf",
                      url=CATALOG_URL)
    r = client.post("/marketplace/sources", json={"path": str(f)})
    assert r.status_code == 200
    source = r.json()
    assert source["name"] == "Shelf" and source["entries"][0]["image"] is True

    assert client.get("/marketplace").json()["sources"] == [source]
    # §22.4 add rules
    assert client.post("/marketplace/sources", json={}).status_code == 422
    assert client.post("/marketplace/sources",
                       json={"url": "https://x.test/m.yaml",
                             "path": str(f)}).status_code == 422
    r = client.post("/marketplace/sources", json={"url": "http://x.test/m.yaml"})
    assert r.status_code == 422 and "https" in r.json()["detail"]
    r = client.post("/marketplace/sources", json={"path": str(f)})
    assert r.status_code == 409 and r.json()["detail"] == "that marketplace is already added"

    # §22.4 refresh: 200 with the reason even when it failed
    serve(monkeypatch, {CATALOG_URL: MarketplaceError(
        "download failed - the server answered 404")})
    r = client.post(f"/marketplace/sources/{source['id']}/refresh")
    assert r.status_code == 200 and r.json()["error"]
    assert [e["title"] for e in r.json()["entries"]] == ["One"]
    r = client.post("/marketplace/refresh")
    assert r.status_code == 200 and r.json()["sources"][0]["error"]
    assert client.post("/marketplace/sources/nope/refresh").status_code == 404

    assert client.delete(f"/marketplace/sources/{source['id']}").json() == {"ok": True}
    assert client.delete(f"/marketplace/sources/{source['id']}").status_code == 404
    assert client.get("/marketplace").json()["sources"] == []


def test_route_refuses_to_refresh_a_source_without_a_url(client, tmp_path):
    """§22.4: a source whose catalog declares no `url` answers 409 with the
    record untouched, and a refresh-all lists it as it was."""
    from autowright.api import marketplace_store

    f = write_catalog(tmp_path, [{"title": "One", "path": "one.autowright"}], name="Shelf")
    source = client.post("/marketplace/sources", json={"path": str(f)}).json()
    assert source["url"] is None
    record = dict(marketplace_store.sources[0])

    r = client.post(f"/marketplace/sources/{source['id']}/refresh")
    assert r.status_code == 409 and r.json()["detail"] == marketplace.NOT_REFRESHABLE
    assert marketplace_store.sources[0] == record

    r = client.post("/marketplace/refresh")
    assert r.status_code == 200
    listed = r.json()["sources"]
    assert [s["id"] for s in listed] == [source["id"]]
    assert listed[0]["refreshedAt"] == source["refreshedAt"]
    assert listed[0]["error"] is None


def test_image_route_serves_the_cached_bytes(client, tmp_path):
    (tmp_path / "cover.png").write_bytes(PNG)
    f = write_catalog(tmp_path, [{"title": "One", "path": "one.autowright",
                                  "image": "cover.png"},
                                 {"title": "Two", "path": "two.autowright"}])
    source = client.post("/marketplace/sources", json={"path": str(f)}).json()
    r = client.get(f"/marketplace/sources/{source['id']}/entries/0/image")
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert client.get(
        f"/marketplace/sources/{source['id']}/entries/1/image").status_code == 404
    assert client.get("/marketplace/sources/nope/entries/0/image").status_code == 404


def test_entry_preview_yields_a_confirmable_token(client, tmp_path):
    from autowright.storage import store

    archive = _export(client)
    (tmp_path / "shared.autowright").write_bytes(archive)
    f = write_catalog(tmp_path, [{"title": "Shared", "path": "shared.autowright"}],
                      name="Shelf")
    source = client.post("/marketplace/sources", json={"path": str(f)}).json()
    before = len(store.autos)

    r = client.post(f"/marketplace/sources/{source['id']}/entries/0/preview")
    assert r.status_code == 200
    body = r.json()
    assert body["preview"]["name"] == "Shared"
    reference = str(tmp_path / "shared.autowright")
    assert body["preview"]["sourceUrl"] == reference
    assert body["preview"]["resolvedUrl"] == reference
    assert len(store.autos) == before  # §5.2: preview writes nothing

    r2 = client.post("/automations/import/confirm", json={"token": body["token"]})
    assert r2.status_code == 200
    assert r2.json()["automation"]["name"] == "Shared 2"
    assert r2.json()["automation"]["allTriggersOff"] is True

    # §22.4: a missing archive answers the ordinary 422; unknown ids 404
    (tmp_path / "shared.autowright").unlink()
    assert client.post(
        f"/marketplace/sources/{source['id']}/entries/0/preview").status_code == 422
    assert client.post(
        f"/marketplace/sources/{source['id']}/entries/9/preview").status_code == 404
    assert client.post("/marketplace/sources/nope/entries/0/preview").status_code == 404


def test_entry_preview_maps_a_transfer_error_to_422(client, tmp_path, monkeypatch):
    f = write_catalog(tmp_path, [{"title": "Web", "path": "https://x.test/w.autowright"}])
    source = client.post("/marketplace/sources", json={"path": str(f)}).json()

    def boom(url):
        raise transfer.TransferError("download failed - the server answered 404")

    monkeypatch.setattr(transfer, "fetch_archive", boom)
    r = client.post(f"/marketplace/sources/{source['id']}/entries/0/preview")
    assert r.status_code == 422 and "404" in r.json()["detail"]


def test_url_add_route(client, monkeypatch):
    """A link source through the §22.4 route, with the network stubbed."""
    monkeypatch.setattr(
        marketplace, "_fetch_url",
        lambda url, *, cap, deadline_s=60: catalog_text(
            [{"title": "Web", "path": "w.autowright"}], name="Linked").encode())
    r = client.post("/marketplace/sources", json={"url": "https://x.test/marketplace.yaml"})
    assert r.status_code == 200
    source = r.json()
    assert source["kind"] == "url" and source["name"] == "Linked"
    assert source["entries"][0]["archive"] == "https://x.test/w.autowright"
    assert client.get("/marketplace").json()["sources"][0]["id"] == source["id"]


# ---------- §22.7 catalog authoring ----------
def _shelf(market, tmp_path, name="shelf"):
    """§22.7 create: an empty catalog in its own folder, added as a file
    source. Answers the folder and the served §22.4 Source."""
    folder = tmp_path / name
    folder.mkdir()
    return folder, market.create_catalog(str(folder))


def _exporter(archives: dict, calls: list | None = None):
    """A §22.7 `export(automation_id)` stub: the `(name, bytes)` each id
    answers with (the §5.1 export the route runs without parameter values), or
    a KeyError for an id this app doesn't hold."""
    def export(automation_id):
        if calls is not None:
            calls.append(automation_id)
        if automation_id not in archives:
            raise KeyError(automation_id)
        return archives[automation_id]
    return export


def test_create_writes_the_empty_catalog_and_adds_the_source(market, tmp_path):
    """§22.7: `format_version: 1`, the folder's name, no entries - then the
    ordinary §22.2 add over the file just written."""
    folder, source = _shelf(market, tmp_path, "community")
    catalog = folder / marketplace.CATALOG_FILENAME
    written = yaml.safe_load(catalog.read_text(encoding="utf-8"))
    assert written == {"format_version": 1, "name": "community", "entries": []}
    assert source["kind"] == "file" and source["origin"] == str(catalog)
    assert source["name"] == "community" and source["entries"] == []
    assert source["url"] is None and source["error"] is None and source["refreshedAt"]
    # the catalog is cached like any other added source
    assert market.catalog_file(source["id"]).read_text(encoding="utf-8") == \
        catalog.read_text(encoding="utf-8")


def test_create_refuses_a_taken_folder_and_a_bad_path(market, tmp_path):
    folder, _ = _shelf(market, tmp_path)
    with pytest.raises(marketplace.MarketplaceDuplicate) as e:
        market.create_catalog(str(folder))
    assert str(e.value) == marketplace.FOLDER_TAKEN
    assert len(market.sources) == 1
    with pytest.raises(MarketplaceError):
        market.create_catalog(str(tmp_path / "nothing"))
    with pytest.raises(MarketplaceError):
        market.create_catalog("shelves/mine")
    with pytest.raises(MarketplaceError):
        market.create_catalog("")


def test_read_catalog_answers_the_file_as_written(market, tmp_path, monkeypatch):
    """§22.7 GET: the file on disk, references unresolved - a `url` source has
    no file on this machine to edit."""
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "cover.png").write_bytes(PNG)
    f = write_catalog(tmp_path, [{"title": "Manga", "description": "Checks it",
                                  "path": "a/manga.autowright", "image": "img/cover.png"}],
                      name="Mine", description="Automations I use.", url=CATALOG_URL)
    source = market.add(path=str(f))
    assert market.read_catalog(source["id"]) == {
        "name": "Mine", "description": "Automations I use.", "url": CATALOG_URL,
        "entries": [{"index": 0, "title": "Manga", "description": "Checks it",
                     "path": "a/manga.autowright", "image": "img/cover.png"}]}

    serve(monkeypatch, {CATALOG_URL: catalog_text(
        [{"title": "Web", "path": "w.autowright"}], name="Linked").encode()})
    linked = market.add(url=CATALOG_URL)
    with pytest.raises(marketplace.MarketplaceNotEditable) as e:
        market.read_catalog(linked["id"])
    assert str(e.value) == marketplace.NOT_EDITABLE
    with pytest.raises(KeyError):
        market.read_catalog("nope")


def test_save_exports_automations_beside_the_catalog(market, tmp_path):
    """§22.7 steps 2-3: every `automationId` entry lands as its own archive
    under `transfer.safe_filename` of the automation's name; a second entry
    with the same name takes ` 2`."""
    folder, source = _shelf(market, tmp_path)
    calls: list = []
    export = _exporter({"a1": ("Daily/Report", b"archive-bytes")}, calls)
    before = source["refreshedAt"]
    saved = market.save_catalog(source["id"], {
        "name": "Shelf", "description": "What I run.", "url": CATALOG_URL,
        "entries": [{"title": "First", "description": "Runs daily", "automationId": "a1"},
                    {"title": "Second", "description": "", "automationId": "a1"}]}, export)
    assert calls == ["a1", "a1"]
    assert (folder / "Daily Report.autowright").read_bytes() == b"archive-bytes"
    assert (folder / "Daily Report 2.autowright").read_bytes() == b"archive-bytes"
    written = yaml.safe_load((folder / marketplace.CATALOG_FILENAME).read_text(encoding="utf-8"))
    assert written["name"] == "Shelf" and written["description"] == "What I run."
    assert written["url"] == CATALOG_URL
    assert [e["path"] for e in written["entries"]] == ["Daily Report.autowright",
                                                       "Daily Report 2.autowright"]
    # §22.7 step 6: the source's cache is rebuilt from the file just written
    assert saved["name"] == "Shelf" and saved["url"] == CATALOG_URL
    assert saved["entries"] == [
        {"index": 0, "title": "First", "description": "Runs daily",
         "archive": str(folder / "Daily Report.autowright"), "image": False},
        {"index": 1, "title": "Second", "description": "",
         "archive": str(folder / "Daily Report 2.autowright"), "image": False}]
    assert saved["refreshedAt"] != before
    assert market.catalog_file(source["id"]).read_text(encoding="utf-8") == \
        (folder / marketplace.CATALOG_FILENAME).read_text(encoding="utf-8")


def test_save_never_overwrites_an_archive_already_there(market, tmp_path):
    """§22.7 step 3: the folder is the user's - a file already on disk keeps
    its bytes and the new export takes ` 2`."""
    folder, source = _shelf(market, tmp_path)
    (folder / "Watcher.autowright").write_bytes(b"the-users-own-file")
    market.save_catalog(source["id"], {
        "name": "Shelf", "description": "", "url": "",
        "entries": [{"title": "Watcher", "description": "", "automationId": "a1"}]},
        _exporter({"a1": ("Watcher", b"freshly-exported")}))
    assert (folder / "Watcher.autowright").read_bytes() == b"the-users-own-file"
    assert (folder / "Watcher 2.autowright").read_bytes() == b"freshly-exported"
    written = yaml.safe_load((folder / marketplace.CATALOG_FILENAME).read_text(encoding="utf-8"))
    assert written["entries"][0]["path"] == "Watcher 2.autowright"


def test_save_keeps_a_path_entry_and_its_image_as_written(market, tmp_path):
    """§22.7: a `path` entry names an archive already in place - neither it nor
    its image is touched by a save."""
    folder, source = _shelf(market, tmp_path)
    (folder / "already.autowright").write_bytes(b"already-here")
    (folder / "images").mkdir()
    (folder / "images" / "cover.png").write_bytes(PNG)
    saved = market.save_catalog(source["id"], {
        "name": "Shelf", "description": "", "url": "",
        "entries": [{"title": "Kept", "description": "Still here",
                     "path": "already.autowright", "image": "images/cover.png"}]},
        _exporter({}))
    written = yaml.safe_load((folder / marketplace.CATALOG_FILENAME).read_text(encoding="utf-8"))
    assert written["entries"] == [{"title": "Kept", "description": "Still here",
                                   "path": "already.autowright", "image": "images/cover.png"}]
    assert (folder / "already.autowright").read_bytes() == b"already-here"
    assert saved["entries"][0]["image"] is True
    assert market.image_path(source["id"], 0).read_bytes() == PNG


def test_save_rewrites_the_file_with_the_catalog_keys_only(market, tmp_path):
    """§22.7 step 4: the editor owns the file - unknown keys and hand-written
    comments don't survive a save."""
    folder = tmp_path / "hand"
    folder.mkdir()
    f = folder / marketplace.CATALOG_FILENAME
    f.write_text("# the shelf I share with friends\n"
                 "format_version: 1\n"
                 "future: 1\n"
                 "name: Hand written\n"
                 "entries:\n"
                 "  - title: One\n"
                 "    path: one.autowright\n"
                 "    future: 2\n", encoding="utf-8")
    source = market.add(path=str(f))
    market.save_catalog(source["id"], {
        "name": "Hand written", "description": "", "url": "",
        "entries": [{"title": "One", "description": "", "path": "one.autowright"}]},
        _exporter({}))
    text = f.read_text(encoding="utf-8")
    assert "#" not in text and "future" not in text
    written = yaml.safe_load(text)
    assert list(written) == ["format_version", "name", "entries"]
    assert list(written["entries"][0]) == ["title", "path"]


def test_save_writes_nothing_when_an_entry_fails(market, tmp_path):
    """§22.7: everything is checked before anything is written - a failure in
    entry 1 leaves entry 0's export unwritten and the file byte-identical."""
    folder, source = _shelf(market, tmp_path)
    f = folder / marketplace.CATALOG_FILENAME
    before_text = f.read_text(encoding="utf-8")
    before_files = sorted(p.name for p in folder.iterdir())
    with pytest.raises(MarketplaceError) as e:
        market.save_catalog(source["id"], {
            "name": "Shelf", "description": "", "url": "",
            "entries": [{"title": "Fine", "description": "", "automationId": "a1"},
                        {"title": "Gone", "description": "", "automationId": "deleted"}]},
            _exporter({"a1": ("Fine", b"archive-bytes")}))
    assert str(e.value) == "entry 1: no automation has that id"
    assert sorted(p.name for p in folder.iterdir()) == before_files
    assert f.read_text(encoding="utf-8") == before_text


def test_save_checks_the_fields_and_the_one_of_rule(market, tmp_path):
    """§22.7 step 1 and the exactly-one rule, both naming the entry."""
    folder, source = _shelf(market, tmp_path)
    f = folder / marketplace.CATALOG_FILENAME
    before_text = f.read_text(encoding="utf-8")

    def save(**body):
        return market.save_catalog(source["id"], {"name": "Shelf", "description": "",
                                                  "url": "", **body}, _exporter({}))

    with pytest.raises(MarketplaceError) as e:
        save(entries=[{"title": "  ", "description": "", "path": "a.autowright"}])
    assert str(e.value) == "entry 0: it has no title"
    with pytest.raises(MarketplaceError) as e:
        save(entries=[{"title": "One", "description": "", "path": "a.autowright"},
                      {"title": "Two", "description": "", "path": "b.autowright",
                       "automationId": "a1"}])
    assert str(e.value).startswith("entry 1: ")
    with pytest.raises(MarketplaceError) as e:
        save(entries=[{"title": "Neither", "description": ""}])
    assert str(e.value).startswith("entry 0: ")
    with pytest.raises(MarketplaceError) as e:
        save(url="http://x.test/marketplace-catalog.yaml", entries=[])
    assert str(e.value) == "`url` must be an https link"
    with pytest.raises(MarketplaceError) as e:
        save(name="x" * (marketplace.MAX_NAME + 1), entries=[])
    assert f"{marketplace.MAX_NAME} characters" in str(e.value)
    assert f.read_text(encoding="utf-8") == before_text


def test_save_refuses_an_archive_file_that_is_not_an_archive(market, tmp_path):
    """§22.7 step 2: an `archiveFile` passes the §5.1 archive validation
    without matching - the verdict is the entry's 422."""
    folder, source = _shelf(market, tmp_path)
    junk = tmp_path / "junk.autowright"
    junk.write_bytes(b"not a zip at all")

    def save(path):
        return market.save_catalog(source["id"], {
            "name": "Shelf", "description": "", "url": "",
            "entries": [{"title": "Copied", "description": "", "archiveFile": str(path)}]},
            _exporter({}))

    with pytest.raises(MarketplaceError) as e:
        save(junk)
    assert str(e.value) == "entry 0: not a valid .autowright archive"
    with pytest.raises(MarketplaceError) as e:
        save(tmp_path / "gone.autowright")
    assert str(e.value).startswith("entry 0: there's no file at ")
    plain = tmp_path / "notes.txt"
    plain.write_text("hello", encoding="utf-8")
    with pytest.raises(MarketplaceError) as e:
        save(plain)
    assert str(e.value) == "entry 0: notes.txt isn't an .autowright file"
    assert sorted(p.name for p in folder.iterdir()) == [marketplace.CATALOG_FILENAME]


def test_removing_an_entry_leaves_its_archive_file(market, tmp_path):
    """§22.7: the folder is the user's - a removed entry's archive stays."""
    folder, source = _shelf(market, tmp_path)
    market.save_catalog(source["id"], {
        "name": "Shelf", "description": "", "url": "",
        "entries": [{"title": "One", "description": "", "automationId": "a1"}]},
        _exporter({"a1": ("One", b"archive-bytes")}))
    assert (folder / "One.autowright").is_file()
    saved = market.save_catalog(source["id"], {
        "name": "Shelf", "description": "", "url": "", "entries": []}, _exporter({}))
    assert saved["entries"] == []
    assert (folder / "One.autowright").read_bytes() == b"archive-bytes"
    written = yaml.safe_load((folder / marketplace.CATALOG_FILENAME).read_text(encoding="utf-8"))
    assert written["entries"] == []


# ---------- §22.4 authoring routes ----------
def test_catalog_create_route(client, tmp_path):
    folder = tmp_path / "shelf"
    folder.mkdir()
    r = client.post("/marketplace/catalogs", json={"folder": str(folder)})
    assert r.status_code == 200
    source = r.json()
    assert source["kind"] == "file" and source["name"] == "shelf"
    assert source["origin"] == str(folder / marketplace.CATALOG_FILENAME)
    assert client.get("/marketplace").json()["sources"] == [source]

    r = client.post("/marketplace/catalogs", json={"folder": str(folder)})
    assert r.status_code == 409 and r.json()["detail"] == marketplace.FOLDER_TAKEN
    r = client.post("/marketplace/catalogs", json={"folder": str(tmp_path / "nothing")})
    assert r.status_code == 422
    assert client.post("/marketplace/catalogs",
                       json={"folder": "shelves/mine"}).status_code == 422
    assert client.post("/marketplace/catalogs", json={}).status_code == 422


def test_catalog_read_route(client, tmp_path, monkeypatch):
    f = write_catalog(tmp_path, [{"title": "One", "description": "Runs daily",
                                  "path": "one.autowright"}], name="Shelf")
    source = client.post("/marketplace/sources", json={"path": str(f)}).json()
    r = client.get(f"/marketplace/sources/{source['id']}/catalog")
    assert r.status_code == 200
    assert r.json() == {"name": "Shelf", "description": "", "url": None,
                        "entries": [{"index": 0, "title": "One", "description": "Runs daily",
                                     "path": "one.autowright", "image": ""}]}
    assert client.get("/marketplace/sources/nope/catalog").status_code == 404

    # §22.7: a `url` source's catalog isn't on this machine
    monkeypatch.setattr(
        marketplace, "_fetch_url",
        lambda url, *, cap, deadline_s=60: catalog_text(
            [{"title": "Web", "path": "w.autowright"}], name="Linked").encode())
    linked = client.post("/marketplace/sources",
                         json={"url": "https://x.test/marketplace.yaml"}).json()
    r = client.get(f"/marketplace/sources/{linked['id']}/catalog")
    assert r.status_code == 409 and r.json()["detail"] == marketplace.NOT_EDITABLE
    r = client.put(f"/marketplace/sources/{linked['id']}/catalog",
                   json={"name": "Linked", "description": "", "url": "", "entries": []})
    assert r.status_code == 409 and r.json()["detail"] == marketplace.NOT_EDITABLE
    assert client.put("/marketplace/sources/nope/catalog",
                      json={"name": "", "description": "", "url": "",
                            "entries": []}).status_code == 404


def test_catalog_save_route_exports_without_parameter_values(client, tmp_path):
    """§22.7 step 2: a marketplace archive is for other people - the route
    exports with the §5.1 `--no-values` rule, so no parameter value travels."""
    from autowright.storage import store

    a = store.create_automation(make_version(), name="Watcher", agent_id="mock")
    store.patch_automation(a, {"paramValues": {"greeting": "super-secret-value"}})
    folder = tmp_path / "shelf"
    folder.mkdir()
    source = client.post("/marketplace/catalogs", json={"folder": str(folder)}).json()

    r = client.put(f"/marketplace/sources/{source['id']}/catalog", json={
        "name": "Shelf", "description": "", "url": "",
        "entries": [{"title": "Watcher", "description": "Watches things",
                     "automationId": a["id"]}]})
    assert r.status_code == 200
    archive = folder / "Watcher.autowright"
    data = archive.read_bytes()
    manifest = yaml.safe_load(zipfile.ZipFile(io.BytesIO(data)).read("manifest.yaml"))
    assert "param_values" not in manifest
    assert b"super-secret-value" not in data
    assert r.json()["entries"] == [{"index": 0, "title": "Watcher",
                                    "description": "Watches things",
                                    "archive": str(archive), "image": False}]
    # §22.7: an unknown automation is the entry's 422, with nothing written
    r = client.put(f"/marketplace/sources/{source['id']}/catalog", json={
        "name": "Shelf", "description": "", "url": "",
        "entries": [{"title": "Gone", "description": "", "automationId": "nope"}]})
    assert r.status_code == 422 and r.json()["detail"] == "entry 0: no automation has that id"


def test_catalog_save_route_copies_and_validates_an_archive_file(client, tmp_path):
    """§22.7 step 2: an `archiveFile` is read, validated like an import would,
    and copied in beside the catalog under its own base name."""
    archive = _export(client)
    picked = tmp_path / "shared.autowright"
    picked.write_bytes(archive)
    folder = tmp_path / "shelf"
    folder.mkdir()
    source = client.post("/marketplace/catalogs", json={"folder": str(folder)}).json()

    r = client.put(f"/marketplace/sources/{source['id']}/catalog", json={
        "name": "Shelf", "description": "", "url": "",
        "entries": [{"title": "Shared", "description": "", "archiveFile": str(picked)}]})
    assert r.status_code == 200
    assert (folder / "shared.autowright").read_bytes() == archive
    assert r.json()["entries"][0]["archive"] == str(folder / "shared.autowright")

    junk = tmp_path / "junk.autowright"
    junk.write_bytes(b"not a zip at all")
    r = client.put(f"/marketplace/sources/{source['id']}/catalog", json={
        "name": "Shelf", "description": "", "url": "",
        "entries": [{"title": "Shared", "description": "", "path": "shared.autowright"},
                    {"title": "Junk", "description": "", "archiveFile": str(junk)}]})
    assert r.status_code == 422
    assert r.json()["detail"] == "entry 1: not a valid .autowright archive"
    assert sorted(p.name for p in folder.iterdir()) == [marketplace.CATALOG_FILENAME,
                                                        "shared.autowright"]
