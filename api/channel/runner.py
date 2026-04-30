import asyncio
import os
from datetime import datetime

from langchain_core.messages import HumanMessage
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from .base import BaseChannel
from .default_tools import make_default_tools
from agent.config import load_config
from agent.skill_loader import extract_artifact_ids_from_output
from agent.user_space import UserSpace


def _is_meta_tool(name: str) -> bool:
    """Default framework tools (send_plan / send_to_user / ...) — they
    represent display/output actions, not business work, so they don't
    produce step events."""
    return name.startswith("send_")


def _format_tool_label(tool_name: str, input_data) -> str:
    """前端展示用的工具名 + 实际参数。

    `run_command` 是 SKILL.md 系统统一的派发器，工具名本身不可读——直接把
    LLM 传给它的完整 CLI 命令串露出来（`query-metrics --metrics revenue
    --start-date 2026-03-27 ...`），用户看到具体在跑什么、传了什么参数，
    不再黑盒。

    其他业务工具：原名 + 主要参数（用 JSON 紧凑表示，截断到 200 字符）。"""
    if not isinstance(input_data, dict) or not input_data:
        return tool_name

    if tool_name == "run_command":
        cmd = input_data.get("command", "")
        if isinstance(cmd, str) and cmd.strip():
            return cmd.strip()
        return tool_name

    # 通用工具：把全部 input 紧凑序列化展出，让 LLM 传的参数透明可见
    try:
        import json
        snippet = json.dumps(input_data, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        snippet = str(input_data)
    if len(snippet) > 200:
        snippet = snippet[:200] + "..."
    return f"{tool_name} {snippet}"


async def _resume_safely(agent, approve: bool, config: dict) -> None:
    """resume 期间收到 cancel 不能直接打断 ainvoke——会让 langgraph 写到一半就停，
    checkpointer 留下半完成 state，下一轮 restart 读到 corrupt 状态。
    把 ainvoke 包成独立 task + shield，cancel 时也等它跑完再 raise。"""
    inner = asyncio.create_task(agent.ainvoke(Command(resume=approve), config=config))
    try:
        await asyncio.shield(inner)
    except asyncio.CancelledError:
        try:
            await inner
        except Exception:
            pass
        raise


class AgentRunner:
    async def run(
        self,
        channel: BaseChannel,
        message: str,
        build_agent_fn,
        config: dict,
    ) -> None:
        # 默认工具集（send_plan 等）由 framework 提供，闭包绑定到当前 channel。
        # agent 视角下它们是稳定的"系统工具"；channel 切换时只换底层 IO 实现。
        extra_tools = make_default_tools(channel)
        agent = build_agent_fn(extra_tools=extra_tools)

        # 解析 thread_id 拿 user_id（thread_id = "{user_id}_{conversation_id}"）
        thread_id = config.get("configurable", {}).get("thread_id", "")
        user_id = thread_id.split("_", 1)[0] if thread_id else "anon"
        cfg = load_config()
        user_space = UserSpace(user_id, cfg.persistence.data_dir)

        # 仅用于 progress 文案区分"规划中"vs"执行中"——状态推断已经下放给前端。
        business_tools_started = 0

        # 被 cancel 时不要关 channel——chat handler 在追问场景下要复用它继续推 SSE。
        # 只有正常结束 / 普通异常时才发 done 事件。
        should_close = True
        try:
            async for event in agent.astream_events(
                {"messages": [HumanMessage(content=message)]},
                config=config,
                version="v2",
            ):
                kind = event["event"]
                if kind == "on_chat_model_start":
                    # Cover the silent gap before the LLM emits its first tool_call.
                    msg = "AI 正在规划任务..." if business_tools_started == 0 else "AI 正在准备下一步..."
                    await channel.send_progress(msg)
                elif kind == "on_chat_model_stream":
                    chunk = event["data"].get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        await channel.send_token(chunk.content)
                elif kind == "on_tool_start":
                    tool_name = event.get("name", "tool")
                    if _is_meta_tool(tool_name):
                        continue
                    business_tools_started += 1
                    # 为本次工具调用预生成 artifact_id + dir，注入 env 让 skill 选择是否使用。
                    # 用进程级 os.environ：单 worker 单进程下 LLM 工具调用串行执行，
                    # 时序上同一时刻只有一个 subprocess 继承 env，安全。多 worker 部署
                    # 时这套方案不可靠（见 known-issues），未来改用 subprocess 显式 env。
                    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
                    artifact_id = f"{timestamp}-report"
                    artifact_dir = user_space.artifacts_dir / artifact_id
                    os.environ["ADS_AGENT_ARTIFACT_DIR"] = str(artifact_dir)
                    os.environ["ADS_AGENT_ARTIFACT_ID"] = artifact_id
                    os.environ["ADS_AGENT_USER_ID"] = user_id
                    input_data = event.get("data", {}).get("input", {})
                    step_msg = _format_tool_label(tool_name, input_data)
                    await channel.send_step(step_msg, "tool_start")
                elif kind == "on_tool_end":
                    tool_name = event.get("name", "tool")
                    if _is_meta_tool(tool_name):
                        continue
                    input_data = event.get("data", {}).get("input", {})
                    await channel.send_step(_format_tool_label(tool_name, input_data), "tool_end")
                    # 工具结束后从 output 抽 artifact_ids（用 sentinel 解析），推 SSE
                    output = event.get("data", {}).get("output", "")
                    if isinstance(output, str):
                        for artifact_id in extract_artifact_ids_from_output(output):
                            await channel.send_artifact_updated(artifact_id, "created")
                elif kind == "on_chain_end":
                    outputs = event.get("data", {}).get("output", {})
                    if isinstance(outputs, dict) and "__interrupt__" in outputs:
                        interrupts = outputs["__interrupt__"]
                        iv = interrupts[0].value if interrupts else {}
                        if not isinstance(iv, dict):
                            iv = {"message": str(iv), "preview": []}
                        approve = await channel.wait_for_confirm(
                            iv.get("message", ""), iv.get("preview", [])
                        )
                        await _resume_safely(agent, approve, config)
        except asyncio.CancelledError:
            should_close = False  # chat handler 会在同一 channel 上接着跑新一轮
            raise
        except GraphInterrupt as e:
            interrupts = e.args[0] if e.args else []
            iv = interrupts[0].value if interrupts else {}
            if not isinstance(iv, dict):
                iv = {"message": str(iv), "preview": []}
            approve = await channel.wait_for_confirm(iv.get("message", ""), iv.get("preview", []))
            await _resume_safely(agent, approve, config)
        except Exception as e:
            await channel.send_token(f"\n[错误] {e}")
        finally:
            if should_close:
                await channel.close()


agent_runner = AgentRunner()
