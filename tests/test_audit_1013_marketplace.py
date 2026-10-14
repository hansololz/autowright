"""Marketplace / transfer audit regressions (2026-10-13).

§21.4 the orphan sweep stays off on every load while an unparseable
`sources.yaml` exists; §5.2 duplicate member names and undecodable names are a
422; §22.4 archive downloads take their own two slots; §22.2 a re-pointed
built-in row is due at once; §22.4 a NUL in the create folder is a 422; §5.2 a
refused redirect closes its response.
"""
import io
import struct
import threading
import warnings
import zipfile
from datetime import datetime, timezone

import pytest
import yaml

from autowright import marketplace, paths, transfer
from autowright.marketplace import MarketplaceError, MarketplaceStore

COPY_ID = "55555555-5555-4555-8555-555555555555"
BUILTIN_URL = marketplace.BUILTIN_CATALOG_URL


def catalog_text(entries) -> str:
    return yaml.safe_dump({"format_version": 1, "name": "Shelf", "entries": entries},
                          sort_keys=False)


def _zip(members: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile warns on a duplicate name
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for path, raw in members:
                z.writestr(path, raw)
    return buf.getvalue()


def _duplicate_zip() -> bytes:
    """Three empty `a` members and one 2 MB `a`: zipfile reads a name by its
    last entry, so a size checked on another entry would gate the big one."""
    return _zip([("a", b""), ("a", b""), ("a", b""), ("a", b"x" * (2 * 1024 * 1024))])


def _shelf(client, tmp_path, archive: bytes) -> dict:
    (tmp_path / "shared.autowright").write_bytes(archive)
    f = tmp_path / "marketplace.yaml"
    f.write_text(catalog_text([{"title": "Shared",
                                "path": str(tmp_path / "shared.autowright")}]),
                 encoding="utf-8")
    return client.post("/marketplace/sources", json={"path": str(f)}).json()


# ---------- §21.4: the sweep stays off on every load ----------
def test_an_unparseable_sources_yaml_keeps_the_copies_on_every_load(home):
    """§21.4 (2026-10-13): the first load saves the seeded table, so the next
    launch takes the ordinary branch - the sweep must stay off there too while
    the unparseable `sources.yaml` is still beside the table."""
    root = paths.marketplace_dir()
    root.mkdir(parents=True, exist_ok=True)
    (root / "sources.yaml").write_text("sources: [oops\n", encoding="utf-8")
    (root / COPY_ID).mkdir()

    first = MarketplaceStore()
    first.load()
    assert (root / "marketplaces.yaml").is_file()
    assert (root / COPY_ID).is_dir()

    second = MarketplaceStore()
    second.load()
    assert (root / COPY_ID).is_dir()
    assert [s for s in second.sources if not s["builtin"]] == []
    assert (root / "sources.yaml").read_text(encoding="utf-8") == "sources: [oops\n"


def test_a_readable_sources_yaml_beside_a_table_is_ignored_and_the_sweep_runs(home, caplog):
    root = paths.marketplace_dir()
    root.mkdir(parents=True, exist_ok=True)
    MarketplaceStore().load()   # writes marketplaces.yaml
    (root / "sources.yaml").write_text(
        "sources:\n"
        f"- id: {COPY_ID}\n"
        "  kind: url\n"
        "  origin: https://x.test/shelf/marketplace-catalog.yaml\n",
        encoding="utf-8")
    (root / COPY_ID).mkdir()
    store = MarketplaceStore()
    with caplog.at_level("INFO"):
        store.load()
    assert [s for s in store.sources if not s["builtin"]] == []
    assert not (root / COPY_ID).exists()
    # the every-load probe is quiet: no migration log for a file it ignores
    assert "sources.yaml" not in caplog.text


# ---------- §5.2: duplicate member names ----------
def test_list_archive_text_refuses_a_name_listed_twice():
    with pytest.raises(transfer.TransferError) as e:
        transfer.list_archive_text(_duplicate_zip())
    assert str(e.value) == "the archive names the same file twice"


def test_import_preview_refuses_a_name_listed_twice(client):
    r = client.post("/automations/import/preview", content=_duplicate_zip())
    assert r.status_code == 422
    assert r.json()["detail"] == "the archive names the same file twice"


def test_archive_route_refuses_a_name_listed_twice(client, tmp_path):
    source = _shelf(client, tmp_path, _duplicate_zip())
    r = client.get(f"/marketplace/sources/{source['id']}/entries/0/archive")
    assert r.status_code == 422
    assert r.json()["detail"] == "the archive names the same file twice"


def test_archive_route_still_lists_a_normal_archive(client, tmp_path):
    source = _shelf(client, tmp_path, _zip([("manifest.yaml", b"a: 1\n"),
                                            ("automation/spec.md", b"# T\n")]))
    r = client.get(f"/marketplace/sources/{source['id']}/entries/0/archive")
    assert r.status_code == 200
    assert [f["path"] for f in r.json()["files"]] == ["manifest.yaml", "automation/spec.md"]


# ---------- §5.2: an undecodable member name ----------
def _undecodable_name_zip() -> bytes:
    """A zip whose central-directory entry sets the UTF-8 flag (0x800) on
    name bytes that aren't UTF-8."""
    data = bytearray(_zip([("name.txt", b"hi")]))
    at = data.index(b"PK\x01\x02")
    flags = struct.unpack_from("<H", data, at + 8)[0]
    struct.pack_into("<H", data, at + 8, flags | 0x800)
    name_length = struct.unpack_from("<H", data, at + 28)[0]
    data[at + 46:at + 46 + name_length] = b"\xff\xfe" + b"x" * (name_length - 2)
    return bytes(data)


def test_an_undecodable_member_name_is_not_a_valid_archive():
    with pytest.raises(transfer.TransferError) as e:
        transfer.list_archive_text(_undecodable_name_zip())
    assert str(e.value) == "not a valid .autowright archive"


def test_import_preview_answers_422_for_an_undecodable_member_name(client):
    r = client.post("/automations/import/preview", content=_undecodable_name_zip())
    assert r.status_code == 422
    assert r.json()["detail"] == "not a valid .autowright archive"


# ---------- §22.4: archive downloads take their own slots ----------
def test_an_archive_download_never_waits_on_a_catalog_read_slot(client, tmp_path,
                                                                monkeypatch):
    f = tmp_path / "marketplace.yaml"
    f.write_text(catalog_text([{"title": "Web", "path": "https://x.test/w.autowright"}]),
                 encoding="utf-8")
    source = client.post("/marketplace/sources", json={"path": str(f)}).json()
    archive = _zip([("manifest.yaml", b"a: 1\n")])
    monkeypatch.setattr(transfer, "fetch_archive", lambda url: (archive, url))
    held = 0
    try:
        while marketplace._read_slots.acquire(blocking=False):
            held += 1
        assert held == marketplace.MAX_CONCURRENT_READS
        answered: list = []
        worker = threading.Thread(target=lambda: answered.append(client.get(
            f"/marketplace/sources/{source['id']}/entries/0/archive")))
        worker.start()
        worker.join(10)
        assert not worker.is_alive()
        assert answered[0].status_code == 200
    finally:
        for _ in range(held):
            marketplace._read_slots.release()


# ---------- §22.2: a re-pointed built-in row is due at once ----------
def test_a_re_pointed_builtin_row_clears_its_read_stamp_and_error(home):
    root = paths.marketplace_dir()
    root.mkdir(parents=True, exist_ok=True)
    recent = datetime.now(timezone.utc).isoformat()
    (root / "marketplaces.yaml").write_text(yaml.safe_dump({"sources": [{
        "id": COPY_ID, "location": "https://x.test/moved/marketplace-catalog.yaml",
        "expanded": True, "auto_refresh": True, "builtin": True,
        "added_at": recent, "refreshed_at": recent, "error": "the host answered 500"}]}),
        encoding="utf-8")
    store = MarketplaceStore()
    store.load()
    row = next(s for s in store.sources if s["builtin"])
    assert row["location"] == BUILTIN_URL
    assert row["refreshed_at"] is None and row["error"] is None
    assert MarketplaceStore.is_due(row)
    saved = yaml.safe_load((root / "marketplaces.yaml").read_text(encoding="utf-8"))
    assert saved["sources"][0]["refreshed_at"] is None
    assert saved["sources"][0]["error"] is None


# ---------- §22.4: a NUL in the create folder ----------
def test_create_catalog_refuses_a_folder_with_a_nul(home):
    store = MarketplaceStore()
    store.load()
    with pytest.raises(MarketplaceError):
        store.create_catalog("/a\x00b")


def test_create_route_answers_422_for_a_folder_with_a_nul(client):
    r = client.post("/marketplace/catalogs", json={"folder": "/a\x00b"})
    assert r.status_code == 422


# ---------- §5.2: a refused redirect closes its response ----------
def test_a_refused_redirect_closes_the_response():
    class Refused(Exception):
        pass

    class Response:
        closed = False

        def close(self):
            self.closed = True

    handler = transfer.HttpsOnlyRedirectHandler(Refused)
    response = Response()
    with pytest.raises(Refused):
        handler.redirect_request(None, response, 302, "Found", {}, "http://x.test/a")
    assert response.closed
