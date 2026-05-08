"""agent/loop.py 主循环单测。

不打真 LLM——用一个 mock AsyncOpenAI 假 client，按测试 case 编造流式响应。
覆盖：
  1. 单工具调用 → 拿到结果 → LLM 出最终答案（无 tool_calls）
  2. 多工具并行 emit（一轮 emit 多个 tool_calls，验证 asyncio.gather 真并行）
  3. 中间 middleware 注入 system message
  4. HitL on_interrupt 拒绝 → 把"用户拒绝"喂回 LLM
  5. HitL on_interrupt 通过 → tool 正常执行
  6. recursion_limit 撞墙
  7. 工具异常 → 喂回 LLM 错误消息不崩
  8. tool_calls JSON args 解析失败兜底
  9. 中间错误（LLM API 异常）→ yield error 事件
  10. 空 messages 跑通（只有 system prompt + user 一句）
  11. tool 调用按 wait_for 超时
  12. middleware 替换 messages（before_model 返回新 state）
"""
import asyncio
import json
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent.loop import (
    AgentConfig,
    AgentLoop,
    AgentState,
    Middleware,
    ToolCall,
    ToolSpec,
)


# ============================================================
# 假 OpenAI 流式响应构造工具
# ============================================================


def _make_chunk(*, content=None, tool_calls=None, finish_reason=None, usage=None):
    """造一个 mock 的 ChatCompletionChunk-like 对象。"""
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
    if tool_calls:
        delta.tool_calls = tool_calls
    else:
        delta.tool_calls = None

    choice = MagicMock()
    choice.delta = delta
    choice.finish_reason = finish_reason
    chunk.choices = [choice]
    chunk.usage = None
    return chunk


def _make_tc_delta(*, index, id=None, name=None, arguments=None):
    """造一个 streaming tool_call delta。"""
    tc = MagicMock()
    tc.index = index
    tc.id = id
    if name is not None or arguments is not None:
        fn = MagicMock()
        fn.name = name
        fn.arguments = arguments
        tc.function = fn
    else:
        tc.function = None
    return tc


class FakeStream:
    """async iterator 包装一组 chunks——模拟 openai 返回的 stream。"""
    def __init__(self, chunks):
        self.chunks = chunks

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for c in self.chunks:
            yield c


def _make_fake_client(stream_chunks_per_call: list[list[Any]]):
    """造一个假 AsyncOpenAI client：每次 chat.completions.create 返回下一组 stream chunks。

    stream_chunks_per_call: 每个元素是一组 chunks，对应该次 LLM 调用的流式响应
    """
    client = MagicMock()
    client.chat = MagicMock()
    call_idx = [0]

    async def fake_create(**kwargs):
        idx = call_idx[0]
        call_idx[0] += 1
        if idx >= len(stream_chunks_per_call):
            raise RuntimeError(f"假 client 没准备第 {idx + 1} 次调用的 stream")
        return FakeStream(stream_chunks_per_call[idx])

    client.chat.completions = MagicMock()
    client.chat.completions.create = fake_create
    return client


# ============================================================
# 工具：构造 mock loop（真实 AgentLoop 但 _client 替换成假的）
# ============================================================


def _make_loop(*, tools=None, middlewares=None, interrupt_tools=None, fake_client=None,
               recursion_limit=10, tool_timeout=5.0):
    cfg = AgentConfig(
        model="test-model",
        system_prompt="你是测试助手",
        recursion_limit=recursion_limit,
        tool_timeout_seconds=tool_timeout,
        interrupt_tools=interrupt_tools or {},
    )
    loop = AgentLoop(
        config=cfg,
        tools=tools or [],
        middlewares=middlewares or [],
    )
    if fake_client is not None:
        loop._client = fake_client
    return loop


# ============================================================
# Tests
# ============================================================


@pytest.mark.asyncio
async def test_single_tool_then_final_answer():
    """单工具 → 拿结果 → LLM 出最终答案。"""
    # call 1: LLM emit tool_call query_test({"q": "hi"})
    call1_chunks = [
        _make_chunk(tool_calls=[_make_tc_delta(index=0, id="call_1", name="query_test", arguments='{"q":"hi"}')]),
        _make_chunk(finish_reason="tool_calls"),
        _make_chunk(usage={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110}),
    ]
    # call 2: LLM 看到 tool 结果后给最终答案
    call2_chunks = [
        _make_chunk(content="结果是 42"),
        _make_chunk(finish_reason="stop"),
    ]
    client = _make_fake_client([call1_chunks, call2_chunks])

    async def query_test_fn(args):
        return f"result for {args['q']}: 42"

    tool = ToolSpec(
        name="query_test",
        func=query_test_fn,
        schema={"name": "query_test", "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}},
    )
    loop = _make_loop(tools=[tool], fake_client=client)

    events = []
    async for ev in loop.run("hi"):
        events.append(ev)

    types = [e["type"] for e in events]
    assert "model_start" in types
    assert "tool_start" in types
    assert "tool_end" in types
    assert "complete" in types
    final = next(e for e in events if e["type"] == "complete")
    assert "42" in final["final_message"].content


@pytest.mark.asyncio
async def test_multi_tool_calls_run_in_parallel():
    """LLM 一轮 emit 3 个 tool_calls → 应该 asyncio.gather 并行执行（总耗时 ≈ 单个，不是 3 倍）。"""
    call1_chunks = [
        _make_chunk(tool_calls=[
            _make_tc_delta(index=0, id="c1", name="slow_tool", arguments='{"i":1}'),
            _make_tc_delta(index=1, id="c2", name="slow_tool", arguments='{"i":2}'),
            _make_tc_delta(index=2, id="c3", name="slow_tool", arguments='{"i":3}'),
        ]),
        _make_chunk(finish_reason="tool_calls"),
    ]
    call2_chunks = [
        _make_chunk(content="all done"),
        _make_chunk(finish_reason="stop"),
    ]
    client = _make_fake_client([call1_chunks, call2_chunks])

    sleep_per_tool = 0.3

    async def slow_fn(args):
        await asyncio.sleep(sleep_per_tool)
        return f"done {args['i']}"

    tool = ToolSpec(name="slow_tool", func=slow_fn, schema={"name": "slow_tool", "parameters": {}})
    loop = _make_loop(tools=[tool], fake_client=client)

    import time as time_mod
    t0 = time_mod.perf_counter()
    events = []
    async for ev in loop.run("跑 3 个慢工具"):
        events.append(ev)
    elapsed = time_mod.perf_counter() - t0

    # 串行至少 0.9s（3 × 0.3），并行 ~0.3s+开销。给 2x 缓冲
    assert elapsed < sleep_per_tool * 2, f"工具未并行，elapsed={elapsed:.3f}s"
    assert sum(1 for e in events if e["type"] == "tool_end") == 3


@pytest.mark.asyncio
async def test_middleware_can_modify_messages():
    """middleware before_model 返回新 state → loop 用新 messages 调 LLM。"""

    class InjectSystemMW:
        def before_model(self, state):
            # 在 messages 头部注入一条 SystemMessage
            new_messages = [SystemMessage(content="[INJECTED]")] + state.messages
            return AgentState(
                messages=new_messages,
                iter_count=state.iter_count,
                thread_id=state.thread_id,
                extras=state.extras,
            )

    call1_chunks = [
        _make_chunk(content="ok"),
        _make_chunk(finish_reason="stop"),
    ]
    client = _make_fake_client([call1_chunks])
    seen_messages = []

    async def fake_create(**kwargs):
        seen_messages.append(kwargs["messages"])
        return FakeStream(call1_chunks)

    client.chat.completions.create = fake_create

    loop = _make_loop(middlewares=[InjectSystemMW()], fake_client=client)
    async for _ in loop.run("hi"):
        pass

    # 验证 LLM 看到的 messages 含注入的 SystemMessage
    assert any(m.get("content") == "[INJECTED]" for m in seen_messages[0]), f"未注入；msgs={seen_messages[0]}"


@pytest.mark.asyncio
async def test_recursion_limit_emits_event():
    """recursion_limit=2 的情况下，LLM 一直 emit tool_calls → 撞墙发 recursion_limit 事件。"""
    # 每次都 emit tool_call 不收敛
    perpetual_call_chunks = [
        _make_chunk(tool_calls=[_make_tc_delta(index=0, id="loop_call", name="noop", arguments="{}")]),
        _make_chunk(finish_reason="tool_calls"),
    ]
    client = _make_fake_client([perpetual_call_chunks] * 5)  # 准备多份

    async def noop(args):
        return "noop"

    tool = ToolSpec(name="noop", func=noop, schema={"name": "noop", "parameters": {}})
    loop = _make_loop(tools=[tool], recursion_limit=2, fake_client=client)

    events = []
    async for ev in loop.run("infinite loop"):
        events.append(ev)

    assert any(e["type"] == "recursion_limit" for e in events)


@pytest.mark.asyncio
async def test_hitl_approve():
    """HitL on_interrupt 返回 True → 工具正常执行。"""
    call1_chunks = [
        _make_chunk(tool_calls=[_make_tc_delta(index=0, id="dangerous", name="kill_db", arguments='{}')]),
        _make_chunk(finish_reason="tool_calls"),
    ]
    call2_chunks = [
        _make_chunk(content="kill done"),
        _make_chunk(finish_reason="stop"),
    ]
    client = _make_fake_client([call1_chunks, call2_chunks])

    executed = []

    async def kill_db(args):
        executed.append(True)
        return "db killed"

    tool = ToolSpec(name="kill_db", func=kill_db, schema={"name": "kill_db", "parameters": {}})
    loop = _make_loop(tools=[tool], interrupt_tools={"kill_db": {"risk": "high"}}, fake_client=client)

    async def approve_all(tool_calls):
        return True

    events = []
    async for ev in loop.run("kill it", on_interrupt=approve_all):
        events.append(ev)

    assert any(e["type"] == "interrupt_resolved" and e["approved"] for e in events)
    assert executed == [True], "approve 后工具应执行"


@pytest.mark.asyncio
async def test_hitl_reject_feeds_refusal_back_to_llm():
    """HitL 拒绝 → 工具不执行，'用户拒绝' 喂回 LLM 让它继续。"""
    call1_chunks = [
        _make_chunk(tool_calls=[_make_tc_delta(index=0, id="dangerous", name="kill_db", arguments='{}')]),
        _make_chunk(finish_reason="tool_calls"),
    ]
    # call 2: LLM 看到拒绝消息后给 fallback 答复
    call2_chunks = [
        _make_chunk(content="既然你拒绝，我就不做了"),
        _make_chunk(finish_reason="stop"),
    ]
    client = _make_fake_client([call1_chunks, call2_chunks])

    executed = []

    async def kill_db(args):
        executed.append(True)
        return "db killed"

    tool = ToolSpec(name="kill_db", func=kill_db, schema={"name": "kill_db", "parameters": {}})
    loop = _make_loop(tools=[tool], interrupt_tools={"kill_db": {}}, fake_client=client)

    async def reject_all(tool_calls):
        return False

    events = []
    async for ev in loop.run("kill it", on_interrupt=reject_all):
        events.append(ev)

    assert executed == [], "reject 后工具不该执行"
    final = next(e for e in events if e["type"] == "complete")
    assert "拒绝" in final["final_message"].content


@pytest.mark.asyncio
async def test_tool_exception_does_not_crash_loop():
    """工具内部抛异常 → 被捕获，错误消息喂回 LLM，loop 继续。"""
    call1_chunks = [
        _make_chunk(tool_calls=[_make_tc_delta(index=0, id="crash_id", name="crashy", arguments='{}')]),
        _make_chunk(finish_reason="tool_calls"),
    ]
    call2_chunks = [
        _make_chunk(content="工具坏了，我没法继续"),
        _make_chunk(finish_reason="stop"),
    ]
    client = _make_fake_client([call1_chunks, call2_chunks])

    async def crashy(args):
        raise ValueError("故意炸")

    tool = ToolSpec(name="crashy", func=crashy, schema={"name": "crashy", "parameters": {}})
    loop = _make_loop(tools=[tool], fake_client=client)

    events = []
    async for ev in loop.run("跑炸的"):
        events.append(ev)

    tool_end = next(e for e in events if e["type"] == "tool_end")
    assert tool_end["error"] is True
    assert "ValueError" in tool_end["output"] or "故意炸" in tool_end["output"]
    # loop 没崩，最终拿到 complete
    assert any(e["type"] == "complete" for e in events)


@pytest.mark.asyncio
async def test_tool_args_json_parse_failure_falls_back_to_empty():
    """tool_call.arguments 不是合法 JSON → 当作 {} 处理，不崩。"""
    call1_chunks = [
        _make_chunk(tool_calls=[_make_tc_delta(
            index=0, id="bad_json_id", name="my_tool", arguments='{invalid json}',
        )]),
        _make_chunk(finish_reason="tool_calls"),
    ]
    call2_chunks = [
        _make_chunk(content="ok"),
        _make_chunk(finish_reason="stop"),
    ]
    client = _make_fake_client([call1_chunks, call2_chunks])

    received_args = []

    async def my_tool(args):
        received_args.append(args)
        return "ok"

    tool = ToolSpec(name="my_tool", func=my_tool, schema={"name": "my_tool", "parameters": {}})
    loop = _make_loop(tools=[tool], fake_client=client)

    events = []
    async for ev in loop.run("test"):
        events.append(ev)

    assert received_args == [{}], "args 解析失败应降级到空 dict"


@pytest.mark.asyncio
async def test_llm_api_error_yields_error_event():
    """LLM 调用本身抛异常 → yield error 事件不崩。"""
    client = MagicMock()
    client.chat = MagicMock()

    async def fake_create(**kwargs):
        raise RuntimeError("network down")

    client.chat.completions = MagicMock()
    client.chat.completions.create = fake_create

    loop = _make_loop(fake_client=client)
    events = []
    async for ev in loop.run("hi"):
        events.append(ev)

    err = next(e for e in events if e["type"] == "error")
    assert isinstance(err["exception"], RuntimeError)


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_string():
    """LLM 调用没注册的工具 → 返回 Error 字符串喂回 LLM，不崩。"""
    call1_chunks = [
        _make_chunk(tool_calls=[_make_tc_delta(index=0, id="bad", name="nonexistent", arguments='{}')]),
        _make_chunk(finish_reason="tool_calls"),
    ]
    call2_chunks = [
        _make_chunk(content="抱歉调不到"),
        _make_chunk(finish_reason="stop"),
    ]
    client = _make_fake_client([call1_chunks, call2_chunks])
    loop = _make_loop(tools=[], fake_client=client)  # 没注册任何工具

    events = []
    async for ev in loop.run("test"):
        events.append(ev)

    tool_end = next(e for e in events if e["type"] == "tool_end")
    assert "未注册" in tool_end["output"]


@pytest.mark.asyncio
async def test_tool_timeout():
    """工具执行超过 tool_timeout_seconds → 返回超时错误。"""
    call1_chunks = [
        _make_chunk(tool_calls=[_make_tc_delta(index=0, id="slow", name="too_slow", arguments='{}')]),
        _make_chunk(finish_reason="tool_calls"),
    ]
    call2_chunks = [
        _make_chunk(content="超时了"),
        _make_chunk(finish_reason="stop"),
    ]
    client = _make_fake_client([call1_chunks, call2_chunks])

    async def too_slow(args):
        await asyncio.sleep(2.0)
        return "should not reach"

    tool = ToolSpec(name="too_slow", func=too_slow, schema={"name": "too_slow", "parameters": {}})
    loop = _make_loop(tools=[tool], tool_timeout=0.2, fake_client=client)

    events = []
    async for ev in loop.run("test"):
        events.append(ev)

    tool_end = next(e for e in events if e["type"] == "tool_end")
    assert tool_end["error"] is True
    assert "超时" in tool_end["output"]


@pytest.mark.asyncio
async def test_token_streaming_yields_each_delta():
    """LLM 流式输出多个 token chunk → 各自 yield 一条 token 事件。"""
    call1_chunks = [
        _make_chunk(content="你"),
        _make_chunk(content="好"),
        _make_chunk(content="！"),
        _make_chunk(finish_reason="stop"),
    ]
    client = _make_fake_client([call1_chunks])
    loop = _make_loop(fake_client=client)

    tokens = []
    async for ev in loop.run("hi"):
        if ev["type"] == "token":
            tokens.append(ev["delta"])

    assert tokens == ["你", "好", "！"]
