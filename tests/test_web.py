"""The local web app: auth, job board + Notion, résumé edit loop, searches, runs, notes."""
import io
import json
import re
import shutil
import sys
import threading
import time
from datetime import date
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from jobpipe import config, render
from jobpipe.llm import AgentResult
from jobpipe.notion import NotionTracker, TrackerEntry
from jobpipe.store import Store, safe_id
from jobpipe.tracker import LocalTracker
from jobpipe.web.board import Marks
from jobpipe.web.runs import Busy, Run, RunManager
from jobpipe.web.server import create_app
from conftest import BASE, TITLE, FakeNotion, FakeRunner, _resume, make_job, notion_row

TOKEN = "test-token"
H = {"X-Jobpipe-Token": TOKEN}
JOB_URL = "https://boards.greenhouse.io/acme/jobs/j1"


class EditRunner:
    """Claude in the résumé chat: a message ending in "?" gets an answer and no edit; anything else an edit."""
    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply
        self.pipeline = FakeRunner()    # triage and the tailoring stages

    async def run(self, call):
        self.calls.append(call)
        if call.label != "resume-edit":
            return await self.pipeline.run(call)
        if call.tail.split("new message:\n", 1)[1].split("\n")[0].endswith("?"):
            return AgentResult(files={"reply.md": "The summary is the weakest part; want me to tighten it?"}, summary="")
        md = self.reply or _resume().replace("Technical Program Manager with 9+ years",
                                             "Technical Program Manager with 9+ years, reliability first,")
        return AgentResult(files={"resume-edited.md": md, "reply.md": "- Summary: added reliability focus (E1)."},
                           summary="ok")


@pytest.fixture
def env(tmp_dirs, tmp_path):
    store = Store()
    job = make_job("j1")
    store.save_job(job, "tpm-remote")
    store.save_job(make_job("j2", company="Beta"), "tpm-remote")
    out = tmp_path / "outputs" / "2026-10-01"
    run_dir = out / "runs" / "acme-j1"
    run_dir.mkdir(parents=True)
    md = _resume()
    (run_dir / "resume-final.md").write_text(md)
    (run_dir / "01-objectives.md").write_text(f"# Role\n\n## Exact job title\n{TITLE}\n\n## Job summary\nx\n")
    (run_dir / "jd.md").write_text("ATS: Greenhouse\nSource: x\n\nposting")
    (run_dir / "04-match.md").write_text("# Match\n## Evidence ledger\nE1 ...\n")
    (run_dir / "keywords.json").write_text(json.dumps({"TPM": "technical program manag", "reliability": "reliab"}))
    report = out / "report-Acme.md"
    report.write_text("# Report\n\n<script>alert(1)</script>\n\n| a | b |\n|---|---|\n| 1 | 2 |\n")
    pdf = out / f"Jordan-Rivera-Resume-Acme-{TITLE.replace(' ', '-')}.pdf"
    render.render_pdf(md, pdf)
    store.update("j1", run_dir=str(run_dir), pdf=str(pdf), report=str(report), tailored_at="2026-10-01",
                 impact_score=8.0, resume_score=8.0, ats_total=83.0, exact_title=TITLE,
                 triage={"fit_score": 8, "one_line": "good", "band_reason": "b", "level_match": "match",
                         "hard_requirement_issues": [], "strongest_matches": ["m"], "likely_gaps": [],
                         "recommended_base": "x", "role_family": "TPM"})
    store.update("j2", triage={"fit_score": 5, "one_line": "meh"})
    notion = FakeNotion([notion_row("p1", JOB_URL, status="Not started", fit=8),
                         {**notion_row("p9", "https://example.com/other", status="Applied"), "properties": {
                             **notion_row("p9", "https://example.com/other", status="Applied")["properties"],
                             "Name": {"title": [{"plain_text": "Manual job — Zeta"}]},
                             "Company": {"rich_text": [{"plain_text": "Zeta"}]}}}])
    tracker = NotionTracker("ds", "proj", token="t", client=httpx.Client(transport=httpx.MockTransport(notion)))
    searches = tmp_path / "searches.yaml"
    shutil.copy(config.EXAMPLE_PROFILE / "searches.yaml", searches)
    runner = EditRunner()
    runs = RunManager(argv_prefix=[sys.executable, "-c", "import sys; print('args', sys.argv[1:]); print('done')"],
                      db=tmp_path / "runs.db")
    cfg = config.load(searches)
    impact = tmp_path / "impact-record.md"
    impact.write_text("# Impact record\n\n## Northwind\n\nLed 22 teams.\n")
    app = create_app(cfg, token=TOKEN, store=store, tracker=tracker,
                     runner_factory=lambda cfg: runner, runs=runs, allowed_hosts={"testserver"},
                     searches_path=searches, resume_root=tmp_path / "repo",
                     impact_path=impact)
    return {"client": TestClient(app), "app": app, "cfg": cfg, "notion": notion, "tracker": tracker, "runner": runner, "runs": runs, "pdf": pdf,
            "run_dir": run_dir, "searches": searches, "impact": impact, "store": store}


def test_auth_and_host_guard(env):
    c = env["client"]
    assert c.get("/api/summary").status_code == 401
    assert c.get("/api/summary", headers={"X-Jobpipe-Token": "nope"}).status_code == 401
    assert c.get("/api/summary", headers={**H, "host": "evil.example"}).status_code == 403
    r = c.get("/")
    assert r.status_code == 200 and "script-src 'self'" in r.headers["content-security-policy"]
    assert c.get("/api/summary", headers=H).json()["credits"]["allowance"] == 1000
    # file links may carry the token as ?t= (GET only)
    assert c.get(f"/api/jobs/j1/resume/1.pdf?t={TOKEN}").status_code == 200
    assert c.post(f"/api/impact-record/facts?t={TOKEN}", json={"text": "x"}).status_code == 401



def test_remote_when_the_location_or_a_notion_rows_notes_say_so(env):
    from jobpipe.web.board import says_remote
    assert says_remote("Remote USA") and says_remote("All-remote R&D PMO") and says_remote("Atlanta; Remote - Washington, DC")
    assert not any(map(says_remote, ["Seattle, WA (remote not stated)", "Remote/hybrid; NYC", "not remote", "Remote-friendly",
                                      "New York, NY", "Remoteness"]))
    env["store"].save_job(make_job("j7", remote=False, location="Remote USA"), "tpm-remote")   # flagged on-site, but its location says
    env["store"].save_job(make_job("j8", remote=False, location="Remote, hybrid 2 days"), "tpm-remote")
    by_id = {j["id"]: j for j in env["client"].get("/api/jobs", headers=H).json()["jobs"]}
    assert (by_id["j7"]["remote"], by_id["j7"]["mode"]) == (True, "Remote")
    assert not by_id["j8"]["remote"]


def test_each_role_says_its_regions(env):
    env["store"].save_job(make_job("j7", location="London, UK"), "tpm-remote")
    env["store"].save_job(make_job("j8", location="Remote", country_codes=["DE"]), "tpm-remote")
    env["store"].save_job(make_job("j9", location="Remote"), "tpm-remote")
    by_id = {j["id"]: j for j in env["client"].get("/api/jobs", headers=H).json()["jobs"]}
    assert by_id["j1"]["regions"] == ["US"]                     # "New York, NY"
    assert by_id["j7"]["regions"] == ["UK"] and by_id["j8"]["regions"] == ["EMEA"] and by_id["j9"]["regions"] == []
    assert by_id["notion-p9"]["regions"] == []
    assert [by_id[k]["place"] for k in ("j1", "j7", "j8", "j9", "notion-p9")] == ["NY", "UK", "Germany", "", ""]


def test_each_role_carries_its_pay_as_a_number_to_sort_by(env):
    env["store"].save_job(make_job("j7", lo=200000, hi=None), "tpm-remote")                                 # one figure
    env["store"].save_job(make_job("j8", lo=None, hi=None, salary_string="EUR 90k–110k"), "tpm-remote")     # not in USD
    env["store"].save_job(make_job("j9", lo=None, hi=None), "tpm-remote")                                   # not stated
    env["store"].save_job(make_job("j10", lo=None, hi=None, salary_string="$120K – $315K • Offers Equity • Multiple Ranges"), "tpm-remote")  # an Ashby role stored before figures were read
    env["store"].save_job(make_job("j11", lo=None, hi=None, salary_string="$45 – $60 / hour"), "tpm-remote")   # a rate, not yearly pay
    by_id = {j["id"]: j for j in env["client"].get("/api/jobs", headers=H).json()["jobs"]}
    assert (by_id["j1"]["pay"], by_id["j1"]["pay_usd"]) == ("$280k–$360k", 360000)      # the top of the range
    assert (by_id["j7"]["pay"], by_id["j7"]["pay_usd"]) == ("$200k", 200000)
    assert (by_id["j8"]["pay"], by_id["j8"]["pay_usd"]) == ("EUR 90k–110k", None)
    assert (by_id["j9"]["pay"], by_id["j9"]["pay_usd"]) == ("not stated", None)
    assert (by_id["j10"]["pay"], by_id["j10"]["pay_usd"]) == ("$120k–$315k", 315000)
    assert (by_id["j11"]["pay"], by_id["j11"]["pay_usd"]) == ("$45 – $60 / hour", None)
    assert by_id["notion-p9"]["pay_usd"] is None


def test_job_board_merges_local_jobs_and_notion(env):
    jobs = env["client"].get("/api/jobs", headers=H).json()["jobs"]
    by_id = {j["id"]: j for j in jobs}
    assert by_id["j1"]["status"] == "Not started" and by_id["j1"]["notion_page_id"] == "p1"
    assert by_id["j1"]["tailored"] and by_id["j1"]["ats_total"] == 83.0
    assert by_id["j2"]["notion_page_id"] is None and not by_id["j2"]["tailored"]
    notion_only = by_id["notion-p9"]
    assert notion_only["title"] == "Manual job" and notion_only["company"] == "Zeta" and notion_only["status"] == "Applied" and not notion_only["local"]
    assert [j["id"] for j in jobs][0] == "j1"  # highest fit first
    # "remote" means fully remote: a hybrid listing isn't, and a Notion-only row is unknown
    Store().save_job(make_job("j3", remote=True, hybrid=True), "tpm-hybrid")
    Store().save_job(make_job("j4", remote=False, hybrid=True, work_arrangement="hybrid"), "tpm-hybrid")
    by_id = {j["id"]: j for j in env["client"].get("/api/jobs", headers=H).json()["jobs"]}
    assert [by_id[k]["remote"] for k in ("j1", "j3", "j4", "notion-p9")] == [True, False, False, False]


def test_status_change_is_saved_locally_then_sent_to_notion(env, monkeypatch):
    c, notion = env["client"], env["notion"]
    j1 = lambda q="": {j["id"]: j for j in c.get("/api/jobs" + q, headers=H).json()["jobs"]}["j1"]
    assert j1()["tracker_id"] == "p1" and j1()["notion_page_id"] == "p1"
    assert c.post("/api/tracker/p1/status", headers=H, json={"status": "Applied"}).json() == {"ok": True}
    assert j1()["status"] == "Applied"                                   # shown at once, from the local tracker
    assert j1("?wait=1")["pending"] is False                             # the background sync has sent it
    assert notion.rows["p1"]["properties"]["Status"]["status"]["name"] == "Applied"
    assert c.post("/api/notion/p1/status", headers=H, json={"status": "Bogus"}).status_code == 400   # the old path still works
    assert c.post("/api/tracker/nope/status", headers=H, json={"status": "Done"}).status_code == 404

    # Notion down: the change is kept, shown, and flagged as waiting
    def down(*a, **k):
        raise RuntimeError("Notion's servers returned an error (500: Cross-cell memcached access is not allowed).")
    monkeypatch.setattr(env["tracker"], "set_status", down)
    assert c.post("/api/tracker/p1/status", headers=H, json={"status": "Blocked"}).json() == {"ok": True}
    d = c.get("/api/jobs?wait=1", headers=H).json()
    row = {j["id"]: j for j in d["jobs"]}["j1"]
    assert row["status"] == "Blocked" and row["pending"] is True and d["pending"] == 1 and "Cross-cell" in d["notion_error"]
    assert notion.rows["p1"]["properties"]["Status"]["status"]["name"] == "Applied"
    # Notion back: the next sync sends it, and Notion's older value never overwrote the unsent one
    monkeypatch.undo()
    d = c.get("/api/jobs?refresh=1", headers=H).json()
    assert d["pending"] == 0 and d["notion_error"] == "" and {j["id"]: j for j in d["jobs"]}["j1"]["status"] == "Blocked"
    assert notion.rows["p1"]["properties"]["Status"]["status"]["name"] == "Blocked"


def test_jobs_are_cached_and_notion_is_stale_while_revalidate(env, monkeypatch):
    import threading
    import time
    c = env["client"]
    r = c.get("/api/jobs", headers=H)
    etag = r.headers["etag"]
    assert r.json()["syncing"] is False and r.json()["notion_error"] == ""
    assert c.get("/api/jobs", headers={**H, "If-None-Match": etag}).status_code == 304
    # Notion changes behind the app's back; once the snapshot is over a minute old the next request
    # still answers from it, flags the refresh, and ?wait=1 returns the fresh rows.
    env["notion"].rows["p1"]["properties"]["Status"]["status"]["name"] = "Blocked"
    real, gate, list_jobs = time.time, threading.Event(), env["tracker"].list_jobs
    monkeypatch.setattr(time, "time", lambda: real() + 120)
    monkeypatch.setattr(env["tracker"], "list_jobs", lambda: (gate.wait(5), list_jobs())[1])   # Notion is slow
    stale = c.get("/api/jobs", headers=H).json()
    assert stale["syncing"] is True and {j["id"]: j for j in stale["jobs"]}["j1"]["status"] == "Not started"
    gate.set()
    fresh = c.get("/api/jobs?wait=1", headers={**H, "If-None-Match": etag})
    assert fresh.status_code == 200 and fresh.json()["syncing"] is False
    assert {j["id"]: j for j in fresh.json()["jobs"]}["j1"]["status"] == "Blocked"
    # the tracker is a local file, so a restart shows it without waiting for Notion
    assert (config.DATA / "tracker.db").exists()
    # Notion failing: Sync says so, and the list keeps the last synced rows with the reason and their age
    def down():
        raise RuntimeError("Notion's servers returned an error (500: Cross-cell memcached access is not allowed).")
    monkeypatch.setattr(env["tracker"], "list_jobs", down)
    r = c.get("/api/jobs?refresh=1", headers=H)
    assert r.status_code == 502 and "Couldn't sync with Notion. Notion's servers returned an error" in r.json()["detail"]
    kept = c.get("/api/jobs", headers=H).json()
    assert {j["id"]: j for j in kept["jobs"]}["j1"]["status"] == "Blocked"
    assert "Cross-cell" in kept["notion_error"] and kept["notion_synced"] and kept["syncing"] is False
    # an edited job file is picked up without a restart
    job = {**make_job("j2", company="Beta"), "job_title": "Renamed role"}
    time.sleep(0.01)
    (config.DATA / "jobs" / "j2.json").write_text(json.dumps(job))
    assert {j["id"]: j for j in c.get("/api/jobs", headers=H).json()["jobs"]}["j2"]["title"] == "Renamed role"


def test_star_and_referral(env):
    c = env["client"]
    by_id = lambda: {j["id"]: j for j in c.get("/api/jobs", headers=H).json()["jobs"]}
    assert by_id()["j2"]["starred"] is False and by_id()["j2"]["referral_url"] == ""
    assert c.patch("/api/jobs/j2", headers=H, json={"starred": True}).json()["starred"] is True
    r = c.patch("/api/jobs/j2", headers=H, json={"referral_url": "linkedin.com/in/jordan", "referral_name": " Jordan Lee "})
    today = date.today().isoformat()
    assert r.json() == {"id": "j2", "starred": True, "referral_url": "https://linkedin.com/in/jordan", "referral_name": "Jordan Lee",
                        "referral_step": "toask", "referral_dates": {"toask": today}, "referral_note": ""}
    j2 = by_id()["j2"]
    assert j2["starred"] and j2["referral_url"] == "https://linkedin.com/in/jordan" and j2["referral_name"] == "Jordan Lee"
    assert json.loads((config.DATA / "marks.json").read_text())["j2"]["referral_name"] == "Jordan Lee"
    for bad in ("javascript:alert(1)", "not a url", "ftp://x.example/y", "a@b", "mailto:not an address"):
        assert c.patch("/api/jobs/j2", headers=H, json={"referral_url": bad}).status_code == 400
    assert c.patch("/api/jobs/j2", headers=H, json={"referral_url": ""}).json()["referral_url"] == ""
    assert c.patch("/api/jobs/j2", headers=H, json={"starred": False}).json()["starred"] is False
    assert "j2" not in json.loads((config.DATA / "marks.json").read_text())
    # jobs that are only in Notion can be marked too; unknown jobs can't
    assert c.patch("/api/jobs/notion-p9", headers=H, json={"starred": True}).status_code == 200
    assert by_id()["notion-p9"]["starred"] is True
    assert c.patch("/api/jobs/nope", headers=H, json={"starred": True}).status_code == 404
    assert c.patch("/api/jobs/j2", headers=H, json={}).status_code == 400
    # a mark made on a job's Notion row follows the job once a search has stored it
    (config.DATA / "marks.json").write_text(json.dumps({"notion-p1": {"starred": True}}))
    assert by_id()["j1"]["starred"] is True
    c.patch("/api/jobs/j1", headers=H, json={"referral_url": "https://example.com/refer"})
    assert json.loads((config.DATA / "marks.json").read_text()) == {
        "j1": {"starred": True, "referral_url": "https://example.com/refer", "referral_step": "toask", "referral_dates": {"toask": today}}}


def test_referral_email_steps_and_note(env):
    c = env["client"]
    today = date.today().isoformat()
    j2 = lambda: {j["id"]: j for j in c.get("/api/jobs", headers=H).json()["jobs"]}["j2"]
    # no steps or note before there is a referral
    assert c.patch("/api/jobs/j2", headers=H, json={"referral_step": "asked"}).status_code == 400
    assert c.patch("/api/jobs/j2", headers=H, json={"referral_note": "hi"}).status_code == 400
    # an email address is kept as mailto:
    r = c.patch("/api/jobs/j2", headers=H, json={"referral_url": " sam.lee@example.com ", "referral_name": "Sam Lee"}).json()
    assert r["referral_url"] == "mailto:sam.lee@example.com" and r["referral_step"] == "toask"
    assert c.patch("/api/jobs/j2", headers=H, json={"referral_url": "mailto:sam.lee@example.com"}).json()["referral_url"] == "mailto:sam.lee@example.com"
    # each step keeps the day it was first reached; going back forgets the later ones
    marks = json.loads((config.DATA / "marks.json").read_text())
    marks["j2"]["referral_dates"] = {"toask": "2026-01-02"}
    (config.DATA / "marks.json").write_text(json.dumps(marks))
    r = c.patch("/api/jobs/j2", headers=H, json={"referral_step": "referred"}).json()
    assert r["referral_step"] == "referred" and r["referral_dates"] == {"toask": "2026-01-02", "referred": today}
    r = c.patch("/api/jobs/j2", headers=H, json={"referral_step": "asked"}).json()
    assert r["referral_dates"] == {"toask": "2026-01-02", "asked": today}
    assert c.patch("/api/jobs/j2", headers=H, json={"referral_step": "hired"}).status_code == 400
    # a changed contact keeps where the referral stands; the note is trimmed and capped
    r = c.patch("/api/jobs/j2", headers=H, json={"referral_url": "linkedin.com/in/sam-lee", "referral_name": "Sam Lee"}).json()
    assert r["referral_step"] == "asked" and r["referral_url"] == "https://linkedin.com/in/sam-lee"
    assert c.patch("/api/jobs/j2", headers=H, json={"referral_note": "  " + "x" * 3000}).json()["referral_note"] == "x" * 2000
    assert j2()["referral_note"] == "x" * 2000 and j2()["referral_step"] == "asked"
    # removing the referral clears all of it
    r = c.patch("/api/jobs/j2", headers=H, json={"referral_url": ""}).json()
    assert r == {"id": "j2", "starred": False, "referral_url": "", "referral_name": "", "referral_step": "", "referral_dates": {}, "referral_note": ""}
    assert "j2" not in json.loads((config.DATA / "marks.json").read_text())


def test_score_experience_from_impact_record(env, monkeypatch):
    c = env["client"]
    # by default a scored job waits in Find jobs until it is tracked: nothing is written to Notion
    posts = lambda: sum(m == "POST" and p.endswith("/pages") for m, p, _ in env["notion"].requests)
    r = c.post("/api/jobs/j2/score", headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["triage"]["fit_score"] == 8 and r.json()["notion"] == "" and posts() == 0
    # with notion.log_triaged_min_fit set, a score at or above it adds the job, as a pipeline run does
    env["cfg"].notion_log_triaged_min_fit = 7
    r = c.post("/api/jobs/j2/score", headers=H)
    assert r.json()["notion"] == "created"
    c.get("/api/jobs?wait=1", headers=H)       # tracked locally at once; the background sync copies it to Notion
    assert posts() == 1
    call = env["runner"].calls[-1]
    assert call.label == "triage:j2" and call.candidate and "jd.md" in call.documents   # candidate: sees the impact record
    assert Store().state()["j2"]["triage"]["one_line"] == "strong TPM fit"
    j2 = {j["id"]: j for j in c.get("/api/jobs?wait=1", headers=H).json()["jobs"]}["j2"]
    assert j2["fit"] == 8 and j2["status"] == "Not started" and j2["notion_page_id"]   # 7+ goes to the tracker
    assert c.get("/api/jobs/j2", headers=H).json()["triage"]["strongest_matches"] == ["m"]
    # a tailored job is re-scored without touching its Notion row
    before = posts()
    assert c.post("/api/jobs/j1/score", headers=H).json()["notion"] == "" and posts() == before
    # a pasted posting becomes a stored job that can be scored
    assert c.post("/api/jobs", headers=H, json={"title": "Staff TPM", "company": "Acme", "description": "short"}).status_code == 400
    jid = c.post("/api/jobs", headers=H, json={"title": "Staff TPM", "company": "Acme", "description": "x" * 700}).json()["id"]
    assert jid == "pasted-acme-staff-tpm" and c.post(f"/api/jobs/{jid}/score", headers=H).status_code == 200
    # a job that is only in Notion: the posting is read from its page, then stored under the same id
    import jobpipe.jd
    monkeypatch.setattr(jobpipe.jd, "fetch_posting_text", lambda url, client=None: "")
    assert c.post("/api/jobs/notion-p9/score", headers=H).status_code == 422
    monkeypatch.setattr(jobpipe.jd, "fetch_posting_text", lambda url, client=None: "Lead programs. " * 60)
    assert c.post("/api/jobs/notion-p9/score", headers=H).status_code == 200
    p9 = {j["id"]: j for j in c.get("/api/jobs", headers=H).json()["jobs"]}["notion-p9"]
    assert p9["local"] and p9["fit"] == 8 and p9["status"] == "Applied" and p9["title"] == "Manual job"
    assert c.post("/api/jobs/nope/score", headers=H).status_code == 404
    assert c.get("/api/summary", headers=H).json()["impact_record"]["path"].endswith("impact-record.md")


def test_a_job_you_apply_to_keeps_its_posting(env, monkeypatch):
    c, app = env["client"], env["app"]
    from jobpipe.postings import PostingKeeper
    kicks = []
    monkeypatch.setattr(PostingKeeper, "kick", lambda self: kicks.append(1))
    c.get("/api/jobs", headers=H)
    assert c.post("/api/tracker/p1/status", headers=H, json={"status": "Applied"}).status_code == 200 and kicks
    import jobpipe.jd
    monkeypatch.setattr(jobpipe.jd, "read_posting",
                        lambda url, client=None: {"url": url, "description": "Lead programs. " * 40})
    app.state.posting_keeper.run_once()
    # p9 was only in Notion, applied to: its posting is stored and opens with the role
    d = c.get("/api/jobs/notion-p9", headers=H).json()
    assert d["local"] and d["status"] == "Applied" and d["description"].startswith("Lead programs.")
    assert d["posting_kept"]


def test_add_a_job_from_its_link(env, monkeypatch):
    c = env["client"]
    import jobpipe.jd
    read = {"https://jobs.lever.co/zeta/1": {"job_title": "Staff TPM", "company": "Zeta", "location": "Remote",
                                             "remote": True, "description": "Lead the release train. " * 40}}
    monkeypatch.setattr(jobpipe.jd, "read_posting",
                        lambda url, client=None: {"url": url, **read.get(url, {"description": ""})})
    assert c.post("/api/jobs/lookup", headers=H, json={"url": "not a link"}).status_code == 400
    d = c.post("/api/jobs/lookup", headers=H, json={"url": "jobs.lever.co/zeta/1"}).json()
    assert d["url"] == "https://jobs.lever.co/zeta/1" and d["job_title"] == "Staff TPM" and d["readable"]
    assert d["existing"] is None
    # a link the app already has, even with tracking on it, is that job
    d = c.post("/api/jobs/lookup", headers=H, json={"url": JOB_URL + "?utm_source=linkedin"}).json()
    assert d["existing"]["id"] == "j1"
    assert c.post("/api/jobs", headers=H, json={"url": JOB_URL}).json() == {"id": "j1", "existing": True}
    # just the link: the posting is read on the server
    r = c.post("/api/jobs", headers=H, json={"url": "https://jobs.lever.co/zeta/1"})
    assert r.json() == {"id": "pasted-zeta-staff-tpm", "existing": False}
    job = Store().job("pasted-zeta-staff-tpm")
    assert (job["location"], job["remote"], job["description_read"]) == ("Remote", True, True)
    row = {j["id"]: j for j in c.get("/api/jobs", headers=H).json()["jobs"]}["pasted-zeta-staff-tpm"]
    assert row["searches"] == ["manual"] and not row["tracker_id"] and row["remote"]
    assert c.get("/api/jobs/pasted-zeta-staff-tpm", headers=H).json()["description_read"] is True
    assert c.post("/api/jobs/pasted-zeta-staff-tpm/score", headers=H).status_code == 200
    # a page that can't be read needs the description pasted
    r = c.post("/api/jobs", headers=H, json={"url": "https://example.com/jobs/9", "title": "PM", "company": "Ex"})
    assert r.status_code == 400 and "Paste the full job description" in r.json()["detail"]
    text = "Shape the roadmap for Ex's platform. " * 10
    r = c.post("/api/jobs", headers=H, json={"url": "https://example.com/jobs/9", "title": "PM", "company": "Ex",
                                             "description": text})
    job = Store().job(r.json()["id"])
    assert job["description_read"] is False and job["url"] == "https://example.com/jobs/9"


def test_a_pasted_role_located_remote_is_remote(env):
    c = env["client"]
    text = "Plan retreats and offsites for client teams worldwide. " * 10
    add = lambda title, location: c.post("/api/jobs", headers=H, json={
        "title": title, "company": "TeamOut", "location": location, "description": text}).json()["id"]
    ids = [add("Trip Designer", "Remote"), add("Ops Lead", "Remote, US"), add("Planner", "Hybrid / Remote - NYC"),
           add("Concierge", "New York, NY")]
    rows = {j["id"]: j for j in c.get("/api/jobs", headers=H).json()["jobs"]}
    assert [(rows[i]["remote"], rows[i]["mode"]) for i in ids] == [
        (True, "Remote"), (True, "Remote"), (False, "Hybrid"), (False, "Not stated")]


def test_pasted_description_is_kept_with_the_role(env, monkeypatch):
    c = env["client"]
    import jobpipe.jd
    monkeypatch.setattr(jobpipe.jd, "fetch_posting_text", lambda url, client=None: "")
    jobs = lambda: {j["id"]: j for j in c.get("/api/jobs", headers=H).json()["jobs"]}
    n = len(jobs())
    assert c.put("/api/jobs/notion-p9/description", headers=H, json={"description": "short"}).status_code == 400
    assert c.put("/api/jobs/nope/description", headers=H, json={"description": "x" * 400}).status_code == 404
    text = "Own the release train for Zeta's platform. " * 10       # under 600 chars, but pasted: enough
    assert c.put("/api/jobs/notion-p9/description", headers=H, json={"description": text}).status_code == 200
    p9 = jobs()["notion-p9"]
    assert len(jobs()) == n and p9["local"] and p9["status"] == "Applied"     # still one role, still tracked
    d = c.get("/api/jobs/notion-p9", headers=H).json()
    assert d["description"] == text.strip() and d["description_pasted"]
    # scoring compares against the pasted text, without reading the page
    assert c.post("/api/jobs/notion-p9/score", headers=H).status_code == 200
    jd = env["runner"].calls[-1].documents["jd.md"]
    assert "Own the release train" in jd and "pasted from the posting by you" in jd
    # a found job: the pasted text replaces the stored one, and survives the search finding it again
    store = Store()
    found = store.job("j2")
    assert c.put("/api/jobs/j2/description", headers=H, json={"description": text + " Extra."}).status_code == 200
    assert store.job("j2")["description_before_paste"] == found.get("description", "")
    store.save_job(found, "s1")
    assert store.job("j2")["description"].endswith("Extra.")


def wait_for_scoring(c):
    import time
    for _ in range(400):
        sc = c.get("/api/summary", headers=H).json()["scoring"]
        if not sc["active"]:
            return sc
        time.sleep(0.05)
    raise AssertionError("scoring didn't finish")


def test_batch_signal_then_full_score(env):
    import asyncio
    from jobpipe.pipeline import Pipeline
    Store().save_job(make_job("j3", company="Gamma"), "tpm-remote")
    with env["client"] as c:     # one event loop for the whole test, as under uvicorn
        by_id = lambda: {j["id"]: j for j in c.get("/api/jobs", headers=H).json()["jobs"]}
        # signal: several jobs at once, queued in the background
        r = c.post("/api/score", headers=H, json={"job_ids": ["j2", "j3", "nope"], "kind": "signal"})
        assert r.status_code == 200 and set(r.json()["active"]) == {"j2", "j3"}
        sc = wait_for_scoring(c)
        assert sc["errors"] == {} and sc["finished"] == 2
        assert by_id()["j2"]["fit"] == 8 and by_id()["j3"]["fit"] == 8 and by_id()["j3"]["impact_score"] is None
        assert c.post("/api/score", headers=H, json={"job_ids": ["nope"]}).status_code == 400
        assert c.post("/api/score", headers=H, json={"job_ids": ["j2"], "kind": "bogus"}).status_code == 400

        # full: stages 1-2 of the tailoring pipeline, no résumé written
        env["runner"].calls.clear()
        c.post("/api/score", headers=H, json={"job_ids": ["j2"], "kind": "full"})
        assert wait_for_scoring(c)["errors"] == {}
        assert [x.label for x in env["runner"].calls] == ["01-objectives", "02-skills", "03-experience", "04-matcher"]
        d = c.get("/api/jobs/j2", headers=H).json()
        full = d["full_score"]
        assert full["scores"]["Impact record"] == 8 and full["recommended_base"] == BASE
        assert full["gaps"] == ["AI evaluation depth"] and full["baseline"][BASE]["total"] > 0
        assert full["baseline"]["Impact record"]["total"] is None       # no document to count keywords in
        assert d["impact_score"] == 8 and not d["tailored"] and d["has_heatmap"]
        assert c.get("/api/jobs/j2/heatmap", headers=H).status_code == 200
        # scoring it again reuses the saved analysis: nothing changed, so no Claude calls
        env["runner"].calls.clear()
        c.post("/api/score", headers=H, json={"job_ids": ["j2"], "kind": "full"})
        assert wait_for_scoring(c)["errors"] == {} and env["runner"].calls == []
        # a tailored job already has its full score; the reason is kept for the UI
        c.post("/api/score", headers=H, json={"job_ids": ["j1"], "kind": "full"})
        assert "already scored every requirement" in wait_for_scoring(c)["errors"]["j1"]

    # tailoring the job afterwards starts at the writers
    runner = FakeRunner()
    p = Pipeline(config.load(env["searches"]), store=Store(), jobs=object(), runner=runner, use_notion=False,
                 log=lambda s: None, fetch_pages=False)
    done = asyncio.run(p.tailor_job(Store().job("j2")))
    assert done.tailored and [x.label for x in runner.calls][:2] == ["05-writer-A", "05-writer-B"]


def test_scoring_queue_limits_and_cancel():
    import asyncio
    from jobpipe.web.scoring import Scorer

    async def scenario():
        gate, started = asyncio.Event(), []

        async def slow(jid):
            started.append(jid)
            await gate.wait()
            if jid == "c":
                raise RuntimeError("boom")

        s = Scorer({"signal": slow, "full": slow})
        s.submit(list("abcde"), "signal")
        s.submit(["a"], "signal")                      # already queued: ignored
        await asyncio.sleep(0.01)
        states = {k: v["state"] for k, v in s.public()["active"].items()}
        assert started == ["a", "b", "c"] and states == {"a": "running", "b": "running", "c": "running",
                                                          "d": "queued", "e": "queued"}
        s.cancel_queued()
        assert set(s.public()["active"]) == {"a", "b", "c"} and s.finished == 2
        gate.set()
        await asyncio.sleep(0.01)
        assert s.public() == {"active": {}, "errors": {"c": "boom"}, "finished": 5} and started == ["a", "b", "c"]

    asyncio.run(scenario())


def test_static_and_pdf_caching(env):
    c = env["client"]
    html = c.get("/").text
    assert "/static/app.js?v=" in html and "/static/app.css?v=" in html
    assert c.get("/").headers["cache-control"] == "no-store"
    assert "immutable" in c.get("/static/app.js?v=abc").headers["cache-control"]
    assert c.get("/static/app.js").headers["cache-control"] == "no-cache"
    r = c.get("/api/jobs/j1/resume/1.pdf", headers=H)
    assert r.status_code == 200 and r.headers["cache-control"] == "private, no-cache"
    job = c.get("/api/jobs/j1", headers=H).json()
    name = f"{job['title']} - {job['company']}.pdf"
    assert r.headers["content-disposition"] == f'inline; filename="{name}"; filename*=UTF-8\'\'{quote(name)}'  # not "1.pdf"
    assert PdfReader(io.BytesIO(r.content)).metadata.title == name[:-4]  # the PDF viewer saves under this, not "<Name> Resume"
    assert c.get("/api/jobs/j1/resume/1.pdf", headers={**H, "If-None-Match": r.headers["etag"]}).status_code == 304
    # The app links to it by a path ending in that name, so no browser falls back on another one.
    assert c.get("/api/jobs/j1", headers=H).json()["resume"]["pdf_name"] == name
    named = c.get(f"/api/jobs/j1/resume/1/{quote(name)}", headers=H)
    assert named.content == r.content and named.headers["content-disposition"] == r.headers["content-disposition"]
    assert c.get("/api/summary", headers=H).headers["cache-control"] == "no-store"


def test_detail_report_and_files(env):
    c = env["client"]
    d = c.get("/api/jobs/j1", headers=H).json()
    assert d["resume"]["current"] == 1 and d["resume"]["versions"][0]["source"] == "pipeline"
    assert d["resume"]["versions"][0]["check"]["passed"] is True
    assert d["has_report"] is True
    html = c.get("/api/jobs/j1/report", headers=H).json()["html"]
    assert "<script>" not in html and "&lt;script&gt;" in html and "<table>" in html
    assert c.get("/api/jobs/j2", headers=H).json().get("resume") is None
    assert c.get("/api/jobs/nope", headers=H).status_code == 404


def test_edit_propose_accept_restore(env):
    c, run_dir = env["client"], env["run_dir"]
    before_pdf = env["pdf"].read_bytes()
    r = c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "Lead with reliability"})
    assert r.status_code == 200, r.text
    p = r.json()["proposal"]
    assert p["pages"] == 2 and p["check"]["passed"] is True and p["keywords_pct"] == 100.0
    assert "<li>" in p["changes_html"]
    assert any(o["op"] == "change" and any(w[0] == "add" for w in o["words"]) for o in p["diff"])
    call = env["runner"].calls[0]
    assert call.candidate and "Lead with reliability" in call.tail and "Do-not-claim" in call.instructions
    assert c.get("/api/jobs/j1/resume/proposal.pdf", headers=H).status_code == 200
    assert (run_dir / "resume-final.md").read_text() == _resume()        # nothing saved yet

    h = c.post("/api/jobs/j1/edit/accept", headers=H).json()
    assert h["current"] == 2 and h["proposal"] is None
    assert "reliability first" in (run_dir / "resume-final.md").read_text()
    assert env["pdf"].read_bytes() != before_pdf                          # deliverable PDF updated
    assert c.post("/api/jobs/j1/edit/accept", headers=H).status_code == 400

    h = c.post("/api/jobs/j1/restore", headers=H, json={"n": 1}).json()
    assert h["current"] == 3 and (run_dir / "resume-final.md").read_text() == _resume()


def _ratings(run_dir):
    """The tailoring run's ratings: two skills rows and one experience row; the tailored résumé is the second source."""
    (run_dir / "ratings-final.json").write_text(json.dumps({
        "sources": ["Platform resume", "Tailored resume"], "scores": [6, 8],
        "skills": [{"requirement": "Program management", "weight": "High", "ratings": ["strong", "strong"]},
                   {"requirement": "Reliability", "weight": "Med", "ratings": ["missing", "partial"]}],
        "experience": [{"requirement": "Cross-team launches", "weight": "High", "ratings": ["partial", "partial"]}]}))


def test_edit_is_rerated_and_its_scores_become_the_jobs(env):
    c, runner = env["client"], env["runner"]
    _ratings(env["run_dir"])
    rerate = AgentResult(files={"ratings.json": json.dumps(
        {"skills": ["strong", "strong"], "experience": ["strong"], "score": 9})}, summary="")
    pipeline_run = runner.pipeline.run

    async def run(call):
        return rerate if call.label == "resume-rerate" else await pipeline_run(call)
    runner.pipeline.run = run

    v1 = c.get("/api/jobs/j1", headers=H).json()["resume"]["versions"][0]
    # skills (3*1 + 2*.5)/5 = 80, experience 50, keywords 100: .35*80 + .35*50 + .3*100
    assert v1["ats"] == 75.5 and v1["resume_score"] == 8 and v1["ats_parts"]["skills"] == 80.0

    p = c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "Lead with reliability"}).json()["proposal"]
    assert p["ats"] == 100.0 and p["resume_score"] == 9.0 and p["rerated"] is True
    call = next(x for x in runner.calls if x.label == "resume-rerate")
    assert call.model == config.ModelCfg("claude-sonnet-5-5", "medium") and call.candidate is False   # light
    assert "Score each" not in call.instructions and "scoring-and-report.md" in call.documents
    reqs = json.loads(call.documents["requirements.json"])
    assert [r["previous"] for r in reqs["skills"]] == ["strong", "partial"] and reqs["previous_score"] == 8
    assert "+ " in call.documents["changes.md"] and "reliability first" in call.documents["resume-edited.md"]
    job = lambda: next(j for j in c.get("/api/jobs", headers=H).json()["jobs"] if j["id"] == "j1")
    assert job()["ats_total"] == 83.0                       # a proposal changes nothing yet

    c.post("/api/jobs/j1/edit/accept", headers=H)
    assert (job()["ats_total"], job()["resume_score"]) == (100.0, 9.0)
    c.post("/api/jobs/j1/restore", headers=H, json={"n": 1})
    assert (job()["ats_total"], job()["resume_score"]) == (75.5, 8)


def test_compare_two_versions_lists_what_moved(env):
    c, runner = env["client"], env["runner"]
    _ratings(env["run_dir"])
    rerate = AgentResult(files={"ratings.json": json.dumps(
        {"skills": ["strong", "strong"], "experience": ["strong"], "score": 9})}, summary="")
    pipeline_run = runner.pipeline.run

    async def run(call):
        return rerate if call.label == "resume-rerate" else await pipeline_run(call)
    runner.pipeline.run = run
    c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "Lead with reliability"})
    c.post("/api/jobs/j1/edit/accept", headers=H)

    res = c.get("/api/jobs/j1/resume/compare?a=1&b=2", headers=H).json()
    # raised ratings first, the heavier requirement first
    assert [(m["requirement"], m["a"], m["b"]) for m in res["moved"]] == [
        ("Cross-team launches", "partial", "strong"), ("Reliability", "partial", "strong")]
    assert res["keywords"] == {"gained": [], "lost": []}
    assert any(o["op"] != "same" for o in res["diff"])
    back = c.get("/api/jobs/j1/resume/compare?a=2&b=1", headers=H).json()
    assert [(m["a"], m["b"]) for m in back["moved"]] == [("strong", "partial"), ("strong", "partial")]
    assert c.get("/api/jobs/j1/resume/compare?a=1&b=7", headers=H).status_code == 404


def test_compare_lists_keywords_gained_and_lost(env):
    c = env["client"]
    env["runner"].reply = re.sub(r"(?i)technical program manag\w*", "program lead", _resume())
    c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "Say program lead"})
    c.post("/api/jobs/j1/edit/accept", headers=H)
    assert c.get("/api/jobs/j1/resume/compare?a=1&b=2", headers=H).json()["keywords"] == {"gained": [], "lost": ["TPM"]}
    assert c.get("/api/jobs/j1/resume/compare?a=2&b=1", headers=H).json()["keywords"] == {"gained": ["TPM"], "lost": []}


def test_failed_rerating_keeps_the_edit_and_rescores_keywords_only(env):
    c = env["client"]
    _ratings(env["run_dir"])
    env["runner"].reply = re.sub(r"(?i)technical program manag\w*", "program lead", _resume())   # drops the TPM keyword
    p = c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "Say program lead"}).json()["proposal"]
    # FakeRunner writes no ratings.json: skills/experience carry over, keywords drop to 50%
    assert p["rerated"] is False and p["resume_score"] == 8 and p["ats"] == 60.5
    c.post("/api/jobs/j1/edit/accept", headers=H)
    assert next(j for j in c.get("/api/jobs", headers=H).json()["jobs"] if j["id"] == "j1")["ats_total"] == 60.5


def test_chat_quotes_the_apps_scores(env):
    c, runner = env["client"], env["runner"]
    _ratings(env["run_dir"])
    c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "What's the score?"})
    call = runner.calls[-1]
    sheet = call.documents["scores.md"]
    assert "Resume score: 8/10" in sheet and "ATS: 75.5%" in sheet and "Pending" not in sheet
    assert "- [Med] Reliability: partial" in sheet and "Never give a score of your own" in call.instructions

    c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "Lead with reliability"})
    c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "Better?"})
    assert "Pending proposal (not accepted yet): resume score 8/10" in runner.calls[-1].documents["scores.md"]


def test_edit_discard_and_validation(env):
    c = env["client"]
    assert c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "  "}).status_code == 400
    c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "x"})
    assert c.get("/api/jobs/j1/edit", headers=H).json()["proposal"]["instruction"] == "x"
    c.post("/api/jobs/j1/edit/discard", headers=H)
    assert c.get("/api/jobs/j1/edit", headers=H).json()["proposal"] is None
    assert not (env["run_dir"] / "versions" / "proposal.md").exists()
    assert c.post("/api/jobs/j2/edit", headers=H, json={"instruction": "x"}).status_code == 404


def _page_fields(html_doc):
    """The editable spans of render(editable=True): {key: text shown}."""
    import html as htmllib, re
    return {k: htmllib.unescape(re.sub(r"<[^>]+>", "", t))
            for k, t in re.findall(r'data-ed="([^"]+)"[^>]*>(.*?)</span>', html_doc)}


def test_edit_on_page_saves_a_new_version(env):
    c, run_dir = env["client"], env["run_dir"]
    pg = c.get("/api/jobs/j1/resume/page", headers=H).json()
    assert pg["n"] == 1 and pg["markdown"] == _resume() and 'contenteditable="plaintext-only"' in pg["html"]
    f = _page_fields(pg["html"])
    company = next(k for k in f if k.endswith(":company"))
    role = company.replace("company", "role")
    bullet = next(k for k in f if k.endswith(":bullet"))
    edits = [{"key": company, "old": f[company], "text": f[company] + " Inc."},
             {"key": role, "old": f[role], "text": "Staff TPM, Payments"},
             {"key": bullet, "old": f[bullet], "text": ""}]
    before_pdf = env["pdf"].read_bytes()
    r = c.post("/api/jobs/j1/resume/page", headers=H, json={"base": 1, "edits": edits})
    assert r.status_code == 200, r.text
    h = r.json()
    assert h["current"] == 2 and h["pages"] == 2
    v = h["versions"][-1]
    assert v["source"] == "by hand" and v["changes"] == "Edited on the page (3 changes)."
    md = (run_dir / "resume-final.md").read_text()
    assert f"**{f[company]} Inc.** | Staff TPM, Payments" in md and f[bullet] not in md
    assert env["pdf"].read_bytes() != before_pdf                       # deliverable PDF updated
    assert "saved as v2" in c.get("/api/jobs/j1/chat", headers=H).json()["chat"][-1]["text"]
    # an editor opened on v1 is now stale
    assert c.post("/api/jobs/j1/resume/page", headers=H, json={"base": 1, "edits": edits}).status_code == 400


def test_edit_as_text_and_its_checks(env):
    c = env["client"]
    c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "Lead with reliability"})   # a pending proposal
    md = _resume()
    bad = c.post("/api/jobs/j1/resume/page", headers=H, json={"base": 1, "markdown": md})
    assert bad.status_code == 400 and "Nothing changed" in bad.json()["detail"]
    assert c.post("/api/jobs/j1/resume/page", headers=H, json={"base": 1, "markdown": "just text"}).status_code == 400
    r = c.post("/api/jobs/j1/resume/page", headers=H, json={"base": 1, "markdown": md.replace("9+ years", "10+ years")})
    assert r.status_code == 200, r.text
    assert r.json()["current"] == 2 and r.json()["proposal"] is None    # the proposal was built on v1
    assert not (env["run_dir"] / "versions" / "proposal.md").exists()
    assert "10+ years" in (env["run_dir"] / "resume-final.md").read_text()


def test_hand_edit_scores_keywords_at_once_then_rescore_rates_the_rest(env):
    c, runner = env["client"], env["runner"]
    _ratings(env["run_dir"])
    pipeline_run = runner.pipeline.run

    async def run(call):
        if call.label == "resume-rerate":
            return AgentResult(files={"ratings.json": json.dumps(
                {"skills": ["strong", "strong"], "experience": ["partial"], "score": 9})}, summary="")
        return await pipeline_run(call)
    runner.pipeline.run = run
    job = lambda: next(j for j in c.get("/api/jobs", headers=H).json()["jobs"] if j["id"] == "j1")
    md = re.sub(r"(?i)technical program manag\w*", "program lead", _resume())     # drops the TPM keyword
    h = c.post("/api/jobs/j1/resume/page", headers=H, json={"base": 1, "markdown": md}).json()
    v = h["versions"][-1]
    # skills/experience carried over from v1 (80, 50), keywords 50%: .35*80 + .35*50 + .3*50
    assert v["rating"] is True and v["rerated"] is False and v["ats"] == 60.5 and v["base"] == 1
    assert job()["ats_total"] == 60.5                                   # the job's ATS moved already
    assert not any(x.label == "resume-rerate" for x in runner.calls)

    v = c.post("/api/jobs/j1/resume/rescore", headers=H, json={"n": 2}).json()["version"]
    call = next(x for x in runner.calls if x.label == "resume-rerate")
    assert "program lead" in call.documents["resume-edited.md"] and "- " in call.documents["changes.md"]
    # skills 100, experience 50, keywords 50
    assert v["rating"] is False and v["rerated"] is True and v["ats"] == 67.5 and v["resume_score"] == 9.0
    assert (job()["ats_total"], job()["resume_score"]) == (67.5, 9.0)
    assert c.post("/api/jobs/j1/resume/rescore", headers=H, json={"n": 2}).json()["version"]["ats"] == 67.5  # no rerun
    assert sum(x.label == "resume-rerate" for x in runner.calls) == 1


def test_page_edits_keep_markdown_the_edit_does_not_touch():
    from jobpipe.web.resumes import EditError, apply_page_edits
    md = ("# Jordan Rivera\n**Staff TPM | 9+ Years**\nPortland, OR|jordan@example.com\n\n## CORE SKILLS\n"
          "**Hard Skills:** SQL, `Python`, Jira's API\n- First bullet\n  continues here\n- Second\n")
    f = _page_fields(render.render(md, editable=True))
    skills = next(k for k, t in f.items() if t.startswith("Hard Skills"))
    assert f[skills] == "Hard Skills: SQL, Python, Jira’s API"          # as the page shows it
    out, n = apply_page_edits(md, [{"key": skills, "old": f[skills], "text": "Hard Skills: SQL, Go, Python, Jira’s API"}])
    assert n == 1 and "**Hard Skills:** SQL, Go, `Python`, Jira's API" in out
    contact = next(k for k in f if k.endswith(":contact"))
    out, _ = apply_page_edits(md, [{"key": contact, "old": f[contact], "text": f[contact].replace("Portland", "Seattle")}])
    assert "Seattle, OR|jordan@example.com" in out
    head = next(k for k in f if k.endswith(":headline"))
    out, _ = apply_page_edits(md, [{"key": head, "old": f[head], "text": "Principal TPM | 9+ Years"}])
    assert "**Principal TPM | 9+ Years**" in out
    first = next(k for k, t in f.items() if t == "First bullet")
    out, _ = apply_page_edits(md, [{"key": first, "old": "First bullet", "text": ""}])
    assert "First bullet" not in out and "continues here" not in out and "- Second" in out
    with pytest.raises(EditError, match="can't be empty"):
        apply_page_edits(md, [{"key": head, "old": f[head], "text": " "}])
    with pytest.raises(EditError, match="changed since"):
        apply_page_edits(md, [{"key": head, "old": "something else", "text": "x"}])


def test_editable_page_renders_like_the_pdf():
    md = _resume()
    plain = render.render(md)
    page = render.render(md, editable=True)
    assert "data-ed" not in plain and "page-guide" in page
    import re
    spans = lambda doc: re.sub(r"<span[^>]*>|</span>", "", doc.split("</style>", 1)[1])   # the editor's and the job line's
    assert spans(page) == spans(plain)


def test_chat_answers_questions_and_remembers_the_conversation(env):
    c, runner = env["client"], env["runner"]
    r = c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "What's weakest?"}).json()
    assert r["proposal"] is None and "weakest part" in r["reply_html"]
    assert [m["role"] for m in r["chat"]] == ["user", "claude"] and "<p>" in r["chat"][1]["html"]
    assert c.get("/api/jobs/j1/edit", headers=H).json()["proposal"] is None    # a question makes no edit
    assert "first message)" in runner.calls[0].tail and runner.calls[0].expect == ["reply.md"]
    assert runner.calls[0].prose_file == "reply.md"           # an answer without its file block still counts

    r = c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "Yes, tighten it"}).json()
    assert r["proposal"]["instruction"] == "Yes, tighten it" and r["chat"][-1]["proposal"] is True
    tail = runner.calls[1].tail
    assert "What's weakest?" in tail and "want me to tighten it?" in tail     # the earlier turns go with it

    # a follow-up while the proposal is pending sees the proposal, and accepting is noted in the chat
    c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "Is that two pages?"})
    assert any(k.startswith("resume-proposed.md") for k in runner.calls[2].documents)
    assert c.get("/api/jobs/j1/edit", headers=H).json()["proposal"]["instruction"] == "Yes, tighten it"
    c.post("/api/jobs/j1/edit/accept", headers=H)
    chat = c.get("/api/jobs/j1", headers=H).json()["resume"]["chat"]
    assert chat[-1] == {**chat[-1], "role": "note", "text": "Accepted the proposed edit as v2."}

    assert c.post("/api/jobs/j1/chat/clear", headers=H).json()["chat"] == []
    assert c.get("/api/jobs/j1/chat", headers=H).json()["chat"] == []
    assert c.get("/api/jobs/j1", headers=H).json()["resume"]["current"] == 2   # versions survive a new chat



def test_chat_keeps_the_message_while_claude_answers(env):
    """The message is saved, and the chat says Claude is on it, before the reply: a page redrawn or
    reloaded meanwhile still shows both."""
    c, runner = env["client"], env["runner"]
    get_chat = next(r.endpoint for r in env["app"].routes
                    if getattr(r, "path", "") == "/api/jobs/{jid}/chat" and "GET" in r.methods)
    seen = []
    answer = runner.run

    async def run(call):
        seen.append(get_chat("j1"))
        return await answer(call)
    runner.run = run
    assert c.get("/api/jobs/j1", headers=H).json()["resume"]["chat_busy"] is False
    c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "What's weakest?"})
    assert seen[0]["busy"] is True and [m["text"] for m in seen[0]["chat"]] == ["What's weakest?"]
    after = c.get("/api/jobs/j1/chat", headers=H).json()
    assert after["busy"] is False and [m["role"] for m in after["chat"]] == ["user", "claude"]


def test_chat_notes_a_message_claude_couldnt_answer(env):
    c, runner = env["client"], env["runner"]

    async def fail(call):
        raise RuntimeError("CLI exited 1")
    runner.run = fail
    r = c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "Tighten the summary"})
    assert r.status_code == 502 and "CLI exited 1" in r.json()["detail"]
    chat = c.get("/api/jobs/j1/chat", headers=H).json()
    assert [(m["role"], m["text"][:6]) for m in chat["chat"]] == [("user", "Tighte"), ("note", "Claude")]
    assert chat["busy"] is False

def test_searches_edit_round_trip(env):
    c = env["client"]
    d = c.get("/api/searches", headers=H).json()
    assert [s["id"] for s in d["searches"]] == ["tpm-remote", "em-remote", "tpm-hybrid", "em-hybrid"]
    ss = d["searches"]
    ss.append({"id": "dir-remote", "name": "Director", "titles": ["Director of Engineering", ""], "remote": True,
               "min_salary_usd": "350000", "limit": "5"})
    r = c.put("/api/searches", headers=H, json={"searches": ss})
    assert r.status_code == 200, r.text
    text = env["searches"].read_text()
    assert "# Your profile's settings" in text and 'titles: ["Director of Engineering"]' in text
    assert config.load(env["searches"]).search("dir-remote").limit == 5
    bad = [{"id": "Bad Id", "titles": ["x"]}]
    assert c.put("/api/searches", headers=H, json={"searches": bad}).status_code == 400
    both = [{"id": "x", "titles": ["x"], "remote": True, "work_arrangement": ["hybrid"]}]
    assert c.put("/api/searches", headers=H, json={"searches": both}).status_code == 400
    assert "min_salary_usd" in c.get("/api/searches/requests", headers=H).json()["tpm-remote"]
    # Where: a region, or several, kept as written and sent to JobsPipe as their countries
    assert {r["id"] for r in d["regions"]} == {"EU", "Schengen", "EEA", "Europe", "EMEA"}
    ss.append({"id": "em-eu", "name": "EM, Europe", "titles": ["Engineering Manager"], "remote": True, "country": ["eu", "Schengen"]})
    assert c.put("/api/searches", headers=H, json={"searches": ss}).status_code == 200
    assert 'country: ["EU", "Schengen"]' in env["searches"].read_text()
    assert len(c.get("/api/searches/requests", headers=H).json()["em-eu"]["job_country_code_or"]) == 31
    assert c.get("/api/searches", headers=H).json()["searches"][-1]["country"] == ["EU", "Schengen"]
    narnia = [{"id": "x", "titles": ["x"], "country": "Narnia"}]
    assert "Where" in c.put("/api/searches", headers=H, json={"searches": narnia}).json()["detail"]


def test_add_edit_delete_one_search(env):
    c = env["client"]
    new = {"name": "Director of TPM, remote", "titles": ["Director, Technical Program Management"], "remote": True, "limit": ""}
    r = c.post("/api/searches", headers=H, json=new)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "director-of-tpm-remote" and [s["id"] for s in r.json()["searches"]][-1] == "director-of-tpm-remote"
    assert c.post("/api/searches", headers=H, json=new).json()["id"] == "director-of-tpm-remote-2"   # same name again
    assert c.post("/api/searches", headers=H, json={**new, "id": "tpm-remote"}).status_code == 400    # id taken
    assert c.post("/api/searches", headers=H, json={"name": "No titles"}).status_code == 400
    text = env["searches"].read_text()
    assert "# Your profile's settings" in text and "id: director-of-tpm-remote-2" in text
    assert config.load(env["searches"]).search("director-of-tpm-remote").remote is True

    edited = {**new, "titles": ["Director of Engineering"], "remote": None, "work_arrangement": ["hybrid"], "locations": ["New York"]}
    r = c.put("/api/searches/director-of-tpm-remote", headers=H, json=edited)
    assert r.status_code == 200, r.text
    s = config.load(env["searches"]).search("director-of-tpm-remote")
    assert s.titles == ["Director of Engineering"] and s.work_arrangement == ["hybrid"] and not s.remote
    assert c.put("/api/searches/nope", headers=H, json=edited).status_code == 404

    r = c.delete("/api/searches/director-of-tpm-remote-2", headers=H)
    assert [s["id"] for s in r.json()["searches"]] == ["tpm-remote", "em-remote", "tpm-hybrid", "em-hybrid", "director-of-tpm-remote"]
    assert c.delete("/api/searches/director-of-tpm-remote-2", headers=H).status_code == 404


def test_runs(env):
    c, runs = env["client"], env["runs"]
    r = c.post("/api/runs", headers=H, json={"kind": "run", "search_ids": ["tpm-remote", "bad id!"], "no_tailor": True})
    assert r.status_code == 200
    rid = r.json()["id"]
    runs.wait(rid)
    out = c.get(f"/api/runs/{rid}", headers=H).json()
    assert out["status"] == "done"
    assert out["lines"][0] == "args ['run', '--search', 'tpm-remote', '--no-tailor']"
    assert c.post("/api/runs", headers=H, json={"kind": "tailor", "job_id": "nope"}).status_code == 400
    assert c.post("/api/runs", headers=H, json={"kind": "rm -rf"}).status_code == 400
    r = c.post("/api/runs", headers=H, json={"kind": "tailor-pasted", "title": "Staff TPM", "company": "Acme",
                                             "description": "x" * 400})
    runs.wait(r.json()["id"])
    line = c.get(f"/api/runs/{r.json()['id']}", headers=H).json()["lines"][0]
    assert "'tailor', 'pasted-acme-staff-tpm']" in line          # stored first, then tailored as a stored job
    assert Store().job("pasted-acme-staff-tpm")["description"] == "x" * 400


def test_run_results_list_the_jobs_it_scored(env):
    c, runs = env["client"], env["runs"]
    root = Store().root
    runs.argv_prefix = [sys.executable, "-c",
                        "from pathlib import Path; from jobpipe.store import Store; "
                        f"Store(Path({str(root)!r})).update('j2', triage={{'fit_score': 7, 'one_line': 'better'}}, "
                        "triaged_at='2026-10-01')"]
    r = c.post("/api/runs", headers=H, json={"kind": "run", "search_ids": ["tpm-remote"], "no_tailor": True})
    runs.wait(r.json()["id"])
    out = c.get(f"/api/runs/{r.json()['id']}", headers=H).json()
    assert out["status"] == "done", out["lines"]
    assert [(x["id"], x["fit"], x["tailored"]) for x in out["results"]] == [("j2", 7, False)]

    # After a restart the run, its output and its results are still there.
    again = RunManager(db=env["runs"].db)
    run = again.runs[r.json()["id"]]
    assert run.status == "done" and run.label == "Search + signal score" and run.args == ["run", "--search", "tpm-remote", "--no-tailor"]
    assert [x["id"] for x in run.results] == ["j2"]


def test_a_job_kept_under_its_page_address_moves_to_a_safe_id(tmp_dirs, tmp_path):
    """A JobsPipe job stored under its URL as the id (Himalayas postings, before ids were made safe) couldn't
    open: /api/jobs/<id> can't hold its slashes. At start-up it moves to a safe id, and its tracker row, the
    runs that scored it and its star follow."""
    url = "https://himalayas.app/companies/acme/jobs/staff-tpm"
    store = Store()
    store.save_job(make_job(url, url=url), "tpm-remote")
    store.update(url, triage={"fit_score": 8, "one_line": "good"})
    local = LocalTracker(store.root / "tracker.db")
    local.track(TrackerEntry(TITLE, "Acme", url, 8, "good"), job_id=url)
    tid = local.rows()[0]["page_id"]
    Marks(store.root / "marks.json").set(url, starred=True)
    runs = RunManager(argv_prefix=[sys.executable, "-c", "print('done')"], db=local.path)
    run = runs.start("Search", ["run"], marks={url: "before", "j9": "x"})
    runs.wait(run.id)
    runs.save_results(run, [{"id": url, "title": TITLE}])
    assert (store.jobs_dir / "https:").is_dir()

    runs = RunManager(db=local.path)       # the app restarts
    c = TestClient(create_app(config.load(), token=TOKEN, store=store, tracker=local, runs=runs,
                              allowed_hosts={"testserver"}, resume_root=tmp_path / "repo"))
    new = safe_id(url)
    d = c.get(f"/api/jobs/{new}", headers=H)
    assert d.status_code == 200, d.text
    assert (d.json()["company"], d.json()["fit"], d.json()["tracker_id"], d.json()["starred"]) == ("Acme", 8, tid, True)
    assert list(store.jobs_dir.iterdir()) == [store.jobs_dir / f"{new}.json"] and store.job(new)["id"] == new
    assert [r["job_id"] for r in local.rows()] == [new]
    assert [x["id"] for x in c.get(f"/api/runs/{run.id}", headers=H).json()["results"]] == [new]
    assert RunManager(db=local.path).runs[run.id].marks == {new: "before", "j9": "x"}
    assert store.fix_ids() == {}           # once is enough

    # A run from before safe ids that outlived the restart writes to the old id: the next start folds that in.
    store.update(url, impact_score=9.0, tailored_at="2026-10-11")
    assert store.fix_ids() == {url: new}
    st = store.state()[new]
    assert (st["impact_score"], st["tailored_at"], st["triage"]["fit_score"], st["searches"]) == (9.0, "2026-10-11", 8, ["tpm-remote"])


def _until(cond, seconds=10):
    end = time.monotonic() + seconds
    while not cond():
        assert time.monotonic() < end, "timed out"
        threading.Event().wait(0.05)


def test_runs_are_kept_in_the_database(tmp_path):
    db = tmp_path / "tracker.db"
    runs = RunManager(argv_prefix=[sys.executable, "-c", "print('one'); print('two')"], db=db)
    first = runs.start("first", ["a"], marks={"j1": "x"})
    runs.wait(first.id)
    runs.argv_prefix = [sys.executable, "-c", "import time; print('started'); time.sleep(30)"]
    second = runs.start("second", [])
    _until(lambda: second.lines)

    reopened = RunManager(argv_prefix=[sys.executable, "-c", "pass"], db=db)   # the app restarted mid-run
    a, b = reopened.runs[first.id], reopened.runs[second.id]
    assert (a.label, a.args, a.lines, a.status, a.marks) == ("first", ["a"], ["one", "two"], "done", {"j1": "x"})
    assert a.finished and a.results is None
    assert all(isinstance(t, float) for t in a.line_at) and len(a.line_at) == 2   # each line keeps when it was printed
    assert (b.lines, b.status) == (["started"], "running")       # still going: the restart didn't stop it
    second.proc.kill()                                          # killed outright, it can't say how it ended
    _until(lambda: b.status != "running")
    assert (b.lines, b.status, b.returncode) == (["started"], "interrupted", None)
    assert reopened.active() is None and reopened.start("third", []).id == second.id + 1


def test_a_run_carries_on_through_a_restart_and_its_end_lands(tmp_path):
    db = tmp_path / "tracker.db"
    script = "import time; print('started', flush=True); time.sleep(1.5); print('finished')"
    old = RunManager(argv_prefix=[sys.executable, "-c", script], db=db)
    run = old.start("Make résumé: x", ["tailor", "j1"])
    _until(lambda: run.lines)
    old_proc = run.proc
    old.close()                                                 # the app stops; the run's process doesn't
    new = RunManager(argv_prefix=[sys.executable, "-c", "pass"], db=db)
    picked = new.runs[run.id]
    assert picked.status == "running"
    _until(lambda: picked.status != "running")
    assert (picked.lines, picked.status, picked.returncode) == (["started", "finished"], "done", 0)
    assert not old_proc.alive()


def test_stopping_a_picked_up_run_checks_it_is_ours(tmp_path, monkeypatch):
    import jobpipe.web.runs as runs_mod
    db = tmp_path / "tracker.db"
    RunManager(argv_prefix=[sys.executable, "-c", "import time; print('x', flush=True); time.sleep(30)"], db=db).start("s", [])
    new = RunManager(db=db)
    run = new.runs[1]
    assert run.status == "running" and run.proc.popen is None
    # A process that only has the run's pid (macOS reuses them) is never signalled.
    monkeypatch.setattr(runs_mod, "is_ours", lambda pid, nonce: False)
    new.stop(1)
    threading.Event().wait(0.5)
    assert run.proc.alive()
    monkeypatch.undo()
    new.stop(1)                                                 # the real check: it is ours, so it stops
    _until(lambda: run.status != "running")
    assert run.status == "stopped" and run.returncode == -15


def test_waiting_runs_wait_on_through_a_restart(tmp_path):
    import sqlite3
    db = tmp_path / "tracker.db"
    old = RunManager(argv_prefix=[sys.executable, "-c", "import time; time.sleep(30)"], db=db, max_runs=1)
    going = old.start("Make résumé: a", ["tailor", "a"], queue=True)
    waiting = old.start("Make résumé: b", ["tailor", "b"], marks=lambda: {"b": "1"}, key="tailor:b", queue=True)
    assert waiting.status == "queued"
    old.close()
    with sqlite3.connect(db) as con:                            # an edited database can't queue another command
        con.execute("INSERT INTO runs(id, label, args, started, queued) VALUES (9, 'x', '[\"tailor\", \"--jd-file=/etc/hosts\"]', 'now', 1)")
    new = RunManager(argv_prefix=[sys.executable, "-c", "print('made')"], db=db, max_runs=1)
    assert [r.id for r in new.waiting] == [waiting.id] and new.runs[waiting.id].key == "tailor:b"
    assert new.runs[9].status == "failed" and "Not started" in new.runs[9].lines[-1]
    with pytest.raises(Busy):                                   # still one run per job
        new.start("again", ["tailor", "b"], key="tailor:b", queue=True)
    new.resume(lambda: {"b": "2"})
    assert new.runs[waiting.id].status == "queued"              # the slot is still taken by the one going
    new.stop(going.id)                                          # it ends: the waiting one starts
    _until(lambda: new.runs[waiting.id].status == "done")
    assert new.runs[waiting.id].lines == ["made"] and new.runs[waiting.id].marks == {"b": "2"}


def test_quitting_stops_every_run(tmp_path):
    db = tmp_path / "tracker.db"
    runs = RunManager(argv_prefix=[sys.executable, "-c", "import time; print('x', flush=True); time.sleep(30)"], db=db, max_runs=1)
    a = runs.start("a", ["tailor", "a"], queue=True)
    b = runs.start("b", ["tailor", "b"], queue=True)
    assert runs.going() == {"running": 1, "waiting": 1, "text": "1 run is going and 1 is waiting their turn"}
    assert runs.stop_all() == 2
    assert (a.status, b.status) == ("stopped", "stopped") and not runs.waiting
    reopened = RunManager(db=db)                                # and they stay stopped
    assert [reopened.runs[x.id].status for x in (a, b)] == ["stopped", "stopped"] and not reopened.waiting


def test_the_app_says_what_quitting_would_stop_and_restarting_keeps(env, monkeypatch):
    import signal
    import uvicorn
    from jobpipe.web import server as server_mod
    c, runs = env["client"], env["runs"]
    runs.runs.clear()
    runs.argv_prefix = [sys.executable, "-c", "import time; time.sleep(30)"]
    assert c.get("/api/runs/going", headers=H).json() == {"running": 0, "waiting": 0, "text": ""}
    c.post("/api/runs", headers=H, json={"kind": "tailor", "job_id": "j1"})
    assert c.get("/api/runs/going", headers=H).json()["text"] == "1 run is going"

    make = lambda: server_mod.app_server(uvicorn.Config(env["app"]), runs)
    restart = make()
    restart.handle_exit(signal.SIGTERM, None)                    # oyster restart: the runs carry on
    assert restart.should_exit and not restart.quitting
    monkeypatch.setattr(server_mod, "at_terminal", lambda: False)
    quit_ = make()
    quit_.handle_exit(signal.SIGINT, None)                       # the Mac app's Quit (it asked already): they stop
    assert quit_.should_exit and quit_.quitting
    monkeypatch.setattr(server_mod, "at_terminal", lambda: True)
    ctrl_c = make()
    ctrl_c.handle_exit(signal.SIGINT, None)                      # Ctrl+C at a terminal with a run going: it says so first
    assert not ctrl_c.should_exit
    ctrl_c.handle_exit(signal.SIGINT, None)                      # and a second one quits
    assert ctrl_c.should_exit and ctrl_c.quitting

    assert c.post("/api/runs/stop-all", headers=H).json() == {"stopped": 1}
    assert c.get("/api/runs/going", headers=H).json()["running"] == 0


def test_run_lines_get_times_in_an_older_database(tmp_path):
    import sqlite3
    db = tmp_path / "tracker.db"
    with sqlite3.connect(db) as con:   # the schema from before lines had times, with one old run
        con.executescript("CREATE TABLE runs (id INTEGER PRIMARY KEY, label TEXT NOT NULL, args TEXT NOT NULL, started TEXT NOT NULL, "
                          "finished TEXT, returncode INTEGER, marks TEXT, results TEXT); "
                          "CREATE TABLE run_lines (run_id INTEGER NOT NULL, n INTEGER NOT NULL, line TEXT NOT NULL, PRIMARY KEY (run_id, n)); "
                          "INSERT INTO runs VALUES (1, 'old', '[]', '2026-10-01T10:00:00+00:00', '2026-10-01T10:20:00+00:00', 0, NULL, NULL); "
                          "INSERT INTO run_lines VALUES (1, 0, 'hello');")
    runs = RunManager(argv_prefix=[sys.executable, "-c", "print('new')"], db=db)
    assert runs.runs[1].lines == ["hello"] and runs.runs[1].line_at == [None]
    runs.wait(runs.start("new", []).id)
    assert RunManager(db=db).runs[2].line_at[0] > 0


def _tailor_run(rid, lines, started, finished=None, returncode=None, job="j1"):
    """A tailoring run with its lines printed at the given seconds after `started` (a Unix time)."""
    from datetime import datetime, timezone
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")
    run = Run(rid, f"Tailor {job}", ["tailor", job], iso(started), finished=iso(finished) if finished else None, returncode=returncode)
    run.lines = [line for line, _ in lines]
    run.line_at = [None if at is None else started + at for _, at in lines]
    if finished is None:
        run.proc = object()   # still going
    return run


FULL_RUN = [("Tailoring: TPM @ Northwind Cloud", 5), ("  Stage 1: objectives, skills, experience", 60), ("  Stage 2: matcher", 200),
            ("  Stage 3: writers A, B, C", 500), ("  Stage 3: rating drafts", 800), ("  Stage 4: merge", 900),
            ("  Stage 4: merge scored 85% < Draft A 86%; restoring keywords", 1100), ("  Stage 5: PDF", 1300),
            ("  Stage 6: heat map and report", 1330)]


def test_tailoring_progress_learns_each_steps_time_from_past_runs():
    from jobpipe.web.progress import Estimator, STEPS
    runs = RunManager(argv_prefix=[sys.executable, "-c", "pass"])
    t0 = 1_790_000_000
    for k in range(3):   # three finished runs, 1500 s each: their steps take 60, 140, 300, 300, 100, 400, 30, 170 s
        runs.runs[k + 1] = _tailor_run(k + 1, FULL_RUN, t0 + k * 10_000, finished=t0 + k * 10_000 + 1500, returncode=0)
    est = Estimator(runs)
    assert [round(m) for m in est.medians()] == [60, 140, 300, 300, 100, 400, 30, 170]

    # a run 100 s into its writers step (500 s in): 200 of 300 s left there, then 100 + 400 + 30 + 170 after it
    now = t0 + 50_000
    run = runs.runs[9] = _tailor_run(9, FULL_RUN[:4], now - 600, job="j9")
    p = est.of(run, now=now)
    assert (p["job"], p["step"], p["n"], p["of"]) == ("j9", STEPS[3][0], 4, 8)
    assert p["eta_s"] == 900 and p["total_s"] == 1500 and not p["slow"]
    assert p["fraction"] == round(600 / 1500, 4) and p["cap"] == round((500 + 285) / 1500, 4)

    # long past the step's usual time it's "slow", stays just short of the step's end, and keeps a small floor of time
    late = est.of(run, now=now + 2000)
    assert late["slow"] and late["fraction"] == late["cap"] and late["eta_s"] == 30 + 700 == late["eta_min_s"]

    # a job that already had a full score skips steps 2 and 3, so it has six steps and less time to go
    reused = [FULL_RUN[0], ("  Stages 1-2: reusing this job's full score", 20), ("  Stage 3: writers A, B, C", 40)]
    r = est.of(_tailor_run(10, reused, now - 40, job="j10"), now=now)
    assert (r["n"], r["of"], r["eta_s"]) == (2, 6, 300 + 100 + 400 + 30 + 170)

    # only tailoring runs that are going get a ring
    assert est.of(runs.runs[1], now=now) is None
    search = Run(11, "Search", ["search"], "2026-10-06T10:00:00+00:00")
    search.proc = object()
    assert est.of(search, now=now) is None


def test_tailoring_progress_before_runs_had_step_times():
    """Old runs only say how long they took in all: the first guesses are stretched to match."""
    from jobpipe.web.progress import DEFAULT_S, Estimator
    runs = RunManager(argv_prefix=[sys.executable, "-c", "pass"])
    t0 = 1_790_000_000
    total = 2 * sum(DEFAULT_S)
    for k in range(2):
        runs.runs[k + 1] = _tailor_run(k + 1, [(line, None) for line, _ in FULL_RUN], t0 + k * 10_000, finished=t0 + k * 10_000 + total, returncode=0)
    assert Estimator(runs).medians() == [2 * d for d in DEFAULT_S]


def test_summary_says_how_far_a_tailoring_run_is(env):
    c, runs = env["client"], env["runs"]
    runs.argv_prefix = [sys.executable, "-c", "import time; print('Tailoring: x @ y'); print('  Stage 1: objectives'); time.sleep(30)"]
    run = runs.start("Tailor x", ["tailor", "j1"])
    try:
        for _ in range(100):
            if len(run.lines) >= 2:
                break
            threading.Event().wait(0.05)
        d = c.get("/api/summary", headers=H).json()
        assert d["durations"]["make_resume_s"] > d["durations"]["full_score_s"] > 0   # the times on the job's buttons
        p = d["active_runs"][0]["progress"]
        assert (p["job"], p["step"], p["n"], p["of"]) == ("j1", "Pulling out requirements", 2, 8) and p["eta_s"] > 0
    finally:
        run.proc.kill()
        runs.wait(run.id)


def test_outside_resume_for_a_job_only_in_the_tracker(env, tmp_path):
    c = env["client"]
    refs = tmp_path / "repo" / "references"
    refs.mkdir(parents=True)
    (refs / "resume-rules.md").write_text("# Rules\n")
    (refs / "resume-zeta-manual-job.md").write_text(_resume())
    run = tmp_path / "repo" / "resume-runs" / "zeta-other-role"
    run.mkdir(parents=True)
    for name, text in (("resume-final.md", _resume()), ("jd.md", "posting"), ("04-match.md", "# Match\n")):
        (run / name).write_text(text)

    # Matched by name: every word of "zeta-manual-job" is in the job's company and title.
    o = c.get("/api/jobs/notion-p9/outside-resume", headers=H).json()
    assert o["matched"] and o["source"]["key"] == "ref:resume-zeta-manual-job"
    assert [x["key"] for x in o["options"]] == ["ref:resume-zeta-manual-job", "run:zeta-other-role"]
    assert o["resume"]["current"] == 1 and o["resume"]["editable"] is False
    assert c.get("/api/jobs/notion-p9/resume/1.pdf", headers=H).content[:4] == b"%PDF"
    assert c.post("/api/jobs/notion-p9/edit", headers=H, json={"instruction": "x"}).status_code == 400

    # Picking the skill run copies it, with its posting and match brief, so it can be edited.
    o = c.post("/api/jobs/notion-p9/outside-resume", headers=H, json={"source": "run:zeta-other-role"}).json()
    assert not o["matched"] and o["source"]["key"] == "run:zeta-other-role" and o["resume"]["editable"] is True
    p = c.post("/api/jobs/notion-p9/edit", headers=H, json={"instruction": "Lead with reliability."})
    assert p.status_code == 200
    assert c.post("/api/jobs/notion-p9/edit/accept", headers=H).json()["current"] == 2
    assert not (run / "versions").exists()      # edits stay in data/, never in the repo's run folder
    v2 = next(env["store"].root.glob("linked/*/versions/v2.pdf"))
    job = next(j for j in c.get("/api/jobs", headers=H).json()["jobs"] if j["id"] == "notion-p9")
    assert PdfReader(v2).metadata.title == f"{job['title']} - {job['company']}"   # drawn with the role's name

    assert c.post("/api/jobs/notion-p9/outside-resume", headers=H, json={"source": "none"}).json()["resume"] is None
    assert c.post("/api/jobs/notion-p9/outside-resume", headers=H, json={"source": "../x"}).status_code == 400
    assert c.post("/api/jobs/notion-p9/outside-resume", headers=H, json={"source": ""}).json()["matched"] is True


def test_sent_resume_recorded_when_applied_and_kept_in_notion(env):
    c, notion = env["client"], env["notion"]
    jobs = lambda q="": {j["id"]: j for j in c.get(f"/api/jobs{q}", headers=H).json()["jobs"]}
    assert c.get("/api/jobs/j2/sent", headers=H).status_code == 400          # not tracked
    assert c.get("/api/jobs/j1/sent", headers=H).json()["sent"] is None

    # Marking a tailored job Applied freezes its current résumé as the one sent, and puts it in Notion.
    c.post("/api/tracker/p1/status", headers=H, json={"status": "Applied"})
    s = c.get("/api/jobs/j1/sent", headers=H).json()
    assert s["sent"]["source"] == "app" and s["sent"]["note"] == "v1" and s["sent"]["name"] == env["pdf"].name
    job = c.get("/api/jobs/j1", headers=H).json()
    shown = c.get("/api/jobs/j1/sent.pdf", headers=H)
    assert s["sent"]["download_name"] == f"{job['title']} - {job['company']}.pdf" and quote(s["sent"]["download_name"]) in shown.headers["content-disposition"]
    assert PdfReader(io.BytesIO(shown.content)).metadata.title == f"{job['title']} - {job['company']}"
    assert c.get(f"/api/jobs/j1/sent/file/{env['pdf'].name}", headers=H).content == c.get("/api/jobs/j1/sent.pdf", headers=H).content
    assert jobs("?wait=1")["j1"]["pending"] is False
    assert notion.rows["p1"]["properties"]["Resume Used"]["files"][0]["name"] == env["pdf"].name
    sent_copy = next(data for name, data in notion.uploads.values() if name == env["pdf"].name)
    assert PdfReader(io.BytesIO(sent_copy)).metadata.title == f"{job['title']} - {job['company']}"   # not "<Name> Resume"

    # A file already in Notion's Resume Used is shown (downloaded once), and never replaced by an upload.
    pdf = env["pdf"].read_bytes()
    notion.uploads["fu9"] = ("Sent-Zeta.pdf", pdf)
    notion.rows["p9"]["properties"]["Resume Used"] = {"files": [
        {"name": "Sent-Zeta.pdf", "type": "file", "file": {"url": "https://files.example/fu9/Sent-Zeta.pdf"}}]}
    assert jobs("?refresh=1")["notion-p9"]["sent_resume"] == "Sent-Zeta.pdf"
    assert c.get("/api/jobs/notion-p9/sent", headers=H).json()["sent"]["source"] == "notion"
    shown = PdfReader(io.BytesIO(c.get("/api/jobs/notion-p9/sent.pdf", headers=H).content))
    assert shown.metadata.title == "Sent-Zeta" and len(shown.pages) == len(PdfReader(env["pdf"]).pages)
    assert c.put("/api/jobs/notion-p9/sent", headers={**H, "X-Filename": "x.pdf"}, content=b"not a pdf").status_code == 400
    s = c.put("/api/jobs/notion-p9/sent", headers={**H, "X-Filename": "My%20Resume.pdf"}, content=pdf).json()
    assert s["sent"]["source"] == "upload" and s["sent"]["name"] == "My-Resume.pdf" and s["in_notion"]
    assert jobs("?wait=1")["notion-p9"]["sent_resume"] == "My-Resume.pdf"
    assert notion.rows["p9"]["properties"]["Resume Used"]["files"][0]["name"] == "Sent-Zeta.pdf"

    # Forgetting the copy here falls back to Notion's file.
    assert c.delete("/api/jobs/notion-p9/sent", headers=H).json()["sent"]["source"] == "notion"


def test_denied_without_marking_applied_keeps_the_resume_sent(env):
    c, notion = env["client"], env["notion"]
    assert "Denied" in c.get("/api/summary", headers=H).json()["statuses"]
    jobs = lambda: {j["id"]: j for j in c.get("/api/jobs?wait=1", headers=H).json()["jobs"]}
    assert jobs()["j1"]["status"] == "Not started"
    # Turned down without being marked Applied first: it was still sent, so its résumé is kept as the one sent.
    assert c.post("/api/tracker/p1/status", headers=H, json={"status": "Denied"}).json() == {"ok": True}
    assert c.get("/api/jobs/j1/sent", headers=H).json()["sent"]["note"] == "v1"
    job = jobs()["j1"]
    assert job["status"] == "Denied" and job["pending"] is False
    assert notion.rows["p1"]["properties"]["Status"]["status"]["name"] == "Denied"


def test_interviewing_counts_as_applied_with_its_day(env):
    c = env["client"]
    statuses = c.get("/api/summary", headers=H).json()["statuses"]
    assert {"Interviewing", "Offer"} <= set(statuses) and "Waiting" not in statuses
    jobs = lambda: {j["id"]: j for j in c.get("/api/jobs?wait=1", headers=H).json()["jobs"]}
    assert jobs()["j1"]["applied_on"] == ""
    # straight to Interviewing: it was sent, so the résumé is kept and the day it was applied to is today
    assert c.post("/api/tracker/p1/status", headers=H, json={"status": "Interviewing"}).json() == {"ok": True}
    assert c.get("/api/jobs/j1/sent", headers=H).json()["sent"]["note"] == "v1"
    assert jobs()["j1"]["applied_on"] == date.today().isoformat()


def test_runs_side_by_side(env):
    c, runs = env["client"], env["runs"]
    runs.runs.clear()
    runs.argv_prefix = [sys.executable, "-c", "import time; time.sleep(5)"]
    start = lambda **body: c.post("/api/runs", headers=H, json=body)
    search = start(kind="preflight", search_ids=["tpm-remote"])
    assert search.status_code == 200
    r = start(kind="run", search_ids=["tpm-remote"])                 # searches take turns
    assert r.status_code == 409 and "search is already going" in r.json()["detail"]
    tailor = start(kind="tailor", job_id="j2")                       # a tailoring run goes alongside
    assert tailor.status_code == 200
    r = start(kind="tailor", job_id="j2")                            # but not twice for one job
    assert r.status_code == 409 and "already being made" in r.json()["detail"]
    assert {x["id"] for x in c.get("/api/summary", headers=H).json()["active_runs"]} == {search.json()["id"], tailor.json()["id"]}

    runs.max_runs = 2
    with pytest.raises(Busy, match="the most at once"):              # at the limit, a run that doesn't queue is refused
        runs.start("Startup refresh", ["startups", "refresh"])
    for x in (search, tailor):
        c.post(f"/api/runs/{x.json()['id']}/stop", headers=H)
        runs.wait(x.json()["id"])
    assert start(kind="preflight", search_ids=["tpm-remote"]).status_code == 200
    c.post(f"/api/runs/{max(runs.runs)}/stop", headers=H)


def test_make_resume_waits_its_turn(env):
    c, runs = env["client"], env["runs"]
    runs.runs.clear()
    runs.max_runs = 1
    runs.argv_prefix = [sys.executable, "-c", "import time; time.sleep(30)"]
    start = lambda **body: c.post("/api/runs", headers=H, json=body)
    first = start(kind="tailor", job_id="j1").json()
    second = start(kind="tailor", job_id="j2")                       # the most are going: it waits, not refused
    assert second.status_code == 200 and second.json()["status"] == "queued"
    second = second.json()
    r = start(kind="tailor", job_id="j2")                            # still one run per job, waiting or not
    assert r.status_code == 409 and "already being made" in r.json()["detail"]
    status = {j["id"]: j["status"] for j in c.get("/api/jobs", headers=H).json()["jobs"]}
    assert status["j2"] == "In progress"
    going = c.get("/api/summary", headers=H).json()["active_runs"]
    assert [(x["id"], x["status"], x["queue_position"]) for x in going] == [
        (first["id"], "running", None), (second["id"], "queued", 1)]
    assert c.get(f"/api/runs/{second['id']}", headers=H).json()["results"] == []

    c.post(f"/api/runs/{first['id']}/stop", headers=H)               # a slot frees: the queued run starts
    runs.wait(first["id"])
    for _ in range(100):                                              # the slot frees just after wait() returns
        if runs.runs[second["id"]].status == "running":
            break
        threading.Event().wait(0.02)
    assert runs.runs[second["id"]].status == "running" and runs.runs[second["id"]].marks["j1"]
    third = start(kind="tailor", job_id="j1").json()
    assert third["status"] == "queued"
    stopped = c.post(f"/api/runs/{third['id']}/stop", headers=H).json()   # stopping a waiting run drops it
    assert stopped["status"] == "stopped" and not runs.waiting
    c.post(f"/api/runs/{second['id']}/stop", headers=H)
    runs.wait(second["id"])
    assert runs.runs[third["id"]].proc is None                        # it never started


def test_make_resume_for_checked_jobs_queues_them(env):
    c, runs = env["client"], env["runs"]
    runs.runs.clear()
    runs.max_runs = 1
    runs.argv_prefix = [sys.executable, "-c", "import time; time.sleep(30)"]
    solo = c.post("/api/runs", headers=H, json={"kind": "tailor", "job_id": "j2"}).json()
    r = c.post("/api/runs/resumes", headers=H, json={"job_ids": ["j1", "j2", "nope", "j1"]}).json()
    # one run per job, in order; one already going and one with no posting are skipped and said why
    assert [(x["args"], x["status"]) for x in r["runs"]] == [(["tailor", "j1"], "queued")]
    assert r["skipped"] == [{"id": "j2", "reason": "already being made"}, {"id": "nope", "reason": "no posting stored"}]
    status = {j["id"]: j["status"] for j in c.get("/api/jobs", headers=H).json()["jobs"]}
    assert status["j1"] == status["j2"] == "In progress"
    # the ones still waiting can be dropped; the one going carries on
    assert c.post("/api/runs/resumes/stop-waiting", headers=H).json() == {"stopped": 1}
    assert not runs.waiting and runs.runs[solo["id"]].status == "running"
    c.post(f"/api/runs/{solo['id']}/stop", headers=H)
    runs.wait(solo["id"])


def test_a_tailoring_run_puts_its_job_in_progress_at_once(env):
    c, runs = env["client"], env["runs"]
    runs.runs.clear()
    runs.argv_prefix = [sys.executable, "-c", "import time; time.sleep(5)"]
    status = lambda: {j["id"]: j["status"] for j in c.get("/api/jobs", headers=H).json()["jobs"]}
    assert status()["j1"] == "Not started" and status()["j2"] == ""       # j2 isn't tracked yet
    ids = [c.post("/api/runs", headers=H, json={"kind": "tailor", "job_id": jid}).json()["id"] for jid in ("j1", "j2")]
    assert status()["j1"] == status()["j2"] == "In progress"
    for rid in ids:
        c.post(f"/api/runs/{rid}/stop", headers=H)
        runs.wait(rid)


def test_overlapping_runs_keep_their_own_results(env, monkeypatch):
    c, runs, store = env["client"], env["runs"], env["store"]
    runs.runs.clear()
    runs.argv_prefix = [sys.executable, "-c", "import time; time.sleep(5)"]
    a = c.post("/api/runs", headers=H, json={"kind": "tailor", "job_id": "j1"}).json()["id"]
    b = c.post("/api/runs", headers=H, json={"kind": "tailor", "job_id": "j2"}).json()["id"]
    for rid, jid in ((a, "j1"), (b, "j2")):          # what each run's process does to the store
        monkeypatch.setenv("JOBPIPE_RUN_ID", str(rid))
        store.update(jid, triage={"fit_score": 9}, triaged_at="2026-10-02")
    monkeypatch.delenv("JOBPIPE_RUN_ID")
    for rid in (a, b):
        c.post(f"/api/runs/{rid}/stop", headers=H)
        runs.wait(rid)
    assert [x["id"] for x in c.get(f"/api/runs/{a}", headers=H).json()["results"]] == ["j1"]
    assert [x["id"] for x in c.get(f"/api/runs/{b}", headers=H).json()["results"]] == ["j2"]


def test_a_job_scored_in_the_app_during_a_run_is_not_the_runs(env, monkeypatch):
    c, runs, store = env["client"], env["runs"], env["store"]
    runs.runs.clear()
    runs.argv_prefix = [sys.executable, "-c", "import time; time.sleep(5)"]
    rid = c.post("/api/runs", headers=H, json={"kind": "tailor", "job_id": "j1"}).json()["id"]
    monkeypatch.setenv("JOBPIPE_RUN_ID", str(rid))
    store.update("j1", triage={"fit_score": 9}, triaged_at="2026-10-02")
    store.update("j2", triage={"fit_score": 8}, triaged_at="2026-10-02")    # an earlier run touched it...
    monkeypatch.delenv("JOBPIPE_RUN_ID")
    store.update("j2", triage={"fit_score": 7}, triaged_at="2026-10-03")    # ...then the app scored it again
    c.post(f"/api/runs/{rid}/stop", headers=H)
    runs.wait(rid)
    assert [x["id"] for x in c.get(f"/api/runs/{rid}", headers=H).json()["results"]] == ["j1"]


def test_confirmed_facts(env):
    c, impact = env["client"], env["impact"]
    v = c.get("/api/impact-record", headers=H).json()["version"]
    assert c.post("/api/impact-record/facts", headers=H, json={"text": " ", "version": v}).status_code == 400
    d = c.post("/api/impact-record/facts", headers=H,
               json={"text": "At Fabrikam I didn't\nmanage the vendor team.", "version": v}).json()
    assert d["markdown"] == impact.read_text() and d["version"] != v
    assert impact.read_text().startswith("# Impact record\n\n## Northwind\n\nLed 22 teams.\n\n# Confirmed facts\n\n")
    assert impact.read_text().endswith(", user-confirmed:** At Fabrikam I didn't manage the vendor team.\n")
    assert list((env["store"].root / "impact-record-history").glob("*.md"))      # the text it replaced is kept
    # a record changed since the app opened it isn't added to unseen
    r = c.post("/api/impact-record/facts", headers=H, json={"text": "x y z", "version": v})
    assert r.status_code == 409 and r.json()["current"]["version"] == d["version"]


def test_add_confirmed_fact():
    from jobpipe.web.server import add_confirmed_fact
    day = date(2026, 10, 6)
    item = "- **Oct 6, 2026, user-confirmed:** New one."
    # into the existing section, before the next top-level heading
    md = "# Work\n\nLed it.\n\n# Confirmed facts\n\nIntro.\n\n- **Sep 1, 2026, user-confirmed:** Old.\n\n\n# Bottom line\n\nEnd.\n"
    assert add_confirmed_fact(md, "New one.", day, "Jordan") == md.replace("Old.\n\n\n", f"Old.\n{item}\n\n")
    # a section with no list yet, at the end; a "# Confirmed facts" inside a code block doesn't count
    assert add_confirmed_fact("# Confirmed facts\n\nIntro.", "New one.", day, "Jordan") == f"# Confirmed facts\n\nIntro.\n\n{item}\n"
    out = add_confirmed_fact("# Work\n\n```\n# Confirmed facts\n```\n", "New one.", day, "Jordan")
    assert out.endswith(f"```\n\n# Confirmed facts\n\nAnswers Jordan gave to follow-up questions. Every score, tailoring run "
                        f"and résumé edit treats them as user-confirmed evidence, at exactly the scope stated.\n\n{item}\n")


def test_loosen_lists_and_run_scores(tmp_path):
    from jobpipe.web.server import loosen_lists, run_scores
    assert loosen_lists("Intro:\n- a\n- b\n\n| t |\n") == "Intro:\n\n- a\n- b\n\n| t |"
    (tmp_path / "ratings-final.json").write_text(json.dumps({"scores": [8, 6, 7, 9]}))
    (tmp_path / "scorecard-final.md").write_text("| Source | S | E | K | Total |\n| Tailored resume | 96% | 79% | 74% | 83% PASS |\n")
    assert run_scores(tmp_path) == {"impact_score": 8.0, "resume_score": 9.0, "ats_total": 83.0}


def test_track_and_dismiss_found_jobs(env):
    c, notion = env["client"], env["notion"]
    jobs = {j["id"]: j for j in c.get("/api/jobs", headers=H).json()["jobs"]}
    assert jobs["j2"]["notion_page_id"] is None and jobs["j2"]["dismissed"] is False

    assert c.post("/api/jobs/j2/dismiss", headers=H, json={"dismissed": True}).json() == {"ok": True}
    assert {j["id"]: j for j in c.get("/api/jobs", headers=H).json()["jobs"]}["j2"]["dismissed"] is True

    r = c.post("/api/jobs/j2/track", headers=H).json()
    assert r["action"] == "created"
    j2 = {j["id"]: j for j in c.get("/api/jobs", headers=H).json()["jobs"]}["j2"]
    assert j2["tracker_id"] and j2["status"] == "Not started" and j2["dismissed"] is False     # tracked at once, locally
    c.get("/api/jobs?refresh=1", headers=H)                                                     # then copied to Notion
    created = next(row["_create"]["properties"] for row in notion.rows.values() if "_create" in row)
    assert created["Name"]["title"][0]["text"]["content"] == f"{TITLE} — Beta"
    assert created["Status"]["status"]["name"] == "Not started" and created["Fit Score"]["number"] == 5.0
    assert created["Notes"]["rich_text"][0]["text"]["content"].startswith("Scored ")
    j2 = {j["id"]: j for j in c.get("/api/jobs?refresh=1", headers=H).json()["jobs"]}["j2"]
    assert j2["notion_page_id"] and j2["status"] == "Not started" and j2["pending"] is False

    # a tailored job already in Notion: Track updates its row, Not started -> In progress
    assert c.post("/api/jobs/j1/track", headers=H).json()["action"] == "updated"
    c.get("/api/jobs?refresh=1", headers=H)
    assert notion.rows["p1"]["properties"]["Status"]["status"]["name"] == "In progress"
    assert c.post("/api/jobs/nope/track", headers=H).status_code == 404


def test_run_output_streams_while_running():
    runs = RunManager(argv_prefix=[sys.executable, "-c", "import time; print('first'); time.sleep(1.5); print('second')"])
    run = runs.start("stream", [])
    import time
    for _ in range(50):              # the first line arrives long before the process ends
        if run.lines:
            break
        time.sleep(0.05)
    assert run.lines == ["first"] and run.status == "running"
    runs.wait(run.id)
    assert run.lines == ["first", "second"] and run.status == "done"


def test_failed_tailoring_exits_with_an_error(monkeypatch, capsys):
    import jobpipe.pipeline
    from jobpipe import cli

    class Failing:
        runner = None

        def __init__(self, *a, **k):
            pass

        async def tailor_job(self, job):
            from types import SimpleNamespace
            return SimpleNamespace(tailored=None, error="tailoring failed: the writer returned nothing")

    monkeypatch.setattr(jobpipe.pipeline, "Pipeline", Failing)
    monkeypatch.setattr(Store, "job", lambda self, jid: {"id": jid, "job_title": "TPM", "company": "Acme"})
    with pytest.raises(SystemExit) as e:
        cli.main(["tailor", "j1"])
    assert "the writer returned nothing" in str(e.value.code)


def test_profile_edit(env):
    c, searches = env["client"], env["searches"]
    p = c.get("/api/profile", headers=H).json()
    assert p["saved"] and p["example"] and p["candidate"]["name"] == "Jordan Rivera"
    assert [f["file"] for f in p["files"]] == ["resume-platform.md"] and p["files"][0]["words"] > 500
    assert p["impact_record"]["words"] == 6                         # the env's impact record

    body = {"name": "Sam  Lee", "pronouns": "she/her", "city": "Austin, TX", "phone": "+1 555-010-0142",
            "email": "sam@example.com", "linkedin": "https://www.linkedin.com/in/sam-lee/", "pdf_prefix": "",
            "evidence": "", "resumes": []}
    r = c.put("/api/profile", headers=H, json=body)
    assert r.status_code == 400 and "at least one résumé" in r.json()["detail"]
    r = c.put("/api/profile", headers=H, json={**body, "linkedin": "jordan.example.com",
                                               "resumes": [{"name": "Platform resume", "file": "resume-platform.md"}]})
    assert r.status_code == 400 and "LinkedIn" in r.json()["detail"]
    r = c.put("/api/profile", headers=H, json={**body, "resumes": [{"name": "X", "file": "../impact-record.md"}]})
    assert r.status_code == 400

    env["runs"].active = lambda: object()                          # a run is going
    r = c.put("/api/profile", headers=H, json={**body, "resumes": [{"name": "Platform resume", "file": "resume-platform.md"}]})
    assert r.status_code == 409
    del env["runs"].active

    r = c.put("/api/profile", headers=H, json={**body, "resumes": [{"name": "Platform resume", "file": "resume-platform.md"}]})
    assert r.status_code == 200, r.text
    assert not r.json()["example"]                                   # saving makes it yours
    text = searches.read_text()
    assert "# The contact line every résumé must carry." in text and "example:" not in text
    cand = config.parse_candidate(config.yaml.safe_load(text)["candidate"])
    assert (cand.name, cand.pronouns, cand.linkedin) == ("Sam Lee", "she/her", "linkedin.com/in/sam-lee")
    assert cand.pdf_prefix == "Sam-Lee-Resume" and cand.resumes == {"Platform resume": "resume-platform.md"}
    assert cand.evidence == config.Candidate(name="x").evidence      # blank: the generic default
    assert config.load(searches).search("tpm-remote")                # the searches are untouched


def test_profile_resume_upload(env):
    c = env["client"]
    up = lambda **kw: c.post("/api/profile/resumes", headers=H, json={"filename": "Platform Leadership.md",
                                                                       "markdown": "# Jordan Rivera\n**Staff TPM**\n", **kw})
    r = up()
    assert r.status_code == 200 and r.json()["file"] == "resume-platform-leadership.md"
    assert (config.REFERENCES / "resume-platform-leadership.md").read_text().startswith("# Jordan Rivera")
    assert "resume-platform-leadership.md" in [f["file"] for f in r.json()["files"]]
    assert up().status_code == 409
    assert up(replace=True, markdown="# Jordan Rivera\n**Director**\n").status_code == 200
    assert c.post("/api/profile/resumes", headers=H, json={"filename": "cv.pdf", "markdown": "x"}).status_code == 400


def test_impact_record_edit_and_save(env):
    c, path = env["client"], env["impact"]
    assert c.get("/api/impact-record").status_code == 401
    d = c.get("/api/impact-record", headers=H).json()
    assert d["markdown"] == path.read_text() and d["version"] and d["updated"]

    new = d["markdown"] + "\n## Northwind\n\nRan partner onboarding.\n"
    saved = c.put("/api/impact-record", headers=H, json={"markdown": new, "version": d["version"]}).json()
    assert path.read_text() == new and saved["version"] != d["version"]
    # The text it replaced is kept, once per stretch of editing.
    snaps = list((env["store"].root / "impact-record-history").glob("*.md"))
    assert [s.read_text() for s in snaps] == [d["markdown"]]
    again = c.put("/api/impact-record", headers=H, json={"markdown": new + "More.\n", "version": saved["version"]}).json()
    assert len(list(snaps[0].parent.glob("*.md"))) == 1

    # Changed outside the window: the save is refused with what's on disk, unless forced.
    path.write_text("# Edited in another editor\n")
    r = c.put("/api/impact-record", headers=H, json={"markdown": "# Mine\n", "version": again["version"]})
    assert r.status_code == 409 and r.json()["current"]["markdown"] == "# Edited in another editor\n"
    assert path.read_text() == "# Edited in another editor\n"
    r = c.put("/api/impact-record", headers=H, json={"markdown": "# Mine\n", "version": again["version"], "force": True})
    assert r.status_code == 200 and path.read_text() == "# Mine\n"

    assert c.put("/api/impact-record", headers=H, json={"markdown": "  \n", "version": r.json()["version"]}).status_code == 400
    assert path.read_text() == "# Mine\n"


def test_interview_prep_notes(env):
    c, path = env["client"], env["store"].root / "interview-prep.md"
    assert c.get("/api/interview-prep").status_code == 401
    assert not path.exists()
    # First open writes the starter, in data/ where Claude can find it and add to it.
    d = c.get("/api/interview-prep", headers=H).json()
    assert path.read_text() == d["markdown"] and d["markdown"].startswith("# Interview prep") and d["path"] == str(path)
    assert "## Stories to find" in d["markdown"]

    new = d["markdown"] + "- [ ] A time I pushed back on a VP\n"
    saved = c.put("/api/interview-prep", headers=H, json={"markdown": new, "version": d["version"]}).json()
    assert path.read_text() == new
    assert [s.read_text() for s in (env["store"].root / "interview-prep-history").glob("*.md")] == [d["markdown"]]

    # Claude adds a note while the tab is open: the stale save is refused with the new text.
    path.write_text(new + "- [ ] Numbers for the Northwind migration\n")
    r = c.put("/api/interview-prep", headers=H, json={"markdown": new + "Mine\n", "version": saved["version"]})
    assert r.status_code == 409 and "Northwind migration" in r.json()["current"]["markdown"]

    # Unlike the impact record, it can be cleared.
    r = c.put("/api/interview-prep", headers=H, json={"markdown": "", "version": r.json()["current"]["version"]})
    assert r.status_code == 200 and path.read_text() == ""
    assert c.get("/api/interview-prep", headers=H).json()["markdown"] == ""

    html = c.post("/api/interview-prep/preview", headers=H, json={"markdown": "- [ ] find\n- [x] done\n"}).json()["html"]
    assert '<li class="task">☐ find</li>' in html and '<li class="task">☑ done</li>' in html


def test_impact_record_preview_escapes_html(env):
    html = env["client"].post("/api/impact-record/preview", headers=H,
                              json={"markdown": "## Hi\n\n<script>alert(1)</script>\n\n- a\n- b\n"}).json()["html"]
    assert "<h2>Hi</h2>" in html and "<script>" not in html and "<li>a</li>" in html



def test_search_once_runs_the_dialogs_filter_without_saving_it(env):
    import ast
    c, runs, searches = env["client"], env["runs"], env["searches"]
    before = searches.read_text()
    spec = {"name": "Director, remote", "titles": ["Director of Engineering", ""], "remote": True, "limit": "3",
            "min_salary_usd": "", "id": "tpm-remote"}
    r = c.post("/api/runs", headers=H, json={"kind": "once", "search": spec})
    assert r.status_code == 200 and r.json()["label"] == "Search once: Director of Engineering + signal score"
    runs.wait(r.json()["id"])
    args = ast.literal_eval(c.get(f"/api/runs/{r.json()['id']}", headers=H).json()["lines"][0][len("args "):])
    assert args[:3] == ["run", "--no-tailor", "--search-json"]
    assert json.loads(args[3]) == {"name": "Director, remote", "titles": ["Director of Engineering"], "remote": True, "limit": 3}
    assert searches.read_text() == before                              # nothing saved

    r = c.post("/api/runs", headers=H, json={"kind": "once", "search": {"titles": ["TPM"]}, "score": False})
    runs.wait(r.json()["id"])
    assert "'search', '--search-json'" in c.get(f"/api/runs/{r.json()['id']}", headers=H).json()["lines"][0]
    r = c.post("/api/runs", headers=H, json={"kind": "once", "search": {"titles": []}})
    assert r.status_code == 400 and "titles" in r.json()["detail"]


def test_one_off_search_from_the_command_line(capsys):
    from jobpipe import cli
    cli.main(["show-request", "--search-json", json.dumps({"titles": ["Director of Engineering"], "limit": 3})])
    body = json.loads(capsys.readouterr().out.split("\n", 1)[1])
    assert body["job_title_or"] == ["Director of Engineering"] and body["limit"] == 3
    defaults = config.load().defaults
    assert body["min_salary_usd"] == defaults["min_salary_usd"] and body["job_title_not"] == defaults["exclude_titles"]


def test_credit_ledger(env):
    c = env["client"]
    st = Store()
    st.record_call(search_id="tpm-remote", body={"limit": 10}, charged=4, returned=6, already_paid=2, status="ok")
    st.record_call(search_id="one-off", body={"limit": 3}, charged=None, returned=3, already_paid=None, status="ok")
    st.record_call(search_id="gone", body={}, charged=None, returned=0, already_paid=None, status="400: bad field")
    d = c.get("/api/credits", headers=H).json()
    assert (d["used"], d["left"], d["allowance"]) == (7, d["allowance"] - 7, env["cfg"].allowance_amount)
    assert [(x["name"], x["counted"]) for x in d["calls"]] == [("gone", 0), ("Search once", 3), ("TPM, remote, $200k+", 4)]
    assert d["calls"][2]["body"] == {"limit": 10} and d["months"][0]["credits"] == 7
    reqs = c.get("/api/searches/requests", headers=H).json()
    assert reqs["tpm-remote"]["remote"] is True


def test_a_job_is_tailored_by_one_process_at_a_time(tmp_path):
    from jobpipe.store import Store
    store = Store(tmp_path)
    with store.tailoring("j1") as first:
        # A second process (here, a second open of the lock file) is turned away while the first holds it.
        with store.tailoring("j1") as second, store.tailoring("j2") as other:
            assert (first, second, other) == (True, False, True)
    with store.tailoring("j1") as again:
        assert again


# ---- startups ---------------------------------------------------------------------------------------
def test_startups_tab_roles_into_find_jobs_and_the_tracker(env, monkeypatch):
    from jobpipe import startups as su
    c = env["client"]
    store = su.StartupStore(config.DATA / "startups.json")
    store.merge([{"name": "Acme", "website": "https://acme.com", "stage": "Series B", "one_liner": "AI for banks", "hq": "New York, NY",
                  "round": {"name": "Series B", "stage": "Series B", "amount": 2e7, "currency": "$", "amount_usd": 2e7, "date": "2026-10-01",
                            "headline": "Acme Raises $20M in Series B Funding", "url": "https://news.example/acme", "source": "FinSMEs"},
                  "sources": [{"kind": "news", "name": "FinSMEs", "url": "https://news.example/acme", "at": "2026-10-01"}]},
                 {"name": "Zeta", "website": "https://zeta.example", "batch": "Summer 2026", "hiring": True, "yc_stage": "Early"}])
    d = c.get("/api/startups", headers=H).json()
    assert d["count"] == 2 and d["recent"] == 1 and d["lookup"] is False and "Technical Program Manager" in d["phrases"]
    by = {s["id"]: s for s in d["startups"]}
    assert by["acme"]["line"] == "Series B · $20M · Oct 2026 (FinSMEs)" and by["acme"]["round"]["amount"] == "$20M"
    assert by["zeta"]["batch"] == "S26" and by["zeta"]["stage"] == "Unknown" and by["zeta"]["roles"] is None
    # the job already stored at Acme (j1, from a filter) now carries Acme's round, by company name
    rows = {j["id"]: j for j in c.get("/api/jobs", headers=H).json()["jobs"]}
    assert rows["j1"]["funding"]["stage"] == "Series B" and rows["j1"]["funding"]["line"].startswith("Series B · $20M")
    assert rows["j2"]["funding"] is None
    store.merge([{"name": "Beta", "stage": "Growth", "getro_stage": "ipo", "exited": "public"}])      # j2's company went public: no chip
    assert {j["id"]: j for j in c.get("/api/jobs", headers=H).json()["jobs"]}["j2"]["funding"] is None
    # read Acme's open roles (its board is mocked), add the matching one, and it lands in Find jobs with the round
    roles = {"board": {"kind": "greenhouse", "board": "acme", "url": "https://boards.greenhouse.io/acme"}, "careers_url": "", "checked": "2026-10-04T00:00:00+00:00",
             "jobs": [{"title": "Staff Technical Program Manager", "url": "https://boards.greenhouse.io/acme/jobs/77", "location": "New York (Remote)",
                       "remote": True, "description": "Run the platform programs. " * 40, "posted": "2026-10-01", "id": "77", "match": True},
                      {"title": "Sales Lead", "url": "https://boards.greenhouse.io/acme/jobs/78", "location": "Austin", "remote": False,
                       "description": "Sell. " * 100, "posted": "2026-10-01", "id": "78", "match": False}]}
    monkeypatch.setattr(su, "open_roles", lambda s, phrases, *a, **k: roles)
    d = c.post("/api/startups/acme/roles", headers=H).json()
    assert d["board"]["kind"] == "greenhouse" and [j["job_id"] for j in d["roles"]["jobs"]] == [None, None]
    assert c.post("/api/startups/acme/roles/add", headers=H, json={"url": "https://nope"}).status_code == 404
    r = c.post("/api/startups/acme/roles/add", headers=H, json={"url": "https://boards.greenhouse.io/acme/jobs/77"}).json()
    assert r == {"id": "startup-acme-staff-technical-program-manager-77", "existing": False}
    assert c.post("/api/startups/acme/roles/add", headers=H, json={"url": "https://boards.greenhouse.io/acme/jobs/77"}).json()["existing"]
    rows = {j["id"]: j for j in c.get("/api/jobs", headers=H).json()["jobs"]}
    job = rows[r["id"]]
    assert job["funding"]["stage"] == "Series B" and job["remote"] and job["searches"] == ["startups"] and not job["tracker_id"]
    acme = next(s for s in c.get("/api/startups", headers=H).json()["startups"] if s["id"] == "acme")
    assert acme["roles"]["jobs"][0]["job_id"] == r["id"]
    # tracking it writes the round into the tracker's notes and the Notion page body
    assert c.post(f"/api/jobs/{r['id']}/track", headers=H).json()["action"] == "created"
    c.get("/api/jobs?wait=1", headers=H)
    created = [b for m, p, b in env["notion"].requests if m == "POST" and p.endswith("/pages")]
    mine = next(b for b in created if "jobs/77" in b["properties"]["Job URL"]["url"])
    assert "Series B · $20M · Oct 2026 (FinSMEs)" in mine["properties"]["Notes"]["rich_text"][0]["text"]["content"]
    body = json.dumps(mine.get("children", []), ensure_ascii=False)
    assert "Funding:" in body and "Series B · $20M · Oct 2026 (FinSMEs)" in body
    # tracking the company itself: an "Open roles — Zeta" row linked to its careers page, with what's known
    r = c.post("/api/startups/zeta/track", headers=H).json()
    assert r["action"] == "created" and r["tracker_id"]
    rows = {j["id"]: j for j in c.get("/api/jobs", headers=H).json()["jobs"]}
    row = rows[r["tracker_id"]] if r["tracker_id"] in rows else next(j for j in rows.values() if j["tracker_id"] == r["tracker_id"])
    assert row["title"] == "Open roles" and row["company"] == "Zeta" and row["status"] == "Not started" and row["url"] == "https://zeta.example"
    assert row["funding"]["batch"] == "S26" and row["funding"]["line"] == "YC S26"
    assert next(s for s in c.get("/api/startups", headers=H).json()["startups"] if s["id"] == "zeta")["tracked"] == r["tracker_id"]
    # dismiss, add by hand, unknown ids, and the refresh run
    assert c.patch("/api/startups/zeta", headers=H, json={"dismissed": True}).json()["dismissed"] is True
    assert c.get("/api/startups", headers=H).json()["count"] == 3
    assert c.post("/api/startups", headers=H, json={"name": " "}).status_code == 400
    s = c.post("/api/startups", headers=H, json={"name": "Nova Labs", "website": "nova.example"}).json()
    assert s["id"] == "nova-labs" and s["website"] == "https://nova.example" and s["sources"][0]["kind"] == "manual"
    assert c.post("/api/startups/nope/roles", headers=H).status_code == 404
    assert "no lookup key" in c.post("/api/startups/nova-labs/enrich", headers=H).json()["detail"]
    run = c.post("/api/startups/refresh", headers=H).json()
    assert run["label"] == "Refresh startup sources" and run["args"] == ["startups", "refresh"]
    assert c.post("/api/startups/refresh", headers=H).status_code == 409
    env["runs"].wait(run["id"])
    sm = c.get("/api/summary", headers=H).json()["startups"]
    assert sm["count"] == 4 and sm["hiring"] == 1 and sm["auto_refresh_hours"] == 24        # Acme has a matching role
    # the app's own timer: a refresh is due while the sources have never been read, not once they just were
    assert env["app"].state.auto_refresh().label == "Refresh startup sources"
    env["runs"].wait(env["runs"].active().id) if env["runs"].active() else None
    su.StartupStore(config.DATA / "startups.json").update_meta(refreshed=now_iso_for_test())
    assert env["app"].state.auto_refresh() is None


def now_iso_for_test():
    from jobpipe.store import now_iso
    return now_iso()


def test_ai_settings(env, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)              # restored after the test
    c, searches = env["client"], env["searches"]
    a = c.get("/api/ai", headers=H).json()
    assert a["backend"] == "claude-code" and a["stages"]["writer"] == {"model": "claude-opus-5-5", "effort": "high"}
    assert a["stages"]["rescore"] == {"model": "claude-sonnet-5-5", "effort": "medium"}
    assert a["stages"]["chat"] == {"model": "claude-sonnet-5-5", "effort": "medium"}       # not the writer's Opus at high
    assert {p["id"] for p in a["providers"]} >= {"claude-code", "api", "openai", "gemini", "ollama"}
    openrouter = next(p for p in a["providers"] if p["id"] == "openrouter")         # not whatever sorts first
    assert openrouter["defaults"]["writer"] == "anthropic/claude-opus-5.5"
    assert openrouter["defaults"]["analysis"] == "anthropic/claude-sonnet-5.5"
    assert openrouter["defaults"]["chat"] == "anthropic/claude-sonnet-5.5"
    assert "claude-haiku-5-5" in a["claude_models"]
    assert c.post("/api/ai/models", headers=H, json={"backend": "claude-code"}).json()["models"][0] == "claude-opus-5-5"
    assert c.post("/api/ai/models", headers=H, json={"backend": "openai"}).status_code == 400   # no key yet

    stages = {s: {"model": "gpt-x", "effort": "medium"} for s in ("triage", "analysis", "writer")}
    assert c.put("/api/ai", headers=H, json={"backend": "openai", "stages": {**stages, "writer": {"model": ""}}}).status_code == 400
    assert c.put("/api/ai", headers=H, json={"backend": "openai-compatible", "stages": stages}).status_code == 400  # no address
    monkeypatch.setattr(env["runs"], "active", lambda: object())   # saved while a run is going: it keeps its own AI
    a = c.put("/api/ai", headers=H, json={"backend": "openai", "stages": stages, "key": "sk-new"}).json()
    monkeypatch.setattr(env["runs"], "active", lambda: None)
    assert a["backend"] == "openai" and next(p for p in a["providers"] if p["id"] == "openai")["key_set"]
    text = searches.read_text()
    assert "backend: openai" in text and "writer: {model: gpt-x, effort: medium}" in text
    assert "# Which AI does the work" in text                   # comments kept
    assert "OPENAI_API_KEY=sk-new" in (config.PROFILE / ".env").read_text()
    assert oct((config.PROFILE / ".env").stat().st_mode & 0o777) == "0o600"
    assert config.load(searches).writer_model == config.ModelCfg("gpt-x", "medium")
    # a page without the live-score row: OpenAI has no Sonnet, so it takes the quick-score model
    assert config.load(searches).rescore_model == config.ModelCfg("gpt-x", "medium") == env["cfg"].rescore_model
    # nor the chat row: OpenAI has no Sonnet, so it takes the writing model, at medium effort
    assert config.load(searches).chat_model == config.ModelCfg("gpt-x", "medium") == env["cfg"].chat_model
    assert "chat: {model: gpt-x, effort: medium}" in searches.read_text()
    assert c.get("/api/summary", headers=H).json()["backend_label"] == "ChatGPT (OpenAI API)"
    a = c.put("/api/ai", headers=H, json={"backend": "ollama", "base_url": "http://127.0.0.1:11434/v1", "stages": stages}).json()
    assert a["base_url"] == "http://127.0.0.1:11434/v1" and "OPENAI_API_KEY=sk-new" in (config.PROFILE / ".env").read_text()
    # each AI's picks are remembered, so switching back to one restores them (Claude's from before the first switch too)
    assert a["saved"]["openai"]["stages"]["writer"] == {"model": "gpt-x", "effort": "medium"}
    assert a["saved"]["claude-code"]["stages"]["writer"] == {"model": "claude-opus-5-5", "effort": "high"}
    assert a["saved"]["ollama"]["base_url"] == "http://127.0.0.1:11434/v1"
    assert any(p["id"] == "kimi" and p["key_env"] == "MOONSHOT_API_KEY" for p in a["providers"])


def test_the_chat_has_its_own_model_and_a_message_can_still_pick_another(env, monkeypatch):
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "MOONSHOT_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)                    # only Claude in the menu: no provider is asked
    c, runner = env["client"], env["runner"]
    chats = lambda: [x for x in runner.calls if x.label == "resume-edit"]
    m = c.get("/api/ai/chat-models", headers=H).json()
    assert m["default"] == {"backend": "claude-code", "model": "claude-sonnet-5-5"}
    assert m["groups"][0]["models"][:2] == ["claude-sonnet-5-5", "claude-opus-5-5"]     # the chat's, then the writer's
    c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "What's weakest?"})
    assert chats()[-1].model == config.ModelCfg("claude-sonnet-5-5", "medium")
    r = c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "And the skills?", "backend": "claude-code",
                                                      "model": "claude-opus-5-5"}).json()
    assert chats()[-1].model == config.ModelCfg("claude-opus-5-5", "medium")             # the menu's pick, the chat's effort
    assert r["chat"][-1]["via"].endswith("claude-opus-5-5")

    stages = c.get("/api/ai", headers=H).json()["stages"]                            # set on the Profile tab
    a = c.put("/api/ai", headers=H, json={"backend": "claude-code",
                                          "stages": {**stages, "chat": {"model": "claude-haiku-5-5", "effort": "low"}}})
    assert a.status_code == 200 and "chat: {model: claude-haiku-5-5, effort: low}" in env["searches"].read_text()
    c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "Too long?"})
    assert chats()[-1].model == config.ModelCfg("claude-haiku-5-5", "low")
    assert c.get("/api/ai/chat-models", headers=H).json()["default"]["model"] == "claude-haiku-5-5"
    assert config.load(env["searches"]).writer_model == config.ModelCfg("claude-opus-5-5", "high")   # writing unchanged

    # a profile saved before the chat had its own model: Sonnet on Claude, the writing model elsewhere
    text = env["searches"].read_text()
    env["searches"].write_text("\n".join(l for l in text.splitlines() if not l.lstrip().startswith("chat:")) + "\n")
    assert config.load(env["searches"]).chat_model == config.ModelCfg("claude-sonnet-5-5", "medium")
    assert c.get("/api/ai", headers=H).json()["stages"]["chat"] == {"model": "claude-sonnet-5-5", "effort": "medium"}
    assert config.chat_default("kimi", {"model": "kimi-k3"}) == {"model": "kimi-k3", "effort": "medium"}


def test_claude_sign_in_helper(env, monkeypatch):
    import subprocess
    from jobpipe import llm
    c = env["client"]

    def missing():
        raise RuntimeError("Claude Code CLI not found.")
    monkeypatch.setattr(llm, "find_claude", missing)
    assert c.get("/api/ai/claude", headers=H).json() == {"found": None, "error": "Claude Code CLI not found."}
    assert "Install the Claude app" in c.post("/api/ai/claude-login", headers=H).json()["detail"]

    monkeypatch.setattr(llm, "find_claude", lambda: "/Apps/Claude Code/claude")
    opened = []
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: opened.append(args))
    monkeypatch.setattr(sys, "platform", "darwin")
    assert c.post("/api/ai/claude-login", headers=H).json() == {"opened": True}
    script = config.DATA / "claude-sign-in.command"
    assert opened == [["open", "-a", "Terminal", str(script)]]
    assert "exec '/Apps/Claude Code/claude'" in script.read_text() and script.stat().st_mode & 0o100


def test_other_keys(env, monkeypatch):
    monkeypatch.delenv("JOBSPIPE_API_KEY", raising=False)
    c = env["client"]
    k = {x["name"]: x for x in c.get("/api/keys", headers=H).json()["keys"]}
    assert not k["JOBSPIPE_API_KEY"]["set"] and k["NOTION_TOKEN"]["restart"]
    assert c.put("/api/keys", headers=H, json={"name": "PATH", "value": "x"}).status_code == 400
    k = {x["name"]: x for x in c.put("/api/keys", headers=H, json={"name": "JOBSPIPE_API_KEY", "value": "jp_live_1"}).json()["keys"]}
    assert k["JOBSPIPE_API_KEY"]["set"] and "jp_live_1" not in str(k)
    assert "JOBSPIPE_API_KEY=jp_live_1" in (config.PROFILE / ".env").read_text()


# ---- résumé formats --------------------------------------------------------------------------------------
LONG = "\n".join(f"- Led delivery review {k} across platform teams, cutting release risk with clear owners and dates."
                 for k in range(14))


def test_formats_are_listed_with_previews(env):
    c = env["client"]
    d = c.get("/api/formats", headers=H).json()
    assert d["default"] == "signature" and d["behind"] == []
    assert [f["id"] for f in d["formats"]] == ["signature", "executive", "modern", "minimal", "compact"]
    assert next(f for f in d["formats"] if f["id"] == "compact")["max_pages"] == 1
    r = c.get("/api/formats/modern/thumb.png", headers=H)
    assert r.status_code == 200 and r.content[:4] == b"\x89PNG"
    assert c.get("/api/formats/modern/preview.pdf", headers=H).content[:4] == b"%PDF"
    assert c.get("/api/formats/nope/thumb.png", headers=H).status_code == 404


def test_a_preview_that_cant_be_drawn_says_so_where_it_would_be(env, monkeypatch, capsys):
    """When the renderer can't start (a fresh Mac that won't run the bundled browser, say), the Profile shows a
    page that says so and why instead of the browser's broken-image mark, and the log has the traceback."""
    c = env["client"]
    start = render.Printer.__enter__

    def no_browser(self):
        raise RuntimeError("BrowserType.launch: spawn ENOENT\nCall log:\n  - <launching>")
    monkeypatch.setattr(render.Printer, "__enter__", no_browser)
    r = c.get("/api/formats/signature/thumb.png", headers=H)
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert r.headers["cache-control"] == "no-store"
    assert "This preview couldn't be drawn." in r.text and "spawn ENOENT" in r.text and "Call log" not in r.text
    err = capsys.readouterr().err
    assert "Couldn't draw the signature format's thumbnail" in err and "RuntimeError: BrowserType.launch: spawn ENOENT" in err
    # a format's live preview (the editor) and an unknown format
    r = c.post("/api/formats/own/preview", headers=H, json={"tokens": {}})
    assert r.status_code == 200 and "spawn ENOENT" in r.text
    assert c.get("/api/formats/nope/thumb.png", headers=H).status_code == 404
    # once the browser can start again, thumbnails are drawn as before
    monkeypatch.setattr(render.Printer, "__enter__", start)
    assert c.get("/api/formats/signature/thumb.png", headers=H).content[:4] == b"\x89PNG"


def test_a_new_default_format_redraws_the_resumes_already_made(env):
    c = env["client"]
    before = env["pdf"].read_bytes()
    d = c.put("/api/profile/format", headers=H, json={"format": "executive", "redraw": True}).json()
    assert (d["default"], d["redrawn"], d["over"], d["behind"]) == ("executive", 1, [], [])
    assert "resume_format: executive" in env["searches"].read_text()
    r = c.get("/api/jobs/j1", headers=H).json()["resume"]
    assert r["format"]["id"] == "executive" and r["versions"][-1]["format"] == "executive"
    assert env["pdf"].read_bytes() != before                      # the deliverable PDF is redrawn too
    assert c.put("/api/profile/format", headers=H, json={"format": "fancy"}).status_code == 400
    # Without redraw, résumés keep their format and are listed as behind
    d = c.put("/api/profile/format", headers=H, json={"format": "minimal"}).json()
    assert d["redrawn"] == 0 and [b["format"] for b in d["behind"]] == ["Executive"]
    r = c.post("/api/jobs/j1/resume/format", headers=H).json()
    assert r["applied"] and r["resume"]["format"]["id"] == "minimal"


def test_compact_keeps_a_resume_that_runs_long_until_a_trim_is_accepted(env):
    c, runner = env["client"], env["runner"]
    md = _resume().replace("\n\n**Fabrikam Games**", "\n" + LONG + "\n\n**Fabrikam Games**")
    (env["run_dir"] / "resume-final.md").write_text(md)
    d = c.put("/api/profile/format", headers=H, json={"format": "compact", "redraw": True}).json()
    assert d["redrawn"] == 0 and d["over"][0]["id"] == "j1" and d["over"][0]["pages"] >= 2
    assert d["behind"][0]["format"] == "Signature"
    r = c.post("/api/jobs/j1/resume/format", headers=H).json()
    assert not r["applied"] and r["trim_format"] == "compact" and "one page in the Compact format" in r["trim"]
    assert r["resume"]["format"]["id"] == "signature" and r["resume"]["default_format"]["id"] == "compact"
    # The trim is a chat message held to Compact: its proposal is drawn in Compact, and accepting it switches.
    p = c.post("/api/jobs/j1/edit", headers=H, json={"instruction": r["trim"], "format": "compact"}).json()["proposal"]
    call = runner.calls[-1]
    assert "fit one page in the Compact format" in call.instructions
    assert p["format"] == "compact" and p["pages"] == 1
    c.post("/api/jobs/j1/edit/accept", headers=H)
    r = c.get("/api/jobs/j1", headers=H).json()["resume"]
    assert r["format"]["id"] == "compact" and c.get("/api/formats", headers=H).json()["behind"] == []
    page = c.get("/api/jobs/j1/resume/page", headers=H).json()
    assert (page["margin_in"], page["max_pages"]) == (0.5, 1)
    assert c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "x", "format": "bogus"}).status_code == 400


# ---- uploading a résumé ----------------------------------------------------------------------------------
def _sorter(runner, md=None, notes="None.", fail=None):
    """Claude sorting an upload into the markdown shape."""
    pipeline_run = runner.pipeline.run

    async def run(call):
        if call.label == "resume-import":
            runner.calls.append(call)
            if fail:
                raise RuntimeError(fail)
            return AgentResult(files={"resume.md": md or _resume(), "notes.md": notes}, summary="")
        return await pipeline_run(call)
    runner.pipeline.run = run


def _upload(c, name, data):
    return c.post("/api/profile/resumes/import", headers={**H, "X-Filename": quote(name)}, content=data)


def test_upload_a_pdf_resume_review_it_and_save_it(env, tmp_path):
    from jobpipe import config as cfgmod
    c, runner = env["client"], env["runner"]
    _sorter(runner, notes="- Couldn't read the Woodgrove dates.")
    pdf = tmp_path / "Jordan Rivera Resume.pdf"
    render.render_pdf(_resume(), pdf)
    d = _upload(c, pdf.name, pdf.read_bytes()).json()
    call = next(x for x in runner.calls if x.label == "resume-import")
    assert "Northwind Cloud" in call.documents["resume.txt"] and call.candidate is False
    assert d["kind"] == "PDF" and d["has_original"] and d["name"] == "jordan-rivera" and d["kept"] > 90
    assert "<h2" in d["html"] and any(f["text"] == "Claude: Couldn't read the Woodgrove dates." for f in d["flags"])
    assert c.get(f"/api/profile/resumes/import/{d['id']}/original.pdf", headers=H).content[:4] == b"%PDF"
    # edited on the review screen: checked again against the file, no AI
    n = len(runner.calls)
    e = c.post(f"/api/profile/resumes/import/{d['id']}/check", headers=H,
               json={"markdown": d["markdown"].replace("Ran the ledger", "Spearheaded the ledger")}).json()
    assert "spearheaded" in e["added"] and len(runner.calls) == n
    saved = c.post("/api/profile/resumes", headers=H, json={"markdown": d["markdown"], "filename": "jordan-rivera.md",
                                                            "import_id": d["id"]}).json()
    assert saved["file"] == "resume-jordan-rivera.md"
    assert (cfgmod.REFERENCES / "originals" / "resume-jordan-rivera.pdf").read_bytes() == pdf.read_bytes()
    assert c.post(f"/api/profile/resumes/import/{d['id']}/check", headers=H, json={"markdown": "x"}).status_code == 404


def test_upload_a_word_file_and_markdown_already_in_shape(env):
    from test_resume_import import WORD_BODY, docx, para
    c, runner = env["client"], env["runner"]
    _sorter(runner)
    d = _upload(c, "cv.docx", docx(WORD_BODY, para("Jordan Rivera"))).json()
    assert d["kind"] == "Word file" and not d["has_original"]
    assert "Moved 31 payment services" in d["source"] and any("page header" in f["text"] for f in d["flags"])
    n = len(runner.calls)
    m = _upload(c, "resume-platform.md", _resume().encode()).json()       # nothing to sort
    assert len(runner.calls) == n and m["markdown"] == _resume() and m["kept"] == 100 and m["name"] == "platform"


def test_upload_falls_back_to_a_rough_sort_and_refuses_what_it_cant_read(env):
    c, runner = env["client"], env["runner"]
    _sorter(runner, fail="Claude isn't signed in")
    d = _upload(c, "cv.txt", b"Jordan Rivera\nProgram Manager\njordan@example.com\n\nEXPERIENCE\n- Led things\n").json()
    assert d["markdown"].startswith("# Jordan Rivera\n**Program Manager**")
    assert any("Claude couldn't sort it (Claude isn't signed in)" in f["text"] for f in d["flags"])
    r = _upload(c, "cv.doc", b"x")
    assert r.status_code == 400 and "save it as .docx" in r.json()["detail"]
    assert c.post("/api/profile/resumes/import/nothex/check", headers=H, json={"markdown": "x"}).status_code == 404


def test_upload_a_resume_for_a_job(env):
    c, store = env["client"], env["store"]
    _sorter(env["runner"])
    up = lambda name, md: _upload(c, name, md.encode()).json()

    # A job with the app's résumé: the upload is its next version, and the deliverable follows it.
    d = up("resume-acme.md", _resume().replace("Ran the ledger", "Owned the ledger"))
    h = c.post("/api/jobs/j1/resume/upload", headers=H, json={"import_id": d["id"], "markdown": d["markdown"]}).json()
    v = h["versions"][-1]
    assert h["added_version"] and h["current"] == 2 and (v["source"], v["changes"]) == ("upload", "Uploaded resume-acme.md.")
    assert "Owned the ledger" in (env["run_dir"] / "resume-final.md").read_text()
    assert c.post(f"/api/profile/resumes/import/{d['id']}/check", headers=H, json={"markdown": "x"}).status_code == 404

    # A job with no résumé: the upload becomes its own, offered for it alone, kept with the file it came from.
    d = up("my-cv.md", _resume())
    h = c.post("/api/jobs/notion-p9/resume/upload", headers=H, json={"import_id": d["id"], "markdown": d["markdown"]}).json()
    assert not h["added_version"] and h["current"] == 1 and h["versions"][0]["changes"] == "Uploaded my-cv.md."
    o = c.get("/api/jobs/notion-p9/outside-resume", headers=H).json()
    assert o["source"]["key"] == "upload" and o["source"]["label"] == "my-cv.md (uploaded for this role)" and not o["matched"]
    assert c.get("/api/jobs/notion-p9/resume/1.pdf", headers=H).content[:4] == b"%PDF"
    assert (next(store.root.glob("uploads/*/original.md"))).read_text() == _resume()
    assert "upload" not in [x["key"] for x in c.get("/api/jobs/j2/outside-resume", headers=H).json()["options"]]

    # Uploading again adds a version; after picking none, an upload starts afresh from the new file.
    d = up("my-cv-2.md", _resume().replace("Ran the ledger", "Led the ledger"))
    assert c.post("/api/jobs/notion-p9/resume/upload", headers=H, json={"import_id": d["id"], "markdown": d["markdown"]}).json()["current"] == 2
    c.post("/api/jobs/notion-p9/outside-resume", headers=H, json={"source": "none"})
    d = up("my-cv-3.md", _resume().replace("Ran the ledger", "Shipped the ledger"))
    h = c.post("/api/jobs/notion-p9/resume/upload", headers=H, json={"import_id": d["id"], "markdown": d["markdown"]}).json()
    assert h["current"] == 1 and "Shipped the ledger" in c.get("/api/jobs/notion-p9/resume/1.md", headers=H).json()["markdown"]
    assert c.post("/api/jobs/notion-p9/outside-resume", headers=H, json={"source": "upload"}).json()["source"]["key"] == "upload"

    # A résumé the app can't lay out, or an upload that's gone, is refused before anything is saved.
    d = up("bad.md", _resume())
    bad = c.post("/api/jobs/j1/resume/upload", headers=H, json={"import_id": d["id"], "markdown": "just words"})
    assert bad.status_code == 400 and "can't be laid out" in bad.json()["detail"]
    assert c.post("/api/jobs/j1/resume/upload", headers=H, json={"import_id": "0" * 16, "markdown": _resume()}).status_code == 404
    assert c.post("/api/jobs/nope/resume/upload", headers=H, json={"import_id": d["id"], "markdown": _resume()}).status_code == 404


# ---- formats of your own --------------------------------------------------------------------------------
def test_a_format_from_an_uploaded_resumes_look(env, tmp_path):
    from jobpipe import formats, own_formats
    c, runner = env["client"], env["runner"]
    _sorter(runner)
    pdf = tmp_path / "Navy CV.pdf"
    render.render_pdf(_resume(), pdf, fmt="executive")
    d = _upload(c, pdf.name, pdf.read_bytes()).json()
    assert d["look"]
    look = c.get(f"/api/profile/resumes/import/{d['id']}/look", headers=H).json()
    assert look["tokens"]["body_font"] == "Georgia" and look["tokens"]["accent"] == "#1F3A5F" and look["source"] == pdf.name
    assert c.get(f"/api/profile/resumes/import/{d['id']}/look.png", headers=H).content[:4] == b"\x89PNG"
    r = c.post("/api/formats/own", headers=H, json={"name": "Navy", "import_id": d["id"]}).json()
    assert r["created"] == "own-navy"
    own = next(f for f in r["formats"] if f["id"] == "own-navy")
    assert own["own"] and own["source"] == pdf.name and own["tokens"]["name_align"] == "center"
    assert "Body text: Georgia" in own["notes"]["found"]
    assert c.get("/api/formats/own-navy/thumb.png", headers=H).content[:4] == b"\x89PNG"
    # it can be the default, and résumés redraw in it
    d2 = c.put("/api/profile/format", headers=H, json={"format": "own-navy", "redraw": True}).json()
    assert d2["default"] == "own-navy" and d2["redrawn"] == 1
    assert c.get("/api/jobs/j1", headers=H).json()["resume"]["format"]["name"] == "Navy"
    # a Markdown upload has no look to read
    m = _upload(c, "r.md", _resume().encode()).json()
    assert not m["look"] and c.get(f"/api/profile/resumes/import/{m['id']}/look", headers=H).status_code == 400


def test_customise_edit_preview_and_delete_a_format_of_your_own(env):
    c = env["client"]
    r = c.post("/api/formats/own", headers=H, json={"name": "My Compact", "from": "compact"}).json()
    fid = r["created"]
    own = next(f for f in r["formats"] if f["id"] == fid)
    assert own["max_pages"] == 1 and own["tokens"]["skills_first"]
    assert c.post("/api/formats/own", headers=H, json={"name": "my compact", "from": "signature"}).status_code == 400
    r = c.put(f"/api/formats/own/{fid}", headers=H, json={"name": "Teal one-pager",
                                                          "tokens": {**own["tokens"], "accent": "#0E7C74", "body_size": 99}}).json()
    own = next(f for f in r["formats"] if f["id"] == fid)
    assert own["name"] == "Teal one-pager" and own["tokens"]["accent"] == "#0E7C74" and own["tokens"]["body_size"] == 11.5
    png = c.post("/api/formats/own/preview", headers=H, json={"tokens": {"accent": "#7A1F5C", "heading_style": "bar"}})
    assert png.status_code == 200 and png.content[:4] == b"\x89PNG"
    c.put("/api/profile/format", headers=H, json={"format": fid})
    r = c.delete(f"/api/formats/own/{fid}", headers=H).json()
    assert r["default"] == "signature" and fid not in [f["id"] for f in r["formats"]]
    assert "resume_format: signature" in env["searches"].read_text()
    assert c.delete(f"/api/formats/own/{fid}", headers=H).status_code == 404
    assert r["choices"]["fonts"][0] == "Helvetica Neue" and r["ranges"]["body_size"] == [9.5, 11.5]


def test_the_page_script_calls_only_functions_and_elements_that_exist():
    # A merge once dropped loadFormats() while showTab() still called it, and the Profile's format card went blank.
    import re
    from pathlib import Path
    static = Path(__file__).resolve().parent.parent / "jobpipe" / "web" / "static"
    js = (static / "app.js").read_text() + (static / "theme.js").read_text()
    called = set(re.findall(r"(?<![.\w])((?:load|render|open|show|apply|import|refresh|select|save)[A-Z]\w*)\(", js))
    defined = {a or b for a, b in re.findall(r"function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=", js)}
    assert called - defined == set()
    html = (static / "index.html").read_text()
    made = set(re.findall(r'\bid:\s*"([\w-]+)"', js))
    assert {i for i in re.findall(r'\$\("#([\w-]+)"\)', js) if f'id="{i}"' not in html and i not in made} == set()


# ---- from a résumé into the impact record -------------------------------------------------------------
def test_suggestions_from_a_resume_and_adding_them_to_the_impact_record(env):
    c = env["client"]
    env["impact"].write_text("# Impact record\n\n## Northwind Cloud\n\n- Ran the move of 31 payment services onto the "
                             "new ledger platform over 20 months, with no customer-facing downtime.\n")
    r = c.post("/api/impact-record/suggestions", headers=H, json={"markdown": _resume(), "source": "cv.pdf"}).json()
    assert r["source"] == "cv.pdf" and r["models"] is False and r["version"]
    roles = {x["company"]: x for x in r["roles"]}
    assert roles["Northwind Cloud"]["known"] and not roles["Woodgrove Bank"]["known"]
    assert roles["Northwind Cloud"]["lines"][0]["status"] == "detail"
    wb = roles["Woodgrove Bank"]
    d = c.post("/api/impact-record/suggestions/apply", headers=H, json={
        "version": r["version"], "company": wb["company"], "title": wb["title"], "dates": wb["dates"],
        "lines": [wb["lines"][0]["text"]], "source": "cv.pdf"}).json()
    assert "# From your résumés" in d["markdown"] and "## Woodgrove Bank, Quality Assurance Analyst" in d["markdown"]
    assert d["markdown"] == env["impact"].read_text()
    stale = c.post("/api/impact-record/suggestions/apply", headers=H, json={
        "version": r["version"], "company": "X", "title": "Y", "lines": ["z z z z"]})
    assert stale.status_code == 409                                   # the record changed since the suggestions
    again = c.post("/api/impact-record/suggestions/apply", headers=H, json={
        "version": d["version"], "company": wb["company"], "title": wb["title"], "lines": [wb["lines"][0]["text"]]})
    assert again.status_code == 400 and "already" in again.json()["detail"]
    saved = c.post("/api/impact-record/suggestions", headers=H, json={"file": "resume-platform.md"}).json()
    assert saved["source"] == "resume-platform.md" and saved["roles"]
    assert c.post("/api/impact-record/suggestions", headers=H, json={"file": "../searches.yaml"}).status_code == 400
