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


def flat(content):
    """A message's text, whether sent as a string or as content parts."""
    return content if isinstance(content, str) else "".join(p["text"] for p in content)


def reply(text=OK, finish="stop", prompt_tokens=None, cached=0, written=0, cost=None, refusal=None, status=200,
          error=None, metadata=None):
    def make(body):
        if status != 200:
            return httpx.Response(status, json={"error": {"code": status, "message": error or "bad",
                                                          **({"metadata": metadata} if metadata else {})}})
        sent = sum(len(flat(m["content"])) for m in body["messages"]) // 4
        usage = {"prompt_tokens": sent if prompt_tokens is None else prompt_tokens, "completion_tokens": 20,
                 "prompt_tokens_details": {"cached_tokens": cached, "cache_write_tokens": written}}
        return httpx.Response(200, json={
            "model": body["model"], "choices": [{"finish_reason": finish,
                                                 "message": {"role": "assistant", "content": text, "refusal": refusal}}],
            "usage": usage if cost is None else {**usage, "cost": cost}})
    return make


def runner_with(replies, provider="openai", models=("gpt-b", "gpt-a"), key_status=200, **kw):
    """A runner on a mocked API: replies[i] answers the i-th chat request (the last one repeats), `models` is the
    model list (ids, or OpenRouter-style dicts), and GET /key answers key_status."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        seen.append((request, body))
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [m if isinstance(m, dict) else {"id": m} for m in models]})
        if request.url.path.endswith("/key"):
            return httpx.Response(key_status, json={"data": {"label": "test"}} if key_status == 200
                                  else {"error": {"code": key_status, "message": "No auth credentials found"}})
        return replies[min(len(chats(seen)) - 1, len(replies) - 1)](body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatRunner(provider=provider, client=client, retry_wait=0, **kw), seen


def chats(seen):
    """The chat requests' bodies, leaving out GET /models and /key."""
    return [body for req, body in seen if req.url.path.endswith("/chat/completions")]


def call(**kw):
    return AgentCall(**{**dict(label="04-matcher", model=ModelCfg("gpt-x", "high"), instructions="Brief.",
                               documents={"jd.md": "posting"}, expect=["ratings.json", "04-match.md"]), **kw})


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
    monkeypatch.setenv("GEMINI_API_KEY", "g-key")
    monkeypatch.setenv("MOONSHOT_API_KEY", "m-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")


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
    bad = lambda body: (httpx.Response(400, json={"error": {"message": "reasoning is not supported by this model"}})
                        if "reasoning" in body else reply()(body))
    runner, seen = runner_with([bad], provider="openrouter")
    asyncio.run(runner.run(call(model=ModelCfg("vendor/plain-model", "high"))))
    assert ["reasoning" in b for b in chats(seen)] == [True, False]


def test_effort_is_dropped_only_for_the_model_that_rejects_it():
    """The web app keeps one runner for every run and step: one model turning effort down mustn't turn it off for
    the others. A 422 (a FastAPI server's validation error) counts like a 400."""
    bad = lambda body: (httpx.Response(422, json={"detail": "reasoning_effort: extra inputs are not permitted"})
                        if body["model"] == "plain" and "reasoning_effort" in body else reply()(body))
    runner, seen = runner_with([bad], provider="openai-compatible", base_url="http://box:8000/v1")
    for m in ("plain", "thinker", "plain", "thinker"):
        asyncio.run(runner.run(call(model=ModelCfg(m, "high"), candidate=False)))
    assert [(b["model"], "reasoning_effort" in b) for b in chats(seen)] == [
        ("plain", True), ("plain", False), ("thinker", True), ("plain", False), ("thinker", True)]
    assert all(b["max_tokens"] == 64000 for b in chats(seen))             # only what was turned down is left out


def test_an_output_limit_goes_in_each_providers_own_field():
    for provider, field in (("openai", "max_completion_tokens"), ("gemini", "max_tokens"), ("kimi", "max_tokens"),
                            ("ollama", "max_tokens")):
        runner, seen = runner_with([reply()], provider=provider)
        asyncio.run(runner.run(call(candidate=False)))
        body = chats(seen)[0]
        assert body[field] == 64000 and ({"max_tokens", "max_completion_tokens"} - {field}).isdisjoint(body), provider
    # Fireworks stops at 2,048 output tokens unless asked for more: a résumé needs more
    short = lambda body: reply()(body) if body.get("max_tokens", 2048) > 2048 else reply(finish="length")(body)
    runner, seen = runner_with([short], provider="openai-compatible", base_url="https://api.fireworks.ai/inference/v1")
    assert asyncio.run(runner.run(call(candidate=False))).files["04-match.md"] == "# M\n"


def test_a_model_that_rejects_the_output_limit_gets_the_request_without_it():
    too_big = lambda body: (httpx.Response(400, json={"error": {"message": "max_tokens is too large: 64000. This model "
                                                                            "supports at most 16384 completion tokens"}})
                            if body["model"] == "small" and "max_tokens" in body else reply()(body))
    runner, seen = runner_with([too_big], provider="openai-compatible", base_url="http://box:8000/v1")
    for m in ("small", "big", "small"):
        asyncio.run(runner.run(call(model=ModelCfg(m, "high"), candidate=False)))
    assert [(b["model"], "max_tokens" in b, "reasoning_effort" in b) for b in chats(seen)] == [
        ("small", True, True), ("small", False, True), ("big", True, True), ("small", False, True)]
    # a prompt plus 64,000 more tokens can be over a small model's context window: asked again without the limit
    window = lambda body: (httpx.Response(400, json={"error": {"message": "This model's maximum context length is 32768 "
                                                                           "tokens. However, you requested 98000 tokens"}})
                           if "max_tokens" in body else reply()(body))
    runner, seen = runner_with([window], provider="lmstudio")
    asyncio.run(runner.run(call(candidate=False)))
    assert ["max_tokens" in b for b in chats(seen)] == [True, False] and "reasoning_effort" in chats(seen)[1]
    # a server that names both settings as unknown gets the request again without either, in one more try
    strict = lambda body: (httpx.Response(400, json={"error": {"message": "Unrecognized request arguments supplied: "
                                                                           "max_completion_tokens, reasoning_effort"}})
                           if {"max_completion_tokens", "reasoning_effort"} & body.keys() else reply()(body))
    runner, seen = runner_with([strict])
    asyncio.run(runner.run(call(candidate=False)))
    assert [sorted({"max_completion_tokens", "reasoning_effort"} & b.keys()) for b in chats(seen)] == [
        ["max_completion_tokens", "reasoning_effort"], []]
    # any other 400 is an error, not a reason to drop settings
    runner, seen = runner_with([reply(status=400, error="messages: field required")])
    with pytest.raises(AgentError, match="error 400"):
        asyncio.run(runner.run(call(candidate=False)))
    assert len(chats(seen)) == 1


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


OPUS = ModelCfg("anthropic/claude-opus-5.5", "high")


def test_openrouter_caches_claude_like_the_claude_api():
    runner, seen = runner_with([reply(prompt_tokens=50_000, cached=40_000, written=9_000, cost=0.0123)],
                               provider="openrouter")
    asyncio.run(runner.run(call(model=OPUS, tail="Your lens: A")))
    req = next(r for r, _ in seen if r.url.path.endswith("/chat/completions"))
    assert str(req.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer or-key"
    system, user = chats(seen)[0]["messages"]
    assert system["role"] == "system" and "cache_control" not in system["content"][0]          # the preamble
    assert system["content"][1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}         # candidate materials
    assert "[Impact record]" in system["content"][1]["text"]
    assert user["content"][0]["cache_control"] == {"type": "ephemeral"}     # the writers share the job documents
    assert "posting" in user["content"][0]["text"] and "Your lens: A" in user["content"][1]["text"]
    u = runner.usage_log[0]
    assert (u["input"], u["cache_read"], u["cache_write"], u["cost"]) == (1_000, 40_000, 9_000, 0.0123)

    asyncio.run(runner.run(call(model=OPUS)))                                   # no lens: no second breakpoint
    assert not any("cache_control" in b for b in chats(seen)[1]["messages"][1]["content"])
    asyncio.run(runner.run(call(model=OPUS, candidate=False)))                  # job-only: just the preamble
    system = chats(seen)[2]["messages"][0]["content"]
    assert len(system) == 1 and "cache_control" not in system[0] and "[Impact record]" not in system[0]["text"]


def test_openrouter_reasoning_and_max_tokens():
    models = [{"id": "anthropic/claude-opus-5.5", "top_provider": {"max_completion_tokens": 128000}},
              {"id": "deepseek/deepseek-chat", "top_provider": {"max_completion_tokens": 16384}}]
    runner, seen = runner_with([reply()], provider="openrouter", models=models)
    asyncio.run(runner.run(call(model=OPUS)))
    asyncio.run(runner.run(call(model=ModelCfg("deepseek/deepseek-chat", "low"))))
    opus, deepseek = chats(seen)
    assert opus["reasoning"] == {"effort": "high"} and "reasoning_effort" not in opus
    assert opus["max_tokens"] == 64000                       # Claude's thinking budget is worked out from it
    assert deepseek["max_tokens"] == 16384 and deepseek["reasoning"] == {"effort": "low"}   # the model's own limit
    assert sum(r.url.path.endswith("/models") for r, _ in seen) == 1          # the list is read once


def test_openrouter_gemini_gets_five_minute_breakpoints_and_others_none():
    runner, seen = runner_with([reply()], provider="openrouter")
    asyncio.run(runner.run(call(model=ModelCfg("google/gemini-3.5-flash", "medium"))))
    asyncio.run(runner.run(call(model=ModelCfg("openai/gpt-6.1-sol", "medium"))))
    gemini, gpt = chats(seen)
    assert gemini["messages"][0]["content"][1]["cache_control"] == {"type": "ephemeral"}   # Gemini's TTL is fixed
    assert isinstance(gpt["messages"][0]["content"], str)                       # OpenAI caches on its own
    runner, seen = runner_with([reply()], provider="openai")
    asyncio.run(runner.run(call()))
    assert isinstance(chats(seen)[0]["messages"][0]["content"], str)


def test_openrouter_out_of_credit_and_spend_limits():
    runner, _ = runner_with([reply(status=402, error="Insufficient credits")], provider="openrouter")
    with pytest.raises(AgentError, match="out of credit .*openrouter.ai/settings/credits"):
        asyncio.run(runner.run(call(model=OPUS)))
    limit = {"limit_source": "openrouter_key_limit", "limit_usd": 10, "spend_usd": 10.02,
             "resets_at": "2026-11-01T00:00:00Z", "remedy_hint": "Raise the key's limit in Settings > API Keys."}
    runner, _ = runner_with([reply(status=403, error="Key limit exceeded", metadata=limit)], provider="openrouter")
    with pytest.raises(AgentError) as e:
        asyncio.run(runner.run(call(model=OPUS)))
    assert ("spend limit reached (this key's credit limit: $10.02 of $10.00; it resets 2026-11-01T00:00:00Z). "
            "Raise the key's limit in Settings > API Keys.") in str(e.value)
    runner, _ = runner_with([reply(status=403, error="Input flagged", metadata={"reasons": ["x"]})], provider="openrouter")
    with pytest.raises(AgentError, match=r"refused the request \(403: Input flagged\)"):
        asyncio.run(runner.run(call(model=OPUS)))


def test_openrouter_401_is_checked_against_the_key():
    runner, seen = runner_with([reply(status=401, error="No auth credentials found")], provider="openrouter",
                               key_status=401)
    with pytest.raises(AgentError, match="rejected the key"):
        asyncio.run(runner.run(call(model=OPUS)))
    assert len(chats(seen)) == 1                                                # a bad key isn't tried again
    runner, seen = runner_with([reply(status=401, error="User not found")], provider="openrouter")
    with pytest.raises(AgentError, match="your key works, so the fault is on OpenRouter's side"):
        asyncio.run(runner.run(call(model=OPUS)))
    assert len(chats(seen)) == 3                                                # tried three times
    runner, seen = runner_with([reply(status=401), reply()], provider="openrouter")
    assert "ratings.json" in asyncio.run(runner.run(call(model=OPUS))).files    # the second try worked


def test_a_200_that_carries_an_error_is_tried_again_then_explained():
    failed = lambda body: httpx.Response(200, json={"error": {"code": 502, "message": "Provider returned error"}})
    partway = lambda body: httpx.Response(200, json={"choices": [{"finish_reason": "error", "message": {"content": "<fi"},
                                                                  "error": {"code": 502, "message": "upstream died"}}]})
    runner, seen = runner_with([failed, partway, reply()], provider="openrouter")
    assert "ratings.json" in asyncio.run(runner.run(call(model=OPUS))).files and len(chats(seen)) == 3
    runner, seen = runner_with([failed], provider="openrouter")
    with pytest.raises(AgentError, match=r"failed while answering \(502: Provider returned error\)"):
        asyncio.run(runner.run(call(model=OPUS)))
    bad = lambda body: httpx.Response(200, json={"error": {"code": 400, "message": "Bad input"}})
    runner, seen = runner_with([bad], provider="openrouter")
    with pytest.raises(AgentError, match="Bad input"):
        asyncio.run(runner.run(call(model=OPUS)))
    assert len(chats(seen)) == 1                                                # not worth another try


def test_check_connection_on_openrouter_asks_about_the_key():
    runner, seen = runner_with([reply()], provider="openrouter", key_status=401, models=["anthropic/claude-opus-5.5"])
    with pytest.raises(AgentError, match="rejected the key"):                   # its model list is public
        asyncio.run(runner.models())
    assert [r.url.path for r, _ in seen] == ["/api/v1/key"]
    runner, seen = runner_with([reply()], provider="openrouter", models=["anthropic/claude-opus-5.5"])
    assert asyncio.run(runner.models()) == ["anthropic/claude-opus-5.5"]
    assert [r.url.path for r, _ in seen] == ["/api/v1/key", "/api/v1/models"]
