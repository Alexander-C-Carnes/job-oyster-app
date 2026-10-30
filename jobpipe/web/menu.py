"""What the Mac menu bar lists.

Runs that are going or waiting their turn (a search, Make résumé, or a signal/full score), résumés
ready to send, and roles at the Profile › Menu Bar Settings fit floor (7 by default) found or
scored since yesterday. The launcher polls GET /api/menu and opens the browser on a click. A
résumé is ready when one has been made and the application has not been sent (and the role is not
ruled out). The fit is the full score where there is one, else the signal score, the same number
the job list shows. A role already listed under résumés ready is not listed again.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from ..notion import APPLIED_STATUSES
from .searches_file import MENU_FIT_FLOOR_MAX

FIT_FLOOR = 7   # default; Profile › Menu Bar Settings can set menu.fit_floor in searches.yaml
# Sent, or ruled out: nothing to send, and not a new role to look at.
_ASIDE = set(APPLIED_STATUSES) | {"Not Applying"}
_SCORE_KIND = {"signal": "Signal score", "full": "Full score", "upload": "Score résumé"}


def local_day(value: Any) -> str:
    """YYYY-MM-DD. A date written by the app (triaged_at, full_scored_at) stays as written.
    A timestamp (first_seen) becomes the local day, so 'since yesterday' follows this Mac."""
    text = str(value or "").strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-" and (len(text) == 10 or text[10] != "T"):
        return text[:10]
    if len(text) < 11 or text[10] != "T":
        return ""
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().date().isoformat()


def _fit(row: dict) -> Optional[float]:
    score = row["impact_score"] if row.get("impact_score") is not None else row.get("fit")
    if score is None:
        return None
    try:
        return float(score)
    except (TypeError, ValueError):
        return None


def _fit_out(n: float) -> int | float:
    return int(n) if n == int(n) else round(n, 1)


def snapshot(rows: list[dict], state: dict[str, dict], runs: list[dict],
             scores: Optional[list[dict]] = None, today: Optional[date] = None,
             fit_floor: Optional[int] = None) -> dict:
    """rows: the job board. state: data/state.json. runs: going and waiting, each
    {id, label, status, args}. scores: signal/full/upload in progress, each
    {id: job id, kind, state: queued|running} — they do not go through RunManager.
    fit_floor: Newest at fit N+ (Profile › Menu Bar Settings); default FIT_FLOOR."""
    today = today or date.today()
    since, today_s = (today - timedelta(days=1)).isoformat(), today.isoformat()
    floor = FIT_FLOOR if fit_floor is None else max(1, min(MENU_FIT_FLOOR_MAX, int(fit_floor)))
    by_id = {r["id"]: r for r in rows}

    ready = []
    for row in rows:
        if not row.get("tailored") or row.get("dismissed") or row.get("status") in _ASIDE:
            continue
        ready.append({"id": row["id"], "title": row.get("title") or "", "company": row.get("company") or ""})
    ready.sort(key=lambda r: (r["company"].lower(), r["title"].lower()))
    ready.sort(key=lambda r: (state.get(r["id"]) or {}).get("tailored_at") or "", reverse=True)
    ready_ids = {r["id"] for r in ready}

    new_roles = []
    for row in rows:
        if row["id"] in ready_ids or row.get("dismissed") or row.get("status") in _ASIDE:
            continue
        fit = _fit(row)
        if fit is None or fit < floor:
            continue
        st = state.get(row["id"]) or {}
        days = [local_day(st.get("first_seen") or row.get("first_seen") or ""),
                local_day(st.get("triaged_at") or ""), local_day(st.get("full_scored_at") or "")]
        if not any(since <= d <= today_s for d in days if d):
            continue
        new_roles.append({"id": row["id"], "title": row.get("title") or "", "company": row.get("company") or "",
                          "fit": _fit_out(fit)})
    new_roles.sort(key=lambda r: (-float(r["fit"]), r["company"].lower(), r["title"].lower()))

    listed = []
    for run in runs:
        if run.get("status") not in ("running", "queued"):
            continue
        args = run.get("args") or []
        job_id = args[1] if len(args) >= 2 and args[0] == "tailor" and isinstance(args[1], str) else ""
        listed.append({"id": run["id"], "label": run.get("label") or "", "status": run["status"], "job_id": job_id})
    for score in scores or []:
        jid = score.get("id") or ""
        if not jid:
            continue
        row = by_id.get(jid) or {}
        what = _SCORE_KIND.get(score.get("kind") or "", "Score")
        name = row.get("company") or row.get("title") or jid
        status = "running" if score.get("state") == "running" else "queued"
        listed.append({"id": f"score:{jid}", "label": f"{what}: {name}", "status": status, "job_id": jid})
    listed.sort(key=lambda r: (r["status"] != "running", str(r["id"])))

    return {"runs": listed, "ready": ready, "new_roles": new_roles, "fit_floor": floor}
