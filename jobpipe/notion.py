"""Stage 7: log jobs in the Notion job tracker (the Tasks data source).

Mirrors resume-job-fit's rules:
- Find an existing row by Job URL (ignoring tracking parameters) before creating one.
- New row: Name "<title> — <Company>", Company, Job URL, Status, Type "Apply to Job",
  Fit Score (impact-record score), Project relation, one-line Notes.
- Existing row: refresh Fit Score and the scores in Notes (keeping text you added),
  move Status to In progress only from Not started/empty, fill Job URL only if empty.
- Leave Due/Submitted, Priority, Contact and Resume Used for you.

Needs NOTION_TOKEN: an internal integration token, with the Tasks database shared to
that integration (Notion: ... menu on the database > Connections > add the integration).
"""
from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from .store import canonical_url

API = "https://api.notion.com/v1"
VERSION = "2025-09-03"  # data-source API
# "Interviewing" and "Offer" follow "Applied".
# "Denied": applied and turned down, e.g. not offered an interview (the dashboard calls it Rejected).
# "Ghosted": applied and never heard back. The app moves a job here on its own after tracker.ghost_after_days
# (searches.yaml) at Applied or Interviewing with no change of status; any other status can be picked again.
# "Not Applying": ruled out (a poor fit, or little chance of an interview). Kept so it isn't found and scored again.
STATUSES = ("Not started", "In progress", "Blocked", "Applied", "Interviewing", "Offer", "Denied", "Ghosted", "Done",
            "Not Applying")
# The stages that mean the application was sent: they count as applied on the dashboard.
APPLIED_STATUSES = ("Applied", "Interviewing", "Offer", "Denied", "Ghosted", "Done")
SCORE_NOTE_RE = re.compile(r"(?:Scored|Triaged) \d{4}-\d{2}-\d{2}[^\n]*")


class StatusRefused(RuntimeError):
    """Notion wouldn't set a Status, usually because its Status property has no option by that name."""


@dataclass
class TrackerEntry:
    title: str
    company: str
    url: str
    fit_score: Optional[float]
    notes: str                      # the one-line score note
    status: str = "In progress"     # "In progress" for tailored jobs, "Not started" for triaged ones
    body: list[str] | None = None   # page body paragraphs (markdown-ish, links allowed as plain URLs)


class NotionTracker:
    def __init__(self, data_source_id: str, project_page_id: str = "",
                 token: Optional[str] = None, client: Optional[httpx.Client] = None):
        token = token or os.environ.get("NOTION_TOKEN")
        if not token:
            raise RuntimeError("NOTION_TOKEN is not set (see docs/setup-from-code.md: Set up the Notion tracker)")
        self.ds = data_source_id
        self.project = project_page_id
        self.http = client or httpx.Client(timeout=30)
        self.headers = {"Authorization": f"Bearer {token}", "Notion-Version": VERSION,
                        "Content-Type": "application/json"}
        self.backoff = (0.6, 1.8)   # waits before the second and third try of a failed request

    def _req(self, method: str, path: str, json: Optional[dict] = None) -> dict:
        # Notion's 5xx and 429 answers are usually brief, so reads and status updates are tried again.
        # Creating a page is not: a 500 can arrive after the page was made, and a retry would add a second.
        waits = self.backoff if method in ("GET", "PATCH") or path.endswith("/query") else ()
        for wait in (*waits, None):
            r = self.http.request(method, API + path, headers=self.headers, json=json)
            if r.status_code < 400:
                return r.json()
            if wait is None or not (r.status_code == 429 or r.status_code >= 500):
                break
            time.sleep(wait)
        try:
            said = r.json().get("message") or r.text[:300]
        except ValueError:
            said = r.text[:300]
        if r.status_code >= 500:
            raise RuntimeError(f"Notion's servers returned an error ({r.status_code}: {said}). "
                               "That is on Notion's side; try again in a few minutes.")
        raise RuntimeError(f"Notion {method} {path} -> {r.status_code}: {said}")

    # ---- lookups -----------------------------------------------------------
    def find(self, url: str) -> Optional[dict]:
        """The tracker row for this posting, matching Job URL without query strings."""
        core = canonical_url(url)
        if not core:
            return None
        needle = core.split("://", 1)[-1]
        res = self._req("POST", f"/data_sources/{self.ds}/query", {
            "filter": {"property": "Job URL", "url": {"contains": needle}}, "page_size": 10})
        for page in res.get("results", []):
            if canonical_url(_prop_url(page)) == core:
                return page
        return None

    def list_jobs(self) -> list[dict]:
        """Every "Apply to Job" row, flattened: page_id, page_url, name, company, job_url,
        status, fit, notes, priority, due."""
        rows: list[dict] = []
        cursor = None
        while True:
            body: dict[str, Any] = {"filter": {"property": "Type", "select": {"equals": "Apply to Job"}},
                                    "page_size": 100}
            if cursor:
                body["start_cursor"] = cursor
            res = self._req("POST", f"/data_sources/{self.ds}/query", body)
            for page in res.get("results", []):
                p = page.get("properties", {})
                prio = (p.get("Priority") or {}).get("select")
                due = (p.get("Due/Submitted") or {}).get("date")
                rows.append({
                    "page_id": page["id"], "page_url": page.get("url", ""),
                    "name": _plain((p.get("Name") or {}).get("title", [])),
                    "company": _plain((p.get("Company") or {}).get("rich_text", [])),
                    "job_url": _prop_url(page), "status": _prop_status(page) or "",
                    "fit": (p.get("Fit Score") or {}).get("number"),
                    "notes": _plain((p.get("Notes") or {}).get("rich_text", [])),
                    "priority": prio.get("name") if prio else "",
                    "due": due.get("start") if due else "",
                    "resume_files": _files(p.get("Resume Used")),
                })
            if not res.get("has_more"):
                return rows
            cursor = res.get("next_cursor")

    def known_urls(self) -> dict[str, str]:
        """canonical Job URL -> Status, for every "Apply to Job" row (used to skip known jobs)."""
        return {canonical_url(r["job_url"]): r["status"] for r in self.list_jobs() if r["job_url"]}

    def set_status(self, page_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        try:
            self._req("PATCH", f"/pages/{page_id}", {"properties": {"Status": {"status": {"name": status}}}})
        except RuntimeError as e:
            # Notion's API can't add options to a Status property, so a missing one has to be added by hand.
            if "-> 400" in str(e):
                raise StatusRefused(f'Notion would not set Status to "{status}" ({e}). If its Status property has no '
                                   f'"{status}" option, add one in Notion, then sync again.') from e
            raise

    # ---- "Resume Used": the résumé sent with the application ----------------
    def resume_files(self, page_id: str) -> list[dict]:
        """The row's Resume Used files, with fresh download links (Notion's expire after an hour)."""
        page = self._req("GET", f"/pages/{page_id}")
        return _files(page.get("properties", {}).get("Resume Used"))

    def download(self, url: str) -> bytes:
        r = self.http.get(url, follow_redirects=True)   # a signed link: no Notion auth header
        r.raise_for_status()
        return r.content

    def attach_resume(self, page_id: str, name: str, data: bytes) -> None:
        """Upload a PDF and set it as the row's Resume Used."""
        up = self._req("POST", "/file_uploads", {"mode": "single_part", "filename": name,
                                                 "content_type": "application/pdf"})
        headers = {k: v for k, v in self.headers.items() if k != "Content-Type"}   # httpx sets the multipart one
        r = self.http.post(f"{API}/file_uploads/{up['id']}/send", headers=headers,
                           files={"file": (name, data, "application/pdf")})
        if r.status_code >= 400:
            raise RuntimeError(f"Notion file upload -> {r.status_code}: {r.text[:300]}")
        self._req("PATCH", f"/pages/{page_id}", {"properties": {"Resume Used": {"files": [
            {"type": "file_upload", "file_upload": {"id": up["id"]}, "name": name}]}}})

    # ---- writes ------------------------------------------------------------
    def upsert(self, e: TrackerEntry) -> tuple[str, str]:
        """Create or update the row. Returns (action, page_url)."""
        action, page = self.upsert_page(e)
        return action, page.get("url", "")

    def upsert_page(self, e: TrackerEntry) -> tuple[str, dict]:
        """Create or update the row. Returns (action, the page: its "id" and "url")."""
        page = self.find(e.url)
        if page is None:
            props: dict[str, Any] = {
                "Name": {"title": [_text(f"{e.title} — {e.company}")]},
                "Company": {"rich_text": [_text(e.company)]},
                "Job URL": {"url": e.url},
                "Status": {"status": {"name": e.status}},
                "Type": {"select": {"name": "Apply to Job"}},
                "Notes": {"rich_text": [_text(e.notes)]},
            }
            if e.fit_score is not None:
                props["Fit Score"] = {"number": e.fit_score}
            if self.project:
                props["Project"] = {"relation": [{"id": self.project}]}
            created = self._req("POST", "/pages", {
                "parent": {"type": "data_source_id", "data_source_id": self.ds},
                "properties": props,
                "children": [_paragraph(p) for p in (e.body or [])],
            })
            self._verify(created["id"], e)
            return "created", created

        props = {}
        if e.fit_score is not None:
            props["Fit Score"] = {"number": e.fit_score}
        old_notes = _plain(page["properties"].get("Notes", {}).get("rich_text", []))
        props["Notes"] = {"rich_text": [_text(merge_notes(old_notes, e.notes))]}
        status = _prop_status(page)
        # Only Not started/empty moves to In progress; every later stage is never moved back.
        if status in (None, "", "Not started") and e.status == "In progress":
            props["Status"] = {"status": {"name": "In progress"}}
        if not _prop_url(page):
            props["Job URL"] = {"url": e.url}
        self._req("PATCH", f"/pages/{page['id']}", {"properties": props})
        self._verify(page["id"], e)
        return "updated", page

    def _verify(self, page_id: str, e: TrackerEntry) -> None:
        page = self._req("GET", f"/pages/{page_id}")
        p = page["properties"]
        problems = []
        if not _prop_url(page):
            problems.append("Job URL")
        if e.fit_score is not None and p.get("Fit Score", {}).get("number") != e.fit_score:
            problems.append("Fit Score")
        if not _prop_status(page):
            problems.append("Status")
        if problems:
            raise RuntimeError(f"Notion row {page_id} did not save: {', '.join(problems)}")


def merge_notes(old: str, new_line: str) -> str:
    """Replace the previous score line, keep anything you wrote."""
    if SCORE_NOTE_RE.search(old or ""):
        return SCORE_NOTE_RE.sub(new_line, old, count=1)
    return f"{new_line}\n{old}".strip() if old else new_line


def _text(s: str) -> dict:
    return {"type": "text", "text": {"content": s[:2000]}}


def _paragraph(s: str) -> dict:
    # **Label:** prefixes become bold runs; URLs become links.
    runs: list[dict] = []
    m = re.match(r"\*\*(.+?)\*\*\s*(.*)", s, re.S)
    if m:
        runs.append({"type": "text", "text": {"content": m.group(1) + " "}, "annotations": {"bold": True}})
        s = m.group(2)
    for part in re.split(r"(https?://\S+)", s):
        if not part:
            continue
        if part.startswith("http"):
            runs.append({"type": "text", "text": {"content": part, "link": {"url": part}}})
        else:
            runs.append({"type": "text", "text": {"content": part[:2000]}})
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": runs}}


def _files(prop: Optional[dict]) -> list[dict]:
    """A files property as [{name, url}]: Notion-hosted files and external links alike."""
    out = []
    for f in (prop or {}).get("files") or []:
        url = (f.get("file") or f.get("external") or {}).get("url") or ""
        if url:
            out.append({"name": f.get("name") or url.rsplit("/", 1)[-1].split("?")[0], "url": url})
    return out


def _plain(rich: list[dict]) -> str:
    return "".join(r.get("plain_text") or r.get("text", {}).get("content", "") for r in rich)


def _prop_url(page: dict) -> str:
    return page.get("properties", {}).get("Job URL", {}).get("url") or ""


def _prop_status(page: dict) -> Optional[str]:
    st = page.get("properties", {}).get("Status", {}).get("status")
    return st.get("name") if st else None
