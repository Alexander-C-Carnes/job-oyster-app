"""oyster-ci's PR rules (tools/ci/pr_rules.py).."""
import importlib.util
import subprocess
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("pr_rules", Path(__file__).with_name("pr_rules.py"))
pr_rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr_rules)

ADDED = """@@ -5,0 +6,3 @@
+## 2026-10-17
+
+- **A new thing** (abc1234). It does this.
"""


def test_an_added_entry_passes_with_its_card():
    assert pr_rules.check(ADDED, "", ["jobpipe/web/whats-new/a-new-thing.md"]) == []


def test_no_entry_fails():
    assert "no CHANGELOG.md entry" in pr_rules.check("", "Some description")[0]
    # A heading alone isn't an entry
    assert pr_rules.check("@@ -5,0 +6 @@\n+## 2026-10-17\n", "")


def test_changelog_none_with_a_reason_passes():
    for line in ("Changelog: none — CI scripts only", "**Changelog:** none - docs only", "- Changelog: None (tests)"):
        assert pr_rules.check("", f"## What\n\nStuff\n\n{line}\n") == [], line


def test_changelog_none_needs_a_reason():
    assert "needs a reason" in pr_rules.check("", "Changelog: none")[0]


def test_the_templates_example_in_a_comment_doesnt_count():
    assert pr_rules.check("", "<!-- Changelog: none — CI scripts only -->")


# ---- the what's new card ------------------------------------------------------------------------------------

ENTRY = "+- **Prep kits** (abc1234). On Interviewing roles.\n"
CARD = ["jobpipe/web/whats-new/prep-kits.md"]


def test_a_change_needs_a_changelog_entry_or_a_reason():
    assert "no CHANGELOG.md entry" in pr_rules.check("", "")[0]
    assert pr_rules.check("", "Changelog: none — CI only") == []
    assert "needs a reason" in pr_rules.check("", "Changelog: none")[0]


def test_an_entry_needs_a_card_or_a_reason_for_none():
    assert pr_rules.check(ENTRY, "", CARD) == []
    assert pr_rules.check(ENTRY, "Card: none — a fix") == []
    assert pr_rules.check(ENTRY, "- **Card:** none — wording only") == []
    assert "no what's new card" in pr_rules.check(ENTRY, "")[0]
    assert "`Card: none` needs a reason" in pr_rules.check(ENTRY, "Card: none")[0]
    assert "adds no card" in pr_rules.check(ENTRY, "Card: prep-kits")[0]
    assert pr_rules.check(ENTRY, "Card: prep-kits", CARD) == []


def test_the_reason_can_come_in_a_commit_so_the_fixer_can_add_it():
    assert pr_rules.check(ENTRY, "", commits="Fix the filter\n\nCard: none — a fix\n") == []
    assert pr_rules.check(ENTRY, "<!-- Card: none — example -->") != []          # a template's example doesn't count


def test_internal_work_needs_no_card():
    assert pr_rules.check("", "Changelog: none — docs") == []


def test_the_gate_reads_the_cards_and_commits_from_git(tmp_path):
    git = lambda *a: subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *a], cwd=tmp_path,
                                    check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q", "-b", "main")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## 2026-11-17\n")
    cards = tmp_path / "jobpipe" / "web" / "whats-new"
    cards.mkdir(parents=True)
    (cards / "README.md").write_text("guide")
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    base = git("rev-parse", "HEAD")
    body = tmp_path / "body.txt"
    body.write_text("Adds prep kits.")
    run = lambda: subprocess.run([sys.executable, str(Path(__file__).with_name("pr_rules.py")), base, str(body)],
                                 cwd=tmp_path, capture_output=True, text=True)

    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## 2026-11-17\n\n### Job details\n- Prep kits\n")
    (cards / "README.md").write_text("guide, edited")                  # the guide isn't a card
    git("commit", "-qam", "Prep kits")
    r = run()
    assert r.returncode == 1 and "no what's new card" in r.stdout

    git("commit", "-q", "--allow-empty", "-m", "Card: none — a fix")
    assert run().returncode == 0

    git("reset", "-q", "--hard", "HEAD~1")
    (cards / "prep-kits.md").write_text("a card")
    git("add", "-A")
    git("commit", "-q", "-m", "Card for prep kits")
    r = run()
    assert r.returncode == 0 and "changelog, card" in r.stdout
