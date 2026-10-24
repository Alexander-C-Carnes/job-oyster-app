import httpx
import pytest

from jobpipe.notion import NotionTracker, TrackerEntry, merge_notes
from conftest import FakeNotion, notion_row

URL = "https://boards.greenhouse.io/acme/jobs/1"


def tracker(fake):
    return NotionTracker("ds1", "proj1", token="secret",
                         client=httpx.Client(transport=httpx.MockTransport(fake)))


def entry(**kw):
    base = dict(title="Staff TPM", company="Acme", url=URL + "?gh_src=x", fit_score=8.0,
                notes="Scored 2026-09-30 · Tailored resume 9/10 · ATS 84%", status="In progress",
                body=["**Posting:** " + URL])
    return TrackerEntry(**{**base, **kw})


def test_database_url_comes_from_the_data_sources_parent_and_is_asked_once():
    fake = FakeNotion()
    t = tracker(fake)
    assert t.database_url() == "https://www.notion.so/1a2b3c4d111122223333444455556666"
    assert t.database_url() == t.database_url()
    assert [p for m, p, _ in fake.requests if m == "GET"] == ["/v1/data_sources/ds1"]


def test_creates_row_with_tracker_properties():
    fake = FakeNotion()
    action, url = tracker(fake).upsert(entry())
    assert action == "created" and url
    create = next(b for m, p, b in fake.requests if m == "POST" and p.endswith("/pages"))
    props = create["properties"]
    assert create["parent"] == {"type": "data_source_id", "data_source_id": "ds1"}
    assert props["Name"]["title"][0]["text"]["content"] == "Staff TPM — Acme"
    assert props["Type"]["select"]["name"] == "Apply to Job"
    assert props["Status"]["status"]["name"] == "In progress"
    assert props["Fit Score"]["number"] == 8.0
    assert props["Project"]["relation"] == [{"id": "proj1"}]
    for untouched in ("Due/Submitted", "Priority", "Contact", "Resume Used"):
        assert untouched not in props


def test_updates_existing_row_without_duplicating_or_demoting():
    fake = FakeNotion([notion_row("p1", URL, status="Applied", fit=6,
                                  notes="Scored 2026-09-01 · old\nRecruiter: Sam")])
    action, _ = tracker(fake).upsert(entry())
    assert action == "updated"
    assert not any(m == "POST" and p.endswith("/pages") for m, p, _ in fake.requests)
    row = fake.rows["p1"]["properties"]
    assert row["Status"]["status"]["name"] == "Applied"          # never moved back
    assert row["Fit Score"]["number"] == 8.0
    notes = row["Notes"]["rich_text"][0]["plain_text"]
    assert "Recruiter: Sam" in notes and "old" not in notes and "ATS 84%" in notes


def test_not_started_moves_to_in_progress():
    fake = FakeNotion([notion_row("p1", URL, status="Not started")])
    tracker(fake).upsert(entry())
    assert fake.rows["p1"]["properties"]["Status"]["status"]["name"] == "In progress"


def test_merge_notes():
    assert merge_notes("", "Scored x") == "Scored x"
    assert merge_notes("my note", "Scored x") == "Scored x\nmy note"
    assert merge_notes("Triaged 2026-09-01 · Quick fit 7/10\nmine", "Scored 2026-09-30 · 9") == \
        "Scored 2026-09-30 · 9\nmine"


def test_notion_errors_are_retried_for_reads_and_explained():
    import httpx
    from jobpipe.notion import NotionTracker
    calls = []

    def flaky(fail_times, status=500):
        def handler(request):
            calls.append((request.method, request.url.path))
            if len(calls) <= fail_times:
                return httpx.Response(status, json={"object": "error", "status": status, "code": "internal_server_error",
                                                    "message": "Cross-cell memcached access is not allowed"})
            return httpx.Response(200, json={"results": [], "has_more": False, "id": "p", "url": "u"})
        return handler

    def tracker(handler):
        t = NotionTracker("ds", "proj", token="t", client=httpx.Client(transport=httpx.MockTransport(handler)))
        t.backoff = (0, 0)
        return t

    assert tracker(flaky(2)).list_jobs() == [] and len(calls) == 3          # a brief 5xx is ridden out
    calls.clear()
    with pytest.raises(RuntimeError, match=r"Notion's servers returned an error \(500: Cross-cell memcached.*Notion's side"):
        tracker(flaky(99)).list_jobs()
    assert len(calls) == 3
    calls.clear()
    with pytest.raises(RuntimeError):                                        # creating a page is never retried
        tracker(flaky(99))._req("POST", "/pages", {})
    assert len(calls) == 1
    calls.clear()
    with pytest.raises(RuntimeError, match=r"-> 400: Cross-cell"):           # a 4xx is our mistake: no retry
        tracker(flaky(99, status=400)).list_jobs()
    assert len(calls) == 1
