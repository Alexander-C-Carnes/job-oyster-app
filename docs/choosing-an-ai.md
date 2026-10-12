# Which AI does the work

Pick it on the **Profile** tab, under **AI**: the provider, its key, and a model and effort for each step (signal score, analysis, writing, the résumé chat, live scores). It's saved in `models:` in your `searches.yaml` and the key in your profile's `.env`. **Check connection** lists the provider's models, which also confirms the key works (OpenRouter shows its model list to anyone, so there the app asks OpenRouter about the key first). The first time you choose OpenRouter, it fills in Claude Sonnet 5.5 for each step and Claude Opus 5.5 for writing; pick others from its list.

Each provider's key stays saved under its own name, so you can keep several (say ChatGPT, Gemini and Kimi) and switch between them: choose another provider and click **Save**. The app remembers the models and effort you picked for each provider (`models.saved` in `searches.yaml`), so switching back puts them back.

The résumé chat has its own model menu, next to **New chat**. It lists every AI you've set up (Claude through your subscription, plus each provider with a key saved, or a local server you've used) and their models, with the Profile's résumé chat model as the default: Claude Sonnet 5.5 at medium effort on Claude, since every message is a call (on another provider, its writing model at medium until you pick one). Pick another to ask one message of, say, Claude Opus or Kimi; it answers at the chat's effort, and each reply says which model wrote it. Your pick is remembered in that browser. A proposed edit is still re-rated by the Profile's AI, so its scores stay comparable.

| Provider (`models.backend`) | How you pay | Key in `.env` |
|---|---|---|
| Claude, your subscription (`claude-code`, the default) | Your Pro or Max plan; nothing per call | none: sign in to Claude Code |
| Claude API (`api`) | Per call | `ANTHROPIC_API_KEY` |
| ChatGPT, OpenAI API (`openai`) | Per call (a ChatGPT subscription doesn't include it) | `OPENAI_API_KEY` |
| Gemini, Google AI API (`gemini`) | Per call; check Google for a free allowance | `GEMINI_API_KEY` |
| Kimi, Moonshot AI API (`kimi`) | Per call; a key from [platform.kimi.ai](https://platform.kimi.ai/console/api-keys), not the China platform | `MOONSHOT_API_KEY` |
| OpenRouter (`openrouter`): models from many companies | Per call; a few models are free | `OPENROUTER_API_KEY` |
| Ollama or LM Studio on your Mac (`ollama`, `lmstudio`) | Free | none |
| Any other OpenAI-compatible API (`openai-compatible`, with `models.base_url`) | Depends | `OPENAI_COMPATIBLE_API_KEY`, if it needs one |

Things to know before switching from Claude:

- **Quality.** The briefs and rules were tuned on Claude Opus. Every résumé still goes through the same format, honesty-trace and ATS checks, and broken output is asked for again, but a weaker model writes weaker résumés and fit scores.
- **Size.** Every candidate-facing step reads your whole impact record, every résumé and the rules at once, often 30,000 tokens or more. A local model needs a context window that large (Ollama: set `OLLAMA_CONTEXT_LENGTH=65536` and restart it; LM Studio: load the model with a 64k context). If a server cuts the prompt short, the step stops with an error rather than scoring on half your record.
- **Cost.** Make résumé takes about 14 calls (the median of measured runs), each with all of that material, so on a pay-per-call provider it costs real money per job. The same materials go first in every request, so a provider that caches repeated prompts charges much less for them after the first call. OpenAI, Kimi and DeepSeek do that on their own. Claude and Gemini cache only when the request marks what to keep, so the app marks it: the Claude API keeps your materials for an hour, and so does OpenRouter for Claude models; for Gemini models on OpenRouter it's five minutes, Gemini's fixed time. On the Claude API, the three résumé writers start together, so none could read what another was still writing: the app writes their shared prompt to the cache once first, with a request that reads the prompt and writes no answer (up to about $0.50 less per résumé on Opus 5.5). The token totals printed at the end of a run count what was written to the cache and read from it, and on OpenRouter what it charged.
- **Effort** is sent as `reasoning_effort`. On OpenRouter it goes as OpenRouter's `reasoning` setting instead (the only way it turns on Claude's thinking). Kimi has no medium effort, so on Kimi low, medium and high are sent as low, high and max.
- **Output length.** Every request leaves room for 64,000 output tokens (on OpenRouter, the model's own limit if that's lower), since some servers default to far fewer: Fireworks stops at 2,048, so writing a résumé hit its length limit. OpenAI gets it as `max_completion_tokens`, which its reasoning models need; the rest as `max_tokens`.
- **A model that turns down** the effort or the output limit gets the request again without it, and that model keeps getting it without. Other models still get it.
- **OpenRouter errors** say what went wrong: out of credit (add some at openrouter.ai/settings/credits), or a key, guardrail or workspace spending limit reached. Before a 401 is blamed on your key, the app asks OpenRouter whether the key works; if it does, the request is tried again, since OpenRouter has answered 401 during its own outages.

Signing in with a ChatGPT or Google subscription (instead of an API key) isn't supported yet: OpenAI's Codex CLI can't be fully stopped from using its own tools, and Google has retired the Gemini CLI's personal sign-in.

## Claude through your subscription

Every Claude step runs through the Claude Code CLI (`claude -p`), which uses your Claude subscription (Pro/Max) instead of API credits (`models.backend: claude-code`, the default). The pipeline uses the first `claude` that starts: each one on your PATH, then `~/.local/bin/claude`, then the CLI bundled with the Claude desktop app, so an old install that crashes under a newer Node is skipped. Set `CLAUDE_BIN` or `models.claude_bin` to choose one yourself. The CLI must be signed in: run `claude` and use `/login`. Each agent runs with no tools, no MCP servers and no skills; `ANTHROPIC_API_KEY` is removed from its environment so Claude Code can't bill an API key instead of your login. Calls count toward your plan's usage limits: triage is one short call per job (15 jobs took about 2 minutes), and a full tailoring run is about 14 calls. Each command prints its token totals at the end.

The pipeline's stages use Claude Opus 5.5 (`models:` in `searches.yaml`; triage at low effort, analysis at medium, writing at high). The résumé chat and its live scores, a call for every message and every edit, use Claude Sonnet 5.5 at medium. The impact record, résumé variants, rules and rubric form one system prompt shared by every candidate-facing agent. To use the Claude API instead, set `models.backend: api` and put `ANTHROPIC_API_KEY` in your profile's `.env`; that path needs API credits.
