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

1. **首先调用 `send_plan` 工具**声明本次任务计划。

   - tasks 列表 2-5 项，每项 `{"id": "t1", "name": "..."}`
   - name 用简短中文，≤20 字（如"查询点击数据"、"汇总并生成图表"）
   - id 顺序为 t1, t2, t3...
   - **只在最开始调一次**，不要反复调用、不要中途追加

2. **按计划逐项调用业务工具**（即 `run_command`，每次传一条 CLI 命令），
   一次一个，不输出中间散文。

   **重要**：`run_command` 的 `command` 参数是**子命令名 + 参数**，**不要**前缀 skill 包名。
   即使用户说"调用 demo-artifact-writer 的 write-demo-report"，你也应该传：

       ✅ `run_command(command="write-demo-report --title '测试'")`
       ❌ `run_command(command="demo-artifact-writer write-demo-report --title '测试'")`

   已注册的子命令清单见下方"业务技能"段——`run_command` 只接受这些子命令名，
   传 skill 包名会得到 `Error: 未注册的命令` 错误。

3. **业务工具全部执行完毕后，直接以 Markdown 文本输出最终分析结果**——
   系统会把你的文字逐 token 流式推送到前端。

4. **（可选）需要展示图表时，把图表作为 markdown 代码块嵌进文本里**，
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

5. **（可选）最后再输出一行 Markdown 引导追问**（如"如需下钻分析某渠道..."）。

## 不要做的事

- ❌ 不要在工具调用之前 / 之间输出"我现在要查询..."、"接下来..."这种散文
- ❌ 不要重复声明你要做什么——直接做即可。前端会自动展示工具执行进度
- ❌ 不要调用任何额外的"进度 / 图表 / 摘要"性质的工具——除 `send_plan` 外，
   一律通过最终 Markdown 文本输出，避免无谓的 LLM round trip

## 标准示例

用户问"查询最近7天点击数据并生成折线图"，假设 SKILL.md 注册了 `query-metrics`：

1. `send_plan(tasks=[{"id":"t1","name":"查询点击数据"},{"id":"t2","name":"生成趋势图"}])`
2. `run_command(command="query-metrics --metrics click --start-date 2026-04-21 --end-date 2026-04-27 --time-mode event --dimensions day")`
3. 直接输出 Markdown：

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
