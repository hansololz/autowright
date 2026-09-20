"""Marketplace audit regressions (2026-09-18).

§22.1 origin-aware references (a remote catalog may carry only https links),
§22.2 lenient load (any skipped row flips the table read-only, exactly like a
corrupt file) and the sweep of a `<id>/` directory no row names.
"""
import uuid

import pytest
import yaml

from autowright import marketplace, paths
from autowright.marketplace import (MarketplaceError, MarketplaceStore,
                                    MarketplaceUnwritable)
from autowright.yamlio import save_yaml

CATALOG_URL = "https://x.test/shelf/marketplace-catalog.yaml"


def catalog_text(entries) -> str:
    return yaml.safe_dump({"format_version": 1, "name": "Shelf", "entries": entries},
                          sort_keys=False)


@pytest.fixture()
def market(home):
    s = MarketplaceStore()
    s.load()
    return s


# ---------- §22.1 origin-aware references ----------
def test_a_remote_catalog_cant_reference_a_local_path():
    """§22.1: a shared catalog must never point the app at files on the
    user's disk."""
    text = catalog_text([{"title": "Local", "path": "/Users/x/manga.autowright"}])
    with pytest.raises(MarketplaceError) as e:
        marketplace.parse_catalog(text, location=CATALOG_URL)
    assert str(e.value) == "entry 0: a remote catalog can't reference a local path"


def test_a_remote_catalogs_image_must_be_a_link_too():
    text = catalog_text([{"title": "Local image",
                          "path": "https://x.test/manga.autowright",
                          "image": "/Users/x/cover.png"}])
    with pytest.raises(MarketplaceError) as e:
        marketplace.parse_catalog(text, location=CATALOG_URL)
    assert str(e.value) == "entry 0: a remote catalog can't reference a local path"


def test_a_remote_catalog_of_links_is_fine():
    catalog = marketplace.parse_catalog(
        catalog_text([{"title": "Linked", "path": "https://x.test/manga.autowright",
                       "image": "https://x.test/cover.png"}]),
        location=CATALOG_URL)
    assert catalog["entries"][0]["path"] == "https://x.test/manga.autowright"


@pytest.mark.parametrize("location", [None, "/Users/x/marketplace.yaml"])
def test_a_local_catalog_still_references_local_paths(location):
    """§22.1: the rule is origin-aware — a catalog kept by the app, or one at
    a path, keeps referencing automations anywhere on the machine."""
    catalog = marketplace.parse_catalog(
        catalog_text([{"title": "Local", "path": "/Users/x/manga.autowright",
                       "image": "/Users/x/cover.png"}]),
        location=location)
    assert catalog["entries"][0]["image"] == "/Users/x/cover.png"


def test_a_relative_reference_still_names_its_key():
    with pytest.raises(MarketplaceError) as e:
        marketplace.parse_catalog(catalog_text([{"title": "Rel", "path": "x.autowright"}]),
                                  location=CATALOG_URL)
    assert str(e.value) == "entry 0: `path` must be an https link or an absolute path"


# ---------- §22.2 lenient load ----------
def _row(**over) -> dict:
    row = {"id": str(uuid.uuid4()), "location": None, "shown": True,
           "auto_refresh": False, "added_at": "", "refreshed_at": None, "error": None}
    row.update(over)
    return row


@pytest.mark.parametrize("bad", [
    "not a mapping",
    {"location": None},                       # no id
    {"id": "not-a-uuid", "location": None},   # the id names a directory
    {"id": "11111111-1111-1111-1111-111111111111", "location": "somewhere"},
])
def test_a_skipped_row_flips_the_table_read_only(home, bad):
    """§22.2: the next save must not rewrite marketplaces.yaml without the row
    the user hand-edited — exactly the corrupt-file rule."""
    kept = _row()
    save_yaml(paths.marketplace_dir() / "marketplaces.yaml", {"sources": [kept, bad]})

    store = MarketplaceStore()
    store.load()
    assert [s["id"] for s in store.sources] == [kept["id"]]
    with pytest.raises(MarketplaceUnwritable):
        store.remove(kept["id"])


def test_a_table_of_good_rows_stays_writable(home):
    kept = _row()
    save_yaml(paths.marketplace_dir() / "marketplaces.yaml", {"sources": [kept]})

    store = MarketplaceStore()
    store.load()
    store.remove(kept["id"])
    # §22.2: the built-in catalog the load seeded is the only row left
    assert [s["builtin"] for s in store.sources] == [True]


# ---------- §22.2 the sources store holds nothing else ----------
def test_an_unreadable_table_sweeps_nothing(home):
    """§5 read-only degradation: with no row list to compare against, every
    copy would look orphaned - a damaged file is degraded, never destroyed."""
    copy = paths.marketplace_dir() / str(uuid.uuid4())
    copy.mkdir(parents=True)
    (paths.marketplace_dir() / "marketplaces.yaml").write_text(
        "sources: [unclosed\n", encoding="utf-8")

    store = MarketplaceStore()
    store.load()
    assert copy.is_dir()


def test_load_sweeps_a_directory_no_row_names(home):
    """§22.2: a crash between a remove's table save and its directory delete."""
    kept = _row()
    save_yaml(paths.marketplace_dir() / "marketplaces.yaml", {"sources": [kept]})
    mine = paths.marketplace_dir() / kept["id"]
    mine.mkdir(parents=True)
    (mine / marketplace.CATALOG_FILENAME).write_text(catalog_text([]), encoding="utf-8")
    orphan = paths.marketplace_dir() / str(uuid.uuid4())
    orphan.mkdir(parents=True)
    handmade = paths.marketplace_dir() / "my catalogs"
    handmade.mkdir()

    store = MarketplaceStore()
    store.load()
    assert not orphan.exists()
    assert mine.is_dir() and handmade.is_dir()
    assert (paths.marketplace_dir() / "marketplaces.yaml").exists()
