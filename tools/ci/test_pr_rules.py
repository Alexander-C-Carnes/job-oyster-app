"""oyster-ci's PR rules (tools/ci/pr_rules.py).."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("pr_rules", Path(__file__).with_name("pr_rules.py"))
pr_rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr_rules)

ADDED = """@@ -5,0 +6,3 @@
+## 2026-10-17
+
+- **A new thing** (abc1234). It does this.
"""


def test_an_added_entry_passes():
    assert pr_rules.check(ADDED, "") == []


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
