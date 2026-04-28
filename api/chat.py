import asyncio
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from agent.config import load_config
from agent.core import build_agent
from agent.session import SessionManager
from agent.skill_loader import load_md_skills
from agent.user_space import UserSpace
from api.channel import (
    ExternallyConfirmable,
    WebSSEChannel,
    agent_runner,
    channel_registry,
)
from api.models import AppendRequest, ChatRequest, ConfirmRequest, SkillCreateRequest

router = APIRouter()
session_mgr = SessionManager()
cfg = load_config()

# 当前活跃的 agent 运行（按 conversation_id 索引）。追问场景下：先 cancel 当前 task，
# 在同一 channel + thread 上跑新一轮——LLM 看到完整历史 + 新指令。
_active_runs: dict[str, dict] = {}

PLAN_INSTRUCTION = """## 执行流程（严格遵守）

收到用户问题后，按以下顺序执行：

1. **第一步调用 `send_plan`**，声明本次将要执行的任务（2-5 个）
   - 每项格式：`{"id": "t1", "name": "任务描述"}`
   - id 按 `t1, t2, t3...` 顺序，name 简短中文（≤20 字）
   - **plan 中只包含真正的业务工具调用**（即下面"业务技能"段落里 SKILL.md 文档定义的 `run_command` 子命令）
   - **不要**把 `send_to_user` / `send_plan` 这种展示/规划动作列为任务——它们是输出指令，不是计算步骤
   - **任务数量必须等于你接下来要调用的业务工具数量**——每个任务对应一次工具调用，按声明的顺序执行
   - 任务的 running / done 状态由系统根据工具执行自动推断，**你不需要也无法手动上报状态**

2. **按 plan 顺序依次调用业务工具**（即 `run_command`，每次传一条 CLI 命令），一次一个

3. **业务工具全部执行完毕后，直接以 Markdown 文本输出最终分析结果**——系统会把你的文字逐 token 流式推送到前端。

## 中间进度提示

执行过程中如需告知用户进展，**只能**使用：

```
send_to_user(action="progress", content="正在查询数据...")
```

`progress` 显示在顶部状态栏，不进消息流。

**禁止在工具调用之间输出散文或"思考"**——保持静默直到下一个工具调用，避免类似"我现在要查询..."、"接下来..."这样的中间话语进入消息流。所有自然语言只能出现在最终分析阶段（步骤 3）。

## 图表展示

需要展示图表时调用：

```
send_to_user(action="chart", chart_type="line", title="...", x_data=[...], series=[...])
```

`chart_type`: `bar` | `line` | `pie` | `scatter`

## 最终输出顺序

所有业务工具执行完成后，按以下顺序输出最终结果：

1. **直接输出 Markdown 文本** — 数据结论和分析摘要（会逐 token 流式推送）
2. （可选）一个或多个 `send_to_user(action="chart", ...)` — 图表
3. **直接输出 Markdown 文本** — 引导追问（如"如需下钻分析某渠道..."），**只能放在最后**

## 标准示例

用户问"查询最近7天点击数据并生成折线图"，假设你看到 SKILL.md 注册了 `query-metrics` 子命令：

1. `send_plan(tasks=[{"id":"t1","name":"查询最近7天点击"},{"id":"t2","name":"生成趋势折线图"}])`
2. `send_to_user(action="progress", content="正在查询数据...")`
3. `run_command(command="query-metrics --metrics click --start-date 2026-04-21 --end-date 2026-04-27 --time-mode event --dimensions day")`
4. **直接输出**：
```
**最近7天点击趋势**
- 总点击：xxx
- 日均：xxx
```
5. `send_to_user(action="chart", chart_type="line", title="点击趋势", x_data=[...], series=[...])`
6. **直接输出**："如需下钻分析某渠道，请告诉我。"

send_plan 是计划展示，不是询问用户，无需等待用户回复。
"""


def _make_build_fn(user_id: str):
    us = UserSpace(user_id, cfg.persistence.data_dir)
    md_pkg = load_md_skills(cfg.skills.md_dir, us.skills_dir)

    # SKILL.md 全文塞进 system prompt，LLM 直接看 CLI 用法
    system_prompt = PLAN_INSTRUCTION + "\n\n" + us.get_agents_md()
    if md_pkg.prompt_addition:
        system_prompt += "\n\n" + md_pkg.prompt_addition

    def _build(extra_tools=None):
        # 业务工具集 = SKILL.md 加载出的 run_command 通用工具（如果有 skill 包）
        skills = list(md_pkg.tools)
        return build_agent(
            user_id=user_id,
            system_prompt=system_prompt,
            skills=skills,
            interrupt_on=cfg.agent.interrupt_on,
            cfg=cfg,
            extra_tools=extra_tools,
        )

    return _build


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
    channel = WebSSEChannel(user_id, session_id, queue)
    channel_registry.register(session_id, channel)

    build_fn = _make_build_fn(user_id)
    config = session_mgr.get_config(user_id, conversation_id)

    task = asyncio.create_task(agent_runner.run(channel, req.message, build_fn, config))
    _active_runs[conversation_id] = {
        "task": task,
        "channel": channel,
        "build_fn": build_fn,
        "config": config,
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
    channel.resolve_confirm(req.action == "approve")
    return {"status": "ok"}


@router.post("/{user_id}/cancel")
async def cancel(user_id: str):
    return {"status": "cancelled"}


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

    # 2. 在同 channel/thread 上启动新一轮——LLM 看完整历史 + 新追问
    new_task = asyncio.create_task(
        agent_runner.run(active["channel"], req.message, active["build_fn"], active["config"])
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
