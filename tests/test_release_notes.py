import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import release_notes  # noqa: E402

OLD = """# Changelog

What changed in the app, newest first. Each entry names the commit that made it.

## 2026-03-02

### Tracker
- **A Denied status** (abc1234). Roles you were turned down for get their own stage.
  - with a sub-point that isn't an entry
  A second paragraph that isn't one either.

## 2026-03-01

### Startups
- **A Startups tab** (def5678, 9abcdef). Roles at **young** companies in one place.
"""

NEW = """# Changelog

## 2026-03-04

### Tracker
- **Filter by score** (1111111). Pick a range.

### Mac app
- **Check for Updates… in the menu** (2222222). Ask any time.

## 2026-03-03

### Tracker
- **Fold the job header** (3333333). More room.
""" + OLD.split("\n", 4)[4]


def test_entries_are_headlines_under_their_date_and_area():
    assert release_notes.entries(OLD) == [("2026-03-02", "Tracker", "A Denied status"),
                                          ("2026-03-01", "Startups", "A Startups tab")]


def test_public_changelog_has_headlines_only_and_leaves_out_released_ones():
    public = release_notes.headlines(NEW, omit=OLD)
    assert "Each release's notes" in public and "(1111111)" not in public and "Pick a range" not in public
    assert "## 2026-03-04\n\n### Tracker\n- Filter by score\n\n### Mac app\n- Check for Updates… in the menu" in public
    assert "A Denied status" not in public and "2026-03-02" not in public
    assert release_notes.entries(public) == [("2026-03-04", "Tracker", "Filter by score"),
                                             ("2026-03-04", "Mac app", "Check for Updates… in the menu"),
                                             ("2026-03-03", "Tracker", "Fold the job header")]


def test_whats_new_groups_new_headlines_by_area():
    assert release_notes.whats_new(OLD, NEW, "2026-03-02") == (
        "### Tracker\n- Filter by score\n- Fold the job header\n\n### Mac app\n- Check for Updates… in the menu")
    assert release_notes.whats_new(NEW, NEW, "2026-03-01") == ""
    # Before the last release's day: an old entry reworded, or one the release already had, isn't new.
    assert release_notes.whats_new("", NEW, "2026-03-03").count("- ") == 3
    assert "A Denied status" not in release_notes.whats_new("", NEW, "2026-03-03")


def test_cli_lists_what_head_adds_since_a_tag(tmp_path):
    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=tmp_path,
                       check=True, capture_output=True, env={**os.environ, "GIT_COMMITTER_DATE": "2026-03-02T12:00:00+0000"})
    git("init", "-q")
    (tmp_path / "CHANGELOG.md").write_text(release_notes.headlines(OLD))
    git("add", "."); git("commit", "-qm", "one"); git("tag", "v1.0.0")
    (tmp_path / "CHANGELOG.md").write_text(release_notes.headlines(NEW))
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "release_notes.py"), "v1.0.0"], cwd=tmp_path,
                         capture_output=True, text=True, check=True).stdout
    assert out == release_notes.whats_new(OLD, NEW, "2026-03-02") + "\n"
    assert subprocess.run([sys.executable, str(ROOT / "tools" / "release_notes.py"), "v9"], cwd=tmp_path,
                          capture_output=True, text=True).returncode != 0
