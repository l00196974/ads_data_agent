"""subagent.make_task_tool 契约测试。

锁住：
  1. subagents 空 → 返 None
  2. 返回的 ToolSpec name='task' / schema 含 subagent_type enum
  3. 调 task(subagent_type=不存在的) → 返 Error 字符串不抛
  4. 调 task() spawn 子 loop 真跑（mock LLM）→ 返 final_message 内容
  5. 子 loop 工具事件转发到 channel 带 subagent= 标记
  6. 子 loop tools 不含 task 自身（不能套娃）
  7. 子 loop 撞 recursion_limit 返友好错误字符串
"""
from unittest.mock import MagicMock

import pytest
from agent.messages import HumanMessage

from agent.config import SubAgentSpec
from agent.loop import AgentConfig, AgentLoop, ToolSpec
from agent.subagent import make_task_tool


def _mk_chunk(*, content=None, tool_calls=None, finish_reason=None, usage=None):
    chunk = MagicMock()
    if usage is not None:
        chunk.usage = MagicMock(
            prompt_tokens=usage.get("input_tokens", 0),
            completion_tokens=usage.get("output_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        )
        chunk.choices = []
        return chunk
    delta = MagicMock()
    delta.content = content
    delta.tool_calls = tool_calls or None
    choice = MagicMock()
    choice.delta = delta
    choice.finish_reason = finish_reason
    chunk.choices = [choice]
    chunk.usage = None
    return chunk


class _FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for c in self.chunks:
            yield c


def _make_fake_client(stream_chunks_per_call):
    client = MagicMock()
    client.chat = MagicMock()
    call_idx = [0]

    async def fake_create(**kw):
        idx = call_idx[0]
        call_idx[0] += 1
        return _FakeStream(stream_chunks_per_call[idx])

    client.chat.completions = MagicMock()
    client.chat.completions.create = fake_create
    return client


class _FakeChannel:
    def __init__(self):
        self.events = []

    async def send_step(self, msg, type, subagent=None, skill_subcmd=None):
        self.events.append(("step", msg, type, subagent, skill_subcmd))

    async def send_token(self, t): pass
    async def send_progress(self, m): pass
    async def send_metrics(self, m): pass
    async def send_artifact_updated(self, *a, **kw): pass
    async def send_reasoning(self, c): pass
    async def wait_for_confirm(self, *a, **kw): return True, False
    async def close(self): pass


def _base_config():
    return AgentConfig(model="test", system_prompt="主 sys", recursion_limit=10)


def test_empty_subagents_returns_none():
    tool = make_task_tool(
        subagents=[],
        base_loop_config=_base_config(),
        skill_tools=[],
        channel=_FakeChannel(),
        thread_id="t",
    )
    assert tool is None


def test_task_tool_schema_has_subagent_type_enum():
    subs = [
        SubAgentSpec(name="data-fetcher", description="d", system_prompt="p1"),
        SubAgentSpec(name="data-analyst", description="d", system_prompt="p2"),
    ]
    tool = make_task_tool(
        subagents=subs,
        base_loop_config=_base_config(),
        skill_tools=[],
        channel=_FakeChannel(),
        thread_id="t",
    )
    assert tool is not None
    assert tool.name == "task"
    enum = tool.schema["parameters"]["properties"]["subagent_type"]["enum"]
    assert enum == ["data-fetcher", "data-analyst"]


@pytest.mark.asyncio
async def test_task_unknown_subagent_returns_error_string():
    subs = [SubAgentSpec(name="data-fetcher", description="d", system_prompt="p")]
    tool = make_task_tool(
        subagents=subs,
        base_loop_config=_base_config(),
        skill_tools=[],
        channel=_FakeChannel(),
        thread_id="t",
    )
    out = await tool.func({"subagent_type": "nonexistent", "description": "x"})
    assert "Error" in out and "未注册" in out


@pytest.mark.asyncio
async def test_task_spawns_subagent_returns_final_message():
    """task() 调用 spawn 子 loop 真跑——mock LLM 直接答 → 返回字符串给主 Agent。"""
    subs = [SubAgentSpec(name="data-analyst", description="d", system_prompt="你是分析师")]

    sub_chunks = [
        _mk_chunk(content="子 Agent 分析结果"),
        _mk_chunk(finish_reason="stop"),
    ]
    fake_client = _make_fake_client([sub_chunks])

    orig = AgentLoop._get_client
    AgentLoop._get_client = lambda self: fake_client
    try:
        tool = make_task_tool(
            subagents=subs,
            base_loop_config=_base_config(),
            skill_tools=[],
            channel=_FakeChannel(),
            thread_id="t",
        )
        result = await tool.func({"subagent_type": "data-analyst", "description": "分析数据"})
        assert "子 Agent 分析结果" in result
    finally:
        AgentLoop._get_client = orig


@pytest.mark.asyncio
async def test_task_forwards_subagent_tool_events_to_channel():
    """子 loop 的 tool_start 事件转发到 channel 带 subagent= 标记。"""
    subs = [SubAgentSpec(name="data-fetcher", description="d", system_prompt="你取数")]

    # 子 loop: emit tool_call → tool_end → final answer
    sub_call1 = [
        _mk_chunk(tool_calls=[_make_tc_delta(0, "c1", "run_command", '{"command":"query-x"}')]),
        _mk_chunk(finish_reason="tool_calls"),
    ]
    sub_call2 = [
        _mk_chunk(content="拿到了"),
        _mk_chunk(finish_reason="stop"),
    ]
    fake_client = _make_fake_client([sub_call1, sub_call2])

    async def fake_skill(args):
        return f"result for {args.get('command')}"

    skill_tool = ToolSpec(
        name="run_command",
        func=fake_skill,
        schema={"name": "run_command", "parameters": {}},
    )

    orig = AgentLoop._get_client
    AgentLoop._get_client = lambda self: fake_client
    try:
        channel = _FakeChannel()
        tool = make_task_tool(
            subagents=subs,
            base_loop_config=_base_config(),
            skill_tools=[skill_tool],
            channel=channel,
            thread_id="main_t",
        )
        result = await tool.func({"subagent_type": "data-fetcher", "description": "拉数据"})
        assert "拿到了" in result

        # 验证 channel 收到子 Agent 的 tool_start/tool_end + subagent 标记
        steps = [e for e in channel.events if e[0] == "step"]
        assert any(s[2] == "tool_start" and s[3] == "data-fetcher" for s in steps)
        assert any(s[2] == "tool_end" and s[3] == "data-fetcher" for s in steps)
        # skill_subcmd 应该正确解析
        tool_starts = [s for s in steps if s[2] == "tool_start"]
        assert tool_starts[0][4] == "query-x"  # skill_subcmd 字段
    finally:
        AgentLoop._get_client = orig


def _make_tc_delta(index, id, name, arguments):
    """造一个 streaming tool_call delta。"""
    tc = MagicMock()
    tc.index = index
    tc.id = id
    fn = MagicMock()
    fn.name = name
    fn.arguments = arguments
    tc.function = fn
    return tc


@pytest.mark.asyncio
async def test_task_subagent_recursion_limit_returns_friendly_error():
    """子 loop 撞 recursion_limit → 返"步数超限"友好提示。"""
    subs = [SubAgentSpec(name="data-fetcher", description="d", system_prompt="你取数")]

    # 死循环
    perpetual = [
        _mk_chunk(tool_calls=[_make_tc_delta(0, "c1", "run_command", '{"command":"x"}')]),
        _mk_chunk(finish_reason="tool_calls"),
    ]
    fake_client = _make_fake_client([perpetual] * 50)

    async def fake_skill(args):
        return "ok"

    skill_tool = ToolSpec(name="run_command", func=fake_skill, schema={"name": "run_command", "parameters": {}})

    orig = AgentLoop._get_client
    AgentLoop._get_client = lambda self: fake_client
    try:
        tool = make_task_tool(
            subagents=subs,
            base_loop_config=_base_config(),
            skill_tools=[skill_tool],
            channel=_FakeChannel(),
            thread_id="t",
        )
        result = await tool.func({"subagent_type": "data-fetcher", "description": "无限拉数据"})
        assert "步数超限" in result or "recursion" in result.lower()
    finally:
        AgentLoop._get_client = orig
