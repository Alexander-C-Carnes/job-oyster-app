"""Hosted mode: OYSTER_HOSTED=1 makes the app safe to run for strangers on the paid website (docs/paid-service.md).

The AI is always the Claude API through the website's metering proxy (OYSTER_AI_PROXY_URL), signed in with this
user's proxy token (OYSTER_AI_PROXY_TOKEN), never a provider key. Claude Code, local models and custom addresses are
refused, the settings that pick the AI or hold keys can't be changed, and each action runs on a model from MODELS.
With the switch off, nothing here changes how the app works.
"""
from __future__ import annotations

import os
from dataclasses import replace
from urllib.parse import urlsplit

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
