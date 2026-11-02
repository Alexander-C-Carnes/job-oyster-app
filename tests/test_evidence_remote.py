"""The evidence models on a shared service (evidence_remote.py, OYSTER_EVIDENCE_URL): off by default, so the models
load in this process as before; in hosted mode they never do; set, the client batches its calls to the service and
copes with it being down. The service is a fake here (httpx.MockTransport)."""
import base64
import json

import httpx
import numpy as np
import pytest

from jobpipe import evidence, evidence_remote, hosted
from jobpipe.config import EvidenceCfg
from jobpipe.evidence import load as real_load       # imported before conftest's fixture swaps it out


class Local:
    """Stands in for LocalModels: records that the models were loaded in this process."""
    made: list = []

    def __init__(self, cfg):
        Local.made.append(cfg)
        self.name = "local"


@pytest.fixture
def local(monkeypatch):
    Local.made = []
    monkeypatch.setattr(evidence, "LocalModels", Local)
    monkeypatch.setattr(evidence, "_loaded", {})
    monkeypatch.delenv(evidence_remote.URL, raising=False)
    monkeypatch.delenv(hosted.SWITCH, raising=False)

    def refuse(self, request):
        raise AssertionError(f"reached the network: {request.url}")
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    return Local


def test_off_by_default_the_models_load_here_as_before(local):
    assert evidence_remote.url() == ""
    m = real_load(EvidenceCfg())
    assert isinstance(m, Local) and len(local.made) == 1
    assert real_load(EvidenceCfg()) is m                  # once per process, as before
    with pytest.raises(evidence.Unavailable, match="switched off"):
        real_load(EvidenceCfg(enabled=False))


def test_hosted_without_the_service_never_loads_them_here(local, monkeypatch):
    monkeypatch.setenv(hosted.SWITCH, "1")
    with pytest.raises(evidence.Unavailable, match=evidence_remote.URL):
        real_load(EvidenceCfg())
    assert local.made == []


def test_with_the_setting_the_service_answers(local, monkeypatch):
    made = []
    monkeypatch.setattr(evidence_remote, "RemoteModels", lambda url: made.append(url) or "remote")
    monkeypatch.setenv(evidence_remote.URL, "http://127.0.0.1:8791/")
    monkeypatch.setenv(hosted.SWITCH, "1")
    assert real_load(EvidenceCfg()) == "remote" and made == ["http://127.0.0.1:8791"]
    assert local.made == []
    with pytest.raises(evidence.Unavailable, match="switched off"):    # searches.yaml still turns it off
        real_load(EvidenceCfg(enabled=False))


class Service:
    """A fake honesty-check service: vectors are [len(text), 1, 0], scores are len(a) / 100."""

    def __init__(self, status=200):
        self.status, self.calls = status, []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.calls.append((path, body))
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "nope"})
        if path == "/v1/info":
            return httpx.Response(200, json={"name": "fake", "models": ["e", "r", "v"], "dim": 3})
        if path == "/v1/embed":
            v = np.array([[len(t), 1, 0] for t in body["texts"]], dtype="<f4")
            return httpx.Response(200, json={"shape": list(v.shape), "data": base64.b64encode(v.tobytes()).decode()})
        return httpx.Response(200, json={"scores": [len(a) / 100 for a, _ in body["pairs"]]})

    def models(self):
        return evidence_remote.RemoteModels("http://svc.test", httpx.Client(transport=httpx.MockTransport(self.handle)))


def test_the_client_batches_and_reassembles():
    svc = Service()
    m = svc.models()
    assert (m.name, m.names, m.dim) == ("fake", ["e", "r", "v"], 3)
    texts = ["x" * n for n in range(150)]
    v = m.embed(texts, "find it")
    assert v.shape == (150, 3) and list(v[:, 0]) == list(range(150))
    embeds = [b for p, b in svc.calls if p == "/v1/embed"]
    assert [len(b["texts"]) for b in embeds] == [64, 64, 22] and {b["instruction"] for b in embeds} == {"find it"}
    pairs = [("a" * n, "claim") for n in range(70)]
    assert m.entail(pairs) == [n / 100 for n in range(70)]
    assert m.judge("judge it", pairs[:5]) == [n / 100 for n in range(5)]
    sent = [(p, len(b["pairs"]), b.get("instruction")) for p, b in svc.calls if "pairs" in b]
    assert sent == [("/v1/entail", 32, None), ("/v1/entail", 32, None), ("/v1/entail", 6, None),
                    ("/v1/judge", 5, "judge it")]
    assert m.embed([]).shape == (0, 3) and m.entail([]) == []


def test_an_index_built_through_the_service_works(tmp_path):
    ps = [evidence.Passage("Record", "Built the weekly portfolio digest"), evidence.Passage("Record", "Ran it")]
    index = evidence.Index(Service().models(), ps, tmp_path)
    assert index.vectors.shape == (2, 3) and len(list(tmp_path.glob("index-*.npy"))) == 1
    assert index.nearest(["digest"], "find", 1) == [[0]]


def test_a_service_that_is_down_or_failing_is_unavailable():
    with pytest.raises(evidence.Unavailable, match="answered 503"):
        Service(503).models()

    def down(request):
        raise httpx.ConnectError("refused")
    with pytest.raises(evidence.Unavailable, match="couldn't reach"):
        evidence_remote.RemoteModels("http://svc.test", httpx.Client(transport=httpx.MockTransport(down)))
