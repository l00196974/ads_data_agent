"""Unit tests for AgentRunner.

Mocks langgraph's astream_events to feed deterministic event sequences,
then asserts the SSE events the runner emits to the channel queue.

Append-only model: runner forwards step events for business tools, filters
out send_* meta-tools. No task state inference, no task_update events.
"""
import asyncio
import json

import pytest
from unittest.mock import MagicMock

from api.channel.runner import AgentRunner, _is_meta_tool
from api.channel.web_sse import WebSSEChannel


def test_meta_tool_filter():
    # send_* 前缀都视为 channel-injected meta tool（即使 send_plan 已下线，
    # 保留前缀过滤是为了兼容未来再加的展示型工具）
    assert _is_meta_tool("send_to_user")
    assert _is_meta_tool("send_plan")
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


def test_business_tool_emits_step_events():
    """Each business tool emits step:tool_start and step:tool_end."""
    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "query_campaign_report", "data": {"input": {}}},
        {"event": "on_tool_end", "name": "query_campaign_report", "data": {}},
    ])
    types = [(e["event"], e["data"].get("type")) for e in emitted]
    assert types == [
        ("step", "tool_start"),
        ("step", "tool_end"),
        ("done", None),
    ]


def test_send_to_user_filtered_as_meta():
    """send_to_user is also meta — chart/progress emissions go through the
    skill closure, runner shouldn't re-emit step events for it."""
    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "send_to_user", "data": {"input": {"action": "chart"}}},
        {"event": "on_tool_end", "name": "send_to_user", "data": {}},
    ])
    types = [e["event"] for e in emitted]
    assert types == ["done"]


def test_token_stream_passes_through():
    """LLM streaming text tokens go straight to channel.send_token."""
    chunk_mock = MagicMock()
    chunk_mock.content = "hello"
    emitted = _run_with_events([
        {"event": "on_chat_model_stream", "data": {"chunk": chunk_mock}},
    ])
    assert emitted[0]["event"] == "token"
    assert emitted[0]["data"] == {"token": "hello"}


def test_no_task_update_events_emitted():
    """Append-only model: runner never emits task_update — that whole layer
    is gone. Frontend infers nothing from these events anymore."""
    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "query_campaign_report", "data": {"input": {}}},
        {"event": "on_tool_end", "name": "query_campaign_report", "data": {}},
    ])
    assert not any(e["event"] == "task_update" for e in emitted)


def test_runner_pushes_artifact_updated_when_tool_output_contains_sentinel():
    """工具结束后 runner 应从 event["data"]["output"] 抽 artifact_ids，推 SSE 事件。"""
    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "run_command", "data": {"input": {}}},
        {
            "event": "on_tool_end",
            "name": "run_command",
            "data": {"output": "DIR=/tmp/x\nID=abc\n\n[已生成 artifact: 2026-04-30-120000-a]\n[已生成 artifact: 2026-04-30-120000-b]"},
        },
    ])
    artifact_events = [e for e in emitted if e["event"] == "artifact_updated"]
    assert len(artifact_events) == 2
    ids = {e["data"]["artifact_id"] for e in artifact_events}
    assert ids == {"2026-04-30-120000-a", "2026-04-30-120000-b"}
    assert all(e["data"]["action"] == "created" for e in artifact_events)


def test_runner_no_artifact_event_when_output_has_no_sentinel():
    """普通工具返回（无 sentinel）不推 artifact_updated 事件。"""
    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "run_command", "data": {"input": {}}},
        {"event": "on_tool_end", "name": "run_command", "data": {"output": "just plain output"}},
    ])
    artifact_events = [e for e in emitted if e["event"] == "artifact_updated"]
    assert len(artifact_events) == 0


def test_runner_extracts_ids_from_tool_message_output():
    """langgraph 在 on_tool_end 给的 output 通常是 ToolMessage（不是裸 str），
    runner 必须从 .content 取文本——这是真实 e2e 跑时发现的 bug。"""
    from langchain_core.messages import ToolMessage
    tm = ToolMessage(
        content="result\n[已生成 artifact: 2026-04-30-150000-x]",
        tool_call_id="call_1",
    )
    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "run_command", "data": {"input": {}}},
        {"event": "on_tool_end", "name": "run_command", "data": {"output": tm}},
    ])
    artifact_events = [e for e in emitted if e["event"] == "artifact_updated"]
    assert len(artifact_events) == 1
    assert artifact_events[0]["data"]["artifact_id"] == "2026-04-30-150000-x"


def test_runner_does_not_inject_env_to_global_environ(monkeypatch):
    """runner 不再用 os.environ 注入 ADS_AGENT_* env——会和 langchain create_task
    派发的 tool 协程有 race。env 注入由 skill_loader.run_command 在调用 subprocess
    时显式做（见 test_skill_loader.py::test_skill_loader_injects_artifact_env）。

    本测试守住"不再注入"行为，避免 regression 把 runner 改回 race-prone 模式。"""
    import os

    # 清掉可能遗留的 env
    for k in ["ADS_AGENT_ARTIFACT_DIR", "ADS_AGENT_ARTIFACT_ID", "ADS_AGENT_USER_ID"]:
        os.environ.pop(k, None)

    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "run_command", "data": {"input": {}}},
        {"event": "on_tool_end", "name": "run_command", "data": {"output": "done"}},
    ])
    # Step events 仍正常发出
    assert any(e["event"] == "step" and e["data"].get("type") == "tool_start" for e in emitted)
    # 但 ADS_AGENT_* env 不应该被 runner 设上
    assert "ADS_AGENT_ARTIFACT_DIR" not in os.environ
    assert "ADS_AGENT_ARTIFACT_ID" not in os.environ


def test_runner_handles_missing_thread_id():
    """thread_id 为空时 user_id 默认 anon，不报错。"""
    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "run_command", "data": {"input": {}}},
        {"event": "on_tool_end", "name": "run_command", "data": {"output": "ok"}},
    ])
    # 应当正常处理，不抛异常
    assert any(e["event"] == "step" for e in emitted)
