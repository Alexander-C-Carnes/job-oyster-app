import time
from urllib.parse import parse_qs, urlsplit

import httpx

from jobpipe import config, regions
from jobpipe.config import Search
from jobpipe.himalayas import Himalayas, to_job, wants
from jobpipe.jobs_api import JobsClient
from jobpipe.pipeline import Pipeline
from jobpipe.store import Store, safe_id
from conftest import FakeJobsAPI, FakeRunner, jobspipe_client, make_job

NOW = int(time.time())
DESC = "<p>" + "Lead the program across platform teams. " * 40 + "</p>"


def raw(slug, title, where, days_ago=1, **kw):
    url = f"https://himalayas.app/companies/{slug}/jobs/{slug}-role"
    return {"title": title, "companyName": slug.title(), "companySlug": slug, "locationRestrictions": where,
            "pubDate": str(NOW - days_ago * 86400), "guid": url, "applicationLink": url, "description": DESC,
            "minSalary": None, "maxSalary": None, "currency": None, "salaryPeriod": "annual", "seniority": ["Manager"], **kw}


JOBS = [raw("nordwind", "Engineering Manager, Platform", ["Germany", "Netherlands"]),
        raw("anywhere", "Senior Engineering Manager", []),
        raw("texan", "Engineering Manager", ["United States"]),
        raw("london", "Engineering Manager", ["United Kingdom"]),
        raw("salesy", "Sales Engineering Manager", ["Germany"]),          # off-track for an Engineering Manager filter
        raw("director", "Director of Product", ["Germany"]),               # q matched its description, not its title
        raw("stale", "Engineering Manager", ["France"], days_ago=30)]


class FakeHimalayas:
    def __init__(self, jobs=JOBS):
        self.jobs, self.calls = jobs, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        q = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
        self.calls.append(q)
        names = {"DE": "Germany", "NL": "Netherlands", "FR": "France", "GB": "United Kingdom", "US": "United States"}
        if q.get("worldwide") == "true":
            jobs = [j for j in self.jobs if not j["locationRestrictions"]]
        elif q.get("country"):
            jobs = [j for j in self.jobs if names.get(q["country"]) in j["locationRestrictions"]
                    or (not j["locationRestrictions"] and q.get("exclude_worldwide") != "true")]
        else:
            jobs = self.jobs
        page = int(q.get("page", 1))
        return httpx.Response(200, json={"jobs": jobs[(page - 1) * 20: page * 20], "totalCount": len(jobs)})


def hima(fake):
    return Himalayas(client=httpx.Client(transport=httpx.MockTransport(fake)), pause=0)


def test_a_region_filter_keeps_the_roles_it_covers():
    fake = FakeHimalayas()
    eu = Search(id="em-eu", name="EM EU", titles=["Engineering Manager"], remote=True, country=["EU", "Schengen"], posted_within_days=7)
    got = {j["company"] for j in hima(fake).search(eu)}
    assert got == {"Nordwind", "Anywhere"}
    # a region: each of its countries without the worldwide jobs, then the worldwide ones, once each
    by_country = [c for c in fake.calls if "country" in c]
    assert len(fake.calls) == 32 and [c for c in fake.calls if c.get("worldwide") == "true"]
    assert {c["country"] for c in by_country} == set(regions.codes(["EU", "Schengen"]))
    assert all(c["exclude_worldwide"] == "true" and c["sort"] == "recent" for c in by_country)


def test_a_one_country_filter_asks_by_country_and_shares_queries():
    fake = FakeHimalayas()
    h = hima(fake)
    uk = Search(id="em-uk", name="EM UK", titles=["Engineering Manager"], remote=True, country="GB")
    assert {j["company"] for j in h.search(uk)} == {"London", "Anywhere"}
    assert sorted(c.get("country", "worldwide") for c in fake.calls) == ["GB", "worldwide"]
    n = len(fake.calls)
    h.search(Search(id="em-uk-2", name="again", titles=["Engineering Manager"], remote=True, country="GB"))
    assert len(fake.calls) == n                      # same query this run: asked once


def test_titles_with_the_same_words_share_a_query_and_a_cap_stops_a_flood():
    fake = FakeHimalayas()
    h = Himalayas(client=httpx.Client(transport=httpx.MockTransport(fake)), pause=0, max_requests=3)
    s = Search(id="em", name="EM", titles=["Engineering Manager", "Manager, Engineering"], remote=True, country=["EU"])
    h.search(s)
    assert len(fake.calls) == 3 and h.limited      # one query per country for both titles, cut off at the cap


def test_a_429_waits_and_asks_again():
    calls = []
    def flaky(request):
        calls.append(1)
        return httpx.Response(429) if len(calls) == 1 else httpx.Response(200, json={"jobs": [raw("london", "Engineering Manager", ["United Kingdom"])]})
    h = Himalayas(client=httpx.Client(transport=httpx.MockTransport(flaky)), pause=0, backoff=0)
    uk = Search(id="em-uk", name="EM UK", titles=["Engineering Manager"], remote=True, country="GB")
    assert [j["company"] for j in h.search(uk)] == ["London"] and not h.limited


def test_hybrid_filters_skip_himalayas():
    ny = Search(id="em-ny", name="EM NY", titles=["Engineering Manager"], work_arrangement=["hybrid"], locations=["New York"])
    assert not wants(ny) and hima(FakeHimalayas()).search(ny) == []


def test_jobs_are_stored_like_jobspipes_with_their_source():
    j = to_job(raw("nordwind", "Engineering Manager", ["Germany"], minSalary="9000", maxSalary="11000", currency="EUR", salaryPeriod="monthly"))
    assert j["id"] == safe_id(j["url"]) and j["url"].startswith("https://himalayas.app/") and j["source"] == "Himalayas"
    assert j["country_codes"] == ["DE"] and j["remote"] and j["salary_string"] == "EUR 108k–132k"
    assert "max_annual_salary_usd" not in j and len(j["description"]) > 600
    us = to_job(raw("texan", "EM", ["United States"], minSalary="200000", maxSalary="250000", currency="USD"))
    assert us["max_annual_salary_usd"] == 250000


def test_a_run_adds_himalayas_jobs_free_beside_jobspipe(tmp_dirs):
    cfg = config.load()
    store = Store()
    eu = Search(id="em-eu", name="EM EU", titles=["Engineering Manager"], remote=True, country=["EU", "Schengen"], min_salary_usd=200000)
    fake_api = FakeJobsAPI({"Engineering Manager": [make_job("jp1", title="Engineering Manager")]})
    p = Pipeline(cfg, store=store, jobs=JobsClient(cfg, store, client=jobspipe_client(fake_api)), runner=FakeRunner({}),
                 use_notion=False, log=lambda s: None, fetch_pages=False, himalayas=hima(FakeHimalayas()))
    cands, _ = p.search([eu])
    assert {c.meta.company for c in cands} == {"Acme", "Nordwind", "Anywhere"}
    nord = next(c for c in cands if c.meta.company == "Nordwind")
    assert "pay not stated" in nord.flags and store.state()[nord.id]["searches"] == ["em-eu"]
    assert store.job(nord.id)["source"] == "Himalayas"
