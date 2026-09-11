"""§19 `GET /automations/{id}/diff`: what changed between two stored versions.

Computed here, once, from stdlib difflib, so the §9.2 version diff modal and
the §20 `automation diff` command render one result and neither diffs. A
version is compared as the files its §5 folder holds — the manifest, the spec,
the notes, then one script per step — and every file is listed, unchanged ones
included, so a consumer can show the whole folder with the changes marked.
"""
from __future__ import annotations

import difflib
from typing import Any

from .specmd import blocks_to_md
from .storage import manifest_packages, manifest_step_entry, strip_param_values
from .yamlio import dump_yaml

# The document files, in navigator order; steps follow.
DOCUMENTS = (("manifest", "Manifest", "automation.yaml"),
             ("spec", "Spec", "spec.md"),
             ("notes", "Notes", "notes.md"))


def manifest_text(ver: dict) -> str:
    """The versioned manifest fields the §5 writer emits, through the same YAML
    dump, minus the `when` / `note` metadata that differs by construction — so
    the manifest side of a diff reads as the on-disk automation.yaml would."""
    pkgs = manifest_packages(ver)
    return dump_yaml({
        "params": strip_param_values(ver.get("params")),
        **({"packages": pkgs} if pkgs else {}),
        "steps": [manifest_step_entry(s, s.get("file") or "")
                  for s in ver.get("steps", []) or []],
    })


def notes_text(ver: dict) -> str | None:
    """§5: notes.md exists only when the notes are non-empty — an empty notes
    doc is an absent file, so adding notes reads as a new file."""
    notes = (ver.get("notes") or "").strip()
    return notes + "\n" if notes else None


def _lines(text: str) -> list[str]:
    """§9.2: a single trailing newline is neither rendered nor counted, and
    empty text is zero lines (not one empty line)."""
    if text.endswith("\n"):
        text = text[:-1]
    return text.split("\n") if text else []


def _step_keys(ver: dict) -> list[tuple[tuple[str, int], dict]]:
    """Steps keyed for matching across versions: the k-th step of a name
    matches the k-th of the same name on the other side (the §9.2 change
    badge's by-name rule), so a rename reads as one removed and one new."""
    seen: dict[str, int] = {}
    out = []
    for s in ver.get("steps", []) or []:
        name = s.get("name", "")
        k = seen.get(name, 0)
        seen[name] = k + 1
        out.append(((name, k), s))
    return out


def diff_lines(old: list[str], new: list[str]) -> list[dict]:
    """Side-by-side rows from SequenceMatcher opcodes. A `replace` block pairs
    line-for-line into `mod` rows; the longer side's leftover becomes `del` /
    `add` rows, so a two-column view renders straight from the list."""
    rows: list[dict] = []
    sm = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for i, j in zip(range(i1, i2), range(j1, j2)):
                rows.append({"kind": "same", "left": {"number": i + 1, "text": old[i]},
                             "right": {"number": j + 1, "text": new[j]}})
            continue
        left = list(range(i1, i2)) if tag in ("replace", "delete") else []
        right = list(range(j1, j2)) if tag in ("replace", "insert") else []
        for n in range(max(len(left), len(right))):
            i = left[n] if n < len(left) else None
            j = right[n] if n < len(right) else None
            rows.append({
                "kind": "mod" if i is not None and j is not None else ("del" if i is not None else "add"),
                "left": {"number": i + 1, "text": old[i]} if i is not None else None,
                "right": {"number": j + 1, "text": new[j]} if j is not None else None,
            })
    return rows


def _file(kind: str, name: str, file: str | None, old: str | None, new: str | None) -> dict:
    rows = diff_lines(_lines(old or ""), _lines(new or ""))
    added = sum(r["kind"] in ("add", "mod") for r in rows)
    removed = sum(r["kind"] in ("del", "mod") for r in rows)
    if old is None and new is None:
        status = "unchanged"
    elif old is None:
        status = "new"
    elif new is None:
        status = "removed"
    else:
        status = "changed" if added or removed else "unchanged"
    return {"kind": kind, "name": name, "file": file, "status": status,
            "added": added, "removed": removed, "rows": rows}


def diff_versions(old: dict, new: dict) -> list[dict]:
    """The §19 `files` list for two loaded version dicts (§5 internal shape):
    the three documents, then the steps in the NEW version's order with the
    old-only steps appended in their own order."""
    files = [
        _file("manifest", "Manifest", "automation.yaml", manifest_text(old), manifest_text(new)),
        _file("spec", "Spec", "spec.md", blocks_to_md(old.get("spec", []) or []),
              blocks_to_md(new.get("spec", []) or [])),
        _file("notes", "Notes", "notes.md", notes_text(old), notes_text(new)),
    ]
    old_steps = dict(_step_keys(old))
    new_steps = _step_keys(new)
    matched: set[tuple[str, int]] = set()
    for key, s in new_steps:
        o = old_steps.get(key)
        if o is not None:
            matched.add(key)
        files.append(_file("step", key[0], s.get("file"),
                           o.get("code", "") if o is not None else None, s.get("code", "")))
    for key, s in _step_keys(old):
        if key not in matched:
            files.append(_file("step", key[0], s.get("file"), s.get("code", ""), None))
    return files


def parse_version_label(label: Any) -> int | None:
    """"vN" (case-insensitive, as §19 execute's `version`) → N; None when it
    is anything else — the caller answers the 404."""
    if not isinstance(label, str):
        return None
    try:
        return int(label.strip().lower().lstrip("v"))
    except ValueError:
        return None
