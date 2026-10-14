"""JobsPipe job search with a credit budget.

Pricing reminder (docs.jobspipe.dev, Rate limits & quotas): 1 credit per distinct job
returned per calendar month; empty results, jobs already paid for this month, and
401/402/429/502/504 responses are free; a 400 costs 1 credit, so a bad request is
never retried.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

import httpx
import jobspipe

from .config import Config, Search
from .doh import DoHTransport
from .store import Store, safe_id


class BudgetExhausted(RuntimeError):
    pass


@dataclass
class Budget:
    per_run: int
    allowance: int
    period: str
    store: Store
    spent_this_run: int = 0

    def remaining(self) -> int:
        left_allowance = self.allowance - self.store.credits_used(self.period)
        return max(0, min(self.per_run - self.spent_this_run, left_allowance))


@dataclass
class SearchResult:
    search: Search
    jobs: list[dict] = field(default_factory=list)
    total_results: Optional[int] = None
    credits_charged: Optional[int] = None
    jobs_already_paid: Optional[int] = None
    skipped_reason: Optional[str] = None


def _meta(resp: jobspipe.JobSearchResponse, key: str) -> Optional[int]:
    val = getattr(resp.metadata, key, None)
    if val is None and resp.metadata.model_extra:
        val = resp.metadata.model_extra.get(key)
    return int(val) if val is not None else None


def _http_client() -> httpx.Client:
    """The JobsPipe library's own HTTP settings, with the API's address found by DNS over HTTPS
    (a network DNS filter sent it to a block page; see doh.py)."""
    base_url = os.environ.get("JOBSPIPE_BASE_URL") or "https://api.jobspipe.dev"
    return httpx.Client(base_url=base_url, timeout=httpx.Timeout(60.0, connect=10.0),
                        transport=DoHTransport({urlsplit(base_url).hostname or ""}))


class JobsClient:
    def __init__(self, cfg: Config, store: Store, client: Optional[jobspipe.Jobspipe] = None):
        self.cfg = cfg
        self.store = store
        self._client = client
        self.budget = Budget(cfg.max_credits_per_run, cfg.allowance_amount, cfg.allowance_period, store)

    @property
    def client(self) -> jobspipe.Jobspipe:
        if self._client is None:  # created on first use, so commands that don't search need no key
            self._client = jobspipe.Jobspipe(http_client=_http_client())
        return self._client

    def _call(self, search: Search, body: dict) -> jobspipe.JobSearchResponse:
        try:
            resp = self.client.jobs.search(**body)
        except jobspipe.BadRequestError as e:
            # A rejected body costs 1 credit. Record it and surface the API's message.
            self.store.record_call(search_id=search.id, body=body, charged=1, returned=0,
                                   already_paid=None, status=f"400: {e.body or e.message}")
            self.budget.spent_this_run += 1
            raise
        except jobspipe.PaymentRequiredError:
            self.store.record_call(search_id=search.id, body=body, charged=0, returned=0,
                                   already_paid=None, status="402 out of credits")
            raise BudgetExhausted("JobsPipe says the credit quota is used up (402).") from None
        charged = _meta(resp, "credits_charged")
        paid = _meta(resp, "jobs_already_paid")
        counted = charged if charged is not None else len(resp.data)
        self.store.record_call(search_id=search.id, body=body, charged=charged,
                               returned=len(resp.data), already_paid=paid, status="ok")
        self.budget.spent_this_run += counted
        return resp

    def size(self, search: Search) -> SearchResult:
        """Count matches for a search. Pays for at most one job."""
        if self.budget.remaining() < 1:
            return SearchResult(search, skipped_reason="no credits left in this run's budget")
        body = search.request_body(limit=1)
        body["include_total_results"] = True
        resp = self._call(search, body)
        return SearchResult(search, jobs=[j.model_dump() for j in resp.data],
                            total_results=resp.metadata.total_results,
                            credits_charged=_meta(resp, "credits_charged"),
                            jobs_already_paid=_meta(resp, "jobs_already_paid"))

    def run(self, search: Search) -> SearchResult:
        remaining = self.budget.remaining()
        if remaining < 1:
            return SearchResult(search, skipped_reason="no credits left in this run's budget")
        limit = min(search.limit, remaining)
        body = search.request_body(limit=limit)
        resp = self._call(search, body)
        jobs = [{**j.model_dump(), "id": safe_id(j.id)} for j in resp.data]
        for job in jobs:
            self.store.save_job(job, search.id)
        return SearchResult(search, jobs=jobs, total_results=resp.metadata.total_results,
                            credits_charged=_meta(resp, "credits_charged"),
                            jobs_already_paid=_meta(resp, "jobs_already_paid"),
                            skipped_reason=None if limit == search.limit else
                            f"limit lowered from {search.limit} to {limit} to stay in budget")


def post_filter(job: dict, search: Search, enforce_min_salary: bool) -> tuple[bool, list[str]]:
    """Local safety net on jobs already paid for. Returns (keep, flags)."""
    flags: list[str] = []
    if search.remote and job.get("remote") is False:
        return False, ["not remote"]
    if "hybrid" in [w.lower() for w in search.work_arrangement] and job.get("hybrid") is False:
        return False, ["not hybrid"]
    if enforce_min_salary and search.min_salary_usd:
        top = job.get("max_annual_salary_usd") or job.get("min_annual_salary_usd")
        if top is None:
            flags.append("pay not in USD" if job.get("salary_string") else "pay not stated")
        elif top < search.min_salary_usd:
            return False, [f"pay tops out at ${top:,.0f}"]
    if job.get("closed_at"):
        return False, ["posting closed"]
    return True, flags


# Pay a posting states in US dollars, in its own words: "$120K – $315K • Offers Equity", "$94,000 - $225,000", "US$180k".
# Another currency ("GBP 95k–120k", "€80k", "C$150k") or a rate per hour, day, week or month gives no figures.
_USD_AMOUNT = re.compile(r"(?<![A-Za-z])(?:US)?\$\s?(\d[\d,]*(?:\.\d+)?)\s*([kK])?(?![\d,])")
_OTHER_CURRENCY = re.compile(r"[€£¥₹]|\b(?:EUR|GBP|CAD|AUD|NZD|CHF|SEK|NOK|DKK|PLN|INR|JPY|SGD|HKD|MXN|BRL|ZAR)\b|(?<![A-Za-z])(?:C|CA|A|AU|NZ|S|HK|MX|R)\$", re.I)
_NOT_YEARLY = re.compile(r"\b(?:hour|hourly|hr|day|daily|week|weekly|month|monthly)\b|/\s*(?:hr|hour|day|wk|week|mo|month)\b", re.I)


def usd_range(text: str) -> tuple[Optional[float], Optional[float]]:
    """(low, high) of the yearly pay in US dollars a pay string states, or (None, None). An amount under $10k or
    over $5M (a bonus, a rate, a typo) is left out."""
    if not text or _OTHER_CURRENCY.search(text) or _NOT_YEARLY.search(text):
        return None, None
    amounts = []
    for num, k in _USD_AMOUNT.findall(text):
        try:
            v = float(num.replace(",", "")) * (1000 if k else 1)
        except ValueError:
            continue
        if 10_000 <= v <= 5_000_000:
            amounts.append(v)
    return (min(amounts), max(amounts)) if amounts else (None, None)


def usd_pay(job: dict) -> tuple[Optional[float], Optional[float]]:
    """The job's yearly pay in US dollars, (low, high): the figures its source gave, else what its pay text states."""
    lo, hi = job.get("min_annual_salary_usd"), job.get("max_annual_salary_usd")
    return (lo, hi) if lo or hi else usd_range(job.get("salary_string") or "")


def salary_text(job: dict) -> str:
    lo, hi = usd_pay(job)
    if lo and hi and lo != hi:
        return f"${lo/1000:,.0f}k–${hi/1000:,.0f}k"
    if hi or lo:
        return f"${(hi or lo)/1000:,.0f}k"
    return job.get("salary_string") or "not stated"


def work_mode(job: dict) -> str:
    if job.get("remote"):
        return "Remote"
    if job.get("hybrid"):
        return "Hybrid"
    return "On-site" if job.get("remote") is False else "Not stated"
