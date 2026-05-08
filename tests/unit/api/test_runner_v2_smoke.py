"""runner_v2 端到端冒烟测试。

证明 agent.loop + agent.state + middleware_loop 串成的新链路能跑通：
  1. 简单 chat：用户问问题 → LLM 出最终答案 → channel 收到 token + metrics
  2. 工具调用 + DateReminder middleware：用户问问题 → DateReminder 注入日期 →
     LLM emit tool_call → 工具执行 → LLM 看结果出最终答案
  3. 持久化：跑完 ThreadStore.load_messages(thread_id) 能拿到本轮 user + ai

mock LLM stream chunks 跟 test_loop.py 共享造法。
"""
import json
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage, HumanMessage

from agent.loop import AgentConfig, ToolSpec
from agent.middleware_loop import DateReminder
from agent.middleware_loop.date_reminder import _REMINDER_MARKER
from agent.state import ThreadStore
from api.channel.runner_v2 import AgentRunnerV2


# ============================================================
# Fake stream / OpenAI client（精简版，从 test_loop.py 共享思路）
# ============================================================


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


def _mk_tc_delta(*, index, id=None, name=None, arguments=None):
    tc = MagicMock()
    tc.index = index
    tc.id = id
    fn = MagicMock()
    fn.name = name
    fn.arguments = arguments
    tc.function = fn if (name or arguments) else None
    return tc


class _FakeStream:
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for c in self.chunks:
            yield c


def _make_fake_openai_client(stream_chunks_per_call):
    client = MagicMock()
    client.chat = MagicMock()
    call_idx = [0]

    async def fake_create(**kwargs):
        idx = call_idx[0]
        call_idx[0] += 1
        return _FakeStream(stream_chunks_per_call[idx])

    client.chat.completions = MagicMock()
    client.chat.completions.create = fake_create
    return client


# ============================================================
# Fake Channel
# ============================================================


class _FakeChannel:
    """收集所有 send_* 调用为 (kind, args) 列表，便于断言。"""
    def __init__(self):
        self.events = []  # list of (method, dict_of_args)

    async def send_token(self, token):
        self.events.append(("token", {"token": token}))

    async def send_step(self, msg, type, subagent=None, skill_subcmd=None):
        self.events.append(("step", {"msg": msg, "type": type, "subagent": subagent, "skill_subcmd": skill_subcmd}))

    async def send_progress(self, message):
        self.events.append(("progress", {"message": message}))

    async def send_artifact_updated(self, artifact_id, action):
        self.events.append(("artifact_updated", {"artifact_id": artifact_id, "action": action}))

    async def send_reasoning(self, content):
        self.events.append(("reasoning", {"content": content}))

    async def send_metrics(self, metrics):
        self.events.append(("metrics", metrics))

    async def wait_for_confirm(self, message, preview, *, tool_name="", risk_level="medium"):
        # 默认 approve；测试想测 reject 时各 case 可重写
        return True, False

    async def close(self):
        pass


# ============================================================
# Tests
# ============================================================


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ThreadStore(tmp_path / "test.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_simple_chat_streams_token_and_persists(store):
    """最简：用户问 → LLM 直接答 → channel 收到 token + metrics + ThreadStore 持久化。"""
    chunks = [
        _mk_chunk(content="你好"),
        _mk_chunk(content="，世界"),
        _mk_chunk(finish_reason="stop"),
        _mk_chunk(usage={"input_tokens": 50, "output_tokens": 10, "total_tokens": 60}),
    ]
    fake_client = _make_fake_openai_client([chunks])

    config = AgentConfig(model="test-model", system_prompt="你是助手", recursion_limit=5)

    # 用 monkey-patch 把 AgentLoop 的 client 替换成假的
    from agent.loop import AgentLoop
    orig_get_client = AgentLoop._get_client
    AgentLoop._get_client = lambda self: fake_client
    try:
        runner = AgentRunnerV2(store)
        channel = _FakeChannel()

        await runner.run(
            channel=channel,
            thread_id="t1",
            user_message="你好啊",
            loop_config=config,
            tools=[],
            middlewares=[],
        )

        # token 收到了
        tokens = [e[1]["token"] for e in channel.events if e[0] == "token"]
        assert tokens == ["你好", "，世界"]

        # metrics 收到了
        metrics_events = [e for e in channel.events if e[0] == "metrics"]
        assert len(metrics_events) == 1
        assert metrics_events[0][1]["input_tokens"] == 50

        # ThreadStore 持久化了 user + ai
        loaded = await store.load_messages("t1")
        assert any(isinstance(m, HumanMessage) and m.content == "你好啊" for m in loaded)
        assert any(isinstance(m, AIMessage) and "你好" in m.content for m in loaded)
    finally:
        AgentLoop._get_client = orig_get_client


@pytest.mark.asyncio
async def test_tool_call_with_date_reminder_middleware(store):
    """中等：DateReminder middleware 注入 + 工具调用 → tool_start/end 事件 + 最终 metrics。"""
    # call 1: LLM emit tool_call
    call1 = [
        _mk_chunk(tool_calls=[_mk_tc_delta(index=0, id="c1", name="echo_tool", arguments='{"msg":"hi"}')]),
        _mk_chunk(finish_reason="tool_calls"),
        _mk_chunk(usage={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}),
    ]
    # call 2: 看到结果给最终答案
    call2 = [
        _mk_chunk(content="搞定"),
        _mk_chunk(finish_reason="stop"),
        _mk_chunk(usage={"input_tokens": 130, "output_tokens": 5, "total_tokens": 135}),
    ]
    fake_client = _make_fake_openai_client([call1, call2])

    async def echo_fn(args):
        return f"echoed: {args.get('msg', '')}"

    tool = ToolSpec(
        name="echo_tool",
        func=echo_fn,
        schema={"name": "echo_tool", "parameters": {"type": "object", "properties": {"msg": {"type": "string"}}}},
    )

    config = AgentConfig(model="test", system_prompt="你是助手", recursion_limit=10)

    from agent.loop import AgentLoop
    orig = AgentLoop._get_client
    AgentLoop._get_client = lambda self: fake_client
    try:
        runner = AgentRunnerV2(store)
        channel = _FakeChannel()

        await runner.run(
            channel=channel,
            thread_id="t2",
            user_message="跑 echo",
            loop_config=config,
            tools=[tool],
            middlewares=[DateReminder()],
        )

        # tool_start + tool_end 都来过
        steps = [e for e in channel.events if e[0] == "step"]
        types = [s[1]["type"] for s in steps]
        assert "tool_start" in types
        assert "tool_end" in types

        # metrics 两轮调用都来了
        metrics_events = [e for e in channel.events if e[0] == "metrics"]
        assert len(metrics_events) == 2

        # 最终 token "搞定" 来了
        tokens = "".join(e[1]["token"] for e in channel.events if e[0] == "token")
        assert "搞定" in tokens

        # ThreadStore 持久化
        loaded = await store.load_messages("t2")
        assert any(isinstance(m, HumanMessage) and m.content == "跑 echo" for m in loaded)
    finally:
        AgentLoop._get_client = orig


@pytest.mark.asyncio
async def test_recursion_limit_yields_warning_token(store):
    """LLM 死循环 emit tool_call → 撞 recursion_limit → channel 收到警告 token。"""
    # 每次都 emit tool_call 不收敛
    perpetual = [
        _mk_chunk(tool_calls=[_mk_tc_delta(index=0, id="loop", name="noop_tool", arguments='{}')]),
        _mk_chunk(finish_reason="tool_calls"),
    ]
    fake_client = _make_fake_openai_client([perpetual] * 10)

    async def noop(args):
        return "ok"

    tool = ToolSpec(name="noop_tool", func=noop, schema={"name": "noop_tool", "parameters": {}})
    config = AgentConfig(model="test", system_prompt="you", recursion_limit=2)

    from agent.loop import AgentLoop
    orig = AgentLoop._get_client
    AgentLoop._get_client = lambda self: fake_client
    try:
        runner = AgentRunnerV2(store)
        channel = _FakeChannel()
        await runner.run(
            channel=channel,
            thread_id="t3",
            user_message="loop",
            loop_config=config,
            tools=[tool],
            middlewares=[],
        )

        # 警告 token 到了
        tokens = "".join(e[1]["token"] for e in channel.events if e[0] == "token")
        assert "工具调用上限" in tokens or "recursion" in tokens.lower()
    finally:
        AgentLoop._get_client = orig


@pytest.mark.asyncio
async def test_tool_messages_persisted_to_thread_store(store):
    """工具调用产生的 ToolMessage 也要落库（之前 P3c 骨架只 save user+final ai 是 bug）。"""
    call1 = [
        _mk_chunk(tool_calls=[_mk_tc_delta(index=0, id="c1", name="my_tool", arguments='{"x":1}')]),
        _mk_chunk(finish_reason="tool_calls"),
    ]
    call2 = [
        _mk_chunk(content="完成"),
        _mk_chunk(finish_reason="stop"),
    ]
    fake_client = _make_fake_openai_client([call1, call2])

    async def my_tool(args):
        return f"tool got x={args['x']}"

    tool = ToolSpec(name="my_tool", func=my_tool, schema={"name": "my_tool", "parameters": {}})
    config = AgentConfig(model="test", system_prompt="sys", recursion_limit=5)

    from agent.loop import AgentLoop
    from langchain_core.messages import ToolMessage
    orig = AgentLoop._get_client
    AgentLoop._get_client = lambda self: fake_client
    try:
        runner = AgentRunnerV2(store)
        ch = _FakeChannel()
        await runner.run(
            channel=ch, thread_id="tm_t",
            user_message="跑工具",
            loop_config=config, tools=[tool], middlewares=[],
        )

        loaded = await store.load_messages("tm_t")
        # 必须含 ToolMessage（之前 P3c 骨架版本会漏）
        tool_messages = [m for m in loaded if isinstance(m, ToolMessage)]
        assert len(tool_messages) == 1, f"ToolMessage 没落库: loaded={[type(m).__name__ for m in loaded]}"
        assert "tool got x=1" in tool_messages[0].content
    finally:
        AgentLoop._get_client = orig


@pytest.mark.asyncio
async def test_history_loaded_across_runs(store):
    """跑完一轮 → 第二次 run 能拿到上轮 history（thread persistence 验证）。"""
    chunks_round1 = [
        _mk_chunk(content="第一次回答"),
        _mk_chunk(finish_reason="stop"),
    ]
    chunks_round2 = [
        _mk_chunk(content="第二次回答"),
        _mk_chunk(finish_reason="stop"),
    ]
    fake_client = _make_fake_openai_client([chunks_round1, chunks_round2])

    config = AgentConfig(model="test", system_prompt="你是助手", recursion_limit=3)

    from agent.loop import AgentLoop
    orig = AgentLoop._get_client
    AgentLoop._get_client = lambda self: fake_client
    try:
        runner = AgentRunnerV2(store)
        ch1 = _FakeChannel()
        await runner.run(
            channel=ch1,
            thread_id="conv_1",
            user_message="问题1",
            loop_config=config,
            tools=[],
            middlewares=[],
        )
        ch2 = _FakeChannel()
        await runner.run(
            channel=ch2,
            thread_id="conv_1",  # 同一 thread
            user_message="问题2",
            loop_config=config,
            tools=[],
            middlewares=[],
        )

        loaded = await store.load_messages("conv_1")
        # 4 条：q1 / a1 / q2 / a2
        assert len(loaded) == 4
        contents = [m.content for m in loaded]
        assert "问题1" in contents
        assert "第一次回答" in contents
        assert "问题2" in contents
        assert "第二次回答" in contents
    finally:
        AgentLoop._get_client = orig
