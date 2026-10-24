"""Stage 0: build jd.md for a job (posting text verbatim, ATS on line 1, source URL on line 2)."""
from __future__ import annotations

import html
import json
import re
from typing import Any, Iterator, Optional
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx

from .jobs_api import salary_text, work_mode
from .store import canonical_url

MIN_DESCRIPTION_CHARS = 600  # below this the listing is a stub; never score from a title alone
MAX_PAGE_CHARS = 40_000      # more visible text than this is an app shell or a whole site, not one posting
MIN_PASTED_CHARS = 300       # text you pasted from the posting is trusted as the whole posting from here up


def detect_ats(*urls: Optional[str]) -> str:
    joined = " ".join(u for u in urls if u).lower()
    if "gh_jid=" in joined or "greenhouse.io" in joined:
        return "Greenhouse"
    if "lever.co" in joined:
        return "Lever"
    if "myworkdayjobs.com" in joined or "workday" in joined:
        return "Workday"
    if "icims.com" in joined:
        return "iCIMS"
    if "ashbyhq.com" in joined:
        return "Ashby"
    if "smartrecruiters.com" in joined:
        return "SmartRecruiters"
    return "not identified"


def posting_url(job: dict) -> str:
    return job.get("final_url") or job.get("url") or job.get("source_url") or ""


def normalize_posting_url(url: str) -> str:
    """The link to keep for a posting. A LinkedIn job copied from search results or a feed
    (…/jobs/search/?currentJobId=123) becomes that job's own page; an iCIMS link loses the
    tracking and iframe-sizing parameters (…/job?iis=LinkedIn&width=1200&height=800…)."""
    url = url.strip()
    p = urlsplit(url)
    if p.netloc.lower().removeprefix("www.").endswith("linkedin.com"):
        jid = linkedin_job_id(url)
        if jid:
            return f"https://www.linkedin.com/jobs/view/{jid}"
    if p.netloc.lower().endswith(".icims.com") and re.match(r"/jobs/\d+", p.path):
        return urlunsplit(p._replace(query="", fragment=""))
    return url


def linkedin_job_id(url: str) -> str:
    m = re.search(r"[?&]currentJobId=(\d+)", url) or re.search(r"/jobs/view/(?:[^/?#]*?-)?(\d{6,})", url)
    return m.group(1) if m else ""


# Query parameters that name the job on a careers page (the rest is tracking).
JOB_ID_PARAMS = {"gh_jid", "ashby_jid", "jk", "jobid", "job_id", "id", "currentjobid", "req", "reqid", "pid"}


def posting_key(url: Optional[str]) -> str:
    """Compares equal for two links to the same posting. Unlike canonical_url it keeps the job's id
    when that is in the query string (acme.com/careers?gh_jid=1 and ?gh_jid=2 are different jobs)."""
    if not url:
        return ""
    url = normalize_posting_url(url)
    ids = sorted((k.lower(), v) for k, v in parse_qsl(urlsplit(url).query) if k.lower() in JOB_ID_PARAMS)
    return canonical_url(url) + "".join(f"&{k}={v}" for k, v in ids)


def html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", raw)
    raw = re.sub(r"(?i)<li[^>]*>", "- ", raw)
    text = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def _ld_json_posting(raw: str, meta: Optional[dict] = None) -> str:
    """The description in a page's schema.org JobPosting block. Careers sites built in JavaScript
    (Ashby, and many company sites) show no text without a browser but still publish this for search engines.
    Its title, employer, place and pay go into `meta` when given."""
    best, item_best = "", None
    for m in re.finditer(r'(?is)<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', raw):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(data, dict):
            data = data.get("@graph") or [data]
        for item in data if isinstance(data, list) else []:
            if isinstance(item, dict) and item.get("@type") == "JobPosting" and isinstance(item.get("description"), str):
                text = html_to_text(html.unescape(item["description"]))
                if len(text) >= len(best):
                    best, item_best = text, item
    if meta is not None and item_best:
        _fill(meta, **_ld_json_meta(item_best))
    return best


def _ld_json_meta(item: dict) -> dict:
    org = item.get("hiringOrganization")
    places = item.get("jobLocation") or []
    places = places if isinstance(places, list) else [places]
    where = []
    for place in places:
        a = place.get("address") if isinstance(place, dict) else None
        if isinstance(a, dict):
            bits = [a.get(k) for k in ("addressLocality", "addressRegion", "addressCountry")]
            bits = [b.get("name") if isinstance(b, dict) else b for b in bits]
            line = ", ".join(dict.fromkeys(str(b).strip() for b in bits if b and str(b).strip()))
            if line and line not in where:
                where.append(line)
    out = {"job_title": html.unescape(str(item.get("title") or "")).strip(),
           "company": html.unescape(str(org.get("name") if isinstance(org, dict) else org or "")).strip(),
           "location": "; ".join(where[:3])}
    if str(item.get("jobLocationType") or "").upper() == "TELECOMMUTE":
        out["remote"] = True
    pay = item.get("baseSalary")
    value = pay.get("value") if isinstance(pay, dict) else None
    if isinstance(value, dict) and str(value.get("unitText") or "").upper() == "YEAR" \
            and str(pay.get("currency") or "USD").upper() == "USD":
        for src, dst in (("minValue", "min_annual_salary_usd"), ("maxValue", "max_annual_salary_usd")):
            try:
                out[dst] = float(value[src])
            except (KeyError, TypeError, ValueError):
                pass
    return out


def _fill(meta: dict, **fields: Any) -> None:
    """Keep the first non-empty value found for each field."""
    for k, v in fields.items():
        if v not in (None, "") and meta.get(k) in (None, ""):
            meta[k] = v


def _page_title_meta(raw: str, url: str = "") -> dict:
    """Title and employer from the page's own title, for pages with no structured job data:
    "Job Application for X at Y" (Greenhouse), "X @ Y" (Ashby), "X at Y — Place | LinkedIn",
    and "Y | X" or "X | Y" where Y is the site's own name (a careers site titled "Acme | Staff TPM")."""
    m = re.search(r'(?is)<meta[^>]*property=["\']og:title["\'][^>]*content=["\']([^"\']*)', raw) \
        or re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    t = re.sub(r"\s+", " ", html.unescape(m.group(1))).strip() if m else ""
    t = re.sub(r"\s*[|–—-]\s*(LinkedIn( Jobs)?|Careers?|Jobs?)$", "", t, flags=re.I)
    t = re.sub(r"^Job Application for\s+", "", t, flags=re.I)
    place = ""
    m = re.match(r"(.+?)\s+[—–]\s+([^—–]+)$", t)
    if m and " at " in m.group(1):
        t, place = m.group(1), m.group(2).strip()
    m = re.match(r"(.+)\s+(?:@|at)\s+(.+)$", t)
    if m:
        return {"job_title": m.group(1).strip(), "company": m.group(2).strip(), "location": place}
    host = urlsplit(url).netloc.lower().split(":")[0].split(".")
    site = host[-2] if len(host) >= 2 else ""
    parts = [s.strip() for s in t.split(" | ")]
    if site and len(parts) == 2:
        for i, name in enumerate(parts):
            if re.sub(r"[^a-z0-9]", "", name.lower()) == site:
                return {"job_title": parts[1 - i], "company": name}
    return {"job_title": t[:150]}


def _linkedin_posting(jid: str, c: httpx.Client, meta: dict) -> str:
    """A LinkedIn job's public page, without signing in."""
    r = c.get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}")
    if r.status_code != 200:
        return ""
    raw = r.text
    grab = lambda pat: html_to_text(m.group(1)) if (m := re.search(pat, raw, re.S)) else ""
    _fill(meta, job_title=grab(r'class="[^"]*top-card-layout__title[^"]*"[^>]*>(.*?)</h2>'),
          company=grab(r'class="[^"]*topcard__org-name-link[^"]*"[^>]*>(.*?)</a>'),
          location=grab(r'class="[^"]*topcard__flavor--bullet[^"]*"[^>]*>(.*?)</span>'))
    return grab(r'class="show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>')


def _dicts(node: Any) -> Iterator[dict]:
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _dicts(v)


def _next_data_posting(raw: str, url: str) -> str:
    """The posting inside a Next.js page's data blob (e.g. mercor.com): the job whose id is in the URL,
    else the longest description on the page."""
    m = re.search(r'(?is)<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', raw)
    if not m:
        return ""
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return ""
    ids = set(re.findall(r"[0-9a-f]{8}-[0-9a-f-]{27}|\d{5,}", url.lower()))
    found = []
    for d in _dicts(data):
        text = next((d[k] for k in ("descriptionHtml", "description_html", "description", "descriptionPlain")
                     if isinstance(d.get(k), str) and len(d[k]) >= MIN_DESCRIPTION_CHARS), "")
        if text:
            mine = any(str(v).lower() in ids for v in d.values() if isinstance(v, (str, int)))
            found.append((mine, len(text), text))
    return html_to_text(max(found)[2]) if found else ""


def _ats_posting(url: str, c: httpx.Client, meta: dict) -> str:
    """Ask the applicant-tracking system directly, for the sites whose pages hold no posting text."""
    p = urlsplit(url)
    host, sld = p.netloc.lower(), p.netloc.lower().removeprefix("www.").split(".")[0]
    m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([^/?&]+)/jobs/(\d+)", url)
    gh = [(m.group(1), m.group(2))] if m else []
    m = re.search(r"[?&]gh_jid=(\d+)", url)
    if m:  # a company page that embeds its Greenhouse board; the board is usually named after the company
        gh += [(sld, m.group(1)), (sld.replace("careers", ""), m.group(1))]
    for board, jid in gh:
        r = c.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid}")
        if r.status_code == 200:
            d = r.json()
            _fill(meta, job_title=d.get("title"), company=d.get("company_name"),
                  location=(d.get("location") or {}).get("name"))
            return html_to_text(html.unescape(d.get("content") or ""))
    m = re.search(r"/job/(\d+)", p.path)
    if m and host == "careers.oracle.com":
        r = c.get("https://eeho.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails",
                  params={"expand": "all", "onlyData": "true", "finder": f'ById;Id="{m.group(1)}",siteNumber=CX_45001'})
        items = r.json().get("items") if r.status_code == 200 else None
        if items:
            _fill(meta, job_title=items[0].get("Title"), company="Oracle", location=items[0].get("PrimaryLocation"))
            return html_to_text("\n".join(items[0].get(k) or "" for k in (
                "ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr")))
    m = re.search(r"/careers/job/(\d+)", p.path)
    if m:  # Eightfold (e.g. apply.careers.microsoft.com): the page is an app shell, the API has the text
        r = c.get(f"https://{host}/api/apply/v2/jobs/{m.group(1)}", params={"domain": ".".join(host.split(".")[-2:])})
        if r.status_code == 200 and "json" in r.headers.get("content-type", ""):
            d = r.json()
            _fill(meta, job_title=d.get("name"), location=d.get("location"))
            return html_to_text(d.get("job_description") or "")
    if host.endswith(".icims.com") and re.match(r"/jobs/\d+", p.path):
        return _icims_posting(urlunsplit(p._replace(query="")), c, meta)
    m = re.match(r"/([^/]+)/j/([A-Za-z0-9]+)", p.path)
    if m and host == "apply.workable.com":  # a Workable job page is an app shell, its API has the text
        r = c.get(f"https://apply.workable.com/api/v2/accounts/{m.group(1)}/jobs/{m.group(2)}")
        if r.status_code == 200:
            d = r.json()
            loc = d.get("location") or {}
            _fill(meta, job_title=d.get("title"), location=", ".join(x for x in (loc.get("region"), loc.get("country")) if x),
                  remote=True if d.get("remote") or d.get("workplace") == "remote" else None)
            return html_to_text("\n".join(d.get(k) or "" for k in ("description", "requirements", "benefits")))
    jid = linkedin_job_id(url) if sld == "linkedin" else ""
    if jid:
        return _linkedin_posting(jid, c, meta)
    return ""


def _icims_posting(url: str, c: httpx.Client, meta: dict) -> str:
    """An iCIMS job: the page at the link is the employer's site around an iframe, and the posting
    (with its JobPosting data) is the iframe's own page, …/job?in_iframe=1."""
    r = c.get(url, params={"in_iframe": "1"})
    return _ld_json_posting(r.text, meta) if r.status_code == 200 else ""


def fetch_posting_text(url: str, client: Optional[httpx.Client] = None) -> str:
    """Best-effort text of the employer's posting: the ATS's own API where the page has none, then the
    structured job data inside the page, then the page's visible text."""
    if not url:
        return ""
    c = client or httpx.Client(timeout=20, follow_redirects=True,
                               headers={"User-Agent": "Mozilla/5.0 (jobpipe resume tailoring)"})
    return _read_with_retry(url, c, {})


def read_posting(url: str, client: Optional[httpx.Client] = None) -> dict:
    """A posting read from its link, as a job to store: url, job_title, company, location, description
    (and remote or pay when the page states them). Fields the page doesn't give are left out;
    description is "" when the page has no posting text this can read. browser_check names the site
    when it only shows its pages to a person's browser."""
    url = normalize_posting_url(url)
    c = client or httpx.Client(timeout=20, follow_redirects=True,
                               headers={"User-Agent": "Mozilla/5.0 (jobpipe resume tailoring)"})
    meta: dict = {}
    text = _read_with_retry(url, c, meta)
    return {"url": url, **{k: v for k, v in meta.items() if v not in (None, "")}, "description": text}


# Sites that show every page only after a "verify you are human" check (Cloudflare's challenge), whatever
# the request says it is: the app can't read them, so it doesn't try. HiringCafe's listings, searches and
# API all answered with the challenge on 2026-10-17. Its Apply button leads to the employer's own posting.
BROWSER_CHECK_SITES = {"hiring.cafe": "HiringCafe", "hiringcafe.com": "HiringCafe"}


def browser_check_site(url: str) -> str:
    """The site's name when its pages only open in a person's browser, else ""."""
    return BROWSER_CHECK_SITES.get(urlsplit(url).netloc.lower().removeprefix("www."), "")


def _read_with_retry(url: str, c: httpx.Client, meta: dict) -> str:
    site = browser_check_site(url)
    if site:
        meta["browser_check"] = site
        return ""
    try:
        return _read_posting(url, c, meta)
    except httpx.TransportError:      # a timeout or dropped connection: one more try
        try:
            return _read_posting(url, c, meta)
        except (httpx.HTTPError, ValueError):
            return ""
    except (httpx.HTTPError, ValueError):
        return ""


def _read_posting(url: str, c: httpx.Client, meta: dict) -> str:
    text = _ats_posting(url, c, meta)
    if len(text) >= MIN_DESCRIPTION_CHARS:
        return text
    r = c.get(url)
    if r.headers.get("cf-mitigated") == "challenge":  # Cloudflare wants a person's browser first
        meta["browser_check"] = r.request.headers.get("host", r.url.host).removeprefix("www.")
        return ""
    if r.status_code >= 400:
        return ""
    raw = r.text
    try:
        text = _ld_json_posting(raw, meta)
        if len(text) >= MIN_DESCRIPTION_CHARS:
            return text
        # A company page built on its Ashby board: the board's own page has the posting with its
        # title, employer, place and pay, which the company page often leaves out.
        board = _ashby_board_page(url, raw)
        if board:
            text = _ld_json_posting(c.get(board).text, meta)
            if len(text) >= MIN_DESCRIPTION_CHARS:
                return text
        text = _next_data_posting(raw, url)
        if len(text) >= MIN_DESCRIPTION_CHARS:
            return text
        # A company page that embeds its iCIMS portal in an iframe.
        frame = re.search(r'(?is)<iframe[^>]*src=["\'](https?://[^"\']*\.icims\.com/jobs/\d+[^"\']*)', raw)
        if frame:
            text = _icims_posting(html.unescape(frame.group(1)).split("?")[0], c, meta)
            if len(text) >= MIN_DESCRIPTION_CHARS:
                return text
        text = html_to_text(raw)
        return text if len(text) <= MAX_PAGE_CHARS else ""
    finally:
        _fill(meta, **_page_title_meta(raw, url))


UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def _ashby_board_page(url: str, raw: str) -> str:
    """The Ashby board page for the job on a company page: one that embeds the board (?ashby_jid=...),
    or one that shows the job under its Ashby id and links to it (acme.example/careers/<id>)."""
    if urlsplit(url).netloc.lower() == "jobs.ashbyhq.com":
        return ""
    jid = re.search(rf"[?&]ashby_jid=({UUID})", url, re.I)
    org = re.search(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)", raw)
    if jid and org:
        return f"https://jobs.ashbyhq.com/{org.group(1)}/{jid.group(1)}"
    for uid in re.findall(UUID, urlsplit(url).path.lower()):
        org = re.search(rf"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)/{uid}", raw, re.I)
        if org:
            return f"https://jobs.ashbyhq.com/{org.group(1)}/{uid}"
    return ""


def description(job: dict, fetch: bool = True) -> tuple[str, str]:
    """(posting text, where it came from). Text you pasted wins; otherwise the longer of the API
    text and the live page."""
    api_text = (job.get("description") or "").strip()
    if job.get("description_pasted") and api_text:
        return api_text, "pasted from the posting by you"
    if "<" in api_text and ">" in api_text:
        api_text = html_to_text(api_text)
    listing = f"{job.get('source') or 'JobsPipe'} description"
    if len(api_text) >= MIN_DESCRIPTION_CHARS or not fetch:
        return api_text, listing
    page = fetch_posting_text(posting_url(job))
    if len(page) > len(api_text):
        return page, "employer posting page"
    return api_text, listing


def build_jd(job: dict, fetch: bool = True) -> tuple[str, bool]:
    """Returns (jd.md text, usable). usable is False when there is no real posting text."""
    text, origin = description(job, fetch=fetch)
    url = posting_url(job)
    ats = detect_ats(job.get("final_url"), job.get("url"), job.get("source_url"))
    header = [
        f"ATS: {ats}",
        f"Source: {url}",
        "",
        f"# {job.get('job_title', '').strip()} — {job.get('company') or 'Company not stated'}",
        "",
        f"Listing metadata (from {job.get('source') or 'JobsPipe'}, not the posting text):",
        f"- Location: {job.get('location') or job.get('long_location') or 'Not stated'}",
        f"- Work arrangement: {work_mode(job)}",
        f"- Pay: {salary_text(job)}",
        f"- Posted: {job.get('date_posted') or 'Not stated'}",
        f"- Posting text source: {origin}",
        "",
        "## Posting text (verbatim)",
        "",
    ]
    floor = MIN_PASTED_CHARS if job.get("description_pasted") else MIN_DESCRIPTION_CHARS
    return "\n".join(header) + text + "\n", len(text) >= floor
