"""OpenAICompatRunner (OpenAI, Gemini, Kimi, OpenRouter, Ollama, LM Studio...) against a mocked chat API (no network)."""
import asyncio
import json

import httpx
import pytest

from jobpipe import config
from jobpipe.config import ModelCfg
from jobpipe.llm import AgentCall, AgentError, OpenAICompatRunner
from jobpipe.pipeline import make_runner

OK = '<file name="ratings.json">\n{"a": 1}\n</file>\n<file name="04-match.md">\n# M\n</file>\nDone.'


def reply(text=OK, finish="stop", prompt_tokens=None, cached=0, refusal=None, status=200, error=None):
    def make(body):
        if status != 200:
            return httpx.Response(status, json={"error": {"message": error or "bad"}})
        sent = sum(len(m["content"]) for m in body["messages"]) // 4
        return httpx.Response(200, json={
            "model": body["model"], "choices": [{"finish_reason": finish,
                                                 "message": {"role": "assistant", "content": text, "refusal": refusal}}],
            "usage": {"prompt_tokens": sent if prompt_tokens is None else prompt_tokens, "completion_tokens": 20,
                      "prompt_tokens_details": {"cached_tokens": cached}}})
    return make


def runner_with(replies, provider="openai", models=("gpt-b", "gpt-a"), **kw):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        seen.append((request, body))
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": m} for m in models]})
        return replies[min(len(seen) - 1, len(replies) - 1)](body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatRunner(provider=provider, client=client, retry_wait=0, **kw), seen


def call(**kw):
    return AgentCall(**{**dict(label="04-matcher", model=ModelCfg("gpt-x", "high"), instructions="Brief.",
                               documents={"jd.md": "posting"}, expect=["ratings.json", "04-match.md"]), **kw})


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("MOONSHOT_API_KEY", "m-key")


def test_request_shape_and_parsing():
    runner, seen = runner_with([reply(cached=30)])
    res = asyncio.run(runner.run(call()))
    assert res.files == {"ratings.json": '{"a": 1}\n', "04-match.md": "# M\n"}
    req, body = seen[0]
    assert str(req.url) == "https://api.openai.com/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer sk-openai"
    assert body["model"] == "gpt-x" and body["reasoning_effort"] == "high"
    system, user = body["messages"]
    assert system["role"] == "system" and "Jordan Rivera" in system["content"]       # the candidate materials
    assert user["role"] == "user" and "posting" in user["content"] and "Expected output files" in user["content"]
    u = runner.usage_log[0]
    assert u["cache_read"] == 30 and u["output"] == 20 and u["label"] == "04-matcher"


def test_kimi_address_key_and_effort():
    runner, seen = runner_with([reply()], provider="kimi")
    asyncio.run(runner.run(call(model=ModelCfg("kimi-k3", "medium"), candidate=False)))
    req, body = seen[0]
    assert str(req.url) == "https://api.moonshot.ai/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer m-key"
    assert body["reasoning_effort"] == "high"                  # Kimi has no medium: low, high or max


def test_effort_is_dropped_when_the_model_rejects_it():
    bad = lambda body: (httpx.Response(400, json={"error": {"message": "Unsupported parameter: 'reasoning_effort'"}})
                        if "reasoning_effort" in body else reply()(body))
    runner, seen = runner_with([bad])
    asyncio.run(runner.run(call()))
    asyncio.run(runner.run(call()))
    assert ["reasoning_effort" in b for _, b in seen] == [True, False, False]


def test_providers_addresses_and_keys(monkeypatch):
    runner, seen = runner_with([reply()], provider="gemini")
    asyncio.run(runner.run(call(candidate=False)))
    assert str(seen[0][0].url).startswith("https://generativelanguage.googleapis.com/v1beta/openai/")
    assert seen[0][0].headers["authorization"] == "Bearer g-key"
    runner, seen = runner_with([reply()], provider="ollama")                     # local: no key needed
    asyncio.run(runner.run(call(candidate=False)))
    assert str(seen[0][0].url) == "http://localhost:11434/v1/chat/completions" and "authorization" not in seen[0][0].headers
    runner, seen = runner_with([reply()], provider="openai-compatible", base_url="http://box:8000/v1/")
    asyncio.run(runner.run(call(candidate=False)))
    assert str(seen[0][0].url) == "http://box:8000/v1/chat/completions"
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY is not set"):
        OpenAICompatRunner(provider="openai")
    with pytest.raises(RuntimeError, match="base_url"):
        OpenAICompatRunner(provider="openai-compatible")


def test_errors_are_explained():
    for kw, match in ((dict(status=401, error="Incorrect API key"), "rejected the key"),
                      (dict(status=404, error="model not found"), "doesn't know that model"),
                      (dict(refusal="I can't help"), "declined"),
                      (dict(finish="length"), "length limit"),
                      (dict(prompt_tokens=50), "context window is too small")):
        runner, _ = runner_with([reply(**kw)], provider="ollama")
        with pytest.raises(AgentError, match=match):
            asyncio.run(runner.run(call()))


def test_rate_limits_are_retried_and_bad_output_is_asked_for_again():
    runner, seen = runner_with([lambda b: httpx.Response(429, json={}), reply(text="no files"), reply()])
    res = asyncio.run(runner.run(call()))
    assert len(seen) == 3 and "ratings.json" in res.files
    assert "previous attempt had these problems" in seen[2][1]["messages"][1]["content"]


def test_unreachable_local_server():
    def down(request):
        raise httpx.ConnectError("refused")
    runner = OpenAICompatRunner(provider="ollama", retry_wait=0,
                                client=httpx.AsyncClient(transport=httpx.MockTransport(down)))
    with pytest.raises(AgentError, match="Is Ollama running"):
        asyncio.run(runner.run(call()))


def test_model_list_and_make_runner(tmp_path):
    runner, _ = runner_with([reply()])
    assert asyncio.run(runner.models()) == ["gpt-a", "gpt-b"]
    listed = ["gpt-c", "text-embedding-3-small", "tts-1", "whisper-1", "dall-e-3", "gpt-image-1", "gemini-x-embedding"]
    runner, _ = runner_with([reply()], models=listed)
    assert asyncio.run(runner.models()) == ["gpt-c"]           # only models that write text
    p = tmp_path / "searches.yaml"
    p.write_text("models:\n  backend: gemini\n  writer: {model: gemini-x, effort: high}\n")
    cfg = config.load(p)
    r = make_runner(cfg)
    assert isinstance(r, OpenAICompatRunner) and r.provider == "gemini" and r.label.startswith("Gemini")
    cfg.backend = "nope"
    with pytest.raises(ValueError, match="openrouter"):
        make_runner(cfg)
