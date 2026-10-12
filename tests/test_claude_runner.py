"""ClaudeRunner against the real Anthropic SDK with a mocked HTTP transport (no network)."""
import asyncio
import json

import anthropic
import httpx2
import pytest

from jobpipe.config import ModelCfg
from jobpipe.llm import AgentCall, AgentError, ClaudeRunner


def sse(text: str, stop_reason: str = "end_turn") -> bytes:
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
            "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 1, "cache_read_input_tokens": 5,
                      "cache_creation_input_tokens": 0}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None},
                           "usage": {"output_tokens": 20}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


def runner_with(responses):
    seen = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append((dict(request.headers), json.loads(request.content)))
        text, stop = responses[min(len(seen) - 1, len(responses) - 1)]
        return httpx2.Response(200, content=sse(text, stop), headers={"content-type": "text/event-stream"})

    client = anthropic.AsyncAnthropic(api_key="sk-test", max_retries=0,
                                      http_client=anthropic.DefaultAsyncHttpxClient(
                                          transport=httpx2.MockTransport(handler)))
    return ClaudeRunner(client=client), seen


def message_json(cache_write=0):
    """A non-streamed reply to a max_tokens 0 request: no content, stopped at max_tokens."""
    return {"id": "msg_0", "type": "message", "role": "assistant", "model": "claude-opus-5-5", "content": [],
            "stop_reason": "max_tokens", "stop_sequence": None,
            "usage": {"input_tokens": 12, "output_tokens": 0, "cache_read_input_tokens": 0,
                      "cache_creation_input_tokens": cache_write}}


def call(**kw):
    return AgentCall(**{**dict(label="04-matcher", model=ModelCfg("claude-opus-5-5", "high"),
                               instructions="Brief.", documents={"jd.md": "posting"},
                               expect=["ratings.json", "04-match.md"]), **kw})


def test_request_shape_caching_and_parsing():
    ok = '<file name="ratings.json">\n{"a": 1}\n</file>\n<file name="04-match.md">\n# M\n</file>\nDone.'
    runner, seen = runner_with([(ok, "end_turn")])
    res = asyncio.run(runner.run(call(tail="Your lens: A")))
    assert res.files["ratings.json"] == '{"a": 1}\n'
    headers, body = seen[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"] == {"effort": "high"}
    assert body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in headers["anthropic-beta"]
    assert "thinking" not in body and "temperature" not in body
    assert body["system"][1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "[Impact record]" in body["system"][1]["text"]
    user = body["messages"][0]["content"]
    assert user[0]["cache_control"] == {"type": "ephemeral"}          # shared job docs before the lens
    assert user[1]["text"].rstrip().endswith("ratings.json, 04-match.md")
    assert "Your lens: A" in user[1]["text"]
    assert runner.usage_log[0]["cache_read"] == 5


def test_warm_writes_the_shared_prefix_once_without_streaming_or_output():
    ok = '<file name="ratings.json">\n{"a": 1}\n</file>\n<file name="04-match.md">\n# M\n</file>'
    seen = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        seen.append((dict(request.headers), body))
        if body.get("stream"):
            return httpx2.Response(200, content=sse(ok), headers={"content-type": "text/event-stream"})
        return httpx2.Response(200, json=message_json(cache_write=33000))

    client = anthropic.AsyncAnthropic(api_key="sk-test", max_retries=0, http_client=anthropic.DefaultAsyncHttpxClient(
        transport=httpx2.MockTransport(handler)))
    runner = ClaudeRunner(client=client)
    writer = call(label="05-writer-A", tail="Your lens: A (posting language).")
    asyncio.run(runner.warm(writer))
    asyncio.run(runner.run(writer))
    (_, warm), (_, real) = seen
    assert warm["max_tokens"] == 0 and not warm.get("stream") and real["stream"] is True
    # the prefix the writer sends, cut where the writers part (their lens), with the writer's own effort: both
    # thinking and effort are part of what's cached, so a warm-up without them could write an entry nobody reads
    assert warm["system"] == real["system"] and warm["system"][1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert warm["messages"][0]["content"] == real["messages"][0]["content"][:1]
    assert warm["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert warm["output_config"] == real["output_config"] == {"effort": "high"}
    assert "thinking" not in warm and "fallbacks" not in warm and "output_config" in warm
    assert runner.usage_log[0] == {"label": "05-writer-A (cache warm-up)", "model": "claude-opus-5-5",
                                   "stop_reason": "max_tokens", "input": 12, "output": 0, "cache_read": 0,
                                   "cache_write": 33000}


def test_a_failed_warm_up_is_left_to_the_calls():
    seen = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "no"}})

    client = anthropic.AsyncAnthropic(api_key="sk-test", max_retries=0, http_client=anthropic.DefaultAsyncHttpxClient(
        transport=httpx2.MockTransport(handler)))
    runner = ClaudeRunner(client=client)
    asyncio.run(runner.warm(call(model=ModelCfg("claude-haiku-4-5", "low"), tail="lens")))
    assert runner.usage_log == [] and "output_config" not in seen[0]       # Haiku 4.5 takes no effort


def test_haiku_gets_no_effort_or_fallbacks():
    ok = '<file name="ratings.json">\n{"a": 1}\n</file>\n<file name="04-match.md">\n# M\n</file>'
    runner, seen = runner_with([(ok, "end_turn")])
    asyncio.run(runner.run(call(model=ModelCfg("claude-haiku-4-5", "low"))))
    headers, body = seen[0]
    assert body["model"] == "claude-haiku-4-5" and "output_config" not in body and "fallbacks" not in body
    assert "server-side-fallback" not in headers.get("anthropic-beta", "")


def test_haiku_5_5_gets_effort_but_no_fallbacks():
    ok = '<file name="ratings.json">\n{"a": 1}\n</file>\n<file name="04-match.md">\n# M\n</file>'
    runner, seen = runner_with([(ok, "end_turn")])
    asyncio.run(runner.run(call(model=ModelCfg("claude-haiku-5-5", "low"))))
    headers, body = seen[0]
    assert body["output_config"] == {"effort": "low"} and body["max_tokens"] == 64000
    assert "fallbacks" not in body                     # Haiku has no server-side fallback: a list would be a 400
    assert "server-side-fallback" not in headers.get("anthropic-beta", "")


def test_job_only_agents_get_no_candidate_materials():
    ok = '<file name="ratings.json">\n{}\n</file>\n<file name="04-match.md">\nx\n</file>'
    runner, seen = runner_with([(ok, "end_turn")])
    asyncio.run(runner.run(call(candidate=False)))
    assert len(seen[0][1]["system"]) == 1


def test_retries_once_on_missing_file_then_fails():
    runner, seen = runner_with([("no files here", "end_turn")])
    with pytest.raises(AgentError, match="missing file"):
        asyncio.run(runner.run(call()))
    assert len(seen) == 2
    assert "previous attempt had these problems" in seen[1][1]["messages"][0]["content"][-1]["text"]


def test_a_chat_reply_written_as_plain_prose_counts_as_its_file():
    runner, seen = runner_with([("Here's a draft you can paste in.\n\n> I ran onboarding.", "end_turn")])
    res = asyncio.run(runner.run(call(expect=["reply.md"], prose_file="reply.md")))
    assert res.files["reply.md"] == "Here's a draft you can paste in.\n\n> I ran onboarding.\n" and len(seen) == 1
    edit = '<file name="resume-edited.md">\n# R\n</file>\nTightened the summary.'
    runner, _ = runner_with([(edit, "end_turn")])
    res = asyncio.run(runner.run(call(expect=["reply.md"], prose_file="reply.md")))
    assert res.files == {"resume-edited.md": "# R\n", "reply.md": "Tightened the summary.\n"}
    runner, seen = runner_with([("", "end_turn")])        # no prose either: retried, then an error
    with pytest.raises(AgentError, match="missing file reply.md"):
        asyncio.run(runner.run(call(expect=["reply.md"], prose_file="reply.md")))
    assert len(seen) == 2


def test_refusal_and_max_tokens_raise():
    for stop in ("refusal", "max_tokens"):
        runner, _ = runner_with([("x", stop)])
        with pytest.raises(AgentError):
            asyncio.run(runner.run(call()))
