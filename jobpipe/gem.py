"""Gem job boards (jobs.gem.com): one posting, or every open role, from Gem's public GraphQL.

A listing page is an empty shell. The script on it asks this same endpoint, which answers without
signing in. A removed posting is `null` with HTTP 200; the page itself stays 200, so the HTML
can't say whether the job closed.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlsplit

import httpx

from .jobs_api import usd_range

ENDPOINT = "https://jobs.gem.com/api/public/graphql"
_SLUG = re.compile(r"[A-Za-z0-9_-]+")

_POSTING = """
query ExternalJobPosting($boardId: String!, $extId: String!) {
  oatsExternalJobPosting(boardId: $boardId, extId: $extId) {
    title
    extId
    descriptionHtml
    firstPublishedTsSec
    compensationHtml
    locations { name city isoCountry isRemote }
    job { locationType teamDisplayName }
    jobPostSectionHtml { introHtml outroHtml }
  }
}
"""

_BOARD = """
query JobBoardList($boardId: String!) {
  oatsExternalJobPostings(boardId: $boardId) {
    jobPostings {
      title
      extId
      descriptionHtml
      firstPublishedTsSec
      compensationHtml
      locations { name city isoCountry isRemote }
      job { locationType teamDisplayName }
      jobPostSectionHtml { introHtml outroHtml }
    }
  }
  jobBoardExternal(vanityUrlPath: $boardId) { teamDisplayName }
}
"""


def split_url(url: str) -> tuple[str, str]:
    """(board, ext id) for a jobs.gem.com link. The ext id is "" on a board index
    (jobs.gem.com/acme) and on anything that isn't a Gem listing. An /application suffix is the
    same posting."""
    parts = [p for p in urlsplit((url or "").strip()).path.split("/") if p]
    host = urlsplit((url or "").strip()).netloc.lower().removeprefix("www.")
    if host != "jobs.gem.com":
        return "", ""
    if parts and parts[-1] == "application":
        parts = parts[:-1]
    board = parts[0] if parts and _SLUG.fullmatch(parts[0]) else ""
    ext = parts[1] if board and len(parts) > 1 and _SLUG.fullmatch(parts[1]) else ""
    return board, ext


def posting(board: str, ext_id: str, client: httpx.Client) -> Optional[dict]:
    """One posting: title, company, location, description, and pay when Gem states it.
    None when that posting is gone. Raises httpx.HTTPError or ValueError when Gem didn't answer,
    which is not the same as gone."""
    body = _ask(client, _POSTING, {"boardId": board, "extId": ext_id})
    data = body["data"]
    if "oatsExternalJobPosting" not in data or (body.get("errors") and data.get("oatsExternalJobPosting") is None):
        raise ValueError("Gem gave no posting")
    raw = data["oatsExternalJobPosting"]
    if not raw:
        return None
    got = _fields(raw)
    out = {"job_title": got["title"], "company": got["company"], "location": got["location"],
           "description": got["description"]}
    if got["remote"]:
        out["remote"] = True
    lo, hi = got["pay_usd"] or (None, None)
    if lo:
        out["min_annual_salary_usd"] = lo
    if hi:
        out["max_annual_salary_usd"] = hi
    return out


def board_jobs(board: str, client: httpx.Client) -> Optional[list[dict]]:
    """The board's open roles, [] when it has none, None when there is no such board.
    Raises httpx.HTTPError or ValueError when Gem didn't answer."""
    body = _ask(client, _BOARD, {"boardId": board})
    data = body["data"]
    if "jobBoardExternal" not in data or (body.get("errors") and data.get("jobBoardExternal") is None):
        raise ValueError("Gem gave no board")
    if data.get("jobBoardExternal") is None:
        return None
    out = []
    for raw in (data.get("oatsExternalJobPostings") or {}).get("jobPostings") or []:
        if not isinstance(raw, dict):
            continue
        got = _fields(raw)
        if not got["id"]:
            continue
        role = {"title": got["title"], "url": f"https://jobs.gem.com/{board}/{got['id']}",
                "location": got["location"], "remote": got["remote"], "description": got["description"],
                "posted": got["posted"], "id": got["id"]}
        if got["pay"]:
            role["pay"] = got["pay"]
        if got["pay_usd"]:
            role["pay_usd"] = list(got["pay_usd"])
        out.append(role)
    return out


def _ask(client: httpx.Client, query: str, variables: dict) -> dict:
    r = client.post(ENDPOINT, json={"query": query, "variables": variables})
    if r.status_code != 200:
        raise ValueError(f"Gem answered {r.status_code}")
    try:
        body = r.json()
    except ValueError as e:
        raise ValueError("Gem's answer wasn't JSON") from e
    if not isinstance(body, dict) or not isinstance(body.get("data"), dict):
        raise ValueError("Gem gave no data")
    return body


def _fields(raw: dict) -> dict:
    job = raw.get("job") if isinstance(raw.get("job"), dict) else {}
    locs = [x for x in (raw.get("locations") or []) if isinstance(x, dict)]
    where = []
    for loc in locs:
        line = (loc.get("name") or "").strip()
        if not line:
            line = ", ".join(x for x in ((loc.get("city") or "").strip(), (loc.get("isoCountry") or "").strip()) if x)
        if line and line not in where:
            where.append(line)
    sections = raw.get("jobPostSectionHtml") if isinstance(raw.get("jobPostSectionHtml"), dict) else {}
    pay = _plain(raw.get("compensationHtml") or "")
    text = _plain("\n".join(x for x in (
        sections.get("introHtml"), raw.get("descriptionHtml"), raw.get("compensationHtml"), sections.get("outroHtml")) if x))
    lo, hi = usd_range(pay)
    posted = ""
    ts = raw.get("firstPublishedTsSec")
    if isinstance(ts, (int, float)) and ts > 0:
        posted = datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    return {"title": (raw.get("title") or "").strip(), "company": (job.get("teamDisplayName") or "").strip(),
            "location": "; ".join(where),
            "remote": str(job.get("locationType") or "").upper() == "REMOTE" or any(loc.get("isRemote") for loc in locs),
            "description": text, "posted": posted, "pay": pay, "pay_usd": (lo, hi) if lo or hi else None,
            "id": str(raw.get("extId") or "")}


def _plain(html: Any) -> str:
    from .jd import html_to_text
    return html_to_text(str(html or ""))
