"""The ready-to-send list on the Tracker.

A résumé is ready when it has been made and the role is still before Applied. Beside those, the list
names three things that should happen first: a fit report's questions that haven't been answered, a
referral still at To ask, and a posting that closed after the résumé was made.

Answering a question adds a confirmed fact, the same way the Impact record does, and remembers the
question so it leaves the list. "Already in my record" remembers it without adding a second fact.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import Any, Optional

from ..store import _write

# Statuses that mean the application has not gone out yet (notion.APPLIED_STATUSES is the rest, plus Not Applying).
NOT_SENT = {"", "Not started", "In progress", "Blocked"}
# A referral still at To ask is worth surfacing until the role is over.
OVER = {"Denied", "Ghosted", "Done", "Not Applying"}
REASONS = ("closed", "questions", "referral", "ready")

_HEADING = re.compile(r"^#{1,6}\s+questions before i finalize\s*#*\s*$", re.I)
_NEXT = re.compile(r"^#{1,6}\s+\S")
_FENCE = re.compile(r"^\s*(```|~~~)")
_ITEM = re.compile(r"^\s*(?:(\d+)[.)]|[-*])\s+(\S.*?)\s*$")
_KEY = re.compile(r"^[a-f0-9]{16}$")

# report path -> (mtime, [(key, text), ...]). Reports change when a résumé is made, not while the list is open.
_cache: dict[str, tuple[int, list[tuple[str, str]]]] = {}


def question_key(text: str) -> str:
    norm = re.sub(r"\s+", " ", text.strip().lower())
    return hashlib.sha256(norm.encode()).hexdigest()[:16]


def parse_questions(md: str) -> list[str]:
    """The numbered questions under "Questions before I finalize", in order. A bullet counts only when it asks one."""
    lines, start, fenced = md.splitlines(), None, False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
        elif not fenced and _HEADING.match(line.strip()):
            start = i + 1
            break
    if start is None:
        return []
    out, fenced = [], False
    for line in lines[start:]:
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced and _NEXT.match(line):
            break
        if fenced:
            continue
        m = _ITEM.match(line)
        if not m:
            continue
        text = re.sub(r"\s+", " ", m.group(2)).strip()
        if len(text) < 8 or (not m.group(1) and not text.endswith("?")):
            continue
        if text not in out:
            out.append(text)
    return out


def questions_in(path: Optional[Path]) -> list[tuple[str, str]]:
    """(key, text) for each question in this report, re-read when the file changes."""
    if path is None:
        return []
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return []
    hit = _cache.get(str(path))
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    found = [(question_key(q), q) for q in parse_questions(text)]
    _cache[str(path)] = (mtime, found)
    return found


def is_key(key: str) -> bool:
    return bool(_KEY.fullmatch(key or ""))


class Answered:
    """Questions already dealt with, per job: data/ready.json, {answered: {job id: [question keys]}}."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def get(self) -> dict[str, set[str]]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}
        out = {}
        for jid, keys in (data.get("answered") or {}).items():
            if isinstance(keys, list):
                out[str(jid)] = {k for k in keys if is_key(str(k))}
        return out

    def add(self, jid: str, key: str) -> None:
        with self._lock:
            try:
                data = json.loads(self.path.read_text())
            except (OSError, ValueError):
                data = {}
            answered = data.get("answered")
            if not isinstance(answered, dict):
                answered = {}
            keys = [k for k in (answered.get(jid) or []) if is_key(str(k))]
            if key not in keys:
                keys.append(key)
            answered[jid] = keys
            data["answered"] = answered
            _write(self.path, data)


def inbox(rows: list[dict], questions: dict[str, list[tuple[str, str]]], answered: dict[str, set[str]]) -> list[dict]:
    """One row per tracked role that has something to do before it goes out, or a referral still at To ask.

    `questions` is job id -> the report's questions. A role can carry more than one reason. Closed wins
    over ready: a résumé for a posting that has closed is not ready to send."""
    items = []
    for r in rows:
        if not r.get("tracker_id") or r.get("dismissed"):
            continue
        status = r.get("status") or ""
        if status in OVER:
            continue
        pending = status in NOT_SENT
        reasons: list[str] = []
        open_q: list[dict[str, str]] = []
        if r.get("tailored") and pending and r.get("closed"):
            reasons.append("closed")
        elif r.get("tailored") and pending:
            done = answered.get(r["id"]) or set()
            open_q = [{"key": k, "text": t} for k, t in questions.get(r["id"]) or [] if k not in done]
            if open_q:
                reasons.append("questions")
            reasons.append("ready")
        if r.get("referral_url") and (r.get("referral_step") or "toask") == "toask":
            reasons.append("referral")
        if not reasons:
            continue
        fit = r.get("impact_score")
        if fit is None:
            fit = r.get("fit")
        items.append({
            "id": r["id"], "title": r.get("title") or "", "company": r.get("company") or "",
            "fit": fit, "reasons": reasons, "questions": open_q,
            "referral_name": r.get("referral_name") or "", "closed": r.get("closed") or "",
        })
    items.sort(key=lambda it: (min(REASONS.index(x) for x in it["reasons"]),
                               -(it["fit"] or 0), it["company"].lower(), it["title"].lower()))
    return items


def counts(items: list[dict]) -> dict[str, int]:
    c = {k: 0 for k in (*REASONS, "total")}
    for it in items:
        for k in it["reasons"]:
            c[k] += 1
    c["total"] = len(items)
    return c
