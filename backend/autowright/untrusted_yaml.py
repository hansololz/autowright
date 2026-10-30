"""§5 untrusted-YAML rule: a §5.1 archive member or a §22 catalog is loaded
with YAML anchors and aliases refused, and error text never embeds an
untrusted value's full repr.

PyYAML's safe loader expands aliases happily: a few hundred bytes of nested
`&a [*a, *a, …]` lists describe a billion-element structure, and `&a [*a]` a
self-referencing one. Nothing a byte cap checks bounds that, so the loader
here refuses the document at the first anchor or alias instead — no app
writer ever emits either, so a real archive or catalog never carries one.
"""
from __future__ import annotations

import reprlib
from typing import Any

import yaml
from yaml.events import AliasEvent

ALIAS_REFUSED = "anchors and aliases (& and *) aren't allowed"


class _NoAliasLoader(yaml.SafeLoader):
    """A SafeLoader that raises ValueError on any anchor or alias — checked
    while composing, before any structure is built from them."""

    def compose_node(self, parent, index):  # noqa: ANN001 — PyYAML's own signature
        event = self.peek_event()
        if isinstance(event, AliasEvent) or getattr(event, "anchor", None) is not None:
            raise ValueError(ALIAS_REFUSED)
        return super().compose_node(parent, index)


def load_untrusted(text: str) -> Any:
    """`yaml.safe_load` for untrusted text, with anchors and aliases refused
    (ValueError). Every other failure is PyYAML's own (YAMLError, ValueError
    for an impossible date, RecursionError for nesting too deep) — the
    callers' existing error set."""
    loader = _NoAliasLoader(text)
    try:
        return loader.get_single_data()
    finally:
        loader.dispose()


_excerpt_repr = reprlib.Repr()
_excerpt_repr.maxstring = 200
_excerpt_repr.maxother = 200
_excerpt_repr.maxlist = _excerpt_repr.maxtuple = _excerpt_repr.maxdict = 10
_excerpt_repr.maxset = _excerpt_repr.maxfrozenset = 10
_excerpt_repr.maxlevel = 3


def excerpt(value: Any, limit: int = 200) -> str:
    """An untrusted value for error text: a `reprlib` repr (bounded depth and
    width, so a huge or self-referencing structure is never walked whole),
    escaped like `repr` (control characters show as escapes), and at most
    `limit` characters."""
    text = _excerpt_repr.repr(value)
    if len(text) > limit:
        text = text[:max(0, limit - 1)] + "…"
    return text
