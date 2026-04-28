import asyncio
from uuid import uuid4

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langgraph.types import Command

from agent.config import load_config
from agent.core import build_agent
from agent.session import SessionManager
from agent.user_space import UserSpace
from api.channel import WebSSEChannel, agent_runner, channel_registry
from api.models import AppendRequest, ChatRequest, ConfirmRequest, SkillCreateRequest
from skills.system import SYSTEM_SKILLS

router = APIRouter()
session_mgr = SessionManager()
cfg = load_config()

PLAN_INSTRUCTION = """## 执行流程（严格遵守）

收到用户问题后，按以下顺序执行：

1. **第一步调用 `send_plan`**，声明本次将要执行的任务（2-5 个）
   - 每项格式：`{"id": "t1", "name": "任务描述"}`
   - id 按 `t1, t2, t3...` 顺序，name 简短中文（≤20 字）
   - **plan 中只包含真正的业务工具调用**（如 query_campaign_report / analyze_budget / detect_anomaly / build_chart），**不要把 `send_to_user`、`send_plan` 这种展示/规划动作列为任务**——它们是输出指令，不是计算步骤
   - **任务数量必须等于你接下来要调用的业务工具数量**——每个任务对应一次业务工具调用，按声明的顺序执行
   - 任务的 running / done 状态由系统根据工具执行自动推断，**你不需要也无法手动上报状态**

2. **按 plan 顺序依次调用业务工具**（如 query_campaign_report / build_chart / analyze_budget），一次一个

3. **所有业务工具完成后，调用 `send_to_user` 展示最终结果**

## 中间进度提示

执行中需要告知用户细节进度时，**只能**使用：

```
send_to_user(action="progress", content="正在生成折线图...")
```

**禁止**把进度信息放进 `action="text"`：
- ❌ send_to_user(action="text", content="正在分析数据...")
- ❌ 任何"正在..."/"已获取..."/"接下来..."的 text 输出

`progress` 显示在顶部 tips，不进对话流。

## 最终输出顺序

所有 `send_to_user(action="text"|"chart")` 必须按：

1. `action="text"` — 数据结论和分析摘要
2. `action="chart"` — 图表（可多个）
3. `action="text"` — 引导追问（如"如需下钻分析某渠道..."），**只能最后**

## 标准示例

用户问"查询最近7天曝光数据并生成折线图"：

```
send_plan(tasks=[
  {"id": "t1", "name": "查询最近7天数据"},
  {"id": "t2", "name": "生成趋势折线图"},
])

send_to_user(action="progress", content="正在查询数据库...")
query_campaign_report(days=7)

send_to_user(action="progress", content="正在构建图表配置...")
build_chart(data=..., chart_type="line")

send_to_user(action="text", content="**分析结果**\n- 总曝光：xxx")
send_to_user(action="chart", ...)
send_to_user(action="text", content="如需下钻分析某渠道，请告诉我。")
```

send_plan 是计划展示，不是询问用户，无需等待用户回复。
"""


def _make_build_fn(user_id: str):
    us = UserSpace(user_id, cfg.persistence.data_dir)
    system_prompt = PLAN_INSTRUCTION + "\n\n" + us.get_agents_md()

    def _build(extra_tools=None):
        return build_agent(
            user_id=user_id,
            system_prompt=system_prompt,
            skills=SYSTEM_SKILLS,
            interrupt_on=cfg.agent.interrupt_on,
            cfg=cfg,
            extra_tools=extra_tools,
        )

    return _build


@router.post("/{user_id}")
async def chat(user_id: str, req: ChatRequest):
    # ephemeral session_id for HitL confirm lookup; conversation_id 决定 langgraph thread。
    session_id = f"{user_id}_{uuid4().hex[:8]}"
    conversation_id = req.conversation_id or uuid4().hex[:12]
    queue: asyncio.Queue = asyncio.Queue()
    channel = WebSSEChannel(user_id, session_id, queue)
    channel_registry.register(session_id, channel)

    asyncio.create_task(
        agent_runner.run(
            channel,
            req.message,
            _make_build_fn(user_id),
            session_mgr.get_config(user_id, conversation_id),
        )
    )

    async def event_generator():
        while True:
            chunk = await queue.get()
            yield chunk
            if "event: done" in chunk or "event: error" in chunk:
                channel_registry.unregister(session_id)
                break

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
    channel = channel_registry.get(req.session_id)
    if not channel or not isinstance(channel, WebSSEChannel):
        return {"status": "not_found"}
    channel.resolve_confirm(req.action == "approve")
    return {"status": "ok"}


@router.post("/{user_id}/cancel")
async def cancel(user_id: str):
    return {"status": "cancelled"}


@router.post("/{user_id}/append")
async def append_message(user_id: str, req: AppendRequest):
    build_fn = _make_build_fn(user_id)
    agent = build_fn()
    config = session_mgr.get_config(user_id, req.conversation_id)
    agent.invoke(Command(resume={"additional_context": req.message}), config=config)
    return {"status": "appended", "message": req.message}


@router.post("/{user_id}/reset")
async def reset_conversation(user_id: str):
    """开启新对话。前端调此 endpoint 拿到一个新的 conversation_id，
    后续 chat 请求带上它就会进入全新的 langgraph thread。
    旧对话的 sqlite checkpoint 不删除（按 conversation_id 隔离，互不干扰）。"""
    return {"conversation_id": uuid4().hex[:12]}
