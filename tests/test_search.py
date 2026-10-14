import jobspipe
import pytest

from jobpipe import config
from jobpipe.jobs_api import JobsClient, post_filter, salary_text, usd_range
from jobpipe.store import Store, canonical_url, safe_id
from conftest import FakeJobsAPI, jobspipe_client, make_job


def test_saved_searches_send_the_requested_filters():
    cfg = config.load()
    bodies = {s.id: s.request_body() for s in cfg.searches}
    assert set(bodies) == {"tpm-remote", "em-remote", "tpm-hybrid", "em-hybrid"}
    for sid, b in bodies.items():
        assert b["min_salary_usd"] == 200000
        assert b["job_country_code_or"] == ["US"]
        assert b["limit"] == 10
    assert "Technical Program Manager" in bodies["tpm-remote"]["job_title_or"]
    assert "Program Manager, Technical" in bodies["tpm-remote"]["job_title_or"]  # comma kept inside the phrase
    assert "Engineering Manager" in bodies["em-remote"]["job_title_or"]
    assert bodies["tpm-remote"]["remote"] is True and "work_arrangement_or" not in bodies["tpm-remote"]
    assert bodies["em-hybrid"]["work_arrangement_or"] == ["hybrid"]
    assert bodies["em-hybrid"]["job_location_or"] == ["Portland"]
    assert "remote" not in bodies["em-hybrid"]


def test_budget_caps_limit_and_ledger_counts_charged_credits(tmp_dirs):
    cfg = config.load()
    cfg.max_credits_per_run = 3
    fake = FakeJobsAPI({"Technical Program Manager": [make_job(f"j{i}") for i in range(10)]})
    store = Store()
    client = JobsClient(cfg, store, client=jobspipe_client(fake))
    res = client.run(cfg.search("tpm-remote"))
    assert fake.bodies[0]["limit"] == 3            # lowered to the remaining budget
    assert len(res.jobs) == 3 and res.credits_charged == 3
    assert store.credits_used("once") == 3
    res2 = client.run(cfg.search("em-remote"))    # budget spent: no call at all
    assert res2.skipped_reason and len(fake.bodies) == 1


def test_already_paid_jobs_are_free(tmp_dirs):
    cfg = config.load()
    fake = FakeJobsAPI({"Technical Program Manager": [make_job("a"), make_job("b")]})
    client = JobsClient(cfg, Store(), client=jobspipe_client(fake))
    client.run(cfg.search("tpm-remote"))
    client.run(cfg.search("tpm-remote"))
    assert client.budget.spent_this_run == 2


def test_bad_request_costs_one_credit_and_is_not_retried(tmp_dirs):
    cfg = config.load()
    fake = FakeJobsAPI(fail_400=True)
    store = Store()
    client = JobsClient(cfg, store, client=jobspipe_client(fake))
    with pytest.raises(jobspipe.BadRequestError):
        client.run(cfg.search("em-hybrid"))
    assert len(fake.bodies) == 1
    assert store.credits_used("once") == 1
    assert "unknown field" in store.credits()["calls"][-1]["status"]


def test_a_job_whose_id_is_its_page_address_is_kept_under_a_safe_id(tmp_dirs):
    """JobsPipe gives Himalayas postings their address as the id. Kept as it is, its slashes nest the job's
    file in folders named after the URL and break /api/jobs/<id>."""
    cfg = config.load()
    url = "https://himalayas.app/companies/acme/jobs/staff-tpm"
    fake = FakeJobsAPI({"Technical Program Manager": [make_job(url, url=url), make_job("a1b2")]})
    store = Store()
    res = JobsClient(cfg, store, client=jobspipe_client(fake)).run(cfg.search("tpm-remote"))
    new, kept = (j["id"] for j in res.jobs)
    assert kept == "a1b2" and new == safe_id(url) and len(new) == 16 and new.isalnum()
    assert store.job(new)["url"] == url and set(store.state()) == {new, kept}
    assert sorted(p.name for p in store.jobs_dir.iterdir()) == sorted([f"{new}.json", "a1b2.json"])


def test_post_filter():
    cfg = config.load()
    remote, hybrid = cfg.search("tpm-remote"), cfg.search("tpm-hybrid")
    assert post_filter(make_job(), remote, True) == (True, [])
    assert post_filter(make_job(remote=False), remote, True)[0] is False
    assert post_filter(make_job(remote=False, hybrid=False), hybrid, True)[0] is False
    assert post_filter(make_job(lo=100000, hi=150000), remote, True)[0] is False
    keep, flags = post_filter(make_job(lo=None, hi=None), remote, True)
    assert keep and flags == ["pay not stated"]
    assert post_filter(make_job(closed_at="2026-09-01"), remote, True)[0] is False


def test_canonical_url_drops_tracking():
    assert canonical_url("https://Boards.greenhouse.io/acme/jobs/1?gh_src=abc&utm_source=x#top") == \
        "https://boards.greenhouse.io/acme/jobs/1"


def test_jobspipe_address_comes_from_dns_over_https():
    import httpx
    from jobpipe import doh

    sent = []

    def api(request):
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    looked_up = []

    def resolve(host):
        looked_up.append(host)
        return "203.0.113.10", 60

    t = doh.DoHTransport({"api.jobspipe.dev"}, inner=httpx.MockTransport(api), resolve=resolve)
    c = httpx.Client(base_url="https://api.jobspipe.dev", transport=t)
    c.post("/v1/jobs/search", json={"limit": 1})
    c.post("/v1/jobs/search", json={"limit": 1})
    r = sent[0]
    assert r.url.host == "203.0.113.10" and r.url.path == "/v1/jobs/search"
    assert r.headers["host"] == "api.jobspipe.dev"                  # the API still sees its own name
    assert r.extensions["sni_hostname"] == "api.jobspipe.dev"       # and TLS checks the certificate for it
    assert looked_up == ["api.jobspipe.dev"]                        # the answer is reused
    httpx.Client(transport=t).get("https://example.com/")           # other hosts keep normal DNS
    assert sent[-1].url.host == "example.com" and "sni_hostname" not in sent[-1].extensions
    # no resolver answers: the request goes out with normal DNS, as before
    t = doh.DoHTransport({"api.jobspipe.dev"}, inner=httpx.MockTransport(api), resolve=lambda h: ("", 0))
    httpx.Client(transport=t).get("https://api.jobspipe.dev/x")
    assert sent[-1].url.host == "api.jobspipe.dev"


def test_dns_over_https_lookup_reads_the_first_resolver_that_answers():
    import httpx
    from jobpipe import doh

    def handler(request):
        assert request.url.params["name"] == "api.jobspipe.dev"
        if request.url.host == "1.1.1.1":
            return httpx.Response(503)
        return httpx.Response(200, json={"Answer": [
            {"type": 5, "data": "alias.jobspipe.dev."}, {"type": 1, "TTL": 60, "data": "203.0.113.10"}]})

    assert doh.lookup("api.jobspipe.dev", httpx.Client(transport=httpx.MockTransport(handler))) == ("203.0.113.10", 60)
    down = httpx.Client(transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down"))))
    assert doh.lookup("api.jobspipe.dev", down) == ("", 0)


def test_pay_stated_in_dollars_is_read_as_a_yearly_range():
    assert usd_range("$120K – $315K • Offers Equity • Multiple Ranges") == (120000, 315000)
    assert usd_range("$94,000 - $225,000") == (94000, 225000)
    assert usd_range("US$180k") == (180000, 180000)
    assert usd_range("$150K base + $5K signing bonus") == (150000, 150000)       # the bonus isn't pay
    for text in ("GBP 95k–120k", "€80K – €100K", "C$150k", "$45 – $60 / hour", "$8,000 a month", "Competitive", ""):
        assert usd_range(text) == (None, None), text
    assert salary_text({"salary_string": "$120K – $315K • Offers Equity"}) == "$120k–$315k"
    assert salary_text({"salary_string": "US$180k"}) == "$180k"
    assert salary_text({"salary_string": "GBP 95k–120k"}) == "GBP 95k–120k"
    assert salary_text({"min_annual_salary_usd": 200000, "max_annual_salary_usd": 200000}) == "$200k"
    assert salary_text({"min_annual_salary_usd": 280000, "max_annual_salary_usd": 360000, "salary_string": "$1 – $2"}) == "$280k–$360k"
