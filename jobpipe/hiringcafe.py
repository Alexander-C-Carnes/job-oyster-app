"""HiringCafe (hiringcafe.com) saved searches, checked on every pipeline run beside JobsPipe and Himalayas. Free.

A search is added from a HiringCafe link: a share link (hiringcafe.com/s/<code>, or hiring.cafe's) or a search page
whose ?searchState= holds every filter as JSON. A share link is resolved once, when the search is added, and the
decoded searchState is kept in searches.yaml beside the link, so a run doesn't depend on the share link.

HiringCafe renders its search page on its server: /classic?searchState=…&page=N carries that page's results (40 at
most) in the page's __NEXT_DATA__ JSON, and each result has the employer's own apply link. That link is the job's
url, so a job HiringCafe found is the one JobsPipe or Himalayas found at the same posting. A result carries only a
short summary, not the posting: a new result's HiringCafe listing (/job/<id>) is read for the whole posting text,
and a result with no apply link has it read for the link too. HiringCafe's robots.txt asks crawlers to keep off
search pages (it lets anyone read /job/); this reads one person's saved searches, a page or two each per run.

HiringCafe sits behind Cloudflare's bot check. Plain HTTP clients (httpx, curl) and headless Chromium get its "Just a
moment…" challenge (403, cf-mitigated: challenge) on every page; curl_cffi, which connects with Chrome's TLS and
HTTP/2 fingerprint, gets the pages (2026-10-17). If the check turns that away too, the search is logged as blocked
and the run goes on without it. Some networks' DNS filters answer hiringcafe.com with a block page, so its address
comes from DNS over HTTPS (doh.py), as JobsPipe's does.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.parse import parse_qs, urlencode, urlsplit

from . import doh, regions
from .config import HiringCafeSearch
from .jd import html_to_text, posting_key
from .store import Store, safe_id

HOST = "hiringcafe.com"
HOSTS = {HOST, "hiring.cafe"}       # hiring.cafe sends every page on to hiringcafe.com
ORIGIN = f"https://{HOST}"
SOURCE = "HiringCafe"
LINK_FIELDS = ("url", "final_url", "source_url", "apply_url", "listing_url")     # where a stored job keeps its posting's links

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', re.S)
_SHARE = re.compile(r"/s/([A-Za-z0-9_-]+)/?$")


class LinkError(ValueError):
    """Not a HiringCafe search link (said in words for the form)."""


class Unreadable(RuntimeError):
    """HiringCafe didn't give the page: its bot check, an error status or no connection."""


class Blocked(Unreadable):
    """HiringCafe's bot check, or a 403/429: it's turning this connection away for now, not just that page."""


@dataclass
class Page:
    status: int
    text: str = ""
    location: str = ""          # the redirect's target, when redirects aren't followed
    challenged: bool = False    # Cloudflare's "verify you are human" page instead of HiringCafe's


Fetch = Callable[[str, bool], Page]        # (url, follow redirects) -> Page


def browser_fetch(timeout: float = 30) -> Fetch:
    """GETs with Chrome's TLS fingerprint (curl_cffi), hiringcafe.com's address from DNS over HTTPS."""
    try:
        from curl_cffi import CurlOpt, requests
        from curl_cffi.requests.exceptions import RequestException
    except ImportError:
        raise Unreadable("curl_cffi isn't installed (run ./oyster update, or pip install -r requirements.txt)") from None
    ip, _ = doh.lookup(HOST)
    session = requests.Session(impersonate="chrome", timeout=timeout,
                               curl_options={CurlOpt.RESOLVE: [f"{HOST}:443:{ip}"]} if ip else None)

    def fetch(url: str, follow: bool = True) -> Page:
        try:
            r = session.get(url, allow_redirects=follow)
        except RequestException as e:
            raise Unreadable(f"couldn't connect ({e})") from None
        return Page(r.status_code, r.text, r.headers.get("location") or "", r.headers.get("cf-mitigated") == "challenge")
    return fetch


def _host(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def is_link(url: str) -> bool:
    return _host(url) in HOSTS


def state_in(url: str) -> Optional[dict]:
    """The search a link's ?searchState= holds, or None."""
    raw = (parse_qs(urlsplit(url).query).get("searchState") or [""])[0]
    try:
        state = json.loads(raw) if raw else None
    except ValueError:
        return None
    return state if isinstance(state, dict) else None


def search_url(state: dict, page: int = 0) -> str:
    q = {"searchState": json.dumps(state, separators=(",", ":"), ensure_ascii=False)}
    if page:
        q["page"] = str(page)
    return f"{ORIGIN}/classic?{urlencode(q)}"


def listing_url(raw: dict) -> str:
    """The result's own page on HiringCafe (robots.txt lets anyone read /job/)."""
    rid = raw.get("requisition_id") or ""
    return f"{ORIGIN}/job/{rid}" if rid else ""


def page_props(html: str) -> dict:
    m = _NEXT_DATA.search(html)
    try:
        return ((json.loads(m.group(1)) if m else {}).get("props") or {}).get("pageProps") or {}
    except ValueError:
        return {}


def describe(state: dict) -> str:
    """What a search asks for, in a line: its title words, workplace, places and how recent."""
    q = re.sub(r"\s+", " ", str(state.get("jobTitleQuery") or state.get("searchQuery") or "")).strip()
    places = [str(p.get("formatted_address")) for p in state.get("locations") or []
              if isinstance(p, dict) and p.get("formatted_address")]
    days = state.get("dateFetchedPastNDays")
    parts = [q or "Any title", ", ".join(state.get("workplaceTypes") or []), ", ".join(places),
             ", ".join(state.get("commitmentTypes") or []),
             f"posted in the last {days} day{'' if days == 1 else 's'}" if isinstance(days, int) and 0 < days < 1000 else ""]
    return " · ".join(p for p in parts if p)


def posting_text(raw: dict) -> str:
    """The whole posting a HiringCafe job carries (its listing page's does; a search result's doesn't), as text."""
    text = (raw.get("job_information") or {}).get("description") or ""
    return html_to_text(text) if "<" in text else text


def _num(v) -> Optional[float]:
    try:
        return float(v) if v not in (None, "") and float(v) > 0 else None
    except (TypeError, ValueError):
        return None


def to_job(raw: dict) -> dict:
    """A HiringCafe result in the shape the app stores (JobsPipe's field names), with `source: HiringCafe`."""
    v = raw.get("v5_processed_job_data") or {}
    info = raw.get("job_information") or {}
    url = raw.get("apply_url") or listing_url(raw)
    where = v.get("workplace_type") or ""
    lo, hi = _num(v.get("yearly_min_compensation")), _num(v.get("yearly_max_compensation"))
    cur = (v.get("listed_compensation_currency") or "").upper()
    pay = ""
    if lo or hi:
        k = lambda n: f"{n / 1000:,.0f}k"
        pay = f"{cur} {k(lo)}–{k(hi)}" if lo and hi and lo != hi else f"{cur} {k(hi or lo)}"
    posted = (v.get("estimated_publish_date") or "")[:10]
    countries = [c for c in v.get("workplace_countries") or [] if isinstance(c, str)]
    job = {
        "id": safe_id(url),         # a stored job at the same posting keeps its own id (Pipeline._hiringcafe)
        "job_title": info.get("title") or v.get("core_job_title") or "",
        "company": v.get("company_name") or (raw.get("enriched_company_data") or {}).get("name") or "",
        "url": url, "apply_url": raw.get("apply_url") or "", "listing_url": listing_url(raw), "source": SOURCE,
        "date_posted": posted if re.fullmatch(r"\d{4}-\d\d-\d\d", posted) else "",
        "location": v.get("formatted_workplace_location") or "",
        "country_codes": sorted({c for w in countries for c in ([w.upper()] if len(w) == 2 else regions.places(w))}),
        "remote": where == "Remote", "hybrid": where == "Hybrid",
        "seniority": v.get("seniority_level") or "",
        "employment_statuses": [c for c in v.get("commitment") or [] if isinstance(c, str)],
        "salary_string": pay.strip(),
        "description": posting_text(raw),       # "" when the listing wasn't read: never the result's short summary
    }
    if cur == "USD":
        job.update(min_annual_salary_usd=lo, max_annual_salary_usd=hi)
    return job


def links(job: dict) -> set[str]:
    """The posting's links a job carries, compared as jd.posting_key does."""
    return {k for k in (posting_key(job.get(f)) for f in LINK_FIELDS) if k}


def posting_index(store: Store) -> dict[str, str]:
    """Every stored job's posting links -> its id: what a HiringCafe result is matched against."""
    out: dict[str, str] = {}
    for jid in store.state():
        for k in links(store.job(jid)):
            out.setdefault(k, jid)
    return out


class HiringCafe:
    def __init__(self, *, fetch: Optional[Fetch] = None, max_pages: int = 3, pause: float = 1.0,
                 listing_pause: float = 3.0, log: Callable[[str], None] = lambda s: None):
        self._fetch = fetch             # made on first use: curl_cffi and the DNS lookup only when there's a search
        self.max_pages = max_pages      # 40 results a page; a search's newest week is rarely more than one
        self.pause = pause              # seconds between pages
        self.listing_pause = listing_pause      # seconds between listings: ~17 a second apart got the bot check (2026-10-28)
        self.blocked = False            # the bot check turned a listing away: no more listings until reset()
        self.log = log

    def reset(self) -> None:
        """A new run: read listings again."""
        self.blocked = False

    def _get(self, url: str, follow: bool = True) -> Page:
        if self._fetch is None:
            self._fetch = browser_fetch()
        p = self._fetch(url, follow)
        if p.challenged:
            raise Blocked("HiringCafe's bot check (Cloudflare) turned the request away")
        if p.status in (403, 429):
            raise Blocked(f"HiringCafe answered {p.status}")
        if p.status >= 400:
            raise Unreadable(f"HiringCafe answered {p.status}")
        return p

    def resolve(self, link: str) -> dict:
        """The search a HiringCafe link names: from its ?searchState=, or where its share link leads."""
        link = link.strip()
        if not is_link(link):
            raise LinkError("That isn't a HiringCafe link (hiringcafe.com or hiring.cafe).")
        state = state_in(link)
        if state is not None:
            return state
        m = _SHARE.search(urlsplit(link).path)
        if not m:
            raise LinkError("Paste a search's share link (hiringcafe.com/s/…) or the address of its search page.")
        try:
            to = self._get(f"{ORIGIN}/s/{m.group(1)}", follow=False).location
        except Unreadable as e:
            if re.search(r"\b(404|410)$", str(e)):
                raise LinkError("HiringCafe doesn't know that share link.") from None
            raise
        state = state_in(to)
        if state is None:
            raise LinkError("That share link doesn't lead to a search.")
        return state

    def results(self, state: dict) -> list[dict]:
        """The search's results, page by page, newest first as HiringCafe orders them."""
        got: list[dict] = []
        for page in range(self.max_pages):
            if page:
                time.sleep(self.pause)
            props = page_props(self._get(search_url(state, page)).text)
            if props.get("ssrError"):
                raise Unreadable(f"HiringCafe's search failed ({props['ssrError']})")
            hits = [h for h in props.get("ssrHits") or [] if isinstance(h, dict)]
            got.extend(hits)
            if not hits or props.get("ssrIsLastPage", True):
                break
        return got

    def _listing(self, raw: dict) -> dict:
        """A result filled in from its listing page: the posting text, and the apply link when it had none."""
        url = listing_url(raw)
        if self.blocked or not url:
            return raw
        time.sleep(self.listing_pause)
        try:
            job = page_props(self._get(url).text).get("job")
        except Blocked as e:
            self.blocked = True
            self.log(f"  HiringCafe: {e} at {url}, so no more listings are read this run")
            return raw
        except Unreadable as e:
            self.log(f"  HiringCafe: couldn't read {url} ({e})")
            return raw
        return {**raw, **job} if isinstance(job, dict) else raw

    def listing_text(self, url: str) -> str:
        """The posting text on a HiringCafe listing page, or "" when it can't be read."""
        try:
            job = page_props(self._get(url).text).get("job")
        except Unreadable:
            return ""
        return posting_text(job) if isinstance(job, dict) else ""

    def search(self, s: HiringCafeSearch, wanted: Callable[[dict], bool] = lambda job: True) -> list[dict]:
        """The search's open results as stored jobs, one per posting. A result's listing is read only when
        wanted(job) (the app has no posting text for it yet): for the posting, and the apply link when it has none.
        A stored job is matched by its listing too, so one whose result has no apply link isn't read again."""
        jobs: dict[str, dict] = {}
        for raw in self.results(s.search_state):
            if raw.get("is_expired"):
                continue
            if (not raw.get("apply_url") or not posting_text(raw)) and wanted(to_job(raw)):
                raw = self._listing(raw)
            job = to_job(raw)
            if job["url"] and job["job_title"]:
                jobs.setdefault(posting_key(job["url"]), job)
        return list(jobs.values())

