import asyncio
import json
import sqlite3
from types import SimpleNamespace

from jobpipe import config, lowfit
from jobpipe import startups as su
from jobpipe.jobs_api import JobsClient
from jobpipe.pipeline import Candidate, Pipeline
from jobpipe.store import Store
from conftest import FakeJobsAPI, FakeRunner, jobspipe_client, make_job
from test_web import H, env  # noqa: F401 - the web app fixture


def stored(store, fits: dict) -> None:
    for jid, fit in fits.items():
        store.save_job(make_job(jid), "tpm")
        if fit is not None:
            store.update(jid, triage={"fit_score": fit})


def test_dismisses_scores_at_or_below_the_setting_and_leaves_the_rest(tmp_dirs):
    store = Store()
    stored(store, {"two": 2, "four": 4, "five": 5, "tracked": 3, "starred": 3, "kept": 3, "unscored": None})
    store.update("kept", kept=True)
    with sqlite3.connect(store.root / "tracker.db") as con:
        con.execute("CREATE TABLE tracker (job_id TEXT, status TEXT)")
        con.execute("INSERT INTO tracker VALUES ('tracked', 'Not started')")
    (store.root / "marks.json").write_text(json.dumps({"starred": {"starred": True}}))
    lines = []
    assert lowfit.dismiss_low_fit(SimpleNamespace(dismiss_at_or_below_fit=None), store) == []
    out = lowfit.dismiss_low_fit(SimpleNamespace(dismiss_at_or_below_fit=4), store, log=lines.append)
    assert sorted(out) == ["four", "two"] and lines == [
        "Dismissed 2 low-fit job(s) (scored 4 or less). A job you restore won't be dismissed again."]
    st = store.state()
    assert st["two"]["dismissed"] and st["two"]["low_fit"]
    assert not any(st[j].get("dismissed") for j in ("five", "tracked", "starred", "kept", "unscored"))
    assert lowfit.dismiss_low_fit(SimpleNamespace(dismiss_at_or_below_fit=4), store) == []      # already dismissed


def test_a_search_run_dismisses_what_it_scores_low(tmp_dirs):
    cfg = config.load()
    cfg.dismiss_at_or_below_fit = 4
    store = Store()
    stored(store, {"low": None, "high": None, "old": 2})
    p = Pipeline(cfg, store=store, jobs=JobsClient(cfg, store, client=jobspipe_client(FakeJobsAPI({}))),
                 runner=FakeRunner({"low": 3, "high": 8}), use_notion=False, log=lambda s: None, fetch_pages=False)
    asyncio.run(p.triage([Candidate(store.job(j), "tpm") for j in ("low", "high")]))
    st = store.state()
    assert st["low"]["dismissed"] and not st["high"].get("dismissed")
    assert not st["old"].get("dismissed")       # scored before: only `dismiss-low-fit` reaches it


def test_scoring_startup_roles_dismisses_the_low_ones(tmp_dirs):
    store = Store()
    for i in (1, 2):
        store.save_job({"id": f"startup-x-role-{i}", "job_title": "EM", "company": "X", "url": f"https://x/{i}",
                        "description": "Lead platform programs across teams. " * 30}, "startups")
    cfg = config.Config(searches=[], dismiss_at_or_below_fit=4)
    asyncio.run(su.score_startup_roles(cfg, store, log=lambda s: None, runner=FakeRunner({"startup-x-role-1": 2})))
    st = store.state()
    assert st["startup-x-role-1"]["dismissed"] and not st["startup-x-role-2"].get("dismissed")


def test_signal_score_in_the_app_and_restore_keeps_it(env):  # noqa: F811
    c, store = env["client"], env["store"]
    env["cfg"].dismiss_at_or_below_fit = 4
    env["runner"].pipeline.fit_by_job["j2"] = 3
    r = c.post("/api/jobs/j2/score", headers=H).json()
    assert r["low_fit"] is True
    row = {j["id"]: j for j in c.get("/api/jobs", headers=H).json()["jobs"]}["j2"]
    assert row["dismissed"] and row["low_fit"]
    c.post("/api/jobs/j2/dismiss", headers=H, json={"dismissed": False})      # Restore
    row = {j["id"]: j for j in c.get("/api/jobs", headers=H).json()["jobs"]}["j2"]
    assert not row["dismissed"] and not row["low_fit"] and store.state()["j2"]["kept"]
    assert c.post("/api/jobs/j2/score", headers=H).json()["low_fit"] is False      # a later score keeps it


def test_profile_tracker_sets_it(env):  # noqa: F811
    c = env["client"]
    for bad in ("low", 0, 10):
        assert c.put("/api/tracker/settings", headers=H, json={"dismiss_at_or_below_fit": bad}).status_code == 400
    r = c.put("/api/tracker/settings", headers=H, json={"dismiss_at_or_below_fit": "4"}).json()
    assert r["dismiss_at_or_below_fit"] == 4 and r["ghost_after_days"] == 60 and r["moved"] == []
    assert env["cfg"].dismiss_at_or_below_fit == 4 and config.load(env["searches"]).dismiss_at_or_below_fit == 4
    assert c.put("/api/tracker/settings", headers=H, json={"dismiss_at_or_below_fit": ""}).json()["dismiss_at_or_below_fit"] is None
    assert config.load(env["searches"]).dismiss_at_or_below_fit is None
