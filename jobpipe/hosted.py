"""Hosted mode: OYSTER_HOSTED=1 makes the app safe to run for strangers on the paid website (docs/paid-service.md).

The AI is always the Claude API through the website's metering proxy (OYSTER_AI_PROXY_URL), signed in with this
user's proxy token (OYSTER_AI_PROXY_TOKEN), never a provider key. Claude Code, local models and custom addresses are
refused, the settings that pick the AI or hold keys can't be changed, and each action runs on a model from MODELS.
With the switch off, nothing here changes how the app works.
"""
from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from typing import Iterator
from urllib.parse import urlsplit

from . import config

SWITCH = "OYSTER_HOSTED"
PROXY_URL = "OYSTER_AI_PROXY_URL"
PROXY_TOKEN = "OYSTER_AI_PROXY_TOKEN"
LABEL = "Claude, through Job Oyster"
REFUSED = "The website sets the AI and its keys, so they can't be changed here."

# The models each action may use; the first is used when the settings name another.
MODELS = {
    "triage": ("claude-haiku-5-5",),                          # Signal score
    "analysis": ("claude-opus-5-5", "claude-sonnet-5-5"),     # Full score
    "writer": ("claude-opus-5-5", "claude-sonnet-5-5"),       # Make résumé
    "chat": ("claude-sonnet-5-5", "claude-haiku-5-5"),        # the résumé chat
    "rescore": ("claude-sonnet-5-5", "claude-haiku-5-5"),     # live scores after a chat edit
}
EFFORTS = ("low", "medium", "high")
# Signal score is free on the website within fair use: at most this many a day per user (UTC days).
SIGNAL_CAP = "OYSTER_SIGNAL_SCORE_DAILY_CAP"
SIGNAL_CAP_DEFAULT = 100


class OverCap(RuntimeError):
    """Today's Signal scores are used up."""


def enabled() -> bool:
    return os.environ.get(SWITCH, "").strip().lower() in ("1", "true", "yes", "on")


def proxy_url() -> str:
    url = os.environ.get(PROXY_URL, "").strip().rstrip("/")
    if urlsplit(url).scheme not in ("http", "https") or not urlsplit(url).hostname:
        raise RuntimeError(f"Hosted mode ({SWITCH}) needs {PROXY_URL}: the AI proxy's address, http(s)://…")
    return url


def proxy_token() -> str:
    token = os.environ.get(PROXY_TOKEN, "").strip()
    if not token:
        raise RuntimeError(f"Hosted mode ({SWITCH}) needs {PROXY_TOKEN}: this user's token for the AI proxy.")
    return token


def allowed(stage: str, model: str) -> bool:
    return model in MODELS.get(stage, ())


def lock(cfg):
    """Pin a Config to the proxy and the allowed models (in place, and returned). Raises RuntimeError when the proxy's
    address or token is missing, so a hosted copy that isn't set up stops at start rather than reaching another AI."""
    cfg.backend, cfg.base_url, cfg.api_key_env, cfg.claude_bin = "api", proxy_url(), PROXY_TOKEN, None
    proxy_token()
    for stage, models in MODELS.items():
        m = getattr(cfg, f"{stage}_model")
        if not allowed(stage, m.model) or m.effort not in EFFORTS:
            setattr(cfg, f"{stage}_model", replace(m, model=m.model if allowed(stage, m.model) else models[0],
                                                    effort=m.effort if m.effort in EFFORTS else "medium"))
    return cfg


def signal_cap() -> int:
    try:
        return max(0, int(os.environ.get(SIGNAL_CAP, "").strip() or SIGNAL_CAP_DEFAULT))
    except ValueError:
        return SIGNAL_CAP_DEFAULT


def _count(change: int) -> None:
    """Add change to today's Signal scores in data/signal-scores.json, shared by the server and its runs (each its own
    process, so the file is locked). Raises OverCap, counting nothing, when one more would pass the cap."""
    path = config.DATA / "signal-scores.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    with open(path.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            saved = json.loads(path.read_text())
        except (OSError, ValueError):
            saved = {}
        used = int(saved.get("used") or 0) if saved.get("day") == today else 0
        if change > 0 and used + change > signal_cap():
            raise OverCap(f"Today's {signal_cap()} free Signal scores are used up. More can be scored tomorrow "
                          "(the day starts at midnight UTC).")
        path.write_text(json.dumps({"day": today, "used": max(0, used + change)}))


@contextmanager
def signal_score() -> Iterator[None]:
    """One Signal score, counted against today's cap in hosted mode (nothing happens otherwise). One that fails is
    given back."""
    if not enabled():
        yield
        return
    _count(1)
    try:
        yield
    except BaseException:
        _count(-1)
        raise
