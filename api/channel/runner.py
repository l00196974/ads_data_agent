import asyncio

from langchain_core.messages import HumanMessage
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from .base import BaseChannel


def _is_meta_tool(name: str) -> bool:
    """Channel-injected IO tools (send_plan / send_to_user / ...) — they
    represent display/output actions, not business work, so they don't
    produce step events."""
    return name.startswith("send_")


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
        skill = channel.get_skill()
        extra_tools = skill if isinstance(skill, list) else [skill]
        agent = build_agent_fn(extra_tools=extra_tools)

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
                    input_data = event.get("data", {}).get("input", {})
                    step_msg = tool_name
                    if isinstance(input_data, dict) and input_data:
                        first_key = next(iter(input_data.keys()))
                        if isinstance(input_data[first_key], str) and len(input_data[first_key]) < 30:
                            step_msg += f": {input_data[first_key]}"
                    await channel.send_step(step_msg, "tool_start")
                elif kind == "on_tool_end":
                    tool_name = event.get("name", "tool")
                    if _is_meta_tool(tool_name):
                        continue
                    await channel.send_step(tool_name, "tool_end")
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
