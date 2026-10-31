"""The Mac menu bar's list: runs, résumés ready to send, roles at the fit-score floor since yesterday."""
from datetime import date, datetime

from jobpipe.web.menu import local_day, snapshot


def test_local_day_keeps_a_date_and_converts_a_timestamp():
    assert local_day("2026-10-29") == "2026-10-29"
    assert local_day("") == "" and local_day("not a date") == ""
    stamp = "2026-10-30T03:30:00+00:00"
    assert local_day(stamp) == datetime.fromisoformat(stamp).astimezone().date().isoformat()


def test_ready_to_send_is_the_trackers_list_and_a_ready_one_is_not_listed_again():
    def row(i, company="Acme", **extra):
        return {"id": i, "title": i.title(), "company": company, "tailored": True, "status": "In progress",
                "dismissed": False, "tracker_id": f"t-{i}", **extra}

    rows = [
        row("old", fit=9), row("new", status="Not started", fit=9),
        row("sent", status="Applied"), row("out", status="Not Applying"), row("gone", dismissed=True),
        row("blocked", company="Beta", status="Blocked"),
        row("closed", fit=9, closed="2026-10-29"),                        # its posting closed: not ready
        row("untracked", fit=9, tracker_id=""),                            # not on the Tracker: a new role
        {"id": "plain", "title": "Plain", "company": "Zeta", "tailored": False, "status": "", "dismissed": False,
         "fit": 9},
    ]
    state = {"old": {"tailored_at": "2026-10-01", "triaged_at": "2026-10-30"},
             "new": {"tailored_at": "2026-10-20", "triaged_at": "2026-10-30"},
             "blocked": {"tailored_at": "2026-10-20"},
             **{i: {"triaged_at": "2026-10-30"} for i in ("closed", "untracked", "plain")}}
    snap = snapshot(rows, state, [], today=date(2026, 10, 30))
    assert [r["id"] for r in snap["ready"]] == ["new", "blocked", "old"]   # newest résumé first; same day, by company
    assert [r["id"] for r in snap["new_roles"]] == ["closed", "untracked", "plain"]   # the ready ones are not repeated


def test_new_roles_are_fit_7_or_higher_found_or_scored_since_yesterday():
    def row(i, fit, **extra):
        return {"id": i, "title": i, "company": "C", "tailored": False, "status": "", "dismissed": False,
                "fit": fit, **extra}

    rows = [row("yesterday", 8), row("today", 8.5), row("older", 9), row("seven", 7), row("six", 6),
            row("sent", 9, status="Interviewing"), row("hidden", 9, dismissed=True)]
    state = {
        "yesterday": {"triaged_at": "2026-10-29"},
        "today": {"full_scored_at": "2026-10-30", "triaged_at": "2020-01-01"},
        "older": {"triaged_at": "2026-10-28", "first_seen": "2026-10-28T12:00:00+00:00"},
        "seven": {"triaged_at": "2026-10-30"},
        "six": {"triaged_at": "2026-10-30"},
        "sent": {"triaged_at": "2026-10-30"},
        "hidden": {"triaged_at": "2026-10-30"},
    }
    snap = snapshot(rows, state, [], today=date(2026, 10, 30))
    assert [(r["id"], r["fit"]) for r in snap["new_roles"]] == [
        ("today", 8.5), ("yesterday", 8), ("seven", 7)]
    assert snap["fit_floor"] == 7
    high = snapshot(rows, state, [], today=date(2026, 10, 30), fit_floor=9)
    assert high["new_roles"] == [] and high["fit_floor"] == 9   # today is 8.5
    mid = snapshot(rows, state, [], today=date(2026, 10, 30), fit_floor=8)
    assert [(r["id"], r["fit"]) for r in mid["new_roles"]] == [("today", 8.5), ("yesterday", 8)]
    assert snapshot(rows, state, [], today=date(2026, 10, 30), fit_floor=10)["fit_floor"] == 9  # capped like Score filters



def test_the_full_score_is_the_fit_the_menu_uses():
    rows = [
        {"id": "low", "title": "L", "company": "A", "tailored": False, "status": "", "dismissed": False,
         "impact_score": 6, "fit": 9},
        {"id": "high", "title": "H", "company": "B", "tailored": False, "status": "", "dismissed": False,
         "impact_score": 9, "fit": 4},
    ]
    state = {i: {"triaged_at": "2026-10-30"} for i in ("low", "high")}
    snap = snapshot(rows, state, [], today=date(2026, 10, 30))
    assert [r["id"] for r in snap["new_roles"]] == ["high"]


def test_the_menu_lists_runs_going_before_ones_waiting():
    runs = [
        {"id": 2, "label": "Search", "status": "queued", "args": ["run"]},
        {"id": 1, "label": "Make résumé: Acme", "status": "running", "args": ["tailor", "j1"]},
        {"id": 3, "label": "done", "status": "done", "args": ["tailor", "j2"]},
    ]
    snap = snapshot([], {}, runs, today=date(2026, 10, 30))
    assert [(r["id"], r["job_id"], r["status"]) for r in snap["runs"]] == [(1, "j1", "running"), (2, "", "queued")]


def test_the_menu_lists_scores_in_progress_with_the_runs():
    rows = [{"id": "j1", "title": "TPM", "company": "Acme", "tailored": False, "status": "", "dismissed": False}]
    scores = [{"id": "j1", "kind": "signal", "state": "running"},
              {"id": "j2", "kind": "full", "state": "queued"}]
    snap = snapshot(rows, {}, [{"id": 1, "label": "Search", "status": "queued", "args": ["run"]}],
                    scores=scores, today=date(2026, 10, 30))
    assert [(r["id"], r["label"], r["status"], r["job_id"]) for r in snap["runs"]] == [
        ("score:j1", "Signal score: Acme", "running", "j1"),
        (1, "Search", "queued", ""),
        ("score:j2", "Full score: j2", "queued", "j2"),
    ]
