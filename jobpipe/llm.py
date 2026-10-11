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
prompt caching (where they have it) reuses.
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
    """A Claude model from before effort and refusal fallbacks (Haiku 4.5): send it neither."""
    return model.startswith(OLDER_CLAUDE)


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


@dataclass
class AgentResult:
    files: dict[str, str]
    summary: str
    usage: dict = field(default_factory=dict)


class Runner(Protocol):
    async def run(self, call: AgentCall) -> AgentResult: ...


class _RetryingRunner:
    """Validates an agent's files and retries once, as the skill does for a missing or broken output."""
    usage_log: list[dict]

    async def _once(self, call: AgentCall, extra_note: str = "") -> AgentResult:
        raise NotImplementedError

    async def run(self, call: AgentCall) -> AgentResult:
        result = await self._once(call)
        problems = validate(result, call.expect)
        if problems:
            note = ("Your previous attempt had these problems: " + "; ".join(problems)
                    + ". Produce every expected file again, complete and valid.")
            result = await self._once(call, note)
            problems = validate(result, call.expect)
            if problems:
                raise AgentError(f"{call.label}: " + "; ".join(problems))
        if call.run_dir:
            write_files(call.run_dir, result.files)
        return result


def user_text(call: AgentCall, extra_note: str = "") -> str:
    shared = "\n\n".join(doc(name, text) for name, text in call.documents.items())
    return "\n\n".join(x for x in (
        shared or "(no job documents)",
        call.instructions + ("\n\n" + call.tail if call.tail else "")
        + "\n\nExpected output files: " + ", ".join(call.expect),
        extra_note) if x)


def fallback_bins() -> list[Path]:
    """Where a working Claude Code CLI may be when the `claude` on PATH is missing or broken:
    the native installer's location, then the CLI bundled with the Claude desktop app (newest first)."""
    home = Path.home()
    bundled = sorted((home / "Library/Application Support/Claude/claude-code").glob("*/claude.app/Contents/MacOS/claude"),
                     key=lambda p: [int(x) if x.isdigit() else 0 for x in p.parts[-5].split(".")], reverse=True)
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
        blocks = [{"type": "text", "text": preamble()}]
        if candidate:
            if self._candidate is None:
                self._candidate = candidate_materials()
            blocks.append({"type": "text", "text": self._candidate,
                           "cache_control": {"type": "ephemeral", "ttl": "1h"}})
        return blocks

    def _user(self, call: AgentCall) -> list[dict]:
        shared = "\n\n".join(doc(name, text) for name, text in call.documents.items())
        blocks = [{"type": "text", "text": shared or "(no job documents)"}]
        # Second breakpoint: the three writers share everything up to their lens.
        if call.tail:
            blocks[0]["cache_control"] = {"type": "ephemeral"}
        blocks.append({"type": "text", "text": call.instructions + ("\n\n" + call.tail if call.tail else "")
                       + "\n\nExpected output files: " + ", ".join(call.expect)})
        return blocks

    async def _once(self, call: AgentCall, extra_note: str = "") -> AgentResult:
        kwargs = dict(
            model=call.model.model,
            max_tokens=64000,
            system=self._system(call.candidate),
            messages=[{"role": "user", "content": self._user(call) + (
                [{"type": "text", "text": extra_note}] if extra_note else [])}],
        )
        if not older_claude(call.model.model):
            kwargs["output_config"] = {"effort": call.model.effort}
        if self.fallbacks and not older_claude(call.model.model):
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



@dataclass(frozen=True)
class Provider:
    label: str                       # what the app calls it
    base_url: str                    # the OpenAI-compatible API root ("" = models.base_url)
    key_env: str                     # the .env key holding its API key ("" = none needed)
    local: bool = False              # runs on this Mac
    signup: str = ""                 # where to get a key, or the app
    efforts: Optional[dict] = None   # the app's effort -> the provider's reasoning_effort (None: EFFORTS)


# models.backend values besides claude-code and api, each an OpenAI-compatible chat API.
PROVIDERS = {
    "openai": Provider("ChatGPT (OpenAI API)", "https://api.openai.com/v1", "OPENAI_API_KEY",
                       signup="https://platform.openai.com/api-keys"),
    "gemini": Provider("Gemini (Google AI API)", "https://generativelanguage.googleapis.com/v1beta/openai",
                       "GEMINI_API_KEY", signup="https://aistudio.google.com/apikey"),
    # Kimi K3 takes reasoning_effort low, high or max (no medium); older Kimi models reject it and get no effort.
    "kimi": Provider("Kimi (Moonshot AI API)", "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY",
                     signup="https://platform.kimi.ai/console/api-keys",
                     efforts={"low": "low", "medium": "high", "high": "max"}),
    "openrouter": Provider("OpenRouter (many models)", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
                           signup="https://openrouter.ai/keys"),
    "ollama": Provider("Ollama (on this Mac)", "http://localhost:11434/v1", "", local=True, signup="https://ollama.com"),
    "lmstudio": Provider("LM Studio (on this Mac)", "http://localhost:1234/v1", "", local=True,
                         signup="https://lmstudio.ai"),
    "openai-compatible": Provider("Another OpenAI-compatible API", "", "OPENAI_COMPATIBLE_API_KEY"),
}
CLAUDE_MODELS = ["claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"]
OLDER_CLAUDE = ("claude-haiku-4-5",)
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
    - Effort is sent as reasoning_effort. A model or server that rejects it gets the request again
      without it, and keeps getting it without.
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
            headers.update({"X-Title": "job-search", "HTTP-Referer": "https://github.com/Alexander-C-Carnes/job-search-app"})
        self.client = client or httpx.AsyncClient(headers=headers, timeout=timeout_s)
        if client is not None:
            self.client.headers.update(headers)
        self.sem = asyncio.Semaphore(max_parallel)
        self.retry_wait = retry_wait
        self.send_effort = True
        self.usage_log: list[dict] = []

    @property
    def label(self) -> str:
        return PROVIDERS[self.provider].label

    async def _post(self, path: str, body: dict) -> httpx.Response:
        """POST with up to three tries for rate limits, server errors and dropped connections."""
        for attempt in range(3):
            last, wait = attempt == 2, self.retry_wait * (attempt + 1) ** 2
            try:
                r = await self.client.post(self.base_url + path, json=body)
            except httpx.TransportError as e:
                if last:
                    raise self._unreachable(e) from None
            else:
                if last or r.status_code not in (429, 500, 502, 503, 504):
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
        except ValueError:
            msg = r.text[:300]
        if r.status_code in (401, 403):
            env = PROVIDERS[self.provider].key_env
            return AgentError(f"{label}: {self.label} rejected the key ({r.status_code}: {msg}). Check {env} on the "
                              f"Profile tab (AI).")
        if r.status_code == 404:
            return AgentError(f"{label}: {self.label} doesn't know that model, or the address is wrong "
                              f"({self.base_url}): {msg}")
        return AgentError(f"{label}: {self.label} error {r.status_code}: {msg}")

    async def _once(self, call: AgentCall, extra_note: str = "") -> AgentResult:
        system, user = system_text(call.candidate), user_text(call, extra_note)
        body = {"model": call.model.model, "messages": [{"role": "system", "content": system},
                                                        {"role": "user", "content": user}]}
        async with self.sem:
            if self.send_effort and call.model.effort:
                efforts = PROVIDERS[self.provider].efforts or EFFORTS
                r = await self._post("/chat/completions", {**body, "reasoning_effort": efforts.get(call.model.effort, "high")})
                if r.status_code == 400 and re.search(r"reasoning|effort|unsupported|unrecognized|unknown", r.text, re.I):
                    self.send_effort = False
                    r = await self._post("/chat/completions", body)
            else:
                r = await self._post("/chat/completions", body)
        if r.status_code >= 400:
            raise self._error(call.label, r)
        data = r.json()
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        u = data.get("usage") or {}
        cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
        usage = {"label": call.label, "model": data.get("model") or call.model.model,
                 "stop_reason": choice.get("finish_reason"), "input": (u.get("prompt_tokens") or 0) - cached,
                 "output": u.get("completion_tokens") or 0, "cache_read": cached, "cache_write": 0}
        self.usage_log.append(usage)
        sent = (len(system) + len(user)) / 4                       # rough token count of what was sent
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
        """The model ids the provider lists (GET /models), for the settings form."""
        try:
            r = await self.client.get(self.base_url + "/models")
        except httpx.TransportError as e:
            raise self._unreachable(e) from None
        if r.status_code >= 400:
            raise self._error("models", r)
        return sorted(m["id"] for m in r.json().get("data", []) if m.get("id") and not NOT_CHAT.search(m["id"]))


def parse_output(text: str, usage: Optional[dict] = None) -> AgentResult:
    files = {name.strip(): body.strip() + "\n" for name, body in FILE_RE.findall(text)}
    summary = FILE_RE.sub("", text).strip()
    return AgentResult(files=files, summary=summary, usage=usage or {})


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
