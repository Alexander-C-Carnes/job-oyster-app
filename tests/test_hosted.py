"""Hosted mode (OYSTER_HOSTED=1, docs/paid-service.md item 1.1): the AI is always the proxy, on allowed models, and
nothing that picks the AI or holds keys can be changed. With the switch off, nothing changes."""
import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone

import pytest

from jobpipe import config, hosted
from jobpipe.llm import AgentError, ClaudeCodeRunner, ClaudeRunner, OpenAICompatRunner
from jobpipe.pipeline import make_runner
from jobpipe.triage import triage_one
from conftest import FakeRunner
from test_web import H, env  # noqa: F401 - the web app fixture

PROXY = "https://ai-proxy.example/v1"


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv(hosted.SWITCH, "1")
    monkeypatch.setenv(hosted.PROXY_URL, PROXY)
    monkeypatch.setenv(hosted.PROXY_TOKEN, "user-token")


@pytest.fixture
def henv(on, env):  # noqa: F811 - the switch is on before the app's config is loaded
    return env


def searches(tmp_path, models: str):
    path = tmp_path / "searches.yaml"
    shutil.copy(config.EXAMPLE_PROFILE / "searches.yaml", path)
    raw = config.yaml.safe_load(path.read_text()) or {}
    raw["models"] = config.yaml.safe_load(models)
    path.write_text(config.yaml.safe_dump(raw))
    return path


ELSEWHERE = """
backend: ollama
base_url: http://169.254.169.254/v1
api_key_env: AWS_SECRET_ACCESS_KEY
claude_bin: /bin/sh
triage: {model: claude-opus-5-5, effort: high}
writer: {model: gpt-oss:20b, effort: max}
chat: {model: claude-haiku-5-5, effort: low}
"""


def test_the_switch_reads_like_the_other_oyster_settings(monkeypatch):
    for v, want in (("1", True), ("true", True), ("on", True), ("0", False), ("", False), ("no", False)):
        monkeypatch.setenv(hosted.SWITCH, v)
        assert hosted.enabled() is want
    monkeypatch.delenv(hosted.SWITCH)
    assert not hosted.enabled()


def test_hosted_config_is_pinned_to_the_proxy_and_allowed_models(on, tmp_path):
    cfg = config.load(searches(tmp_path, ELSEWHERE))
    assert (cfg.backend, cfg.base_url, cfg.api_key_env, cfg.claude_bin) == ("api", PROXY, hosted.PROXY_TOKEN, None)
    assert cfg.triage_model == config.ModelCfg("claude-haiku-5-5", "high")       # Opus isn't allowed for Signal score
    assert cfg.writer_model == config.ModelCfg("claude-opus-5-5", "medium")      # nor another provider's model, nor max
    assert cfg.chat_model == config.ModelCfg("claude-haiku-5-5", "low")          # an allowed pick is kept
    for stage in hosted.MODELS:
        assert hosted.allowed(stage, getattr(cfg, f"{stage}_model").model)


def test_hosted_mode_wont_start_without_the_proxy(monkeypatch, tmp_path):
    path = searches(tmp_path, ELSEWHERE)
    monkeypatch.setenv(hosted.SWITCH, "1")
    monkeypatch.delenv(hosted.PROXY_URL, raising=False)
    monkeypatch.setenv(hosted.PROXY_TOKEN, "user-token")
    with pytest.raises(RuntimeError, match=hosted.PROXY_URL):
        config.load(path)
    for bad in ("file:///etc/passwd", "ai-proxy.example", "https://"):
        monkeypatch.setenv(hosted.PROXY_URL, bad)
        with pytest.raises(RuntimeError, match=hosted.PROXY_URL):
            config.load(path)
    monkeypatch.setenv(hosted.PROXY_URL, PROXY)
    monkeypatch.delenv(hosted.PROXY_TOKEN)
    with pytest.raises(RuntimeError, match=hosted.PROXY_TOKEN):
        config.load(path)


def test_with_the_switch_off_the_settings_are_used_as_written(monkeypatch, tmp_path):
    monkeypatch.setenv(hosted.SWITCH, "0")
    cfg = config.load(searches(tmp_path, ELSEWHERE))
    assert (cfg.backend, cfg.base_url, cfg.api_key_env, cfg.claude_bin) == \
        ("ollama", "http://169.254.169.254/v1", "AWS_SECRET_ACCESS_KEY", "/bin/sh")
    assert cfg.triage_model == config.ModelCfg("claude-opus-5-5", "high")
    assert isinstance(make_runner(cfg), OpenAICompatRunner)


@pytest.mark.parametrize("backend", ["claude-code", "api", "ollama", "lmstudio", "openai-compatible"])
def test_hosted_runners_always_reach_the_proxy_with_the_users_token(on, tmp_path, backend):
    cfg = config.load(searches(tmp_path, "{backend: claude-code}"))
    cfg.backend, cfg.base_url = backend, "http://127.0.0.1:11434/v1"         # changed after loading: still the proxy
    r = make_runner(cfg)
    assert isinstance(r, ClaudeRunner) and not isinstance(r, ClaudeCodeRunner)
    assert str(r.client.base_url).rstrip("/") == PROXY and r.client.api_key == "user-token"


REFUSED = [("put", "/api/ai", {"backend": "ollama", "base_url": "http://127.0.0.1:11434/v1", "stages": {}}),
           ("put", "/api/ai", {"backend": "api", "stages": {}, "key": "sk-ant-mine"}),
           ("get", "/api/keys", None),
           ("put", "/api/keys", {"name": "JOBSPIPE_API_KEY", "value": "x"}),
           ("get", "/api/ai/claude", None),
           ("post", "/api/ai/claude-login", None),
           ("post", "/api/ai/models", {"backend": "openai-compatible", "base_url": "http://169.254.169.254/v1"}),
           ("post", "/api/ai/models", {"backend": "claude-code"})]


@pytest.mark.parametrize("method,path,body", REFUSED)
def test_hosted_refuses_changing_the_ai_keys_or_claude_sign_in(henv, method, path, body):
    r = getattr(henv["client"], method)(path, headers=H, **({"json": body} if body is not None else {}))
    assert r.status_code == 403 and r.json()["detail"] == hosted.REFUSED
    assert henv["cfg"].backend == "api" and henv["cfg"].base_url == PROXY


def test_hosted_settings_read_back_as_the_websites_ai(henv):
    c = henv["client"]
    a = c.get("/api/ai", headers=H).json()
    assert a["hosted"] and a["backend"] == "api" and a["env_file"] == ""
    assert [p["id"] for p in a["providers"]] == ["api"] and a["providers"][0]["label"] == hosted.LABEL
    assert all(hosted.allowed(s, a["stages"][s]["model"]) for s in hosted.MODELS)
    s = c.get("/api/summary", headers=H).json()
    assert s["hosted"] and s["backend_label"] == hosted.LABEL
    m = c.get("/api/ai/chat-models", headers=H).json()
    assert [g["models"] for g in m["groups"]] == [list(hosted.MODELS["chat"])]
    assert m["default"] == {"backend": "api", "model": henv["cfg"].chat_model.model}


def test_hosted_chat_takes_only_the_allowed_models(henv):
    c, runner = henv["client"], henv["runner"]
    for pick in ({"backend": "ollama"}, {"backend": "claude-code"}, {"model": "claude-opus-5-5"},
                 {"backend": "api", "model": "gpt-oss:20b"}):
        r = c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "What's weakest?", **pick})
        assert r.status_code == 403, pick
    r = c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "What's weakest?", "model": "claude-haiku-5-5"})
    assert r.status_code == 200
    assert [x for x in runner.calls if x.label == "resume-edit"][-1].model.model == "claude-haiku-5-5"


def test_with_the_switch_off_the_ai_routes_work_as_before(env, monkeypatch):  # noqa: F811
    monkeypatch.setenv(hosted.SWITCH, "0")
    c = env["client"]
    a = c.get("/api/ai", headers=H).json()
    assert "hosted" not in a and len(a["providers"]) > 1
    assert c.get("/api/keys", headers=H).status_code == 200
    assert c.post("/api/ai/models", headers=H, json={"backend": "claude-code"}).json()["models"][0] == "claude-opus-5-5"
    assert c.get("/api/summary", headers=H).json()["hosted"] is False


# ---- 1.3: Signal score on Haiku, with a daily cap per user -------------------------------------------------------

class Failing(FakeRunner):
    async def run(self, call):
        self.calls.append(call)
        raise AgentError("the proxy is down")


def score(runner, n=1):
    model = config.ModelCfg("claude-haiku-5-5", "low")
    return [asyncio.run(triage_one(runner, model, "# A posting", f"j{i}")) for i in range(n)]


def test_hosted_signal_scores_stop_at_the_daily_cap(on, tmp_dirs, monkeypatch):
    monkeypatch.setenv(hosted.SIGNAL_CAP, "2")
    runner = FakeRunner()
    score(runner, 2)
    with pytest.raises(hosted.OverCap, match="2 free Signal scores"):
        score(runner)
    assert len(runner.calls) == 2                                   # the third never reached the AI
    saved = json.loads((config.DATA / "signal-scores.json").read_text())
    assert saved == {"day": datetime.now(timezone.utc).date().isoformat(), "used": 2}


def test_a_failed_signal_score_doesnt_count(on, tmp_dirs, monkeypatch):
    monkeypatch.setenv(hosted.SIGNAL_CAP, "1")
    with pytest.raises(AgentError):
        score(Failing())
    score(FakeRunner())                                             # the failed one was given back


def test_the_cap_starts_again_each_day_and_is_shared_by_the_runs(on, tmp_dirs, monkeypatch):
    monkeypatch.setenv(hosted.SIGNAL_CAP, "1")
    config.DATA.mkdir(parents=True, exist_ok=True)
    (config.DATA / "signal-scores.json").write_text(json.dumps({"day": "2026-10-31", "used": 1}))
    score(FakeRunner())                                             # yesterday's don't count
    # A run is its own process with the same data folder: it sees today's score.
    code = ("import sys; from jobpipe import config, hosted; config.DATA = __import__('pathlib').Path(sys.argv[1])\n"
            "try: hosted._count(1)\nexcept hosted.OverCap: print('over')")
    out = subprocess.run([sys.executable, "-c", code, str(config.DATA)], capture_output=True, text=True,
                         env={**os.environ, hosted.SIGNAL_CAP: "1"}, cwd=config.ROOT)
    assert out.stdout.strip() == "over", out.stderr


def test_the_cap_is_a_setting_and_off_hosted_mode_there_is_none(tmp_dirs, monkeypatch):
    for v, want in (("", hosted.SIGNAL_CAP_DEFAULT), ("250", 250), ("lots", hosted.SIGNAL_CAP_DEFAULT), ("-3", 0)):
        monkeypatch.setenv(hosted.SIGNAL_CAP, v)
        assert hosted.signal_cap() == want
    monkeypatch.setenv(hosted.SWITCH, "0")
    monkeypatch.setenv(hosted.SIGNAL_CAP, "0")
    score(FakeRunner(), 3)
    assert not (config.DATA / "signal-scores.json").exists()


def test_the_score_button_runs_on_haiku_and_says_when_the_cap_is_reached(henv, monkeypatch):
    monkeypatch.setenv(hosted.SIGNAL_CAP, "1")
    c, runner = henv["client"], henv["runner"].pipeline
    assert c.post("/api/jobs/j1/score", headers=H).status_code == 200
    assert [x.model.model for x in runner.calls if x.label.startswith("triage:")] == ["claude-haiku-5-5"]
    r = c.post("/api/jobs/j2/score", headers=H)
    assert r.status_code == 429 and "Signal scores are used up" in r.json()["detail"]


# ---- 2.3: Own AI picks its provider and models; every call still goes through the proxy -------------------------
@pytest.fixture
def own(on, monkeypatch):
    monkeypatch.setenv(hosted.PLAN, "own")


@pytest.fixture
def oenv(own, env, monkeypatch):  # noqa: F811 - the plan is set before the app's config is loaded
    import jobpipe.pipeline as pipeline
    saved: dict = {}
    monkeypatch.setattr(pipeline, "own_ai_keys", lambda: {b: {"base_url": u} for b, (k, u) in saved.items()})
    monkeypatch.setattr(pipeline, "save_own_ai_key", lambda b, k, u="": saved.__setitem__(b, (k, u)))
    return {**env, "saved": saved}


def test_own_ai_keeps_the_users_provider_and_models_but_not_local_ones(own, tmp_path):
    cfg = config.load(searches(tmp_path, "{backend: openai, writer: {model: gpt-6, effort: high}, "
                                         "triage: {model: gpt-6-mini, effort: low}}"))
    assert (cfg.backend, cfg.base_url, cfg.api_key_env, cfg.claude_bin) == \
        ("openai", PROXY + "/own/openai", hosted.PROXY_TOKEN, None)
    assert cfg.writer_model == config.ModelCfg("gpt-6", "high") and cfg.triage_model.model == "gpt-6-mini"
    for elsewhere in ("ollama", "lmstudio", "claude-code"):
        cfg = config.load(searches(tmp_path, f"{{backend: {elsewhere}, base_url: 'http://127.0.0.1:11434/v1'}}"))
        assert (cfg.backend, cfg.base_url) == ("api", PROXY)
    assert hosted.allowed("writer", "claude-fable-5-1")             # any model of theirs


def test_own_ai_runners_reach_the_proxy_with_the_users_token(own, tmp_path):
    cfg = config.load(searches(tmp_path, "{backend: gemini}"))
    r = make_runner(cfg)
    assert isinstance(r, OpenAICompatRunner) and r.base_url == PROXY + "/own/gemini"
    assert r.client.headers["authorization"] == "Bearer user-token"
    cfg.backend = "api"
    r = make_runner(cfg)
    assert isinstance(r, ClaudeRunner) and str(r.client.base_url).rstrip("/") == PROXY
    cfg.backend, cfg.base_url = "ollama", "http://127.0.0.1:11434/v1"   # changed after loading: still the proxy
    r = make_runner(cfg)
    assert isinstance(r, ClaudeRunner) and r.client.api_key == "user-token"


def test_own_ai_settings_offer_the_key_providers_and_send_the_key_to_the_proxy(oenv):
    c, cfg, saved = oenv["client"], oenv["cfg"], oenv["saved"]
    a = c.get("/api/ai", headers=H).json()
    assert a["own_ai"] and not a.get("hosted") and a["env_file"] == ""
    assert [p["id"] for p in a["providers"]] == list(hosted.OWN_BACKENDS) and not any(p["key_set"] for p in a["providers"])
    stages = {s: {"model": "gpt-6", "effort": "medium"} for s in a["stages"]}
    r = c.put("/api/ai", headers=H, json={"backend": "openai", "stages": stages, "key": "sk-openai-mine"})
    assert r.status_code == 200, r.text
    assert saved == {"openai": ("sk-openai-mine", "")} and r.json()["providers"][1]["key_set"]
    assert not (config.PROFILE / ".env").exists() or "sk-openai-mine" not in (config.PROFILE / ".env").read_text()
    assert (cfg.backend, cfg.base_url, cfg.writer_model.model) == ("openai", PROXY + "/own/openai", "gpt-6")
    r = c.put("/api/ai", headers=H, json={"backend": "openai-compatible", "base_url": "https://my-llm.example/v1",
                                          "stages": stages, "key": "k2"})
    assert r.status_code == 200 and saved["openai-compatible"] == ("k2", "https://my-llm.example/v1")
    assert cfg.base_url == PROXY + "/own/openai-compatible"
    for local in ("ollama", "lmstudio", "claude-code"):
        r = c.put("/api/ai", headers=H, json={"backend": local, "base_url": "http://127.0.0.1:11434/v1", "stages": stages})
        assert r.status_code == 403, local
    assert c.get("/api/keys", headers=H).status_code == 403          # the other services' keys stay the website's
    m = c.get("/api/ai/chat-models", headers=H).json()
    assert m["default"]["backend"] == "openai-compatible" and m["groups"][0]["models"] == ["gpt-6"]
    r = c.post("/api/jobs/j1/edit", headers=H, json={"instruction": "What's weakest?", "model": "gpt-6"})
    assert r.status_code == 200
