"""Hosted mode (OYSTER_HOSTED=1, docs/paid-service.md item 1.1): the AI is always the proxy, on allowed models, and
nothing that picks the AI or holds keys can be changed. With the switch off, nothing changes."""
import shutil

import pytest

from jobpipe import config, hosted
from jobpipe.llm import ClaudeCodeRunner, ClaudeRunner, OpenAICompatRunner
from jobpipe.pipeline import make_runner
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
