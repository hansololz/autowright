"""2026-10-31 second sweep audit batch (transfer, marketplace, drafting,
installer, paths): the §5.1 import door normalizing param definitions like
the §5 loader (§21 2026-10-31), the one-year timeout cap at import, archive param values kind-checked,
the §8 untrusted loader on agent output, a failed catalog-save write
unlinking its archive, the §5 file-name byte cap, the Ollama download's https
and byte cap, a bounded archive-slot wait, and one §5.2 deadline per URL
import.

Each test names the spec rule it pins (§5 / §5.1 / §5.2 in
`spec/storage.md`, §8 in `spec/agent-pipeline.md`, §22.4 / §22.7 in
`spec/marketplace.md`)."""
import pytest
from test_audit_1030b_pipeline import _validate
from test_marketplace import write_catalog
from test_transfer import _archive

from autowright import installer, marketplace, paths, transfer
from autowright.marketplace import MarketplaceBusy, MarketplaceStore
from autowright.storage import STEP_TIMEOUT_MAX_S


@pytest.fixture(autouse=True)
def stub_check_ready(monkeypatch):
    """§5.1 summary readiness flag - stubbed so no test spawns a harness."""
    monkeypatch.setattr(transfer.harness, "check_ready",
                        lambda name, model=None, mode="default": False)


def _number(**fields):
    return {"name": "n", "kind": "number", "label": "N", "default": 5, **fields}


def _current(a):
    return a["versions"][a["current_version"]]


# ---------- A · §5.1: import normalizes like the §5 loader (§21 2026-10-31) ----------

_NUMBER_STORED = {"name": "n", "kind": "number", "label": "N", "default": 5}


@pytest.mark.parametrize("param, stored", [
    (_number(min="1e3"), _NUMBER_STORED),
    (_number(min=None), _NUMBER_STORED),
    (_number(min=True), _NUMBER_STORED),
    (_number(min=float("inf")), _NUMBER_STORED),
    (_number(label=True), {"name": "n", "kind": "number", "default": 5}),
    (_number(help=3), _NUMBER_STORED),
    ({"name": "h", "kind": "kv", "label": "H", "default": [{"key": 1, "value": "v"}]},
     {"name": "h", "kind": "kv", "label": "H", "default": [{"key": "1", "value": "v"}]}),
    ({"name": "h", "kind": "kv", "label": "H", "default": [{"key": "k", "value": 2}]},
     {"name": "h", "kind": "kv", "label": "H", "default": [{"key": "k", "value": "2"}]}),
    (_number(default=float("nan")), {**_NUMBER_STORED, "default": 0}),
    (_number(default=float("inf")), {**_NUMBER_STORED, "default": 0}),
    (_number(default=float("nan"), min=2),
     {"name": "n", "kind": "number", "label": "N", "default": 2, "min": 2}),
    ({"name": "w", "kind": "list", "label": "W", "default": "NVDA\nAAPL"},
     {"name": "w", "kind": "list", "label": "W", "default": []}),
    ({"name": "t", "kind": "text", "label": "T", "default": [1, 2]},
     {"name": "t", "kind": "text", "label": "T", "default": ""}),
])
def test_import_normalizes_a_definition_the_loader_normalizes(store, param, stored):
    a, _ = transfer.import_automation(store, _archive(params=[param]))
    assert _current(a)["params"][0] == stored


def test_a_v0_12_0_catalog_archive_shape_imports(store):
    """§21 2026-10-31: the built-in catalog's v0.12.0 "Congress stock buys"
    archive shape installs. Before the fix the import refused it with
    "param 'watchlist': the default isn't a list value"."""
    params = [
        {"name": "include_sales", "kind": "toggle", "label": "Include sales too",
         "help": "Off means only purchases.", "default": False},
        {"name": "min_amount", "kind": "number", "label": "Smallest trade worth reporting",
         "default": 15000, "min": 0},
        {"name": "watchlist", "kind": "list", "label": "Watch-list tickers",
         "help": "One ticker per line.", "default": "NVDA\nAAPL\nMSFT\nTSLA"},
        {"name": "post_to_discord", "kind": "toggle", "label": "Post the roundup to Discord",
         "help": "On days with new trades", "send the headline": None, "default": True},
        {"name": "discord_channel", "kind": "text", "label": "Discord channel id",
         "default": "0"},
    ]
    a, _ = transfer.import_automation(store, _archive(params=params, param_values={}))
    stored = {p["name"]: p for p in _current(a)["params"]}
    assert stored["watchlist"]["default"] == []
    assert stored["include_sales"]["default"] is False
    assert stored["min_amount"]["default"] == 15000
    assert stored["post_to_discord"]["default"] is True
    assert stored["discord_channel"]["default"] == "0"
    assert "send the headline" not in stored["post_to_discord"]
    assert a["param_values"] == {}


def test_a_number_min_and_text_label_help_import(store):
    a, _ = transfer.import_automation(store, _archive(params=[_number(min=2, help="h")]))
    assert _current(a)["params"][0]["min"] == 2


def test_unknown_definition_keys_are_trimmed_at_import(store):
    a, _ = transfer.import_automation(
        store, _archive(params=[_number(color="red", min=1)]))
    stored = _current(a)["params"][0]
    assert "color" not in stored
    assert stored == {"name": "n", "kind": "number", "label": "N", "default": 5, "min": 1}
    # The version files on disk carry no such key either.
    written = [f.read_text(encoding="utf-8")
               for f in (paths.automations_dir() / a["id"]).rglob("*.yaml")]
    assert written and not any("color" in text for text in written)


# ---------- B · §5.1: archive param values are kind-checked, dropped ----------

def test_a_non_finite_archive_param_value_is_dropped_and_the_import_succeeds(store):
    data = _archive(params=[_number(), {"name": "t", "kind": "text", "label": "T",
                                        "default": ""}],
                    param_values={"n": float("inf"), "t": "kept"})
    a, _ = transfer.import_automation(store, data)
    assert a["param_values"] == {"t": "kept"}


def test_a_wrong_kind_archive_param_value_is_dropped(store):
    data = _archive(params=[_number()], param_values={"n": "five"})
    a, _ = transfer.import_automation(store, data)
    assert a["param_values"] == {}


# ---------- U · §5.1: the one-year step timeout cap at import ----------

def test_import_refuses_a_timeout_above_one_year(store):
    data = _archive(steps=[{"file": "01-a.py", "name": "A", "description": "",
                            "timeout": 40_000_000, "code": "print('a')\n"}])
    with pytest.raises(transfer.TransferError, match="one year"):
        transfer.import_automation(store, data)


def test_import_accepts_a_timeout_of_exactly_one_year(store):
    data = _archive(steps=[{"file": "01-a.py", "name": "A", "description": "",
                            "timeout": STEP_TIMEOUT_MAX_S, "code": "print('a')\n"}])
    a, _ = transfer.import_automation(store, data)
    assert _current(a)["steps"][0]["timeout"] == STEP_TIMEOUT_MAX_S


# ---------- G · §8: agent output goes through the untrusted loader ----------

def test_an_aliased_manifest_default_is_a_validation_error():
    draft, errors = _validate(extra=(
        "params:\n"
        "  - { name: h, kind: kv, label: H, default: &a [{key: k, value: *a}] }"))
    assert draft == {}
    assert any("anchors and aliases" in e for e in errors), errors


def test_an_aliased_actions_block_is_a_validation_error():
    from autowright.drafting import validate_actions

    out, errors = validate_actions("note: &a hi\ndescription: *a\n")
    assert out == {}
    assert any("anchors and aliases" in e for e in errors), errors


# ---------- F3 · §22.7 step 5: a failed archive write leaves no file ----------

def test_a_failed_archive_write_unlinks_the_file(tmp_path, monkeypatch):
    target = tmp_path / "Pack.autowright"

    class Full:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.f.close()

        def write(self, data):
            self.f.write(data[:3])
            raise OSError(28, "No space left on device")

    real_open = open
    monkeypatch.setattr("builtins.open",
                        lambda p, mode="r", *a, **kw: Full(real_open(p, mode, *a, **kw))
                        if mode == "xb" else real_open(p, mode, *a, **kw))
    with pytest.raises(OSError, match="No space"):
        marketplace._write_new_archive(target, b"archive-bytes")
    monkeypatch.undo()
    assert not target.exists()


# ---------- F4 · §5: the file-name stem is capped at 200 UTF-8 bytes ----------

def test_a_long_cjk_name_is_capped_on_a_character_boundary():
    name = "漫" * 300
    stem = paths.safe_filename(name)
    assert len(stem.encode("utf-8")) <= paths.FILENAME_STEM_MAX_BYTES
    assert stem == "漫" * (paths.FILENAME_STEM_MAX_BYTES // 3)
    assert len((stem + ".autowright").encode("utf-8")) <= 255
    assert transfer.safe_filename(name) == stem


def test_a_cut_stem_drops_trailing_spaces_and_dots():
    stem = paths.safe_filename("a" * 198 + " .b" + "c" * 50)
    assert stem == "a" * 198


def test_the_name_is_nfc_normalized():
    decomposed = "Café"
    assert paths.safe_filename(decomposed) == "Café"


def test_a_short_name_is_unchanged():
    assert paths.safe_filename("Daily/Report:2") == "Daily Report 2"
    assert paths.safe_filename("con") == "automation"


# ---------- AF · §5.2/§19: the Ollama download's https and byte cap ----------

class _Body:
    """A response streaming `chunks` 64 KiB reads, with an optional
    Content-Length."""

    def __init__(self, size, length=None):
        self.remaining = size
        self.headers = {"Content-Length": str(length)} if length is not None else {}
        self.reads = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read1(self, n):
        self.reads += 1
        take = min(n, self.remaining)
        self.remaining -= take
        return b"x" * take


def test_the_download_goes_through_open_https_only_with_the_deadline_timeout(
        tmp_path, monkeypatch):
    seen = {}

    def fake_open(req, *, timeout, refuse):
        seen["timeout"], seen["refuse"] = timeout, refuse
        return _Body(1000, length=1000)

    monkeypatch.setattr(transfer, "open_https_only", fake_open)
    installer._download("https://ollama.test/o.zip", str(tmp_path / "o.zip"),
                        lambda **kw: None, "Downloading Ollama")
    assert seen["timeout"] == installer.DOWNLOAD_READ_TIMEOUT_S
    assert "redirected off https" in str(seen["refuse"]())
    assert (tmp_path / "o.zip").stat().st_size == 1000

    # With less of the deadline left than 120 s, the open's timeout is what
    # remains.
    monkeypatch.setattr(installer, "INSTALL_TIMEOUT_S", 30)
    installer._download("https://ollama.test/o.zip", str(tmp_path / "o.zip"),
                        lambda **kw: None, "Downloading Ollama")
    assert 29 < seen["timeout"] <= 30


def test_the_download_is_capped_during_the_read(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "DOWNLOAD_MAX_BYTES", 200_000)
    body = _Body(10_000_000)
    monkeypatch.setattr(transfer, "open_https_only", lambda req, *, timeout, refuse: body)
    with pytest.raises(RuntimeError, match="larger than"):
        installer._download("https://ollama.test/o.zip", str(tmp_path / "o.zip"),
                            lambda **kw: None, "Downloading Ollama")
    # Stopped at the cap, never read to the end.
    assert body.remaining > 9_000_000
    assert (tmp_path / "o.zip").stat().st_size <= 200_000


def test_a_short_download_body_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(transfer, "open_https_only",
                        lambda req, *, timeout, refuse: _Body(500, length=1000))
    with pytest.raises(RuntimeError, match="closed before"):
        installer._download("https://ollama.test/o.zip", str(tmp_path / "o.zip"),
                            lambda **kw: None, "Downloading Ollama")


def test_the_real_cap_is_two_gigabytes():
    assert installer.DOWNLOAD_MAX_BYTES == 2 * 1024 ** 3


# ---------- AG · §22.4: the archive-slot wait is bounded ----------

def test_an_archive_slot_wait_times_out_as_marketplace_busy(home, tmp_path, monkeypatch):
    market = MarketplaceStore()
    market.load()
    f = write_catalog(tmp_path, [{"title": "Web", "path": "https://x.test/w.autowright"}])
    source = market.add(path=str(f))
    monkeypatch.setattr(marketplace, "IMAGE_SLOT_WAIT_S", 0.1)
    monkeypatch.setattr(transfer, "fetch_archive",
                        lambda url: pytest.fail("the download ran without a slot"))
    for _ in range(marketplace.MAX_CONCURRENT_ARCHIVE_DOWNLOADS):
        assert marketplace._archive_slots.acquire(timeout=5)
    try:
        with pytest.raises(MarketplaceBusy):
            market.entry_archive(source["id"], 0)
    finally:
        for _ in range(marketplace.MAX_CONCURRENT_ARCHIVE_DOWNLOADS):
            marketplace._archive_slots.release()


def test_an_archive_slot_is_released_after_a_failed_download(home, tmp_path, monkeypatch):
    market = MarketplaceStore()
    market.load()
    f = write_catalog(tmp_path, [{"title": "Web", "path": "https://x.test/w.autowright"}])
    source = market.add(path=str(f))

    def boom(url):
        raise transfer.TransferError("download failed")

    monkeypatch.setattr(transfer, "fetch_archive", boom)
    for _ in range(3):
        with pytest.raises(transfer.TransferError):
            market.entry_archive(source["id"], 0)
    taken = [marketplace._archive_slots.acquire(timeout=0)
             for _ in range(marketplace.MAX_CONCURRENT_ARCHIVE_DOWNLOADS)]
    for ok in taken:
        if ok:
            marketplace._archive_slots.release()
    assert all(taken)


# ---------- AG (N6) · §5.2: one deadline per URL import ----------

def test_the_github_listing_calls_and_the_download_share_one_deadline(monkeypatch):
    deadlines: list = []

    def fake_api(path, deadline=None):
        deadlines.append(deadline)
        if path.endswith("/releases/latest"):
            return None
        return [{"type": "file", "name": "a.autowright", "download_url": "https://x/a"}]

    class _Done:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def geturl(self):
            return "https://x/a"

        def read1(self, n):
            return b""

    def fake_open(req, *, timeout, refuse):
        return _Done()

    real_open_timeout = transfer.open_timeout

    def recording_open_timeout(deadline):
        deadlines.append(deadline)
        return real_open_timeout(deadline)

    monkeypatch.setattr(transfer, "_github_api", fake_api)
    monkeypatch.setattr(transfer, "open_https_only", fake_open)
    monkeypatch.setattr(transfer, "open_timeout", recording_open_timeout)
    data, resolved = transfer.fetch_archive("https://github.com/alice/repo")
    assert resolved == "https://x/a"
    assert len(deadlines) == 3
    assert all(d is deadlines[0] for d in deadlines), deadlines
    assert deadlines[0] is not None
