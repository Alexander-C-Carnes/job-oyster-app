"""The honesty-check models on a shared service instead of in this process (docs/paid-service.md item 2.4).

On the website every user's copy has a small memory limit, so the evidence models (evidence.Models: the embedding
model, the reranker and the NLI verifier) run once on the server and each copy calls them over its loopback.
OYSTER_EVIDENCE_URL names the service (http://127.0.0.1:8791); evidence.load() returns RemoteModels when it is set.
The service runs the models it was started with, so searches.yaml's model names don't apply; `evidence: enabled:
false` still turns the check off. Unset (the default), the models load in this process as before.

Only text and scores cross: the passages, queries and claims of one call, and the vectors and probabilities back.
The service keeps nothing; the passage vectors are cached in the user's own folder (evidence.Index).
"""
from __future__ import annotations

import base64
import os
from typing import Any, Optional, Sequence

import httpx

from .evidence import Unavailable

URL = "OYSTER_EVIDENCE_URL"
TIMEOUT = httpx.Timeout(connect=5, read=600, write=60, pool=10)    # a call may wait behind other users' checks
EMBED_BATCH, PAIR_BATCH = 64, 32       # short calls, so the service takes turns between users


def url() -> str:
    """The service's address, or "" when the models run in this process."""
    return os.environ.get(URL, "").strip().rstrip("/")


def decode(body: dict):
    """Vectors as the service sends them: float32 rows, base64."""
    import numpy as np
    rows, dim = body["shape"]
    return np.frombuffer(base64.b64decode(body["data"]), dtype="<f4").reshape(rows, dim)


class RemoteModels:
    """evidence.Models, answered by the honesty-check service (hosted/evidence_service.py)."""

    def __init__(self, base: str, client: Optional[httpx.Client] = None):
        self.base = base
        # its address is the operator's (like the AI proxy's), so it isn't sent through the egress guard
        self._client = client or httpx.Client(timeout=TIMEOUT, trust_env=False)
        info = self._call("GET", "/v1/info")
        self.name, self.names, self.dim = info["name"], list(info["models"]), int(info["dim"])

    def _call(self, method: str, path: str, body: Any = None) -> dict:
        try:
            r = self._client.request(method, self.base + path, json=body)
        except httpx.HTTPError as e:
            raise Unavailable(f"couldn't reach the honesty-check service ({type(e).__name__})") from e
        if r.status_code != 200:
            raise Unavailable(f"the honesty-check service answered {r.status_code}")
        try:
            return r.json()
        except ValueError:
            raise Unavailable("the honesty-check service sent something that isn't JSON") from None

    def embed(self, texts: Sequence[str], instruction: Optional[str] = None):
        import numpy as np
        texts = list(texts)
        parts = [decode(self._call("POST", "/v1/embed", {"texts": texts[i:i + EMBED_BATCH],
                                                          "instruction": instruction}))
                 for i in range(0, len(texts), EMBED_BATCH)]
        return np.vstack(parts) if parts else np.zeros((0, self.dim), dtype=np.float32)

    def _scores(self, path: str, pairs: Sequence[tuple[str, str]], **extra) -> list[float]:
        out: list[float] = []
        pairs = [list(p) for p in pairs]
        for i in range(0, len(pairs), PAIR_BATCH):
            out += [float(s) for s in self._call("POST", path, {**extra, "pairs": pairs[i:i + PAIR_BATCH]})["scores"]]
        return out

    def judge(self, instruction, pairs):
        return self._scores("/v1/judge", pairs, instruction=instruction)

    def entail(self, pairs):
        return self._scores("/v1/entail", pairs)
