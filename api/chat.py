import asyncio
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from agent import auto_approve, conversation_events, conversation_meta, conversation_metrics
from agent.config import load_config
from agent.session import SessionManager
from agent.skill_loader import load_md_skills
from agent.user_space import UserSpace
from api.channel import (
    ExternallyConfirmable,
    WebSSEChannel,
    channel_registry,
)
from api.models import (
    AppendRequest,
    ChatRequest,
    ConfirmRequest,
    RenameConversationRequest,
    SkillCreateRequest,
)

router = APIRouter()
session_mgr = SessionManager()
cfg = load_config()

# 当前活跃的 agent 运行（按 conversation_id 索引）。追问场景下：先 cancel 当前 task，
# 在同一 channel + thread 上跑新一轮——LLM 看到完整历史 + 新指令。
_active_runs: dict[str, dict] = {}

PLAN_INSTRUCTION = """## 执行流程（严格遵守）

收到用户问题后，按以下顺序执行：

1. **直接调用业务工具**（即 `run_command`），不输出中间散文。前端会自动展示
   每个工具的执行进度（开始 → 结束），不需要你主动报告。

   **关键：独立查询应同一轮并行 emit 多个 tool_calls**——系统会用 `asyncio.gather`
   并发执行，避免每个工具都付一次 LLM round-trip 成本（LLM 等待 → 接收结果 →
   再决定下一个工具）。一次并行 N 个 vs 串行 N 次，整轮总耗时差 (N-1) × LLM-TTFT
   ≈ 节省数秒。

   **判断规则**——独立 vs 依赖：

   - ✅ **独立（必须并行 emit）**：参数互相不依赖前序结果。例如：
     - 同一指标多时间窗对比（近 7 天 + 近 30 天 + 上周 vs 本周）
     - 同一时间窗多指标拉取（曝光 + 点击 + 流水 + CTR）
     - 同一时间窗多维度并列对比（按媒体 + 按版位 + 按推广标的 三个独立 group by）
     - 多个 list-* 类元数据查询（list-metrics + list-dimensions 并行）
   - ❌ **依赖（必须串行）**：后一个工具的参数来自前一个工具的输出。例如：
     - `search-dimension-values` → 拿到精确值后再 `query-metrics --filter`
     - 先 `list-dimensions` → 看到字段名后再 `query-metrics --dimensions`
     - 子 Agent 工具 `task` —— 顺序更安全，**不要并行 task**（独立 context 难协调）

   **示例**：用户问"对比近 7 天 vs 上周 7 天的曝光、点击、流水"——独立 6 个查询，
   应一次 emit 6 个 `run_command` 并行执行，1 轮搞定，而不是 6 轮串行。

   **重要**：`run_command` 的 `command` 参数是**子命令名 + 参数**，**不要**前缀 skill 包名。
   即使用户说"调用 demo-artifact-writer 的 write-demo-report"，你也应该传：

       ✅ `run_command(command="write-demo-report --title '测试'")`
       ❌ `run_command(command="demo-artifact-writer write-demo-report --title '测试'")`

   已注册的子命令清单见下方"业务技能"段——`run_command` 只接受这些子命令名，
   传 skill 包名会得到 `Error: 未注册的命令` 错误。

2. **业务工具全部执行完毕后，直接以 Markdown 文本输出最终分析结果**——
   系统会把你的文字逐 token 流式推送到前端。

3. **（可选）需要展示图表时，把图表作为 markdown 代码块嵌进文本里**，
   语言标识用 `chart`，内容是一段 JSON：

   ~~~
   ```chart
   {"type": "line", "title": "点击趋势", "x_data": ["...", "..."], "series": [{"name": "click", "data": [123, 456]}]}
   ```
   ~~~

   - `type`: `bar` | `line` | `pie` | `scatter`
   - `x_data`: X 轴标签数组（pie 图忽略）
   - `series`: 每项 `{"name": "...", "data": [...]}`
   - **不要为图表单独调用任何工具**——前端会扫 markdown 里的 ```chart``` 代码块自动渲染

4. **（可选）最后再输出一行 Markdown 引导追问**（如"如需下钻分析某渠道..."）。

## 不要做的事

- ❌ 不要在工具调用之前 / 之间输出"我现在要查询..."、"接下来..."这种散文
- ❌ 不要重复声明你要做什么——直接做即可。前端会自动展示工具执行进度
- ❌ 不要调用任何额外的"进度 / 计划 / 图表 / 摘要"性质的工具——一律通过最终
   Markdown 文本输出，避免无谓的 LLM round trip

## 工具调用次数限制（必读）

本次会话工具调用上限约 **75 次**（系统会在 70% / 90% 时往对话里注入警告 system 消息）。
- **简单查询** 通常 1-3 次工具调用即可回答；多了就是浪费
- **复杂诊断**（多维下钻 / 对比分析 / 人群+画像）可达 30-60 次，但**不要超过 60**
- 看到系统注入的 `[ITER-GUARD-SOFT]` 提示 → 立即收敛：评估已收集的数据是否够回答，够就直接出最终 Markdown
- 看到 `[ITER-GUARD-URGENT]` → **强制总结**：再调至多 1-2 次工具就必须给最终回答；信息不足就直接告诉用户"基于现有数据..."并请求缩小问题范围
- **不要无限探索**——LLM 容易陷入"再查一个维度看看"的循环；优先复用已有数据，再考虑新查询
- self-check 节奏：每调 5 次工具问自己一遍"我是不是已经能回答用户的问题了？"

## 标准示例

用户问"查询最近7天点击数据并生成折线图"，假设 SKILL.md 注册了 `query-metrics`：

1. `run_command(command="query-metrics --metrics click --start-date 2026-04-21 --end-date 2026-04-27 --time-mode event --dimensions day")`
2. 直接输出 Markdown：

~~~
**最近7天点击趋势**
- 总点击：xxx
- 日均：xxx

```chart
{"type": "line", "title": "点击趋势", "x_data": ["04-21","04-22","04-23","04-24","04-25","04-26","04-27"], "series": [{"name": "click", "data": [120,135,128,150,162,158,170]}]}
```

如需下钻分析某渠道，请告诉我。
~~~
"""


# 历史 _today_context() / _WEEKDAY_CN 已迁移到 agent/middleware/date_reminder.py
# system_prompt 必须 100% 跨天稳定（OpenAI 兼容 cache 前缀），日期改成 middleware
# 每轮注入 <system-reminder> 到 messages 头部


# ThreadStore 单例——AgentRunnerV2 期望复用 store connection（aiosqlite + WAL）。
# 第一次需要 v2 时 lazy init，进程内全局共享。chat 多并发也线程安全（aiosqlite
# 内部串行化写入）。
_v2_store_cache: dict[str, "object"] = {}


async def _get_v2_store():
    """lazy init agent.state.ThreadStore（v2 的状态持久化层）。"""
    if "store" in _v2_store_cache:
        return _v2_store_cache["store"]
    from agent.state import ThreadStore
    store = ThreadStore(Path(cfg.persistence.data_dir) / "checkpoints.db")
    await store.init()
    _v2_store_cache["store"] = store
    return store


async def _start_v2_agent_run(channel, user_id: str, conversation_id: str, message: str) -> asyncio.Task:
    """装配 agent loop 链路 + 起 task。

    架构（P4b 完成后唯一链路）：
    - agent.loop.AgentLoop（直调 openai SDK，不走 langchain_openai 包装）
    - agent.state.ThreadStore（loop_messages 表持久化）
    - agent.middleware（DateReminder/IterationGuard/ToolOutputTruncation）
    - SkillsPackage.tools（ToolSpec 格式，run_command 派发 SKILL.md 子命令）
    """
    from agent.loop import AgentConfig as LoopConfig, AgentLoop
    from agent.middleware import DateReminder, IterationGuard, Summarization, ToolOutputTruncation
    from agent.subagent import make_task_tool
    from api.channel.runner_v2 import AgentRunnerV2

    us = UserSpace(user_id, cfg.persistence.data_dir)
    md_pkg = load_md_skills(
        cfg.skills.md_dir,
        us.skills_dir,
        user_id=user_id,
        artifacts_root=us.artifacts_dir,
    )

    # **system_prompt 必须 100% 跨天稳定**——同 v1 路径，日期由 DateReminder
    # middleware 注入到 messages，不进 system_prompt
    parts = [PLAN_INSTRUCTION, us.get_agents_md()]
    if md_pkg.prompt_addition:
        parts.append(md_pkg.prompt_addition)
    system_prompt = "\n\n".join(parts)

    intercepted = cfg.agent.interrupt_on.all_intercepted()
    interrupt_tools = {name: {"risk": cfg.agent.interrupt_on.classify(name)} for name in intercepted}

    loop_config = LoopConfig(
        model=cfg.llm.model,
        api_key=cfg.llm.api_key or None,
        base_url=cfg.llm.base_url or None,
        system_prompt=system_prompt,
        recursion_limit=cfg.agent.recursion_limit,
        interrupt_tools=interrupt_tools,
    )

    thread_id = session_mgr.get_thread_id(user_id, conversation_id)

    # Summarization 需要 openai client——跟主 loop 复用同一 client（避免新建连接）
    # 通过临时 AgentLoop 实例拿 client（仅为获取 client，loop 实例本身丢弃）
    _client_donor = AgentLoop(config=loop_config, tools=[], middlewares=[])
    summarization_client = _client_donor._get_client()

    middlewares = [
        ToolOutputTruncation(
            max_bytes=cfg.agent.long_context.tool_output_max_bytes,
            data_dir=cfg.persistence.data_dir,
        ),
        Summarization(
            openai_client=summarization_client,
            model=cfg.llm.model,
            trigger_tokens=cfg.agent.long_context.summarization_trigger_tokens,
            keep_messages=cfg.agent.long_context.summarization_keep_messages,
        ),
        IterationGuard(recursion_limit=cfg.agent.recursion_limit),
        DateReminder(),
    ]

    store = await _get_v2_store()
    runner_v2 = AgentRunnerV2(store)

    # 构造 tools 列表 = skill tools + 可选 task tool（subagents 配置非空时才加）
    tools = list(md_pkg.tools)
    task_tool = make_task_tool(
        subagents=cfg.agent.subagents,
        base_loop_config=loop_config,
        skill_tools=md_pkg.tools,  # 子 loop 共享同一套 skill 工具
        channel=channel,
        thread_id=thread_id,
    )
    if task_tool is not None:
        tools.append(task_tool)

    return asyncio.create_task(runner_v2.run(
        channel=channel,
        thread_id=thread_id,
        user_message=message,
        loop_config=loop_config,
        tools=tools,
        middlewares=middlewares,
    ))


def _register_active_task(conversation_id: str, task: asyncio.Task) -> None:
    """完成时清理；只清理"还是当前 task"的那条记录（被 append 顶替的就别动）。"""
    def _cleanup(t: asyncio.Task) -> None:
        cur = _active_runs.get(conversation_id)
        if cur and cur.get("task") is t:
            _active_runs.pop(conversation_id, None)

    task.add_done_callback(_cleanup)


@router.post("/{user_id}")
async def chat(user_id: str, req: ChatRequest):
    # ephemeral session_id for HitL confirm lookup; conversation_id 决定 langgraph thread。
    session_id = f"{user_id}_{uuid4().hex[:8]}"
    conversation_id = req.conversation_id or uuid4().hex[:12]
    queue: asyncio.Queue = asyncio.Queue()
    channel = WebSSEChannel(user_id, session_id, queue, conversation_id=conversation_id)
    # 每次新一轮发问 = 新 turn_id；channel 写 step / artifact_updated 事件时按
    # turn_id 分组，前端拉历史时能正确把"思考分组"插到对应 user/ai 之间。
    turn_id = uuid4().hex[:12]
    channel.set_turn_id(turn_id)
    channel_registry.register(session_id, channel)

    # 写会话元数据：首次发消息 → INSERT（标题取 message 前 20 字），
    # 后续 → UPDATE last_active_at + message_count++（同时清归档状态）。
    conversation_meta.upsert(user_id, conversation_id, req.message)

    # 启动 v2 链路：agent.loop + agent.state + agent.middleware（P4b 已删 v1）
    task = await _start_v2_agent_run(channel, user_id, conversation_id, req.message)
    _active_runs[conversation_id] = {
        "task": task,
        "channel": channel,
        "session_id": session_id,
    }
    _register_active_task(conversation_id, task)

    async def event_generator():
        # finally 兜底：客户端断连时 generator 被 GC，也要清 registry
        try:
            while True:
                chunk = await queue.get()
                yield chunk
                if chunk.startswith("event: done") or chunk.startswith("event: error"):
                    break
        finally:
            channel_registry.unregister(session_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Session-Id": session_id,
            "X-Conversation-Id": conversation_id,
            "Access-Control-Expose-Headers": "X-Session-Id, X-Conversation-Id",
        },
    )


@router.post("/{user_id}/confirm")
async def confirm(user_id: str, req: ConfirmRequest):
    # Capability check, not class check — any channel that satisfies the
    # ExternallyConfirmable protocol can be confirmed via this endpoint
    # (WebSSEChannel today; future Slack/WebSocket channels for free).
    channel = channel_registry.get(req.session_id)
    if not channel:
        return {"status": "not_found"}
    if not isinstance(channel, ExternallyConfirmable):
        return {"status": "not_supported_on_this_channel"}
    channel.resolve_confirm(req.action == "approve", add_to_auto_approve=req.add_to_auto_approve)
    return {"status": "ok"}


@router.post("/{user_id}/cancel")
async def cancel(user_id: str):
    return {"status": "cancelled"}


@router.get("/preferences/auto_approve")
async def list_auto_approve():
    """列出全部"以后不再确认"的工具名（全局共享、所有用户可见）。
    设置页用此列表展示，让用户可以撤销某条免确认。"""
    return {"tools": auto_approve.list_all()}


@router.post("/preferences/auto_approve/{tool_name}")
async def add_auto_approve(tool_name: str):
    """直接把工具加入全局白名单。

    主要走这两个调用方：
      - 用户在 confirm 弹窗里勾"以后不再确认"——经由 /confirm endpoint 间接调用 auto_approve.add()
      - 运维 / 管理员场景——curl POST 直接加（绕过 HitL 流程）

    front-end 设置页**不**直接用此端点（用户应当通过弹窗勾选触发，而不是凭空加），
    但保留给运维 / 测试 / 自动化脚本用。
    """
    auto_approve.add(tool_name)
    return {"status": "ok", "tools": auto_approve.list_all()}


@router.delete("/preferences/auto_approve/{tool_name}")
async def remove_auto_approve(tool_name: str):
    """从全局白名单撤销某个工具——下次该工具被 medium_risk 拦截时重新弹窗。"""
    auto_approve.remove(tool_name)
    return {"status": "ok", "tools": auto_approve.list_all()}


@router.post("/{user_id}/append")
async def append_message(user_id: str, req: AppendRequest):
    """追问 = 取消当前运行的 agent task，在同一个 channel + langgraph thread 上重起一轮。

    为什么不再用 aupdate_state：那只把消息写进 checkpointer，但 langgraph 只在
    节点之间读 state——LLM 一旦进入"生成最终回复"阶段就不再回 model 节点，
    新消息要等下一轮才被看到，结果就是这次"被忽略"。

    cancel + 重起的妙处：
    - 同一 conversation_id → 同一 langgraph thread → checkpointer 里完整历史保留
    - 新一轮 astream_events 启动时，LLM 看到：原问题 + 已做的工具调用 + 新追问
    - SSE channel 不关（runner 在 CancelledError 时跳过 channel.close），
      前端连接没断，无缝继续接收新一轮的事件
    """
    if req.conversation_id is None:
        return {"status": "no_conversation_id", "hint": "追问需要带 conversation_id"}
    active = _active_runs.get(req.conversation_id)
    if active is None:
        return {"status": "no_active_run", "hint": "无在跑会话；请直接 /chat 发新问题"}

    # 1. cancel 当前 task；runner 在 CancelledError 时跳过 channel.close，前端不断流。
    # 这里**不要 shield**——shield 会让 wait_for 超时只 cancel shield future、
    # 原 task 继续跑，结果新一轮和旧 task 同时往 langgraph 同 thread 写状态。
    # 直接 await task：上面已经 cancel() 过了，wait_for 只是等它响应并退出。
    if not active["task"].done():
        active["task"].cancel()
        try:
            await asyncio.wait_for(active["task"], timeout=3)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

    # 2. 同 channel 复用，但要刷新 turn_id——新一轮发问的 step 事件要按新 turn_id 分组
    turn_id = uuid4().hex[:12]
    active["channel"].set_turn_id(turn_id)
    conversation_meta.upsert(user_id, req.conversation_id, req.message)

    # 3. 在同 channel/thread 上启动新一轮——LLM 看完整历史 + 新追问
    new_task = await _start_v2_agent_run(
        active["channel"], user_id, req.conversation_id, req.message,
    )
    active["task"] = new_task
    _register_active_task(req.conversation_id, new_task)

    return {"status": "redirected", "message": req.message}


@router.post("/{user_id}/reset")
async def reset_conversation(user_id: str):
    """开启新对话。前端调此 endpoint 拿到一个新的 conversation_id，
    后续 chat 请求带上它就会进入全新的 langgraph thread。
    旧对话的 sqlite checkpoint 不删除（按 conversation_id 隔离，互不干扰）。"""
    return {"conversation_id": uuid4().hex[:12]}


# ==================== 历史会话管理 endpoints ====================

@router.get("/{user_id}/conversations")
async def list_conversations(user_id: str, archived: bool = False, limit: int = 50):
    """列出该用户的会话（默认未归档），按 last_active_at 倒序。

    每条会话附带累计 metrics（calls / total_tokens 等），sidebar 显示用。
    后端用一次 GROUP BY 拿全部用户的 summary（避免 N+1 查询）。
    """
    convs = conversation_meta.list_for_user(user_id, archived=archived, limit=limit)
    summaries = conversation_metrics.summary_for_user_conversations(user_id)
    for c in convs:
        c["metrics"] = summaries.get(c["conversation_id"]) or {
            "calls": 0, "input_tokens": 0, "output_tokens": 0,
            "total_tokens": 0, "cache_read_tokens": 0, "duration_ms": 0,
        }
    return {"conversations": convs}


@router.get("/{user_id}/conversations/{conversation_id}/metrics")
async def get_conversation_metrics(user_id: str, conversation_id: str):
    """会话内每次 LLM 调用的明细（V2 详情面板画时序图用）。
    V1 前端先不展开，只用聚合 summary，但 endpoint 留好。"""
    return {
        "summary": conversation_metrics.summary_for_conversation(user_id, conversation_id),
        "calls": conversation_metrics.list_for_conversation(user_id, conversation_id),
    }


@router.get("/{user_id}/conversations/{conversation_id}/messages")
async def get_conversation_messages(user_id: str, conversation_id: str):
    """拉取某历史会话的可见消息 + 思考过程事件。

    `messages`: 过滤后的 user/assistant 文本对。**只保留 content 非空的 AIMessage**——
        中间 tool-calling 的 ai message content 通常为空（工具调用走 tool_calls 字段），
        会自动被过滤掉，留下的就是每轮最终的 markdown 回复。

    `events`: 按时间顺序的 step / artifact_updated 事件，每条带 turn_id。
        前端按 turn_id 把事件分组，再插到对应的 user/ai 消息对之间渲染思考分组。

    返回空数组（而非 404）当 thread 没消息——可能是 meta 行 INSERT 了但 LLM
    没真跑过，前端按"空对话"渲染即可。
    """
    thread_id = session_mgr.get_thread_id(user_id, conversation_id)
    store = await _get_v2_store()
    raw_messages = await store.load_messages(thread_id)

    visible_messages: list[dict] = []
    for m in raw_messages:
        cls = type(m).__name__
        content = getattr(m, "content", "")
        # 多模态 content 可能是 list，目前简化只处理 str
        if not isinstance(content, str):
            continue
        if cls == "HumanMessage":
            # 跳过 DateReminder 注入的 <system-reminder> 伪 HumanMessage——
            # 那不是用户真发的消息，前端不该展示
            if "[DATE-REMINDER]" in content:
                continue
            visible_messages.append({"role": "user", "content": content})
        elif cls == "AIMessage" and content.strip():
            # 含 tool_calls 的中间 ai 消息 content 通常为空，自然被过滤
            visible_messages.append({"role": "assistant", "content": content})

    events = conversation_events.list_for_conversation(user_id, conversation_id)
    return {"messages": visible_messages, "events": events}


@router.patch("/{user_id}/conversations/{conversation_id}")
async def rename_conversation(user_id: str, conversation_id: str, req: RenameConversationRequest):
    """重命名会话。失败 = 找不到该 conv（404 风格的 not_found 状态）。"""
    ok = conversation_meta.rename(user_id, conversation_id, req.title)
    if not ok:
        return {"status": "not_found"}
    return {"status": "ok", "conversation": conversation_meta.get(user_id, conversation_id)}


@router.delete("/{user_id}/conversations/{conversation_id}")
async def archive_conversation(user_id: str, conversation_id: str):
    """软删：移到已归档分组（仍在 db，可恢复）。"""
    ok = conversation_meta.archive(user_id, conversation_id)
    if not ok:
        return {"status": "not_found_or_already_archived"}
    return {"status": "archived"}


@router.post("/{user_id}/conversations/{conversation_id}/restore")
async def restore_conversation(user_id: str, conversation_id: str):
    """从归档恢复。"""
    ok = conversation_meta.restore(user_id, conversation_id)
    if not ok:
        return {"status": "not_found"}
    return {"status": "ok"}


@router.delete("/{user_id}/conversations/{conversation_id}/permanent")
async def delete_conversation_permanent(user_id: str, conversation_id: str):
    """硬删：双删 meta + 事件 + ThreadStore messages + tool_outputs 卸盘文件。
    **不可逆**——前端必须先弹"无法恢复"确认框。
    """
    thread_id = session_mgr.get_thread_id(user_id, conversation_id)
    store = await _get_v2_store()
    try:
        await store.delete_thread(thread_id)
    except Exception:
        # store 没这个 thread 也无所谓（meta 可能存在但 LLM 没真跑过）
        pass
    conversation_events.delete_for_conversation(user_id, conversation_id)
    conversation_metrics.delete_for_conversation(user_id, conversation_id)
    conversation_meta.delete_permanent(user_id, conversation_id)
    # 清理 ToolOutputTruncationMiddleware 卸盘的大工具返回值文件——
    # 路径 {data_dir}/{user_id}/tool_outputs/{conv_id}/，整个 conv 子目录 rmtree。
    # 失败容忍：目录不存在 / 权限错误都不影响主删除流程
    import shutil
    tool_outputs_dir = Path(cfg.persistence.data_dir) / user_id / "tool_outputs" / conversation_id
    if tool_outputs_dir.exists():
        try:
            shutil.rmtree(tool_outputs_dir)
        except OSError:
            pass
    return {"status": "deleted"}
