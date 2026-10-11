"""Run pipeline commands (`python -m jobpipe ...`) in the background and keep their output.

Up to MAX_RUNS at once, so several jobs can be tailored side by side. A run started with queue=True
when that many are going (Make résumé) waits its turn instead of being refused, and starts by itself
when one finishes. Runs that would collide share a `key` and wait for each other: searches (they spend
JobsPipe credits and write the day's digest) go one at a time, and a job is never tailored by two runs
at once. Each run is told its id (JOBPIPE_RUN_ID), and the store marks the jobs it changes with it, so
a run's results are its own.

Given a database (the app uses data/tracker.db), runs outlive the app. Each one is started through
supervise.py in a session of its own, which saves every line the run prints and its exit status in the
database; the app follows the run there. When the app restarts, it picks up the runs still going and
the ones still waiting their turn (saved too), so restarting the app cuts nothing short. Quitting the
app is different: it stops them (stop_all). A run whose process died without saving its status (the
Mac was shut down) shows as interrupted. Without a database (tests), runs are the app's own child
processes and end with it.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .. import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  label TEXT NOT NULL, args TEXT NOT NULL,   -- args: the command's arguments, as JSON
  started TEXT NOT NULL, finished TEXT, returncode INTEGER,
  marks TEXT,     -- JSON: each job's scoring fields when the run started, to find what it changed
  results TEXT    -- JSON: the jobs it scored or tailored, once worked out
);
CREATE TABLE IF NOT EXISTS run_lines (
  run_id INTEGER NOT NULL, n INTEGER NOT NULL, line TEXT NOT NULL,
  at REAL,        -- when it was printed (Unix time), to learn how long each step of a run takes
  PRIMARY KEY (run_id, n)
);
"""
# Added later, so an older database gains them: the supervising process of a run that's going (pid, and the
# nonce on its command line that says it's ours), the run's key, and whether it's waiting its turn.
COLUMNS = {"pid": "INTEGER", "nonce": "TEXT", "key": "TEXT", "queued": "INTEGER NOT NULL DEFAULT 0"}

MAX_RUNS = 3   # each tailoring run makes ~11 Claude calls; more at once mostly queues on usage limits
FOLLOW_EVERY = 0.2   # seconds between looks at a run's new lines in the database

# The only runs that wait their turn, and so the only ones the app starts from the database after a restart.
# A saved waiting run must be exactly this, so an edited database can't make the app run some other command.
JOB_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,250}")


def restorable(args: list) -> bool:
    return (isinstance(args, list) and len(args) == 2 and args[0] == "tailor"
            and isinstance(args[1], str) and bool(JOB_ID.fullmatch(args[1])))


class Busy(RuntimeError):
    pass


def is_ours(pid: int, nonce: str) -> bool:
    """The process `pid` is the supervisor with this nonce. macOS reuses pids, so a pid alone never
    identifies a run: one that ended while the app was closed may have handed its pid to anything."""
    try:
        cmd = subprocess.run(["ps", "-ww", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return f"jobpipe.web.supervise {nonce} " in cmd


class Supervised:
    """A run's supervisor process (supervise.py): the leader of its own process group, holding the run's
    command. `popen` is set while this app started it (it reaps it); after a restart only pid and nonce are known."""

    def __init__(self, pid: int, nonce: str, popen: Optional[subprocess.Popen] = None):
        self.pid, self.nonce, self.popen = pid, nonce, popen

    def alive(self, check: bool = False) -> bool:
        """Still running. check: also confirm it's still our process (a `ps` call), not a reused pid."""
        if self.popen is not None:
            return self.popen.poll() is None
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:      # another user's process has the pid now
            return False
        return not check or is_ours(self.pid, self.nonce)

    def signal(self, sig: int) -> None:
        # Our own child can't have handed its pid on before we reap it; any other process is checked first.
        if self.alive() and (self.popen is not None or is_ours(self.pid, self.nonce)):
            try:
                os.killpg(self.pid, sig)
            except (ProcessLookupError, PermissionError):
                pass

    def terminate(self) -> None:
        self.signal(signal.SIGTERM)

    def kill(self) -> None:
        self.signal(signal.SIGKILL)

    def wait(self, timeout: Optional[float] = None) -> None:
        end = time.monotonic() + (timeout if timeout is not None else 1e9)
        while self.alive():
            if time.monotonic() > end:
                raise subprocess.TimeoutExpired(f"run supervisor {self.pid}", timeout or 0)
            time.sleep(0.02)


@dataclass
class Run:
    id: int
    label: str
    args: list[str]
    started: str
    lines: list[str] = field(default_factory=list)
    line_at: list[Optional[float]] = field(default_factory=list)   # when each line was printed (None before lines had times)
    returncode: Optional[int] = None
    finished: Optional[str] = None
    proc: Any = None            # the running process: a Supervised, or a Popen without a database
    marks: Optional[Any] = None
    results: Optional[list[dict]] = None
    key: Optional[str] = None   # runs with the same key don't overlap
    queued: bool = False        # waiting for a free slot; started by the manager when one opens
    stopping: bool = False      # Stop was asked for, so if it dies without saying how it ended, it was stopped

    @property
    def status(self) -> str:
        if self.queued:
            return "queued"
        if self.returncode is None:
            return "running" if self.proc else "interrupted"   # no process: it died without saying how it ended
        return "done" if self.returncode == 0 else ("stopped" if self.returncode < 0 else "failed")

    def public(self, since: int = 0) -> dict:
        return {"id": self.id, "label": self.label, "args": self.args, "started": self.started,
                "finished": self.finished, "status": self.status, "returncode": self.returncode,
                "lines": self.lines[since:], "line_count": len(self.lines)}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunManager:
    def __init__(self, argv_prefix: Optional[list[str]] = None, cwd: Optional[Path] = None,
                 db: Optional[Path] = None, max_runs: int = MAX_RUNS):
        self.argv_prefix = argv_prefix or [sys.executable, "-m", "jobpipe"]
        self.max_runs = max_runs
        self.cwd = cwd or config.ROOT
        self.db = db
        self.runs: dict[int, Run] = {}
        self.waiting: list[Run] = []     # queued runs, first in first out
        self.lock = threading.Lock()
        self.closed = False
        self._next = 1
        # What a waiting run saved before a restart marks when it starts (the app sets it: see resume()).
        self.marks_factory: Optional[Callable[[], Any]] = None
        if db is not None:
            db.parent.mkdir(parents=True, exist_ok=True)
            with closing(self._db()) as con, con:
                con.executescript(SCHEMA)
                have = {c["name"] for c in con.execute("PRAGMA table_info(runs)")}
                for name, kind in COLUMNS.items():
                    if name not in have:
                        con.execute(f"ALTER TABLE runs ADD COLUMN {name} {kind}")
                if "at" not in {c["name"] for c in con.execute("PRAGMA table_info(run_lines)")}:
                    con.execute("ALTER TABLE run_lines ADD COLUMN at REAL")   # a database from before lines had times
            self._load()

    def _db(self) -> sqlite3.Connection:
        assert self.db is not None
        con = sqlite3.connect(self.db, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def _load(self) -> None:
        """The saved runs. Runs still going (their supervisor is alive and ours) are followed again, and runs
        that were waiting their turn wait again; resume() starts them."""
        with closing(self._db()) as con:
            rows = list(con.execute("SELECT * FROM runs ORDER BY id"))
            for r in rows:
                self.runs[r["id"]] = Run(r["id"], r["label"], json.loads(r["args"]), r["started"],
                                         returncode=r["returncode"], finished=r["finished"],
                                         marks=json.loads(r["marks"]) if r["marks"] else None,
                                         results=json.loads(r["results"]) if r["results"] else None, key=r["key"])
            for r in con.execute("SELECT run_id, line, at FROM run_lines ORDER BY run_id, n"):
                if r["run_id"] in self.runs:
                    self.runs[r["run_id"]].lines.append(r["line"])
                    self.runs[r["run_id"]].line_at.append(r["at"])
        self._next = max(self.runs, default=0) + 1
        for r in rows:
            run = self.runs[r["id"]]
            if r["queued"]:
                if restorable(run.args):
                    run.queued, run.marks = True, None
                    self.waiting.append(run)
                else:
                    run.lines.append("Not started: a waiting run must be Make résumé for one job.")
                    self._finish(run, 1)
            elif run.returncode is None and r["pid"] and r["nonce"]:
                proc = Supervised(r["pid"], r["nonce"])
                if proc.alive(check=True):
                    run.proc = proc
                    threading.Thread(target=self._follow, args=(run,), daemon=True).start()

    def resume(self, marks_factory: Optional[Callable[[], Any]] = None) -> None:
        """Start the runs that were waiting their turn when the app closed, as slots allow."""
        self.marks_factory = marks_factory
        self._start_waiting()

    def close(self) -> None:
        """Stop following and starting runs, as when the app stops: the runs carry on, and the next app picks
        them up. The processes this one started are still reaped when they end, so none is left a zombie
        (which looks alive) while this process lives on."""
        self.closed = True
        for run in self.runs.values():
            if isinstance(run.proc, Supervised) and run.proc.popen is not None:
                threading.Thread(target=run.proc.popen.wait, daemon=True).start()

    def running(self) -> list[Run]:
        return [r for r in self.runs.values() if r.status == "running"]

    def active(self) -> Optional[Run]:
        return next(iter(self.running()), None)

    def start(self, label: str, args: list[str], marks: Any = None,
              key: Optional[str] = None, busy: str = "", queue: bool = False) -> Run:
        """marks: anything the caller needs later to see what the run changed; saved with it. A callable is
        called when the run starts, so a queued run marks the state it starts from.
        key: a run with the same key going or waiting makes this one Busy, with the message `busy`.
        queue: when MAX_RUNS are going, wait for a free slot (status "queued") instead of raising Busy."""
        with self.lock:
            going = self.running()
            if key and any(r.key == key for r in (*going, *self.waiting)):
                raise Busy(busy or "That run is already going.")
            if len(going) >= self.max_runs and not queue:
                raise Busy(f"{len(going)} runs are going, the most at once. Start this when one finishes.")
            run = Run(self._next, label, args, now(), marks=marks, key=key)
            self._next += 1
            wait = len(going) >= self.max_runs or bool(self.waiting)     # behind the runs already waiting, too
            if self.db is not None:      # saved now, so a run still waiting when the app restarts waits on
                with closing(self._db()) as con, con:
                    con.execute("INSERT INTO runs(id, label, args, started, key, queued) VALUES(?, ?, ?, ?, ?, ?)",
                                (run.id, run.label, json.dumps(run.args), run.started, key, int(wait)))
            if wait:
                run.queued = True
                self.waiting.append(run)
            else:
                self._launch(run)
            self.runs[run.id] = run
        return run

    def _launch(self, run: Run) -> None:
        """Start the run's process (with the lock held)."""
        run.started = now()
        if callable(run.marks):
            run.marks = run.marks()
        elif run.marks is None and run.queued and self.marks_factory:   # waited through a restart
            run.marks = self.marks_factory()
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "JOBPIPE_RUN_ID": str(run.id)}
        cmd = [*self.argv_prefix, *run.args]
        if self.db is None:
            # Unbuffered, so the Runs tab shows each line as it's printed rather than all of it at the end.
            run.proc = subprocess.Popen(cmd, cwd=self.cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, bufsize=1, env=env)
            run.queued = False
            threading.Thread(target=self._pump, args=(run,), daemon=True).start()
            return
        with closing(self._db()) as con, con:
            con.execute("UPDATE runs SET started = ?, marks = ?, queued = 0 WHERE id = ?",
                        (run.started, json.dumps(run.marks) if run.marks is not None else None, run.id))
        nonce = secrets.token_hex(8)
        # A session of its own: no terminal, no pipe to this process, so it carries on if the app stops.
        with open(self.db.parent / "runs.log", "a") as log:
            popen = subprocess.Popen([sys.executable, "-m", "jobpipe.web.supervise", nonce, str(self.db), str(run.id),
                                      "--", *cmd], cwd=self.cwd, env=env, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=log, start_new_session=True)
        run.proc = Supervised(popen.pid, nonce, popen)
        run.queued = False         # only now: with no process yet it would read as interrupted
        with closing(self._db()) as con, con:
            con.execute("UPDATE runs SET pid = ?, nonce = ? WHERE id = ?", (popen.pid, nonce, run.id))
        threading.Thread(target=self._follow, args=(run,), daemon=True).start()

    def _start_waiting(self) -> None:
        """Start queued runs while there are free slots."""
        with self.lock:
            while not self.closed and self.waiting and len(self.running()) < self.max_runs:
                run = self.waiting.pop(0)
                try:
                    self._launch(run)
                except Exception as e:  # noqa: BLE001 - a run that can't start fails; the next one still gets its turn
                    run.lines.append(f"Couldn't start: {e}")
                    run.queued, run.proc = False, None
                    self._finish(run, 1)

    def queue_position(self, run: Run) -> Optional[int]:
        """1 for the next queued run to start, None when it isn't waiting."""
        return self.waiting.index(run) + 1 if run in self.waiting else None

    def _finish(self, run: Run, returncode: int) -> None:
        run.finished, run.returncode = now(), returncode
        if self.db is not None:
            with closing(self._db()) as con, con:
                con.execute("UPDATE runs SET returncode = ?, finished = ?, queued = 0 WHERE id = ?",
                            (returncode, run.finished, run.id))

    def _follow(self, run: Run) -> None:
        """Read a supervised run's lines and, once it ends, its status, from the database as they're saved."""
        con = self._db()
        checked, gone = time.monotonic(), False
        try:
            while not self.closed:
                end = con.execute("SELECT returncode, finished FROM runs WHERE id = ?", (run.id,)).fetchone()
                for r in con.execute("SELECT line, at FROM run_lines WHERE run_id = ? AND n >= ? ORDER BY n",
                                     (run.id, len(run.lines))):
                    run.line_at.append(r["at"])
                    run.lines.append(r["line"])
                if end is not None and end["finished"] is not None:
                    run.finished, run.returncode = end["finished"], end["returncode"]
                    if isinstance(run.proc, Supervised):
                        run.proc.alive()       # reaps our own child
                    return
                if gone:                   # it died without saving how it ended (looked again, in case it just had)
                    if run.stopping:       # stopped before it could (still starting up): say so for it
                        self._finish(run, -signal.SIGTERM)
                    run.proc = None
                    return
                check = time.monotonic() - checked > 10
                if check:
                    checked = time.monotonic()
                gone = not (isinstance(run.proc, Supervised) and run.proc.alive(check=check))
                if not gone:
                    time.sleep(FOLLOW_EVERY)
        finally:
            con.close()
            self._start_waiting()      # its slot is free

    def _pump(self, run: Run) -> None:
        """A run without a database: read its output from the pipe."""
        assert run.proc and run.proc.stdout
        for line in run.proc.stdout:
            run.line_at.append(time.time())
            run.lines.append(line.rstrip("\n"))
        returncode = run.proc.wait()
        run.finished, run.returncode = now(), returncode
        self._start_waiting()

    def save_results(self, run: Run, results: list[dict]) -> None:
        run.results = results
        if self.db is not None:
            with closing(self._db()) as con, con:
                con.execute("UPDATE runs SET results = ? WHERE id = ?", (json.dumps(results), run.id))

    def rename_jobs(self, moved: dict[str, str]) -> None:
        """Runs' marks and results name jobs by id: follow jobs that moved to a new one ({old: new}, Store.fix_ids)."""
        for run in self.runs.values():
            marks = run.marks if isinstance(run.marks, dict) and moved.keys() & run.marks.keys() else None
            results = run.results if run.results and any(r.get("id") in moved for r in run.results) else None
            if marks is None and results is None:
                continue
            if marks is not None:
                run.marks = {moved.get(k, k): v for k, v in marks.items()}
            if results is not None:
                run.results = [{**r, "id": moved.get(r.get("id"), r.get("id"))} for r in results]
            if self.db is not None:
                with closing(self._db()) as con, con:
                    con.execute("UPDATE runs SET marks = ?, results = ? WHERE id = ?",
                                (json.dumps(run.marks) if run.marks is not None else None,
                                 json.dumps(run.results) if run.results is not None else None, run.id))

    def stop(self, run_id: int) -> Run:
        run = self.runs[run_id]
        with self.lock:
            if run.queued:             # never started: it leaves the queue
                self.waiting.remove(run)
                run.queued = False
                self._finish(run, -signal.SIGTERM)
                return run
        if run.proc and run.status == "running":
            run.stopping = True
            run.proc.terminate()
        return run

    def going(self) -> dict:
        """How many runs are going and waiting, for anything about to quit the app."""
        going, waiting = len(self.running()), len(self.waiting)
        parts = [f"{going} run{'s are' if going != 1 else ' is'} going"] if going else []
        if waiting:
            parts.append(f"{waiting} {'are' if waiting != 1 else 'is'} waiting their turn" if going
                         else f"{waiting} run{'s are' if waiting != 1 else ' is'} waiting their turn")
        return {"running": going, "waiting": waiting, "text": " and ".join(parts)}

    def stop_all(self, timeout: float = 10) -> int:
        """Quitting the app: drop the waiting runs and stop the ones going, then wait (up to `timeout`) for them
        to save how they ended. Returns how many there were."""
        with self.lock:
            waiting, self.waiting = self.waiting, []
            for run in waiting:
                run.queued = False
                self._finish(run, -signal.SIGTERM)
        going = self.running()
        for run in going:
            run.stopping = True
            run.proc.terminate()
        end = time.monotonic() + timeout
        while time.monotonic() < end and any(r.status == "running" for r in going):
            time.sleep(0.05)
        return len(waiting) + len(going)

    def wait(self, run_id: int, timeout: float = 30) -> Run:
        run = self.runs[run_id]
        if run.proc:
            run.proc.wait(timeout)
        for _ in range(250):  # let the reader drain the pipe, or the follower read the end from the database
            if run.returncode is not None or run.proc is None:
                break
            threading.Event().wait(0.02)
        return run
