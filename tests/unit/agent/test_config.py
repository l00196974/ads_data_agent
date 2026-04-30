# tests/test_config.py
import os
import pytest
from agent.config import load_config, AppConfig


def test_load_config_defaults():
    cfg = load_config("config.yaml")
    assert cfg.server.port == 8000
    assert cfg.agent.max_iterations == 20
    # 当前业务定位为纯分析场景（无写操作），interrupt_on 默认为空列表。
    # 业务扩展到写工具时按 LangChain tool name 在 yaml 启用，框架启动时会校验。
    assert cfg.agent.interrupt_on == []


def test_env_override(monkeypatch):
    # 隔离 dotenv 加载到 os.environ 的 LLM_API_KEY，否则它优先级更高会盖掉断言
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = load_config("config.yaml")
    assert cfg.llm.api_key == "test-key"


def test_llm_api_key_priority(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-key")
    monkeypatch.setenv("LLM_API_KEY", "generic-key")
    cfg = load_config("config.yaml")
    assert cfg.llm.api_key == "generic-key"


def test_base_url_env(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "deepseek-chat")
    cfg = load_config("config.yaml")
    assert cfg.llm.base_url == "https://api.deepseek.com/v1"
    assert cfg.llm.provider == "openai"
    assert cfg.llm.model == "deepseek-chat"
