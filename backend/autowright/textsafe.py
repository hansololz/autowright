"""§5 / §19 text safety: lone UTF-16 surrogates (third sweep, 2026-10-31).

The store writes YAML and the API answers JSON, both UTF-8. A lone surrogate
(`"\\ud800"` in a JSON body, `\\udcXX` from a Linux path that isn't UTF-8) can't
be encoded as UTF-8, so one such string stored anywhere made every later
`GET /state` fail. Three seams share these helpers: the request-body door
(422), the §5.1 archive validator (TransferError), and the loader's read
seam (replacement with U+FFFD, warning).
"""
from __future__ import annotations

import re
from typing import Any, Callable

SURROGATE_RE = re.compile("[\ud800-\udfff]")

LONE_SURROGATE_ERROR = "text must not contain lone surrogate characters"


def has_lone_surrogate(text: str) -> bool:
    """Whether `text` holds any code point in the surrogate range. Python
    strings never hold a *paired* surrogate (a pair decodes to one code point),
    so any surrogate present is lone."""
    return bool(SURROGATE_RE.search(text))


def replace_lone_surrogates(text: str) -> str:
    """`text` with every surrogate replaced by U+FFFD."""
    return SURROGATE_RE.sub("�", text)


def walk_strings(obj: Any, fn: Callable[[str], str]) -> Any:
    """Rebuild `obj` (nested dict/list/tuple/str scalars) with `fn` applied to
    every string, keys included. Non-container, non-string values are returned
    as they are."""
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, dict):
        return {(fn(k) if isinstance(k, str) else k): walk_strings(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [walk_strings(v, fn) for v in obj]
    if isinstance(obj, tuple):
        return tuple(walk_strings(v, fn) for v in obj)
    return obj


def any_string(obj: Any, pred: Callable[[str], bool]) -> bool:
    """Whether `pred` holds for any string anywhere in `obj` (keys included)."""
    if isinstance(obj, str):
        return pred(obj)
    if isinstance(obj, dict):
        return any((isinstance(k, str) and pred(k)) or any_string(v, pred) for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return any(any_string(v, pred) for v in obj)
    return False
