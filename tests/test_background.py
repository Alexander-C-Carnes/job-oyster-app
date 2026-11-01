"""Long answers run in the background (docs/paid-service.md item 1.4): no request waits longer than wait_s, and the page
asks GET /api/work/{id} until the answer is ready."""
import asyncio
import threading
import time

import pytest
from fastapi.testclient import TestClient

from jobpipe import config
from jobpipe.web.server import create_app
from test_web import H, EditRunner, env  # noqa: F401 - the web app fixture

WAIT = 0.2


class Slow(EditRunner):
    """The résumé chat, holding each reply (and re-rating) until `gate` is set."""
    def __init__(self):
        super().__init__()
        self.gate = threading.Event()
        self.fail = False

    async def run(self, call):
        if call.label.startswith(("resume-edit", "resume-fact", "rescore", "triage:")):
            while not self.gate.is_set():
                await asyncio.sleep(0.01)
            if self.fail:
                raise RuntimeError("the proxy dropped it")
        return await super().run(call)


@pytest.fixture
def slow(env, tmp_path):  # noqa: F811
    runner = Slow()
    app = create_app(env["cfg"], token="test-token", store=env["store"], tracker=env["tracker"],
                     runner_factory=lambda cfg: runner, runs=env["runs"], allowed_hosts={"testserver"},
                     searches_path=env["searches"], resume_root=tmp_path / "repo", impact_path=env["impact"],
                     wait_s=WAIT, poll_s=WAIT)
    with TestClient(app) as c:            # one event loop for every request, as the real server has
        yield c, runner
    runner.gate.set()


def until_done(c, r):
    for _ in range(100):
        if r.status_code != 202:
            return r
        r = c.get(f"/api/work/{r.json()['work']}", headers=H)
    raise AssertionError("never finished")


def test_a_slow_chat_reply_answers_202_and_is_fetched_when_ready(slow):
    c, runner = slow
    t = time.monotonic()
    r = c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "Tighten the summary"})
    assert r.status_code == 202 and r.json()["pending"] and time.monotonic() - t < WAIT + 1
    assert c.get("/api/jobs/j1/chat", headers=H).json()["busy"]
    wid = r.json()["work"]
    assert c.get(f"/api/work/{wid}", headers=H).status_code == 202     # still working
    assert c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "And again"}).status_code == 409
    runner.gate.set()
    done = until_done(c, c.get(f"/api/work/{wid}", headers=H))
    assert done.status_code == 200 and done.json()["proposal"] and "reliability" in done.json()["reply_html"]
    assert c.get(f"/api/work/{wid}", headers=H).json() == done.json()     # kept, should the answer go astray
    assert not c.get("/api/jobs/j1/chat", headers=H).json()["busy"]


def test_a_failure_comes_back_with_the_routes_own_status(slow):
    c, runner = slow
    runner.fail = True
    r = c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "Tighten the summary"})
    runner.gate.set()
    r = until_done(c, r)
    assert r.status_code == 502 and "the proxy dropped it" in r.json()["detail"]


def test_the_work_goes_on_when_nobody_asks(slow):
    c, runner = slow
    assert c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "Tighten the summary"}).status_code == 202
    runner.gate.set()                                             # the page went away: the reply is still saved
    for _ in range(100):
        if not c.get("/api/jobs/j1/chat", headers=H).json()["busy"]:
            break
        time.sleep(0.02)
    chat = c.get("/api/jobs/j1/chat", headers=H).json()["chat"]
    assert any("reliability" in str(m) for m in chat)


def test_the_score_button_and_re_rating_run_in_the_background_too(slow):
    c, runner = slow
    r = c.post("/api/jobs/j2/score", headers=H)
    assert r.status_code == 202
    runner.gate.set()
    assert until_done(c, r).json()["triage"]["fit_score"] == 8
    r = c.post("/api/jobs/j1/resume/rescore", headers=H, json={"n": 1})     # nothing to re-rate: answered at once
    assert r.status_code == 200 and r.json()["version"]["n"] == 1


def test_unknown_work_and_fast_answers(env):  # noqa: F811
    c = env["client"]
    assert c.get("/api/work/nope", headers=H).status_code == 404
    r = c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "What's weakest?"})
    assert r.status_code == 200 and r.json()["reply_html"]            # quick: answered in the same request


def test_the_page_follows_a_still_working_answer():
    js = (config.ROOT / "jobpipe" / "web" / "static" / "app.js").read_text()
    assert "res.status === 202 && data?.pending && data.work" in js and "/api/work/" in js
