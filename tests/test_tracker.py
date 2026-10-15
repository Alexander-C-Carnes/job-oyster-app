"""The local tracker: source of truth on disk, Notion as a synced copy."""
import json

import httpx
import pytest

from jobpipe.notion import NotionTracker, TrackerEntry
from jobpipe.tracker import LocalTracker
from conftest import FakeNotion, notion_row

URL = "https://boards.greenhouse.io/acme/jobs/1"


def entry(url=URL, status="Not started", fit=8.0, notes="Triaged 2026-10-01 · Quick fit 8/10 · good"):
    return TrackerEntry(title="Staff TPM", company="Acme", url=url, fit_score=fit, notes=notes, status=status, body=["b"])


def make(tmp_path, rows=None, seed=None):
    fake = FakeNotion(rows)
    notion = NotionTracker("ds", "proj", token="t", client=httpx.Client(transport=httpx.MockTransport(fake)))
    notion.backoff = (0, 0)
    return LocalTracker(tmp_path / "tracker.db", notion=notion, seed=seed), fake, notion


def status_in_notion(fake, pid):
    return fake.rows[pid]["properties"]["Status"]["status"]["name"]


def test_changes_are_local_first_and_queued_while_notion_is_down(tmp_path, monkeypatch):
    t, fake, notion = make(tmp_path, [notion_row("p1", "https://x.example/jobs/9", status="Applied", fit=7)])
    t.sync()
    assert [(r["page_id"], r["status"], r["pending"]) for r in t.rows()] == [("p1", "Applied", False)]

    def down(*a, **k):
        raise RuntimeError("Notion's servers returned an error (500).")
    for name in ("upsert_page", "set_status", "list_jobs"):
        monkeypatch.setattr(notion, name, down)
    assert t.upsert(entry(), job_id="j1") == ("created", "")           # tracked here even though Notion failed
    t.set_status("p1", "Done")
    rows = {r["page_id"]: r for r in t.rows()}
    new = next(r for r in rows.values() if r["job_id"] == "j1")
    assert rows["p1"]["status"] == "Done" and rows["p1"]["pending"] and new["pending"] and new["notion_page_id"] is None
    assert t.state()["pending"] == 2 and "500" in t.state()["error"]
    assert status_in_notion(fake, "p1") == "Applied" and len(fake.rows) == 1
    with pytest.raises(ValueError):
        t.set_status("p1", "Bogus")
    with pytest.raises(KeyError):
        t.set_status("nope", "Done")

    monkeypatch.undo()                                                   # Notion is back: the queue is sent
    t.sync()
    assert t.state() == {"notion": True, "synced": t.state()["synced"], "error": "", "pending": 0}
    assert status_in_notion(fake, "p1") == "Done" and len(fake.rows) == 2
    sent = next(r for r in t.rows() if r["job_id"] == "j1")
    assert sent["notion_page_id"] and sent["page_url"].startswith("https://notion.so/") and not sent["pending"]
    assert sent["page_id"] == new["page_id"]                             # its id here never changes
    assert t.version() == LocalTracker(tmp_path / "tracker.db").version()   # another process sees the same state


def test_pull_brings_in_notion_changes_but_unsent_local_ones_win(tmp_path, monkeypatch):
    t, fake, notion = make(tmp_path, [notion_row("p1", URL, status="Not started", fit=7, notes="mine")])
    t.sync()
    # edited in Notion, and a row added there (as the resume-job-fit skill does)
    fake.rows["p1"]["properties"]["Status"]["status"]["name"] = "In progress"
    fake.rows["p2"] = notion_row("p2", "https://y.example/jobs/2", status="Applied", fit=9)
    t.sync()
    assert {r["page_id"]: r["status"] for r in t.rows()} == {"p1": "In progress", "p2": "Applied"}
    assert t.known_urls() == {URL: "In progress", "https://y.example/jobs/2": "Applied"}

    # both sides change p1 before a sync: the change made here wins and is sent
    monkeypatch.setattr(notion, "set_status", lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
    t.set_status("p1", "Blocked")
    fake.rows["p1"]["properties"]["Status"]["status"]["name"] = "Done"
    with pytest.raises(RuntimeError):
        t.sync()
    monkeypatch.undo()
    t.sync()
    assert {r["page_id"]: r["status"] for r in t.rows()}["p1"] == "Blocked" and status_in_notion(fake, "p1") == "Blocked"

    # tracking a job whose row already exists updates it by the old rules: status only moves Not started -> In progress
    assert t.track(entry(status="In progress", fit=9.0, notes="Scored 2026-10-01 · Tailored resume 9/10")) == "updated"
    p1 = next(r for r in t.rows() if r["page_id"] == "p1")
    assert p1["status"] == "Blocked" and p1["fit"] == 9.0 and p1["notes"].endswith("mine") and p1["pending"]
    t.sync()
    assert fake.rows["p1"]["properties"]["Fit Score"]["number"] == 9.0 and len(fake.rows) == 2   # no duplicate page

    # deleted in Notion: gone here too. An empty answer is not taken to mean everything was deleted.
    del fake.rows["p2"]
    t.sync()
    assert [r["page_id"] for r in t.rows()] == ["p1"]
    fake.rows.clear()
    t.sync()
    assert [r["page_id"] for r in t.rows()] == ["p1"]


def test_seed_from_the_old_snapshot_and_working_without_notion(tmp_path):
    snap = tmp_path / "notion-cache.json"
    snap.write_text(json.dumps({"rows": [{"page_id": "p1", "page_url": "https://notion.so/p1", "name": "Staff TPM — Acme",
                                          "company": "Acme", "job_url": URL, "status": "Applied", "fit": 8, "notes": "n",
                                          "priority": "", "due": ""}]}))
    t = LocalTracker(tmp_path / "tracker.db", notion=None, seed=snap)
    assert [(r["page_id"], r["notion_page_id"], r["status"]) for r in t.rows()] == [("p1", "p1", "Applied")]
    LocalTracker(tmp_path / "tracker.db", notion=None, seed=snap)        # seeding happens once
    assert len(t.rows()) == 1

    # no Notion at all: tracking still works, nothing waits, sync is a no-op
    assert t.upsert(entry(url="https://z.example/jobs/3"), job_id="j3") == ("created", "")
    assert t.track(entry(url=""), job_id="pasted-1") == "created"        # no URL: kept here, never sent
    t.set_status("p1", "Done")
    t.sync()
    assert t.state() == {"notion": False, "synced": None, "error": "", "pending": 0}
    assert {r["job_id"]: r["status"] for r in t.rows()} == {None: "Done", "j3": "Not started", "pasted-1": "Not started"}


def test_tailoring_puts_a_job_in_progress_as_it_starts(tmp_path):
    t, fake, notion = make(tmp_path, [notion_row("p1", URL, status="Not started"),
                                      notion_row("p2", "https://x.example/jobs/2", status="Applied")])
    t.sync()
    assert t.start(entry(status="In progress"), job_id="j1") == "updated"
    assert t.start(entry(url="https://x.example/jobs/2", status="In progress")) == "unchanged"   # later stages stay
    t.sync()
    assert status_in_notion(fake, "p1") == "In progress" and status_in_notion(fake, "p2") == "Applied"

    # An untracked job is added here, and goes to Notion only with the run's own entry, body and all.
    new = "https://x.example/jobs/3"
    assert t.start(entry(url=new, status="In progress", notes=""), job_id="j3") == "created"
    t.sync()
    assert {r["job_id"]: r["status"] for r in t.rows()}["j3"] == "In progress"
    assert not any(r.get("_create", {}).get("properties", {}).get("Job URL", {}).get("url") == new for r in fake.rows.values())
    t.upsert(entry(url=new, status="In progress", notes="Scored 2026-10-06 · Tailored resume 9/10"), job_id="j3")
    made = next(r["_create"] for r in fake.rows.values() if r.get("_create", {}).get("properties", {}).get("Job URL", {}).get("url") == new)
    assert made["properties"]["Status"]["status"]["name"] == "In progress" and made["children"]
    assert [r["job_id"] for r in t.rows()].count("j3") == 1


@pytest.mark.parametrize("status", ["Denied", "Not Applying"])
def test_denied_and_not_applying_are_kept(tmp_path, status):
    t, fake, notion = make(tmp_path, [notion_row("p1", URL, status="Applied")])
    t.sync()
    t.set_status("p1", status)
    t.sync()
    assert status_in_notion(fake, "p1") == status
    # tailoring it later doesn't pull it back to In progress, and searches skip it
    t.track(entry(status="In progress", fit=5.0))
    t.sync()
    assert {r["page_id"]: r["status"] for r in t.rows()}["p1"] == status == status_in_notion(fake, "p1")
    assert t.known_urls() == {URL: status}


def test_not_applying_is_kept_and_a_missing_notion_option_is_explained(tmp_path):
    t, fake, notion = make(tmp_path, [notion_row("p1", URL, status="Not started")])
    t.sync()
    t.set_status("p1", "Not Applying")
    t.sync()
    assert status_in_notion(fake, "p1") == "Not Applying"
    # tailoring it later doesn't pull it back to In progress
    t.track(entry(status="In progress", fit=5.0))
    t.sync()
    assert {r["page_id"]: r["status"] for r in t.rows()}["p1"] == "Not Applying" == status_in_notion(fake, "p1")

    # a Notion database without the option: the error says what to add, and the change stays queued
    def no_option(request):
        if request.method == "PATCH":
            return httpx.Response(400, json={"object": "error", "status": 400, "code": "validation_error",
                                             "message": "Invalid status option."})
        return fake(request)
    notion.http = httpx.Client(transport=httpx.MockTransport(no_option))
    t.set_status("p1", "Not started")
    t.set_status("p1", "Not Applying")
    with pytest.raises(RuntimeError, match=r'add one in Notion'):
        t.sync()
    assert next(r for r in t.rows() if r["page_id"] == "p1")["pending"]


def test_applied_day_is_kept_for_the_dashboard(tmp_path, monkeypatch):
    import jobpipe.tracker as tracker_mod
    day = {"v": "2026-10-03"}
    monkeypatch.setattr(tracker_mod, "_today", lambda: day["v"])
    t, fake, notion = make(tmp_path, [notion_row("p1", URL), notion_row("p2", "https://x.example/jobs/2")])
    t.sync()
    applied_on = lambda: {r["page_id"]: r["applied_on"] for r in t.rows()}
    assert applied_on() == {"p1": "", "p2": ""}

    t.set_status("p1", "Applied")
    day["v"] = "2026-10-05"
    t.set_status("p1", "Interviewing")                 # a later stage keeps the day it was applied to
    assert applied_on()["p1"] == "2026-10-03"
    t.set_status("p1", "In progress")                  # moved back: not applied any more
    assert applied_on()["p1"] == ""

    # a status changed in Notion counts from the sync that sees it
    fake.rows["p2"]["properties"]["Status"]["status"]["name"] = "Offer"
    t.sync()
    assert applied_on()["p2"] == "2026-10-05"
    # Notion's Due/Submitted wins once it's set, unless it's still in the future
    fake.rows["p2"]["properties"]["Due/Submitted"] = {"date": {"start": "2026-09-30"}}
    t.sync()
    assert applied_on()["p2"] == "2026-09-30"
    fake.rows["p2"]["properties"]["Due/Submitted"] = {"date": {"start": "2026-10-20"}}
    t.sync()
    assert applied_on()["p2"] == "2026-10-05"


def test_a_tracker_from_before_the_applied_day_is_backfilled(tmp_path):
    import sqlite3
    db = tmp_path / "tracker.db"
    con = sqlite3.connect(db)
    con.executescript("""CREATE TABLE tracker (id TEXT PRIMARY KEY, notion_page_id TEXT UNIQUE, page_url TEXT NOT NULL DEFAULT '',
        job_id TEXT, name TEXT NOT NULL DEFAULT '', company TEXT NOT NULL DEFAULT '', job_url TEXT NOT NULL DEFAULT '',
        url_key TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '', fit REAL, notes TEXT NOT NULL DEFAULT '',
        priority TEXT NOT NULL DEFAULT '', due TEXT NOT NULL DEFAULT '', entry TEXT, dirty_status INTEGER NOT NULL DEFAULT 0,
        updated TEXT NOT NULL DEFAULT '');
        INSERT INTO tracker(id, status, updated) VALUES ('a', 'Applied', '2026-09-20T12:00:00+00:00'),
                                                      ('b', 'In progress', '2026-09-21T12:00:00+00:00');""")
    con.commit()
    con.close()
    t = LocalTracker(db)
    assert {r["page_id"]: r["applied_on"] for r in t.rows()} == {"a": "2026-09-20", "b": ""}


def test_a_job_left_at_applied_or_interviewing_moves_to_ghosted(tmp_path, monkeypatch):
    from datetime import date
    import jobpipe.tracker as tracker_mod
    day = {"v": "2026-08-01"}
    monkeypatch.setattr(tracker_mod, "_today", lambda: day["v"])
    t, fake, notion = make(tmp_path, [notion_row(f"p{i}", f"https://x.example/jobs/{i}") for i in range(1, 5)])
    t.sync()
    t.set_status("p1", "Applied")
    t.set_status("p3", "Offer")                       # only Applied and Interviewing wait for an answer
    day["v"] = "2026-08-20"
    t.set_status("p2", "Applied")
    t.set_status("p2", "Interviewing")
    t.set_status("p2", "Interviewing")                # setting the same status again doesn't restart the clock
    t.sync()
    row = lambda pid: next(r for r in t.rows() if r["page_id"] == pid)
    assert row("p2")["status_since"] == "2026-08-20"

    assert t.ghost_stale(0, today=date(2027, 1, 1)) == []                 # 0 days: never
    assert t.ghost_stale(60, today=date(2026, 9, 29)) == []               # 59 days
    assert t.ghost_stale(60, today=date(2026, 9, 30)) == [{"id": "p1", "name": row("p1")["name"], "from": "Applied"}]
    p1 = row("p1")
    assert (p1["status"], p1["ghosted_on"], p1["ghosted_from"], p1["status_since"]) == ("Ghosted", "2026-09-30", "Applied", "2026-08-01")
    assert p1["pending"] and p1["applied_on"] == "2026-08-01"            # still counts as applied, on the day it was
    t.sync()
    assert status_in_notion(fake, "p1") == "Ghosted" and not row("p1")["pending"]
    assert t.known_urls()["https://x.example/jobs/1"] == "Ghosted"        # searches skip it
    assert [m["from"] for m in t.ghost_stale(60, today=date(2026, 10, 19))] == ["Interviewing"]
    t.sync()
    assert row("p3")["status"] == "Offer" and row("p4")["status"] == "Not started"

    # Heard back after all: picking a status again forgets the move and starts the clock again.
    day["v"] = "2026-10-20"
    t.set_status("p1", "Interviewing")
    assert (row("p1")["ghosted_on"], row("p1")["status_since"]) == ("", "2026-10-20")
    assert t.ghost_stale(60, today=date(2026, 12, 18)) == []
    # A status changed in Notion restarts it too.
    fake.rows["p2"]["properties"]["Status"]["status"]["name"] = "Applied"
    day["v"] = "2026-10-25"
    t.sync()
    assert (row("p2")["status"], row("p2")["ghosted_on"], row("p2")["status_since"]) == ("Applied", "", "2026-10-25")


def test_a_status_notion_has_no_option_for_waits_without_holding_up_the_rest(tmp_path):
    from jobpipe.notion import StatusRefused
    t, fake, notion = make(tmp_path, [notion_row("p1", URL, status="Applied"), notion_row("p2", "https://x.example/jobs/2")])
    t.sync()

    def no_ghosted(request):
        if request.method == "PATCH" and b"Ghosted" in request.content:
            return httpx.Response(400, json={"object": "error", "status": 400, "message": "Invalid status option."})
        return fake(request)
    notion.http = httpx.Client(transport=httpx.MockTransport(no_ghosted))
    t.set_status("p1", "Ghosted")
    t.set_status("p2", "Applied")
    fake.rows["p2"]["properties"]["Notes"] = {"rich_text": [{"plain_text": "edited in Notion"}]}
    with pytest.raises(StatusRefused, match='"Ghosted" option'):
        t.sync()
    rows = {r["page_id"]: r for r in t.rows()}
    assert rows["p1"]["pending"] and status_in_notion(fake, "p1") == "Applied"     # waits for the option
    assert status_in_notion(fake, "p2") == "Applied" and not rows["p2"]["pending"]  # the rest went
    assert rows["p2"]["notes"] == "edited in Notion"                                # and Notion was still read
    assert '"Ghosted"' in t.state()["error"]


def test_a_tracker_from_before_the_status_day_is_backfilled(tmp_path):
    import sqlite3
    db = tmp_path / "tracker.db"
    con = sqlite3.connect(db)
    con.executescript("""CREATE TABLE tracker (id TEXT PRIMARY KEY, notion_page_id TEXT UNIQUE, page_url TEXT NOT NULL DEFAULT '',
        job_id TEXT, name TEXT NOT NULL DEFAULT '', company TEXT NOT NULL DEFAULT '', job_url TEXT NOT NULL DEFAULT '',
        url_key TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '', fit REAL, notes TEXT NOT NULL DEFAULT '',
        priority TEXT NOT NULL DEFAULT '', due TEXT NOT NULL DEFAULT '', entry TEXT, dirty_status INTEGER NOT NULL DEFAULT 0,
        updated TEXT NOT NULL DEFAULT '', resume_files TEXT NOT NULL DEFAULT '[]', resume_upload TEXT,
        applied TEXT NOT NULL DEFAULT '');
        INSERT INTO tracker(id, status, applied, updated) VALUES ('a', 'Applied', '2026-09-01', '2026-09-20T12:00:00+00:00'),
                                                               ('b', 'Interviewing', '2026-09-01', '2026-09-25T12:00:00+00:00');""")
    con.commit()
    con.close()
    t = LocalTracker(db)
    # Applied since the day it was applied to; any other stage since the row last changed (never earlier than it was)
    assert {r["page_id"]: r["status_since"] for r in t.rows()} == {"a": "2026-09-01", "b": "2026-09-25"}


def test_two_processes_syncing_at_once_make_one_notion_page(tmp_path):
    # The app's background sync and a run's own process (scoring startup roles) each open tracker.db. Both used
    # to send the same queued job at once; each found no page by its URL and made one, so it was in Notion twice.
    import threading
    import time
    fake = FakeNotion()

    def slow(request):           # Notion takes a moment to answer, so the two syncs overlap
        time.sleep(0.05)
        return fake(request)

    def tracker():
        notion = NotionTracker("ds", "proj", token="t", client=httpx.Client(transport=httpx.MockTransport(slow)))
        notion.backoff = (0, 0)
        return LocalTracker(tmp_path / "tracker.db", notion=notion)
    app, run = tracker(), tracker()
    run.track(entry(), job_id="j1")
    threads = [threading.Thread(target=t.sync) for t in (app, run)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(fake.rows) == 1
    assert [(r["job_id"], r["notion_page_id"]) for r in app.rows()] == [("j1", "page1")]
