"""OYSTER_FOLLOW_MAIN: the app pulls new commits on main and restarts on them when nothing is running."""
import subprocess
from pathlib import Path

import pytest

from jobpipe.web import follow

ID = ["-c", "user.name=Test", "-c", "user.email=test@example.com", "-c", "commit.gpgsign=false"]


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *ID, *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def commit(cwd: Path, name: str, text: str = "x") -> str:
    (cwd / name).write_text(text)
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", f"add {name}")
    return git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path):
    """(origin's other checkout, the app's checkout), both on main at the same commit."""
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    other = tmp_path / "other"
    git(tmp_path, "clone", "-q", str(origin), str(other))
    git(other, "checkout", "-q", "-b", "main")
    commit(other, "a.txt")
    git(other, "push", "-q", "origin", "main")
    app = tmp_path / "app"
    git(tmp_path, "clone", "-q", str(origin), str(app))
    return other, app


def pull(root: Path):
    out = git(root, "pull", "-q", "--ff-only")
    return subprocess.CompletedProcess([], 0, stdout=f"Updated:\n  {out}", stderr="")


class Recorder:
    def __init__(self):
        self.restarts, self.lines = 0, []

    def restart(self):
        self.restarts += 1

    def log(self, line):
        self.lines.append(line)


def follower(app: Path, busy=lambda: False, update=pull):
    r = Recorder()
    return follow.Follower(app, busy, r.restart, log=r.log, update=update), r


def test_enabled_reads_the_setting(monkeypatch):
    monkeypatch.delenv("OYSTER_FOLLOW_MAIN", raising=False)
    assert not follow.enabled()
    for on in ("1", "true", "Yes", "on"):
        monkeypatch.setenv("OYSTER_FOLLOW_MAIN", on)
        assert follow.enabled()
    monkeypatch.setenv("OYSTER_FOLLOW_MAIN", "0")
    assert not follow.enabled()


def test_nothing_new_does_nothing(repos):
    _, app = repos
    f, r = follower(app)
    assert not f.step()
    assert r.restarts == 0


def test_new_commits_on_main_update_and_restart(repos):
    other, app = repos
    f, r = follower(app)
    new = commit(other, "b.txt")
    git(other, "push", "-q", "origin", "main")
    assert f.step()
    assert git(app, "rev-parse", "HEAD") == new
    assert r.restarts == 1
    assert any(new[:7] in line for line in r.lines)
    assert not f.step()                        # once restarting, it waits for the restart
    assert r.restarts == 1


def test_waits_while_a_run_is_going(repos):
    other, app = repos
    going = {"busy": True}
    f, r = follower(app, busy=lambda: going["busy"])
    commit(other, "b.txt")
    git(other, "push", "-q", "origin", "main")
    before = git(app, "rev-parse", "HEAD")
    assert not f.step()
    assert git(app, "rev-parse", "HEAD") == before   # not even pulled: new files under an old server don't mix
    going["busy"] = False
    assert f.step()
    assert r.restarts == 1


@pytest.mark.parametrize("setup", ["changes", "branch", "own commit"])
def test_leaves_someones_work_alone(repos, setup):
    other, app = repos
    commit(other, "b.txt")
    git(other, "push", "-q", "origin", "main")
    if setup == "changes":
        (app / "a.txt").write_text("edited")
    elif setup == "branch":
        git(app, "checkout", "-q", "-b", "feature")
    else:
        commit(app, "mine.txt")
    before = git(app, "rev-parse", "HEAD")
    f, r = follower(app)
    assert not f.step()
    assert git(app, "rev-parse", "HEAD") == before
    assert r.restarts == 0


def test_untracked_files_dont_count_as_changes(repos):
    other, app = repos
    (app / "notes.txt").write_text("not in git")
    commit(other, "b.txt")
    git(other, "push", "-q", "origin", "main")
    f, r = follower(app)
    assert f.step()


def test_restarts_when_someone_else_pulled(repos):
    other, app = repos
    f, r = follower(app)
    commit(other, "b.txt")
    git(other, "push", "-q", "origin", "main")
    git(app, "pull", "-q", "--ff-only")        # a pull by hand: the files moved, the app still runs the old code
    assert f.step()
    assert r.restarts == 1


def test_a_failed_update_is_said_once_and_the_app_keeps_running(repos):
    other, app = repos
    calls = []

    def broken(root):
        calls.append(root)
        return subprocess.CompletedProcess([], 1, stdout="", stderr="pip: no network")

    f, r = follower(app, update=broken)
    commit(other, "b.txt")
    git(other, "push", "-q", "origin", "main")
    assert not f.step()
    assert not f.step()
    assert len(calls) == 1
    assert r.restarts == 0
    assert "pip: no network" in r.lines[0]


def test_exec_again_keeps_the_arguments_and_doesnt_open_the_browser(monkeypatch):
    seen = {}
    monkeypatch.setattr(follow.sys, "argv", ["/x/jobpipe/__main__.py", "serve", "--port", "8765"])
    monkeypatch.setattr(follow.os, "execv", lambda exe, argv: seen.update(exe=exe, argv=argv))
    follow.exec_again()
    assert seen["argv"][1:] == ["-m", "jobpipe", "serve", "--port", "8765", "--no-open"]
