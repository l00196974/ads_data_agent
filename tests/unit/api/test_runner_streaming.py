"""Unit tests for AgentRunner.

Mocks langgraph's astream_events to feed deterministic event sequences,
then asserts the SSE events the runner emits to the channel queue.

Append-only model: runner forwards step events for business tools, filters
out deepagents framework meta-tools (write_todos / read_file / ls / ...).
No task state inference, no task_update events.
"""
import asyncio
import json

import pytest
from unittest.mock import MagicMock

from api.channel.runner import AgentRunner, _is_meta_tool
from api.channel.web_sse import WebSSEChannel


def test_meta_tool_filter():
    # agent 内部簿记类工具——过滤掉避免噪音淹没前端步骤列表
    assert _is_meta_tool("write_todos")
    assert _is_meta_tool("read_todos")
    assert _is_meta_tool("read_file")
    assert _is_meta_tool("ls")
    # 业务工具不过滤——前端必须看到
    assert not _is_meta_tool("query_campaign_report")
    assert not _is_meta_tool("build_chart")
    assert not _is_meta_tool("run_command")
    # write_file / edit_file 是用户**真关心**的业务事件（"agent 在我工作区写了文件"），
    # 必须展示——而且它们默认在 medium_risk 列表里会触发 HitL 弹窗。
    # 如果哪天有人手抖把这俩加回 meta filter，前端步骤会丢、确认弹窗背景没上下文，
    # 用户会困惑"刚才确认了什么"。
    assert not _is_meta_tool("write_file")
    assert not _is_meta_tool("edit_file")
    # task 工具是真实的业务派工动作（"派给 XXX 子 Agent"），要展示
    assert not _is_meta_tool("task")
    # 历史 send_* 前缀已不再特殊处理——任意未列入 meta 集合的名字都不算 meta，
    # 包括假想中的 send_xxx（不会真存在，但万一加了也得显式列入 _FRAMEWORK_META_TOOLS）
    assert not _is_meta_tool("send_anything")


def test_build_hitl_response_approve_single_action():
    """单个 action_request 的 approve → {"decisions": [{"type": "approve"}]}"""
    from api.channel.runner import _build_hitl_response
    assert _build_hitl_response(True, 1) == {"decisions": [{"type": "approve"}]}


def test_build_hitl_response_reject_with_message():
    """reject 时带用户解释——LLM 看到这段拒绝原因再决定下一步。"""
    from api.channel.runner import _build_hitl_response
    resp = _build_hitl_response(False, 1, reject_message="不允许操作生产数据")
    assert resp == {"decisions": [{"type": "reject", "message": "不允许操作生产数据"}]}


def test_build_hitl_response_multi_actions():
    """LLM 一次输出多个并行 medium_risk tool_calls——HumanInTheLoopMiddleware 会校验
    decision 数 == action_request 数，不一致直接 raise。当前 UX 是"全部一起 approve/reject"，
    所以构造 N 个相同的 decision。"""
    from api.channel.runner import _build_hitl_response
    resp = _build_hitl_response(True, 3)
    assert len(resp["decisions"]) == 3
    assert all(d == {"type": "approve"} for d in resp["decisions"])


def test_build_hitl_response_zero_actions_clamped_to_one():
    """0 actions 不该发生（HITL 不会拦 0 个 tool_calls），但防御性 clamp 到 1。"""
    from api.channel.runner import _build_hitl_response
    assert len(_build_hitl_response(True, 0)["decisions"]) == 1


def test_handle_interrupt_resume_value_is_hitl_response_dict(tmp_path):
    """端到端守住：resume value 是 langchain HumanInTheLoopMiddleware 期望的
    HITLResponse dict，而不是 bool（旧 bug：之前传 bool 一直没 e2e 验证，
    潜伏到第一次真业务 medium_risk 启用时会炸）。"""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from agent import auto_approve
    from agent.config import InterruptConfig
    from api.channel.runner import _handle_interrupt
    from langgraph.types import Command

    auto_approve.init(tmp_path)
    cfg = InterruptConfig(high_risk=[], medium_risk=["write_file"])
    channel = MagicMock()
    channel.wait_for_confirm = AsyncMock(return_value=(True, False))
    agent = MagicMock()
    agent.ainvoke = AsyncMock()

    iv = {"action_requests": [{"action": "write_file", "args": {"path": "x"}}]}
    asyncio.run(_handle_interrupt(iv, channel, agent, {}, cfg))

    # ainvoke 第一个参数应该是 Command(resume=HITLResponse_dict)
    call_args = agent.ainvoke.call_args
    cmd = call_args[0][0]
    assert isinstance(cmd, Command)
    assert cmd.resume == {"decisions": [{"type": "approve"}]}


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


def test_task_tool_label_shows_subagent_role():
    """task 工具的 step 文案应该展示"派给 XXX（中文角色名）"，而不是把整段 description 露出来。
    不映射的子 Agent 名直接透传不死。"""
    emitted = _run_with_events([
        {
            "event": "on_tool_start",
            "name": "task",
            "data": {"input": {"subagent_type": "issue-diagnostician", "description": "x" * 5000}},
        },
        {"event": "on_tool_end", "name": "task", "data": {}},
    ])
    starts = [e for e in emitted if e["event"] == "step" and e["data"].get("type") == "tool_start"]
    assert len(starts) == 1
    # description 不能进 label，否则前端会被几千字 prompt 撑爆
    assert "x" * 100 not in starts[0]["data"]["msg"]
    assert "问题诊断师" in starts[0]["data"]["msg"]
    assert "派给" in starts[0]["data"]["msg"]


def test_subagent_context_propagates_to_inner_step():
    """task on_tool_start 后，到 task on_tool_end 之间触发的工具 step 事件
    应当带上 subagent 字段——让前端嵌套展示"哪个子 Agent 在干这个活"。"""
    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "task",
         "data": {"input": {"subagent_type": "data-fetcher", "description": "fetch"}}},
        # task 内部，子 Agent 调了一次 run_command
        {"event": "on_tool_start", "name": "run_command",
         "data": {"input": {"command": "query-metrics --metrics click"}}},
        {"event": "on_tool_end", "name": "run_command", "data": {"output": "ok"}},
        {"event": "on_tool_end", "name": "task", "data": {"output": "done"}},
    ])
    steps = [e for e in emitted if e["event"] == "step"]
    # task 自身的 step 不带 subagent（它是上层派工方在调）
    assert steps[0]["data"]["type"] == "tool_start"
    assert "派给" in steps[0]["data"]["msg"]
    assert "subagent" not in steps[0]["data"]
    # 子 Agent 内部 run_command 的 start/end 都带 subagent=data-fetcher
    assert steps[1]["data"]["type"] == "tool_start"
    assert steps[1]["data"]["subagent"] == "data-fetcher"
    assert steps[2]["data"]["type"] == "tool_end"
    assert steps[2]["data"]["subagent"] == "data-fetcher"
    # task 自身的 tool_end 也不带 subagent（弹栈在 send 之前）
    assert steps[3]["data"]["type"] == "tool_end"
    assert "subagent" not in steps[3]["data"]


def test_subagent_thinking_step_emitted_around_llm_call():
    """子 Agent 内部每次 LLM 调用，runner 在 thinking 分组里推一条
    "🧠 XXX 思考分析" 的 tool_start / tool_end，让用户看到 AI 在 reasoning
    （而不是工具调用间隙几十秒静默以为卡死）。"""
    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "task",
         "data": {"input": {"subagent_type": "issue-diagnostician", "description": "x"}}},
        # 子 Agent 第一次 LLM 调用——产出工具决定
        {"event": "on_chat_model_start", "data": {"input": {}}},
        {"event": "on_chat_model_end", "data": {"output": None}},
        # 子 Agent 调一个工具
        {"event": "on_tool_start", "name": "run_command",
         "data": {"input": {"command": "query-metrics --metrics click"}}},
        {"event": "on_tool_end", "name": "run_command", "data": {"output": "ok"}},
        # 子 Agent 第二次 LLM 调用——综合分析
        {"event": "on_chat_model_start", "data": {"input": {}}},
        {"event": "on_chat_model_end", "data": {"output": None}},
        {"event": "on_tool_end", "name": "task", "data": {"output": "done"}},
    ])
    thinking_steps = [
        e for e in emitted
        if e["event"] == "step" and "思考分析" in e["data"].get("msg", "")
    ]
    # 2 次 LLM 调用 → 2 对 tool_start/tool_end → 4 条 step
    assert len(thinking_steps) == 4
    # 都带 subagent=issue-diagnostician
    assert all(s["data"].get("subagent") == "issue-diagnostician" for s in thinking_steps)
    # 文案含中文角色名
    assert all("问题诊断师" in s["data"]["msg"] for s in thinking_steps)
    # 一对 start/end 配对
    assert thinking_steps[0]["data"]["type"] == "tool_start"
    assert thinking_steps[1]["data"]["type"] == "tool_end"
    assert thinking_steps[2]["data"]["type"] == "tool_start"
    assert thinking_steps[3]["data"]["type"] == "tool_end"


def test_subagent_progress_heartbeat_with_char_count():
    """子 Agent 内部 LLM token 流过来时，runner 累计字数，每跨 50 字门槛
    推一条 progress 事件，文案带 "已生成 N 字"。让用户看到字数实时增长，
    确认 AI 在思考没卡死。"""
    chunk_a = MagicMock(content="a" * 30)
    chunk_b = MagicMock(content="b" * 30)  # 累计到 60，跨过 50
    chunk_c = MagicMock(content="c" * 60)  # 累计到 120，跨过 100
    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "task",
         "data": {"input": {"subagent_type": "data-analyst", "description": "x"}}},
        {"event": "on_chat_model_start", "data": {"input": {}}},
        # 子 Agent 流式 token——不推 token 事件，但累积字数推 progress 心跳
        {"event": "on_chat_model_stream", "data": {"chunk": chunk_a}},
        {"event": "on_chat_model_stream", "data": {"chunk": chunk_b}},
        {"event": "on_chat_model_stream", "data": {"chunk": chunk_c}},
        {"event": "on_chat_model_end", "data": {"output": None}},
        {"event": "on_tool_end", "name": "task", "data": {"output": "ok"}},
    ])
    # 子 Agent token 不应泄漏到 token 事件（防 "两份报告" regression）
    assert all(e["event"] != "token" for e in emitted)
    # progress 事件应当包含 "已生成 X 字" 心跳，至少 2 次（30→60 跨 50；60→120 跨 100）
    progress_events = [e for e in emitted if e["event"] == "progress"]
    char_count_progresses = [
        e for e in progress_events if "已生成" in e["data"].get("message", "")
    ]
    assert len(char_count_progresses) >= 2
    # 文案含中文角色名
    assert all("数据分析师" in e["data"]["message"] for e in char_count_progresses)


def test_subagent_token_stream_filtered():
    """子 Agent 内部 LLM 的 token stream 不应该推给前端——它们的输出是给主 Agent
    的 ToolMessage，主 Agent 消化后再以最终 markdown token 流的形式产出。
    否则用户会看到"子 Agent 自己的报告 + 主 Agent 又总结一份"两份重复内容。"""
    chunk_outer_pre = MagicMock(content="我来安排")
    chunk_inner = MagicMock(content="子Agent内部分析过程")
    chunk_outer_post = MagicMock(content="最终结论：xxx")
    emitted = _run_with_events([
        # 主 Agent 在 task 调用前的过渡 token——应当推到前端
        {"event": "on_chat_model_stream", "data": {"chunk": chunk_outer_pre}},
        # 进入子 Agent
        {"event": "on_tool_start", "name": "task",
         "data": {"input": {"subagent_type": "data-analyst", "description": "x"}}},
        # 子 Agent 内部的 token——**不应当**推到前端
        {"event": "on_chat_model_stream", "data": {"chunk": chunk_inner}},
        {"event": "on_tool_end", "name": "task", "data": {"output": "ok"}},
        # 主 Agent 拿到子 Agent 结果后输出的最终 token——应当推到前端
        {"event": "on_chat_model_stream", "data": {"chunk": chunk_outer_post}},
    ])
    tokens = [e["data"].get("token") for e in emitted if e["event"] == "token"]
    assert "我来安排" in tokens
    assert "最终结论：xxx" in tokens
    assert "子Agent内部分析过程" not in tokens, "子 Agent 的 token 不应该泄漏给前端"


def test_subagent_context_clears_after_task_end():
    """task on_tool_end 之后再来的工具 step 不应该带 subagent——上下文已清。"""
    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "task",
         "data": {"input": {"subagent_type": "data-fetcher", "description": "x"}}},
        {"event": "on_tool_end", "name": "task", "data": {"output": "ok"}},
        # task 已结束，主 Agent 又调了一次工具——不应该带 subagent
        {"event": "on_tool_start", "name": "run_command",
         "data": {"input": {"command": "query-metrics"}}},
        {"event": "on_tool_end", "name": "run_command", "data": {"output": "ok"}},
    ])
    steps = [e for e in emitted if e["event"] == "step"]
    # 最后一组 run_command 的 start/end 都不带 subagent
    assert steps[-2]["data"]["type"] == "tool_start"
    assert "subagent" not in steps[-2]["data"]
    assert steps[-1]["data"]["type"] == "tool_end"
    assert "subagent" not in steps[-1]["data"]


def test_write_todos_filtered_as_meta():
    """deepagents 默认注入的 write_todos 是子 Agent 自我管理 TODO 状态用的，
    对用户没有业务含义，必须从 step 事件流里过滤掉——否则前端步骤列表会被
    一堆 'write_todos' 噪音淹没（Force-Diag 实测：1 次诊断派工触发 4 次 write_todos）。"""
    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "write_todos",
         "data": {"input": {"todos": [{"content": "查数据", "status": "in_progress"}]}}},
        {"event": "on_tool_end", "name": "write_todos", "data": {}},
    ])
    # 完全不应该出现 step 事件
    assert all(e["event"] != "step" for e in emitted)


def test_hitl_extract_tool_name_from_langchain_payload():
    """langchain HumanInTheLoopMiddleware 抛的 interrupt payload 是
    {action_requests: [{action: tool_name, args: {...}}], ...} 标准格式。
    runner 必须能从中拿到 tool_name 用于风险分级 + 白名单匹配。"""
    from api.channel.runner import _extract_tool_name_from_interrupt
    iv = {
        "action_requests": [
            {"action": "create-audience", "args": {"name": "x"}, "description": "desc"}
        ],
        "review_configs": [],
    }
    assert _extract_tool_name_from_interrupt(iv) == "create-audience"


def test_hitl_extract_tool_name_legacy_format():
    """项目历史也可能用 {tool_name: ..., message: ...} 简化格式直接抛——也支持。"""
    from api.channel.runner import _extract_tool_name_from_interrupt
    assert _extract_tool_name_from_interrupt({"tool_name": "modify-budget"}) == "modify-budget"


def test_hitl_extract_tool_name_safe_fallback():
    """非 dict / 拿不到任何字段时返回空串，不能 raise——runner 决策路径上调用。"""
    from api.channel.runner import _extract_tool_name_from_interrupt
    assert _extract_tool_name_from_interrupt(None) == ""
    assert _extract_tool_name_from_interrupt("just a string") == ""
    assert _extract_tool_name_from_interrupt({}) == ""


def test_hitl_auto_approve_skips_prompt_for_medium_in_whitelist(tmp_path):
    """medium_risk + 在白名单 → runner 不弹窗、直接 resume(approve=True)。
    这是"以后不再确认"的核心收益——用户体验上不再被打扰。"""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from agent import auto_approve
    from agent.config import InterruptConfig
    from api.channel.runner import _handle_interrupt

    auto_approve.init(tmp_path)
    auto_approve.add("create-audience")
    cfg = InterruptConfig(high_risk=[], medium_risk=["create-audience"])
    channel = MagicMock()
    channel.wait_for_confirm = AsyncMock(return_value=(True, False))
    agent = MagicMock()
    agent.ainvoke = AsyncMock()

    iv = {"action_requests": [{"action": "create-audience", "args": {}}]}
    asyncio.run(_handle_interrupt(iv, channel, agent, {}, cfg))

    channel.wait_for_confirm.assert_not_called()  # 没弹窗——直接放行
    agent.ainvoke.assert_called_once()  # 直接 resume


def test_hitl_high_risk_always_prompts_even_if_in_whitelist(tmp_path):
    """high_risk 工具即使被错误地写进白名单也必须弹窗——这是硬安全约束。
    防御场景：运维误把 delete-adgroup 加进白名单，runner 也得守住底线。"""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from agent import auto_approve
    from agent.config import InterruptConfig
    from api.channel.runner import _handle_interrupt

    auto_approve.init(tmp_path)
    auto_approve.add("delete-adgroup")  # 故意加 high_risk 工具到白名单
    cfg = InterruptConfig(high_risk=["delete-adgroup"], medium_risk=[])
    channel = MagicMock()
    channel.wait_for_confirm = AsyncMock(return_value=(True, False))
    agent = MagicMock()
    agent.ainvoke = AsyncMock()

    iv = {"action_requests": [{"action": "delete-adgroup", "args": {}}]}
    asyncio.run(_handle_interrupt(iv, channel, agent, {}, cfg))

    channel.wait_for_confirm.assert_called_once()  # 必须弹窗——白名单被忽略


def test_hitl_remember_writes_to_whitelist_only_for_medium(tmp_path):
    """approve + 勾"以后不再确认" + medium_risk → 写白名单。
    high_risk 即使勾了也不写——上一个测试守住"读时忽略"，这个测试守住"写时拒绝"。
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from agent import auto_approve
    from agent.config import InterruptConfig
    from api.channel.runner import _handle_interrupt

    auto_approve.init(tmp_path)
    cfg = InterruptConfig(high_risk=["delete-adgroup"], medium_risk=["create-audience"])
    channel = MagicMock()
    # 用户两次都 approve + 勾"以后不再问"
    channel.wait_for_confirm = AsyncMock(return_value=(True, True))
    agent = MagicMock()
    agent.ainvoke = AsyncMock()

    # medium → 写白名单
    asyncio.run(_handle_interrupt(
        {"action_requests": [{"action": "create-audience", "args": {}}]},
        channel, agent, {}, cfg,
    ))
    assert auto_approve.contains("create-audience")

    # high → 即使勾了也不写
    asyncio.run(_handle_interrupt(
        {"action_requests": [{"action": "delete-adgroup", "args": {}}]},
        channel, agent, {}, cfg,
    ))
    assert not auto_approve.contains("delete-adgroup")


def test_runner_handles_missing_thread_id():
    """thread_id 为空时 user_id 默认 anon，不报错。"""
    emitted = _run_with_events([
        {"event": "on_tool_start", "name": "run_command", "data": {"input": {}}},
        {"event": "on_tool_end", "name": "run_command", "data": {"output": "ok"}},
    ])
    # 应当正常处理，不抛异常
    assert any(e["event"] == "step" for e in emitted)
