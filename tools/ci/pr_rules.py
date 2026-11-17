#!/usr/bin/env python3
"""The merge rules oyster-ci checks on a pull request besides the checks (docs/ci.md → Gate).

Changelog: the PR adds an entry to CHANGELOG.md (a "- **Headline** (sha). Paragraph" line under a date and area),
or its description has a line "Changelog: none — <why>" (for CI, docs or other internal work). The headline goes
public as release notes, which the privacy check in tools/ci/checks.sh covers.

Card: a PR with a CHANGELOG.md entry also adds or updates a what's new card (jobpipe/web/whats-new/<name>.md, which the
app shows when it next opens), or says why not in a line "Card: none — <why>" (a fix, wording, speed). The line can be
in the description or in one of the PR's commit messages, so oyster-ci's fixer, which can't edit descriptions, can add
it with a commit. A PR with "Changelog: none" needs no card.

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


CARDS = "jobpipe/web/whats-new/"


def says_none(value: str | None) -> bool:
    return bool(value) and value.lower().startswith("none")


def reason(value: str) -> str:
    return re.sub(r"^none\W*", "", value, flags=re.I).strip()


def card_problem(cards: list[str], body: str, commits: str) -> str | None:
    """Why the PR's what's new card (or its Card: line) is missing, or None. cards: the card files the PR adds or changes."""
    if cards:
        return None
    line = field(body, "Card") or field(commits, "Card")
    if says_none(line):
        return None if reason(line) else "`Card: none` needs a reason: `Card: none — <why>`"
    if line:
        return (f"`Card: {line}` but the PR adds no card: add {CARDS}<name>.md (its README says how), "
                "or say `Card: none — <why>`")
    return (f"no what's new card: add one in {CARDS} (its README says when), or a line `Card: none — <why>` "
            "to the description or a commit message")


def check(diff: str, body: str, cards: list[str] = (), commits: str = "") -> list[str]:
    """The rules this PR breaks, as one sentence each."""
    problems = []
    none = field(body, "Changelog")
    if not changelog_added(diff) and not says_none(none):
        problems.append("no CHANGELOG.md entry: add one, or a line `Changelog: none — <why>` to the description")
    elif says_none(none) and not reason(none):
        problems.append("`Changelog: none` needs a reason: `Changelog: none — <why>`")
    if changelog_added(diff) and (why := card_problem(list(cards), body, commits)):
        problems.append(why)
    return problems


def main() -> int:
    base, body_file = sys.argv[1], Path(sys.argv[2])
    diff = subprocess.run(["git", "diff", "--unified=0", base, "HEAD", "--", "CHANGELOG.md"],
                          capture_output=True, text=True, check=True).stdout
    git = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, check=True).stdout
    cards = [f for f in git("diff", "--name-only", "--diff-filter=AMR", base, "HEAD", "--", CARDS).split()
             if f.endswith(".md") and not f.endswith("/README.md")]
    problems = check(diff, body_file.read_text(), cards, git("log", "--format=%B", f"{base}..HEAD"))
    for p in problems:
        print(f"✘ {p}")
    if not problems:
        print("✔ PR rules: changelog, card")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
