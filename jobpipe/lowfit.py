"""Dismiss a job as soon as its signal score is pipeline.dismiss_at_or_below_fit or less. A search run, a startup
refresh and the app's Signal score all call dismiss_low_fit() after saving a score, and `python -m jobpipe
dismiss-low-fit` applies it to the jobs scored before the setting was on.

A job tracked, starred or already dismissed is left alone, and so is one you restored (`kept` in its state), so a
later score doesn't dismiss it again. A job dismissed this way carries `low_fit` (when), which the app shows as a Low
fit tag.

A score that moves a job forward on its own (a search run's résumé at pipeline.shortlist_min_fit, the tracker at
cfg.track_min_fit) never dismisses it, so the two can't both act on one job: see forward_floor() and highest().
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from .store import now_iso


def tracked_ids(data: Path) -> set[str]:
    """The stored jobs in the tracker (data/tracker.db)."""
    path = data / "tracker.db"
    if not path.exists():
        return set()
    with closing(sqlite3.connect(path)) as con:
        return {r[0] for r in con.execute("SELECT job_id FROM tracker WHERE job_id IS NOT NULL")}


def starred_ids(data: Path) -> set[str]:
    """The jobs you starred (data/marks.json)."""
    try:
        marks = json.loads((data / "marks.json").read_text())
    except (OSError, ValueError):
        return set()
    return {k for k, v in marks.items() if isinstance(v, dict) and v.get("starred")}


def forward_floor(cfg: Any, jid: str = "") -> int:
    """The lowest signal score at which `jid` moves forward on its own: a search run makes it a résumé
    (pipeline.shortlist_min_fit) or puts it in the tracker (notion.log_triaged_min_fit, or for a startup role
    startups.roles.track_min_fit)."""
    return min(f for f in (cfg.shortlist_min_fit, cfg.track_min_fit(jid)) if f is not None)


def highest(cfg: Any) -> int:
    """The highest dismiss_at_or_below_fit that stays below every job's forward_floor()."""
    floors = (cfg.shortlist_min_fit, cfg.notion_log_triaged_min_fit, cfg.startups.roles_track_min_fit)
    return min(f for f in floors if f is not None) - 1


def dismiss_low_fit(cfg: Any, store: Any, ids: Optional[Iterable[str]] = None,
                    log: Callable[[str], None] = lambda s: None) -> list[str]:
    """Dismiss the jobs in `ids` (every stored job when None) whose signal score is cfg.dismiss_at_or_below_fit or
    less. Returns the ones dismissed."""
    limit = cfg.dismiss_at_or_below_fit
    if limit is None:
        return []
    state = store.state()
    skip = tracked_ids(store.root) | starred_ids(store.root)
    out = []
    for jid in (state if ids is None else ids):
        s = state.get(jid) or {}
        fit = (s.get("triage") or {}).get("fit_score")
        if (fit is None or fit > limit or fit >= forward_floor(cfg, jid) or s.get("dismissed") or s.get("kept")
                or jid in skip):
            continue
        store.update(jid, dismissed=True, low_fit=now_iso())
        out.append(jid)
    if out:
        log(f"Dismissed {len(out)} low-fit job(s) (scored {limit} or less). A job you restore won't be dismissed again.")
    return out
