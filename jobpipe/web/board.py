"""Fast reads for the web app's job board.

The pipeline runs as a separate process and writes data/ while the server is up, so nothing
here is cached blindly: files are re-read only when their mtime or size changes. Tracked jobs
come from the local tracker (jobpipe/tracker.py), which is synced with Notion in the
background, so no request waits on Notion.

Stars and referral links live in data/marks.json, a file only the web app writes, so they
never race the pipeline's writes to state.json.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from .. import regions
from ..jd import posting_key, posting_url
from ..jobs_api import salary_text, usd_pay, work_mode
from ..startups import StartupStore, funding_block, funding_line
from ..store import Store, _write, canonical_url, month_key, now_iso
from ..tracker import LocalTracker


class JsonFile:
    """A JSON file parsed once, then re-read only when its mtime or size changes."""

    def __init__(self, path: Path, default: Callable[[], Any]):
        self.path, self.default = path, default
        self._sig: Any = ()
        self._data: Any = None
        self._lock = threading.Lock()

    def sig(self) -> Optional[tuple[int, int]]:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None
        return st.st_mtime_ns, st.st_size

    def read(self) -> Any:
        sig = self.sig()
        with self._lock:
            if sig != self._sig:
                try:
                    self._data = json.loads(self.path.read_text()) if sig else self.default()
                except FileNotFoundError:
                    sig, self._data = None, self.default()
                self._sig = sig
            return self._data


class Marks:
    """Per-job stars, referrals and the outside résumé picked for the job:
    {job id: {starred, starred_at, referral_url, referral_name, referral_step, referral_dates, referral_note, resume_source}}.
    referral_url is a web link or a mailto: address; referral_dates holds the day each step was reached."""
    FIELDS = ("starred", "referral_url", "referral_name", "referral_step", "referral_dates", "referral_note", "resume_source")

    def __init__(self, path: Path):
        self.file = JsonFile(path, dict)
        self._lock = threading.Lock()

    def sig(self):
        return self.file.sig()

    def get(self, *keys: str) -> dict:
        data = self.file.read()
        return next((data[k] for k in keys if data.get(k)), {})

    def set(self, key: str, *aliases: str, **fields: Any) -> dict:
        """Update one job's marks. Marks saved under an alias (the job's Notion-only id from
        before a search found it) move to `key`."""
        with self._lock:
            data = dict(self.file.read())
            rec: dict = {}
            for k in (*aliases, key):
                rec.update(data.pop(k, None) or {})
            rec.update(fields)
            if "starred" in fields:
                if fields["starred"]:
                    rec["starred_at"] = now_iso()
                else:
                    rec.pop("starred_at", None)
            rec = {k: v for k, v in rec.items() if v}
            if rec:
                data[key] = rec
            _write(self.file.path, data)
            return rec


def clean_url(raw: Any) -> str:
    """A referral link as typed: '' clears it, a bare domain gets https://. Raises ValueError."""
    url = str(raw or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    p = urlsplit(url)
    if p.scheme not in ("http", "https") or "." not in p.netloc or any(c.isspace() for c in url) or len(url) > 2000:
        raise ValueError("Enter a web address, e.g. https://www.linkedin.com/in/name")
    return url


# Where a referral stands, in order.
REFERRAL_STEPS = ("toask", "asked", "referred", "thanked")
_EMAIL = re.compile(r"[^@\s/:]+@[^@\s/]+\.[^@\s/.]+")


def clean_contact(raw: Any) -> str:
    """The person referring you, as typed: a web link (see clean_url) or an email address, kept as
    mailto:address. '' clears it. Raises ValueError."""
    text = str(raw or "").strip()
    addr = text[7:] if text.lower().startswith("mailto:") else text
    if _EMAIL.fullmatch(addr) and len(addr) <= 254:
        return "mailto:" + addr
    try:
        return clean_url(text)
    except ValueError:
        raise ValueError("Enter a LinkedIn link, a referral link or an email address") from None


class TrackerSync:
    """Keeps the local tracker in step with Notion without making a request wait for it.

    rows() answers from the local tracker and starts a background sync when the last one is older
    than `ttl`. It only waits for Notion when the tracker has never been filled. kick() starts a
    sync now (after a change made here), refresh() runs one and raises if Notion fails.
    """

    def __init__(self, tracker: LocalTracker, ttl: float = 60.0, after: Callable[[], None] = lambda: None):
        self.tracker, self.ttl, self.after = tracker, ttl, after   # after: called once a sync has read Notion
        self.at = 0.0            # when a sync was last tried
        self._idle = threading.Event()
        self._idle.set()
        self._again = False

    @property
    def syncing(self) -> bool:
        return not self._idle.is_set()

    def rows(self) -> list[dict]:
        t = self.tracker
        if t.notion is not None and time.time() - self.at > self.ttl:
            if not t.rows() and t.state()["synced"] is None:
                try:           # nothing to show yet: wait for Notion once (a failure waits out the ttl)
                    self.refresh()
                except Exception:  # noqa: BLE001 - the board still shows local jobs; the tracker kept the reason
                    pass
            else:
                self.kick()
        return t.rows()

    def kick(self) -> None:
        if self.tracker.notion is None:
            return
        if not self._idle.is_set():
            self._again = True     # a change arrived mid-sync: go round once more so it isn't left waiting
            return
        self._idle.clear()
        threading.Thread(target=self._background, daemon=True).start()

    def _background(self) -> None:
        try:
            while True:
                self._again = False
                try:
                    self.refresh()
                except Exception:  # noqa: BLE001 - reported to the UI as notion_error
                    break
                if not self._again:
                    break
        finally:
            self._idle.set()

    def refresh(self) -> None:
        self.at = time.time()
        self.tracker.sync()
        self.after()

    def wait(self, timeout: float = 20.0) -> None:
        self._idle.wait(timeout)


class JobBoard:
    """Local jobs merged with the Notion rows and the marks, cached as ready-to-send JSON."""

    def __init__(self, store: Store, sync: TrackerSync, marks: Marks,
                 run_scores: Callable[[str], dict], sent=None, startups: Optional[StartupStore] = None):
        self.store, self.sync, self.marks, self.sent = store, sync, marks, sent
        self.startups = startups or StartupStore(store.root / "startups.json")
        self.run_scores = run_scores
        self.state_file = JsonFile(store.state_path, dict)
        self.credits_file = JsonFile(store.credits_path, dict)
        self._jobs: dict[str, tuple[int, dict]] = {}     # job id -> (file mtime, list fields)
        self._scores: dict[str, dict] = {}                # run dir -> scores read from its files
        self._scores_sig: Any = ()
        self._key: Any = None
        self._rows: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._json = b"[]"
        self._hash = ""
        self._lock = threading.Lock()

    def state(self) -> dict:
        return self.state_file.read()

    def credits_used(self, period: str) -> int:
        c = self.credits_file.read()
        if period == "monthly":
            return c.get("months", {}).get(month_key(), 0)
        return c.get("lifetime", 0)

    def _job(self, jid: str) -> Optional[dict]:
        """The posting fields the list shows (not the description), re-read when the file changes."""
        path = self.store.jobs_dir / f"{jid}.json"
        try:
            mtime = os.stat(path).st_mtime_ns
        except FileNotFoundError:
            self._jobs.pop(jid, None)
            return None
        hit = self._jobs.get(jid)
        if hit and hit[0] == mtime:
            return hit[1]
        try:
            job = json.loads(path.read_text())
        except (OSError, ValueError):
            return None
        # Fully remote: the listing says remote and doesn't also say hybrid, or its location does ("Remote USA").
        remote = (bool(job.get("remote")) and not job.get("hybrid")
                  and (job.get("work_arrangement") or "remote").lower() == "remote") or says_remote(job.get("location") or "")
        mode = work_mode(job)
        lo, hi = usd_pay(job)                   # the source's figures, or a dollar range in its pay text
        slim = {"title": job.get("job_title") or "", "company": job.get("company") or "",
                "location": job.get("location") or "", "mode": "Remote" if remote else "Hybrid" if mode == "Remote" else mode,
                "remote": remote, "pay": salary_text(job), "pay_usd": hi or lo or None,   # the number the list sorts by
                "url": posting_url(job), "posted": job.get("date_posted") or "",
                "no_posting": not (job.get("description") or "").strip(),   # Run full score and Make résumé wait for it
                "company_domain": job.get("company_domain") or "", "funding": job.get("funding"),
                "source": job.get("source") or ("Himalayas" if "himalayas.app/" in posting_url(job) else ""),
                "regions": regions.areas([*(job.get("country_codes") or []), job.get("country_code") or ""],
                                         job.get("location") or "", job.get("job_title") or ""),
                "place": regions.place([*(job.get("country_codes") or []), job.get("country_code") or ""], job.get("location") or "")}
        self._jobs[jid] = (mtime, slim)
        return slim

    def _build(self, state: dict, rows_n: list[dict]) -> list[dict]:
        by_url = {canonical_url(r["job_url"]): r for r in rows_n if r["job_url"]}
        by_job = {r["job_id"]: r for r in rows_n if r["job_id"]}
        out, seen = [], set()
        for jid, st in state.items():
            job = self._job(jid)
            if not job:
                continue
            n = by_job.get(jid) or by_url.get(canonical_url(job["url"]))
            tri = st.get("triage") or {}
            if st.get("run_dir") and st.get("ats_total") is None:
                if st["run_dir"] not in self._scores:
                    self._scores[st["run_dir"]] = self.run_scores(st["run_dir"])
                st = {**st, **self._scores[st["run_dir"]]}
            m = self.marks.get(jid, "notion-" + n["page_id"]) if n else self.marks.get(jid)
            if n:
                seen.add(n["page_id"])
            out.append({
                "id": jid, "title": job["title"] or st.get("exact_title") or "", "company": job["company"],
                "location": job["location"], "mode": job["mode"], "remote": job["remote"], "pay": job["pay"], "pay_usd": job["pay_usd"],
                "url": job["url"], "source": job.get("source", ""), "funding": self._funding(job),
                "regions": job["regions"], "place": job.get("place", ""), "no_posting": job.get("no_posting", False),
                "fit": tri.get("fit_score") if tri else None, "one_line": tri.get("one_line", ""),
                "searches": st.get("searches", []), "tailored": bool(st.get("run_dir")),
                "impact_score": st.get("impact_score"), "resume_score": st.get("resume_score"),
                "ats_total": st.get("ats_total"), "posted": job["posted"], "first_seen": st.get("first_seen") or "",
                "status": n["status"] if n else "", "local": True, "dismissed": bool(st.get("dismissed")),
                "closed": st.get("closed_at") or "", "low_fit": st.get("low_fit") or "",
                **_tracker_fields(n),
                **_mark_fields(m),
                "sent_resume": self._sent_name(n),
            })
        for r in rows_n:
            if r["page_id"] in seen:
                continue
            title = r["name"]
            if r["company"] and title.endswith(f" — {r['company']}"):
                title = title[: -len(r["company"]) - 3]
            jid = "notion-" + r["page_id"]
            remote = says_remote(r["notes"] or "")   # a row made in Notion has no location; its notes often say
            out.append({"id": jid, "title": title, "company": r["company"], "location": "", "mode": "Remote" if remote else "",
                        "remote": remote, "pay": "", "pay_usd": None, "funding": self._funding({"company": r["company"]}), "regions": regions.areas([], "", title), "place": "",
                        "url": r["job_url"], "fit": r["fit"], "one_line": r["notes"], "searches": [],
                        "tailored": False, "impact_score": None, "resume_score": None, "ats_total": None,
                        "posted": "", "first_seen": "", **_tracker_fields(r),
                        "status": r["status"], "local": False, "dismissed": False, "closed": "",
                        **_mark_fields(self.marks.get(jid)), "sent_resume": self._sent_name(r)})
        # Best fit first: the full score where there is one, else the signal score.
        out.sort(key=lambda r: (-(r["impact_score"] or r["fit"] or 0), r["company"].lower(), r["title"].lower()))
        return out

    def _funding(self, job: dict) -> Optional[dict]:
        """The round chip for a job: the funding block the job carries (a role added from a startup), else the
        startup its company matches in data/startups.json (by website, then name)."""
        f = job.get("funding")
        if not f:
            s = self.startups.for_job(job)
            if not s or s.get("exited"):     # a company a VC board lists after it was bought or went public isn't a startup
                return None
            f = funding_block(s)
        if (f.get("stage") or "Unknown") == "Unknown" and not (f.get("round") or f.get("amount") or f.get("batch")):
            return None
        return {**f, "line": funding_line(f)}

    def _sent_name(self, n: Optional[dict]) -> str:
        """The file name of the résumé sent for this tracked job: recorded here, else in Notion's Resume Used."""
        if not n:
            return ""
        mine = self.sent.index.read().get(n["page_id"]) if self.sent else None
        if mine and mine["source"] != "notion":
            return mine["name"]
        files = n.get("resume_files") or []
        return files[0] if files else ""

    def _refresh(self) -> None:
        # Read the version before the rows: a background sync landing in between then costs
        # one extra rebuild, instead of caching old rows under the new version.
        version = self.sync.tracker.version()
        rows_n = self.sync.rows()
        state = self.state()
        state_sig = self.state_file.sig()
        if state_sig != self._scores_sig:
            self._scores, self._scores_sig = {}, state_sig
        mtimes = []
        for jid in state:
            try:
                mtimes.append(os.stat(self.store.jobs_dir / f"{jid}.json").st_mtime_ns)
            except FileNotFoundError:
                mtimes.append(0)
        key = (state_sig, tuple(mtimes), version, self.marks.sig(), self.sent.sig() if self.sent else None, self.startups.sig())
        if key == self._key:
            return
        self._rows = self._build(state, rows_n)
        self._by_id = {r["id"]: r for r in self._rows}
        self._json = json.dumps(self._rows, separators=(",", ":")).encode()
        self._hash = hashlib.blake2b(self._json, digest_size=8).hexdigest()
        self._key = key

    def row(self, jid: str) -> Optional[dict]:
        with self._lock:
            self._refresh()
            return self._by_id.get(jid)

    def rows(self) -> list[dict]:
        """Every row on the board, as /api/jobs sends them."""
        with self._lock:
            self._refresh()
            return list(self._rows)

    def by_tracker(self, row_id: str) -> Optional[dict]:
        """The row for this tracker id."""
        with self._lock:
            self._refresh()
            return next((r for r in self._rows if r["tracker_id"] == row_id), None)

    def by_posting(self, url: str) -> Optional[dict]:
        """The row for this posting link, found or tracked already."""
        key = posting_key(url)
        if not key:
            return None
        with self._lock:
            self._refresh()
            return next((r for r in self._rows if r["url"] and posting_key(r["url"]) == key), None)

    def payload(self) -> tuple[bytes, str]:
        """The /api/jobs response body and its ETag."""
        with self._lock:
            self._refresh()
            t = self.sync.tracker.state()
            tail = {"notion": t["notion"], "syncing": self.sync.syncing, "notion_error": t["error"],
                    "notion_synced": t["synced"], "pending": t["pending"]}
            flags = f"{int(tail['notion'])}{int(tail['syncing'])}{int(bool(tail['notion_error']))}{tail['pending']}"
            return (b'{"jobs":' + self._json + b"," + json.dumps(tail).encode()[1:],
                    f'"{self._hash}-{flags}"')


_REMOTE = re.compile(r"\bremote\b", re.I)
_NOT_REMOTE = re.compile(r"\bhybrid\b|\b(?:not|no|non)[\s-]+remote\b|\bremote[\s-]+(?:optional|friendly|possible|not)\b", re.I)


def says_remote(text: str) -> bool:
    """A location or note that says the role is remote ("Remote USA", "All-remote"), and not hybrid,
    "not remote", "remote not stated" or only "remote-friendly"."""
    return bool(_REMOTE.search(text)) and not _NOT_REMOTE.search(text)


def _tracker_fields(n: Optional[dict]) -> dict:
    """tracker_id: the job is tracked. notion_page_id: its row exists in Notion. pending: it has a change Notion hasn't had yet.
    applied_on: the day it was applied to (YYYY-MM-DD), for the dashboard. status_since: the day its status last changed (by you).
    ghosted_on, ghosted_from: the day the app moved it to Ghosted on its own, and the status it had then.
    reached: the furthest stage it got to (Applied, Interviewing, Offer), for the dashboard's application flow."""
    if not n:
        return {"tracker_id": None, "notion_page_id": None, "notion_url": None, "pending": False, "applied_on": "",
                "status_since": "", "ghosted_on": "", "ghosted_from": "", "reached": ""}
    return {"tracker_id": n["page_id"], "notion_page_id": n["notion_page_id"], "notion_url": n["page_url"] or None,
            "pending": n["pending"], "applied_on": n.get("applied_on", ""), "status_since": n.get("status_since", ""),
            "ghosted_on": n.get("ghosted_on", ""), "ghosted_from": n.get("ghosted_from", ""),
            "reached": n.get("reached", "")}


def referral_fields(m: dict) -> dict:
    return {"referral_url": m.get("referral_url", ""), "referral_name": m.get("referral_name", ""),
            "referral_step": m.get("referral_step", ""), "referral_dates": m.get("referral_dates") or {},
            "referral_note": m.get("referral_note", "")}


def _mark_fields(m: dict) -> dict:
    return {"starred": bool(m.get("starred")), **referral_fields(m), "resume_source": m.get("resume_source", "")}
