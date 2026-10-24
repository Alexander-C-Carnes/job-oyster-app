"""The dashboard's application flow and the share card's stats."""
from datetime import date

from jobpipe.web.flow import application_flow


def job(i, status, reached="", company=None, applied_on="2026-09-01", **kw):
    return {"id": f"j{i}", "tracker_id": f"t{i}", "status": status, "reached": reached or "",
            "company": f"Co{i}" if company is None else company, "applied_on": applied_on, **kw}


def links(flow):
    return {(l["from"], l["to"]): l["n"] for l in flow["links"] if l["n"]}


def test_each_application_flows_to_where_it_ended():
    jobs = [
        job(1, "Applied"), job(2, "Done"),                                  # no reply yet
        job(3, "Denied", "Applied"), job(4, "Denied", "Interviewing"),      # rejected before and after an interview
        job(5, "Ghosted", "Applied"), job(6, "Ghosted", "Interviewing"),
        job(7, "Interviewing", "Interviewing"),
        job(8, "Offer", "Offer", title="Staff TPM", company="Acme", status_since="2026-10-20"),
        job(9, "In progress"), job(10, "Not Applying"),                     # never applied: not counted
        {**job(11, "Applied"), "tracker_id": None},                         # not tracked: not counted
    ]
    f = application_flow(jobs, today=date(2026, 10, 24), first_name="Sam")
    assert links(f) == {("applied", "interviewed"): 4, ("applied", "no_reply"): 2, ("applied", "rejected"): 1,
                        ("applied", "ghosted"): 1, ("interviewed", "rejected"): 1, ("interviewed", "ghosted"): 1,
                        ("interviewed", "interviewing"): 1, ("interviewed", "offer"): 1}
    assert (f["applications"], f["interviewed"], f["offers"], f["rejected"], f["ghosted"], f["no_reply"],
            f["interviewing"]) == (8, 4, 1, 2, 2, 2, 1)
    assert f["interview_rate"] == 50                     # 4 of 8
    assert f["heard_back"] == 63                         # 5 of 8: the interviews and the rejection without one
    assert f["offer"] == {"title": "Staff TPM", "company": "Acme", "date": "2026-10-20"}
    assert f["first_name"] == "Sam"
    assert sum(l["n"] for l in f["links"] if l["from"] == "applied") == f["applications"]


def test_the_status_alone_counts_when_reached_is_missing():
    f = application_flow([job(1, "Interviewing"), job(2, "Offer"), job(3, "Denied")], today=date(2026, 10, 24))
    assert (f["interviewed"], f["offers"], links(f)[("applied", "rejected")]) == (2, 1, 1)


def test_companies_interviewed_counts_each_company_once():
    jobs = [job(1, "Interviewing", company="Acme"), job(2, "Denied", "Interviewing", company=" acme "),
            job(3, "Offer", company="Beta"), job(4, "Interviewing", company=""), job(5, "Interviewing", company=""),
            job(6, "Applied", company="Gamma")]
    assert application_flow(jobs, today=date(2026, 10, 24))["companies_interviewed"] == 4   # a blank company is its own


def test_the_search_runs_from_the_first_application_to_today():
    jobs = [job(1, "Applied", applied_on="2026-08-12"), job(2, "Applied", applied_on="2026-09-30"),
            job(3, "Applied", applied_on=""), job(4, "Applied", applied_on="2026-11-30")]     # a future Due date is ignored
    f = application_flow(jobs, today=date(2026, 10, 24))
    assert (f["start"], f["today"], f["days"]) == ("2026-08-12", "2026-10-24", 74)


def test_no_applications_yet():
    f = application_flow([job(1, "In progress")], today=date(2026, 10, 24))
    assert (f["applications"], f["interview_rate"], f["heard_back"], f["start"], f["days"], f["offer"]) == (0, 0, 0, "", 0, None)


def test_the_latest_offer_is_the_one_on_the_card():
    jobs = [job(1, "Offer", title="A", status_since="2026-10-01"), job(2, "Offer", title="B", status_since="2026-10-15")]
    assert application_flow(jobs, today=date(2026, 10, 24))["offer"]["title"] == "B"
