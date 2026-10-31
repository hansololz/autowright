"""spec.md ↔ block-list conversion (§4.1 spec blocks: h1|h2|li|p, §5 spec.md)."""
from __future__ import annotations

import re

# §4.1: a numbered-list line keeps its own `p` block - merging "1. foo" and
# "2. bar" into one paragraph would garble the list on every round trip.
_NUMBERED_RE = re.compile(r"^\d+[.)] ")


def blocks_to_md(blocks: list[dict]) -> str:
    out: list[str] = []
    for b in blocks or []:
        k, text = b.get("kind"), (b.get("text") or "").rstrip()
        if k == "h1":
            out.append(f"# {text}")
        elif k == "h2":
            out.append(f"## {text}")
        elif k == "li":
            out.append(f"- {text}")
        else:
            out.append(text)
    # Blank line between blocks except between consecutive list items.
    lines: list[str] = []
    for i, ln in enumerate(out):
        if i and not (ln.startswith("- ") and lines and lines[-1].startswith("- ")):
            lines.append("")
        lines.append(ln)
    return "\n".join(lines) + "\n"


def md_to_blocks(md: str) -> list[dict]:
    blocks: list[dict] = []
    para: list[str] = []

    def flush() -> None:
        if para:
            blocks.append({"kind": "p", "text": " ".join(para)})
            para.clear()

    # Split on "\n" alone after normalising \r\n and \r: splitlines() also
    # breaks on U+2028, U+2029, U+0085 and \x0c, splitting a heading apart.
    for raw in (md or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        ln = raw.rstrip()
        if not ln.strip():
            flush()
        elif ln.startswith("# "):
            flush()
            blocks.append({"kind": "h1", "text": ln[2:].strip()})
        elif ln.startswith("## "):
            flush()
            blocks.append({"kind": "h2", "text": ln[3:].strip()})
        elif ln.lstrip().startswith("- "):
            flush()
            blocks.append({"kind": "li", "text": ln.lstrip()[2:].strip()})
        elif _NUMBERED_RE.match(ln.strip()):
            flush()
            blocks.append({"kind": "p", "text": ln.strip()})
        else:
            para.append(ln.strip())
    flush()
    return blocks
