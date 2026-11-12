#!/usr/bin/env python3
"""The merge rules oyster-ci checks on a pull request besides the checks (docs/ci.md → Gate).

Changelog: the PR adds an entry to CHANGELOG.md (a "- **Headline** (sha). Paragraph" line under a date and area),
or its description has a line "Changelog: none — <why>" (for CI, docs or other internal work). The headline goes
public as release notes, which the privacy check in tools/ci/checks.sh covers.

    python3 tools/ci/pr_rules.py <base> <description-file>   # in the PR's merge with base; exit 1 with the reasons
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ENTRY = re.compile(r"^\+- \S")


def field(body: str, key: str) -> str | None:
    """The value of a "Key: value" line in a PR description, ignoring HTML comments (the template's examples)."""
    text = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    m = re.search(rf"^[ \t]*(?:[-*][ \t]*)?\**{key}\**[ \t]*:\**[ \t]*(.+)$", text, flags=re.I | re.M)
    return m.group(1).strip() if m else None


def changelog_added(diff: str) -> bool:
    """The diff of CHANGELOG.md adds a top-level entry."""
    return any(ENTRY.match(line) for line in diff.splitlines())


def check(diff: str, body: str) -> list[str]:
    """The rules this PR breaks, as one sentence each."""
    problems = []
    none = field(body, "Changelog")
    if not changelog_added(diff) and not (none and none.lower().startswith("none")):
        problems.append("no CHANGELOG.md entry: add one, or a line `Changelog: none — <why>` to the description")
    elif none and none.lower().startswith("none") and not re.sub(r"^none\W*", "", none, flags=re.I).strip():
        problems.append("`Changelog: none` needs a reason: `Changelog: none — <why>`")
    return problems


def main() -> int:
    base, body_file = sys.argv[1], Path(sys.argv[2])
    diff = subprocess.run(["git", "diff", "--unified=0", base, "HEAD", "--", "CHANGELOG.md"],
                          capture_output=True, text=True, check=True).stdout
    problems = check(diff, body_file.read_text())
    for p in problems:
        print(f"✘ {p}")
    if not problems:
        print("✔ PR rules: changelog")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
