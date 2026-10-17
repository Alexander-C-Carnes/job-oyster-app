"""Background scoring for the web app: a batch of jobs is queued, scored a few at a time,
and the page polls /api/summary for progress, so a reload never loses a batch.

Two kinds of score:
  signal  one Claude call (the triage rubric): a quick read on fit. Three at a time.
  full    stages 1-2 of the tailoring pipeline: every requirement rated against the impact
          record and each résumé. About four calls a job, so one job at a time.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from fastapi import HTTPException

PARALLEL = {"signal": 3, "full": 1, "upload": 1}


class Scorer:
    def __init__(self, fns: dict[str, Callable[[str], Awaitable[dict]]]):
        self.fns = fns
        self.sems = {kind: asyncio.Semaphore(n) for kind, n in PARALLEL.items()}
        self.active: dict[str, dict] = {}    # job id -> {"kind", "state": queued | running, "task"}
        self.errors: dict[str, str] = {}     # job id -> why its last score failed
        self.finished = 0                    # bumps whenever a job leaves the queue, so the page reloads

    def submit(self, ids: list[str], kind: str) -> None:
        """Queue jobs (call from the event loop). A job already queued or running is left alone."""
        for jid in ids:
            if jid in self.active:
                continue
            self.errors.pop(jid, None)
            self.active[jid] = {"kind": kind, "state": "queued"}
            self.active[jid]["task"] = asyncio.create_task(self._run(jid, kind))

    async def _run(self, jid: str, kind: str) -> None:
        try:
            async with self.sems[kind]:
                self.active[jid]["state"] = "running"
                await self.fns[kind](jid)
        except asyncio.CancelledError:
            pass
        except HTTPException as e:
            self.errors[jid] = str(e.detail)
        except Exception as e:  # noqa: BLE001 - shown next to the job in the UI
            self.errors[jid] = str(e)
        finally:
            if self.active.get(jid, {}).get("task") is asyncio.current_task():
                del self.active[jid]
                self.finished += 1

    def cancel_queued(self) -> None:
        """Drop the jobs that haven't started. Running ones finish (their Claude call is already paid for)."""
        for jid, entry in list(self.active.items()):
            if entry["state"] == "queued":
                entry["task"].cancel()   # a task cancelled before its first step never reaches the finally above
                del self.active[jid]
                self.finished += 1

    def public(self) -> dict:
        return {"active": {jid: {"kind": e["kind"], "state": e["state"]} for jid, e in self.active.items()},
                "errors": dict(self.errors), "finished": self.finished}
