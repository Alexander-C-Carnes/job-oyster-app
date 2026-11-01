"""Pay read from a posting's own words: the quote has to be in the text, source pay is left alone,
and a changed description asks for a new reading."""
import asyncio
import json

from jobpipe import config
from jobpipe.config import ModelCfg
from jobpipe.jobs_api import salary_text
from jobpipe.llm import AgentError, AgentResult
from jobpipe.pay_parse import (
    Status, apply_fields, jobs_due, merge_save, next_flagged, normalize, parse_job, pay_string, run_many,
)
from jobpipe.store import Store

YEARLY = {"stated": True, "currency": "USD", "period": "year", "min": 220000, "max": 240000,
          "basis": "total", "quote": "Compensation Range: $220,000-$240,000."}
BODY = "Come join the team. " * 30


def posting(line: str) -> str:
    return BODY + "\n" + line + "\nBenefits follow.\n"


def test_yearly_usd_quote_is_kept_and_shown_as_pay():
    text = posting("Compensation Range: $220,000-$240,000.")
    reading = normalize(YEARLY, text)
    assert reading["stated"] and reading["basis"] == "total"
    job = apply_fields({"id": "j", "description": text}, reading, text.strip())
    assert salary_text(job) == "$220k–$240k"
    assert job["min_annual_salary_usd"] == 220000 and job["max_annual_salary_usd"] == 240000
    assert job["needs_parse_pay_run"] == 0 and job["pay_origin"] == "parse" and job["salary_string"] is None


def test_a_quote_that_is_not_in_the_posting_counts_as_no_pay():
    text = posting("Compensation Range: $220,000-$240,000.")
    guessed = {**YEARLY, "quote": "The salary is $220,000-$240,000."}
    assert normalize(guessed, text)["stated"] is False
    # the numbers have to be the ones in the quote, not a nearby sentence
    swapped = {**YEARLY, "min": 180000, "max": 240000}
    assert normalize(swapped, text)["stated"] is False
    assert normalize({"stated": False}, text)["stated"] is False


def test_hourly_and_other_currency_stay_text():
    hourly = {"stated": True, "currency": "USD", "period": "hour", "min": 45, "max": 60,
              "basis": "base", "quote": "$45–$60 / hour"}
    text = posting("$45–$60 / hour")
    reading = normalize(hourly, text)
    fields = apply_fields({}, reading, text.strip())
    assert fields["salary_string"] == "$45–$60 / hour"
    assert fields["min_annual_salary_usd"] is None and salary_text(fields) == "$45–$60 / hour"
    eur = {"stated": True, "currency": "EUR", "period": "year", "min": 90000, "max": 110000,
           "basis": "base", "quote": "EUR 90,000–110,000 a year"}
    assert pay_string(normalize(eur, posting(eur["quote"]))) == "EUR 90k–110k"


def test_a_reading_of_the_wrong_shape_can_be_tried_again():
    import pytest
    with pytest.raises(ValueError):
        normalize(["nope"], "text")


def test_new_and_changed_descriptions_set_the_flag_and_a_search_keeps_a_finished_reading():
    fresh = merge_save({}, {"id": "j", "description": posting("No numbers here."), "job_title": "CSE"})
    assert fresh["needs_parse_pay_run"] == 1 and "pay_origin" not in fresh
    paid = merge_save({}, {"id": "j", "description": "x", "min_annual_salary_usd": 120000, "max_annual_salary_usd": 180000})
    assert paid["needs_parse_pay_run"] == 0 and paid["pay_origin"] == "source"
    done = {**fresh, "needs_parse_pay_run": 0, "pay_origin": "parse", "min_annual_salary_usd": 220000,
            "max_annual_salary_usd": 240000, "pay_parse": {"stated": True}}
    again = merge_save(done, {"id": "j", "description": done["description"], "job_title": "CSE"})
    assert again["needs_parse_pay_run"] == 0 and again["min_annual_salary_usd"] == 220000 and again["pay_parse"]["stated"]
    changed = merge_save(done, {"id": "j", "description": posting("Compensation Range: $100,000-$110,000.")})
    assert changed["needs_parse_pay_run"] == 1 and "min_annual_salary_usd" not in changed and "pay_origin" not in changed
    # source pay survives a new description, and is not sent for a reading
    kept = merge_save(paid, {"id": "j", "description": posting("Compensation Range: $100,000-$110,000.")})
    assert kept["pay_origin"] == "source" and kept["needs_parse_pay_run"] == 0 and kept["min_annual_salary_usd"] == 120000


class _Runner:
    def __init__(self, raw):
        self.raw = raw
        self.calls = []

    async def run(self, call):
        self.calls.append(call)
        if isinstance(self.raw, Exception):
            raise self.raw
        return AgentResult(files={"pay.json": json.dumps(self.raw)}, summary="ok")


def test_parse_fills_empty_pay_and_leaves_source_pay_and_a_failed_call():
    store = Store()
    text = posting("Compensation Range: $220,000-$240,000.")
    store.save_job({"id": "cyera", "job_title": "CSE", "company": "Cyera", "description": text}, "manual")
    assert store.job("cyera")["needs_parse_pay_run"] == 1
    runner = _Runner(YEARLY)
    result = asyncio.run(parse_job(store, runner, ModelCfg(effort="low"), "cyera"))
    assert result["stated"] and result["pay"] == "$220k–$240k" and not result["kept_source"]
    saved = store.job("cyera")
    assert saved["needs_parse_pay_run"] == 0 and saved["pay_origin"] == "parse"
    assert runner.calls[0].candidate is False and "posting.txt" in runner.calls[0].documents
    # a second automatic pass does not call again
    runner.calls.clear()
    assert asyncio.run(parse_job(store, runner, ModelCfg(), "cyera")).get("skipped") == "not due"
    assert runner.calls == []
    # Find pay may read again, and a new description clears the parsed pay first
    store.paste_description("cyera", posting("Pay is not listed."))
    assert store.job("cyera")["needs_parse_pay_run"] == 1 and "min_annual_salary_usd" not in store.job("cyera")
    # source pay is never replaced, even when Find pay is asked
    store.save_job({"id": "ashby", "job_title": "PM", "company": "Acme", "description": text,
                    "min_annual_salary_usd": 120000, "max_annual_salary_usd": 180000}, "manual")
    forced = asyncio.run(parse_job(store, runner, ModelCfg(), "ashby", force=True))
    assert forced["kept_source"] and forced["stated"]
    assert store.job("ashby")["min_annual_salary_usd"] == 120000 and store.job("ashby")["pay_parse"]["stated"]
    assert salary_text(store.job("ashby")) == "$120k–$180k"
    # a failed call leaves the flag set
    store.save_job({"id": "miss", "job_title": "PM", "company": "Acme", "description": text}, "manual")
    missed = asyncio.run(parse_job(store, _Runner(AgentError("down")), ModelCfg(), "miss"))
    assert missed["error"] and store.job("miss")["needs_parse_pay_run"] == 1
    # jobs already stored, with no flag, are due; flag 0 is not; source pay is marked done instead
    store.save_job({"id": "old", "description": text, "job_title": "Old", "company": "Acme"}, "manual")
    store.update_job("old", needs_parse_pay_run=None)
    store.save_job({"id": "done", "description": text, "job_title": "Done", "company": "Acme"}, "manual")
    store.update_job("done", needs_parse_pay_run=0)
    assert "needs_parse_pay_run" not in store.job("old")
    due = jobs_due(store, limit=50)
    assert due[0] != "old" and "old" in due and "done" not in due and "ashby" not in due
    assert store.job("ashby")["needs_parse_pay_run"] == 0
    picked = next_flagged(store, Status())
    assert picked != "old" and picked != "done" and store.job("done")["needs_parse_pay_run"] == 0


def test_find_pay_reads_the_posting_and_a_short_one_is_refused():
    from fastapi.testclient import TestClient

    from jobpipe import config
    from jobpipe.web.server import create_app
    store = Store()
    text = posting("Compensation Range: $220,000-$240,000.")
    store.save_job({"id": "cyera", "job_title": "CSE", "company": "Cyera", "url": "https://example.com/cyera",
                    "description": text}, "manual")
    store.save_job({"id": "short", "job_title": "Short", "company": "Acme", "description": "Too short."}, "manual")
    app = create_app(config.load(), token="t", store=store, tracker=None,
                     runner_factory=lambda cfg: _Runner(YEARLY), allowed_hosts={"testserver"})
    with TestClient(app) as c:
        r = c.post("/api/jobs/cyera/parse-pay", headers={"X-Jobpipe-Token": "t"})
        assert r.status_code == 200 and r.json()["pay"] == "$220k–$240k" and r.json()["stated"] is True
        assert c.get("/api/jobs/cyera", headers={"X-Jobpipe-Token": "t"}).json()["pay_parse"]["basis"] == "total"
        assert c.get("/api/summary", headers={"X-Jobpipe-Token": "t"}).json()["pay_parse"]["finished"] == 1
        short = c.post("/api/jobs/short/parse-pay", headers={"X-Jobpipe-Token": "t"})
        assert short.status_code == 400 and "no posting text" in short.json()["detail"]


def test_dry_run_prints_the_reading_and_saves_nothing(capsys):
    store = Store()
    text = posting("Compensation Range: $220,000-$240,000.")
    store.save_job({"id": "cyera", "job_title": "CSE", "company": "Cyera", "url": "https://example.com/cyera",
                    "description": text}, "manual")
    store.save_job({"id": "ashby", "job_title": "PM", "company": "Acme", "description": text,
                    "min_annual_salary_usd": 120000, "max_annual_salary_usd": 180000}, "manual")
    store.update_job("ashby", needs_parse_pay_run=None, pay_origin=None)
    (config.DATA / "web-address").write_text("http://10.1.2.3:9999\n")
    n = asyncio.run(run_many(store, _Runner(YEARLY), ModelCfg(), limit=50, dry_run=True, log=print))
    out = capsys.readouterr().out
    assert n == 1 and "Nothing is saved." in out
    assert "  CSE @ Cyera\n    https://example.com/cyera\n    http://10.1.2.3:9999/#job=cyera&tab=posting\n" in out
    assert "Compensation Range: $220,000-$240,000." in out and '"stated": true' in out
    assert "would show $220k–$240k" in out
    saved = store.job("cyera")
    assert saved["needs_parse_pay_run"] == 1 and "min_annual_salary_usd" not in saved and "pay_parse" not in saved
    assert "needs_parse_pay_run" not in store.job("ashby") and "pay_origin" not in store.job("ashby")


def test_run_many_reads_jobs_that_need_it_and_stops_after_one_attempt(capsys):
    store = Store()
    text = posting("Compensation Range: $220,000-$240,000.")
    store.save_job({"id": "cyera", "job_title": "CSE", "company": "Cyera", "url": "https://example.com/cyera",
                    "description": text}, "manual")
    (config.DATA / "web-address").write_text("http://10.1.2.3:9999\n")
    n = asyncio.run(run_many(store, _Runner(YEARLY), ModelCfg(effort="low"), limit=50, log=print))
    assert n == 1 and salary_text(store.job("cyera")) == "$220k–$240k"
    out = capsys.readouterr().out
    assert "  CSE @ Cyera: $220k–$240k\n    https://example.com/cyera\n    http://10.1.2.3:9999/#job=cyera&tab=posting" in out
    n = asyncio.run(run_many(store, _Runner(YEARLY), ModelCfg(), limit=50, log=print))
    assert n == 0 and "No jobs need a pay reading." in capsys.readouterr().out
