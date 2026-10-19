"""The pinned section: résumé markdown the candidate wants word for word in every tailored résumé.

It lives in the profile's references/pinned.md, usually as its own `## ... EXPERIENCE` section of
`**Company** | Title` rows with a date line and bullets. The writers are told it's coming and leave room
for it; tailor.py adds it after every agent step, right after PROFESSIONAL EXPERIENCE, so no merge, honesty
review or trim rewrites or cuts it. A block with no `## ` heading joins the end of PROFESSIONAL EXPERIENCE.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from . import config

FILE = "pinned.md"
AFTER = "PROFESSIONAL EXPERIENCE"
BEFORE = ("ADDITIONAL EXPERIENCE", "CORE SKILLS", "EDUCATION", "INTERESTS")   # where it goes without AFTER


def path() -> Path:
    return config.REFERENCES / FILE


def text() -> str:
    """The pinned block, or "" when the profile has none."""
    try:
        return path().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _heading(chunk: str) -> Optional[str]:
    return chunk[3:].split("\n", 1)[0].strip().upper() if chunk.startswith("## ") else None


def _split(md: str) -> list[str]:
    """The preamble, then one chunk per `## ` section (each keeps its heading line)."""
    return re.split(r"(?m)^(?=## )", md)


def strip(md: str, block: Optional[str] = None) -> str:
    """md without the pinned block (its sections, or its rows when it has no heading of its own)."""
    block = text() if block is None else block.strip()
    if not block:
        return md
    heads = {_heading(c) for c in _split(block)} - {None}
    if not heads:
        return md.replace(block + "\n\n", "").replace(block, "")
    return "".join(c for c in _split(md) if _heading(c) not in heads)


def apply(md: str, block: Optional[str] = None) -> str:
    """md with the pinned block in its place: replaces any copy an agent wrote, so it's always word for word."""
    block = text() if block is None else block.strip()
    if not block:
        return md
    chunks = _split(strip(md, block))
    heads = [_heading(c) for c in chunks]
    own_heading = block.startswith("## ")
    if AFTER in heads:
        i = heads.index(AFTER)
        if not own_heading:              # rows: the last entries of PROFESSIONAL EXPERIENCE
            chunks[i] = chunks[i].rstrip("\n") + "\n\n" + block + "\n\n"
            return "".join(chunks)
        at = i + 1
    else:
        at = next((i for i, h in enumerate(heads) if h in BEFORE), len(chunks))
        if not own_heading:
            block = f"## {AFTER}\n\n{block}"
    if at > 0 and not chunks[at - 1].endswith("\n\n"):
        chunks[at - 1] = chunks[at - 1].rstrip("\n") + "\n\n"
    chunks.insert(at, block + ("\n\n" if at < len(chunks) else "\n"))
    return "".join(chunks)


def note(block: Optional[str] = None) -> str:
    """What the writers, the merge and the trim step are told about it ("" without one)."""
    block = text() if block is None else block.strip()
    if not block:
        return ""
    words = len(re.findall(r"\b\w+\b", block))
    return ("\n\nPinned section: after you finish, the app adds the block below word for word, right after "
            f"PROFESSIONAL EXPERIENCE. It takes about {words} words of the length limit, so leave room for it. "
            "Don't write it into your resume, and don't repeat its points elsewhere (the summary may mention "
            "them).\n\n" + block)
