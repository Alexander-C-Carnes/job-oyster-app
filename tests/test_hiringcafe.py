import argparse
import ast
import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from jobpipe import cli, config
from jobpipe.config import HiringCafeSearch, Search
from jobpipe.hiringcafe import (ORIGIN, HiringCafe, LinkError, Page, Unreadable, describe, links, listing_url,
                                page_props, search_url, to_job)
from jobpipe.jobs_api import JobsClient
from jobpipe.pipeline import Pipeline
from jobpipe.store import Store
from jobpipe.tracker import LocalTracker
from jobpipe.web import searches_file
from jobpipe.web.runs import RunManager
from jobpipe.web.server import create_app
from conftest import FakeJobsAPI, FakeRunner, jobspipe_client, make_job

# HiringCafe's pages as recorded on 2026-10-17, with every value made up (the search, its results, the listing).
FIX = Path(__file__).parent / "fixtures" / "hiringcafe"
STATE = json.loads((FIX / "state.json").read_text())
SHARE = "https://hiringcafe.com/s/Ab12Cd34"
TOKEN = "test-token"
H = {"X-Jobpipe-Token": TOKEN}


ACME_POSTING = "<p>About the role</p><ul><li>Run Acme Robotics' release trains.</li></ul>" + "<p>More about Acme.</p>" * 40


def listing_page(job: dict) -> str:
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps({"props": {"pageProps": {"job": job}}})}</script>'


class FakeCafe:
    """HiringCafe's pages by address; `blocked` answers as its bot check does."""

    def __init__(self, blocked=False):
        self.blocked = blocked
        self.asked: list[str] = []
        self.pages = {search_url(STATE, 0): (FIX / "search-page-0.html").read_text(),
                      search_url(STATE, 1): (FIX / "search-page-1.html").read_text(),
                      f"{ORIGIN}/job/rq0004example": (FIX / "listing.html").read_text(),
                      f"{ORIGIN}/job/rq0001example": listing_page({"job_information": {"description": ACME_POSTING}})}

    def __call__(self, url: str, follow: bool = True) -> Page:
        self.asked.append(url)
        if self.blocked:
            return Page(403, "<title>Just a moment...</title>", challenged=True)
        if url == f"{ORIGIN}/s/Ab12Cd34" and not follow:     # a share link leads to the search page it names
            return Page(307, location=search_url(STATE).removeprefix(ORIGIN))
        if url in self.pages:
            return Page(200, self.pages[url])
        return Page(404, "Not found")


def cafe(fake=None, **kw) -> HiringCafe:
    return HiringCafe(fetch=fake or FakeCafe(), pause=0, **kw)


def saved(sid="hc-tpm", paused=False) -> HiringCafeSearch:
    return HiringCafeSearch(id=sid, name="TPM on HiringCafe", link=SHARE, search_state=STATE, paused=paused)


def test_a_share_link_is_read_once_for_its_search_and_a_search_link_needs_no_request():
    fake = FakeCafe()
    hc = cafe(fake)
    assert hc.resolve(SHARE) == STATE and hc.resolve("https://hiring.cafe/s/Ab12Cd34/") == STATE
    assert fake.asked == [f"{ORIGIN}/s/Ab12Cd34"] * 2           # hiring.cafe's link asked of hiringcafe.com
    fake.asked.clear()
    assert hc.resolve(search_url(STATE, 0).replace("/classic", "/")) == STATE and fake.asked == []
    with pytest.raises(LinkError, match="isn't a HiringCafe link"):
        hc.resolve("https://example.com/s/Ab12Cd34")
    with pytest.raises(LinkError, match="doesn't know that share link"):
        hc.resolve("https://hiringcafe.com/s/Unknown1")
    with pytest.raises(LinkError, match="share link"):
        hc.resolve("https://hiringcafe.com/jobs")
    with pytest.raises(Unreadable, match="bot check"):
        cafe(FakeCafe(blocked=True)).resolve(SHARE)


def test_a_search_reads_each_page_and_each_results_listing_for_its_posting():
    fake = FakeCafe()
    jobs = cafe(fake).search(saved())
    assert fake.asked == [search_url(STATE, 0), search_url(STATE, 1),
                          *(f"{ORIGIN}/job/rq000{n}example" for n in (1, 2, 4, 5))]      # not expired Globex's
    assert [j["company"] for j in jobs] == ["Acme Robotics", "Northwind Data", "Initech", "Acme"]   # Globex's expired
    acme = jobs[0]
    assert acme["url"] == acme["apply_url"] == "https://job-boards.greenhouse.io/acmerobotics/jobs/4001"
    assert acme["source"] == "HiringCafe" and acme["listing_url"] == f"{ORIGIN}/job/rq0001example"
    assert (acme["remote"], acme["hybrid"], acme["country_codes"], acme["date_posted"]) == (True, False, ["US"], "2026-10-15")
    assert (acme["salary_string"], acme["min_annual_salary_usd"], acme["max_annual_salary_usd"]) == ("USD 180k–220k", 180000, 220000)
    assert "Run Acme Robotics' release trains." in acme["description"] and "<li>" not in acme["description"]
    north = jobs[1]         # its listing couldn't be read: no posting text, never the result's short summary
    assert north["description"] == ""
    assert north["hybrid"] and not north["remote"] and north["salary_string"] == "" and "max_annual_salary_usd" not in north
    initech = jobs[2]       # its listing page gave the apply link and the whole posting
    assert initech["url"] == "https://initech.example.com/jobs/4" and initech["salary_string"] == "USD 150k"
    assert "Run Initech's platform programs" in initech["description"] and "<li>" not in initech["description"]
    assert len(cafe(max_pages=1).search(saved())) == 2
    fake.asked.clear()      # a result the app has the posting for isn't read again; one with no apply link is
    cafe(fake).search(saved(), lambda job: False)
    assert fake.asked[2:] == [f"{ORIGIN}/job/rq0004example"]


def test_a_result_whose_listing_cant_be_read_keeps_its_hiringcafe_page():
    fake = FakeCafe()
    del fake.pages[f"{ORIGIN}/job/rq0004example"]
    initech = next(j for j in cafe(fake).search(saved()) if j["company"] == "Initech")
    assert initech["url"] == listing_url({"requisition_id": "rq0004example"}) and initech["apply_url"] == ""


def test_describe_says_what_a_search_asks_for():
    assert describe(STATE) == ('"Technical Program Manager" · Remote, Hybrid · United States · Full Time · '
                               "posted in the last 14 days")
    assert describe({}) == "Any title"


def test_a_run_joins_a_result_to_the_jobspipe_job_at_the_same_posting(tmp_dirs):
    cfg = config.load()
    store = Store()
    tpm = Search(id="tpm", name="TPM", titles=["Technical Program Manager"], remote=True)
    fake_api = FakeJobsAPI({"Technical Program Manager": [make_job("jp1")]})
    p = Pipeline(cfg, store=store, jobs=JobsClient(cfg, store, client=jobspipe_client(fake_api)), runner=FakeRunner({}),
                 use_notion=False, log=lambda s: None, fetch_pages=False, hiringcafe=cafe())
    cands, _ = p.search([tpm], [saved()])
    assert sorted(c.meta.company for c in cands) == ["Acme", "Acme Robotics", "Initech", "Northwind Data"]
    jp1 = store.job("jp1")       # one job: JobsPipe's, which HiringCafe's search found as well
    assert store.state()["jp1"]["searches"] == ["tpm", "hc-tpm"] and "source" not in jp1
    assert len(store.state()) == 4 and next(c for c in cands if c.id == "jp1").search_id == "tpm"
    check = store.hiringcafe_checks()["hc-tpm"]
    assert (check["found"], check["new"]) == (4, 3) and check["at"]

    cands, _ = p.search([], [saved()])                   # the next run: nothing new
    assert len(cands) == 4 and store.hiringcafe_checks()["hc-tpm"]["new"] == 0 and len(store.state()) == 4


def test_a_run_fills_in_a_stored_job_that_only_had_hiringcafes_summary(tmp_dirs):
    store = Store()
    acme, north = map(to_job, page_props((FIX / "search-page-0.html").read_text())["ssrHits"][:2])
    store.save_job({**acme, "description": "Lead technical programs at Acme Robotics."}, "hc-tpm")
    store.save_job({**north, "description": "Pasted by you. " * 30, "description_pasted": "2026-10-20"}, "hc-tpm")
    p = Pipeline(config.load(), store=store, jobs=JobsClient(config.load(), store, client=jobspipe_client(FakeJobsAPI({}))),
                 runner=FakeRunner({}), use_notion=False, log=lambda s: None, fetch_pages=False, hiringcafe=cafe())
    p.search([], [saved()])
    assert "Run Acme Robotics' release trains." in store.job(acme["id"])["description"]
    assert store.job(north["id"])["description"] == "Pasted by you. " * 30


def test_a_run_drops_hiringcafe_results_at_the_defaults_excluded_companies(tmp_dirs):
    cfg = config.load()
    cfg.defaults = {**cfg.defaults, "exclude_companies": ["Initech"]}
    p = Pipeline(cfg, store=Store(), jobs=JobsClient(cfg, Store(), client=jobspipe_client(FakeJobsAPI({}))),
                 runner=FakeRunner({}), use_notion=False, log=lambda s: None, fetch_pages=False, hiringcafe=cafe())
    cands, skipped = p.search([], [saved()])
    assert "Initech" not in [c.meta.company for c in cands] and any("Initech: excluded company" in s for s in skipped)


def test_a_search_the_bot_check_turns_away_is_recorded_and_the_run_goes_on(tmp_dirs):
    cfg = config.load()
    store = Store()
    lines = []
    p = Pipeline(cfg, store=store, jobs=JobsClient(cfg, store, client=jobspipe_client(FakeJobsAPI({}))),
                 runner=FakeRunner({}), use_notion=False, log=lines.append, fetch_pages=False,
                 hiringcafe=cafe(FakeCafe(blocked=True)))
    cands, _ = p.search([], [saved()])
    assert cands == [] and "bot check" in store.hiringcafe_checks()["hc-tpm"]["error"]
    assert any("HiringCafe search not read" in line for line in lines)


def test_a_run_checks_every_search_not_paused_unless_it_names_some():
    cfg = config.Config(searches=[Search(id="tpm", name="TPM", titles=["TPM"])],
                        hiringcafe=[saved("hc-a"), saved("hc-b", paused=True)])
    ns = lambda **kw: argparse.Namespace(**{"search": None, "search_json": None, "title": None, "hiringcafe": None, **kw})
    assert [s.id for s in cli._hiringcafe(cfg, ns())] == ["hc-a"]
    assert [s.id for s in cli._hiringcafe(cfg, ns(search=["tpm"]))] == ["hc-a"]
    assert [s.id for s in cli._hiringcafe(cfg, ns(hiringcafe=["hc-b"]))] == ["hc-b"]
    assert cli._searches(cfg, ns(hiringcafe=["hc-b"])) == []          # those alone, no JobsPipe filters
    assert cli._hiringcafe(cfg, ns(search_json="{}")) == [] and cli._hiringcafe(cfg, ns(title=["x"])) == []
    with pytest.raises(KeyError):
        cfg.hiringcafe_search("nope")


def test_searches_yaml_keeps_the_link_and_the_decoded_search(tmp_path):
    path = tmp_path / "searches.yaml"
    shutil.copy(config.EXAMPLE_PROFILE / "searches.yaml", path)
    assert config.load(path).hiringcafe == [] and searches_file.load_hiringcafe(path) == []
    one = searches_file.clean_hiringcafe({"id": "hc-tpm", "name": " TPM  remote ", "link": SHARE, "search_state": STATE})
    searches_file.save_hiringcafe(path, [one])
    text = path.read_text()
    assert "# HiringCafe searches (hiringcafe.com), checked on every run" in text      # comments kept
    assert "hiringcafe:\n  - id: hc-tpm\n    name: TPM remote\n    link: https://hiringcafe.com/s/Ab12Cd34\n    search_state: {" in text
    assert config.load(path).hiringcafe == [HiringCafeSearch("hc-tpm", "TPM remote", SHARE, STATE, False)]
    searches_file.save_hiringcafe(path, [{**searches_file.load_hiringcafe(path)[0], "paused": True}])
    assert config.load(path).hiringcafe[0].paused and config.load(path).hiringcafe[0].search_state == STATE
    for bad, msg in (({"id": "Bad Id"}, "id"), ({"name": ""}, "name"), ({"link": "https://example.com/s/x"}, "link"),
                     ({"search_state": "x"}, "search_state")):
        with pytest.raises(searches_file.SearchError, match=msg):
            searches_file.clean_hiringcafe({**one, **bad})


def test_the_searches_tab_adds_renames_pauses_checks_and_deletes_one(tmp_dirs):
    searches = tmp_dirs / "searches.yaml"
    shutil.copy(config.EXAMPLE_PROFILE / "searches.yaml", searches)
    store = Store()
    store.record_hiringcafe_check("hc-technical-program-manager", found=4, new=3)
    runs = RunManager(argv_prefix=[sys.executable, "-c", "import sys; print('args', sys.argv[1:])"], db=tmp_dirs / "runs.db")
    c = TestClient(create_app(config.load(searches), token=TOKEN, store=store, tracker=LocalTracker(tmp_dirs / "t.db"),
                              runs=runs, allowed_hosts={"testserver"}, searches_path=searches, hiringcafe=cafe(),
                              resume_root=tmp_dirs / "repo"))
    assert c.get("/api/searches", headers=H).json()["hiringcafe"] == []
    r = c.post("/api/hiringcafe", headers=H, json={"link": SHARE})
    assert r.status_code == 200, r.text
    sid = r.json()["id"]
    assert sid == "hc-technical-program-manager"
    row = r.json()["hiringcafe"][0]
    assert (row["name"], row["link"], row["paused"]) == ("Technical Program Manager", SHARE, False)
    assert row["facts"] == describe(STATE) and row["check"]["new"] == 3
    assert config.load(searches).hiringcafe[0].search_state == STATE
    again = c.post("/api/hiringcafe", headers=H, json={"link": search_url(STATE), "name": "Again"})
    assert again.status_code == 400 and "already have this search" in again.json()["detail"]
    bad = c.post("/api/hiringcafe", headers=H, json={"link": "https://example.com/jobs"})
    assert bad.status_code == 400 and "isn't a HiringCafe link" in bad.json()["detail"]

    r = c.put(f"/api/hiringcafe/{sid}", headers=H, json={"name": "TPM, remote or hybrid"})
    assert r.json()["hiringcafe"][0]["name"] == "TPM, remote or hybrid"
    assert c.put(f"/api/hiringcafe/{sid}", headers=H, json={"paused": True}).json()["hiringcafe"][0]["paused"]
    assert config.load(searches).hiringcafe[0].paused
    assert c.put("/api/hiringcafe/nope", headers=H, json={"paused": True}).status_code == 404

    r = c.post("/api/runs", headers=H, json={"kind": "hiringcafe", "search_ids": [sid]})
    assert r.status_code == 200 and r.json()["label"] == "HiringCafe: TPM, remote or hybrid + signal score"
    runs.wait(r.json()["id"])
    args = ast.literal_eval(c.get(f"/api/runs/{r.json()['id']}", headers=H).json()["lines"][0][len("args "):])
    assert args == ["run", "--no-tailor", "--hiringcafe", sid]
    assert c.post("/api/runs", headers=H, json={"kind": "hiringcafe", "search_ids": ["tpm-remote"]}).status_code == 400

    assert c.delete(f"/api/hiringcafe/{sid}", headers=H).json()["hiringcafe"] == []
    assert config.load(searches).hiringcafe == []


def test_a_new_filter_gets_an_id_no_hiringcafe_search_has(tmp_dirs):
    searches = tmp_dirs / "searches.yaml"
    shutil.copy(config.EXAMPLE_PROFILE / "searches.yaml", searches)
    searches_file.save_hiringcafe(searches, [searches_file.clean_hiringcafe(
        {"id": "hc-sre", "name": "SRE", "link": SHARE, "search_state": STATE})])
    c = TestClient(create_app(config.load(searches), token=TOKEN, store=Store(), tracker=LocalTracker(tmp_dirs / "t.db"),
                              runs=RunManager(db=tmp_dirs / "runs.db"), allowed_hosts={"testserver"},
                              searches_path=searches, hiringcafe=cafe(), resume_root=tmp_dirs / "repo"))
    r = c.post("/api/searches", headers=H, json={"name": "HC SRE", "titles": ["SRE"]})
    assert r.status_code == 200 and r.json()["id"] == "hc-sre-2"


def test_links_compare_a_posting_however_its_link_is_tracked():
    job = to_job({"apply_url": "https://boards.greenhouse.io/acme/jobs/jp1?utm_source=hiring.cafe",
                  "job_information": {"title": "TPM"}})
    assert links(job) & links(make_job("jp1"))
