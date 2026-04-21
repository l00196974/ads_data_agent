# tests/test_config.py
import os
import pytest
from agent.config import load_config, AppConfig


def test_load_config_defaults():
    cfg = load_config("config.yaml")
    assert cfg.server.port == 8000
    assert cfg.agent.max_iterations == 20
    assert "delete_adgroups" in cfg.agent.interrupt_on


def test_env_override(monkeypatch):
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
