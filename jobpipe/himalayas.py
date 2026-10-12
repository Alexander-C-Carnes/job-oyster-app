"""Himalayas (himalayas.app): free remote jobs, read alongside JobsPipe for each saved filter.

Its public search API needs no key and costs nothing (https://himalayas.app/api). Its terms ask that we link back
to the job's Himalayas page and name Himalayas as the source: a stored job's url is that page and its `source` is
"Himalayas", which the app shows beside it. Every job there is remote; `locationRestrictions` names the countries
a candidate must live in (none: anywhere).

The search's `q` matches descriptions too, so titles are checked here the same way as startup roles. Without a
country the newest jobs are mostly American and a week is ~60 pages per title, so a filter with countries asks
once per country (worldwide jobs left out) plus once for worldwide-friendly jobs; a page or two covers a week.
Titles with the same words share a query, and each query is made once per run for every filter that needs it.
"""
from __future__ import annotations

import html
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import httpx

from . import regions
from .config import Search
from .jd import html_to_text
from .startups import role_fits
from .store import safe_id

API = "https://himalayas.app/jobs/api/search"
UA = "jobpipe (personal job search; https://github.com/Alexander-C-Carnes/job-search-app)"
PAGE_SIZE = 20               # the API's page size
WORLDWIDE = "*"              # the scope for jobs open to anyone, anywhere


def wants(search: Search) -> bool:
    """Himalayas lists only remote jobs: it answers filters that are remote or don't say (not hybrid or on-site)."""
    return bool(search.remote) or not search.work_arrangement


def _words(title: str) -> tuple[str, ...]:
    return tuple(sorted(set(re.findall(r"[a-z0-9]+", title.lower()))))


class Himalayas:
    def __init__(self, *, max_pages: int = 5, max_requests: int = 400, pause: float = 0.5, workers: int = 2,
                 backoff: float = 20,
                 client: Optional[httpx.Client] = None, log: Callable[[str], None] = lambda s: None):
        self.max_pages = max_pages              # per query: a country's week is rarely more than two
        self.max_requests = max_requests        # per run, so a big region (EMEA: 108 countries) can't flood it
        self.pause = pause
        self.workers = workers                  # queries at once: a region is ~30 of them (4 drew 429s)
        self.backoff = backoff                  # seconds to wait after a 429 before asking again
        self._lock = threading.Lock()
        self.client = client or httpx.Client(timeout=30, follow_redirects=True, headers={"User-Agent": UA})
        self.log = log
        self._pages: dict[tuple, list[dict]] = {}       # (title words, scope, days) -> raw jobs, shared across filters
        self.requests = 0
        self.limited = False                            # 429 or the request cap: stop asking this run

    def _get(self, params: dict) -> Optional[httpx.Response]:
        with self._lock:
            if self.limited:
                return None
            if self.requests >= self.max_requests:
                self.log(f"Himalayas: {self.max_requests} requests this run; the rest of it skips Himalayas")
                self.limited = True
                return None
            self.requests += 1
        time.sleep(self.pause)
        for wait in (self.backoff, self.backoff * 2, None):
            try:
                r = self.client.get(API, params=params)
            except httpx.HTTPError as e:
                self.log(f"Himalayas: couldn't search ({e})")
                return None
            if r.status_code != 429 or wait is None:
                break
            time.sleep(wait)                    # it publishes no limit: wait, then ask again
        if r.status_code == 429:
            with self._lock:
                if not self.limited:
                    self.log("Himalayas: asked to slow down (429); the rest of this run skips it")
                self.limited = True
            return None
        return r

    def _query(self, title: str, scope: str, days: Optional[int]) -> list[dict]:
        """The newest jobs for a title in one country (a code), worldwide-friendly ones (WORLDWIDE) or all (""),
        back to `days` ago."""
        key = (_words(title), scope, days)
        if key in self._pages:
            return self._pages[key]
        oldest = _since(days)
        where = ({"worldwide": "true"} if scope == WORLDWIDE else
                 {"country": scope, "exclude_worldwide": "true"} if scope else {})
        got: list[dict] = []
        for page in range(1, self.max_pages + 1):
            r = self._get({"q": title, "sort": "recent", "page": page, **where})
            if r is None:
                break
            try:
                jobs = (r.json().get("jobs") or []) if r.status_code == 200 else []
            except ValueError:
                jobs = []
            got.extend(jobs)
            last = _posted(jobs[-1]) if jobs else None
            if len(jobs) < PAGE_SIZE or (oldest and last and last < oldest):
                break
        with self._lock:
            if not self.limited:        # a query cut short isn't kept, so it isn't taken as the whole answer
                self._pages[key] = got
        return got

    def search(self, search: Search) -> list[dict]:
        """The newest Himalayas jobs that answer this filter (up to its limit), as stored jobs (JobsPipe's fields)."""
        if not wants(search) or not search.titles:
            return []
        codes = regions.codes(search.country)
        scopes = [*codes, WORLDWIDE] if codes else [""]
        oldest = _since(search.posted_within_days)
        found: dict[str, tuple] = {}
        titles = list({_words(t): t for t in search.titles}.values())
        asks = [(t, sc) for t in titles for sc in scopes]
        with ThreadPoolExecutor(self.workers) as pool:
            answers = list(pool.map(lambda a: self._query(a[0], a[1], search.posted_within_days), asks))
        for raw in (raw for raws in answers for raw in raws):
            posted = _posted(raw)
            if oldest and posted and posted < oldest:
                continue
            where = list(raw.get("locationRestrictions") or [])
            role = {"title": raw.get("title") or "", "location": "; ".join(where), "remote": True}
            if role_fits(role, search):
                job = to_job(raw)
                found.setdefault(job["id"], (posted or datetime.min.replace(tzinfo=timezone.utc), job))
        newest = sorted(found.values(), key=lambda pj: pj[0], reverse=True)
        return [job for _, job in newest[:search.limit]]


def _since(days: Optional[int]) -> Optional[datetime]:
    return datetime.now(timezone.utc) - timedelta(days=days) if days else None


def _posted(raw: dict) -> Optional[datetime]:
    try:
        return datetime.fromtimestamp(int(raw.get("pubDate")), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _num(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


PER_YEAR = {"annual": 1, "monthly": 12, "fortnightly": 26, "weekly": 52, "hourly": 2080}


def to_job(raw: dict) -> dict:
    """A Himalayas job in the shape the app stores (JobsPipe's field names), with `source: Himalayas`."""
    url = raw.get("guid") or raw.get("applicationLink") or ""
    where = list(raw.get("locationRestrictions") or [])
    codes = sorted({c for w in where for c in regions.places(w)})
    posted = _posted(raw)
    lo, hi = _num(raw.get("minSalary")), _num(raw.get("maxSalary"))
    per = PER_YEAR.get((raw.get("salaryPeriod") or "annual").lower(), 1)
    cur = (raw.get("currency") or "").upper()
    lo, hi = (lo * per if lo else None), (hi * per if hi else None)
    pay = ""
    if lo or hi:
        k = lambda n: f"{n / 1000:,.0f}k"
        pay = f"{cur} {k(lo)}–{k(hi)}" if lo and hi and lo != hi else f"{cur} {k(hi or lo)}"
    job = {
        "id": safe_id(url),         # as JobsPipe stores the Himalayas jobs it finds, so one found by both is one job
        "job_title": html.unescape(raw.get("title") or ""), "company": raw.get("companyName") or "",
        "url": url, "source": "Himalayas", "apply_url": raw.get("applicationLink") or "",
        "date_posted": posted.date().isoformat() if posted else "",
        "location": "; ".join(where) if where else "Remote, anywhere", "country_codes": codes,
        "remote": True, "seniority": ", ".join(raw.get("seniority") or []),
        "employment_statuses": [raw["employmentType"]] if raw.get("employmentType") else [],
        "salary_string": pay.strip(),
        "description": html_to_text(raw.get("description") or "") or raw.get("excerpt") or "",
    }
    if cur == "USD":
        job.update(min_annual_salary_usd=lo, max_annual_salary_usd=hi)
    return job
