"""The job tracker: which jobs are being tracked and where each one stands.

The source of truth is a local SQLite file (data/tracker.db), so tracking a job or changing a
status works at once and offline. The Notion tracker is a synced copy:

- A change made here (track a job, set a status, a tailoring run's scores) is saved locally and
  queued. sync() sends the queue to Notion.
- sync() then reads Notion's rows. Jobs added there (for example by the resume-job-fit skill)
  appear here, and a status edited in Notion replaces the local one, unless this side has an
  unsent change for that job, which wins.
- A job with no URL stays local: Notion rows are matched by Job URL.

When Notion can't be reached nothing is lost: the queue waits and the next sync sends it.

Each row also keeps the day it reached an applied stage (Applied, Interviewing, ... see APPLIED_STATUSES),
set here or by a sync, for the dashboard's applications per day, and the day its status last changed: a job left
at Applied or Interviewing that long (ghost_stale) moves to Ghosted on its own.
"""
from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .notion import APPLIED_STATUSES, STATUSES, NotionTracker, StatusRefused, TrackerEntry, merge_notes
from .store import canonical_url, now_iso

try:
    import fcntl
except ImportError:      # Windows: only the thread lock, which covers one process
    fcntl = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracker (
  id TEXT PRIMARY KEY,             -- the Notion page id for rows first seen there, local-<hex> for rows tracked here
  notion_page_id TEXT UNIQUE,      -- null until the row exists in Notion
  page_url TEXT NOT NULL DEFAULT '',
  job_id TEXT,                     -- the stored job this row was tracked from, if any
  name TEXT NOT NULL DEFAULT '', company TEXT NOT NULL DEFAULT '',
  job_url TEXT NOT NULL DEFAULT '', url_key TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT '', fit REAL, notes TEXT NOT NULL DEFAULT '',
  priority TEXT NOT NULL DEFAULT '', due TEXT NOT NULL DEFAULT '',
  entry TEXT,                      -- queued for Notion: the TrackerEntry to create or update there
  dirty_status INTEGER NOT NULL DEFAULT 0,   -- queued for Notion: the status was set here
  updated TEXT NOT NULL DEFAULT '',
  resume_files TEXT NOT NULL DEFAULT '[]',   -- names of the files in Notion's Resume Used
  resume_upload TEXT,                        -- queued for Notion: a PDF to set as Resume Used
  applied TEXT NOT NULL DEFAULT '',          -- the local day (YYYY-MM-DD) the status reached an applied stage
  status_since TEXT NOT NULL DEFAULT '',     -- the local day the status last changed (a move to Ghosted keeps it)
  ghosted_on TEXT NOT NULL DEFAULT '',       -- the day ghost_stale moved it to Ghosted ('' if you set it, or it moved on)
  ghosted_from TEXT NOT NULL DEFAULT ''      -- the status it had then
);
CREATE INDEX IF NOT EXISTS tracker_url ON tracker(url_key);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""
FROM_NOTION = ("page_url", "name", "company", "job_url", "fit", "notes", "priority", "due", "resume_files")
# Columns added after the first release, for tracker.db files made before them.
ADDED = {"resume_files": "TEXT NOT NULL DEFAULT '[]'", "resume_upload": "TEXT", "applied": "TEXT NOT NULL DEFAULT ''",
         "status_since": "TEXT NOT NULL DEFAULT ''", "ghosted_on": "TEXT NOT NULL DEFAULT ''",
         "ghosted_from": "TEXT NOT NULL DEFAULT ''"}
# The stages a job is moved to Ghosted from when its status hasn't changed in ghost_after_days.
GHOSTABLE = ("Applied", "Interviewing")


class LocalTracker:
    def __init__(self, path: Path, notion: Optional[NotionTracker] = None, seed: Optional[Path] = None):
        self.path, self.notion = path, notion
        self._sync_lock = threading.Lock()
        self._cache: tuple[int, list[dict]] = (-1, [])
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._db()) as con, con:
            con.executescript(SCHEMA)
            have = {r["name"] for r in con.execute("PRAGMA table_info(tracker)")}
            for col, decl in ADDED.items():
                if col not in have:
                    con.execute(f"ALTER TABLE tracker ADD COLUMN {col} {decl}")
                    if col == "applied":
                        # Rows applied to before this column: the day the row last changed is the best guess.
                        con.executemany("UPDATE tracker SET applied = ? WHERE id = ?", [
                            (_local_day(r["updated"]), r["id"]) for r in con.execute(
                                f"SELECT id, updated FROM tracker WHERE status IN ({','.join('?' * len(APPLIED_STATUSES))})",
                                APPLIED_STATUSES)])
                    if col == "status_since":
                        # Rows from before this column: an Applied row got there on its applied day; for the rest the
                        # day the row last changed is the best guess (no earlier, so nothing is ghosted too soon).
                        con.executemany("UPDATE tracker SET status_since = ? WHERE id = ?", [
                            (r["applied"] if r["status"] == "Applied" and r["applied"] else _local_day(r["updated"]), r["id"])
                            for r in con.execute("SELECT id, status, applied, updated FROM tracker")])
            con.execute("PRAGMA journal_mode=WAL")
        if seed is not None:
            self._seed(seed)

    def _db(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def _seed(self, snapshot: Path) -> None:
        """First run after the tracker moved here from Notion: start from the web app's last Notion snapshot."""
        try:
            saved = json.loads(snapshot.read_text())
        except (OSError, ValueError):
            return
        with closing(self._db()) as con, con:
            if con.execute("SELECT 1 FROM tracker LIMIT 1").fetchone():
                return
            for r in saved.get("rows") or []:
                self._insert_from_notion(con, r)
            self._bump(con)

    # ---- reads ----------------------------------------------------------------
    def _meta(self, key: str, default: Any = None) -> Any:
        with closing(self._db()) as con:
            row = con.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def _set_meta(self, con: sqlite3.Connection, **values: Any) -> None:
        con.executemany("INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        [(k, json.dumps(v)) for k, v in values.items()])

    def _bump(self, con: sqlite3.Connection) -> None:
        row = con.execute("SELECT value FROM meta WHERE key = 'version'").fetchone()
        self._set_meta(con, version=(json.loads(row["value"]) if row else 0) + 1)

    def version(self) -> int:
        """Changes whenever a row does, in this process or another (the pipeline writes here too)."""
        return self._meta("version", 0)

    def rows(self) -> list[dict]:
        """Every tracked job, in the shape the Notion rows had: page_id is the row's id here."""
        version = self.version()
        if self._cache[0] != version:
            with closing(self._db()) as con:
                found = con.execute("SELECT * FROM tracker ORDER BY rowid").fetchall()
            self._cache = (version, [{
                "page_id": r["id"], "notion_page_id": r["notion_page_id"], "page_url": r["page_url"], "job_id": r["job_id"],
                "name": r["name"], "company": r["company"], "job_url": r["job_url"], "status": r["status"],
                "fit": r["fit"], "notes": r["notes"], "priority": r["priority"], "due": r["due"],
                "resume_files": json.loads(r["resume_files"] or "[]"),
                "applied_on": _applied_on(r["status"], r["due"], r["applied"]), "status_since": r["status_since"],
                "ghosted_on": r["ghosted_on"], "ghosted_from": r["ghosted_from"],
                "pending": bool(r["dirty_status"] or (r["entry"] and r["job_url"]) or r["resume_upload"])} for r in found])
        return self._cache[1]

    def state(self) -> dict:
        """For the UI: is there a Notion copy, when it last answered, what went wrong, how much is queued."""
        return {"notion": self.notion is not None, "synced": self._meta("synced"), "error": self._meta("error", ""),
                "pending": sum(r["pending"] for r in self.rows()) if self.notion is not None else 0}

    def known_urls(self) -> dict[str, str]:
        """canonical Job URL -> Status for every tracked job (the pipeline skips jobs already applied to)."""
        try:
            self.sync()
        except Exception:  # noqa: BLE001 - Notion down: the local rows are still the best answer
            pass
        return {canonical_url(r["job_url"]): r["status"] for r in self.rows() if r["job_url"]}

    # ---- writes: saved here, queued for Notion -------------------------------------
    def set_status(self, row_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        with closing(self._db()) as con, con:
            done = con.execute("UPDATE tracker SET status = ?, dirty_status = 1, updated = ?, "
                               "applied = CASE WHEN ? THEN CASE WHEN applied = '' THEN ? ELSE applied END ELSE '' END, "
                               f"{_SINCE} WHERE id = ?",
                               (status, now_iso(), status in APPLIED_STATUSES, _today(), status, _today(), status, status,
                                row_id))
            if not done.rowcount:
                raise KeyError(row_id)
            self._bump(con)

    def queue_resume(self, row_id: str, pdf: Path) -> None:
        """Send this PDF to Notion as the row's Resume Used at the next sync (once the row is there)."""
        with closing(self._db()) as con, con:
            if not con.execute("UPDATE tracker SET resume_upload = ?, updated = ? WHERE id = ?",
                               (str(pdf), now_iso(), row_id)).rowcount:
                raise KeyError(row_id)
            self._bump(con)

    def link_job(self, row_id: str, job_id: str) -> None:
        """Note the stored job that holds this row's posting, so the two show as one role."""
        with closing(self._db()) as con, con:
            if con.execute("UPDATE tracker SET job_id = ? WHERE id = ? AND job_id IS NOT ?",
                           (job_id, row_id, job_id)).rowcount:
                self._bump(con)

    def rename_jobs(self, moved: dict[str, str]) -> None:
        """Rows linked to a stored job that moved to a new id ({old: new}, Store.fix_ids) follow it."""
        with closing(self._db()) as con, con:
            if sum(con.execute("UPDATE tracker SET job_id = ? WHERE job_id = ?", (new, old)).rowcount
                   for old, new in moved.items()):
                self._bump(con)

    def track(self, e: TrackerEntry, job_id: Optional[str] = None) -> str:
        """Add the job, or update its row, by the rules the Notion tracker always had: an existing row keeps
        its status except Not started -> In progress, keeps the notes you wrote, and gets the new fit score.
        Returns "created" or "updated"."""
        key = canonical_url(e.url)
        queued = json.dumps(e.__dict__) if key else None
        with closing(self._db()) as con, con:
            row = (con.execute("SELECT * FROM tracker WHERE job_id = ?", (job_id,)).fetchone() if job_id else None) \
                or (con.execute("SELECT * FROM tracker WHERE url_key = ?", (key,)).fetchone() if key else None)
            if row is None:
                con.execute("INSERT INTO tracker(id, job_id, name, company, job_url, url_key, status, fit, notes, entry, updated, "
                            "status_since) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                            ("local-" + secrets.token_hex(8), job_id, f"{e.title} — {e.company}", e.company, e.url, key,
                             e.status, e.fit_score, e.notes, queued, now_iso(), _today()))
                action = "created"
            else:
                status = "In progress" if row["status"] in ("", "Not started") and e.status == "In progress" else row["status"]
                if row["url_key"] or key:     # Notion matches rows by Job URL, so the queued entry must carry one
                    queued = json.dumps({**e.__dict__, "url": e.url or row["job_url"]})
                con.execute("UPDATE tracker SET job_id = COALESCE(?, job_id), status = ?, fit = COALESCE(?, fit), notes = ?, "
                            "job_url = CASE WHEN job_url = '' THEN ? ELSE job_url END, "
                            f"url_key = CASE WHEN url_key = '' THEN ? ELSE url_key END, entry = ?, updated = ?, {_SINCE} "
                            "WHERE id = ?",
                            (job_id, status, e.fit_score, merge_notes(row["notes"], e.notes), e.url, key,
                             queued, now_iso(), status, _today(), status, status, row["id"]))
                action = "updated"
            self._bump(con)
        return action

    def start(self, e: TrackerEntry, job_id: Optional[str] = None) -> str:
        """A job whose résumé is being tailored shows In progress at once. A tracked row moves there from Not
        started (a later stage stays); an untracked job is added here but not queued for Notion: the run tracks
        it with its scores when it ends, and a page made now would keep this entry's thin body.
        Returns "created", "updated" or "unchanged"."""
        key = canonical_url(e.url)
        with closing(self._db()) as con, con:
            row = (con.execute("SELECT * FROM tracker WHERE job_id = ?", (job_id,)).fetchone() if job_id else None) \
                or (con.execute("SELECT * FROM tracker WHERE url_key = ?", (key,)).fetchone() if key else None)
            if row is None:
                con.execute("INSERT INTO tracker(id, job_id, name, company, job_url, url_key, status, fit, notes, updated, "
                            "status_since) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            ("local-" + secrets.token_hex(8), job_id, f"{e.title} — {e.company}", e.company, e.url, key,
                             "In progress", e.fit_score, e.notes, now_iso(), _today()))
                action = "created"
            elif row["status"] in ("", "Not started"):
                con.execute("UPDATE tracker SET job_id = COALESCE(?, job_id), status = 'In progress', dirty_status = 1, "
                            "updated = ?, status_since = ? WHERE id = ?", (job_id, now_iso(), _today(), row["id"]))
                action = "updated"
            else:
                return "unchanged"
            self._bump(con)
        return action

    def upsert(self, e: TrackerEntry, job_id: Optional[str] = None) -> tuple[str, str]:
        """track(), then send it to Notion now if Notion answers. Returns (action, Notion page URL or "")."""
        action = self.track(e, job_id)
        try:
            self.sync()
        except Exception:  # noqa: BLE001 - it stays queued; state()["error"] has the reason
            pass
        key = canonical_url(e.url)
        return action, next((r["page_url"] for r in self.rows() if key and canonical_url(r["job_url"]) == key), "")

    def ghost_stale(self, days: int, today: Optional[date] = None) -> list[dict]:
        """Move the jobs whose status has been Applied or Interviewing for `days` days or more to Ghosted (queued for
        Notion like a status set here). status_since stays the day it reached that status, so the app can say how long
        it waited. 0 days: never. Returns the jobs moved: id, name and the status they had."""
        if days <= 0:
            return []
        today = today or date.today()
        cutoff = (today - timedelta(days=days)).isoformat()
        with closing(self._db()) as con, con:
            stale = con.execute(f"SELECT id, name, status FROM tracker WHERE status IN ({','.join('?' * len(GHOSTABLE))}) "
                                "AND status_since != '' AND status_since <= ?", (*GHOSTABLE, cutoff)).fetchall()
            for r in stale:
                con.execute("UPDATE tracker SET status = 'Ghosted', dirty_status = 1, updated = ?, ghosted_on = ?, "
                            "ghosted_from = ? WHERE id = ?", (now_iso(), today.isoformat(), r["status"], r["id"]))
            if stale:
                self._bump(con)
        return [{"id": r["id"], "name": r["name"], "from": r["status"]} for r in stale]

    # ---- sync with Notion -----------------------------------------------------------
    def sync(self) -> None:
        """Send the queue, then read Notion's rows. Raises what Notion raises; the queue is kept."""
        if self.notion is None:
            return
        with self._syncing():
            try:
                refused = self._push()
                self._pull()
                if refused:      # the rest went through; this status waits in the queue until Notion has its option
                    raise refused
            except Exception as e:
                with closing(self._db()) as con, con:
                    self._set_meta(con, error=str(e), tried=time.time())
                raise
            with closing(self._db()) as con, con:
                self._set_meta(con, error="", synced=time.time(), tried=time.time())

    @contextmanager
    def _syncing(self):
        """One sync at a time, across processes too: the app and a run's own process (scoring startup roles,
        tailoring) each open tracker.db, and two syncs sending the same queued job at once each found no page
        for it in Notion and made one, so the job was there twice."""
        with self._sync_lock, open(self.path.with_name(self.path.name + ".sync-lock"), "a") as f:
            if fcntl is not None:
                fcntl.flock(f, fcntl.LOCK_EX)      # let go when the file closes
            yield

    def _push(self) -> Optional[StatusRefused]:
        """Send the queue. A status Notion has no option for stays queued and the rest still go: returns that error."""
        refused = None
        with closing(self._db()) as con:
            queued = con.execute("SELECT * FROM tracker WHERE (entry IS NOT NULL AND url_key != '') OR dirty_status = 1 "
                                 "OR resume_upload IS NOT NULL").fetchall()
        for row in queued:
            page_id = row["notion_page_id"]
            if row["entry"] and row["url_key"]:
                # upsert finds the row by Job URL first, so a retry after a half-finished attempt can't add a duplicate
                _, page = self.notion.upsert_page(TrackerEntry(**json.loads(row["entry"])))
                page_id = page["id"]
                with closing(self._db()) as con, con:
                    # another local row may already hold this page (the same job tracked twice): keep this one
                    con.execute("DELETE FROM tracker WHERE notion_page_id = ? AND id != ?", (page_id, row["id"]))
                    con.execute("UPDATE tracker SET notion_page_id = ?, page_url = ? WHERE id = ?",
                                (page_id, page.get("url", ""), row["id"]))
                    # leave the queue alone if something newer was queued while this was being sent
                    con.execute("UPDATE tracker SET entry = NULL WHERE id = ? AND entry = ?", (row["id"], row["entry"]))
                    self._bump(con)
            if row["dirty_status"] and page_id:
                try:
                    self.notion.set_status(page_id, row["status"])
                except StatusRefused as e:
                    refused = refused or e
                else:
                    with closing(self._db()) as con, con:
                        con.execute("UPDATE tracker SET dirty_status = 0 WHERE id = ? AND status = ?", (row["id"], row["status"]))
                        self._bump(con)
            if row["resume_upload"] and page_id:
                # A file already in Notion's Resume Used was put there by hand: it is kept, not replaced.
                pdf = Path(row["resume_upload"])
                if pdf.exists() and not self.notion.resume_files(page_id):
                    self.notion.attach_resume(page_id, pdf.name, pdf.read_bytes())
                with closing(self._db()) as con, con:
                    con.execute("UPDATE tracker SET resume_upload = NULL WHERE id = ? AND resume_upload = ?",
                                (row["id"], row["resume_upload"]))
                    self._bump(con)
        return refused

    def _insert_from_notion(self, con: sqlite3.Connection, r: dict) -> None:
        con.execute("INSERT INTO tracker(id, notion_page_id, page_url, name, company, job_url, url_key, status, fit, notes, "
                    "priority, due, resume_files, updated, applied, status_since) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r["page_id"], r["page_id"], r.get("page_url", ""), r.get("name", ""), r.get("company", ""),
                     r.get("job_url", ""), canonical_url(r.get("job_url")), r.get("status", ""), r.get("fit"),
                     r.get("notes", ""), r.get("priority", ""), r.get("due", ""),
                     _file_names(r.get("resume_files")), now_iso(), _applied(r.get("status", ""), ""), _today()))

    def _pull(self) -> None:
        theirs = self.notion.list_jobs()
        with closing(self._db()) as con, con:
            before = [tuple(r) for r in con.execute("SELECT * FROM tracker ORDER BY id")]
            for r in theirs:
                key = canonical_url(r["job_url"])
                row = con.execute("SELECT * FROM tracker WHERE notion_page_id = ? OR id = ?", (r["page_id"],) * 2).fetchone() \
                    or (con.execute("SELECT * FROM tracker WHERE url_key = ? AND notion_page_id IS NULL", (key,)).fetchone()
                        if key else None)
                if row is None:
                    self._insert_from_notion(con, r)
                    continue
                values = {k: r.get(k, "") if k != "fit" else r.get("fit") for k in FROM_NOTION}
                values["resume_files"] = _file_names(r.get("resume_files"))
                if not row["dirty_status"]:       # an unsent status set here wins over Notion's
                    values["status"] = r["status"]
                    values["applied"] = _applied(r["status"], row["applied"])
                    if r["status"] != row["status"]:     # changed in Notion: its clock starts again
                        values.update(status_since=_today(), ghosted_on="", ghosted_from="")
                for k in ("name", "company", "job_url"):     # an empty field in Notion doesn't blank what's known here
                    if not values[k]:
                        del values[k]
                if key:
                    values["url_key"] = key
                con.execute(f"UPDATE tracker SET notion_page_id = ?, {', '.join(k + ' = ?' for k in values)} WHERE id = ?",
                            (r["page_id"], *values.values(), row["id"]))
            # A row that was in Notion and is gone from it was deleted there, unless it still has something to send.
            # An entirely empty answer is not trusted to mean "everything was deleted".
            ids = [r["page_id"] for r in theirs]
            if ids:
                con.execute(
                    f"DELETE FROM tracker WHERE notion_page_id IS NOT NULL AND dirty_status = 0 AND entry IS NULL "
                    f"AND notion_page_id NOT IN ({','.join('?' * len(ids))})", ids)
            if before != [tuple(r) for r in con.execute("SELECT * FROM tracker ORDER BY id")]:
                self._bump(con)


def _today() -> str:
    return date.today().isoformat()


# SET clauses for a status change (parameters: the new status, today, the new status, the new status): the day the
# status changed, and a move to Ghosted made on its own is forgotten once the status moves on.
_SINCE = ("status_since = CASE WHEN status = ? THEN status_since ELSE ? END, "
          "ghosted_on = CASE WHEN status = ? THEN ghosted_on ELSE '' END, "
          "ghosted_from = CASE WHEN status = ? THEN ghosted_from ELSE '' END")


def _local_day(iso: str) -> str:
    """The local day of a UTC timestamp written by now_iso(), or today if there isn't one."""
    try:
        return datetime.fromisoformat(iso).astimezone().date().isoformat()
    except (TypeError, ValueError):
        return _today()


def _applied(status: str, was: str) -> str:
    """The applied day to keep for a row now at `status`: the day it was first seen in an applied stage."""
    return (was or _today()) if status in APPLIED_STATUSES else ""


def _applied_on(status: str, due: str, applied: str) -> str:
    """The day the job was applied to, for the dashboard. Notion's Due/Submitted wins when it is set and not
    in the future (before applying it can be a deadline); else the day the status became an applied stage."""
    if status not in APPLIED_STATUSES:
        return ""
    day = (due or "")[:10]
    return day if day and day <= _today() else applied


def _file_names(files: Optional[list]) -> str:
    """Notion's file links expire within the hour, so only the names are kept; downloads ask Notion afresh."""
    return json.dumps([f["name"] if isinstance(f, dict) else str(f) for f in files or []])
