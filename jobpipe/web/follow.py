"""Keep the app on the latest main by itself: OYSTER_FOLLOW_MAIN=1 in the profile's .env.

For running the app from a git checkout (`oyster run`). Every few minutes it fetches origin/main, and when there are
new commits, the checkout is on main with no changes of its own and no run is going, it updates (`oyster update`:
the commits, then any new packages) and starts again in place: the same process and window, on the new code. A
checkout on another branch, with uncommitted changes, or with commits of its own is left alone. The Mac app isn't a
checkout and updates through its releases instead.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

EVERY = 300.0     # seconds between looks


def enabled() -> bool:
    return os.environ.get("OYSTER_FOLLOW_MAIN", "").strip().lower() in ("1", "true", "yes", "on")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=120)


def head(root: Path) -> str:
    p = _git(root, "rev-parse", "HEAD")
    return p.stdout.strip() if p.returncode == 0 else ""


def look(root: Path) -> tuple[str, str] | None:
    """(HEAD, origin/main) after fetching, when the checkout is on main with no changes of its own; else None."""
    try:
        if _git(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() != "main":
            return None
        changes = _git(root, "status", "--porcelain", "--untracked-files=no")
        if changes.returncode or changes.stdout.strip():
            return None
        if _git(root, "fetch", "-q", "origin", "main").returncode:
            return None
        target = _git(root, "rev-parse", "origin/main").stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    now = head(root)
    return (now, target) if now and target else None


def behind(root: Path, now: str, target: str) -> bool:
    """origin/main is ahead of HEAD and HEAD has nothing of its own."""
    return now != target and _git(root, "merge-base", "--is-ancestor", now, target).returncode == 0


def oyster_update(root: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "QUIET_RESTART_HINT": "1"}      # it would tell you to restart, which is the next step here
    return subprocess.run(["bash", str(root / "oyster"), "update"], cwd=root, capture_output=True, text=True,
                          env=env, timeout=1800)


def say(line: str) -> None:
    print(line, flush=True)


class Follower:
    """`restart` stops the server the way a restart does (runs carry on); serve() then starts again in place. Not a
    SIGTERM: uvicorn raises the signal again once it has shut down, which would end the process first."""

    def __init__(self, root: Path, busy: Callable[[], bool], restart: Callable[[], None],
                 log: Callable[[str], None] = say,
                 update: Callable[[Path], subprocess.CompletedProcess] = oyster_update):
        self.root, self.busy, self.log, self.update, self.restart = root, busy, log, update, restart
        self.started = head(root)       # the code this process loaded
        self.failed = ""                # origin/main's commit an update failed on, said once
        self.restarting = False

    def step(self) -> bool:
        """One look; True when it asked for a restart onto new code."""
        if self.restarting or self.busy():
            return False
        seen = look(self.root)
        if not seen:
            return False
        now, target = seen
        if behind(self.root, now, target):
            if target == self.failed:
                return False
            p = self.update(self.root)
            if p.returncode:
                self.failed = target
                why = (p.stdout + p.stderr).strip().splitlines()
                self.log(f"  Couldn't update to {target[:7]} on its own (OYSTER_FOLLOW_MAIN): "
                         f"{why[-1] if why else 'oyster update failed'}. The app keeps running as it is.")
                return False
            for line in p.stdout.strip().splitlines():
                self.log(f"  {line}")
            now = head(self.root)
        if not now or now == self.started:
            return False
        self.restarting = True
        self.log(f"  Restarting on {now[:7]} (OYSTER_FOLLOW_MAIN): nothing is running, so nothing is cut short.")
        self.restart()
        return True

    def start(self, every: float = EVERY) -> threading.Thread:
        def loop() -> None:
            while not self.restarting:
                threading.Event().wait(every)
                try:
                    self.step()
                except Exception as e:  # noqa: BLE001 - a look that goes wrong mustn't stop the app
                    self.log(f"  Following main: {e}")
        t = threading.Thread(target=loop, daemon=True, name="follow-main")
        t.start()
        return t


def exec_again() -> None:
    """Start this app again in its own process (and so its own window), on whatever code is on disk now."""
    args = sys.argv[1:]
    if "--no-open" not in args:
        args.append("--no-open")
    sys.stdout.flush()
    sys.stderr.flush()
    os.execv(sys.executable, [sys.executable, "-m", "jobpipe", *args])
