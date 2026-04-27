"""Unit tests for AgentRunner state machine.

Mocks langgraph's astream_events to feed deterministic event sequences,
then asserts the SSE events the runner emits to the channel queue.

These tests run offline — no LLM calls, no network.
"""
import asyncio
import json

import pytest
from unittest.mock import MagicMock

from api.channel.runner import AgentRunner, _is_meta_tool
from api.channel.web_sse import WebSSEChannel


def test_meta_tool_filter():
    assert _is_meta_tool("send_plan")
    assert _is_meta_tool("send_to_user")
    assert not _is_meta_tool("query_campaign_report")
    assert not _is_meta_tool("build_chart")


def _make_channel():
    queue = asyncio.Queue()
    return WebSSEChannel("u", "s", queue), queue


async def _drain(queue: asyncio.Queue) -> list[dict]:
    """Pull all queued SSE chunks and parse them into structured events."""
    out = []
    while not queue.empty():
        chunk = await queue.get()
        lines = chunk.split("\n")
        ev_line = next((l for l in lines if l.startswith("event:")), "event: ?")
        data_line = next((l for l in lines if l.startswith("data:")), "data: {}")
        try:
            data = json.loads(data_line[5:].strip() or "{}")
        except json.JSONDecodeError:
            data = {}
        out.append({"event": ev_line[6:].strip(), "data": data})
    return out


def _run_with_events(events: list[dict]) -> list[dict]:
    """Drive the runner with a canned event stream and return what it emitted."""
    async def fake_stream():
        for ev in events:
            yield ev

    channel, queue = _make_channel()
    fake_agent = MagicMock()
    fake_agent.astream_events = lambda *a, **kw: fake_stream()

    runner = AgentRunner()
    asyncio.run(runner.run(channel, "msg", lambda extra_tools=None: fake_agent, {}))
    return asyncio.run(_drain(queue))


def test_task_updates_emitted_in_order():
    """Each business tool's start/end produces task_update(running)/(done)
    for the matching plan task, in declaration order."""
    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "send_plan", "data": {"input": {
            "tasks": [
                {"id": "t1", "name": "查询数据"},
                {"id": "t2", "name": "生成图表"},
            ]
        }}},
        {"event": "on_tool_end", "name": "send_plan", "data": {}},
        {"event": "on_tool_start", "name": "query_campaign_report", "data": {"input": {"days": 7}}},
        {"event": "on_tool_end", "name": "query_campaign_report", "data": {}},
        {"event": "on_tool_start", "name": "build_chart", "data": {"input": {}}},
        {"event": "on_tool_end", "name": "build_chart", "data": {}},
    ])

    sequence = [(e["event"], e["data"].get("task_id"), e["data"].get("status")) for e in emitted]
    assert sequence == [
        ("task_update", "t1", "running"),
        ("step", None, None),
        ("task_update", "t1", "done"),
        ("step", None, None),
        ("task_update", "t2", "running"),
        ("step", None, None),
        ("task_update", "t2", "done"),
        ("step", None, None),
        ("done", None, None),
    ], f"unexpected sequence: {sequence}"


def test_send_plan_does_not_emit_step_or_task_update():
    """send_plan itself is a meta-tool — its tool_start/tool_end must not
    leak through as step/task_update events."""
    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "send_plan", "data": {"input": {"tasks": []}}},
        {"event": "on_tool_end", "name": "send_plan", "data": {}},
    ])
    types = [e["event"] for e in emitted]
    assert types == ["done"], f"send_plan leaked: {types}"


def test_extra_tool_call_beyond_plan_does_not_crash():
    """If LLM invokes more business tools than were declared, runner emits
    step events but stops emitting task_update once the plan is exhausted."""
    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "send_plan", "data": {"input": {
            "tasks": [{"id": "t1", "name": "唯一任务"}]
        }}},
        {"event": "on_tool_end", "name": "send_plan", "data": {}},
        {"event": "on_tool_start", "name": "query_campaign_report", "data": {"input": {}}},
        {"event": "on_tool_end", "name": "query_campaign_report", "data": {}},
        # extra tool call — t2 was never declared
        {"event": "on_tool_start", "name": "build_chart", "data": {"input": {}}},
        {"event": "on_tool_end", "name": "build_chart", "data": {}},
    ])
    task_updates = [e for e in emitted if e["event"] == "task_update"]
    steps = [e for e in emitted if e["event"] == "step"]
    assert len(task_updates) == 2  # t1 running + t1 done only
    assert all(u["data"]["task_id"] == "t1" for u in task_updates)
    assert len(steps) == 4  # 2 tools * (start + end)


def test_token_stream_passes_through():
    """LLM streaming text tokens go straight to channel.send_token."""
    chunk_mock = MagicMock()
    chunk_mock.content = "hello"
    emitted = _run_with_events([
        {"event": "on_chat_model_stream", "data": {"chunk": chunk_mock}},
    ])
    assert emitted[0]["event"] == "token"
    assert emitted[0]["data"] == {"token": "hello"}


def test_business_tool_before_plan_does_nothing():
    """If a business tool fires before send_plan (shouldn't happen but
    defensive), no task_update is emitted because there's no plan yet."""
    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "query_campaign_report", "data": {"input": {}}},
        {"event": "on_tool_end", "name": "query_campaign_report", "data": {}},
    ])
    task_updates = [e for e in emitted if e["event"] == "task_update"]
    assert len(task_updates) == 0
