"""Stop a hosted copy nobody is using (docs/paid-service.md item 2.1): OYSTER_IDLE_EXIT_MINUTES=N.

The website runs one copy of the app per user, started by systemd's socket unit on the first request. After N minutes
with no request from the user, and nothing going (a run, a score, a chat reply), the copy exits; the socket unit
starts it again on the next request. The page's own polling (every 5 seconds while it's open) doesn't count as use,
or an open tab would keep the copy running forever. Off (0) by default, and on the Mac app.
"""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Callable

SETTING = "OYSTER_IDLE_EXIT_MINUTES"
# What the open page asks for by itself (app.js: the 5-second refresh, a run's log, a chat reply in the background).
POLLS = re.compile(r"/api/(summary|ready|impact-record|interview-prep|runs/[^/]+|work/[^/]+|jobs/[^/]+/chat)")


def minutes() -> float:
    try:
        return max(0.0, float(os.environ.get(SETTING, "").strip() or 0))
    except ValueError:
        return 0.0


class Idle:
    def __init__(self, minutes: float, busy: Callable[[], bool], clock: Callable[[], float] = time.monotonic):
        self.minutes, self.busy, self.clock = minutes, busy, clock
        self.last = clock()
        self.stop: Callable[[], None] = lambda: None     # serve() sets it once the server exists

    def seen(self, method: str, path: str) -> None:
        if not (method == "GET" and POLLS.fullmatch(path)):
            self.last = self.clock()

    def due(self) -> bool:
        return bool(self.minutes) and self.clock() - self.last >= self.minutes * 60 and not self.busy()

    def watch(self) -> None:
        every = min(30.0, self.minutes * 15)          # a quarter of the limit, and at least twice a minute

        def loop() -> None:
            while not self.due():
                threading.Event().wait(every)
            print(f"  Nothing used for {self.minutes:g} minutes: stopping until the next request.", flush=True)
            self.stop()
        if self.minutes:
            threading.Thread(target=loop, daemon=True).start()
