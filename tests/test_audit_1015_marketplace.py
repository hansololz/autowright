"""Marketplace audit regressions (2026-10-15).

§22.2 disk-first for every mutation: a refresh (single, all, the auto sweep)
and a §22.7 catalog save whose table write fails leave the row exactly as it
was; an add whose copy write fails is the ordinary 422 with nothing stored;
§3 the auto-refresh sweeper's stop is bounded at `AUTO_REFRESH_STOP_S`.
"""
import threading
import time
from pathlib import Path

import pytest
from test_marketplace import (_exporter, _shelf, added, age_read, builtin_row,  # noqa: F401
                              market, serve, settle_builtin, write_catalog)

from autowright import marketplace
from autowright.marketplace import MarketplaceError


def _failing_save():
    raise OSError(28, "No space left on device")


def _columns(market, source_id):
    with market.lock:
        row = market._find(source_id)
        return {key: row[key] for key in marketplace.COLUMNS}


def _catalog(tmp_path, title, filename):
    return write_catalog(tmp_path, [{"title": title, "path": str(tmp_path / f"{title}.autowright")}],
                         filename=filename)


# ---------- 1. §22.2 a refresh whose table write fails changes nothing ----------

def test_failed_refresh_save_keeps_the_stamp(market, tmp_path, monkeypatch):  # noqa: F811
    source = market.add(path=str(_catalog(tmp_path, "One", "one.yaml")))
    age_read(market, source["id"])
    before = _columns(market, source["id"])
    monkeypatch.setattr(market, "_save", _failing_save)
    with pytest.raises(OSError):
        market.refresh(source["id"])
    after = _columns(market, source["id"])
    assert after == before
    assert after["refreshed_at"] == before["refreshed_at"] and after["error"] is None


def test_failed_refresh_save_keeps_the_error_unset(market, tmp_path, monkeypatch):  # noqa: F811
    f = _catalog(tmp_path, "One", "one.yaml")
    source = market.add(path=str(f))
    f.write_text("format_version: 2\nentries: []\n", encoding="utf-8")  # now refused
    before = _columns(market, source["id"])
    monkeypatch.setattr(market, "_save", _failing_save)
    with pytest.raises(OSError):
        market.refresh(source["id"])
    assert _columns(market, source["id"]) == before


def test_failed_refresh_all_save_keeps_every_row(market, tmp_path, monkeypatch):  # noqa: F811
    serve(monkeypatch, {})
    first = market.add(path=str(_catalog(tmp_path, "One", "one.yaml")))
    second = market.add(path=str(_catalog(tmp_path, "Two", "two.yaml")))
    ids = [first["id"], second["id"], builtin_row(market)["id"]]
    for source_id in ids[:2]:
        age_read(market, source_id)
    before = {source_id: _columns(market, source_id) for source_id in ids}
    monkeypatch.setattr(market, "_save", _failing_save)
    with pytest.raises(OSError):
        market.refresh_all()
    assert {source_id: _columns(market, source_id) for source_id in ids} == before


def test_failed_auto_sweep_save_keeps_the_row(market, tmp_path, monkeypatch):  # noqa: F811
    settle_builtin(market)
    source = market.add(path=str(_catalog(tmp_path, "One", "one.yaml")))
    market.update_settings(source["id"], auto_refresh=True)
    age_read(market, source["id"])
    before = _columns(market, source["id"])
    monkeypatch.setattr(market, "_save", _failing_save)
    with pytest.raises(OSError):
        market.auto_refresh_sweep()
    assert _columns(market, source["id"]) == before


def test_refresh_route_answers_as_the_settings_route_on_a_failed_write(client, tmp_path,
                                                                        monkeypatch):
    from fastapi.testclient import TestClient

    from autowright import api

    f = _catalog(tmp_path, "One", "one.yaml")
    source = client.post("/marketplace/sources", json={"path": str(f)}).json()
    before = _columns(api.marketplace_store, source["id"])
    loose = TestClient(api.app, raise_server_exceptions=False)
    loose.headers.update(client.headers)
    monkeypatch.setattr(api.marketplace_store, "_save", _failing_save)
    settings = loose.patch(f"/marketplace/sources/{source['id']}", json={"expanded": False})
    refresh = loose.post(f"/marketplace/sources/{source['id']}/refresh")
    refresh_all = loose.post("/marketplace/refresh")
    assert settings.status_code != 200
    assert refresh.status_code == settings.status_code
    assert refresh_all.status_code == settings.status_code
    assert _columns(api.marketplace_store, source["id"]) == before


# ---------- 1. §22.7 a catalog save whose table write fails changes nothing ----------

def test_failed_catalog_save_keeps_the_row_and_no_archives(market, tmp_path,  # noqa: F811
                                                           monkeypatch):
    folder, source = _shelf(market, tmp_path)
    before = _columns(market, source["id"])
    files_before = sorted(p.name for p in folder.iterdir())
    monkeypatch.setattr(market, "_save", _failing_save)
    with pytest.raises(OSError):
        market.save_catalog(source["id"], {
            "name": "Shelf", "description": "",
            "entries": [{"title": "First", "description": "", "automationId": "a1"}]},
            _exporter({"a1": ("Daily", b"archive-bytes")}))
    assert _columns(market, source["id"]) == before
    assert sorted(p.name for p in folder.iterdir()) == files_before
    assert not (folder / "Daily.autowright").exists()


def test_failed_kept_catalog_save_keeps_the_row(market, tmp_path, monkeypatch):  # noqa: F811
    source = market.create_catalog(None)
    out = tmp_path / "exports"
    out.mkdir()
    before = _columns(market, source["id"])
    monkeypatch.setattr(market, "_save", _failing_save)
    with pytest.raises(OSError):
        market.save_catalog(source["id"], {
            "name": "Mine", "description": "", "exportFolder": str(out),
            "entries": [{"title": "W", "description": "", "automationId": "a1"}]},
            _exporter({"a1": ("Watcher", b"archive-bytes")}))
    assert _columns(market, source["id"]) == before
    assert list(out.iterdir()) == []


# ---------- 2. §22.2 a failed copy write on add is the ordinary 422 ----------

def test_add_whose_copy_write_fails_is_a_marketplace_error(market, tmp_path,  # noqa: F811
                                                            monkeypatch):
    f = _catalog(tmp_path, "One", "one.yaml")
    rows_before = [s["id"] for s in market.sources]
    dirs_root = market.source_dir("probe").parent
    dirs_before = sorted(p.name for p in dirs_root.iterdir()) if dirs_root.exists() else []

    def partial(path, text, **_kw):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text[:5], encoding="utf-8")  # half a copy
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(marketplace, "atomic_write_text", partial)
    with pytest.raises(MarketplaceError) as e:
        market.add(path=str(f))
    assert type(e.value) is MarketplaceError  # the route's 422, not a 409
    assert str(e.value).startswith("couldn't write the catalog copy: ")
    assert [s["id"] for s in market.sources] == rows_before
    assert added(market) == []
    dirs_after = sorted(p.name for p in dirs_root.iterdir()) if dirs_root.exists() else []
    assert dirs_after == dirs_before


def test_add_route_answers_422_when_the_copy_write_fails(client, tmp_path, monkeypatch):
    f = _catalog(tmp_path, "One", "one.yaml")

    def full(_path, _text, **_kw):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(marketplace, "atomic_write_text", full)
    r = client.post("/marketplace/sources", json={"path": str(f)})
    assert r.status_code == 422
    assert r.json()["detail"].startswith("couldn't write the catalog copy: ")


# ---------- 3. §3 the sweeper's stop is bounded ----------

def test_stop_auto_refresh_returns_promptly_with_a_blocked_sweeper(market,  # noqa: F811
                                                                  monkeypatch):
    assert marketplace.AUTO_REFRESH_STOP_S == 0.5
    entered = threading.Event()
    release = threading.Event()

    def blocked(location):
        entered.set()
        release.wait(30)
        return None, "released"

    monkeypatch.setattr(market, "_fetch_catalog", blocked)
    assert market.is_pending(builtin_row(market))  # the first pass reads it
    market.start_auto_refresh(lambda: None)
    thread = market._auto_thread
    try:
        assert entered.wait(5)
        started = time.monotonic()
        market.stop_auto_refresh()
        assert time.monotonic() - started < 1.5
        assert thread.daemon
    finally:
        release.set()
        thread.join(5)

