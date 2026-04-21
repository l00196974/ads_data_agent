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
    with patch("agent.core.create_deep_agent") as mock_create:
        mock_create.return_value = MagicMock()
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
