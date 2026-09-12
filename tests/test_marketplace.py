"""Marketplace (§22): catalog validation, the two reference forms, the catalog
table's locations/refresh/settings, images read on demand, and the §22.4 routes
with the network stubbed."""
import io
import threading
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
    """A catalog file on this machine, listing archives and images by absolute
    path or https link (§22.1)."""
    f = tmp_path / filename
    f.write_text(catalog_text(entries, **top), encoding="utf-8")
    return f


# §22.2: a link location - what a refresh of a `url`-kind row downloads.
CATALOG_URL = "https://x.test/shelf/marketplace-catalog.yaml"


def serve(monkeypatch, answers) -> list:
    """§22.1 download: what each https reference answers with - bytes, or an
    exception to raise. Returns the list of urls asked for, in order."""
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
                                  location="/m/marketplace.yaml")
    assert "format 1" in str(e.value)
    # unknown keys at any level are ignored so the format can grow in place -
    # the `url` key an older draft carried is one of them (§22.1)
    m = marketplace.parse_catalog(
        catalog_text([{"title": "T", "path": "https://x.test/a.autowright",
                       "future": 1}], future="x", url=CATALOG_URL),
        location="/m/marketplace.yaml")
    assert m["entries"][0]["title"] == "T"
    assert "url" not in m


def test_name_defaults_to_the_locations_stem():
    m = marketplace.parse_catalog(catalog_text([]), location="/m/marketplace.yaml")
    assert m["name"] == "marketplace"
    m = marketplace.parse_catalog(catalog_text([]),
                                  location="https://x.test/lists/mine.yaml")
    assert m["name"] == "mine"
    # a blank name is the same as none; a given one is stripped
    m = marketplace.parse_catalog(catalog_text([], name="  Community  "),
                                  location="/m/community.yaml")
    assert m["name"] == "Community"
    m = marketplace.parse_catalog(catalog_text([], name="   "),
                                  location="/m/community.yaml")
    assert m["name"] == "community"


def test_the_canonical_stem_names_the_folder_or_the_host():
    """§22.1: `marketplace-catalog` is every catalog's stem, so it would name
    them all alike - a path location falls back to its folder's name, a link
    location to its host name. A catalog the app keeps is "My catalog"."""
    assert marketplace.default_name(
        "/shelves/community/marketplace-catalog.yaml") == "community"
    # a catalog at the filesystem root has no folder name to take
    assert marketplace.default_name("/marketplace-catalog.yaml") == "marketplace"
    assert marketplace.default_name(
        "https://x.test/lists/marketplace-catalog.yaml") == "x.test"
    # any other stem still names the catalog
    assert marketplace.default_name("/shelves/shelf.yaml") == "shelf"
    assert marketplace.default_name(None) == marketplace.KEPT_NAME
    assert marketplace.parse_catalog(catalog_text([]))["name"] == "My catalog"


def test_string_limits_reject_rather_than_truncate():
    for top, limit in (("name", marketplace.MAX_NAME),
                       ("description", marketplace.MAX_DESCRIPTION)):
        with pytest.raises(MarketplaceError) as e:
            marketplace.parse_catalog(catalog_text([], **{top: "x" * (limit + 1)}),
                                      location="/m/marketplace.yaml")
        assert f"{limit} characters" in str(e.value)


def test_entry_rules_name_the_entry_index():
    def parse(entries):
        return marketplace.parse_catalog(catalog_text(entries),
                                         location="/m/marketplace.yaml")

    ok = {"title": "Fine", "path": "https://x.test/a.autowright"}
    with pytest.raises(MarketplaceError) as e:
        parse([ok, ok, {"path": "https://x.test/b.autowright"}])
    assert str(e.value).startswith("entry 2: ")
    with pytest.raises(MarketplaceError) as e:
        parse([ok, {"title": "No archive", "path": "https://x.test/b.zip"}])
    assert str(e.value) == "entry 1: `path` must name an .autowright file"
    with pytest.raises(MarketplaceError) as e:
        parse([{"title": "Bad image", "path": "https://x.test/b.autowright",
                "image": "https://x.test/b.bmp"}])
    assert str(e.value).startswith("entry 0: `image` must name a ")
    with pytest.raises(MarketplaceError) as e:
        parse([{"title": "x" * (marketplace.MAX_TITLE + 1),
                "path": "https://x.test/b.autowright"}])
    assert str(e.value).startswith("entry 0: `title` is longer than ")
    # a query string is dropped before the extension is read
    assert parse([{"title": "T", "path": "https://x.test/a.autowright?raw=1",
                   "image": "https://x.test/i.PNG?v=2"}])["entries"][0]["index"] == 0


def test_entries_cap_and_shape():
    with pytest.raises(MarketplaceError) as e:
        marketplace.parse_catalog(
            catalog_text([{"title": "T", "path": "https://x.test/a.autowright"}]
                         * (marketplace.MAX_ENTRIES + 1)),
            location="/m/marketplace.yaml")
    assert "more than 200" in str(e.value)
    with pytest.raises(MarketplaceError):
        marketplace.parse_catalog("format_version: 1\n", location="/m/marketplace.yaml")
    with pytest.raises(MarketplaceError):
        marketplace.parse_catalog("- a\n- b\n", location="/m/marketplace.yaml")
    with pytest.raises(MarketplaceError):
        marketplace.parse_catalog("format_version: 1\nentries: [1, 2]\n",
                                  location="/m/marketplace.yaml")


# ---------- §22.1 references ----------
def test_a_reference_is_an_https_link_or_an_absolute_path(tmp_path):
    """§22.1: a `path` or `image` is exactly one of two forms, taken as
    written - nothing is ever resolved against where the catalog came from, and
    a catalog may mix them. Anything else is rejected naming the entry."""
    archive = str(tmp_path / "manga.autowright")
    assert marketplace.is_reference("https://x.test/manga.autowright")
    assert marketplace.is_reference(archive)
    for bad in ("manga.autowright", "a/manga.autowright", "../x.autowright",
                "http://x.test/m.autowright", "file:///x.autowright", "~/x.autowright"):
        assert not marketplace.is_reference(bad)

    def parse(entry):
        return marketplace.parse_catalog(catalog_text([entry]),
                                         location="/m/marketplace.yaml")

    entry = parse({"title": "Manga", "path": archive,
                   "image": "https://x.test/cover.png"})["entries"][0]
    assert entry["path"] == archive and entry["image"] == "https://x.test/cover.png"
    image = str(tmp_path / "cover.png")
    entry = parse({"title": "Manga", "path": "https://x.test/manga.autowright",
                   "image": image})["entries"][0]
    assert entry["path"] == "https://x.test/manga.autowright" and entry["image"] == image

    for bad in ("manga.autowright", "a/manga.autowright", "../x.autowright",
                "http://x.test/m.autowright", "file:///x.autowright", "~/x.autowright"):
        with pytest.raises(MarketplaceError) as e:
            parse({"title": "Manga", "path": bad})
        assert str(e.value) == \
            "entry 0: `path` must be an https link or an absolute path"
        with pytest.raises(MarketplaceError) as e:
            parse({"title": "Manga", "path": archive, "image": bad + ".png"})
        assert str(e.value) == \
            "entry 0: `image` must be an https link or an absolute path"


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


# ---------- §22.2 locations ----------
def test_a_location_is_blank_a_link_or_an_absolute_path(tmp_path, monkeypatch):
    """§22.2/§22.4: the location a user types - blank is `null`, an https link
    is kept as pasted, a path has `~` expanded and must be absolute."""
    assert marketplace.normalize_location("") is None
    assert marketplace.normalize_location(None) is None
    assert marketplace.normalize_location("   ") is None
    assert marketplace.normalize_location("  https://x.test/m.yaml  ") == \
        "https://x.test/m.yaml"
    assert marketplace.normalize_location(str(tmp_path / "m.yaml")) == \
        str(tmp_path / "m.yaml")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert marketplace.normalize_location("~/m.yaml") == str(tmp_path / "m.yaml")
    for bad in ("m.yaml", "a/m.yaml", "http://x.test/m.yaml", "file:///m.yaml"):
        with pytest.raises(MarketplaceError) as e:
            marketplace.normalize_location(bad)
        assert str(e.value) == marketplace.BAD_LOCATION
    # §22.2: `kind` is derived from the location, never stored
    assert marketplace.kind_of(None) == "none"
    assert marketplace.kind_of("https://x.test/m.yaml") == "url"
    assert marketplace.kind_of(str(tmp_path / "m.yaml")) == "file"


# ---------- §22.2 catalog table ----------
def test_add_by_path_copies_the_catalog(market, tmp_path):
    (tmp_path / "manga.autowright").write_bytes(b"zip")
    (tmp_path / "cover.png").write_bytes(PNG)
    f = write_catalog(tmp_path, [{"title": "Manga", "description": "Checks it",
                                  "path": str(tmp_path / "manga.autowright"),
                                  "image": str(tmp_path / "cover.png")}],
                      name="Mine")
    source = market.add(path=str(f))
    assert source["name"] == "Mine" and source["kind"] == "file"
    assert source["location"] == str(f)
    assert source["shown"] is True and source["autoRefresh"] is False
    assert source["error"] is None and source["refreshedAt"] and source["cached"] is True
    assert source["entries"] == [{"index": 0, "title": "Manga", "description": "Checks it",
                                  "archive": str(tmp_path / "manga.autowright"),
                                  "image": True}]
    copy = market.catalog_file(source["id"])
    assert copy.read_text(encoding="utf-8") == f.read_text(encoding="utf-8")
    # §22.2: nothing else ever lives in the row's directory - no image cache
    assert [p.name for p in market.source_dir(source["id"]).iterdir()] == \
        [marketplace.CATALOG_FILENAME]
    # §22.2: the row is on disk under the §5 root, derived fields are not
    stored = yaml.safe_load((paths.marketplace_dir() / "marketplaces.yaml").read_text())
    assert list(stored["sources"][0]) == ["id", "location", "shown", "auto_refresh",
                                          "added_at", "refreshed_at", "error"]


def test_add_expands_a_leading_tilde(market, tmp_path, monkeypatch):
    """§22.4: a typed path may start with `~`; the row's location is the
    resolved absolute path."""
    monkeypatch.setenv("HOME", str(tmp_path))
    shelf = tmp_path / "shelf"
    shelf.mkdir()
    write_catalog(shelf, [], filename=marketplace.CATALOG_FILENAME)
    source = market.add(path=f"~/shelf/{marketplace.CATALOG_FILENAME}")
    assert source["location"] == str(shelf / marketplace.CATALOG_FILENAME)
    assert source["kind"] == "file" and source["name"] == "shelf"


def test_add_rejects_bad_locations_and_duplicates(market, tmp_path):
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
    with pytest.raises(marketplace.MarketplaceDuplicate) as e:
        market.add(path=str(tmp_path / "." / "marketplace.yaml"))  # compares resolved
    assert str(e.value) == marketplace.ALREADY_ADDED


def test_add_stores_nothing_when_validation_fails(market, tmp_path):
    f = tmp_path / "marketplace.yaml"
    f.write_text("format_version: 9\nentries: []\n", encoding="utf-8")
    with pytest.raises(MarketplaceError):
        market.add(path=str(f))
    assert market.sources == []
    assert not (paths.marketplace_dir() / "marketplaces.yaml").exists()
    # a reference that is neither an https link nor an absolute path is a
    # validation failure naming its entry (§22.1)
    bad = write_catalog(tmp_path, [{"title": "Scheme", "path": "file:///x.autowright"}],
                        filename="scheme.yaml")
    with pytest.raises(MarketplaceError) as e:
        market.add(path=str(bad))
    assert str(e.value).startswith("entry 0: ")
    assert market.sources == []


def test_refresh_rereads_the_location(market, tmp_path, monkeypatch):
    """§22.2: a refresh re-reads the row's location - a path is read, a link is
    downloaded."""
    f = write_catalog(tmp_path, [{"title": "One",
                                  "path": str(tmp_path / "one.autowright")}],
                      name="Shelf")
    source = market.add(path=str(f))
    f.write_text(catalog_text([{"title": "Two",
                                "path": str(tmp_path / "two.autowright")}],
                              name="Shelf"), encoding="utf-8")
    after = market.refresh(source["id"])
    assert [e["title"] for e in after["entries"]] == ["Two"]
    assert after["refreshedAt"] != source["refreshedAt"]
    assert market.catalog_file(source["id"]).read_text(encoding="utf-8") == \
        f.read_text(encoding="utf-8")

    # a link location downloads, and a refresh never fetches the images
    served = catalog_text([{"title": "Linked",
                            "path": "https://x.test/shelf/a.autowright",
                            "image": "https://x.test/shelf/cover.png"}], name="Linked")
    asked = serve(monkeypatch, {CATALOG_URL: served.encode()})
    linked = market.add(url=CATALOG_URL)
    assert asked == [CATALOG_URL]
    assert linked["kind"] == "url" and linked["location"] == CATALOG_URL
    assert linked["entries"][0]["image"] is True
    market.refresh(linked["id"])
    assert asked == [CATALOG_URL, CATALOG_URL]


def test_refresh_keeps_the_copy_and_records_the_error(market, tmp_path):
    f = write_catalog(tmp_path, [{"title": "One",
                                  "path": str(tmp_path / "one.autowright")}])
    source = market.add(path=str(f))
    first_refreshed = source["refreshedAt"]
    f.write_text("format_version: 1\nentries: [oops\n", encoding="utf-8")
    after = market.refresh(source["id"])
    assert after["error"] and "YAML" in after["error"]
    assert after["refreshedAt"] == first_refreshed          # last *successful* read
    assert [e["title"] for e in after["entries"]] == ["One"]  # last good copy
    assert after["cached"] is True and after["location"] == str(f)
    # a success clears the error and stamps a new time
    f.write_text(catalog_text([{"title": "Two",
                                "path": str(tmp_path / "two.autowright")}]),
                 encoding="utf-8")
    fixed = market.refresh(source["id"])
    assert fixed["error"] is None
    assert [e["title"] for e in fixed["entries"]] == ["Two"]


def test_a_null_location_is_not_refreshable(market, tmp_path):
    """§22.2: the app's copy is the only copy - a single-row refresh refuses
    with the row untouched, a refresh-all skips it and still lists it."""
    kept = market.create_catalog(None)
    folder = tmp_path / "shelf"
    folder.mkdir()
    linked = market.create_catalog(str(folder))
    assert kept["location"] is None and kept["kind"] == "none"
    with pytest.raises(marketplace.MarketplaceNotRefreshable) as e:
        market.refresh(kept["id"])
    assert str(e.value) == marketplace.NOT_REFRESHABLE
    assert market.sources[0]["refreshed_at"] is None
    assert market.sources[0]["error"] is None

    sources = market.refresh_all()
    assert [s["id"] for s in sources] == [kept["id"], linked["id"]]
    assert sources[0]["refreshedAt"] is None and sources[0]["error"] is None
    assert sources[1]["refreshedAt"] != linked["refreshedAt"]


def test_refresh_all_never_stops_at_the_first_bad_row(market, tmp_path, monkeypatch):
    good_dir, bad_dir = tmp_path / "good", tmp_path / "bad"
    good_dir.mkdir()
    bad_dir.mkdir()
    good = write_catalog(good_dir, [{"title": "Good",
                                     "path": str(good_dir / "g.autowright")}])
    bad_url = "https://x.test/bad/marketplace-catalog.yaml"
    serve(monkeypatch, {bad_url: catalog_text(
        [{"title": "Bad", "path": "https://x.test/bad/b.autowright"}]).encode()})
    market.add(path=str(good))
    bad_source = market.add(url=bad_url)
    serve(monkeypatch, {bad_url: MarketplaceError(
        "download failed - the server answered 404")})
    sources = market.refresh_all()
    assert sources[0]["error"] is None
    assert sources[1]["id"] == bad_source["id"] and sources[1]["error"]
    assert [e["title"] for e in sources[1]["entries"]] == ["Bad"]


def test_settings_change_the_location_shown_and_auto_refresh(market, tmp_path):
    """§22.2 settings: only the given fields change and nothing is fetched -
    the copy stays until the next refresh reads the new place."""
    first = write_catalog(tmp_path, [{"title": "One",
                                      "path": str(tmp_path / "one.autowright")}],
                          filename="first.yaml")
    second = write_catalog(tmp_path, [{"title": "Two",
                                       "path": str(tmp_path / "two.autowright")}],
                           filename="second.yaml")
    source = market.add(path=str(first))
    moved = market.update_settings(source["id"], location=str(second), shown=False)
    assert moved["location"] == str(second) and moved["shown"] is False
    assert [e["title"] for e in moved["entries"]] == ["One"]  # copy untouched
    assert [e["title"] for e in market.refresh(source["id"])["entries"]] == ["Two"]
    # the form check, and the duplicate rule against another row
    with pytest.raises(MarketplaceError) as e:
        market.update_settings(source["id"], location="shelves/mine.yaml")
    assert str(e.value) == marketplace.BAD_LOCATION
    other = market.add(path=str(first))
    with pytest.raises(marketplace.MarketplaceDuplicate) as e:
        market.update_settings(source["id"], location=str(first))
    assert str(e.value) == marketplace.ALREADY_ADDED
    assert market.update_settings(other["id"], location=str(first))["location"] == str(first)


def test_settings_rules_around_a_null_location(market, tmp_path):
    """§22.2: clearing the location keeps the copy and turns auto refresh off;
    auto refresh needs a location; a row with no readable copy can't be
    cleared, or there would be nothing left."""
    f = write_catalog(tmp_path, [{"title": "One",
                                  "path": str(tmp_path / "one.autowright")}],
                      name="Shelf")
    source = market.add(path=str(f))
    on = market.update_settings(source["id"], auto_refresh=True)
    assert on["autoRefresh"] is True
    cleared = market.update_settings(source["id"], location="")
    assert cleared["location"] is None and cleared["kind"] == "none"
    assert cleared["autoRefresh"] is False and cleared["error"] is None
    assert [e["title"] for e in cleared["entries"]] == ["One"]  # the copy stays
    with pytest.raises(MarketplaceError) as e:
        market.update_settings(source["id"], auto_refresh=True)
    assert str(e.value) == marketplace.AUTO_NEEDS_LOCATION
    # a path again makes the row refreshable
    back = market.update_settings(source["id"], location=str(f))
    assert back["kind"] == "file" and market.refresh(source["id"])["error"] is None
    # §22.2: nothing left to keep
    market.catalog_file(source["id"]).write_text("format_version: 9\n", encoding="utf-8")
    with pytest.raises(MarketplaceError) as e:
        market.update_settings(source["id"], location="")
    assert str(e.value) == marketplace.NO_COPY_TO_KEEP
    assert market.sources[0]["location"] == str(f)
    with pytest.raises(KeyError):
        market.update_settings("nope", shown=False)


def test_auto_refresh_sweeps_only_flagged_rows_with_a_location(market, tmp_path):
    """§22.2 auto refresh: the flagged rows with a location, in table order,
    through the ordinary refresh - a failure lands in `error` like a manual
    one."""
    flagged = write_catalog(tmp_path, [{"title": "One",
                                        "path": str(tmp_path / "one.autowright")}],
                            filename="flagged.yaml")
    plain = write_catalog(tmp_path, [{"title": "Plain",
                                      "path": str(tmp_path / "p.autowright")}],
                          filename="plain.yaml")
    kept = market.create_catalog(None)
    one = market.add(path=str(flagged))
    two = market.add(path=str(plain))
    market.update_settings(one["id"], auto_refresh=True)
    assert market.auto_refresh_sweep() == [one["id"]]
    # §22.2: a `null` location is never swept, flagged or not
    with pytest.raises(MarketplaceError):
        market.update_settings(kept["id"], auto_refresh=True)
    flagged.write_text("format_version: 1\nentries: [oops\n", encoding="utf-8")
    assert market.auto_refresh_sweep() == [one["id"]]
    assert market.serialize(market.sources[1])["error"]
    assert market.serialize(market.sources[2])["error"] is None
    assert [e["title"] for e in market.serialize(market.sources[1])["entries"]] == ["One"]
    assert two["id"] == market.sources[2]["id"]


def test_the_auto_refresh_thread_sweeps_and_reports_the_change(market, tmp_path,
                                                               monkeypatch):
    """§22.2: the sweep runs on a daemon thread off the boot path and calls
    back after a sweep that touched any row (the §19 event)."""
    monkeypatch.setattr(marketplace, "AUTO_REFRESH_DELAY_S", 0.01)
    f = write_catalog(tmp_path, [{"title": "One",
                                  "path": str(tmp_path / "one.autowright")}])
    source = market.add(path=str(f))
    market.update_settings(source["id"], auto_refresh=True)
    changed = threading.Event()
    market.start_auto_refresh(changed.set)
    try:
        assert changed.wait(5)
    finally:
        market.stop_auto_refresh()
    assert market.sources[0]["refreshed_at"] != source["refreshedAt"]


def test_an_unreadable_copy_still_lists_the_row(market, tmp_path):
    f = write_catalog(tmp_path, [{"title": "One",
                                  "path": str(tmp_path / "one.autowright")}],
                      name="Shelf")
    source = market.add(path=str(f))
    market.catalog_file(source["id"]).write_text("format_version: 9\n", encoding="utf-8")
    listed = market.serialize(market.sources[0])
    # §22.2: the row still lists, with zero entries and the location-derived name
    assert listed["entries"] == [] and listed["name"] == "marketplace"
    assert listed["error"] == marketplace.COPY_UNREADABLE_REFRESH
    assert listed["cached"] is False and listed["location"] == str(f)
    # a real stored refresh error wins: it says what actually happened
    f.write_text("format_version: 1\nentries: [oops\n", encoding="utf-8")
    failed = market.refresh(source["id"])
    assert "YAML" in failed["error"]
    assert market.serialize(market.sources[0])["error"] == failed["error"]
    # §22.2: a catalog the app keeps by itself has nothing to refresh from
    kept = market.create_catalog(None)
    market.catalog_file(kept["id"]).write_text("format_version: 9\n", encoding="utf-8")
    listed = market.serialize(market.sources[1])
    assert listed["error"] == marketplace.COPY_UNREADABLE_REMOVE
    assert listed["cached"] is False and listed["name"] == marketplace.KEPT_NAME


def test_remove_drops_the_row_and_its_directory(market, tmp_path):
    f = write_catalog(tmp_path, [])
    source = market.add(path=str(f))
    directory = market.source_dir(source["id"])
    assert directory.is_dir()
    market.remove(source["id"])
    assert market.sources == [] and not directory.exists()
    assert f.is_file()  # §22.2: the catalog file the row read is the user's
    with pytest.raises(KeyError):
        market.remove(source["id"])


def test_sources_yaml_lenient_load(market, home, caplog):
    """§5 lenient load: a row missing `id`, or whose `location` isn't null, an
    absolute path, or an https link, skips with a warning; a missing `shown`
    reads true, a missing `auto_refresh` false, and the `kind`/`origin` keys an
    older draft wrote are simply ignored."""
    paths.marketplace_dir().mkdir(parents=True, exist_ok=True)
    (paths.marketplace_dir() / "marketplaces.yaml").write_text(
        "sources:\n"
        "- id: defaults\n"
        "  location: /m/marketplace.yaml\n"
        "  added_at: '2026-09-11T00:00:00.000000+00:00'\n"
        "- id: old-shape\n"
        "  kind: file\n"
        "  origin: /m/old.yaml\n"
        "  location: /m/old.yaml\n"
        "  shown: false\n"
        "  auto_refresh: true\n"
        "- id: kept\n"
        "  location: null\n"
        "  auto_refresh: true\n"
        "- location: /m/no-id.yaml\n"
        "- id: relative\n"
        "  location: shelves/mine.yaml\n"
        "- just a string\n", encoding="utf-8")
    with caplog.at_level("WARNING"):
        market.load()
    assert [s["id"] for s in market.sources] == ["defaults", "old-shape", "kept"]
    assert market.sources[0]["shown"] is True
    assert market.sources[0]["auto_refresh"] is False
    assert market.sources[0]["refreshed_at"] is None
    assert market.sources[1]["shown"] is False
    assert market.sources[1]["auto_refresh"] is True
    assert list(market.sources[1]) == ["id", "location", "shown", "auto_refresh",
                                       "added_at", "refreshed_at", "error"]
    # §22.2: auto refresh is meaningless, and kept false, without a location
    assert market.sources[2]["location"] is None
    assert market.sources[2]["auto_refresh"] is False
    assert len(caplog.records) == 3
    # a file that isn't a mapping at all loads as no marketplaces, never raises
    (paths.marketplace_dir() / "marketplaces.yaml").write_text("- a\n- b\n", encoding="utf-8")
    market.load()
    assert market.sources == []


def test_entry_archive_rereads_the_copy_at_fetch_time(market, tmp_path):
    (tmp_path / "manga.autowright").write_bytes(b"archive-bytes")
    f = write_catalog(tmp_path, [{"title": "Manga",
                                  "path": str(tmp_path / "manga.autowright")}])
    source = market.add(path=str(f))
    data, reference = market.entry_archive(source["id"], 0)
    assert data == b"archive-bytes"
    assert reference == str(tmp_path / "manga.autowright")
    with pytest.raises(KeyError):
        market.entry_archive(source["id"], 7)
    with pytest.raises(KeyError):
        market.entry_archive("nope", 0)
    # the copy is re-read on every fetch, so a relative reference edited into
    # it since the refresh is still rejected
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


def test_images_are_read_on_demand_and_never_stored(market, tmp_path, monkeypatch):
    """§22.4: the entry's image is read by reference when the page asks - a
    path from disk, a link downloaded; nothing is written."""
    (tmp_path / "cover.png").write_bytes(PNG)
    f = write_catalog(tmp_path, [
        {"title": "On disk", "path": str(tmp_path / "a.autowright"),
         "image": str(tmp_path / "cover.png")},
        {"title": "At a link", "path": str(tmp_path / "b.autowright"),
         "image": "https://x.test/shelf/cover.PNG"},
        {"title": "Missing", "path": str(tmp_path / "c.autowright"),
         "image": str(tmp_path / "gone.png")},
        {"title": "None", "path": str(tmp_path / "d.autowright")},
    ])
    source = market.add(path=str(f))
    assert [e["image"] for e in source["entries"]] == [True, True, True, False]
    assert market.image_bytes(source["id"], 0) == (PNG, ".png")
    asked = serve(monkeypatch, {"https://x.test/shelf/cover.PNG": PNG})
    assert market.image_bytes(source["id"], 1) == (PNG, ".png")
    assert asked == ["https://x.test/shelf/cover.PNG"]
    with pytest.raises(MarketplaceError) as e:
        market.image_bytes(source["id"], 2)
    assert "couldn't read the image" in str(e.value)
    assert market.image_bytes(source["id"], 3) is None
    with pytest.raises(KeyError):
        market.image_bytes(source["id"], 9)
    with pytest.raises(KeyError):
        market.image_bytes("nope", 0)
    # nothing but the catalog copy ever lands under the row's directory
    assert [p.name for p in market.source_dir(source["id"]).iterdir()] == \
        [marketplace.CATALOG_FILENAME]


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


def test_routes_add_list_refresh_and_remove(client, tmp_path):
    (tmp_path / "one.autowright").write_bytes(b"zip")
    (tmp_path / "cover.png").write_bytes(PNG)
    f = write_catalog(tmp_path, [{"title": "One",
                                  "path": str(tmp_path / "one.autowright"),
                                  "image": str(tmp_path / "cover.png")}], name="Shelf")
    r = client.post("/marketplace/sources", json={"path": str(f)})
    assert r.status_code == 200
    source = r.json()
    assert source["name"] == "Shelf" and source["entries"][0]["image"] is True
    assert source["kind"] == "file" and source["location"] == str(f)

    assert client.get("/marketplace").json()["sources"] == [source]
    # §22.4 add rules
    assert client.post("/marketplace/sources", json={}).status_code == 422
    assert client.post("/marketplace/sources",
                       json={"url": "https://x.test/m.yaml",
                             "path": str(f)}).status_code == 422
    r = client.post("/marketplace/sources", json={"url": "http://x.test/m.yaml"})
    assert r.status_code == 422 and "https" in r.json()["detail"]
    r = client.post("/marketplace/sources", json={"path": str(f)})
    assert r.status_code == 409 and r.json()["detail"] == marketplace.ALREADY_ADDED

    # §22.4 refresh: 200 with the reason even when it failed
    f.write_text("format_version: 1\nentries: [oops\n", encoding="utf-8")
    r = client.post(f"/marketplace/sources/{source['id']}/refresh")
    assert r.status_code == 200 and r.json()["error"]
    assert [e["title"] for e in r.json()["entries"]] == ["One"]
    r = client.post("/marketplace/refresh")
    assert r.status_code == 200 and r.json()["sources"][0]["error"]
    assert client.post("/marketplace/sources/nope/refresh").status_code == 404

    assert client.delete(f"/marketplace/sources/{source['id']}").json() == {"ok": True}
    assert client.delete(f"/marketplace/sources/{source['id']}").status_code == 404
    assert client.get("/marketplace").json()["sources"] == []


def test_settings_route(client, tmp_path):
    """§22.4 PATCH: every field optional, 422 for a bad location or auto
    refresh without one, 409 for a location another row holds, 404 unknown."""
    first = write_catalog(tmp_path, [{"title": "One",
                                      "path": str(tmp_path / "one.autowright")}],
                          filename="first.yaml")
    second = write_catalog(tmp_path, [], filename="second.yaml")
    source = client.post("/marketplace/sources", json={"path": str(first)}).json()
    other = client.post("/marketplace/sources", json={"path": str(second)}).json()

    r = client.patch(f"/marketplace/sources/{source['id']}",
                     json={"shown": False, "autoRefresh": True})
    assert r.status_code == 200
    assert r.json()["shown"] is False and r.json()["autoRefresh"] is True
    # §22.2: clearing the location keeps the copy and turns auto refresh off
    r = client.patch(f"/marketplace/sources/{source['id']}", json={"location": "  "})
    assert r.status_code == 200
    assert r.json()["location"] is None and r.json()["kind"] == "none"
    assert r.json()["autoRefresh"] is False
    assert [e["title"] for e in r.json()["entries"]] == ["One"]

    r = client.patch(f"/marketplace/sources/{source['id']}", json={"autoRefresh": True})
    assert r.status_code == 422 and r.json()["detail"] == marketplace.AUTO_NEEDS_LOCATION
    r = client.patch(f"/marketplace/sources/{source['id']}",
                     json={"location": "shelves/mine.yaml"})
    assert r.status_code == 422 and r.json()["detail"] == marketplace.BAD_LOCATION
    r = client.patch(f"/marketplace/sources/{source['id']}",
                     json={"location": str(second)})
    assert r.status_code == 409 and r.json()["detail"] == marketplace.ALREADY_ADDED
    assert client.patch("/marketplace/sources/nope", json={"shown": True}).status_code == 404
    assert client.patch(f"/marketplace/sources/{other['id']}",
                        json={"shown": "no"}).status_code == 422
    # §22.2: a row whose copy can't be read has nothing to keep
    from autowright.api import marketplace_store

    marketplace_store.catalog_file(other["id"]).write_text("format_version: 9\n",
                                                           encoding="utf-8")
    r = client.patch(f"/marketplace/sources/{other['id']}", json={"location": ""})
    assert r.status_code == 422 and r.json()["detail"] == marketplace.NO_COPY_TO_KEEP


def test_route_refuses_to_refresh_a_null_location(client, tmp_path):
    """§22.4: a catalog the app keeps by itself answers 409 with the row
    untouched, and a refresh-all lists it as it was."""
    from autowright.api import marketplace_store

    source = client.post("/marketplace/catalogs", json={}).json()
    assert source["location"] is None
    row = dict(marketplace_store.sources[0])

    r = client.post(f"/marketplace/sources/{source['id']}/refresh")
    assert r.status_code == 409 and r.json()["detail"] == marketplace.NOT_REFRESHABLE
    assert marketplace_store.sources[0] == row

    r = client.post("/marketplace/refresh")
    assert r.status_code == 200
    listed = r.json()["sources"]
    assert [s["id"] for s in listed] == [source["id"]]
    assert listed[0]["refreshedAt"] is None and listed[0]["error"] is None


def test_image_route_reads_the_reference_on_demand(client, tmp_path, monkeypatch):
    """§22.4: 200 with the bytes and the reference's content type, 404 for no
    entry or no image, 502 when the reference can't be read."""
    (tmp_path / "cover.png").write_bytes(PNG)
    f = write_catalog(tmp_path, [{"title": "One",
                                  "path": str(tmp_path / "one.autowright"),
                                  "image": str(tmp_path / "cover.png")},
                                 {"title": "Two",
                                  "path": str(tmp_path / "two.autowright")},
                                 {"title": "Linked",
                                  "path": str(tmp_path / "three.autowright"),
                                  "image": "https://x.test/shelf/cover.jpg"},
                                 {"title": "Gone",
                                  "path": str(tmp_path / "four.autowright"),
                                  "image": str(tmp_path / "gone.png")}])
    source = client.post("/marketplace/sources", json={"path": str(f)}).json()
    r = client.get(f"/marketplace/sources/{source['id']}/entries/0/image")
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    serve(monkeypatch, {"https://x.test/shelf/cover.jpg": b"jpeg-bytes"})
    r = client.get(f"/marketplace/sources/{source['id']}/entries/2/image")
    assert r.status_code == 200 and r.content == b"jpeg-bytes"
    assert r.headers["content-type"] == "image/jpeg"
    assert client.get(
        f"/marketplace/sources/{source['id']}/entries/1/image").status_code == 404
    assert client.get(
        f"/marketplace/sources/{source['id']}/entries/9/image").status_code == 404
    assert client.get("/marketplace/sources/nope/entries/0/image").status_code == 404
    r = client.get(f"/marketplace/sources/{source['id']}/entries/3/image")
    assert r.status_code == 502 and "couldn't read the image" in r.json()["detail"]


def test_entry_preview_yields_a_confirmable_token(client, tmp_path):
    from autowright.storage import store

    archive = _export(client)
    (tmp_path / "shared.autowright").write_bytes(archive)
    f = write_catalog(tmp_path, [{"title": "Shared",
                                  "path": str(tmp_path / "shared.autowright")}],
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
    """A link location through the §22.4 route, with the network stubbed."""
    monkeypatch.setattr(
        marketplace, "_fetch_url",
        lambda url, *, cap, deadline_s=60: catalog_text(
            [{"title": "Web", "path": "https://x.test/w.autowright"}],
            name="Linked").encode())
    r = client.post("/marketplace/sources", json={"url": "https://x.test/marketplace.yaml"})
    assert r.status_code == 200
    source = r.json()
    assert source["kind"] == "url" and source["name"] == "Linked"
    assert source["location"] == "https://x.test/marketplace.yaml"
    assert source["entries"][0]["archive"] == "https://x.test/w.autowright"
    assert client.get("/marketplace").json()["sources"][0]["id"] == source["id"]


# ---------- §22.7 catalog authoring ----------
def _shelf(market, tmp_path, name="shelf"):
    """§22.7 create: an empty catalog in its own folder, whose location is that
    file. Answers the folder and the served §22.4 Source."""
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


def test_create_writes_the_empty_catalog_into_the_folder(market, tmp_path):
    """§22.7: `format_version: 1`, the folder's name, no entries - and the
    row's location is the file just written."""
    folder, source = _shelf(market, tmp_path, "community")
    catalog = folder / marketplace.CATALOG_FILENAME
    written = yaml.safe_load(catalog.read_text(encoding="utf-8"))
    assert written == {"format_version": 1, "name": "community", "entries": []}
    assert source["kind"] == "file" and source["location"] == str(catalog)
    assert source["name"] == "community" and source["entries"] == []
    assert source["error"] is None and source["refreshedAt"]
    # the catalog is copied like any other added row
    assert market.catalog_file(source["id"]).read_text(encoding="utf-8") == \
        catalog.read_text(encoding="utf-8")


def test_create_without_a_folder_keeps_the_only_copy(market):
    """§22.7: no folder means the row's location is `null` and the catalog is
    written straight to the row's copy."""
    source = market.create_catalog(None)
    assert source["location"] is None and source["kind"] == "none"
    assert source["name"] == marketplace.KEPT_NAME and source["entries"] == []
    assert source["refreshedAt"] is None and source["addedAt"]
    assert source["error"] is None and source["cached"] is True
    written = yaml.safe_load(
        market.catalog_file(source["id"]).read_text(encoding="utf-8"))
    assert written == {"format_version": 1, "name": marketplace.KEPT_NAME, "entries": []}
    with pytest.raises(marketplace.MarketplaceNotRefreshable):
        market.refresh(source["id"])


def test_create_refuses_a_taken_folder_and_a_bad_path(market, tmp_path):
    folder, source = _shelf(market, tmp_path)
    with pytest.raises(marketplace.MarketplaceDuplicate) as e:
        market.create_catalog(str(folder))
    assert str(e.value) == marketplace.FOLDER_TAKEN
    assert len(market.sources) == 1
    with pytest.raises(MarketplaceError):
        market.create_catalog(str(tmp_path / "nothing"))
    with pytest.raises(MarketplaceError):
        market.create_catalog("shelves/mine")
    # §22.4: the catalog path is already a row's location
    (folder / marketplace.CATALOG_FILENAME).unlink()
    with pytest.raises(marketplace.MarketplaceDuplicate) as e:
        market.create_catalog(str(folder))
    assert str(e.value) == marketplace.ALREADY_ADDED
    assert len(market.sources) == 1


def test_create_writes_the_editors_content(market, tmp_path):
    """§22.7: the create body is a save's - the exported archive lands beside
    the catalog, listed by its absolute path, and the new row shows it."""
    folder = tmp_path / "shelf"
    folder.mkdir()
    calls: list = []
    source = market.create_catalog(str(folder), {
        "name": "Shelf", "description": "What I run.",
        "entries": [{"title": "First", "description": "Runs daily",
                     "automationId": "a1"}]},
        _exporter({"a1": ("Daily/Report", b"archive-bytes")}, calls))
    assert calls == ["a1"]
    archive = folder / "Daily Report.autowright"
    assert archive.read_bytes() == b"archive-bytes"
    written = yaml.safe_load((folder / marketplace.CATALOG_FILENAME).read_text(encoding="utf-8"))
    assert written["name"] == "Shelf" and written["description"] == "What I run."
    assert written["entries"] == [{"title": "First", "description": "Runs daily",
                                   "path": str(archive)}]
    assert source["kind"] == "file" and source["name"] == "Shelf"
    assert source["location"] == str(folder / marketplace.CATALOG_FILENAME)
    assert source["entries"] == [{"index": 0, "title": "First", "description": "Runs daily",
                                  "archive": str(archive), "image": False}]


def test_create_writes_nothing_when_an_entry_fails(market, tmp_path):
    """§22.7: the same check-everything-first rule a save runs - a folder that
    failed holds neither a catalog nor an export, and no row was added."""
    folder = tmp_path / "shelf"
    folder.mkdir()

    def create(entries):
        return market.create_catalog(str(folder), {
            "name": "Shelf", "description": "", "entries": entries},
            _exporter({"a1": ("Fine", b"archive-bytes")}))

    with pytest.raises(MarketplaceError) as e:
        create([{"title": "Fine", "description": "", "automationId": "a1"},
                {"title": "Gone", "description": "", "automationId": "deleted"}])
    assert str(e.value) == "entry 1: no automation has that id"
    with pytest.raises(MarketplaceError) as e:
        create([{"title": "  ", "description": "", "automationId": "a1"}])
    assert str(e.value) == "entry 0: it has no title"
    assert list(folder.iterdir()) == []
    assert market.sources == []


def test_read_catalog_answers_the_file_as_written(market, tmp_path, monkeypatch):
    """§22.7 GET: the file at the location (or the copy for `null`), references
    unresolved - a link location has no file on this machine to edit."""
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "cover.png").write_bytes(PNG)
    archive = str(tmp_path / "manga.autowright")
    image = str(tmp_path / "img" / "cover.png")
    f = write_catalog(tmp_path, [{"title": "Manga", "description": "Checks it",
                                  "path": archive, "image": image}],
                      name="Mine", description="Automations I use.")
    source = market.add(path=str(f))
    assert market.read_catalog(source["id"]) == {
        "name": "Mine", "description": "Automations I use.",
        "entries": [{"index": 0, "title": "Manga", "description": "Checks it",
                     "path": archive, "image": image}]}
    # a catalog the app keeps: the copy is what the editor reads
    kept = market.create_catalog(None)
    assert market.read_catalog(kept["id"]) == {
        "name": marketplace.KEPT_NAME, "description": "", "entries": []}

    serve(monkeypatch, {CATALOG_URL: catalog_text(
        [{"title": "Web", "path": "https://x.test/w.autowright"}],
        name="Linked").encode()})
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
        "name": "Shelf", "description": "What I run.",
        "entries": [{"title": "First", "description": "Runs daily", "automationId": "a1"},
                    {"title": "Second", "description": "", "automationId": "a1"}]}, export)
    assert calls == ["a1", "a1"]
    assert (folder / "Daily Report.autowright").read_bytes() == b"archive-bytes"
    assert (folder / "Daily Report 2.autowright").read_bytes() == b"archive-bytes"
    written = yaml.safe_load((folder / marketplace.CATALOG_FILENAME).read_text(encoding="utf-8"))
    assert written["name"] == "Shelf" and written["description"] == "What I run."
    assert [e["path"] for e in written["entries"]] == [
        str(folder / "Daily Report.autowright"),
        str(folder / "Daily Report 2.autowright")]
    # §22.7 step 6: the row's copy is refreshed from the file just written
    assert saved["name"] == "Shelf"
    assert saved["entries"] == [
        {"index": 0, "title": "First", "description": "Runs daily",
         "archive": str(folder / "Daily Report.autowright"), "image": False},
        {"index": 1, "title": "Second", "description": "",
         "archive": str(folder / "Daily Report 2.autowright"), "image": False}]
    assert saved["refreshedAt"] != before
    assert market.catalog_file(source["id"]).read_text(encoding="utf-8") == \
        (folder / marketplace.CATALOG_FILENAME).read_text(encoding="utf-8")


def test_save_exports_into_the_export_folder_for_a_kept_catalog(market, tmp_path):
    """§22.7 step 1: a catalog with no location has no folder of its own - the
    body's `exportFolder` says where the automations go, and without one the
    save is refused."""
    source = market.create_catalog(None)
    export = _exporter({"a1": ("Watcher", b"archive-bytes")})
    entries = [{"title": "Watcher", "description": "", "automationId": "a1"}]
    with pytest.raises(MarketplaceError) as e:
        market.save_catalog(source["id"], {"name": "Mine", "description": "",
                                           "entries": entries}, export)
    assert str(e.value) == marketplace.NO_EXPORT_FOLDER
    for bad in ("shelves/mine", str(tmp_path / "nothing")):
        with pytest.raises(MarketplaceError) as e:
            market.save_catalog(source["id"], {"name": "Mine", "description": "",
                                               "exportFolder": bad,
                                               "entries": entries}, export)
        assert "export folder" in str(e.value)
    out = tmp_path / "exports"
    out.mkdir()
    saved = market.save_catalog(source["id"], {"name": "Mine", "description": "",
                                               "exportFolder": str(out),
                                               "entries": entries}, export)
    assert (out / "Watcher.autowright").read_bytes() == b"archive-bytes"
    assert saved["location"] is None and saved["name"] == "Mine"
    assert saved["entries"][0]["archive"] == str(out / "Watcher.autowright")
    # §22.7 step 5: the row's copy is the catalog
    written = yaml.safe_load(
        market.catalog_file(source["id"]).read_text(encoding="utf-8"))
    assert written["entries"][0]["path"] == str(out / "Watcher.autowright")
    # §22.7 step 1: the folder is only needed when an entry is an automation
    kept = market.save_catalog(source["id"], {
        "name": "Mine", "description": "",
        "entries": [{"title": "Watcher", "description": "",
                     "path": str(out / "Watcher.autowright")}]}, _exporter({}))
    assert kept["entries"][0]["archive"] == str(out / "Watcher.autowright")


def test_save_never_overwrites_an_archive_already_there(market, tmp_path):
    """§22.7 step 3: the folder is the user's - a file already on disk keeps
    its bytes and the new export takes ` 2`."""
    folder, source = _shelf(market, tmp_path)
    (folder / "Watcher.autowright").write_bytes(b"the-users-own-file")
    market.save_catalog(source["id"], {
        "name": "Shelf", "description": "",
        "entries": [{"title": "Watcher", "description": "", "automationId": "a1"}]},
        _exporter({"a1": ("Watcher", b"freshly-exported")}))
    assert (folder / "Watcher.autowright").read_bytes() == b"the-users-own-file"
    assert (folder / "Watcher 2.autowright").read_bytes() == b"freshly-exported"
    written = yaml.safe_load((folder / marketplace.CATALOG_FILENAME).read_text(encoding="utf-8"))
    assert written["entries"][0]["path"] == str(folder / "Watcher 2.autowright")


def test_save_keeps_a_path_entry_and_its_image_as_written(market, tmp_path):
    """§22.7: a `path` entry names an archive already in place - neither it nor
    its image is touched by a save."""
    folder, source = _shelf(market, tmp_path)
    (folder / "already.autowright").write_bytes(b"already-here")
    (folder / "images").mkdir()
    (folder / "images" / "cover.png").write_bytes(PNG)
    archive = str(folder / "already.autowright")
    image = str(folder / "images" / "cover.png")
    saved = market.save_catalog(source["id"], {
        "name": "Shelf", "description": "",
        "entries": [{"title": "Kept", "description": "Still here",
                     "path": archive, "image": image}]},
        _exporter({}))
    written = yaml.safe_load((folder / marketplace.CATALOG_FILENAME).read_text(encoding="utf-8"))
    assert written["entries"] == [{"title": "Kept", "description": "Still here",
                                   "path": archive, "image": image}]
    assert (folder / "already.autowright").read_bytes() == b"already-here"
    assert saved["entries"][0]["image"] is True
    assert market.image_bytes(source["id"], 0) == (PNG, ".png")


def test_save_rewrites_the_file_with_the_catalog_keys_only(market, tmp_path):
    """§22.7 step 4: the editor owns the file - unknown keys and hand-written
    comments don't survive a save."""
    folder = tmp_path / "hand"
    folder.mkdir()
    f = folder / marketplace.CATALOG_FILENAME
    archive = str(folder / "one.autowright")
    f.write_text("# the shelf I share with friends\n"
                 "format_version: 1\n"
                 "future: 1\n"
                 "url: https://x.test/shelf/marketplace-catalog.yaml\n"
                 "name: Hand written\n"
                 "entries:\n"
                 "  - title: One\n"
                 f"    path: {archive}\n"
                 "    future: 2\n", encoding="utf-8")
    source = market.add(path=str(f))
    market.save_catalog(source["id"], {
        "name": "Hand written", "description": "",
        "entries": [{"title": "One", "description": "", "path": archive}]},
        _exporter({}))
    text = f.read_text(encoding="utf-8")
    assert "#" not in text and "future" not in text and "url" not in text
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
            "name": "Shelf", "description": "",
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
                                                  **body}, _exporter({}))

    first = str(folder / "a.autowright")
    second = str(folder / "b.autowright")
    with pytest.raises(MarketplaceError) as e:
        save(entries=[{"title": "  ", "description": "", "path": first}])
    assert str(e.value) == "entry 0: it has no title"
    with pytest.raises(MarketplaceError) as e:
        save(entries=[{"title": "One", "description": "", "path": first},
                      {"title": "Two", "description": "", "path": second,
                       "automationId": "a1"}])
    assert str(e.value).startswith("entry 1: ")
    # §22.1: a `path` kept as written still has to be one of the two forms
    with pytest.raises(MarketplaceError) as e:
        save(entries=[{"title": "Relative", "description": "", "path": "a.autowright"}])
    assert str(e.value) == "entry 0: `path` must be an https link or an absolute path"
    with pytest.raises(MarketplaceError) as e:
        save(entries=[{"title": "Neither", "description": ""}])
    assert str(e.value).startswith("entry 0: ")
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
            "name": "Shelf", "description": "",
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
        "name": "Shelf", "description": "",
        "entries": [{"title": "One", "description": "", "automationId": "a1"}]},
        _exporter({"a1": ("One", b"archive-bytes")}))
    assert (folder / "One.autowright").is_file()
    saved = market.save_catalog(source["id"], {
        "name": "Shelf", "description": "", "entries": []}, _exporter({}))
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
    assert source["location"] == str(folder / marketplace.CATALOG_FILENAME)
    assert client.get("/marketplace").json()["sources"] == [source]

    r = client.post("/marketplace/catalogs", json={"folder": str(folder)})
    assert r.status_code == 409 and r.json()["detail"] == marketplace.FOLDER_TAKEN
    r = client.post("/marketplace/catalogs", json={"folder": str(tmp_path / "nothing")})
    assert r.status_code == 422
    assert client.post("/marketplace/catalogs",
                       json={"folder": "shelves/mine"}).status_code == 422


def test_catalog_create_route_without_a_folder(client):
    """§22.4: no folder means the app keeps the only copy - the row's location
    is `null` and the catalog is named "My catalog"."""
    r = client.post("/marketplace/catalogs", json={})
    assert r.status_code == 200
    source = r.json()
    assert source["location"] is None and source["kind"] == "none"
    assert source["name"] == marketplace.KEPT_NAME and source["entries"] == []
    assert source["refreshedAt"] is None and source["cached"] is True
    assert client.get("/marketplace").json()["sources"] == [source]


def test_catalog_create_route_takes_the_editors_content(client, tmp_path):
    """§22.7: the create body carries the same content a save does - the
    automation is exported into the folder, and a taken folder is the 409
    before anything else is written there."""
    from autowright.storage import store

    a = store.create_automation(make_version(), name="Watcher", agent_id="mock")
    store.patch_automation(a, {"paramValues": {"greeting": "super-secret-value"}})
    folder = tmp_path / "shelf"
    folder.mkdir()

    body = {"folder": str(folder), "name": "Shelf", "description": "What I run.",
            "entries": [{"title": "Watcher", "description": "Watches things",
                         "automationId": a["id"]}]}
    r = client.post("/marketplace/catalogs", json=body)
    assert r.status_code == 200
    source = r.json()
    archive = folder / "Watcher.autowright"
    data = archive.read_bytes()
    manifest = yaml.safe_load(zipfile.ZipFile(io.BytesIO(data)).read("manifest.yaml"))
    assert "param_values" not in manifest
    assert b"super-secret-value" not in data
    assert source["name"] == "Shelf"
    assert source["entries"] == [{"index": 0, "title": "Watcher",
                                  "description": "Watches things",
                                  "archive": str(archive), "image": False}]

    # §22.4: the folder now holds a catalog - add it instead, and nothing new lands
    r = client.post("/marketplace/catalogs", json=body)
    assert r.status_code == 409 and r.json()["detail"] == marketplace.FOLDER_TAKEN
    assert sorted(p.name for p in folder.iterdir()) == [
        "Watcher.autowright", marketplace.CATALOG_FILENAME]
    assert len(client.get("/marketplace").json()["sources"]) == 1


def test_catalog_read_route(client, tmp_path, monkeypatch):
    archive = str(tmp_path / "one.autowright")
    f = write_catalog(tmp_path, [{"title": "One", "description": "Runs daily",
                                  "path": archive}], name="Shelf")
    source = client.post("/marketplace/sources", json={"path": str(f)}).json()
    r = client.get(f"/marketplace/sources/{source['id']}/catalog")
    assert r.status_code == 200
    assert r.json() == {"name": "Shelf", "description": "",
                        "entries": [{"index": 0, "title": "One", "description": "Runs daily",
                                     "path": archive, "image": ""}]}
    assert client.get("/marketplace/sources/nope/catalog").status_code == 404

    # §22.7: a link location's catalog isn't on this machine
    monkeypatch.setattr(
        marketplace, "_fetch_url",
        lambda url, *, cap, deadline_s=60: catalog_text(
            [{"title": "Web", "path": "https://x.test/w.autowright"}],
            name="Linked").encode())
    linked = client.post("/marketplace/sources",
                         json={"url": "https://x.test/marketplace.yaml"}).json()
    r = client.get(f"/marketplace/sources/{linked['id']}/catalog")
    assert r.status_code == 409 and r.json()["detail"] == marketplace.NOT_EDITABLE
    r = client.put(f"/marketplace/sources/{linked['id']}/catalog",
                   json={"name": "Linked", "description": "", "entries": []})
    assert r.status_code == 409 and r.json()["detail"] == marketplace.NOT_EDITABLE
    assert client.put("/marketplace/sources/nope/catalog",
                      json={"name": "", "description": "",
                            "entries": []}).status_code == 404
    # §22.7: a catalog the file can't be read from is the §22.1 message
    f.write_text("format_version: 9\n", encoding="utf-8")
    r = client.get(f"/marketplace/sources/{source['id']}/catalog")
    assert r.status_code == 422 and "format 1" in r.json()["detail"]


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
        "name": "Shelf", "description": "",
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
        "name": "Shelf", "description": "",
        "entries": [{"title": "Gone", "description": "", "automationId": "nope"}]})
    assert r.status_code == 422 and r.json()["detail"] == "entry 0: no automation has that id"


def test_catalog_save_route_needs_an_export_folder_without_a_location(client, tmp_path):
    """§22.4/§22.7: a catalog the app keeps has no folder of its own - the body
    says where the exports go."""
    from autowright.storage import store

    a = store.create_automation(make_version(), name="Watcher", agent_id="mock")
    source = client.post("/marketplace/catalogs", json={}).json()
    entries = [{"title": "Watcher", "description": "", "automationId": a["id"]}]

    r = client.put(f"/marketplace/sources/{source['id']}/catalog",
                   json={"name": "Mine", "description": "", "entries": entries})
    assert r.status_code == 422 and r.json()["detail"] == marketplace.NO_EXPORT_FOLDER
    out = tmp_path / "exports"
    out.mkdir()
    r = client.put(f"/marketplace/sources/{source['id']}/catalog",
                   json={"name": "Mine", "description": "", "exportFolder": str(out),
                         "entries": entries})
    assert r.status_code == 200
    assert (out / "Watcher.autowright").is_file()
    assert r.json()["location"] is None
    assert r.json()["entries"][0]["archive"] == str(out / "Watcher.autowright")


def test_catalog_save_route_lists_an_archive_file_where_it_is(client, tmp_path):
    """§22.7 step 2: an `archiveFile` is read and validated like an import
    would, then listed where it is - nothing is copied into the folder."""
    archive = _export(client)
    picked = tmp_path / "shared.autowright"
    picked.write_bytes(archive)
    folder = tmp_path / "shelf"
    folder.mkdir()
    source = client.post("/marketplace/catalogs", json={"folder": str(folder)}).json()

    r = client.put(f"/marketplace/sources/{source['id']}/catalog", json={
        "name": "Shelf", "description": "",
        "entries": [{"title": "Shared", "description": "", "archiveFile": str(picked)}]})
    assert r.status_code == 200
    assert not (folder / "shared.autowright").exists()
    assert picked.read_bytes() == archive
    assert r.json()["entries"][0]["archive"] == str(picked)

    junk = tmp_path / "junk.autowright"
    junk.write_bytes(b"not a zip at all")
    r = client.put(f"/marketplace/sources/{source['id']}/catalog", json={
        "name": "Shelf", "description": "",
        "entries": [{"title": "Shared", "description": "", "path": str(picked)},
                    {"title": "Junk", "description": "", "archiveFile": str(junk)}]})
    assert r.status_code == 422
    assert r.json()["detail"] == "entry 1: not a valid .autowright archive"
    assert sorted(p.name for p in folder.iterdir()) == [marketplace.CATALOG_FILENAME]
