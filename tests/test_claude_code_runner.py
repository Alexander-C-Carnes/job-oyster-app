"""ClaudeCodeRunner against a stand-in `claude` executable that records how it was called."""
import asyncio
import json
import os
import stat
import sys
import textwrap

import pytest

from jobpipe import config, llm
from jobpipe.config import ModelCfg
from jobpipe.llm import AgentCall, AgentError, ClaudeCodeRunner
from jobpipe.pipeline import make_runner

OK = '<file name="ratings.json">\n{"a": 1}\n</file>\n<file name="04-match.md">\n# M\n</file>\nDone.'


def fake_claude(tmp_path, results):
    """results: list of dicts (the JSON `claude -p --output-format json` prints), one per call."""
    log = tmp_path / "calls.jsonl"
    (tmp_path / "results.json").write_text(json.dumps(results))
    script = tmp_path / "claude"
    script.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import json, os, sys
        log = {str(log)!r}
        n = sum(1 for _ in open(log)) if os.path.exists(log) else 0
        args = sys.argv[1:]
        sp = args[args.index("--system-prompt-file") + 1]
        with open(log, "a") as f:
            f.write(json.dumps({{"args": args, "stdin": sys.stdin.read(), "cwd": os.getcwd(),
                                "system": open(sp).read()[:20000],
                                "has_api_key": "ANTHROPIC_API_KEY" in os.environ,
                                "max_out": os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS")}}) + "\\n")
        results = json.load(open({str(tmp_path / "results.json")!r}))
        print(json.dumps(results[min(n, len(results) - 1)]))
    """))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script), log


def result(text, stop="end_turn", is_error=False):
    return {"type": "result", "subtype": "success", "is_error": is_error, "result": text, "stop_reason": stop,
            "usage": {"input_tokens": 3, "output_tokens": 9, "cache_read_input_tokens": 100,
                      "cache_creation_input_tokens": 0}}


def call(**kw):
    return AgentCall(**{**dict(label="04-matcher", model=ModelCfg("claude-opus-5-5", "high"),
                               instructions="Brief.", documents={"jd.md": "the posting"},
                               expect=["ratings.json", "04-match.md"]), **kw})


def test_invocation_uses_subscription_and_no_tools(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-not-leak")
    bin_, log = fake_claude(tmp_path, [result(OK)])
    runner = ClaudeCodeRunner(claude_bin=bin_)
    res = asyncio.run(runner.run(call(tail="Your lens: B")))
    assert res.files["ratings.json"] == '{"a": 1}\n'
    rec = json.loads(log.read_text().splitlines()[0])
    a = rec["args"]
    assert a[:3] == ["-p", "--output-format", "json"]
    assert a[a.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in a and "--disable-slash-commands" in a and "--no-session-persistence" in a
    assert a[a.index("--model") + 1] == "claude-opus-5-5" and a[a.index("--effort") + 1] == "high"
    assert "--bare" not in a                        # --bare skips the keychain login on macOS
    assert rec["has_api_key"] is False              # never bills an API key
    assert rec["max_out"] == "64000"
    assert "[Impact record]" in rec["system"] and "Four standing rules" in rec["system"]
    assert "--effort" not in ClaudeCodeRunner(claude_bin=bin_).command(call(model=ModelCfg("claude-haiku-4-5", "low")))
    assert "--effort" in ClaudeCodeRunner(claude_bin=bin_).command(call(model=ModelCfg("claude-haiku-5-5", "low")))
    assert '<document name="jd.md">' in rec["stdin"] and "Your lens: B" in rec["stdin"]
    assert rec["stdin"].rstrip().endswith("ratings.json, 04-match.md")
    assert os.path.realpath(rec["cwd"]) == os.path.realpath(runner.workdir)   # macOS: /var -> /private/var
    assert runner.usage_log[0]["cache_read"] == 100


def test_job_only_agents_get_no_candidate_materials(tmp_path):
    bin_, log = fake_claude(tmp_path, [result(OK)])
    asyncio.run(ClaudeCodeRunner(claude_bin=bin_).run(call(candidate=False)))
    rec = json.loads(log.read_text().splitlines()[0])
    assert "[Impact record]" not in rec["system"]


def test_retry_then_error_and_failures(tmp_path):
    bin_, log = fake_claude(tmp_path, [result("no files"), result(OK)])
    asyncio.run(ClaudeCodeRunner(claude_bin=bin_).run(call()))
    lines = log.read_text().splitlines()
    assert len(lines) == 2 and "previous attempt had these problems" in json.loads(lines[1])["stdin"]

    for bad in (result("x", is_error=True), result("x", stop="max_tokens"), result("x", stop="refusal")):
        d = tmp_path / f"b{len(list(tmp_path.iterdir()))}"
        d.mkdir()
        b, _ = fake_claude(d, [bad])
        with pytest.raises(AgentError):
            asyncio.run(ClaudeCodeRunner(claude_bin=b).run(call()))


def test_verbose_setting_prints_a_message_list(tmp_path):
    """With "verbose": true in Claude Code's settings, --output-format json prints every message, result last."""
    msgs = [{"type": "system", "subtype": "init"}, {"type": "assistant", "message": {}}, result(OK)]
    bin_, _ = fake_claude(tmp_path, [msgs])
    runner = ClaudeCodeRunner(claude_bin=bin_)
    res = asyncio.run(runner.run(call()))
    assert res.files["ratings.json"] == '{"a": 1}\n' and runner.usage_log[0]["output"] == 9

    d = tmp_path / "none"
    d.mkdir()
    b, _ = fake_claude(d, [[{"type": "system", "subtype": "init"}]])
    with pytest.raises(AgentError, match="without a result"):
        asyncio.run(ClaudeCodeRunner(claude_bin=b).run(call()))


def test_default_backend_is_claude_code(tmp_path):
    cfg = config.load()
    assert cfg.backend == "claude-code"
    bin_, _ = fake_claude(tmp_path, [result(OK)])
    cfg.claude_bin = bin_
    assert isinstance(make_runner(cfg), ClaudeCodeRunner)


def test_missing_cli_is_a_clear_error(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    monkeypatch.setattr(llm, "fallback_bins", lambda: [])
    with pytest.raises(RuntimeError, match="Claude Code CLI not found"):
        ClaudeCodeRunner()


def script(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\nimport sys\n" + textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


CRASH = """\
    sys.stderr.write("file:///cli.js:707\\n" + "x" * 400 + "\\nTypeError: Cannot read properties of undefined (reading 'prototype')\\n"
                     "    at file:///cli.js:707:25327\\n\\nNode.js v26.5.0\\n")
    sys.exit(1)
"""


def test_a_broken_cli_on_path_is_skipped_for_one_that_starts(tmp_path, monkeypatch):
    # An old npm install first on PATH that crashes under a newer Node, and a working CLI elsewhere.
    script(tmp_path / "old" / "claude", CRASH)
    good = script(tmp_path / "new" / "claude", 'print("2.1.0 (Claude Code)")\n')
    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "old"))
    monkeypatch.setattr(llm, "fallback_bins", lambda: [good])
    assert ClaudeCodeRunner().bin == str(good)
    monkeypatch.setattr(llm, "fallback_bins", lambda: [])
    with pytest.raises(RuntimeError, match=r"old/claude doesn't start \(TypeError: Cannot read properties.*npm install -g"):
        ClaudeCodeRunner()


def test_the_desktop_apps_bundled_cli_is_found_newest_first(tmp_path, monkeypatch):
    """A Mac with only the Claude app installed has its CLI at claude-code/<version>/<build id>/claude.app/Contents/
    MacOS/claude (earlier builds: no build id). It comes after the native installer's places, newest version first,
    by number (2.1.9 is older than 2.1.293)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    store = tmp_path / "Library/Application Support/Claude/claude-code"
    for ver, build in [("2.1.293", "8433d0d9cd0d"), ("2.1.9", "0ab1"), ("2.1.295", "9d6405864fdc")]:
        script(store / ver / build / "claude.app/Contents/MacOS/claude", f'print("{ver} (Claude Code)")\n')
    script(store / "2.0.1" / "claude.app/Contents/MacOS/claude", 'print("2.0.1 (Claude Code)")\n')
    bins = llm.fallback_bins()
    assert bins[:2] == [tmp_path / ".local/bin/claude", tmp_path / ".claude/local/claude"]
    assert [p.relative_to(store).parts[0] for p in bins[2:]] == ["2.1.295", "2.1.293", "2.1.9", "2.0.1"]

    monkeypatch.delenv("CLAUDE_BIN", raising=False)
    monkeypatch.setenv("PATH", "/nonexistent")
    assert ClaudeCodeRunner().bin == str(store / "2.1.295/9d6405864fdc/claude.app/Contents/MacOS/claude")


def test_crash_and_expired_login_say_what_happened(tmp_path):
    crashed = script(tmp_path / "c" / "claude", CRASH)
    with pytest.raises(AgentError, match=r"exited 1 without a result: TypeError: Cannot read properties"):
        asyncio.run(ClaudeCodeRunner(claude_bin=str(crashed)).run(call()))
    d = tmp_path / "auth"
    d.mkdir()
    b, _ = fake_claude(d, [result("Failed to authenticate: OAuth session expired and could not be refreshed", is_error=True)])
    with pytest.raises(AgentError, match=r"isn't signed in \(Failed to authenticate.*sign in with /login"):
        asyncio.run(ClaudeCodeRunner(claude_bin=b).run(call()))
