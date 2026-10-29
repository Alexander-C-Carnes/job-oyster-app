"""Startup search: find startups, learn which funding round they're on, and pull their open roles.

Sources (all free, no key):
- Funding news: Google News searches ("raises" "Series A", ...) and the TechCrunch and Crunchbase News
  feeds. A headline such as "Acme raises $20M Series B" is parsed into the company, the amount and the round.
- The Y Combinator directory, through the yc-oss mirror of YC's public index (rebuilt daily, no key):
  batch, whether the company says it's hiring, team size, industries, website.
Optional, with a key in .env: Fundable (FUNDABLE_API_KEY) or People Data Labs (PDL_API_KEY) company
lookups, which give the latest round of a company the news didn't cover.

Each startup is kept in data/startups.json, merged across sources by its name (and website).
Open roles come from the company's own careers board (Greenhouse, Lever (US or EU host) or Ashby public APIs),
found from its website, so they cost no JobsPipe credits. A role added from here is stored like
any other job, with a `funding` block the board shows as a round chip.
"""
from __future__ import annotations

import html
import json
import re
import threading
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import quote, urljoin, urlsplit

import httpx

from . import regions
from .jd import html_to_text
from .store import _read, _write, now_iso, slug

UA = "Mozilla/5.0 (jobpipe startup search; personal job search tool)"

# The rounds, in order. "Growth" is a late or unnamed late-stage round; "Unknown" is a raise whose
# headline didn't name the round (the amount is still shown).
STAGES = ("Pre-seed", "Seed", "Series A", "Series B", "Series C", "Series D+", "Growth", "Unknown")

# ---- funding headlines -------------------------------------------------------------------------------
_VERB = (r"(?:raises|raised|has raised|secures|secured|closes|closed|lands|landed|nabs|nabbed|bags|bagged|grabs|snags|"
         r"announces|announced|picks up|picked up|gets|scores|pulls in|collects|attracts|brings in|receives|wins|"
         r"completes|completed|rakes in|locks in|locked in|banks)")
_ROUND = (r"(?P<round>pre[- ]?seed|seed(?:[- ]stage)?(?:\s+(?:extension|round|funding))?|angel|"
          r"series\s?[A-K](?:\s?[1-9]|\s?extension|\s?prime|\+)?|growth(?:[- ]stage)?|late[- ]stage|bridge|"
          r"mezzanine|pre[- ]ipo|convertible note|venture debt|debt)")
_AMOUNT = (r"(?P<cur>US\$|USD|\$|€|EUR|£|GBP|C\$|CAD|A\$|AUD|₹|INR|¥|JPY|CHF|SEK|NOK|DKK|SGD|HK\$)?\s?"
           r"(?P<amt>\d{1,3}(?:[,.]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s?(?P<unit>mm|million|mn|m|billion|bn|b|k|thousand)?\b")
HEADLINE_RE = re.compile(rf"^(?P<name>.+?)\s+{_VERB}\s+(?:a\s+|an\s+|its\s+|new\s+|fresh\s+|another\s+|\w+-figure\s+)?"
                         rf"(?:{_AMOUNT})?", re.I)
ROUND_RE = re.compile(_ROUND, re.I)
AMOUNT_RE = re.compile(_AMOUNT, re.I)
_NOT_COMPANY = re.compile(r"^(?:the|this|that|how|why|what|when|where|exclusive|breaking|report|sources|"
                          r"who|a|an|here's|watch|inside|opinion|analysis)\b", re.I)
# Apposition after the name: "Acme, a London-based fintech, raises" -> "Acme".
_APPOSITION = re.compile(r"^(?P<name>[^,]+?)\s*,\s+(?:an?|the)\s+.+$", re.I)
# "Fintech startup Acme", "a16z-backed Acme", "Denmark's Acme", "Nine-person Acme": the name is what follows.
_PREFIX = re.compile(r"^(?:[\w.’'&-]+\s+){0,6}?(?:startup|company|firm|unicorn|maker|developer|provider|platform|app|agent|tool|"
                     r"lab|labs|network|marketplace|builder|biotech|fintech|healthtech|edtech|insurtech|proptech|cleantech|"
                     r"agtech|legaltech|regtech|martech|adtech|climate tech|deep tech|deeptech|spinout|spin-out|spinoff|"
                     r"[\w.-]+-backed|[\w-]+-based|[\w-]+-person|[\w-]+-founded|[\w-]+-led)\s+(?=\S)", re.I)
_POSSESSIVE = re.compile(r"^[A-Z][\w.-]*(?:’s|'s)\s+(?=[A-Z0-9])")
_LEGAL = re.compile(r"[,.]?\s+(inc|llc|ltd|limited|corp|corporation|co|gmbh|sas|ag|plc|bv|pty|s\.a\.|ab|oy|ltda)\.?$", re.I)
_UNITS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6, "mn": 1e6, "b": 1e9, "bn": 1e9, "billion": 1e9}
_TO_USD = {"$": 1.0, "us$": 1.0, "usd": 1.0, "€": 1.08, "eur": 1.08, "£": 1.27, "gbp": 1.27, "c$": 0.73, "cad": 0.73,
           "a$": 0.65, "aud": 0.65, "₹": 0.012, "inr": 0.012, "¥": 0.0067, "jpy": 0.0067, "chf": 1.12, "sek": 0.095,
           "nok": 0.092, "dkk": 0.145, "sgd": 0.75, "hk$": 0.128}   # rough, for sorting and the chip only


def company_key(name: str) -> str:
    """'Acme, Inc.' and 'ACME' are one company."""
    s = _LEGAL.sub("", (name or "").strip())
    s = re.sub(r"[’'`]", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def normalize_stage(round_name: Optional[str]) -> str:
    r = (round_name or "").lower().replace("-", " ").strip()
    if not r:
        return "Unknown"
    if "pre seed" in r or r == "preseed" or r == "angel":
        return "Pre-seed"
    if r.startswith("seed"):
        return "Seed"
    m = re.match(r"series\s?([a-k])", r)
    if m:
        letter = m.group(1).upper()
        return f"Series {letter}" if letter in "ABC" else "Series D+"
    if any(w in r for w in ("growth", "late stage", "pre ipo", "mezzanine")):
        return "Growth"
    return "Unknown"


def round_label(round_name: Optional[str]) -> str:
    """'series b' -> 'Series B', 'pre-seed' -> 'Pre-seed', 'seed round' -> 'Seed'."""
    r = re.sub(r"\s+", " ", (round_name or "").strip())
    if not r:
        return ""
    r = re.sub(r"\s+(round|funding|stage)$", "", r, flags=re.I)
    r = re.sub(r"(?i)^pre[- ]?seed", "Pre-seed", r)
    r = re.sub(r"(?i)^seed", "Seed", r)
    r = re.sub(r"(?i)^series\s?([a-k])", lambda m: "Series " + m.group(1).upper(), r)
    r = re.sub(r"(?i)^pre[- ]ipo", "Pre-IPO", r)
    return r[0].upper() + r[1:]


def parse_amount(text: str) -> tuple[Optional[float], str, Optional[float]]:
    """(amount, currency symbol, amount in USD) from '$20M', '€3.5 million', '$1.2B'."""
    m = AMOUNT_RE.search(text or "")
    if not m or not m.group("cur"):
        return None, "", None
    try:
        amt = float(m.group("amt").replace(",", ""))
    except ValueError:
        return None, "", None
    amt *= _UNITS.get((m.group("unit") or "").lower(), 1.0)
    if amt < 50_000:                         # "$5" or "$20" is a typo or a price, not a raise
        return None, "", None
    cur = m.group("cur")
    return amt, cur, amt * _TO_USD.get(cur.lower(), 1.0)


def parse_headline(title: str) -> Optional[dict]:
    """A funding headline -> {company, round, stage, amount, currency, amount_usd}, else None.

    "Acme raises $20M Series B to ..." -> Acme / Series B / 20,000,000.
    Headlines about a fund ("X Ventures closes $200M fund"), an acquisition or a job cut are skipped."""
    t = html.unescape(re.sub(r"\s+", " ", title or "")).strip()
    t = re.sub(r"\s*[|–—-]\s*(TechCrunch|FinSMEs|Crunchbase News|VentureBeat)$", "", t, flags=re.I)
    if re.search(r"\b(fund(?:s)?\b(?! raising)|acqui|lays off|layoffs|cuts|shuts down|files for|ipo\b|goes public|"
                 r"debt facility|credit facility|grant|prize|loan)", t, re.I) and not re.search(r"\bseries\s?[a-k]\b|\bseed\b", t, re.I):
        return None
    m = HEADLINE_RE.match(t)
    if not m:
        return None
    name = m.group("name").strip(" :;–—-")
    name = re.sub(r"^(?:exclusive|breaking|report|scoop|watch|update|news|funding)\s*[:|–—-]\s*", "", name, flags=re.I)
    name = re.sub(r"^[^|]{0,30}\|\s*", "", name)      # "Outlet | Acme raises"
    ap = _APPOSITION.match(name)
    if ap:
        name = ap.group("name").strip()
    name = re.sub(r"\s*\((?:[A-Z]{2,6}|[^)]{0,30})\)$", "", name).strip()   # "Acme (NASDAQ: ACME)"
    name = _company_name(name)
    if not name or len(name) > 60 or _NOT_COMPANY.match(name) or name.lower().endswith((" ventures", " capital", " partners", " fund")):
        return None
    tail = t[m.end("name"):]
    rm = ROUND_RE.search(tail)
    amount, cur, usd = parse_amount(tail)
    if not rm and amount is None:
        return None
    rnd = round_label(rm.group("round")) if rm else ""
    return {"company": name, "round": rnd, "stage": normalize_stage(rnd), "amount": amount, "currency": cur,
            "amount_usd": usd}


def _company_name(name: str) -> str:
    """The company in the words before the verb: 'Fintech startup Acme' -> 'Acme', 'Ex-Tesla team' -> ''."""
    name = name.strip(" \"'“”‘’")
    name = _POSSESSIVE.sub("", name)
    name = _PREFIX.sub("", name)
    words = name.split()
    if len(words) >= 2:
        # Keep the trailing run of capitalized words: "Viral AI agent Instinct" -> "Instinct". A name whose
        # last word is lowercase ("Two Google alumni", "Ex-Tesla team") isn't a company.
        run = []
        for w in reversed(words):
            if w[0].isupper() or w[0].isdigit() or w in ("&", "of", "and", "de"):
                run.insert(0, w)
            else:
                break
        while run and run[0] in ("&", "of", "and", "de"):
            run.pop(0)
        if not run:
            return ""
        if run[0][0].isupper() or run[0][0].isdigit():
            name = " ".join(run)
    return name.strip(" \"'“”‘’,")


def money(amount_usd: Optional[float], currency: str = "$", amount: Optional[float] = None) -> str:
    """'$20M', '€3.5M', '$1.2B'."""
    v = amount if amount is not None else amount_usd
    if v is None:
        return ""
    cur = currency or "$"
    if v >= 1e9:
        s = f"{v / 1e9:.2f}".rstrip("0").rstrip(".") + "B"
    elif v >= 1e6:
        s = f"{v / 1e6:.1f}".rstrip("0").rstrip(".") + "M"
    else:
        s = f"{v / 1e3:.0f}K"
    return f"{cur}{s}" if len(cur) <= 2 else f"{cur} {s}"


# ---- the store ---------------------------------------------------------------------------------------------
class StartupStore:
    """data/startups.json: {id: startup}. A startup is a plain dict:
    id, name, key, website, domain, one_liner, stage, round {name, amount, currency, amount_usd, date,
    headline, url, source}, hq, industries, team_size, batch, hiring, sources [{kind, name, url, at}],
    first_seen, last_seen, dismissed, careers_url, ats {kind, board}, roles {checked, jobs: [...]}, pdl {...}."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._cache: tuple[Any, dict] = ((), {})

    META = "_meta"

    def _raw(self) -> dict:
        sig = self.sig()
        if self._cache[0] != sig:
            self._cache = (sig, _read(self.path, {}) if sig else {})
        return self._cache[1]

    def read(self) -> dict[str, dict]:
        return {k: v for k, v in self._raw().items() if k != self.META}

    def meta(self) -> dict:
        return self._raw().get(self.META) or {}

    def update_meta(self, **fields: Any) -> None:
        with self._lock:
            data = _read(self.path, {})
            data.setdefault(self.META, {}).update(fields)
            _write(self.path, data)

    def sig(self):
        try:
            st = self.path.stat()
        except FileNotFoundError:
            return None
        return st.st_mtime_ns, st.st_size

    def list(self) -> list[dict]:
        """Newest raise first; startups with no round (YC, VC boards) after them, most recently seen first."""
        return sorted(self.read().values(), key=lambda s: ((s.get("round") or {}).get("date") or "", s.get("last_seen") or ""), reverse=True)

    def get(self, sid: str) -> Optional[dict]:
        return self.read().get(sid)

    def by_company(self, name: str, domain: str = "") -> Optional[dict]:
        """The startup whose website or name matches this company, for the round chip on a job."""
        key, dom = company_key(name), _domain(domain)
        if not key and not dom:
            return None
        best = None
        for s in self.read().values():
            if dom and s.get("domain") and s["domain"] == dom:
                return s
            if key and s.get("key") == key:
                best = s
        return best

    def merge(self, found: Iterable[dict]) -> tuple[int, int]:
        """Add or update startups from a source. Returns (new, updated). A startup is matched by website,
        else by name; a newer round replaces an older one; sources accumulate."""
        new = updated = 0
        with self._lock:
            data = _read(self.path, {})
            meta = data.pop(self.META, None)
            by_dom = {s["domain"]: sid for sid, s in data.items() if s.get("domain")}
            by_key = {s["key"]: sid for sid, s in data.items() if s.get("key")}
            for f in found:
                f = _clean(f)
                if not f:
                    continue
                sid = (by_dom.get(f["domain"]) if f.get("domain") else None) or by_key.get(f["key"])
                if sid is None:
                    sid = _new_id(data, f)
                    data[sid] = {"id": sid, "first_seen": now_iso(), "sources": [], "stage": "Unknown"}
                    by_key[f["key"]] = sid
                    if f.get("domain"):
                        by_dom[f["domain"]] = sid
                    new += 1
                else:
                    updated += 1
                _absorb(data[sid], f)
            if meta is not None:
                data[self.META] = meta
            _write(self.path, data)
        return new, updated

    def update(self, sid: str, **fields: Any) -> dict:
        with self._lock:
            data = _read(self.path, {})
            if sid not in data or sid == self.META:
                raise KeyError(sid)
            data[sid].update(fields)
            _write(self.path, data)
            return data[sid]

    def index(self) -> dict[str, dict]:
        """domain -> startup and name key -> startup, for tagging jobs with their company's round."""
        sig = self.sig()
        if getattr(self, "_index_sig", ()) != sig:
            idx: dict[str, dict] = {}
            for s in self.read().values():
                if s.get("domain"):
                    idx.setdefault("d:" + s["domain"], s)
                if s.get("key"):
                    idx.setdefault("k:" + s["key"], s)
            self._index, self._index_sig = idx, sig
        return self._index

    def for_job(self, job: dict) -> Optional[dict]:
        """The startup a stored job's company is, by website then name; None if it isn't one we know."""
        idx = self.index()
        dom = _domain(job.get("company_domain") or "")
        return (idx.get("d:" + dom) if dom else None) or idx.get("k:" + company_key(job.get("company") or ""))


def _domain(url_or_domain: str) -> str:
    s = (url_or_domain or "").strip().lower()
    if not s:
        return ""
    if "://" not in s:
        s = "https://" + s
    host = urlsplit(s).netloc.split("@")[-1].split(":")[0]
    return host.removeprefix("www.")


def _clean(f: dict) -> Optional[dict]:
    name = (f.get("name") or "").strip()
    key = company_key(name)
    if not key:
        return None
    out = {k: v for k, v in f.items() if v not in (None, "", [], {})}
    out["name"], out["key"] = name, key
    if out.get("website"):
        out["domain"] = _domain(out["website"])
    return out


def _new_id(data: dict, f: dict) -> str:
    base = slug(f["name"], max_len=40) or "startup"
    sid, n = base, 2
    while sid in data:
        sid, n = f"{base}-{n}", n + 1
    return sid


def _absorb(s: dict, f: dict) -> None:
    """Fill what's missing, keep the newest round, and note the source."""
    for k, v in f.items():
        if k in ("round", "sources", "stage", "id", "key", "vc_roles"):
            continue
        if k in ("hiring",) or not s.get(k):
            s[k] = v
    if f.get("vc_roles"):
        roles = s.get("roles") or {"checked": "", "board": None, "careers_url": "", "jobs": []}
        if not roles.get("board"):       # the company's own board, once read, is the fuller list
            have = {j.get("url") for j in roles["jobs"]}
            roles["jobs"] = [*roles["jobs"], *[r for r in f["vc_roles"] if r["url"] not in have]]
            roles["checked"] = roles.get("checked") or now_iso()
            roles["from_vc_board"] = True
            s["roles"] = roles
    if f.get("key") and not s.get("key"):
        s["key"] = f["key"]
    rnd = f.get("round")
    if rnd:
        cur = s.get("round") or {}
        known = (rnd.get("stage") or "Unknown") != "Unknown"
        newer = (rnd.get("date") or "") > (cur.get("date") or "")
        # A newer round replaces the one held; the same day's from another outlet doesn't churn it, unless it
        # names the round and the held one doesn't.
        if not cur or (newer and (known or not cur.get("name"))) \
                or ((rnd.get("date") or "") == (cur.get("date") or "") and known and (cur.get("stage") or "Unknown") == "Unknown"):
            s["round"] = rnd
            s["stage"] = rnd.get("stage") or normalize_stage(rnd.get("name"))
        elif (cur.get("stage") or "Unknown") == "Unknown" and known:
            s["stage"] = rnd["stage"]
    elif f.get("stage") and s.get("stage", "Unknown") == "Unknown":
        s["stage"] = f["stage"]
    for src in f.get("sources") or []:
        have = s.setdefault("sources", [])
        if not any(x.get("kind") == src.get("kind") and x.get("url") == src.get("url") for x in have):
            have.append(src)
    s["last_seen"] = now_iso()


# ---- source: funding news feeds ------------------------------------------------------------------------------
# FinSMEs has the most regular round headlines ("Acme Raises $8M in Series A Funding"); AlleyWatch is New York's
# daily funding report. Feeds behind a bot filter answer a feed reader's User-Agent, which is tried second.
DEFAULT_FEEDS = {
    "FinSMEs": "https://www.finsmes.com/feed",
    "TechCrunch": "https://techcrunch.com/category/venture/feed/",
    "Crunchbase News": "https://news.crunchbase.com/feed/",
    "AlleyWatch": "https://www.alleywatch.com/category/funding/feed/",
}
FEED_READER_UA = "feedparser/6.0.11 +https://github.com/kurtmckee/feedparser/"
GOOGLE_NEWS = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
DEFAULT_NEWS_QUERIES = ['"raises" "Series A"', '"raises" "Series B"', '"raises" "Series C"', '"raises" "seed round"',
                        '"raises" "pre-seed"', '"raises" "Series D"']


def _text(el: Optional[ET.Element]) -> str:
    return html.unescape((el.text if el is not None and el.text else "").strip())


def parse_feed(xml_text: str) -> list[dict]:
    """RSS or Atom -> [{title, link, date (ISO), summary}]."""
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError:
        return []
    ns = {"atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/elements/1.1/"}
    out = []
    for item in root.iter("item"):
        out.append({"title": _text(item.find("title")), "link": _text(item.find("link")) or _text(item.find("guid")),
                    "date": _date(_text(item.find("pubDate")) or _text(item.find("dc:date", ns))),
                    "summary": html_to_text(_text(item.find("description")))[:400]})
    for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
        link = entry.find("atom:link", ns)
        out.append({"title": _text(entry.find("atom:title", ns)), "link": link.get("href", "") if link is not None else "",
                    "date": _date(_text(entry.find("atom:published", ns)) or _text(entry.find("atom:updated", ns))),
                    "summary": html_to_text(_text(entry.find("atom:summary", ns)))[:400]})
    return out


def _date(s: str) -> str:
    if not s:
        return ""
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).date().isoformat()


def funding_from_feed(name: str, items: list[dict], since: str) -> list[dict]:
    """Startups from a feed's items: each a parsed headline on or after `since` (ISO date)."""
    out = []
    for it in items:
        if it["date"] and it["date"] < since:
            continue
        title, source = it["title"], name
        if name == "Google News":        # "Acme raises $5M - TechCrunch": the publisher is the source
            m = re.match(r"^(.*\S)\s+-\s+([^-]{2,40})$", title)
            if m:
                title, source = m.group(1), m.group(2).strip()
        p = parse_headline(title)
        if not p:
            continue
        out.append({"name": p["company"], "stage": p["stage"],
                    "round": {"name": p["round"], "stage": p["stage"], "amount": p["amount"], "currency": p["currency"],
                              "amount_usd": p["amount_usd"], "date": it["date"], "headline": title, "url": it["link"],
                              "source": source},
                    "one_liner": it.get("summary", "")[:240] if name != "Google News" else "",
                    "sources": [{"kind": "news", "name": source, "url": it["link"], "at": it["date"]}]})
    return out


def fetch_funding_news(feeds: dict[str, str], queries: list[str], days: int,
                       client: Optional[httpx.Client] = None, log: Callable[[str], None] = lambda s: None) -> list[dict]:
    c = client or httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": UA})
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    found: list[dict] = []
    urls = list(feeds.items()) + [("Google News", GOOGLE_NEWS.format(q=quote(q))) for q in queries]
    for name, url in urls:
        try:
            r = c.get(url)
            if r.status_code in (403, 429, 406):
                r = c.get(url, headers={"User-Agent": FEED_READER_UA})
            items = parse_feed(r.text) if r.status_code == 200 else []
            if r.status_code != 200:
                log(f"{name}: HTTP {r.status_code}")
        except (httpx.HTTPError, ValueError) as e:
            log(f"{name}: couldn't read the feed ({e})")
            continue
        got = funding_from_feed(name, items, since)
        log(f"{name}: {len(items)} items, {len(got)} funding headlines since {since}")
        found += got
    return found


# ---- source: the Y Combinator directory ------------------------------------------------------------------------
# yc-oss/api mirrors YC's public company index to JSON files once a day (github.com/yc-oss/api). No key, unlike
# YC's own search index, whose key rotates.
YC_OSS = "https://yc-oss.github.io/api/companies/{which}.json"
BATCH_SHORT = {"Winter": "W", "Summer": "S", "Fall": "F", "Spring": "X"}


def batch_short(batch: str) -> str:
    """'Winter 2026' -> 'W26' (YC's own shorthand; Spring is X). 'W26' stays 'W26'."""
    m = re.match(r"(Winter|Summer|Fall|Spring)\s+(\d{4})", batch or "")
    return f"{BATCH_SHORT[m.group(1)]}{m.group(2)[2:]}" if m else (batch or "")


def yc_companies(*, hiring_only: bool = True, batches: Optional[list[str]] = None, industries: Optional[list[str]] = None,
                 regions: Optional[list[str]] = None, keywords: Optional[list[str]] = None, limit: int = 2000,
                 client: Optional[httpx.Client] = None, log: Callable[[str], None] = lambda s: None) -> list[dict]:
    """YC companies from the directory mirror. Each has its batch (W26 = winter 2026), whether it says it's hiring,
    team size, industries and website. The round isn't published there, so the stage stays Unknown until a news
    item or a lookup supplies it; YC's own Early/Growth label is kept as `yc_stage`."""
    c = client or httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": UA})
    try:
        r = c.get(YC_OSS.format(which="hiring" if hiring_only else "all"))
        rows = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as e:
        log(f"YC directory: couldn't read it ({e})")
        return []
    if not isinstance(rows, list):
        log(f"YC directory: HTTP {r.status_code}")
        return []
    want_b = {b.lower() for b in batches or []}
    want_i = {i.lower() for i in industries or []}
    want_r = {x.lower() for x in regions or []}
    kw = [k.lower() for k in keywords or [] if k]
    found = []
    for x in rows:
        if x.get("status") not in (None, "Active"):
            continue
        if hiring_only and not x.get("isHiring"):
            continue
        if want_b and (x.get("batch") or "").lower() not in want_b:
            continue
        if want_i and not want_i & {i.lower() for i in x.get("industries") or []}:
            continue
        if want_r and not want_r & {i.lower() for i in x.get("regions") or []}:
            continue
        if kw:
            hay = " ".join([x.get("name") or "", x.get("one_liner") or "", x.get("long_description") or "",
                            *(x.get("tags") or []), *(x.get("industries") or [])]).lower()
            if not any(k in hay for k in kw):
                continue
        found.append(yc_record(x))
        if len(found) >= limit:
            break
    log(f"YC directory: {len(rows)} companies" + (" that say they're hiring" if hiring_only else "") + f", {len(found)} kept")
    return found


def yc_record(x: dict) -> dict:
    url = x.get("url") or (f"https://www.ycombinator.com/companies/{x.get('slug')}" if x.get("slug") else "")
    return {"name": x.get("name") or "", "website": x.get("website") or "", "one_liner": x.get("one_liner") or "",
            "description": (x.get("long_description") or "")[:1500], "batch": x.get("batch") or "", "hiring": bool(x.get("isHiring")),
            "team_size": x.get("team_size"), "industries": list(x.get("industries") or []), "tags": list(x.get("tags") or [])[:8],
            "hq": x.get("all_locations") or "", "yc_stage": x.get("stage") or "", "yc_url": url,
            "jobs_url": f"https://www.workatastartup.com/companies/{x.get('slug')}" if x.get("slug") else "",
            "sources": [{"kind": "yc", "name": f"YC {batch_short(x.get('batch') or '')}".strip(), "url": url, "at": ""}]}


# ---- source: VC portfolio job boards (Getro) -----------------------------------------------------------------------
# Many VC firms' job boards (jobs.<firm>.com) run on Getro, whose public search answers without a key and names
# each company's stage. A board is known by its network id, read from the board's page.
DEFAULT_VC_BOARDS = {"General Catalyst": "https://jobs.generalcatalyst.com", "Techstars": "https://jobs.techstars.com",
                     "Accel": "https://jobs.accel.com", "Insight Partners": "https://jobs.insightpartners.com",
                     "Khosla Ventures": "https://jobs.khoslaventures.com"}
GETRO_STAGES = {"pre_seed": "Pre-seed", "seed": "Seed", "series_a": "Series A", "series_b": "Series B", "series_c": "Series C",
                "ipo": "Growth", "acquisition": "Growth", "series_unknown": "Unknown", "undisclosed": "Unknown", "other": "Unknown"}


def getro_stage(stage: Optional[str]) -> str:
    st = (stage or "").lower()
    if st in GETRO_STAGES:
        return GETRO_STAGES[st]
    if re.fullmatch(r"series_[d-z]", st):
        return "Series D+"
    return "Unknown"


def getro_network_id(board_url: str, client: httpx.Client) -> Optional[int]:
    """The Getro network id behind a VC job board, from its page; None if the board isn't Getro's."""
    try:
        r = client.get(board_url.rstrip("/") + "/jobs")
    except httpx.HTTPError:
        return None
    if r.status_code != 200 or "getro" not in r.text.lower():
        return None
    m = re.search(r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', r.text, re.S)
    try:
        nid = json.loads(m.group(1))["props"]["pageProps"]["network"]["id"] if m else None
    except (ValueError, KeyError, TypeError):
        nid = None
    if nid is None:
        m = re.search(r"collections/(\d+)/search", r.text)
        nid = int(m.group(1)) if m else None
    return int(nid) if nid else None


def getro_jobs(network_id: int, query: str, client: httpx.Client, pages: int = 3) -> list[dict]:
    """Jobs on a Getro board matching a search term (20 a page)."""
    out = []
    for page in range(pages):
        try:
            r = client.post(f"https://api.getro.com/api/v2/collections/{network_id}/search/jobs",
                            json={"hitsPerPage": 20, "page": page, "query": query},
                            headers={"Accept": "application/json", "Content-Type": "application/json"})
            jobs = (r.json().get("results") or {}).get("jobs") if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            jobs = None
        if not jobs:
            break
        out += jobs
        if len(jobs) < 20:
            break
    return out


def vc_board_startups(boards: dict[str, str], phrases: list[str], client: Optional[httpx.Client] = None,
                      log: Callable[[str], None] = lambda s: None, ids: Optional[dict[str, int]] = None) -> list[dict]:
    """Startups hiring for the filters' titles on VC portfolio boards, each with its stage and the matching roles.
    `ids` caches board URL -> network id across refreshes."""
    c = client or httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": UA})
    ids = ids if ids is not None else {}
    found: dict[str, dict] = {}
    for firm, url in boards.items():
        nid = ids.get(url) or getro_network_id(url, c)
        if not nid:
            log(f"{firm}: not a Getro board (or its page didn't load), skipped")
            continue
        ids[url] = nid
        n_jobs = 0
        for phrase in phrases or ["engineer"]:
            for j in getro_jobs(nid, phrase, c):
                if not title_matches(j.get("title") or "", phrases or [phrase]):
                    continue
                o = j.get("organization") or {}
                if not o.get("name"):
                    continue
                n_jobs += 1
                key = company_key(o["name"])
                s = found.setdefault(key, {
                    "name": o["name"], "stage": getro_stage(o.get("stage")),
                    "industries": list(o.get("industry_tags") or [])[:6], "getro_stage": o.get("stage") or "",
                    "exited": {"acquisition": "acquired", "ipo": "public"}.get((o.get("stage") or "").lower(), ""),
                    "sources": [], "vc_roles": []})
                if not any(x["url"] == url for x in s["sources"]):
                    s["sources"].append({"kind": "vc", "name": f"{firm} portfolio", "url": url, "at": ""})
                role_url = j.get("url") or ""
                if role_url and not any(r["url"] == role_url for r in s["vc_roles"]):
                    loc = "; ".join(j.get("locations") or [])
                    s["vc_roles"].append({"title": j.get("title") or "", "url": role_url, "location": loc,
                                          "remote": (j.get("work_mode") or "") == "remote" or "remote" in loc.lower(),
                                          "posted": datetime.fromtimestamp(j["created_at"], tz=timezone.utc).date().isoformat()
                                          if j.get("created_at") else "", "id": str(j.get("id") or ""), "match": True,
                                          "description": "", "board": firm})
        log(f"{firm}: {n_jobs} matching role(s) across the portfolio")
    return list(found.values())


# ---- enrichment: People Data Labs (optional) ----------------------------------------------------------------------
PDL_STAGES = {"pre_seed": "Pre-seed", "seed": "Seed", "series_a": "Series A", "series_b": "Series B", "series_c": "Series C",
              "angel": "Pre-seed", "grant": "Unknown", "private_equity": "Growth", "secondary_market": "Growth",
              "debt_financing": "Unknown", "convertible_note": "Unknown", "equity_crowdfunding": "Seed", "ipo": "Growth"}


def pdl_enrich(domain: str, api_key: str, client: Optional[httpx.Client] = None) -> Optional[dict]:
    """The latest round for a company website, from People Data Labs' company enrichment (1 credit)."""
    if not domain or not api_key:
        return None
    c = client or httpx.Client(timeout=20, headers={"User-Agent": UA})
    r = c.get("https://api.peopledatalabs.com/v5/company/enrich", params={"website": domain, "min_likelihood": 5},
              headers={"X-Api-Key": api_key})
    if r.status_code != 200:
        return None
    d = r.json()
    st = (d.get("latest_funding_stage") or "").lower()
    stage = PDL_STAGES.get(st) or normalize_stage(st.replace("_", " "))
    if st.startswith("series_") and st[7:].upper() not in "ABC":
        stage = "Series D+"
    out = {"stage": stage, "team_size": d.get("employee_count"), "industries": [d["industry"]] if d.get("industry") else [],
           "one_liner": (d.get("summary") or "")[:240], "pdl": {"latest_funding_stage": st, "last_funding_date": d.get("last_funding_date"),
                                                                "total_funding_raised": d.get("total_funding_raised"),
                                                                "number_funding_rounds": d.get("number_funding_rounds")}}
    if st:
        out["round"] = {"name": round_label(st.replace("_", " ")), "stage": stage, "amount": None, "currency": "",
                        "amount_usd": None, "date": d.get("last_funding_date") or "", "headline": "", "url": "",
                        "source": "People Data Labs"}
    loc = d.get("location") or {}
    if loc.get("locality"):
        out["hq"] = ", ".join(x for x in (loc.get("locality"), loc.get("region")) if x)
    return out


def fundable_enrich(domain: str, api_key: str, client: Optional[httpx.Client] = None) -> Optional[dict]:
    """The latest deal for a company website, from Fundable (tryfundable.ai; 1 credit a lookup)."""
    if not domain or not api_key:
        return None
    c = client or httpx.Client(timeout=20, headers={"User-Agent": UA})
    r = c.get("https://www.tryfundable.ai/api/v1/company", params={"domain": domain},
              headers={"Authorization": f"Bearer {api_key}"})
    if r.status_code != 200:
        return None
    d = r.json()
    d = d.get("data") if isinstance(d.get("data"), dict) else d
    deal = d.get("latest_deal") or {}
    kind = (deal.get("type") or "").lower().replace("_", " ")
    stage = normalize_stage(kind) if kind else "Unknown"
    if kind.startswith("series ") and kind[7:8].upper() not in "ABC":
        stage = "Series D+"
    out: dict[str, Any] = {"stage": stage, "team_size": d.get("employee_count") or d.get("headcount"),
                           "fundable": {"latest_deal": deal, "total_raised": d.get("total_raised"),
                                        "num_funding_rounds": d.get("num_funding_rounds")}}
    if d.get("description"):
        out["one_liner"] = str(d["description"])[:240]
    if kind:
        amt = deal.get("total_round_raised")
        out["round"] = {"name": round_label(kind), "stage": stage, "amount": amt, "currency": "$" if amt else "",
                        "amount_usd": amt, "date": (deal.get("date") or "")[:10], "headline": "", "url": "", "source": "Fundable"}
    return out


def theirstack_enrich(domain: str, api_key: str, client: Optional[httpx.Client] = None) -> Optional[dict]:
    """The funding stage of a company website, from TheirStack's company search (3 credits a lookup)."""
    if not domain or not api_key:
        return None
    c = client or httpx.Client(timeout=30, headers={"User-Agent": UA})
    r = c.post("https://api.theirstack.com/v1/companies/search", json={"company_domain_or": [domain], "limit": 1},
               headers={"Authorization": f"Bearer {api_key}"})
    if r.status_code != 200:
        return None
    rows = r.json().get("data") or []
    if not rows:
        return None
    d = rows[0]
    st = (d.get("funding_stage") or "").lower().replace("_", " ")
    stage = normalize_stage(st) if st else "Unknown"
    if st.startswith("series ") and st[7:8].upper() not in "ABC":
        stage = "Series D+"
    out: dict[str, Any] = {"stage": stage, "team_size": d.get("employee_count"), "industries": [d["industry"]] if d.get("industry") else [],
                           "theirstack": {"funding_stage": d.get("funding_stage"), "total_funding_usd": d.get("total_funding_usd"),
                                          "last_funding_round_date": d.get("last_funding_round_date"), "founded_year": d.get("founded_year")}}
    if d.get("yc_batch"):
        out["batch"] = d["yc_batch"]
    if st:
        out["round"] = {"name": round_label(st), "stage": stage, "amount": None, "currency": "", "amount_usd": None,
                        "date": (d.get("last_funding_round_date") or "")[:10], "headline": "", "url": "", "source": "TheirStack"}
    return out


def enrich(s: dict, keys: dict[str, str], client: Optional[httpx.Client] = None) -> tuple[Optional[dict], str]:
    """Look the startup up with whichever lookup has a key (Fundable, then TheirStack, then People Data Labs).
    Returns (fields to merge, which source answered or why none did)."""
    domain = s.get("domain") or _domain(s.get("website") or "")
    if not domain:
        return None, "no website to look up"
    tried = []
    for name, key, fn in (("Fundable", keys.get("fundable"), fundable_enrich), ("TheirStack", keys.get("theirstack"), theirstack_enrich),
                          ("People Data Labs", keys.get("pdl"), pdl_enrich)):
        if not key:
            continue
        tried.append(name)
        try:
            got = fn(domain, key, client)
        except (httpx.HTTPError, ValueError):
            got = None
        if got:
            return {**got, "sources": [{"kind": "lookup", "name": name, "url": "", "at": now_iso()[:10]}]}, name
    if not tried:
        return None, "no lookup key (set FUNDABLE_API_KEY, THEIRSTACK_API_KEY or PDL_API_KEY in .env)"
    return None, f"{' and '.join(tried)} had nothing for {domain}"


# ---- open roles from the company's careers board ---------------------------------------------------------------------
ATS_RE = {
    "greenhouse": re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)|greenhouse\.io/embed/job_board\?for=([A-Za-z0-9_-]+)"),
    "lever": re.compile(r"jobs\.lever\.co/([A-Za-z0-9_-]+)"),
    "lever-eu": re.compile(r"jobs\.eu\.lever\.co/([A-Za-z0-9_-]+)"),      # Lever's EU host: many European companies
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)|api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.-]+)"),
}
CAREERS_PATHS = ("/careers", "/jobs", "/careers/", "/jobs/", "/join", "/join-us", "/company/careers", "/about/careers", "/en/careers")


@dataclass
class Board:
    kind: str
    board: str

    @property
    def url(self) -> str:
        return {"greenhouse": f"https://boards.greenhouse.io/{self.board}", "lever": f"https://jobs.lever.co/{self.board}",
                "lever-eu": f"https://jobs.eu.lever.co/{self.board}",
                "ashby": f"https://jobs.ashbyhq.com/{self.board}"}[self.kind]


def board_jobs(b: Board, client: httpx.Client) -> Optional[list[dict]]:
    """The open roles on a Greenhouse, Lever or Ashby board, or None if that board doesn't exist."""
    try:
        if b.kind == "greenhouse":
            r = client.get(f"https://boards-api.greenhouse.io/v1/boards/{b.board}/jobs", params={"content": "true"})
            if r.status_code != 200:
                return None
            return [{"title": j.get("title") or "", "url": j.get("absolute_url") or "", "location": (j.get("location") or {}).get("name") or "",
                     "remote": "remote" in ((j.get("location") or {}).get("name") or "").lower(),
                     "description": html_to_text(html.unescape(j.get("content") or "")), "posted": (j.get("updated_at") or "")[:10],
                     "id": str(j.get("id") or "")} for j in r.json().get("jobs") or []]
        if b.kind in ("lever", "lever-eu"):
            host = "api.eu.lever.co" if b.kind == "lever-eu" else "api.lever.co"
            r = client.get(f"https://{host}/v0/postings/{b.board}", params={"mode": "json"})
            if r.status_code != 200 or not isinstance(r.json(), list):
                return None
            out = []
            for j in r.json():
                cat = j.get("categories") or {}
                loc = cat.get("location") or ", ".join(cat.get("allLocations") or [])
                out.append({"title": j.get("text") or "", "url": j.get("hostedUrl") or j.get("applyUrl") or "", "location": loc,
                            "remote": (j.get("workplaceType") or "").lower() == "remote" or "remote" in loc.lower(),
                            "description": j.get("descriptionPlain") or html_to_text(j.get("description") or ""),
                            "posted": datetime.fromtimestamp((j.get("createdAt") or 0) / 1000, tz=timezone.utc).date().isoformat()
                            if j.get("createdAt") else "", "id": str(j.get("id") or "")})
            return out
        if b.kind == "ashby":
            r = client.get(f"https://api.ashbyhq.com/posting-api/job-board/{b.board}", params={"includeCompensation": "true"})
            if r.status_code != 200:
                return None
            out = []
            for j in r.json().get("jobs") or []:
                locs = [j.get("location") or ""] + [x.get("location") or "" for x in j.get("secondaryLocations") or []]
                comp = j.get("compensation") or {}
                role = {"title": j.get("title") or "", "url": j.get("jobUrl") or j.get("applyUrl") or "",
                        "location": "; ".join(x for x in locs if x), "remote": bool(j.get("isRemote")),
                        "description": j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml") or ""),
                        "posted": (j.get("publishedAt") or "")[:10], "id": str(j.get("id") or ""),
                        "pay": comp.get("compensationTierSummary") or ""}
                lo, hi = ashby_usd(comp)
                if lo or hi:
                    role["pay_usd"] = [lo, hi]
                out.append(role)
            return out
    except (httpx.HTTPError, ValueError):
        return None
    return None


def ashby_usd(comp: dict) -> tuple[Optional[float], Optional[float]]:
    """(low, high) across the yearly salary components in USD of an Ashby posting's compensation tiers (one tier
    per place, each with its salary, equity and bonus components), or (None, None) when no tier states one."""
    los, his = [], []
    for tier in comp.get("compensationTiers") or []:
        for c in tier.get("components") or []:
            if (c.get("compensationType") == "Salary" and str(c.get("currencyCode") or "").upper() == "USD"
                    and "YEAR" in str(c.get("interval") or "").upper()):
                if c.get("minValue") is not None:
                    los.append(c["minValue"])
                if c.get("maxValue") is not None:
                    his.append(c["maxValue"])
    return (min(los) if los else None, max(his) if his else None)


def boards_in(text: str) -> list[Board]:
    out = []
    for kind, rx in ATS_RE.items():
        for m in rx.finditer(text or ""):
            board = next((g for g in m.groups() if g), "")
            if board and not any(b.kind == kind and b.board == board for b in out):
                out.append(Board(kind, board))
    return out


def find_board(website: str, name: str, client: httpx.Client, log: Callable[[str], None] = lambda s: None) -> tuple[Optional[Board], str]:
    """(the company's careers board, the careers page found). Looks for a Greenhouse, Lever or Ashby link on the
    site's home page and careers pages, then tries the obvious board names."""
    site = website if "://" in (website or "") else f"https://{website}" if website else ""
    careers = ""
    pages = [site] + [urljoin(site, p) for p in CAREERS_PATHS] if site else []
    seen = set()
    for url in pages:
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            r = client.get(url)
        except httpx.HTTPError:
            continue
        if r.status_code >= 400 or "html" not in r.headers.get("content-type", ""):
            continue
        raw = r.text
        found = boards_in(raw)
        if found:
            return found[0], url if url != site else ""
        if url == site:   # follow the home page's own careers link, if it points elsewhere
            m = re.search(r'href=["\']([^"\']*(?:career|jobs|join-us|work-with-us|openings)[^"\']*)["\']', raw, re.I)
            link = urljoin(site, html.unescape(m.group(1))) if m else ""
            if urlsplit(link).scheme in ("http", "https"):   # not a "mailto:jobs@..." link
                if link not in seen and _domain(link) and _domain(link) != _domain(site):
                    pages.append(link)
                    for kind, rx in ATS_RE.items():
                        if rx.search(link):
                            return boards_in(link)[0], link
                elif link not in seen:
                    pages.insert(1, link)
                careers = careers or link
    guesses = []
    for g in (_domain(site).split(".")[0] if site else "", slug(name, max_len=40), slug(name, max_len=40).replace("-", ""),
              re.sub(r"[^a-z0-9]", "", name.lower().replace(" ai", "").replace(".ai", ""))):
        if g and len(g) >= 4 and g not in guesses:     # "flow" or "step" would hit some other company's board
            guesses.append(g)
    for g in guesses:
        for kind in ("greenhouse", "lever", "lever-eu", "ashby"):
            b = Board(kind, g)
            jobs = board_jobs(b, client)
            if jobs is None or (not jobs and kind.startswith("lever")) or len(jobs) > GUESSED_BOARD_MAX_JOBS:
                continue
            if kind == "greenhouse" and not _greenhouse_board_is(b, name, client):
                continue
            return b, careers
    return None, careers


GUESSED_BOARD_MAX_JOBS = 150


def _greenhouse_board_is(b: Board, name: str, client: httpx.Client) -> bool:
    """Is this Greenhouse board the company's (its name matches), not another company's with the same board name?"""
    try:
        r = client.get(f"https://boards-api.greenhouse.io/v1/boards/{b.board}")
        board_name = (r.json().get("name") or "") if r.status_code == 200 else ""
    except (httpx.HTTPError, ValueError):
        return False
    a, c = company_key(board_name), company_key(name)
    # "Acme" is "Acme Labs" but not "Big Acme Holdings"
    return bool(a and c) and (a == c or a.startswith(c + " ") or c.startswith(a + " "))


def title_matches(title: str, phrases: list[str]) -> bool:
    t = title.lower()
    return any(p.lower() in t for p in phrases if p)


# Words that make a matching title a sales, pre-sales, support or partner role rather than an engineering one:
# "Solutions Engineering Manager", "Sales Program Manager", "Technical Program Manager, Field".
QUALIFIERS = (r"sales|pre-?sales|solutions?|support|customer success|customers?|field|partners?|partnerships?|accounts?|"
              r"marketing|revenue|gtm|go-to-market|business development|sdr|bdr|success|enablement|implementation(?:s)?|"
              r"professional services|services|onboarding|renewals?|channel|alliances?|growth marketing|demand gen(?:eration)?")
_QUAL_BEFORE = re.compile(rf"(?:^|\b)(?:{QUALIFIERS})\s*$", re.I)
_QUAL_CLAUSE = re.compile(rf"^(?:(?:{QUALIFIERS}|and|&|of|the|team)\s*)+$", re.I)
# Levels below the searches' (an "Associate TPM" isn't a Lead or Principal one).
_JUNIOR = re.compile(r"\b(?:associate|junior|jr\.?|entry[- ]level|apprentice|intern|internship|trainee|graduate)\b", re.I)
_CLAUSE_SPLIT = re.compile(r"\s*(?:,|;|:|\||/|–|—|\s-\s|\(|\))\s*")


def off_track(title: str, phrase: str) -> bool:
    """Is a title that contains `phrase` a sales, pre-sales, support or partner role, or a junior one, rather than the
    engineering role the phrase names? "Mid-Market Solutions Engineering Manager", "Sales Program Manager" and
    "Technical Program Manager, Field" are; "Engineering Manager, Customer Studios", "Senior Engineering Manager,
    Clinical" and "Manager, Engineering - Sales Planning" are not."""
    t, ph = title or "", (phrase or "").lower()
    if _JUNIOR.search(t):
        return True
    i = t.lower().find(ph)
    if i < 0:
        return False
    clauses = [c for c in _CLAUSE_SPLIT.split(t) if c.strip()]
    for c in clauses:
        j = c.lower().find(ph)
        if j >= 0:
            if _QUAL_BEFORE.search(c[:j].strip()):          # "Solutions" right before "Engineering Manager"
                return True
        elif _QUAL_CLAUSE.match(c.strip()):                   # a clause that is only "Field" or "Customer Success"
            return True
    return False


def matching_phrase(title: str, phrases: list[str]) -> str:
    t = (title or "").lower()
    return next((p for p in phrases if p and p.lower() in t), "")


def role_fits(role: dict, search: Any, exclude: Optional[list[str]] = None) -> bool:
    """Does this role answer a saved search: its title has one of the search's title phrases (and none of the
    excluded ones, nor an off-track qualifier: see off_track), and it's somewhere the search allows (remote roles
    fit any search; a search for a place needs that place in the role's location; a role with no location is given
    the benefit of the doubt). `exclude` are extra title words from startups.roles.exclude_titles."""
    title = role.get("title") or ""
    phrase = matching_phrase(title, list(search.titles))
    if not phrase or title_matches(title, list(search.exclude_titles)) or title_matches(title, list(exclude or [])):
        return False
    if off_track(title, phrase):
        return False
    loc = (role.get("location") or "").lower()
    if elsewhere(f"{title} {loc}", getattr(search, "country", None)):
        return False
    remote = bool(role.get("remote")) or "remote" in loc
    if search.remote:
        return remote or not loc
    if search.locations:
        return remote or not loc or any(p.lower() in loc for p in search.locations)
    return True


def elsewhere(text: str, country) -> bool:
    """Does this location or title pin the role somewhere outside where the search looks (its country or region)?
    A US search skips "Engineering Manager - UK" and "Remote (EU)"; an EU one skips "Austin, TX" (see regions.py)."""
    return regions.elsewhere(text, country)


def role_searches(role: dict, searches: list, exclude: Optional[list[str]] = None) -> list[str]:
    """The ids of the saved searches this role answers."""
    return [s.id for s in searches if role_fits(role, s, exclude)]


def open_roles(s: dict, phrases: list[str], client: Optional[httpx.Client] = None,
               log: Callable[[str], None] = lambda s: None, searches: Optional[list] = None,
               exclude: Optional[list[str]] = None) -> dict:
    """The startup's open roles from its careers board: {board, careers_url, jobs: [...], matching: [i...]}.
    Each job: title, url, location, remote, description, posted, match. A role matches when it answers one of
    `searches` (title and place), or, without them, when its title has one of `phrases`."""
    c = client or httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": UA})
    ats = s.get("ats") or {}
    b = Board(ats["kind"], ats["board"]) if ats.get("kind") and ats.get("board") else None
    careers = s.get("careers_url") or ""
    jobs = board_jobs(b, c) if b else None
    if jobs is None:
        b, careers = find_board(s.get("website") or "", s.get("name") or "", c, log)
        jobs = board_jobs(b, c) if b else None
    out = {"board": {"kind": b.kind, "board": b.board, "url": b.url} if b else None, "careers_url": careers or (s.get("jobs_url") or ""),
           "checked": now_iso(), "jobs": []}
    fits = (lambda j: bool(role_searches(j, searches, exclude))) if searches is not None else (lambda j: title_matches(j.get("title", ""), phrases))
    for j in jobs or []:
        out["jobs"].append({**j, "description": (j.get("description") or "")[:20000], "match": fits(j)})
    if jobs is None:      # no board of its own found: keep what a VC portfolio board listed
        out["jobs"] = [{**j, "match": fits(j)} for j in (s.get("roles") or {}).get("jobs") or [] if j.get("board")]
        out["from_vc_board"] = bool(out["jobs"])
    out["jobs"].sort(key=lambda j: (not j["match"], j.get("title", "").lower()))
    return out


# ---- the automatic scan: every startup's board, matching roles into Find jobs -----------------------------------------
RECHECK_HOURS = 20          # a board read this recently isn't read again on the next refresh
NO_BOARD_RETRY_DAYS = 14    # a website where no board was found is looked at again after this long


def scan_candidates(store: StartupStore, limit: int, now: Optional[datetime] = None) -> list[dict]:
    """Which startups to read this time: not dismissed, with a website, not read in the last RECHECK_HOURS, and not one
    whose site had no board in the last NO_BOARD_RETRY_DAYS. Newest raise first, then the rest as stored."""
    now = now or datetime.now(timezone.utc)
    recheck = (now - timedelta(hours=RECHECK_HOURS)).isoformat(timespec="seconds")
    retry = (now - timedelta(days=NO_BOARD_RETRY_DAYS)).isoformat(timespec="seconds")
    out = []
    for s in store.list():
        if s.get("dismissed") or not (s.get("website") or s.get("ats")):
            continue
        roles = s.get("roles") or {}
        if roles.get("board") and (roles.get("checked") or "") >= recheck:
            continue
        if (s.get("no_board_at") or "") >= retry:
            continue
        out.append(s)
    out.sort(key=lambda s: (not (s.get("round") or {}).get("date"), not s.get("hiring")))   # stable: newest raise first within each
    return out[:limit]


def known_posting_keys(jobs_store: Any) -> set[str]:
    """The posting links of every stored job, so a role isn't added twice."""
    from .jd import posting_key, posting_url
    known = set()
    try:
        for f in jobs_store.jobs_dir.glob("*.json"):
            try:
                known.add(posting_key(posting_url(json.loads(f.read_text()))))
            except (OSError, ValueError):
                pass
    except OSError:
        pass
    known.discard("")
    return known


def add_matching_roles(store: StartupStore, s: dict, roles: list[dict], searches: list, jobs_store: Any, known: set[str],
                       exclude: Optional[list[str]] = None) -> list[str]:
    """Store every role in `roles` that answers a saved search as a job (if its link isn't stored yet). Returns the
    titles added. The roles' `match` flags are refreshed by title and place. A company that was acquired or went
    public (a VC board still lists it) isn't a startup: its roles are matched and shown, but not stored on their own."""
    from .jd import posting_key
    added = []
    for j in roles:
        j["match"] = bool(role_searches(j, searches, exclude))
        if not j["match"] or s.get("exited"):
            continue
        key = posting_key(j.get("url") or "")
        if not key or key in known:
            continue
        jobs_store.save_job(role_as_job(s, j), "startups")
        known.add(key)
        added.append(j.get("title") or "")
    return added


def store_known_roles(store: StartupStore, searches: list, jobs_store: Any, *, log: Callable[[str], None] = lambda s: None,
                      exclude: Optional[list[str]] = None) -> int:
    """Roles the store already holds (listed by a VC board, or read from a company's board earlier) that answer a
    saved search go to Find jobs now, with no board read. Returns how many jobs were added."""
    known = known_posting_keys(jobs_store)
    total = 0
    for s in store.list():
        roles = s.get("roles") or {}
        if s.get("dismissed") or not roles.get("jobs"):
            continue
        before = [j.get("match") for j in roles["jobs"]]
        added = add_matching_roles(store, s, roles["jobs"], searches, jobs_store, known, exclude)
        if added or before != [j.get("match") for j in roles["jobs"]]:
            try:
                store.update(s["id"], roles=roles)
            except KeyError:
                pass
        if added:
            total += len(added)
            log(f"  {s['name']} ({funding_line(funding_block(s)) or 'round unknown'}): added {', '.join(added)}")
    log(f"Open roles already known: {total} new in Search Jobs")
    return total


def scan_boards(store: StartupStore, searches: list, jobs_store: Any, *, limit: int = 300, workers: int = 6,
                client: Optional[httpx.Client] = None, log: Callable[[str], None] = lambda s: None,
                exclude: Optional[list[str]] = None) -> dict:
    """Read the careers board of up to `limit` startups and store every role that answers a saved search as a job
    (it lands in Find jobs with the round chip), so open roles turn up without a search per startup.
    Returns counts: startups read, boards found, roles seen, roles matching, jobs added."""
    from concurrent.futures import ThreadPoolExecutor
    todo = scan_candidates(store, limit)
    counts = {"startups": len(todo), "boards": 0, "roles": 0, "matching": 0, "added": 0}
    if not todo:
        log("Open roles: every startup's board was read recently; nothing to do")
        return counts
    phrases = []
    for sc in searches:
        phrases += [t for t in sc.titles if t and t not in phrases]
    known = known_posting_keys(jobs_store)
    log(f"Open roles: reading {len(todo)} startups' careers boards for {', '.join(phrases) or 'any title'}")

    def one(s: dict) -> tuple[dict, dict]:
        c = client or httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": UA})
        try:
            return s, open_roles(s, phrases, c, searches=searches, exclude=exclude)
        except Exception as e:  # noqa: BLE001 - one site's trouble shouldn't stop the scan
            return s, {"error": str(e)}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for s, roles in pool.map(one, todo):
            if roles.get("error"):
                log(f"  {s['name']}: {roles['error']}")
                continue
            fields: dict[str, Any] = {"roles": roles, "ats": roles["board"], "careers_url": roles["careers_url"] or s.get("careers_url", "")}
            if roles["board"]:
                counts["boards"] += 1
                fields["no_board_at"] = None
            else:
                fields["no_board_at"] = now_iso()
            try:
                store.update(s["id"], **fields)
            except KeyError:
                continue
            counts["roles"] += len(roles["jobs"])
            counts["matching"] += sum(1 for j in roles["jobs"] if j.get("match"))
            added = add_matching_roles(store, s, roles["jobs"], searches, jobs_store, known, exclude)
            counts["added"] += len(added)
            if added:
                log(f"  {s['name']} ({funding_line(funding_block(s)) or 'round unknown'}): added {', '.join(added)}")
    log(f"Open roles: {counts['boards']} boards read of {counts['startups']} startups, {counts['roles']} roles, "
        f"{counts['matching']} match your filters, {counts['added']} new in Search Jobs")
    return counts


def role_as_job(s: dict, role: dict) -> dict:
    """A role from a startup's board as a job to store (the shape JobsPipe jobs have), with the funding block."""
    jid = "startup-" + slug(s["name"], max_len=30) + "-" + (slug(role.get("title", ""), max_len=48) or "role") \
          + ("-" + re.sub(r"[^a-z0-9]", "", role["id"].lower())[-8:] if role.get("id") else "")
    job = {"id": jid, "job_title": role.get("title") or "Role", "company": s["name"], "url": role.get("url") or "",
           "location": role.get("location") or "", "description": role.get("description") or "", "date_posted": role.get("posted") or "",
           "company_domain": s.get("domain") or "", "funding": funding_block(s), "description_read": True}
    if role.get("remote"):
        job["remote"] = True
    if role.get("pay"):
        job["salary_string"] = role["pay"]
    lo, hi = role.get("pay_usd") or (None, None)     # Ashby's figures; a dollar range in the text is read when shown
    if lo:
        job["min_annual_salary_usd"] = lo
    if hi:
        job["max_annual_salary_usd"] = hi
    return job


def stage_round(s: dict) -> tuple[str, str]:
    """(round, where it was seen) for a startup whose stage came without a round, from a VC board: 'Series H' for
    the board's 'series_h' (the stage alone says only 'Series D+'), and the board. ('', '') if the stage is unknown."""
    stage = s.get("stage") or "Unknown"
    if stage == "Unknown":
        return "", ""
    g = (s.get("getro_stage") or "").lower()
    name = round_label(g.replace("_", " ")) if re.fullmatch(r"series_[a-z]|seed|pre_seed", g) else stage
    board = next((x.get("name") or "" for x in s.get("sources") or [] if x.get("kind") == "vc"), "")
    return name, board


def funding_block(s: dict) -> dict:
    """What a job carries about its company's funding: shown as the round chip and written to the tracker."""
    r = s.get("round") or {}
    name, source = (r.get("name") or "", r.get("source") or "") if r else stage_round(s)
    return {"startup_id": s.get("id"), "stage": s.get("stage") or "Unknown", "round": name,
            "amount": money(r.get("amount_usd"), r.get("currency") or "$", r.get("amount")), "date": r.get("date") or "",
            "headline": r.get("headline") or "", "url": r.get("url") or "", "source": source,
            "batch": batch_short(s.get("batch") or ""), "yc_stage": s.get("yc_stage") or "", "team_size": s.get("team_size")}


def funding_line(f: Optional[dict]) -> str:
    """'Series B · $40M · Mar 2026 (FinSMEs)' for notes and the Notion row; '' if nothing is known."""
    if not f:
        return ""
    bits = []
    if f.get("stage") and f["stage"] != "Unknown":
        bits.append(f.get("round") or f["stage"])
    elif f.get("round"):
        bits.append(f["round"])
    if f.get("amount"):
        bits.append(f["amount"])
    if f.get("date"):
        try:
            bits.append(datetime.fromisoformat(f["date"]).strftime("%b %Y"))
        except ValueError:
            bits.append(f["date"])
    if f.get("batch"):
        bits.append(f"YC {batch_short(f['batch'])}")
    line = " · ".join(bits)
    return f"{line} ({f['source']})" if line and f.get("source") else line


# ---- refresh: every source into the store ---------------------------------------------------------------------------
def since_date(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()


def refresh(cfg: Any, store: StartupStore, keys: Optional[dict[str, str]] = None, *, log: Callable[[str], None] = print,
            days: Optional[int] = None, client: Optional[httpx.Client] = None, phrases: Optional[list[str]] = None,
            searches: Optional[list] = None, jobs_store: Any = None, tracked: Optional[dict[str, str]] = None) -> tuple[int, int]:
    """Read the funding feeds, the YC directory and the VC portfolio boards into the store. `cfg` is the StartupsCfg
    from searches.yaml; `phrases` are the filters' title phrases (what counts as a matching role on a VC board).
    Returns (new startups, startups seen again). With a lookup key and enrich_per_refresh > 0, also looks up
    the round of that many startups whose round is unknown, newest first."""
    days = days or cfg.days
    feeds = cfg.feeds or DEFAULT_FEEDS
    queries = cfg.news_queries if cfg.news_queries is not None else DEFAULT_NEWS_QUERIES
    log(f"Funding news: {len(feeds)} feed(s) and {len(queries)} Google News search(es), raises since {since_date(days)}")
    found = fetch_funding_news(feeds, queries, days, client=client, log=log)
    if cfg.keywords:
        kw = [k.lower() for k in cfg.keywords]
        before = len(found)
        found = [f for f in found if any(k in (f.get("name", "") + " " + f.get("one_liner", "") + " "
                                                + (f.get("round") or {}).get("headline", "")).lower() for k in kw)]
        log(f"Keywords {cfg.keywords}: kept {len(found)} of {before} funding headlines")
    found += yc_companies(hiring_only=cfg.yc_hiring_only, batches=cfg.yc_batches, industries=cfg.yc_industries,
                          regions=cfg.yc_regions, keywords=cfg.keywords, client=client, log=log)
    boards = cfg.vc_boards if cfg.vc_boards is not None else DEFAULT_VC_BOARDS
    if boards and phrases:
        log(f"VC portfolio boards: {len(boards)}, roles matching {phrases}")
        ids = store.meta().get("getro_ids") or {}
        found += vc_board_startups(boards, phrases, client=client, log=log, ids=ids)
        store.update_meta(getro_ids=ids)
    new, upd = store.merge(found)
    log(f"Stored: {new} new startup(s), {upd} seen again")
    store.update_meta(refreshed=now_iso(), new=new, updated=upd)
    n = int(getattr(cfg, "enrich_per_refresh", 0) or 0)
    keys = keys or {}
    if n and (keys.get("fundable") or keys.get("pdl")):
        todo = [s for s in store.list() if s.get("stage", "Unknown") == "Unknown" and not s.get("looked_up") and s.get("website")][:n]
        log(f"Looking up the round of {len(todo)} startup(s)")
        for s in todo:
            got, said = enrich(s, keys, client)
            store.update(s["id"], looked_up=now_iso())
            if got:
                store.merge([{**got, "name": s["name"], "website": s.get("website", "")}])
                log(f"  {s['name']}: {funding_line(funding_block(store.get(s['id'])))} ({said})")
            else:
                log(f"  {s['name']}: {said}")
    if getattr(cfg, "roles_auto", False) and searches and jobs_store is not None:
        exclude = list(getattr(cfg, "roles_exclude_titles", None) or [])
        store_known_roles(store, searches, jobs_store, log=log, exclude=exclude)
        scan_boards(store, searches, jobs_store, limit=int(getattr(cfg, "roles_max_per_refresh", 300) or 300), client=client, log=log,
                    exclude=exclude)
    if jobs_store is not None and getattr(cfg, "check_postings", True):
        from .postings import check_closed
        check_closed(jobs_store, tracked=tracked, client=client, log=log)
    return new, upd


# ---- scoring the roles against the impact record (opt-in) --------------------------------------------------------
def unscored_startup_roles(jobs_store: Any) -> list[str]:
    """Startup roles in Find jobs with no signal score yet, newest first."""
    state = jobs_store.state()
    ids = [jid for jid, st in state.items() if jid.startswith("startup-") and not st.get("triage") and not st.get("dismissed")
           and not st.get("tailored_at")]
    return sorted(ids, key=lambda j: state[j].get("first_seen") or "", reverse=True)


async def score_startup_roles(cfg: Any, jobs_store: Any, *, limit: int = 30, log: Callable[[str], None] = print,
                              runner: Any = None, tracker: Any = None, tracked: Optional[dict[str, str]] = None,
                              client: Optional[httpx.Client] = None) -> int:
    """Signal-score up to `limit` unscored startup roles (one short Claude call each, three at a time), so Find jobs
    can be filtered by fit, and put the ones scoring cfg.track_min_fit or more in the tracker. A role with no posting
    text whose posting has closed is tagged Closed, and dismissed unless it's in `tracked` (stored job id -> tracker
    status), as check-postings does. `cfg` is the full Config. Returns how many were scored."""
    from .jd import build_jd
    from .lowfit import dismiss_low_fit
    from .pipeline import make_runner
    from .postings import mark_if_closed
    from .triage import Triage, triage_many
    ids = unscored_startup_roles(jobs_store)[:limit]
    if not ids:
        log("Startup roles: nothing left to score")
        return 0
    todo = {}
    for jid in ids:
        jd_md, usable = build_jd(jobs_store.job(jid))
        if usable:
            todo[jid] = jd_md
        elif mark_if_closed(jobs_store, jid, client):
            if jid not in (tracked or {}):
                jobs_store.update(jid, dismissed=True)
            log(f"  {jid}: posting has closed")
        else:
            log(f"  {jid}: no posting text to score")
    log(f"Startup roles: scoring {len(todo)} against the impact record")
    results = await triage_many(runner or make_runner(cfg), cfg.triage_model, todo)
    today = datetime.now(timezone.utc).date().isoformat()
    scored = []
    for jid, r in results.items():
        if isinstance(r, Triage):
            jobs_store.update(jid, triage=r.__dict__, triaged_at=today)
            job = jobs_store.job(jid)
            log(f"  {r.fit_score}/10  {job.get('job_title')} @ {job.get('company')}: {r.one_line}")
            scored.append(jid)
        else:
            log(f"  {jid}: scoring failed ({r})")
    track_scored_roles(cfg, jobs_store, scored, tracker=tracker, log=log)
    dismiss_low_fit(cfg, jobs_store, scored, log)
    return len(scored)


def track_scored_roles(cfg: Any, jobs_store: Any, ids: Optional[list[str]] = None, *, min_fit: Optional[int] = None,
                       tracker: Any = None, log: Callable[[str], None] = print,
                       read: Optional[Callable[[str], dict]] = None) -> int:
    """Put the scored startup roles (`ids`, or every one in Find jobs) at or above `min_fit` (default
    cfg.track_min_fit) in the tracker as Not started, as a pipeline run does for its jobs. Dismissed, tailored and
    already-tracked roles are left alone. A role whose board gave no posting text gets it from its link (`read`,
    default jd.read_posting). Notion gets them in one sync at the end. Returns how many were added."""
    from .pipeline import Candidate, make_tracker, tracker_entry
    from .jd import posting_url
    from .postings import fetch_posting, has_posting
    from .triage import Triage
    state = jobs_store.state()
    ids = [jid for jid in (ids if ids is not None else state) if jid.startswith("startup-")]
    floor = min_fit if min_fit is not None else cfg.track_min_fit("startup-")
    if floor is None:
        return 0
    todo = [jid for jid in ids if (st := state.get(jid) or {}).get("triage") and not st.get("dismissed")
            and not st.get("tailored_at") and st["triage"].get("fit_score", 0) >= floor]
    if not todo:
        return 0
    tracker = tracker or make_tracker(cfg, log)
    tracked = {r.get("job_id") for r in tracker.rows()}
    today = datetime.now(timezone.utc).date().isoformat()
    n = 0
    for jid in todo:
        if jid in tracked:
            continue
        blank = {"band_reason": "", "role_family": "", "level_match": "", "hard_requirement_issues": [],
                 "strongest_matches": [], "likely_gaps": [], "recommended_base": "", "one_line": ""}
        tri = Triage(**{**blank, **{k: v for k, v in state[jid]["triage"].items() if k in Triage.__dataclass_fields__}})
        e = tracker_entry(Candidate(jobs_store.job(jid), "startups", triage=tri), today)
        if tracker.track(e, job_id=jid) == "created":
            n += 1
            log(f"  Tracker: added {e.title} @ {e.company} ({tri.fit_score}/10)")
            if not has_posting(jobs_store.job(jid)) and fetch_posting(jobs_store, jid, read):
                log(f"    couldn't read its posting from {posting_url(jobs_store.job(jid))}")
    if n:
        log(f"Startup roles: {n} scoring {floor}+ added to the tracker")
        try:
            tracker.sync()
        except Exception as e:  # noqa: BLE001 - they stay queued; the app's next sync sends them
            log(f"  Notion didn't answer ({e}); they'll be copied there on the next sync")
    return n
