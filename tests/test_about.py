import re
import subprocess

import httpx

from jobpipe import config
from jobpipe.web import about


def test_the_mac_app_reports_its_version(monkeypatch, tmp_path):
    monkeypatch.setenv("OYSTER_APP_VERSION", "v0.1.11")
    assert about.running(tmp_path) == {"version": "0.1.11"}


def test_a_checkout_reports_its_commit_and_date(monkeypatch, tmp_path):
    monkeypatch.delenv("OYSTER_APP_VERSION", raising=False)
    assert about.running(tmp_path) == {}                    # not a git checkout, and no version: say nothing
    git = lambda *a: subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *a], cwd=tmp_path,
                                    check=True, capture_output=True, text=True).stdout.strip()
    git("init", "-q", "-b", "main")
    (tmp_path / "f").write_text("x")
    git("add", "f")
    git("commit", "-q", "-m", "x")
    r = about.running(tmp_path)
    assert r == {"commit": git("rev-parse", "--short", "HEAD"), "date": git("log", "-1", "--format=%cs"), "branch": "main"}
    git("checkout", "-q", "--detach")
    assert "branch" not in about.running(tmp_path)


def test_the_newest_release_is_asked_for_once_then_kept(monkeypatch):
    calls = []
    latest = about.Latest(lambda: calls.append(1) or {"tag": "v0.1.11", "url": "u", "date": "2026-10-16"})
    assert latest.get()["tag"] == "v0.1.11" and latest.get()["tag"] == "v0.1.11" and len(calls) == 1
    offline = about.Latest(lambda: calls.append(1) or None)
    assert offline.get() is None
    assert 0 < offline._until - about.time.monotonic() <= 600       # offline: asks again in 10 minutes, not hours


def test_fetch_latest_reads_github_and_survives_being_offline(monkeypatch):
    body = {"tag_name": "v0.1.11", "html_url": f"{about.REPO_URL}/releases/tag/v0.1.11", "published_at": "2026-10-16T07:02:11Z"}
    monkeypatch.setattr(about.httpx, "get", lambda url, **kw: httpx.Response(200, json=body, request=httpx.Request("GET", url)))
    assert about.fetch_latest() == {"tag": "v0.1.11", "url": body["html_url"], "date": "2026-10-16"}

    def down(url, **kw):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(about.httpx, "get", down)
    assert about.fetch_latest() is None
    monkeypatch.setattr(about.httpx, "get", lambda url, **kw: httpx.Response(404, request=httpx.Request("GET", url)))
    assert about.fetch_latest() is None


def test_the_about_links_point_at_the_public_repo_and_its_guides():
    html = (config.ROOT / "jobpipe" / "web" / "static" / "index.html").read_text()
    links = re.findall(r'href="(https://github\.com/[^"]+)"', html)
    assert links and all(u.startswith(about.REPO_URL) for u in links)
    docs = re.findall(re.escape(about.REPO_URL) + r"/blob/main/([^\"#]+)", html)
    assert "docs/setup-from-code.md" in docs and "LICENSE" in docs
    for d in docs:
        assert (config.ROOT / d).is_file(), d
        assert d != "docs/ci.md"                            # private: the public copy leaves it out
