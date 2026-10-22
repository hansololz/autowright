"""Marketplace audit regressions (2026-10-21).

§22.1 the download deadline is checked between trickled bytes (`read1`, not a
blocking `read(n)`); §22.2 a `refreshed_at` in the future counts as due, and
a stop that lands during a pending row's download writes nothing.
"""
from datetime import datetime, timedelta, timezone

import pytest
from test_marketplace import BUILTIN_TEXT, builtin_row, market  # noqa: F401

from autowright import marketplace, transfer
from autowright.marketplace import MarketplaceError


# ---------- §22.1 the deadline runs between trickled bytes ----------

def test_a_trickling_catalog_download_times_out_at_the_deadline(monkeypatch):
    clock = {"now": 1000.0}
    reads = []

    class _Trickle:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def geturl(self):
            return "https://example.test/marketplace.yaml"

        def read(self, _n=-1):
            raise AssertionError("read(n) blocks for n bytes - the deadline never runs")

        def read1(self, _n=-1):
            # One byte per call, each arriving a second later.
            reads.append(1)
            clock["now"] += 1.0
            return b"x"

    monkeypatch.setattr(transfer, "open_https_only",
                        lambda request, timeout, refuse: _Trickle())
    monkeypatch.setattr(marketplace.time, "monotonic", lambda: clock["now"])
    with pytest.raises(MarketplaceError, match="timed out after 5 seconds"):
        marketplace._fetch_url("https://example.test/marketplace.yaml",
                               cap=marketplace.MAX_CATALOG_BYTES, deadline_s=5)
    # Stopped by the deadline after a handful of bytes, not by the byte cap.
    assert len(reads) == 6


# ---------- §22.2 a future stamp is due ----------

def test_a_refreshed_at_in_the_future_counts_as_due():
    now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    row = {"auto_refresh": True, "location": "https://example.test/marketplace.yaml",
           "refreshed_at": (now + timedelta(hours=3)).isoformat()}
    assert marketplace.MarketplaceStore.is_due(row, now)
    row["refreshed_at"] = (now - timedelta(hours=1)).isoformat()
    assert not marketplace.MarketplaceStore.is_due(row, now)


# ---------- §22.2 a stop during a pending download writes nothing ----------

def test_a_stop_during_a_pending_download_writes_nothing(market, monkeypatch):  # noqa: F811
    row = builtin_row(market)
    with market.lock:
        assert market.is_pending(row)

    def fetch(url, *, cap, deadline_s=marketplace.FETCH_DEADLINE_S):
        market._auto_stop.set()  # the stop lands mid-download
        return BUILTIN_TEXT.encode()

    monkeypatch.setattr(marketplace, "_fetch_url", fetch)
    saved = []
    monkeypatch.setattr(market, "_save", lambda: saved.append(True))
    assert market.refresh_pending() == []
    assert saved == []
    with market.lock:
        assert market.is_pending(row)
        assert row["refreshed_at"] is None
