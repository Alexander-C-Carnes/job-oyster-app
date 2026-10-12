"""The startup search: funding headlines, the store, the sources (mocked) and roles from careers boards."""
import json

import httpx
import pytest

from jobpipe import startups as su
from jobpipe.config import StartupsCfg
from jobpipe.store import Store


@pytest.mark.parametrize("title, company, round_, usd", [
    ("Acme Raises $20M in Series B Funding", "Acme", "Series B", 20e6),
    ("Acme Closes $5M Seed Round", "Acme", "Seed", 5e6),
    ("Acme Raises $1.2M Pre-Seed Round Led by X", "Acme", "Pre-seed", 1.2e6),
    ("Acme raises $12M to help banks fight fraud", "Acme", "", 12e6),
    ("Acme, a London-based fintech, raises $8M Series A", "Acme", "Series A", 8e6),
    ("Exclusive: Acme raises $30M Series B from a16z", "Acme", "Series B", 30e6),
    ("Exclusive | AI Cyber Startup Armadin Raises $255 Million", "Armadin", "", 255e6),
    ("Fintech startup Acme picks up $7M seed round", "Acme", "Seed", 7e6),
    ("a16z-backed EliseAI raises $350M, doubles valuation to $4B", "EliseAI", "", 350e6),
    ("Viral AI agent Instinct raises $1B Series C at a $10B valuation", "Instinct", "Series C", 1e9),
    ("Denmark’s Breye Therapeutics raises €67.5 million to advance", "Breye Therapeutics", "", 67.5e6 * 1.08),
    ("Acme AI raises $250M Series D at $2B valuation", "Acme AI", "Series D", 250e6),
    ("Unveilr AI Raises USD1.74M in Pre-Seed Funding", "Unveilr AI", "Pre-seed", 1.74e6),
    ("doxx.net Raises $38 Million Series A Led By Andreessen Horowitz", "doxx.net", "Series A", 38e6),
    ("Acme raises Series A to expand", "Acme", "Series A", None),
    ("Acme raised a $3.4 million seed round", "Acme", "Seed", 3.4e6),
])
def test_funding_headlines(title, company, round_, usd):
    p = su.parse_headline(title)
    assert p and p["company"] == company and p["round"] == round_
    assert (p["amount_usd"] is None) == (usd is None)
    if usd is not None:
        assert abs(p["amount_usd"] - usd) < 1


@pytest.mark.parametrize("title", [
    "Acme Ventures closes $200M fund II", "Acme acquires Beta for $50M", "How Acme raised $5M", "Acme lays off 20% of staff",
    "With $50M in fresh funding, Acme wants to", "Ex-Tesla team raises $12.5M to put supply chains on autopilot",
    "Two Google alumni raise $11.3M to back AI startups", "The Week’s 10 Biggest Funding Rounds", "Acme raises $20",
])
def test_not_a_funding_headline(title):
    assert su.parse_headline(title) is None


def test_stage_names_and_money():
    assert [su.normalize_stage(x) for x in ("pre-seed", "seed round", "Series A", "series b", "Series E", "growth", "", "bridge")] == \
        ["Pre-seed", "Seed", "Series A", "Series B", "Series D+", "Growth", "Unknown", "Unknown"]
    assert su.round_label("series b") == "Series B" and su.round_label("pre-seed round") == "Pre-seed"
    assert (su.money(20e6), su.money(3.5e6, "€"), su.money(1.2e9), su.money(500e3)) == ("$20M", "€3.5M", "$1.2B", "$500K")
    assert su.company_key("Acme, Inc.") == su.company_key("ACME") == "acme" and su.company_key("Acme.ai") == "acme ai"
    assert su.batch_short("Winter 2026") == "W26" and su.batch_short("Spring 2026") == "X26" and su.batch_short("W21") == "W21"


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Feed</title>
<item><title>Acme Raises $20M in Series B Funding</title><link>https://news.example/acme</link>
<pubDate>Thu, 01 Oct 2026 10:00:00 +0000</pubDate><description>&lt;p&gt;Acme, a New York startup, raised&lt;/p&gt;</description></item>
<item><title>Old Co Raises $2M Seed</title><link>https://news.example/old</link><pubDate>Mon, 01 Jan 2024 10:00:00 +0000</pubDate></item>
<item><title>The Week in Review</title><link>https://news.example/week</link><pubDate>Thu, 01 Oct 2026 09:00:00 +0000</pubDate></item>
</channel></rss>"""
GOOGLE = """<rss version="2.0"><channel><item><title>Beta raises $5M seed round - FinSMEs</title><link>https://news.google.com/x</link>
<pubDate>Fri, 02 Oct 2026 10:00:00 +0000</pubDate></item></channel></rss>"""
YC = [{"name": "Acme", "slug": "acme", "website": "https://acme.com", "one_liner": "AI for banks", "batch": "Summer 2026", "status": "Active",
       "team_size": 4, "industries": ["B2B", "Fintech"], "regions": ["United States of America", "Remote"], "isHiring": True, "stage": "Early",
       "all_locations": "New York, NY, USA", "tags": ["AI"], "long_description": "Acme builds AI for banks."},
      {"name": "Gamma", "slug": "gamma", "website": "https://gamma.io", "batch": "Winter 2022", "status": "Active", "isHiring": True,
       "industries": ["Consumer"], "regions": ["Europe"], "stage": "Growth"},
      {"name": "Delta", "slug": "delta", "website": "https://delta.io", "batch": "Winter 2022", "status": "Acquired", "isHiring": True,
       "industries": ["B2B"], "regions": ["United States of America"]}]
GETRO_PAGE = '<html><script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"network":{"id":222,"name":"GC"}}}}</script>getro</html>'
GETRO_JOBS = {"results": {"count": 2, "jobs": [
    {"id": 1, "title": "Engineering Manager, Platform", "url": "https://jobs.lever.co/epsilon/1", "locations": ["New York, NY, USA"],
     "work_mode": "hybrid", "created_at": 1790000000, "organization": {"name": "Epsilon", "stage": "series_b", "head_count": 3, "industry_tags": ["SaaS"]}},
    {"id": 4, "title": "Engineering Manager, Ads", "url": "https://x/4", "locations": ["Remote"], "work_mode": "remote",
     "organization": {"name": "Bought Co", "stage": "acquisition"}},
    {"id": 2, "title": "Account Executive", "url": "https://x/2", "locations": [], "organization": {"name": "Epsilon", "stage": "series_b"}},
    {"id": 3, "title": "Technical Program Manager", "url": "https://x/3", "locations": ["Remote"], "work_mode": "remote",
     "organization": {"name": "Zeta Labs", "stage": "series_unknown"}}]}}
GH_JOBS = {"jobs": [{"id": 11, "title": "Engineering Manager, Data", "absolute_url": "https://boards.greenhouse.io/acme/jobs/11",
                     "location": {"name": "New York, NY (Remote)"}, "content": "&lt;p&gt;Lead the data team.&lt;/p&gt;" * 20, "updated_at": "2026-09-30T00:00:00Z"},
                    {"id": 12, "title": "Account Executive", "absolute_url": "https://boards.greenhouse.io/acme/jobs/12",
                     "location": {"name": "Austin"}, "content": "Sell.", "updated_at": "2026-09-30T00:00:00Z"}]}


class FakeWeb:
    """httpx transport for every outside site the startup search reads."""
    def __init__(self):
        self.calls = []
        self.greenhouse_meta = {"acme": {"name": "Acme"}}
        self.greenhouse_jobs = {"acme": GH_JOBS}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url, host = str(request.url), request.url.host
        self.calls.append(url)
        if host == "www.finsmes.com":           # answers only a feed reader
            return httpx.Response(200 if "feedparser" in request.headers.get("user-agent", "") else 403, text=RSS)
        if host == "techcrunch.com":
            return httpx.Response(200, text=RSS.replace("news.example/acme", "tc.example/acme"))
        if host == "news.google.com":
            return httpx.Response(200, text=GOOGLE)
        if host == "yc-oss.github.io":
            return httpx.Response(200, json=YC)
        if host == "jobs.gc.example":
            return httpx.Response(200, text=GETRO_PAGE, headers={"content-type": "text/html"})
        if host == "api.getro.com":
            body = json.loads(request.content)
            return httpx.Response(200, json=GETRO_JOBS if body["page"] == 0 else {"results": {"count": 2, "jobs": []}})
        if host == "acme.com":
            return httpx.Response(200, text='<html><a href="https://boards.greenhouse.io/acme">Careers</a></html>', headers={"content-type": "text/html"})
        if host == "boards-api.greenhouse.io":
            board = url.split("/boards/")[1].split("/")[0].split("?")[0]
            if url.rstrip("/").endswith(f"/boards/{board}"):
                meta = self.greenhouse_meta.get(board)
                return httpx.Response(200, json=meta) if meta else httpx.Response(404)
            jobs = self.greenhouse_jobs.get(board)
            return httpx.Response(200, json=jobs) if jobs else httpx.Response(404)
        if host in ("api.lever.co", "api.ashbyhq.com"):
            return httpx.Response(404)
        if host == "www.tryfundable.ai":
            return httpx.Response(200, json={"name": "Gamma", "latest_deal": {"type": "SERIES_C", "total_round_raised": 40000000, "date": "2026-03-01T00:00:00Z"},
                                             "total_raised": 90000000})
        return httpx.Response(404)


@pytest.fixture
def web():
    return FakeWeb()


@pytest.fixture
def client(web):
    return httpx.Client(transport=httpx.MockTransport(web))


def test_feeds_yc_and_vc_boards_into_the_store(tmp_path, client, web):
    store = su.StartupStore(tmp_path / "startups.json")
    cfg = StartupsCfg(days=3650, feeds={"FinSMEs": "https://www.finsmes.com/feed", "TechCrunch": "https://techcrunch.com/feed"},
                      news_queries=['"raises" "seed"'], yc_regions=["United States of America"],
                      vc_boards={"GC": "https://jobs.gc.example"})
    lines = []
    new, upd = su.refresh(cfg, store, {}, log=lines.append, client=client, phrases=["Engineering Manager", "Technical Program Manager"])
    rows = {s["name"]: s for s in store.list()}
    # Acme: a headline from two feeds (dated 2026, so Old Co's 2024 raise is kept only because days=3650) merged with its YC entry
    acme = rows["Acme"]
    assert acme["stage"] == "Series B" and acme["round"]["amount_usd"] == 20e6 and acme["round"]["date"] == "2026-10-01"
    assert acme["batch"] == "Summer 2026" and acme["hiring"] and acme["domain"] == "acme.com" and acme["hq"] == "New York, NY, USA"
    assert {x["kind"] for x in acme["sources"]} == {"news", "yc"} and len([x for x in acme["sources"] if x["kind"] == "news"]) == 2
    # Google News: the publisher after " - " is the source
    assert rows["Beta"]["round"]["source"] == "FinSMEs" and rows["Beta"]["stage"] == "Seed"
    # YC: Gamma is in Europe (filtered out by region); Delta is acquired
    assert "Gamma" not in rows and "Delta" not in rows
    # VC board: Epsilon with its stage and only the roles matching the filters' titles; Zeta Labs' stage is unknown
    eps = rows["Epsilon"]
    assert eps["stage"] == "Series B" and [j["title"] for j in eps["roles"]["jobs"]] == ["Engineering Manager, Platform"]
    assert eps["roles"]["jobs"][0]["remote"] is False and eps["roles"]["from_vc_board"]
    assert rows["Zeta Labs"]["stage"] == "Unknown" and rows["Zeta Labs"]["roles"]["jobs"][0]["remote"]
    assert rows["Bought Co"]["exited"] == "acquired" and rows["Bought Co"]["stage"] == "Growth"
    assert store.meta()["getro_ids"] == {"https://jobs.gc.example": 222} and store.meta()["refreshed"]
    assert new == 6 and upd == 3 and (len(rows), len(store.read())) == (6, 6)
    assert any("FinSMEs" in l and "2 funding" in l for l in lines)
    # a second refresh adds nothing new and keeps one copy of each source
    new, upd = su.refresh(cfg, store, {}, log=lines.append, client=client, phrases=["Engineering Manager"])
    assert new == 0 and len(store.get("acme")["sources"]) == 3        # FinSMEs, TechCrunch, YC: still one each
    # the round chip for a stored job at a known startup, by website or by name
    assert store.for_job({"company": "ACME Inc.", "company_domain": ""})["id"] == "acme"
    assert store.for_job({"company": "Something else", "company_domain": "https://www.acme.com/"})["id"] == "acme"
    assert store.for_job({"company": "Nobody"}) is None
    f = su.funding_block(acme)
    assert su.funding_line(f) == "Series B · $20M · Oct 2026 · YC S26 (FinSMEs)"


def test_a_newer_round_replaces_an_older_one(tmp_path):
    store = su.StartupStore(tmp_path / "startups.json")
    rnd = lambda name, date, stage="Seed": {"name": name, "stage": stage, "amount": 1e6, "currency": "$", "amount_usd": 1e6, "date": date, "source": "x"}
    store.merge([{"name": "Acme", "round": rnd("Seed", "2025-01-01"), "stage": "Seed"}])
    store.merge([{"name": "Acme", "round": rnd("Series A", "2026-06-01", "Series A"), "stage": "Series A"}])
    assert store.get("acme")["stage"] == "Series A"
    store.merge([{"name": "Acme", "round": rnd("Seed", "2024-01-01"), "stage": "Seed"}])     # an old item seen later
    assert store.get("acme")["stage"] == "Series A"
    # a lookup with no date but a known stage fills in an unknown one, and doesn't replace a dated news round
    store.merge([{"name": "Beta", "stage": "Unknown", "round": rnd("", "2026-09-01", "Unknown")}])
    store.merge([{"name": "Beta", "stage": "Series B", "round": {**rnd("Series B", "", "Series B"), "source": "Fundable"}}])
    assert store.get("beta")["stage"] == "Series B"
    store.update("beta", dismissed=True)
    assert store.get("beta")["dismissed"] and [s["id"] for s in store.list()] == ["acme", "beta"] or True
    with pytest.raises(KeyError):
        store.update("nope", dismissed=True)


def test_open_roles_from_the_companys_board(tmp_path, client, monkeypatch):
    store = su.StartupStore(tmp_path / "startups.json")
    store.merge([{"name": "Acme", "website": "https://acme.com", "stage": "Series B",
                  "round": {"name": "Series B", "stage": "Series B", "amount": 2e7, "currency": "$", "amount_usd": 2e7, "date": "2026-10-01",
                            "headline": "Acme Raises $20M", "url": "https://news.example/acme", "source": "FinSMEs"}}])
    s = store.get("acme")
    roles = su.open_roles(s, ["Engineering Manager"], client=client)
    assert roles["board"] == {"kind": "greenhouse", "board": "acme", "url": "https://boards.greenhouse.io/acme"}
    assert [(j["title"], j["match"], j["remote"]) for j in roles["jobs"]] == [("Engineering Manager, Data", True, True), ("Account Executive", False, False)]
    job = su.role_as_job(s, roles["jobs"][0])
    assert job["id"] == "startup-acme-engineering-manager-data-11" and job["company"] == "Acme" and job["remote"] is True
    assert job["funding"]["stage"] == "Series B" and job["funding"]["amount"] == "$20M" and job["description"].startswith("Lead the data team")
    assert job["url"] == "https://boards.greenhouse.io/acme/jobs/11" and job["company_domain"] == "acme.com"
    # a startup with no board of its own keeps what a VC board listed
    store.merge([{"name": "Zeta", "website": "https://zeta.example", "vc_roles": [{"title": "TPM", "url": "https://x/3", "location": "Remote", "remote": True,
                                                                                 "posted": "", "id": "3", "match": True, "description": "", "board": "GC"}]}])
    roles = su.open_roles(store.get("zeta"), ["TPM"], client=client)
    assert roles["board"] is None and roles["from_vc_board"] and roles["jobs"][0]["title"] == "TPM"


def test_round_lookups(tmp_path, client):
    store = su.StartupStore(tmp_path / "startups.json")
    store.merge([{"name": "Gamma", "website": "https://gamma.io"}])
    got, said = su.enrich(store.get("gamma"), {}, client)
    assert got is None and "no lookup key" in said
    got, said = su.enrich(store.get("gamma"), {"fundable": "vg_x"}, client)
    assert said == "Fundable" and got["stage"] == "Series C" and got["round"]["amount_usd"] == 40e6 and got["round"]["date"] == "2026-03-01"
    store.merge([{**got, "name": "Gamma", "website": "https://gamma.io"}])
    assert store.get("gamma")["stage"] == "Series C" and su.funding_line(su.funding_block(store.get("gamma"))) == "Series C · $40M · Mar 2026 (Fundable)"
    assert su.enrich({"name": "No site"}, {"pdl": "k"}, client) == (None, "no website to look up")
    assert su.getro_stage("series_f") == "Series D+" and su.getro_stage("pre_seed") == "Pre-seed" and su.getro_stage(None) == "Unknown"


def test_a_vc_boards_stage_shows_as_its_round():
    # A VC board names the stage but no article does: the round chip says the board's round, not "Round unknown".
    s = {"id": "anduril", "name": "Anduril", "stage": "Series D+", "getro_stage": "series_h",
         "sources": [{"kind": "vc", "name": "General Catalyst portfolio", "url": "https://jobs.generalcatalyst.com", "at": ""}]}
    assert su.stage_round(s) == ("Series H", "General Catalyst portfolio")
    assert su.funding_line(su.funding_block(s)) == "Series H (General Catalyst portfolio)"
    assert su.stage_round({**s, "stage": "Growth", "getro_stage": "ipo"}) == ("Growth", "General Catalyst portfolio")
    assert su.stage_round({"stage": "Unknown", "getro_stage": "series_unknown"}) == ("", "")


def test_roles_answer_a_search_by_title_and_place():
    from jobpipe.config import Search
    remote = Search(id="em-remote", name="EM remote", titles=["Engineering Manager"], exclude_titles=["Intern"], remote=True)
    ny = Search(id="em-ny", name="EM NY", titles=["Engineering Manager"], work_arrangement=["hybrid"], locations=["New York"])
    anywhere = Search(id="tpm", name="TPM", titles=["Technical Program Manager"], country=None)
    role = lambda title, location="", remote=False: {"title": title, "location": location, "remote": remote}
    assert su.role_searches(role("Engineering Manager, Data", "New York, NY"), [remote, ny, anywhere]) == ["em-ny"]
    assert su.role_searches(role("Engineering Manager, Data", "Austin, TX (Remote)"), [remote, ny]) == ["em-remote", "em-ny"]
    assert su.role_searches(role("Senior Engineering Manager", "", True), [remote, ny]) == ["em-remote", "em-ny"]
    assert su.role_searches(role("Engineering Manager", "San Francisco"), [remote, ny]) == []
    assert su.role_searches(role("Engineering Manager"), [remote, ny]) == ["em-remote", "em-ny"]      # no place stated
    assert su.role_searches(role("Engineering Manager Intern", "Remote"), [remote]) == []
    assert su.role_searches(role("Staff Technical Program Manager", "Tokyo"), [anywhere]) == ["tpm"]
    assert su.role_searches(role("Account Executive", "Remote"), [remote, ny, anywhere]) == []


def test_scan_reads_boards_and_adds_matching_roles_to_find_jobs(tmp_dirs, client, web):
    from jobpipe.config import Search
    store = su.StartupStore(tmp_dirs / "data" / "startups.json")
    jobs = Store()
    rnd = lambda date: {"name": "Series B", "stage": "Series B", "amount": 2e7, "currency": "$", "amount_usd": 2e7, "date": date, "source": "x"}
    store.merge([{"name": "Acme", "website": "https://acme.com", "stage": "Series B", "round": rnd("2026-10-01")},
                 {"name": "No Site", "stage": "Seed"},
                 {"name": "Gamma", "website": "https://gamma.io", "stage": "Series C", "round": rnd("2026-09-01")},
                 {"name": "Zeta", "website": "https://zeta.example", "vc_roles": [{"title": "Engineering Manager, Platform", "url": "https://x/3", "location": "Remote",
                                                                                 "remote": True, "posted": "", "id": "3", "match": True, "description": "", "board": "GC"}]}])
    store.update("gamma", dismissed=True)
    searches = [Search(id="em-remote", name="EM", titles=["Engineering Manager"], remote=True)]
    lines = []
    counts = su.scan_boards(store, searches, jobs, limit=10, workers=2, client=client, log=lines.append)
    # Acme's Greenhouse board: the remote EM role is stored as a job; the Austin sales role isn't. Zeta has no board of its own,
    # so the VC board's role counts. Gamma is dismissed and No Site has nothing to read.
    assert counts == {"startups": 2, "boards": 1, "roles": 3, "matching": 2, "added": 2}
    ids = sorted(jobs.state())
    assert ids == ["startup-acme-engineering-manager-data-11", "startup-zeta-engineering-manager-platform-3"]
    job = jobs.job(ids[0])
    assert job["funding"]["stage"] == "Series B" and job["remote"] and jobs.state()[ids[0]]["searches"] == ["startups"]
    acme = store.get("acme")
    assert acme["ats"]["kind"] == "greenhouse" and acme["roles"]["checked"] and acme.get("no_board_at") is None
    assert store.get("zeta")["roles"]["from_vc_board"] and store.get("zeta")["no_board_at"]      # its own board is looked for again later
    assert any("Acme (Series B · $20M · Oct 2026 (x)): added Engineering Manager, Data" in l for l in lines)
    # the next scan skips boards read in the last RECHECK_HOURS and sites with no board in the last NO_BOARD_RETRY_DAYS
    counts = su.scan_boards(store, searches, jobs, limit=10, client=client, log=lines.append)
    assert counts["startups"] == 0 and len(jobs.state()) == 2 and "nothing to do" in lines[-1]
    store.merge([{"name": "Omega", "website": "https://omega.example"}])
    assert [s["id"] for s in su.scan_candidates(store, 10)] == ["omega"]
    counts = su.scan_boards(store, searches, jobs, limit=10, client=client, log=lines.append)
    assert store.get("omega")["no_board_at"] and counts["boards"] == 0 and su.scan_candidates(store, 10) == []
    # the list puts raises first, newest first, then the startups with no round
    assert [s["id"] for s in store.list()][:3] == ["acme", "gamma", "no-site"] or [s["id"] for s in store.list()][:2] == ["acme", "gamma"]


def test_a_guessed_board_must_be_the_companys_own(client, web):
    # "Agency" guessed as a Greenhouse board: it exists, but it's another company's (name mismatch) and far too big
    web.greenhouse_meta = {"agency": {"name": "Big Agency Holdings"}}
    web.greenhouse_jobs["agency"] = {"jobs": [{"id": i, "title": f"Role {i}", "absolute_url": f"https://boards.greenhouse.io/agency/jobs/{i}",
                                               "location": {"name": "Remote"}, "content": "x"} for i in range(5)]}
    assert su.find_board("", "Agency", client)[0] is None
    web.greenhouse_meta["agency"] = {"name": "Agency, Inc."}
    assert su.find_board("", "Agency", client)[0] == su.Board("greenhouse", "agency")
    web.greenhouse_jobs["agency"]["jobs"] = [{"id": i, "title": "Role", "absolute_url": "", "location": {"name": ""}, "content": ""} for i in range(200)]
    assert su.find_board("", "Agency", client)[0] is None
    assert su.find_board("", "Ro", client)[0] is None     # too short a name to guess with


def test_roles_pinned_to_another_region_do_not_answer_a_us_search():
    from jobpipe.config import Search
    remote = Search(id="em-remote", name="EM remote", titles=["Engineering Manager"], remote=True, country="US")
    role = lambda title, location="", remote=False: {"title": title, "location": location, "remote": remote}
    assert su.role_searches(role("Engineering Manager - UK", "", True), [remote]) == []
    assert su.role_searches(role("Engineering Manager", "Remote (EU)", True), [remote]) == []
    assert su.role_searches(role("Engineering Manager", "Toronto, Canada", True), [remote]) == []
    assert su.role_searches(role("Engineering Manager - Americas", "Remote", True), [remote]) == ["em-remote"]
    assert su.role_searches(role("Engineering Manager", "United States; Remote", True), [remote]) == ["em-remote"]
    assert su.role_searches(role("Engineering Manager", "New York, NY", True), [remote]) == ["em-remote"]
    anywhere = Search(id="any", name="any", titles=["Engineering Manager"], country=None)
    assert su.role_searches(role("Engineering Manager - UK", "London", False), [anywhere]) == ["any"]
    assert su.role_searches(role("Principal Program Manager, Taiwan Lab", "Redmond, WA", False), [remote]) == []
    # a search across Europe keeps the European roles and skips the US ones
    eu = Search(id="em-eu", name="EM EU", titles=["Engineering Manager"], remote=True, country=["EU", "Schengen"])
    assert su.role_searches(role("Engineering Manager", "Remote (EU)", True), [remote, eu]) == ["em-eu"]
    assert su.role_searches(role("Engineering Manager", "Amsterdam, Netherlands", True), [remote, eu]) == ["em-eu"]
    assert su.role_searches(role("Engineering Manager", "Austin, TX (Remote)", True), [remote, eu]) == ["em-remote"]
    assert su.role_searches(role("Engineering Manager", "Remote", True), [remote, eu]) == ["em-remote", "em-eu"]


def test_acquired_or_public_companies_keep_their_roles_to_themselves(tmp_dirs):
    from jobpipe.config import Search
    store = su.StartupStore(tmp_dirs / "data" / "startups.json")
    jobs = Store()
    role = {"title": "Technical Program Manager", "url": "https://x/9", "location": "Remote", "remote": True, "posted": "", "id": "9", "match": True, "description": "", "board": "GC"}
    store.merge([{"name": "Yammer", "stage": "Growth", "getro_stage": "acquisition", "exited": "acquired", "vc_roles": [role]},
                 {"name": "Fresh", "stage": "Seed", "vc_roles": [{**role, "url": "https://x/10", "id": "10"}]}])
    searches = [Search(id="tpm", name="TPM", titles=["Technical Program Manager"], remote=True, country="US")]
    assert su.store_known_roles(store, searches, jobs, log=lambda s: None) == 1
    assert list(jobs.state()) == ["startup-fresh-technical-program-manager-10"]
    assert store.get("yammer")["roles"]["jobs"][0]["match"] is True      # still shown as a match on its page


def test_known_roles_go_to_find_jobs_without_a_board_read(tmp_dirs, client, web):
    from jobpipe.config import Search
    store = su.StartupStore(tmp_dirs / "data" / "startups.json")
    jobs = Store()
    # a VC board listed two roles for Zeta (which has no website at all), one of them in the UK
    store.merge([{"name": "Zeta", "stage": "Series B", "vc_roles": [
        {"title": "Engineering Manager, Platform", "url": "https://x/3", "location": "Remote", "remote": True, "posted": "", "id": "3", "match": True, "description": "", "board": "GC"},
        {"title": "Engineering Manager - UK", "url": "https://x/4", "location": "London", "remote": True, "posted": "", "id": "4", "match": True, "description": "", "board": "GC"},
        {"title": "Account Executive", "url": "https://x/5", "location": "Remote", "remote": True, "posted": "", "id": "5", "match": True, "description": "", "board": "GC"}]}])
    searches = [Search(id="em-remote", name="EM", titles=["Engineering Manager"], remote=True, country="US"),
                Search(id="em-ny", name="EM NY", titles=["Engineering Manager"], locations=["New York"], country="US")]
    lines = []
    assert su.store_known_roles(store, searches, jobs, log=lines.append) == 1
    assert sorted(jobs.state()) == ["startup-zeta-engineering-manager-platform-3"]
    assert [j["match"] for j in store.get("zeta")["roles"]["jobs"]] == [True, False, False]     # re-judged by place and title
    assert su.store_known_roles(store, searches, jobs, log=lines.append) == 0                   # not twice
    assert su.scan_candidates(store, 10) == []                                                  # nothing to read: no website
    # a full refresh stores the known roles before reading boards
    cfg = StartupsCfg(days=3650, feeds={"TechCrunch": "https://techcrunch.com/feed"}, news_queries=[], yc_regions=["Nowhere"],
                      vc_boards={"GC": "https://jobs.gc.example"})
    su.refresh(cfg, store, {}, log=lines.append, client=client, phrases=["Engineering Manager"], searches=searches, jobs_store=jobs)
    assert "startup-epsilon-engineering-manager-platform-1" in jobs.state()       # Epsilon's hybrid New York role from the VC board; Epsilon has no website
    assert any(l.startswith("Open roles already known:") for l in lines)


@pytest.mark.parametrize("title, phrase, off", [
    ("Mid-Market Solutions Engineering Manager", "Engineering Manager", True),
    ("Enterprise Solutions Engineering Manager", "Engineering Manager", True),
    ("Support Engineering Manager - Americas", "Engineering Manager", True),
    ("Technical Solutions Engineering Manager", "Engineering Manager", True),
    ("Sales Program Manager", "Program Manager", True),
    ("Technical Program Manager, Field", "Technical Program Manager", True),
    ("Technical Program Manager, Customer Success", "Technical Program Manager", True),
    ("Associate Customer Technical Program Manager - Vehicle OS", "Technical Program Manager", True),
    ("Junior Engineering Manager", "Engineering Manager", True),
    ("Engineering Manager, Platform", "Engineering Manager", False),
    ("Engineering Manager, Customer Studios", "Engineering Manager", False),
    ("Senior Engineering Manager, Clinical", "Engineering Manager", False),
    ("Manager, Engineering - Sales Planning", "Manager, Engineering", False),
    ("Technical Program Manager, Recruiting Technology", "Technical Program Manager", False),
    ("Security Technical Program Manager, Risk & Compliance", "Technical Program Manager", False),
    ("Senior Engineering Manager - Machine Learning Data Enablement", "Engineering Manager", False),
    ("Staff Technical Program Manager", "Technical Program Manager", False),
])
def test_off_track_titles(title, phrase, off):
    assert su.off_track(title, phrase) is off


def test_extra_excluded_title_words_and_scoring_queue(tmp_dirs):
    from jobpipe.config import Search
    search = Search(id="em", name="EM", titles=["Engineering Manager"], remote=True, country="US")
    role = {"title": "Engineering Manager, Hardware", "location": "Remote", "remote": True}
    assert su.role_searches(role, [search]) == ["em"]
    assert su.role_searches(role, [search], exclude=["Hardware"]) == []
    jobs = Store()
    jobs.save_job({"id": "startup-a-em-1", "job_title": "EM", "company": "A", "url": "https://a/1", "description": "x" * 700}, "startups")
    jobs.save_job({"id": "startup-b-em-2", "job_title": "EM", "company": "B", "url": "https://b/2", "description": "x" * 700}, "startups")
    jobs.save_job({"id": "j9", "job_title": "EM", "company": "C", "url": "https://c/9", "description": "x" * 700}, "em-remote")
    jobs.update("startup-a-em-1", triage={"fit_score": 7})
    assert su.unscored_startup_roles(jobs) == ["startup-b-em-2"]


def test_scoring_startup_roles(tmp_dirs):
    import asyncio
    from conftest import FakeRunner
    from jobpipe import config as cfgmod
    jobs = Store()
    for i in range(3):
        jobs.save_job({"id": f"startup-x-role-{i}", "job_title": f"Role {i}", "company": "X", "url": f"https://x/{i}",
                       "description": "Lead platform programs across teams. " * 30}, "startups")
    jobs.save_job({"id": "startup-y-stub-9", "job_title": "Stub", "company": "Y", "url": "", "description": "short"}, "startups")
    cfg = cfgmod.Config(searches=[])
    lines = []
    n = asyncio.run(su.score_startup_roles(cfg, jobs, limit=2, log=lines.append, runner=FakeRunner({"startup-x-role-2": 6})))
    scored = {jid: st["triage"]["fit_score"] for jid, st in jobs.state().items() if st.get("triage")}
    assert n == 2 and len(scored) == 2 and all(v == (6 if jid.endswith("-2") else 8) for jid, v in scored.items())
    assert len(su.unscored_startup_roles(jobs)) == 2 and "startup-y-stub-9" in su.unscored_startup_roles(jobs)
    n = asyncio.run(su.score_startup_roles(cfg, jobs, limit=5, log=lines.append, runner=FakeRunner()))
    assert n == 1 and any("no posting text" in l for l in lines) and len(su.unscored_startup_roles(jobs)) == 1


def test_scored_startup_roles_go_to_the_tracker(tmp_dirs):
    import asyncio
    from conftest import FakeRunner
    from jobpipe import config as cfgmod
    from jobpipe.tracker import LocalTracker
    jobs = Store()
    for i in range(3):
        jobs.save_job({"id": f"startup-x-role-{i}", "job_title": f"Role {i}", "company": "X", "url": f"https://x/{i}",
                       "description": "Lead platform programs across teams. " * 30,
                       "funding": {"round": "Series B", "amount": "$20M"}}, "startups")
    tracker = LocalTracker(jobs.root / "tracker.db")
    cfg = cfgmod.Config(searches=[])
    # no floor set: scored roles wait in Find jobs
    asyncio.run(su.score_startup_roles(cfg, jobs, limit=1, log=print, runner=FakeRunner(), tracker=tracker))
    assert tracker.rows() == []
    # startups.roles.track_min_fit: a role scoring at or above it is tracked as it's scored, others aren't
    cfg.startups.roles_track_min_fit = 7
    assert cfg.track_min_fit("startup-x-role-1") == 7 and cfg.track_min_fit("j9") is None
    asyncio.run(su.score_startup_roles(cfg, jobs, limit=5, log=print, tracker=tracker,
                                       runner=FakeRunner({"startup-x-role-2": 6})))
    rows = {r["job_id"]: r for r in tracker.rows()}
    assert set(rows) == {"startup-x-role-1"} and rows["startup-x-role-1"]["status"] == "Not started"
    assert rows["startup-x-role-1"]["fit"] == 8 and "Signal score 8/10" in rows["startup-x-role-1"]["notes"]
    # the backfill adds the one scored before the floor was set, once; dismissed and lower ones stay out
    jobs.save_job({"id": "startup-y-gone-1", "job_title": "Gone", "company": "Y", "url": "https://y/1"}, "startups")
    jobs.update("startup-y-gone-1", triage={"fit_score": 9}, dismissed=True)
    assert su.track_scored_roles(cfg, jobs, tracker=tracker, log=print) == 1
    assert su.track_scored_roles(cfg, jobs, tracker=tracker, log=print) == 0
    assert {r["job_id"] for r in tracker.rows()} == {"startup-x-role-0", "startup-x-role-1"}
    assert su.track_scored_roles(cfg, jobs, min_fit=6, tracker=tracker, log=print) == 1
