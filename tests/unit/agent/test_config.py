# tests/test_config.py
import os
import pytest
from agent.config import load_config, AppConfig


def test_load_config_defaults():
    cfg = load_config("config.yaml")
    assert cfg.server.port == 8000
    assert cfg.agent.max_iterations == 20
    # 默认 high_risk 空（还没真实写广告 skill）；medium_risk 含工作区写文件——
    # write_file / edit_file 是 deepagents FilesystemMiddleware 的工具，agent
    # 写用户工作区时触发 HitL 弹窗，用户可设"以后不再确认"。
    # 加新真实业务工具（create-audience / write-report-artifact 等）时按需扩展。
    assert cfg.agent.interrupt_on.high_risk == []
    assert "write_file" in cfg.agent.interrupt_on.medium_risk
    assert "edit_file" in cfg.agent.interrupt_on.medium_risk


def test_interrupt_config_accepts_legacy_list():
    """旧 config 形式 `interrupt_on: [tool1, tool2]` 必须仍然接受——
    自动归到 medium_risk 保持原"敏感但默认可放行"语义。"""
    from agent.config import InterruptConfig
    legacy = InterruptConfig.model_validate(["tool_a", "tool_b"])
    assert legacy.high_risk == []
    assert legacy.medium_risk == ["tool_a", "tool_b"]
    assert legacy.classify("tool_a") == "medium"
    assert legacy.classify("never_seen") == "none"


def test_interrupt_config_classifies_risk():
    from agent.config import InterruptConfig
    cfg = InterruptConfig(high_risk=["delete-adgroup"], medium_risk=["create-audience"])
    assert cfg.classify("delete-adgroup") == "high"
    assert cfg.classify("create-audience") == "medium"
    assert cfg.classify("query-metrics") == "none"
    assert sorted(cfg.all_intercepted()) == ["create-audience", "delete-adgroup"]


def test_env_override(monkeypatch):
    """LLM_API_KEY 环境变量必须被 LLMConfig 接收。"""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    cfg = load_config("config.yaml")
    assert cfg.llm.api_key == "test-key"


def test_llm_api_key_no_silent_fallback(monkeypatch):
    """删除 ANTHROPIC_API_KEY 静默回退后，仅 LLM_API_KEY 起作用——其他 provider
    特定的 env 不该再被读取（避免老用户配 ANTHROPIC_API_KEY 误以为生效）。"""
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "should-be-ignored")
    cfg = load_config("config.yaml")
    assert cfg.llm.api_key == ""


def test_base_url_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    cfg = load_config("config.yaml")
    assert cfg.llm.base_url == "https://api.deepseek.com/v1"
    assert cfg.llm.provider == "openai"
    assert cfg.llm.model == "deepseek-chat"
