"""Run one resume-job-fit agent through an AI model: Claude (Claude Code or the API), or any
provider with an OpenAI-compatible chat API (OpenAI, Gemini, Kimi, OpenRouter, Ollama, LM Studio).

Each agent brief from skill/agents/ was written for a sub-agent that reads and writes
files in a run folder. Here the orchestrator passes the files inline as named
documents and the agent returns its output files as <file name="..."> blocks,
which are parsed and written to the run folder.

Caching: the candidate materials (impact record, resume variants, rules, rubric) are
the same for every job and every candidate-facing agent, so they sit in the system
prompt behind a cache breakpoint. Job-only agents (01-03) never see them. OpenAI-compatible
providers get the same system prompt first in every request, which is what their automatic
prompt caching (where they have it) reuses. Through OpenRouter, Claude and Gemini cache only
behind breakpoints, so those requests carry the same ones the Claude API gets.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Protocol

import anthropic
import httpx

from . import config
from .config import ModelCfg

FILE_RE = re.compile(r'<file name="([^"]+)">\n?(.*?)\n?</file>', re.S)

# Use preamble(), which fills in the candidate from searches.yaml.
PREAMBLE = """You are one sub-agent in {name}'s resume-job-fit pipeline, run by a Python orchestrator.

How files work here: you have no tools. Every file your brief tells you to read is included in this request as a named document (paths such as `<skill>/references/impact-record.md` or `jd.md` refer to the document with that file name). Write each output file your brief asks for as

<file name="FILE_NAME">
...full file contents...
</file>

using exactly the requested file names, one block per file, complete (never truncated or summarized). JSON files must be valid JSON with no comments. After the files, write the one-paragraph summary your brief asks for, outside any file block.

Four standing rules (every stage, every agent):
1. Only relevant experience. Cite {first}'s experience only where it maps to a requirement in this job.
2. Only the job's actual text, no inference about the job. Every statement about the role must come from the posting's own words. If the posting doesn't state something, write "Not stated in the posting".
3. Ask before calling something a gap. A requirement rated missing or partial in all sources is an unconfirmed gap, not a fact.
4. Mirror the posting's language wherever it truthfully fits. Truth sets the limit: a posting phrase goes in only where the evidence (impact record, resumes, or user-confirmed answers) supports it.
"""


def preamble() -> str:
    return config.candidate().personalize(PREAMBLE)


def older_claude(model: str) -> bool:
    """A Claude model from before effort (Haiku 4.5): send it no effort."""
    return model.startswith(OLDER_CLAUDE)


def refusal_fallback(model: str) -> bool:
    """Whether the API can hand a declined request to another model. Haiku can't (a list of fallback models
    is a 400 on Haiku 5.5, and "default" does nothing), so it gets no fallbacks parameter."""
    return not model.startswith(NO_FALLBACK)


def doc(name: str, text: str) -> str:
    return f'<document name="{name}">\n{text}\n</document>'


def candidate_materials() -> str:
    """Impact record, every resume variant, the rules and the scoring rubric (cached prefix)."""
    ref, method = config.REFERENCES, config.SKILL / "references"
    cand = config.candidate()
    parts = [
        "Candidate materials (read-only). Source names used in ratings.json are given in brackets.",
        doc("<skill>/references/impact-record.md [Impact record]", (ref / "impact-record.md").read_text()),
    ]
    for source, fname in cand.resumes.items():
        parts.append(doc(f"<skill>/references/{fname} [{source}]", (ref / fname).read_text()))
    notes = ref / "user-notes.md"           # confirmed facts, where an older version of the app kept them
    if notes.exists():
        parts.append(doc("user-notes.md [user-confirmed answers]", notes.read_text()))
    parts.append(doc("<skill>/references/resume-rules.md", cand.personalize((method / "resume-rules.md").read_text())))
    parts.append(doc("<skill>/references/scoring-and-report.md",
                     cand.personalize((method / "scoring-and-report.md").read_text())))
    return "\n\n".join(parts)


class AgentError(RuntimeError):
    pass


@dataclass
class AgentCall:
    label: str
    model: ModelCfg
    instructions: str                      # the brief plus orchestrator notes
    documents: dict[str, str]              # file name -> contents, job-specific
    expect: list[str]                      # output file names
    candidate: bool = True                 # include the cached candidate materials
    tail: str = ""                         # per-call text after the shared documents (e.g. writer lens)
    run_dir: Optional[Path] = None
    prose_file: str = ""                   # an expected file that the agent's prose outside file blocks can stand in for


@dataclass
class AgentResult:
    files: dict[str, str]
    summary: str
    usage: dict = field(default_factory=dict)


class Runner(Protocol):
    async def run(self, call: AgentCall) -> AgentResult: ...


async def warm(runner: Runner, call: AgentCall) -> None:
    """Before calls that start together and share call's prefix (they part at call.tail): have the runner write
    that prefix to the cache once, so they read it instead of each writing it. Only the Claude API runner can."""
    if hasattr(runner, "warm"):
        await runner.warm(call)


class _RetryingRunner:
    """Validates an agent's files and retries once, as the skill does for a missing or broken output."""
    usage_log: list[dict]

    async def _once(self, call: AgentCall, extra_note: str = "") -> AgentResult:
        raise NotImplementedError

    async def run(self, call: AgentCall) -> AgentResult:
        result = prose_as_file(call, await self._once(call))
        problems = validate(result, call.expect)
        if problems:
            note = ("Your previous attempt had these problems: " + "; ".join(problems)
                    + ". Produce every expected file again, complete and valid.")
            result = prose_as_file(call, await self._once(call, note))
            problems = validate(result, call.expect)
            if problems:
                raise AgentError(f"{call.label}: " + "; ".join(problems))
        if call.run_dir:
            write_files(call.run_dir, result.files)
        return result


def user_blocks(call: AgentCall, extra_note: str = "") -> list[dict]:
    """The user turn as text blocks: the shared job documents, then the brief. When a per-call tail follows
    (the three writers share everything up to their lens), a 5-minute breakpoint closes the documents."""
    shared = "\n\n".join(doc(name, text) for name, text in call.documents.items())
    blocks = [{"type": "text", "text": shared or "(no job documents)"}]
    if call.tail:
        blocks[0]["cache_control"] = {"type": "ephemeral"}
    blocks.append({"type": "text", "text": call.instructions + ("\n\n" + call.tail if call.tail else "")
                   + "\n\nExpected output files: " + ", ".join(call.expect)})
    if extra_note:
        blocks.append({"type": "text", "text": extra_note})
    return blocks


def user_text(call: AgentCall, extra_note: str = "") -> str:
    return "\n\n".join(b["text"] for b in user_blocks(call, extra_note))


def system_blocks(materials: Optional[str], cache: dict) -> list[dict]:
    """The system prompt as text blocks: the preamble, then the candidate materials behind a breakpoint."""
    blocks = [{"type": "text", "text": preamble()}]
    if materials is not None:
        blocks.append({"type": "text", "text": materials, "cache_control": cache})
    return blocks


def fallback_bins() -> list[Path]:
    """Where a working Claude Code CLI may be when the `claude` on PATH is missing or broken:
    the native installer's location, then the CLI bundled with the Claude desktop app (newest first).
    The desktop app keeps each CLI it downloads at claude-code/<version>/<build id>/claude.app/Contents/MacOS/claude
    (earlier builds left out the build id), so a Mac with only the Claude app installed still has one."""
    home = Path.home()
    store = home / "Library/Application Support/Claude/claude-code"
    cli = "claude.app/Contents/MacOS/claude"
    version = lambda p: [int(x) if x.isdigit() else 0 for x in p.relative_to(store).parts[0].split(".")]
    bundled = sorted([*store.glob(f"*/{cli}"), *store.glob(f"*/*/{cli}")], key=version, reverse=True)
    return [home / ".local/bin/claude", home / ".claude/local/claude", *bundled]


def error_line(stderr: str) -> str:
    """The line of a crash that says what went wrong (not the stack frames or minified source)."""
    lines = [l.strip() for l in stderr.splitlines() if l.strip() and not l.lstrip().startswith("at ") and len(l) < 300]
    return next((l for l in reversed(lines) if "Error" in l), lines[-1] if lines else "no error output")


def find_claude() -> str:
    """The first Claude Code CLI that starts: each `claude` on PATH, then fallback_bins().
    An old npm install can sit on PATH and crash under a newer Node, so each is tried with --version."""
    import subprocess
    on_path = [Path(d) / "claude" for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    broken = []
    for cand in dict.fromkeys([*on_path, *fallback_bins()]):
        if not (cand.is_file() and os.access(cand, os.X_OK)):
            continue
        try:
            p = subprocess.run([str(cand), "--version"], capture_output=True, text=True, timeout=30)
            if p.returncode == 0:
                return str(cand)
            broken.append(f"{cand} doesn't start ({error_line(p.stderr)})")
        except (OSError, subprocess.TimeoutExpired) as e:
            broken.append(f"{cand} doesn't start ({e})")
    fix = ("Install the current one (npm install -g @anthropic-ai/claude-code@latest), run `claude` once to log in "
           "with your subscription, or set CLAUDE_BIN.")
    if broken:
        raise RuntimeError("No working Claude Code CLI: " + "; ".join(broken) + ". " + fix)
    raise RuntimeError("Claude Code CLI not found. " + fix)


class ClaudeCodeRunner(_RetryingRunner):
    """Runs each agent through the Claude Code CLI (`claude -p`), so it counts against a
    Claude subscription (Pro/Max) instead of API credits.

    - ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN are removed from the child's environment;
      otherwise Claude Code would bill the API key instead of the subscription login.
    - No tools, no MCP servers, no skills, no saved session: the agent only reads the
      documents in its prompt and writes <file> blocks, exactly like the API runner.
    - The system prompt (preamble + candidate materials) goes in a file because it is
      larger than a single command-line argument may be.
    - Claude Code caches that system prompt on its own. There's no warm() here: `claude -p` can't
      send a max_tokens 0 request, so warming the writers' cache would cost a whole extra turn before them, and
      a subscription has no per-token charge for the warm-up to save.
    """
    def __init__(self, *, claude_bin: Optional[str] = None, max_parallel: int = 3,
                 timeout_s: int = 1800):
        import tempfile
        self.bin = claude_bin or os.environ.get("CLAUDE_BIN") or find_claude()
        self.sem = asyncio.Semaphore(max_parallel)
        self.timeout_s = timeout_s
        self.workdir = Path(tempfile.mkdtemp(prefix="jobpipe-claude-"))  # no CLAUDE.md or project settings here
        self._system_files: dict[bool, Path] = {}
        self.usage_log: list[dict] = []

    def _system_file(self, candidate: bool) -> Path:
        if candidate not in self._system_files:
            text = system_text(candidate)
            path = self.workdir / ("system-candidate.md" if candidate else "system-job.md")
            path.write_text(text, encoding="utf-8")
            self._system_files[candidate] = path
        return self._system_files[candidate]

    def command(self, call: AgentCall) -> list[str]:
        return [self.bin, "-p", "--output-format", "json", "--tools", "",
                "--system-prompt-file", str(self._system_file(call.candidate)),
                "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
                "--model", call.model.model] + ([] if older_claude(call.model.model) else ["--effort", call.model.effort])

    @staticmethod
    def child_env() -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        env.setdefault("CLAUDE_CODE_MAX_OUTPUT_TOKENS", "64000")
        return env

    async def _once(self, call: AgentCall, extra_note: str = "") -> AgentResult:
        async with self.sem:
            proc = await asyncio.create_subprocess_exec(
                *self.command(call), cwd=self.workdir, env=self.child_env(),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                out, err = await asyncio.wait_for(
                    proc.communicate(user_text(call, extra_note).encode()), self.timeout_s)
            except asyncio.TimeoutError:
                proc.kill()
                raise AgentError(f"{call.label}: claude -p timed out after {self.timeout_s}s") from None
        try:
            data = json.loads(out.decode())
        except json.JSONDecodeError:   # crashed before answering: stdout is empty or not the JSON result
            raise AgentError(f"{call.label}: {self.bin} exited {proc.returncode} without a result: "
                             f"{error_line(err.decode() or out.decode())}") from None
        if isinstance(data, list):   # "verbose": true in the user's Claude Code settings prints every message
            data = next((m for m in reversed(data) if isinstance(m, dict) and m.get("type") == "result"), None)
        if not isinstance(data, dict):
            raise AgentError(f"{call.label}: {self.bin} exited {proc.returncode} without a result: "
                             f"{error_line(err.decode() or out.decode())}")
        u = data.get("usage") or {}
        usage = {"label": call.label, "model": call.model.model, "stop_reason": data.get("stop_reason"),
                 "input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
                 "cache_read": u.get("cache_read_input_tokens", 0),
                 "cache_write": u.get("cache_creation_input_tokens", 0)}
        self.usage_log.append(usage)
        if data.get("is_error") or proc.returncode:
            text = str(data.get("result"))[:500]
            if "authenticate" in text.lower() or "/login" in text:
                raise AgentError(f"{call.label}: Claude Code isn't signed in ({text}). Run \"{self.bin}\" in a "
                                 "terminal, sign in with /login, then try again.")
            raise AgentError(f"{call.label}: Claude Code error ({data.get('subtype')}, "
                             f"{data.get('api_error_status')}): {text}")
        if data.get("stop_reason") == "refusal":
            raise AgentError(f"{call.label}: model declined")
        if data.get("stop_reason") == "max_tokens":
            raise AgentError(f"{call.label}: output hit max_tokens")
        return parse_output(data.get("result") or "", usage)


class ClaudeRunner(_RetryingRunner):
    """Runs each agent through the Claude API (needs API credits)."""
    def __init__(self, *, fallbacks: bool = True, client: Optional[anthropic.AsyncAnthropic] = None,
                 max_parallel: int = 4):
        self.client = client or anthropic.AsyncAnthropic()
        self.fallbacks = fallbacks
        self.sem = asyncio.Semaphore(max_parallel)
        self._candidate: Optional[str] = None
        self.usage_log: list[dict] = []

    def _system(self, candidate: bool) -> list[dict]:
        if candidate and self._candidate is None:
            self._candidate = candidate_materials()
        return system_blocks(self._candidate if candidate else None, {"type": "ephemeral", "ttl": "1h"})

    async def _once(self, call: AgentCall, extra_note: str = "") -> AgentResult:
        kwargs = dict(
            model=call.model.model,
            max_tokens=MAX_OUTPUT,
            system=self._system(call.candidate),
            messages=[{"role": "user", "content": user_blocks(call, extra_note)}],
        )
        if not older_claude(call.model.model):
            kwargs["output_config"] = {"effort": call.model.effort}
        if self.fallbacks and refusal_fallback(call.model.model):
            kwargs.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        async with self.sem:
            async with self.client.beta.messages.stream(**kwargs) as stream:
                msg = await stream.get_final_message()
        usage = {"label": call.label, "model": msg.model, "stop_reason": msg.stop_reason,
                 "input": msg.usage.input_tokens, "output": msg.usage.output_tokens,
                 "cache_read": msg.usage.cache_read_input_tokens or 0,
                 "cache_write": msg.usage.cache_creation_input_tokens or 0}
        self.usage_log.append(usage)
        if msg.stop_reason == "refusal":
            detail = getattr(msg, "stop_details", None)
            raise AgentError(f"{call.label}: model declined ({getattr(detail, 'category', None)})")
        if msg.stop_reason == "max_tokens":
            raise AgentError(f"{call.label}: output hit max_tokens")
        text = "".join(b.text for b in msg.content if b.type == "text")
        return parse_output(text, usage)

    async def warm(self, call: AgentCall) -> None:
        """Write the cache for calls sharing call's prefix before they start together (the three writers): the
        candidate materials, and the shared job documents when call.tail marks them. Started at once, each would
        write the same ~33k tokens itself, since none can read an entry another is still writing.

        A max_tokens 0 request runs only the prefill: it bills the cache write and no output. It can't stream, so
        it's a plain create. Thinking is left at the model's default and effort is the calls' own, as both are part
        of what's cached. A failure only means the calls write the cache themselves."""
        kwargs = dict(model=call.model.model, max_tokens=0, system=self._system(call.candidate),
                      messages=[{"role": "user", "content": user_blocks(call)[:1]}])
        if not older_claude(call.model.model):
            kwargs["output_config"] = {"effort": call.model.effort}
        try:
            async with self.sem:
                msg = await self.client.beta.messages.create(**kwargs)
        except anthropic.APIError:
            return
        self.usage_log.append({"label": f"{call.label} (cache warm-up)", "model": msg.model,
                               "stop_reason": msg.stop_reason, "input": msg.usage.input_tokens,
                               "output": msg.usage.output_tokens,
                               "cache_read": msg.usage.cache_read_input_tokens or 0,
                               "cache_write": msg.usage.cache_creation_input_tokens or 0})



@dataclass(frozen=True)
class Provider:
    label: str                       # what the app calls it
    base_url: str                    # the OpenAI-compatible API root ("" = models.base_url)
    key_env: str                     # the .env key holding its API key ("" = none needed)
    local: bool = False              # runs on this Mac
    signup: str = ""                 # where to get a key, or the app
    efforts: Optional[dict] = None   # the app's effort -> the provider's reasoning_effort (None: EFFORTS)
    defaults: Optional[dict] = None  # step (triage, analysis, writer, chat, rescore) -> the model the settings form offers
    limit_field: str = "max_tokens"  # the request field that sets the output limit


# models.backend values besides claude-code and api, each an OpenAI-compatible chat API.
PROVIDERS = {
    # max_tokens is deprecated there, and its reasoning models reject it.
    "openai": Provider("ChatGPT (OpenAI API)", "https://api.openai.com/v1", "OPENAI_API_KEY",
                       signup="https://platform.openai.com/api-keys", limit_field="max_completion_tokens"),
    "gemini": Provider("Gemini (Google AI API)", "https://generativelanguage.googleapis.com/v1beta/openai",
                       "GEMINI_API_KEY", signup="https://aistudio.google.com/apikey"),
    # Kimi K3 takes reasoning_effort low, high or max (no medium); older Kimi models reject it and get no effort.
    "kimi": Provider("Kimi (Moonshot AI API)", "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY",
                     signup="https://platform.kimi.ai/console/api-keys",
                     efforts={"low": "low", "medium": "high", "high": "max"}),
    # Its model list is sorted by name, so without these the form would offer whatever sorts first.
    "openrouter": Provider("OpenRouter (many models)", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
                           signup="https://openrouter.ai/keys",
                           defaults={"triage": "anthropic/claude-sonnet-5.5", "analysis": "anthropic/claude-sonnet-5.5",
                                     "writer": "anthropic/claude-opus-5.5", "chat": "anthropic/claude-sonnet-5.5",
                                     "rescore": "anthropic/claude-sonnet-5.5"}),
    "ollama": Provider("Ollama (on this Mac)", "http://localhost:11434/v1", "", local=True, signup="https://ollama.com"),
    "lmstudio": Provider("LM Studio (on this Mac)", "http://localhost:1234/v1", "", local=True,
                         signup="https://lmstudio.ai"),
    "openai-compatible": Provider("Another OpenAI-compatible API", "", "OPENAI_COMPATIBLE_API_KEY"),
}
CLAUDE_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5", "claude-haiku-4-5"]
OLDER_CLAUDE = ("claude-haiku-4-5",)                         # takes no effort
NO_FALLBACK = ("claude-haiku-4-5", "claude-haiku-5-5")      # no server-side refusal fallback
MAX_OUTPUT = 64000                                          # output tokens asked for (Fireworks' default is 2,048)
RETRY = (429, 500, 502, 503, 504)                           # statuses worth another try
# What a 400 or 422 names when it turns down the effort or the output limit sent with a request.
REJECTS = {"effort": re.compile(r"reasoning|effort", re.I),
           "limit": re.compile(r"max_(completion_|new_)?tokens|context (length|window)|token limit", re.I)}
UNSUPPORTED = re.compile(r"unsupported|unrecognized|unknown|not supported|not permitted|extra (fields|inputs)", re.I)
# OpenRouter's 403 for a spent budget: error.metadata.limit_source -> what ran out.
SPEND_LIMITS = {"openrouter_key_limit": "this key's credit limit", "openrouter_guardrail_budget": "a guardrail's budget",
                "openrouter_workspace_budget": "your workspace's budget"}
BACKEND_LABELS = {"claude-code": "Claude (your subscription)", "api": "Claude API",
                  **{k: p.label for k, p in PROVIDERS.items()}}
EFFORTS = {"low": "low", "medium": "medium", "high": "high"}   # anything else (max, xhigh) asks for high
# Models a provider lists that can't write text (embeddings, speech, images, video): left out of the settings form.
NOT_CHAT = re.compile(r"embed|tts|whisper|transcribe|dall-e|image|imagen|veo|audio|realtime|moderation|aqa|"
                      r"babbage|davinci", re.I)


def system_text(candidate: bool) -> str:
    return preamble() + ("\n\n" + candidate_materials() if candidate else "")


class OpenAICompatRunner(_RetryingRunner):
    """Runs each agent through an OpenAI-compatible chat API (POST /chat/completions).

    - The key comes from the provider's .env variable; local servers (Ollama, LM Studio) need none.
    - Effort is sent as reasoning_effort; on OpenRouter as its `reasoning` setting (the only way it turns on
      Claude's reasoning).
    - An output limit is sent too (the provider's limit_field: max_completion_tokens on OpenAI), as some servers'
      default is far too short for a résumé; OpenRouter works Claude's thinking budget out from it.
    - A model that rejects the effort or the limit gets the request again without it, and that model keeps
      getting it without (other models aren't affected: the web app keeps one runner for every run).
    - Through OpenRouter, Claude and Gemini get cache breakpoints (cache_breakpoint()). OpenAI, DeepSeek and
      Kimi cache the shared system prompt on their own.
    - A prompt the server cut short (a local model's context window is often far smaller than the
      candidate materials) is an error, not a silently worse answer.
    """
    def __init__(self, *, provider: str, base_url: Optional[str] = None, key_env: Optional[str] = None,
                 api_key: Optional[str] = None, client: Optional[httpx.AsyncClient] = None, max_parallel: int = 3,
                 timeout_s: int = 1800, retry_wait: float = 5.0):
        if provider not in PROVIDERS:
            raise ValueError(f"Unknown AI provider {provider!r} (have: {', '.join(PROVIDERS)})")
        self.provider, p = provider, PROVIDERS[provider]
        self.base_url = (base_url or p.base_url).rstrip("/")
        if not self.base_url:
            raise RuntimeError("models.base_url is not set: give the address of the OpenAI-compatible API "
                               "(e.g. http://localhost:8000/v1).")
        env = key_env if key_env is not None else p.key_env
        key = api_key or (os.environ.get(env, "") if env else "")
        if not key and not p.local and provider != "openai-compatible":     # a local or custom server may need none
            raise RuntimeError(f"{env} is not set. Add your {p.label} key on the Profile tab (AI), "
                               f"or in {config.PROFILE / '.env'}. Get one at {p.signup}.")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        if provider == "openrouter":
            headers.update({"X-Title": "Job Oyster", "HTTP-Referer": "https://github.com/Alexander-C-Carnes/job-search-app"})
        self.client = client or httpx.AsyncClient(headers=headers, timeout=timeout_s)
        if client is not None:
            self.client.headers.update(headers)
        self.sem = asyncio.Semaphore(max_parallel)
        self.retry_wait = retry_wait
        self.rejecting: dict[str, set[str]] = {"effort": set(), "limit": set()}   # setting -> models that reject it
        self.key_works: Optional[bool] = None            # OpenRouter: what GET /key said after the last 401
        self._limits: Optional[dict] = None              # OpenRouter: model id -> its listed output limit
        self._limits_lock = asyncio.Lock()
        self.usage_log: list[dict] = []

    @property
    def label(self) -> str:
        return PROVIDERS[self.provider].label

    def cache_breakpoint(self, model: str) -> Optional[dict]:
        """The cache_control that goes on the candidate materials, or None where the provider caches on its own
        (OpenAI, DeepSeek, Kimi) or not at all. OpenRouter caches Claude and Gemini only behind breakpoints:
        Claude's last an hour, as ClaudeRunner asks; Gemini's last a fixed 5 minutes."""
        if self.provider != "openrouter":
            return None
        if model.startswith("anthropic/"):
            return {"type": "ephemeral", "ttl": "1h"}
        if model.startswith("google/"):
            return {"type": "ephemeral"}
        return None

    async def _max_tokens(self, model: str) -> int:
        """MAX_OUTPUT, or on OpenRouter the model's own output limit when its model list gives a lower one."""
        if self.provider != "openrouter":
            return MAX_OUTPUT
        async with self._limits_lock:
            if self._limits is None:
                self._limits = {}
                try:
                    r = await self.client.get(self.base_url + "/models")
                    if r.status_code == 200:
                        self._limits = {m["id"]: (m.get("top_provider") or {}).get("max_completion_tokens")
                                        for m in r.json().get("data", []) if isinstance(m, dict) and m.get("id")}
                except (httpx.HTTPError, ValueError, AttributeError):
                    pass                                 # unknown: ask for MAX_OUTPUT
        limit = self._limits.get(model)
        return min(MAX_OUTPUT, limit) if isinstance(limit, int) and limit > 0 else MAX_OUTPUT

    async def _key_ok(self) -> Optional[bool]:
        """OpenRouter: whether GET /key accepts the key (401 if not), or None when that can't be told."""
        try:
            r = await self.client.get(self.base_url + "/key")
        except httpx.TransportError:
            return None
        return {200: True, 401: False}.get(r.status_code)

    async def _post(self, path: str, body: dict) -> httpx.Response:
        """POST with up to three tries for rate limits, server errors and dropped connections, and for the
        failures OpenRouter reports as a 200 with an error in it, or as a 401 while the key works (its
        outages have done that)."""
        for attempt in range(3):
            last, wait = attempt == 2, self.retry_wait * (attempt + 1) ** 2
            try:
                r = await self.client.post(self.base_url + path, json=body)
            except httpx.TransportError as e:
                if last:
                    raise self._unreachable(e) from None
            else:
                if r.status_code == 401 and self.provider == "openrouter":
                    self.key_works = await self._key_ok()
                err = body_error(r)
                again = (r.status_code in RETRY or r.status_code == 401 and self.key_works
                         or err is not None and (not isinstance(err.get("code"), int) or err["code"] in (408, *RETRY)))
                if last or not again:
                    return r
                wait = float(r.headers.get("retry-after") or 0) or wait
            await asyncio.sleep(min(wait, 120))

    def _unreachable(self, e: Exception) -> AgentError:
        where = f" Is {self.label.split(' (')[0]} running?" if PROVIDERS[self.provider].local else ""
        return AgentError(f"Couldn't reach {self.base_url} ({e.__class__.__name__}).{where}")

    def _error(self, label: str, r: httpx.Response) -> AgentError:
        try:
            err = r.json().get("error") or r.json()
            msg = err.get("message") if isinstance(err, dict) else str(err)
        except (ValueError, AttributeError):
            err, msg = None, r.text[:300]
        meta = (err.get("metadata") if isinstance(err, dict) else None) or {}
        env = PROVIDERS[self.provider].key_env
        if r.status_code == 401 and self.provider == "openrouter" and self.key_works:
            return AgentError(f"{label}: OpenRouter answered 401 ({msg}), but your key works, so the fault is on "
                              "OpenRouter's side. Try again in a few minutes (status.openrouter.ai).")
        if r.status_code == 402:
            where = " at openrouter.ai/settings/credits" if self.provider == "openrouter" else ""
            return AgentError(f"{label}: {self.label} is out of credit ({msg}). Add credit{where}, then try again.")
        if r.status_code == 403 and meta.get("limit_source"):
            src, resets = meta["limit_source"], meta.get("resets_at")
            spent = (f": {usd(meta['spend_usd'])} of {usd(meta['limit_usd'])}"
                     if meta.get("spend_usd") is not None and meta.get("limit_usd") is not None else "")
            return AgentError(f"{label}: OpenRouter spend limit reached ({SPEND_LIMITS.get(src, src)}{spent}"
                              + (f"; it resets {resets}" if resets else "") + "). "
                              + (meta.get("remedy_hint") or "Raise the limit on openrouter.ai, or wait for it to reset."))
        if r.status_code == 403 and self.provider == "openrouter":   # a moderation flag or a guardrail
            return AgentError(f"{label}: OpenRouter refused the request (403: {msg}).")
        if r.status_code in (401, 403):
            return AgentError(f"{label}: {self.label} rejected the key ({r.status_code}: {msg}). Check {env} on the "
                              f"Profile tab (AI).")
        if r.status_code == 404:
            return AgentError(f"{label}: {self.label} doesn't know that model, or the address is wrong "
                              f"({self.base_url}): {msg}")
        return AgentError(f"{label}: {self.label} error {r.status_code}: {msg}")

    async def _once(self, call: AgentCall, extra_note: str = "") -> AgentResult:
        model, cache = call.model.model, self.cache_breakpoint(call.model.model)
        if cache:       # the same text as blocks, with the breakpoints ClaudeRunner sends
            system = system_blocks(candidate_materials() if call.candidate else None, cache)
            user = user_blocks(call, extra_note)
        else:
            system, user = system_text(call.candidate), user_text(call, extra_note)
        body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
        p, optional = PROVIDERS[self.provider], {}
        if call.model.effort and model not in self.rejecting["effort"]:
            level = (p.efforts or EFFORTS).get(call.model.effort, "high")
            optional["effort"] = {"reasoning": {"effort": level}} if self.provider == "openrouter" else {"reasoning_effort": level}
        if model not in self.rejecting["limit"]:
            optional["limit"] = {p.limit_field: await self._max_tokens(model)}
        async with self.sem:
            while True:
                r = await self._post("/chat/completions", {**body, **{k: v for o in optional.values() for k, v in o.items()}})
                refused = turned_down(r, optional)
                if not refused:
                    break
                for setting in refused:              # asked again without it, and this model isn't sent it again
                    self.rejecting[setting].add(model)
                    del optional[setting]
        if r.status_code >= 400:
            raise self._error(call.label, r)
        if err := body_error(r):
            raise AgentError(f"{call.label}: {self.label} failed while answering "
                             f"({err.get('code') or 'no code'}: {err.get('message') or err}).")
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        u = data.get("usage") or {}
        details = u.get("prompt_tokens_details") or {}
        cached, written = details.get("cached_tokens") or 0, details.get("cache_write_tokens") or 0
        usage = {"label": call.label, "model": data.get("model") or model, "stop_reason": choice.get("finish_reason"),
                 "input": max(0, (u.get("prompt_tokens") or 0) - cached - written),
                 "output": u.get("completion_tokens") or 0, "cache_read": cached, "cache_write": written}
        if isinstance(u.get("cost"), (int, float)):     # OpenRouter: what it charged, in dollars
            usage["cost"] = u["cost"]
        self.usage_log.append(usage)
        sent = sum(len(x) if isinstance(x, str) else sum(len(b["text"]) for b in x)
                   for x in (system, user)) / 4                    # rough token count of what was sent
        if u.get("prompt_tokens") and u["prompt_tokens"] < sent * 0.5:
            hint = (" For Ollama, raise the context length (e.g. OLLAMA_CONTEXT_LENGTH=65536) and restart it."
                    if self.provider == "ollama" else " Load the model with a larger context length."
                    if PROVIDERS[self.provider].local else "")
            raise AgentError(f"{call.label}: {call.model.model} read only {u['prompt_tokens']:,} of about "
                             f"{int(sent):,} tokens: its context window is too small for your materials.{hint}")
        if msg.get("refusal"):
            raise AgentError(f"{call.label}: model declined ({str(msg['refusal'])[:200]})")
        if choice.get("finish_reason") == "length":
            raise AgentError(f"{call.label}: output hit the model's length limit")
        content = msg.get("content") or ""
        if isinstance(content, list):                              # some servers return content parts
            content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
        return parse_output(content, usage)

    async def models(self) -> list[str]:
        """The model ids the provider lists (GET /models), for the settings form, which also checks the key.
        OpenRouter lists its models to anyone, so there the key is checked first (GET /key)."""
        try:
            if self.provider == "openrouter":
                self.key_works = None                    # this 401 is the answer, not a check of one
                r = await self.client.get(self.base_url + "/key")
                if r.status_code >= 400:
                    raise self._error("models", r)
            r = await self.client.get(self.base_url + "/models")
        except httpx.TransportError as e:
            raise self._unreachable(e) from None
        if r.status_code >= 400:
            raise self._error("models", r)
        return sorted(m["id"] for m in r.json().get("data", []) if m.get("id") and not NOT_CHAT.search(m["id"]))


def turned_down(r: httpx.Response, sent: dict) -> list[str]:
    """Which optional settings sent with a request ("effort", "limit") a 400 or 422 turns down: the ones its message
    names, else the first sent when it only says something is unsupported (how some servers turn effort down)."""
    if r.status_code not in (400, 422) or not sent:
        return []
    named = [s for s in sent if REJECTS[s].search(r.text)]
    return named or ([next(iter(sent))] if UNSUPPORTED.search(r.text) else [])


def body_error(r: httpx.Response) -> Optional[dict]:
    """The error a 200 can carry. OpenRouter answers 200 once a provider takes the request, so a later failure
    comes in the body: a top-level error, or choices[0].error with finish_reason "error"."""
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    choice = (data.get("choices") or [{}])[0]
    choice = choice if isinstance(choice, dict) else {}
    err = data.get("error") or choice.get("error") or (
        {"message": "the model stopped with an error"} if choice.get("finish_reason") == "error" else None)
    if not err:
        return None
    return err if isinstance(err, dict) else {"message": str(err)}


def usd(x) -> str:
    try:
        return f"${float(x):,.2f}"
    except (TypeError, ValueError):
        return str(x)


def parse_output(text: str, usage: Optional[dict] = None) -> AgentResult:
    files = {name.strip(): body.strip() + "\n" for name, body in FILE_RE.findall(text)}
    summary = FILE_RE.sub("", text).strip()
    return AgentResult(files=files, summary=summary, usage=usage or {})


def prose_as_file(call: AgentCall, result: AgentResult) -> AgentResult:
    """A chat reply written as plain prose, without its <file> block, still counts as that file: asked
    for something conversational (a question to answer, a line to explain), a model often just answers."""
    if call.prose_file and call.prose_file not in result.files and result.summary.strip():
        result.files[call.prose_file] = result.summary.strip() + "\n"
    return result


def validate(result: AgentResult, expect: list[str]) -> list[str]:
    problems = []
    for name in expect:
        if name not in result.files:
            problems.append(f"missing file {name}")
        elif name.endswith(".json"):
            try:
                json.loads(result.files[name])
            except json.JSONDecodeError as e:
                problems.append(f"{name} is not valid JSON ({e})")
    return problems


def write_files(run_dir: Path, files: dict[str, str]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (run_dir / Path(name).name).write_text(body, encoding="utf-8")


def brief(name: str) -> str:
    return config.candidate().personalize((config.SKILL / "agents" / name).read_text())
