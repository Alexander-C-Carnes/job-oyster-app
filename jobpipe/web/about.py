"""What Profile's About card says about this copy of the app: which version runs, and the newest release.

The Mac app knows its version (the launcher passes OYSTER_APP_VERSION). A git checkout has no release number, since
releases are tagged in the public repo, so it shows the commit it runs and its date instead. The newest release
comes from GitHub's API, asked at most every few hours and only when the card is open; offline, it's left out.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import httpx

from .. import egress

REPO = "Alexander-C-Carnes/job-oyster-app"
REPO_URL = f"https://github.com/{REPO}"
LATEST_EVERY = 6 * 3600.0     # seconds a release lookup is kept (one that failed: 10 minutes)


def home_label(path: Path) -> str:
    """~/JobOyster when the folder sits under the home directory, otherwise the full path."""
    path = Path(path).expanduser()
    try:
        rel = path.resolve().relative_to(Path.home())
    except ValueError:
        return str(path)
    return "~" if not rel.parts else "~/" + rel.as_posix()


def outside(path: Path, folder: Path) -> bool:
    """True when path is not the folder and not inside it."""
    try:
        Path(path).expanduser().resolve().relative_to(Path(folder).expanduser().resolve())
    except ValueError:
        return True
    return False


def dirty(root: Path) -> bool:
    """True when the checkout has changes that are not in the commit About names."""
    try:
        status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return status.returncode == 0 and bool(status.stdout.strip())


def running(root: Path) -> dict:
    """{"version": "0.1.11"} in the Mac app; {"commit", "date", "branch"} from a git checkout; else {}."""
    v = os.environ.get("OYSTER_APP_VERSION", "").strip()
    if v:
        return {"version": v.removeprefix("v")}
    try:
        log = subprocess.run(["git", "log", "-1", "--format=%h%x09%cs"], cwd=root, capture_output=True, text=True,
                             timeout=10)
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=root, capture_output=True,
                                text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return {}
    if log.returncode or "\t" not in log.stdout:
        return {}
    commit, day = log.stdout.strip().split("\t")
    out = {"commit": commit, "date": day}
    if branch.returncode == 0 and branch.stdout.strip() not in ("", "HEAD"):
        out["branch"] = branch.stdout.strip()
    return out


def fetch_latest(timeout: float = 4.0) -> Optional[dict]:
    """The newest published release: {"tag", "url", "date"}, or None when GitHub can't be reached."""
    try:
        with egress.client(timeout=timeout) as c:
            r = c.get(f"https://api.github.com/repos/{REPO}/releases/latest",
                      headers={"Accept": "application/vnd.github+json", "User-Agent": "Job Oyster"})
        r.raise_for_status()
        d = r.json()
        return {"tag": d["tag_name"], "url": d["html_url"], "date": (d.get("published_at") or "")[:10]}
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return None


class Latest:
    """fetch_latest, kept for LATEST_EVERY so opening Profile again doesn't ask GitHub again."""

    def __init__(self, fetch: Optional[Callable[[], Optional[dict]]] = None):
        self.fetch = fetch      # None: fetch_latest, looked up when called (tests swap it)
        self._lock = threading.Lock()
        self._value: Optional[dict] = None
        self._until = 0.0

    def get(self) -> Optional[dict]:
        with self._lock:
            if time.monotonic() >= self._until:
                self._value = (self.fetch or fetch_latest)()
                self._until = time.monotonic() + (LATEST_EVERY if self._value else 600.0)
            return self._value
