"""CLI audit regressions (2026-10-31 second sweep).

§20 References: an exact-name lookup compares through the §4.1 rule
(trimmed, NFC-normalized, casefolded), never a plain `lower()`.
"""
import unicodedata

import pytest

from autowright import cli


def _items(*names):
    return [{"id": f"{i:08d}-0000-0000-0000-000000000000", "name": n}
            for i, n in enumerate(names, start=1)]


def test_a_precomposed_name_finds_one_stored_with_a_combining_accent():
    stored = unicodedata.normalize("NFD", "Café")
    assert stored != "Café"
    items = _items(stored, "Café au lait")
    assert cli._resolve(items, unicodedata.normalize("NFC", "café"), "automation") is items[0]


def test_an_uppercase_sharp_s_spelling_finds_the_name():
    items = _items("Straße", "Straßenbahn")
    assert cli._resolve(items, "STRASSE", "automation") is items[0]


def test_a_padded_reference_finds_the_trimmed_name():
    items = _items("feed", "feed reader")
    assert cli._resolve(items, "  feed ", "automation") is items[0]


def test_the_substring_step_compares_through_the_same_rule():
    items = _items(unicodedata.normalize("NFD", "Morning Café digest"), "Evening news")
    assert cli._resolve(items, "CAFÉ", "automation") is items[0]


def test_an_unmatched_reference_still_exits():
    with pytest.raises(SystemExit):
        cli._resolve(_items("feed"), "nothing", "automation")
