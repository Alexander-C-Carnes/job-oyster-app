"""Hosted mode: OYSTER_HOSTED=1 makes the app safe to run for strangers on the paid website (docs/paid-service.md).

The AI is always the Claude API through the website's metering proxy (OYSTER_AI_PROXY_URL), signed in with this
user's proxy token (OYSTER_AI_PROXY_TOKEN), never a provider key. Claude Code, local models and custom addresses are
refused, the settings that pick the AI or hold keys can't be changed, and each action runs on a model from MODELS.

On the Own AI plan (OYSTER_AI_PLAN=own) the user picks any provider with an API key (OWN_BACKENDS) and any of its
models. Calls still go through the proxy, which holds their key encrypted and forwards to the provider: Claude at the
proxy's root, the OpenAI-compatible providers at <proxy>/own/<backend>. With the switch off, nothing here changes how
the app works.
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
PLAN = "OYSTER_AI_PLAN"
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
# Own AI: the providers a user may bring, each with an API key. No Claude Code (a subscription sign-in) and nothing
# that runs on the user's own computer (Ollama, LM Studio), which the server can't reach.
OWN_BACKENDS = ("api", "openai", "gemini", "kimi", "openrouter", "openai-compatible")
OWN_KEY_HEADER = "X-Oyster-Own-Key"     # a key typed in the settings form, for the proxy to check it once (not kept)
# Signal score is free on the website within fair use: at most this many a day per user (UTC days).
SIGNAL_CAP = "OYSTER_SIGNAL_SCORE_DAILY_CAP"
SIGNAL_CAP_DEFAULT = 100


class OverCap(RuntimeError):
    """Today's Signal scores are used up."""


def enabled() -> bool:
    return os.environ.get(SWITCH, "").strip().lower() in ("1", "true", "yes", "on")


def own_ai() -> bool:
    """Hosted, on the Own AI plan: the user brings their own AI."""
    return enabled() and os.environ.get(PLAN, "").strip().lower() == "own"


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


def backend_url(backend: str) -> str:
    """Where a backend's calls go: the proxy's root for Claude (the SDK adds /v1/messages), else <proxy>/own/<backend>,
    an OpenAI-compatible root the proxy forwards to the user's provider."""
    return proxy_url() if backend == "api" else f"{proxy_url()}/own/{backend}"


def allowed(stage: str, model: str) -> bool:
    return own_ai() or model in MODELS.get(stage, ())


def lock(cfg):
    """Pin a Config to the proxy and the allowed models (in place, and returned). Raises RuntimeError when the proxy's
    address or token is missing, so a hosted copy that isn't set up stops at start rather than reaching another AI.
    On Own AI the backend is kept when it's one of OWN_BACKENDS, and the models are the user's."""
    backend = cfg.backend if own_ai() and cfg.backend in OWN_BACKENDS else "api"
    cfg.backend, cfg.base_url, cfg.api_key_env, cfg.claude_bin = backend, backend_url(backend), PROXY_TOKEN, None
    proxy_token()
    if own_ai():
        return cfg
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
