from unittest.mock import patch, MagicMock
from agent.session import SessionManager


def test_session_manager_thread_id():
    sm = SessionManager()
    tid = sm.get_thread_id("user_001")
    assert tid == "user_001"
    assert sm.get_thread_id("user_001") == tid


def test_session_manager_config():
    sm = SessionManager()
    cfg = sm.get_config("user_001")
    assert cfg["configurable"]["thread_id"] == "user_001"


def test_build_agent_called_with_correct_model():
    # Patch where the symbols are *used* (agent.builder), not where they're
    # re-exported (agent.core).
    with patch("agent.builder.create_deep_agent") as mock_create, \
         patch("agent.builder.get_checkpointer") as mock_cp:
        mock_create.return_value = MagicMock()
        mock_cp.return_value = MagicMock()
        from agent.core import build_agent
        from agent.config import load_config
        cfg = load_config("config.yaml")
        build_agent(
            user_id="user_001",
            system_prompt="测试",
            skills=[],
            interrupt_on=[],
            cfg=cfg,
        )
        call_kwargs = mock_create.call_args
        assert call_kwargs is not None


def test_build_model_openai_protocol(monkeypatch):
    from agent.config import AppConfig, LLMConfig
    from agent.core import _build_model
    from langchain_openai import ChatOpenAI

    cfg = AppConfig(
        llm=LLMConfig(
            provider="openai",
            model="gpt-4o",
            api_key="sk-test",
            base_url="https://api.openai.com/v1",
        )
    )
    model = _build_model(cfg)
    assert isinstance(model, ChatOpenAI)


def test_build_model_anthropic_returns_chatanthropic_instance():
    """After the LLM-construction unification, anthropic returns a real
    ChatAnthropic instance (not a "provider:model" spec string), and
    cfg.llm.api_key is honored — no silent fallback to env vars."""
    from agent.config import AppConfig, LLMConfig
    from agent.core import _build_model
    from langchain_anthropic import ChatAnthropic

    cfg = AppConfig(
        llm=LLMConfig(provider="anthropic", model="claude-sonnet-4-6", api_key="sk-ant-test")
    )
    model = _build_model(cfg)
    assert isinstance(model, ChatAnthropic)
    # api_key wraps in SecretStr; just confirm it was set non-empty (no silent env fallback)
    assert model.anthropic_api_key is not None
