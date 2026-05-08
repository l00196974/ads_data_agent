"""chat.py 的 v2 装配 helper（_start_v2_agent_run + _get_v2_store）单测。

不打真 LLM——验证：
  1. cfg.agent.runner_mode 默认 "v1"
  2. _get_v2_store 返回 ThreadStore 单例（多次调返回同实例）
  3. _start_v2_agent_run 装配出来的 task 不抛、参数正确传递（用 mock LLM client）
  4. v2 task 跑完 ThreadStore 里能看到本轮 messages

不验证 chat() 完整 endpoint 流程（那是集成测试，HTTP 层）——只验证 v2 装配代码
正确把 cfg / SkillsPackage / middleware 串到 AgentRunnerV2。
"""
import asyncio
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from agent.config import load_config


def test_default_runner_mode_v1():
    """config.yaml::agent.runner_mode 默认 v1（不破坏现有用户）。"""
    cfg = load_config()
    assert cfg.agent.runner_mode == "v1"


@pytest.mark.asyncio
async def test_v2_store_singleton(tmp_path, monkeypatch):
    """_get_v2_store 多次调返回同一 ThreadStore 实例。"""
    import api.chat as chat_module

    # 备份并清空缓存
    original_cache = dict(chat_module._v2_store_cache)
    chat_module._v2_store_cache.clear()
    # 重定向 data_dir 到 tmp_path
    chat_module.cfg.persistence.data_dir = str(tmp_path)
    try:
        store1 = await chat_module._get_v2_store()
        store2 = await chat_module._get_v2_store()
        assert store1 is store2
        await store1.close()
    finally:
        chat_module._v2_store_cache.clear()
        chat_module._v2_store_cache.update(original_cache)


@pytest.mark.asyncio
async def test_start_v2_agent_run_assembles_correctly(tmp_path, monkeypatch):
    """_start_v2_agent_run 真起 task 不抛，且 mock LLM client 能被 loop 用上。"""
    import api.chat as chat_module
    from agent.loop import AgentLoop

    # tmp 数据目录隔离测试
    chat_module._v2_store_cache.clear()
    original_data_dir = chat_module.cfg.persistence.data_dir
    chat_module.cfg.persistence.data_dir = str(tmp_path)
    # tmp skills 目录（空目录，无 SKILL.md，SkillsPackage.tools 是 []）
    skill_dir = tmp_path / "skills"
    skill_dir.mkdir()
    original_skills_dir = chat_module.cfg.skills.md_dir
    chat_module.cfg.skills.md_dir = str(skill_dir)

    # mock LLM client：吐 "你好" 然后 stop
    fake_chunk_content = MagicMock()
    fake_chunk_content.choices = [MagicMock()]
    fake_chunk_content.choices[0].delta = MagicMock()
    fake_chunk_content.choices[0].delta.content = "你好"
    fake_chunk_content.choices[0].delta.tool_calls = None
    fake_chunk_content.choices[0].finish_reason = None
    fake_chunk_content.usage = None

    fake_chunk_stop = MagicMock()
    fake_chunk_stop.choices = [MagicMock()]
    fake_chunk_stop.choices[0].delta = MagicMock()
    fake_chunk_stop.choices[0].delta.content = None
    fake_chunk_stop.choices[0].delta.tool_calls = None
    fake_chunk_stop.choices[0].finish_reason = "stop"
    fake_chunk_stop.usage = None

    class FakeStream:
        def __aiter__(self):
            return self._iter()
        async def _iter(self):
            yield fake_chunk_content
            yield fake_chunk_stop

    fake_client = MagicMock()
    fake_client.chat = MagicMock()
    async def fake_create(**kw):
        return FakeStream()
    fake_client.chat.completions = MagicMock()
    fake_client.chat.completions.create = fake_create

    orig_get_client = AgentLoop._get_client
    AgentLoop._get_client = lambda self: fake_client

    # mock channel
    class FakeChannel:
        def __init__(self):
            self.events = []
        async def send_token(self, t): self.events.append(("token", t))
        async def send_step(self, *a, **kw): pass
        async def send_progress(self, m): pass
        async def send_artifact_updated(self, *a, **kw): pass
        async def send_reasoning(self, c): pass
        async def send_metrics(self, m): pass
        async def wait_for_confirm(self, *a, **kw): return True, False
        async def close(self): pass

    channel = FakeChannel()
    try:
        task = await chat_module._start_v2_agent_run(
            channel, "test_user", "test_conv", "你好啊"
        )
        await asyncio.wait_for(task, timeout=5.0)

        # 验证 LLM 真被调（fake stream 吐"你好"）
        tokens = [e[1] for e in channel.events if e[0] == "token"]
        assert tokens == ["你好"]

        # 验证 ThreadStore 持久化（thread_id = test_user_test_conv）
        store = await chat_module._get_v2_store()
        msgs = await store.load_messages("test_user_test_conv")
        # 至少含 user 输入 + ai 回答（DateReminder 注入的 reminder 也在内）
        assert any(getattr(m, "content", "") == "你好啊" for m in msgs)
        assert any("你好" == getattr(m, "content", "") for m in msgs)
    finally:
        AgentLoop._get_client = orig_get_client
        # 关闭 store 再清缓存（避免 db 连接残留影响其他测试）
        if "store" in chat_module._v2_store_cache:
            await chat_module._v2_store_cache["store"].close()
        chat_module._v2_store_cache.clear()
        chat_module.cfg.persistence.data_dir = original_data_dir
        chat_module.cfg.skills.md_dir = original_skills_dir
