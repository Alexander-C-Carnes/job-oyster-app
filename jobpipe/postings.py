"""Is a posting still open? Roles in Find jobs are found once and otherwise never looked at again, so a role that
closed after it was found stays in the list. check_closed() asks the posting's page (or its applicant-tracking
system) and moves closed roles to Dismissed, tagged Closed. A tracked role you haven't applied to yet (one tracked
on its own, say) is checked too, but only tagged Closed: its status is yours to set.

A posting you applied to is kept for good, to read again before an interview after the page comes down.
keep_applied() saves the posting text of every tracked job in an applied stage that doesn't have it yet;
PostingKeeper runs it in the background whenever a status may have changed.
"""
from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import httpx

from . import jd
from .jd import MIN_DESCRIPTION_CHARS, MIN_PASTED_CHARS, linkedin_job_id, posting_url
from .notion import APPLIED_STATUSES
from .store import canonical_url, now_iso

UA = "Mozilla/5.0 (jobpipe; checks whether a posting is still open)"
RECHECK_HOURS = 20
CLOSED_TEXT = re.compile(r"no longer accepting applications|this job is no longer available|job is no longer available|"
                         r"position has been filled|this position has been closed|job has been closed|posting has closed|"
                         r"this job has expired|job posting has expired|this job is closed|no longer open|has been filled|"
                         r"the job you are looking for is no longer|this role has been filled|this opening is closed", re.I)
LINKEDIN_CLOSED = re.compile(r'closed-job|"applicationStatus"\s*:\s*"closed"|No longer accepting applications', re.I)


def is_closed(job: dict, client: httpx.Client) -> Optional[bool]:
    """True if the posting is closed, False if it's open, None if that can't be told (don't touch it)."""
    url = posting_url(job)
    if not url:
        return None
    p = urlsplit(url)
    host = p.netloc.lower().removeprefix("www.")
    try:
        if host.endswith("linkedin.com"):
            jid = linkedin_job_id(url)
            if not jid:
                return None
            r = client.get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}")
            if r.status_code == 404:
                return True
            if r.status_code != 200:
                return None
            return bool(LINKEDIN_CLOSED.search(r.text))
        m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([^/?&]+)/jobs/(\d+)", url) or re.search(r"[?&]gh_jid=(\d+)()", url)
        if m and m.group(1) and m.group(2):
            r = client.get(f"https://boards-api.greenhouse.io/v1/boards/{m.group(1)}/jobs/{m.group(2)}")
            return True if r.status_code == 404 else False if r.status_code == 200 else None
        m = re.search(r"jobs\.lever\.co/([^/?#]+)/([0-9a-f-]{36})", url)
        if m:
            r = client.get(f"https://api.lever.co/v0/postings/{m.group(1)}/{m.group(2)}")
            return True if r.status_code == 404 else False if r.status_code == 200 else None
        m = re.search(r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f-]{36})", url)
        if m:
            r = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{m.group(1)}")
            if r.status_code != 200:
                return None
            return not any(str(j.get("id")) == m.group(2) for j in r.json().get("jobs") or [])
        r = client.get(url)
        if r.status_code in (404, 410):
            return True
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return None
        return bool(CLOSED_TEXT.search(r.text))
    except (httpx.HTTPError, ValueError):
        return None


def check_closed(jobs_store: Any, *, tracked: Optional[dict[str, str]] = None, limit: int = 200, workers: int = 8,
                 client: Optional[httpx.Client] = None, log: Callable[[str], None] = lambda s: None) -> dict:
    """Re-check the roles that weren't checked in the last RECHECK_HOURS, oldest first: the untracked, undismissed
    ones in Find jobs, and the tracked ones not yet applied to or ruled out and not already found closed.
    `tracked`: stored job id -> its tracker status. A closed role is marked closed_at, and dismissed if it isn't
    tracked. Returns counts: checked, closed, unknown."""
    state, tracked = jobs_store.state(), tracked or {}
    since = (datetime.now(timezone.utc) - timedelta(hours=RECHECK_HOURS)).isoformat(timespec="seconds")

    def due(jid: str, st: dict) -> bool:
        if jid in tracked:
            return tracked[jid] not in (*APPLIED_STATUSES, "Not Applying") and not st.get("closed_at")
        return not st.get("dismissed")
    todo = [jid for jid, st in state.items() if due(jid, st) and (st.get("posting_checked") or "") < since]
    todo.sort(key=lambda j: state[j].get("posting_checked") or state[j].get("first_seen") or "")
    todo = todo[:limit]
    counts = {"checked": 0, "closed": 0, "unknown": 0}
    if not todo:
        log("Postings: every role was checked recently")
        return counts
    log(f"Postings: checking whether {len(todo)} role(s) are still open")
    c = client or httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": UA})

    def one(jid: str) -> tuple[str, Optional[bool]]:
        job = jobs_store.job(jid)
        return jid, (is_closed(job, c) if job else None)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for jid, closed in pool.map(one, todo):
            counts["checked"] += 1
            if closed is None:
                counts["unknown"] += 1
                jobs_store.update(jid, posting_checked=now_iso())
                continue
            if closed:
                counts["closed"] += 1
                job = jobs_store.job(jid)
                if jid in tracked:
                    jobs_store.update(jid, posting_checked=now_iso(), closed_at=now_iso())
                    log(f"  closed: {job.get('job_title')} @ {job.get('company')} (in your tracker as {tracked[jid] or 'no status'})")
                else:
                    jobs_store.update(jid, posting_checked=now_iso(), dismissed=True, closed_at=now_iso())
                    log(f"  closed: {job.get('job_title')} @ {job.get('company')}")
            else:
                jobs_store.update(jid, posting_checked=now_iso())
    log(f"Postings: {counts['closed']} closed of {counts['checked']} checked"
        + (f" ({counts['unknown']} couldn't be told)" if counts["unknown"] else ""))
    return counts


def has_posting(job: dict) -> bool:
    """The stored job holds the whole posting: text you pasted or the app read from its page, or a full listing."""
    text = (job.get("description") or "").strip()
    trusted = job.get("description_pasted") or job.get("posting_kept")
    return len(text) >= (MIN_PASTED_CHARS if trusted else MIN_DESCRIPTION_CHARS)


def keep_applied(store: Any, tracker: Any, *, skip: Callable[[str], bool] = lambda row_id: False,
                 read: Optional[Callable[[str], dict]] = None, log: Callable[[str], None] = lambda s: None) -> dict:
    """Save the posting text of every tracked job in an applied stage that doesn't have it yet, read from the
    job's link. A job only in the tracker is stored the way scoring stores it (id notion-<row id>) and linked to
    its row, so it opens with the posting. `skip(row_id)`: leave this row for now (it failed recently).
    Returns counts: kept, unreadable (the ids are in "failed")."""
    counts: dict = {"kept": 0, "unreadable": 0, "failed": []}
    rows = [r for r in tracker.rows() if r["status"] in APPLIED_STATUSES and r["job_url"] and not skip(r["page_id"])]
    by_url: Optional[dict[str, str]] = None
    for r in rows:
        jid = r["job_id"] if r["job_id"] and store.job(r["job_id"]) else None
        if jid is None:
            if by_url is None:      # read every stored job only when a row isn't linked to one
                by_url = {canonical_url(posting_url(j)): j["id"] for j in map(store.job, store.state()) if j}
            jid = by_url.get(canonical_url(r["job_url"]))
        job = store.job(jid) if jid else {}
        if job and has_posting(job):
            if r["job_id"] != jid:
                tracker.link_job(r["page_id"], jid)
            continue
        try:
            found = (read or jd.read_posting)(r["job_url"])
        except Exception:  # noqa: BLE001 - a page that can't be read is tried again later
            found = {}
        text = (found.get("description") or "").strip()
        if len(text) < MIN_PASTED_CHARS or len(text) <= len((job.get("description") or "").strip()):
            counts["unreadable"] += 1
            counts["failed"].append(r["page_id"])
            log(f"  couldn't read the posting for {r['name']}")
            continue
        if job:
            store.keep_posting(jid, text)
        else:
            title = r["name"].removesuffix(f" — {r['company']}") if r["company"] else r["name"]
            jid = "notion-" + r["page_id"]
            store.save_job({**{k: v for k, v in found.items() if k != "url"}, "id": jid, "job_title": title or found.get("job_title", ""),
                            "company": r["company"] or found.get("company", ""), "url": r["job_url"],
                            "description": text, "posting_kept": now_iso()}, "manual")
        tracker.link_job(r["page_id"], jid)
        counts["kept"] += 1
        log(f"  kept the posting for {r['name']}")
    return counts


class PostingKeeper:
    """Runs keep_applied() in a background thread, one pass at a time. kick() after a status may have changed:
    one set here, or a sync that brought one from Notion. A posting that couldn't be read is tried again after
    RECHECK_HOURS, so a closed page isn't fetched on every sync."""

    def __init__(self, store: Any, tracker: Any, read: Optional[Callable[[str], dict]] = None):
        self.store, self.tracker, self.read = store, tracker, read
        self.failed: dict[str, float] = {}       # tracker row id -> when its page last couldn't be read
        self._busy = threading.Lock()
        self._again = False

    def kick(self) -> None:
        if not self._busy.acquire(blocking=False):
            self._again = True
            return
        threading.Thread(target=self._run, daemon=True).start()

    def run_once(self) -> dict:
        cutoff = time.time() - RECHECK_HOURS * 3600
        counts = keep_applied(self.store, self.tracker, read=self.read, skip=lambda rid: self.failed.get(rid, 0) > cutoff)
        self.failed.update({rid: time.time() for rid in counts["failed"]})
        return counts

    def _run(self) -> None:
        try:
            while True:
                self._again = False
                try:
                    self.run_once()
                except Exception:  # noqa: BLE001 - the next kick tries again
                    pass
                if not self._again:
                    break
        finally:
            self._busy.release()
