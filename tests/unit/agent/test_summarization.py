"""Summarization middleware 测试。

锁住：
  1. tokens 低于阈值不触发
  2. 已 summary 过的不二次触发（marker 检测）
  3. messages <= keep_messages 不压（没东西可压）
  4. 触发后调 LLM 生成 summary，替换早期 messages，保留 keep_messages
  5. LLM 调用失败时不崩，跳过 summary
  6. summary 是 SystemMessage 含 marker
"""
from unittest.mock import MagicMock, AsyncMock

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from agent.loop import AgentState
from agent.middleware.summarization import Summarization, _SUMMARY_MARKER


def _make_long_messages(count: int, content_size: int = 5000) -> list:
    """造 count 条 messages，每条 content 5000 字符——很容易撞 trigger_tokens=10000。"""
    msgs = []
    for i in range(count):
        if i % 2 == 0:
            msgs.append(HumanMessage(content="x" * content_size))
        else:
            msgs.append(AIMessage(content="y" * content_size))
    return msgs


def _make_fake_client(summary_text: str):
    """造个返回固定 summary 的 fake AsyncOpenAI。"""
    client = MagicMock()
    client.chat = MagicMock()

    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message = MagicMock()
    response.choices[0].message.content = summary_text

    async def fake_create(**kw):
        return response

    client.chat.completions = MagicMock()
    client.chat.completions.create = fake_create
    return client


@pytest.mark.asyncio
async def test_below_trigger_no_op():
    """messages 总 token 低于 trigger_tokens 不触发。"""
    client = _make_fake_client("summary")
    mw = Summarization(openai_client=client, model="test", trigger_tokens=100000, keep_messages=5)
    msgs = _make_long_messages(2, content_size=100)  # ~100 tokens
    result = await mw.before_model(AgentState(messages=msgs, thread_id="t"))
    assert result is None


@pytest.mark.asyncio
async def test_already_summarized_no_op():
    """messages 里有 [CONVERSATION-SUMMARY] marker → 跳过（不重复 summary）。"""
    client = _make_fake_client("summary")
    mw = Summarization(openai_client=client, model="test", trigger_tokens=100, keep_messages=2)
    # 含 marker 的 system + 一堆其它 messages，总 tokens 远超 trigger
    msgs = [SystemMessage(content=f"{_SUMMARY_MARKER} prior summary")] + _make_long_messages(20, content_size=5000)
    result = await mw.before_model(AgentState(messages=msgs, thread_id="t"))
    assert result is None


@pytest.mark.asyncio
async def test_too_few_messages_no_op():
    """messages <= keep_messages 没什么可压。"""
    client = _make_fake_client("summary")
    mw = Summarization(openai_client=client, model="test", trigger_tokens=10, keep_messages=10)
    msgs = _make_long_messages(8, content_size=10000)  # 总 tokens 巨大但 < keep
    result = await mw.before_model(AgentState(messages=msgs, thread_id="t"))
    assert result is None


@pytest.mark.asyncio
async def test_triggered_replaces_old_with_summary():
    """超阈值且 messages 足够多时 → LLM 调用 + 替换早期 + 留 keep_messages。"""
    client = _make_fake_client("# 对话摘要\n\n用户问点击量，我答了 1234")
    mw = Summarization(openai_client=client, model="test", trigger_tokens=100, keep_messages=3)
    msgs = _make_long_messages(20, content_size=200)  # 20 条 × 200 = ~2000 tokens
    state = AgentState(messages=msgs, thread_id="t")
    result = await mw.before_model(state)

    assert result is not None
    new_messages = result.messages
    # 替换后：1 条 summary system + keep_messages 条最近的
    assert len(new_messages) == 1 + 3
    assert isinstance(new_messages[0], SystemMessage)
    assert _SUMMARY_MARKER in new_messages[0].content
    assert "对话摘要" in new_messages[0].content
    # 最近 3 条原文应保留，是原 messages 的最后 3 条
    assert new_messages[-3:] == msgs[-3:]


@pytest.mark.asyncio
async def test_llm_failure_skips_no_crash():
    """LLM 调用抛异常 → 不崩，返 None 跳过这次 summary。"""
    client = MagicMock()
    client.chat = MagicMock()

    async def failing_create(**kw):
        raise RuntimeError("LLM down")

    client.chat.completions = MagicMock()
    client.chat.completions.create = failing_create

    mw = Summarization(openai_client=client, model="test", trigger_tokens=100, keep_messages=3)
    msgs = _make_long_messages(10, content_size=500)
    result = await mw.before_model(AgentState(messages=msgs, thread_id="t"))
    assert result is None  # 失败 fallback 到不修改


@pytest.mark.asyncio
async def test_empty_summary_response_skips():
    """LLM 给空 summary → 不替换 state（防错误结果污染）。"""
    client = _make_fake_client("")  # 空 summary
    mw = Summarization(openai_client=client, model="test", trigger_tokens=100, keep_messages=3)
    msgs = _make_long_messages(10, content_size=500)
    result = await mw.before_model(AgentState(messages=msgs, thread_id="t"))
    assert result is None
