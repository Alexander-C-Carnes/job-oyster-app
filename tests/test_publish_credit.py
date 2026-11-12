"""The public snapshot commit credits other people's commits (tools/publish_public.py)."""
import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "publish_public.py"
pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="the publish script stays out of the public copy")


def load():
    spec = importlib.util.spec_from_file_location("publish_public", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def commit(repo, name, email, msg):
    subprocess.run(["git", "-c", f"user.name={name}", "-c", f"user.email={email}", "commit", "-q", "--allow-empty",
                    "-m", msg], cwd=repo, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()


def test_coauthors_credits_only_other_github_authors(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    base = commit(tmp_path, "Owner", "owner@some-mac.local", "start")
    commit(tmp_path, "Sam Doe", "123+samdoe@users.noreply.github.com", "a feature")
    commit(tmp_path, "Sam Doe", "123+samdoe@users.noreply.github.com", "another")
    commit(tmp_path, "Owner", "9+Owner@users.noreply.github.com", "merge button")
    commit(tmp_path, "Owner", "owner@some-mac.local", "local work")
    commit(tmp_path, "Claude", "noreply@anthropic.com", "a fix")
    mid = commit(tmp_path, "Pat Roe", "pat@example.com", "a real address is never published")
    head = commit(tmp_path, "Lee Poe", "leepoe@users.noreply.github.com", "old-style noreply")
    pp = load()
    assert pp.coauthors(base, head, "owner", cwd=tmp_path) == [
        "Co-authored-by: Lee Poe <leepoe@users.noreply.github.com>",
        "Co-authored-by: Sam Doe <123+samdoe@users.noreply.github.com>",
    ]
    assert pp.coauthors(mid, head, "owner", cwd=tmp_path) == ["Co-authored-by: Lee Poe <leepoe@users.noreply.github.com>"]
    assert pp.coauthors(head, head, "owner", cwd=tmp_path) == []
    assert pp.coauthors("nosuchrev", head, "owner", cwd=tmp_path) == []
