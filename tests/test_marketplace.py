"""Marketplace (§22): catalog validation, reference resolution, the sources
store's refresh/image cache, and the §22.4 routes with the network stubbed."""
import pytest
import yaml

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


def test_file_references_stay_inside_the_catalog_folder(tmp_path):
    origin = str(tmp_path / "shelf" / "marketplace.yaml")
    (tmp_path / "shelf").mkdir()
    assert marketplace.resolve_reference("a/manga.autowright", kind="file",
                                         origin=origin) == \
        str(tmp_path / "shelf" / "a" / "manga.autowright")
    # an absolute https reference is fine in a file source (mixed catalogs)
    assert marketplace.resolve_reference("https://x.test/m.autowright", kind="file",
                                         origin=origin) == "https://x.test/m.autowright"
    with pytest.raises(MarketplaceError) as e:
        marketplace.resolve_reference("../x.autowright", kind="file", origin=origin)
    assert "outside" in str(e.value)
    with pytest.raises(MarketplaceError) as e:
        marketplace.resolve_reference("/etc/passwd.autowright", kind="file", origin=origin)
    assert "absolute path" in str(e.value)


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
    # an escaping reference is a validation failure naming its entry
    bad = write_catalog(tmp_path, [{"title": "Escape", "path": "../x.autowright"}],
                        filename="escape.yaml")
    with pytest.raises(MarketplaceError) as e:
        market.add(path=str(bad))
    assert str(e.value).startswith("entry 0: ")
    assert market.sources == []


def test_refresh_keeps_the_cache_and_records_the_error(market, tmp_path):
    f = write_catalog(tmp_path, [{"title": "One", "path": "one.autowright"}])
    source = market.add(path=str(f))
    first_refreshed = source["refreshedAt"]
    f.write_text("format_version: 1\nentries: [oops\n", encoding="utf-8")
    after = market.refresh(source["id"])
    assert after["error"] and "YAML" in after["error"]
    assert after["refreshedAt"] == first_refreshed          # last *successful* fetch
    assert [e["title"] for e in after["entries"]] == ["One"]  # last good copy
    # a success clears the error and stamps a new time
    write_catalog(tmp_path, [{"title": "Two", "path": "two.autowright"}])
    fixed = market.refresh(source["id"])
    assert fixed["error"] is None
    assert [e["title"] for e in fixed["entries"]] == ["Two"]


def test_refresh_all_never_stops_at_the_first_bad_source(market, tmp_path):
    good_dir, bad_dir = tmp_path / "good", tmp_path / "bad"
    good_dir.mkdir()
    bad_dir.mkdir()
    good = write_catalog(good_dir, [{"title": "Good", "path": "g.autowright"}])
    bad = write_catalog(bad_dir, [{"title": "Bad", "path": "b.autowright"}])
    market.add(path=str(good))
    bad_source = market.add(path=str(bad))
    bad.unlink()
    sources = market.refresh_all()
    assert sources[0]["error"] is None
    assert sources[1]["id"] == bad_source["id"] and sources[1]["error"]
    assert [e["title"] for e in sources[1]["entries"]] == ["Bad"]


def test_images_are_rebuilt_and_skipped_on_failure(market, tmp_path):
    (tmp_path / "cover.png").write_bytes(PNG)
    f = write_catalog(tmp_path, [
        {"title": "Has one", "path": "a.autowright", "image": "cover.png"},
        {"title": "Missing", "path": "b.autowright", "image": "gone.png"},
        {"title": "Too big", "path": "c.autowright", "image": "huge.png"},
    ])
    (tmp_path / "huge.png").write_bytes(b"0" * (marketplace.MAX_IMAGE_BYTES + 1))
    source = market.add(path=str(f))
    assert [e["image"] for e in source["entries"]] == [True, False, False]
    # a refresh rebuilds the directory: the dropped image's cache file goes
    write_catalog(tmp_path, [{"title": "Has one", "path": "a.autowright"}])
    after = market.refresh(source["id"])
    assert after["entries"] == [{"index": 0, "title": "Has one", "description": "",
                                 "archive": str(tmp_path / "a.autowright"),
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


def test_unreadable_cache_still_lists_the_source(market, tmp_path):
    f = write_catalog(tmp_path, [{"title": "One", "path": "one.autowright"}], name="Shelf")
    source = market.add(path=str(f))
    market.catalog_file(source["id"]).write_text("format_version: 9\n", encoding="utf-8")
    listed = market.serialize(market.sources[0])
    # §22.2: the source still lists, with zero entries and the origin-derived name
    assert listed["entries"] == [] and listed["name"] == "marketplace"
    assert listed["error"] == marketplace.CACHE_UNREADABLE
    assert listed["cached"] is False
    # a real stored refresh error wins over the cache-read one
    f.unlink()
    failed = market.refresh(source["id"])
    assert "couldn't read the catalog file" in failed["error"]


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


def test_routes_add_list_refresh_and_remove(client, tmp_path):
    (tmp_path / "one.autowright").write_bytes(b"zip")
    (tmp_path / "cover.png").write_bytes(PNG)
    f = write_catalog(tmp_path, [{"title": "One", "path": "one.autowright",
                                  "image": "cover.png"}], name="Shelf")
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
    f.unlink()
    r = client.post(f"/marketplace/sources/{source['id']}/refresh")
    assert r.status_code == 200 and r.json()["error"]
    assert [e["title"] for e in r.json()["entries"]] == ["One"]
    r = client.post("/marketplace/refresh")
    assert r.status_code == 200 and r.json()["sources"][0]["error"]
    assert client.post("/marketplace/sources/nope/refresh").status_code == 404

    assert client.delete(f"/marketplace/sources/{source['id']}").json() == {"ok": True}
    assert client.delete(f"/marketplace/sources/{source['id']}").status_code == 404
    assert client.get("/marketplace").json()["sources"] == []


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
