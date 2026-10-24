"""The dashboard's application flow and the numbers on the share card, counted from the tracked jobs.

Every job at an applied stage starts at Applied. One that got to an interview (its status, or the furthest stage it
reached before a rejection or ghosting: the tracker's `reached` column) goes on to Interviewed. Each job then ends where
it stands now: Offer, still Interviewing, Rejected, Ghosted, or No reply yet (Applied or Done and never interviewed).
"""
from __future__ import annotations

from datetime import date
from typing import Iterable, Optional

from ..notion import APPLIED_STATUSES
from ..tracker import REACH

ENDS = ("offer", "interviewing", "rejected", "ghosted", "no_reply")


def _pct(part: int, whole: int) -> int:
    return int(part * 100 / whole + 0.5) if whole else 0


def _end(status: str, interviewed: bool) -> str:
    if status == "Offer":
        return "offer"
    if status == "Denied":
        return "rejected"
    if status == "Ghosted":
        return "ghosted"
    return "interviewing" if interviewed else "no_reply"      # Interviewing, or Applied/Done after an interview


def application_flow(jobs: Iterable[dict], today: Optional[date] = None, first_name: str = "") -> dict:
    """jobs: the board's rows (status, reached, applied_on, status_since, title, company, tracker_id).
    Returns the counts at each stage, the links between them, and the share card's stats."""
    today = today or date.today()
    applied = [j for j in jobs if j.get("tracker_id") and j.get("status") in APPLIED_STATUSES]
    links = {(a, b): 0 for a, b in [("applied", "interviewed")] + [("applied", e) for e in ENDS[2:]]
             + [("interviewed", e) for e in ENDS[:4]]}
    ends = dict.fromkeys(ENDS, 0)
    companies, offers = set(), []
    for j in applied:
        status = j["status"]
        interviewed = max(REACH.get(status, 0), REACH.get(j.get("reached") or "", 0)) >= REACH["Interviewing"]
        end = _end(status, interviewed)
        ends[end] += 1
        if interviewed:
            links[("applied", "interviewed")] += 1
            links[("interviewed", end)] += 1
            companies.add((j.get("company") or "").strip().casefold() or j.get("id") or j["tracker_id"])
        else:
            links[("applied", end)] += 1
        if status == "Offer":
            offers.append(j)
    n, interviewed_n = len(applied), links[("applied", "interviewed")]
    days = sorted(j["applied_on"] for j in applied if j.get("applied_on") and j["applied_on"] <= today.isoformat())
    latest = max(offers, key=lambda j: j.get("status_since") or "", default=None)
    return {
        "applications": n, "interviewed": interviewed_n, "companies_interviewed": len(companies),
        "offers": ends["offer"], "interviewing": ends["interviewing"], "rejected": ends["rejected"],
        "ghosted": ends["ghosted"], "no_reply": ends["no_reply"],
        "interview_rate": _pct(interviewed_n, n),
        # heard back: any answer at all, an interview or a rejection
        "heard_back": _pct(n - ends["no_reply"] - links[("applied", "ghosted")], n),
        "links": [{"from": a, "to": b, "n": v} for (a, b), v in links.items()],
        "start": days[0] if days else "", "today": today.isoformat(),
        "days": (today - date.fromisoformat(days[0])).days + 1 if days else 0,
        "offer": latest and {"title": latest.get("title") or "", "company": latest.get("company") or "",
                             "date": latest.get("status_since") or ""},
        "first_name": first_name,
    }
