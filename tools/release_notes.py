#!/usr/bin/env python3
"""What's new in a release, from CHANGELOG.md.

The private repo's CHANGELOG.md has a paragraph per change under its date and area; the public repo gets a
CHANGELOG.md of the headlines alone, written by tools/publish_public.py with headlines(). Each release's notes
list the headlines CHANGELOG.md gained since the release before it (the Mac app workflow runs this):

    python3 tools/release_notes.py v0.1.9     # what HEAD adds since that tag, as markdown; nothing if none

A headline counts when it's new since the tag and dated on or after the tag's day, so rewording an old entry
doesn't bring it back, and the first release after CHANGELOG.md arrives lists only that day's on.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

DATE = re.compile(r"^## (\d{4}-\d{2}-\d{2})\b")
ENTRY = re.compile(r"^- (?:\*\*(.+?)\*\*|(.+))")          # "- **Headline** (sha). Paragraph", or "- Headline"


def entries(text: str) -> list[tuple[str, str, str]]:
    """(date, area, headline) for each top-level bullet, in file order (newest first)."""
    out, day, area = [], "", ""
    for line in text.splitlines():
        if m := DATE.match(line):
            day, area = m.group(1), ""
        elif line.startswith("### "):
            area = line[4:].strip()
        elif day and (m := ENTRY.match(line)):
            out.append((day, area, (m.group(1) or m.group(2)).strip()))
    return out


def headlines(text: str, omit: str = "") -> str:
    """The public CHANGELOG.md: each entry's headline under its date and area, leaving out those `omit` has."""
    skip = {h for _, _, h in entries(omit)}
    lines = ["# Changelog", "", "What changed in the app, newest first. Each release's notes on GitHub list what it added."]
    day = area = None
    for d, a, h in entries(text):
        if h in skip:
            continue
        if d != day:
            lines += ["", f"## {d}"]
            day, area = d, None
        if a != area:
            lines += ["", f"### {a}"] if a else [""]
            area = a
        lines.append(f"- {h}")
    return "\n".join(lines) + "\n"


def whats_new(old: str, new: str, since: str) -> str:
    """The headlines `new` has and `old` hasn't, dated `since` or later, grouped by area (newest area first)."""
    seen = {h for _, _, h in entries(old)}
    areas: dict[str, list[str]] = {}
    for d, a, h in entries(new):
        if d >= since and h not in seen and h not in areas.get(a, []):
            areas.setdefault(a, []).append(h)
    out = []
    for a, hs in areas.items():
        out += ([f"### {a}"] if a else []) + [f"- {h}" for h in hs] + [""]
    return "\n".join(out).strip()


def git(*args: str) -> str:
    p = subprocess.run(["git", *args], capture_output=True, text=True)
    return p.stdout if p.returncode == 0 else ""


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    tag = sys.argv[1]
    since = git("log", "-1", "--format=%cs", tag).strip()
    if not since:
        sys.exit(f"No tag {tag} here (fetch with tags?)")
    new = Path("CHANGELOG.md").read_text() if Path("CHANGELOG.md").exists() else ""
    notes = whats_new(git("show", f"{tag}:CHANGELOG.md"), new, since)
    if notes:
        print(notes)


if __name__ == "__main__":
    main()
