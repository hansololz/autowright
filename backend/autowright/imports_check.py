"""Curated-import allowlist (§6.2), shared by draft-time validation and the
runtime executor. Step scripts may import the Python stdlib (minus the modules
the §3 bundle trim removes), the curated packages, and the version's declared
§6.2 packages — nothing else."""
from __future__ import annotations

import ast
import sys
from typing import Iterable

# §6.2: stdlib modules the §3 bundle trim removes from the shipped interpreter.
# Rejected by the allowlist in every mode, so a step never works on a developer
# checkout and fails in the release.
TRIMMED_STDLIB = {"tkinter", "_tkinter", "idlelib", "turtle", "turtledemo",
                  "ensurepip", "venv"}

ALLOWED_IMPORTS = (set(sys.stdlib_module_names) - TRIMMED_STDLIB) | {
    "autowright", "requests", "httpx", "bs4", "lxml", "feedparser", "dateutil", "yaml",
}


def disallowed_imports(code: str, extra: Iterable[str] = ()) -> list[str]:
    """Module names imported by `code` that aren't on the §6.2 allowlist.

    Same rule as §8 draft validation: every `import X` / `from X import …`
    (absolute, any nesting) is checked by its top-level package name.
    `extra` is the version's declared package imports (§6.2), accepted on top.
    Unparseable code returns [] — the syntax error surfaces at exec time.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return []
    allowed = ALLOWED_IMPORTS | set(extra)
    bad: list[str] = []
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods = [node.module.split(".")[0]]
        for mod in mods:
            if mod not in allowed and mod not in bad:
                bad.append(mod)
    return bad
