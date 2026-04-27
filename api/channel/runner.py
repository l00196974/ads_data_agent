from langchain_core.messages import HumanMessage
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from .base import BaseChannel


def _is_meta_tool(name: str) -> bool:
    """Channel-injected IO tools (send_plan / send_to_user / ...) — they
    don't represent business work, so they don't drive task status."""
    return name.startswith("send_")


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

        # Per-run plan state. The runner — not the LLM — owns task status:
        # send_plan sets the tasks; subsequent business-tool start/end events
        # advance the cursor and emit task_update.
        tasks: list[dict] = []
        cursor = 0  # next task awaiting a tool_start

        async def _mark_running() -> None:
            nonlocal cursor
            while cursor < len(tasks) and tasks[cursor].get("status") == "done":
                cursor += 1
            if cursor >= len(tasks):
                return
            task = tasks[cursor]
            if task.get("status") in (None, "pending"):
                task["status"] = "running"
                await channel.send_task_update(task["id"], "running")

        async def _mark_done() -> None:
            nonlocal cursor
            if cursor >= len(tasks):
                return
            task = tasks[cursor]
            if task.get("status") == "running":
                task["status"] = "done"
                await channel.send_task_update(task["id"], "done")
                cursor += 1

        try:
            async for event in agent.astream_events(
                {"messages": [HumanMessage(content=message)]},
                config=config,
                version="v2",
            ):
                kind = event["event"]
                if kind == "on_chat_model_start":
                    # Cover the silent gaps between tool calls — the LLM may
                    # take 3-15s to emit a tool_call, during which the UI
                    # would otherwise look frozen.
                    if not tasks:
                        await channel.send_progress("AI 正在规划任务...")
                    elif cursor < len(tasks):
                        await channel.send_progress("AI 正在准备下一步...")
                    else:
                        await channel.send_progress("AI 正在生成最终分析...")
                elif kind == "on_chat_model_stream":
                    chunk = event["data"].get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        await channel.send_token(chunk.content)
                elif kind == "on_tool_start":
                    tool_name = event.get("name", "tool")
                    input_data = event.get("data", {}).get("input", {})

                    # Capture plan from send_plan input — runner becomes the
                    # source of truth for task status from this point on.
                    if tool_name == "send_plan":
                        raw = input_data.get("tasks", []) if isinstance(input_data, dict) else []
                        tasks = [
                            {"id": t.get("id"), "name": t.get("name", ""), "status": "pending"}
                            for t in raw
                            if isinstance(t, dict) and t.get("id")
                        ]
                        cursor = 0
                        continue

                    if _is_meta_tool(tool_name):
                        continue

                    # Real business tool → mark current task as running.
                    await _mark_running()

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
                    await _mark_done()
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
                        await agent.ainvoke(Command(resume=approve), config=config)
        except GraphInterrupt as e:
            interrupts = e.args[0] if e.args else []
            iv = interrupts[0].value if interrupts else {}
            if not isinstance(iv, dict):
                iv = {"message": str(iv), "preview": []}
            approve = await channel.wait_for_confirm(iv.get("message", ""), iv.get("preview", []))
            await agent.ainvoke(Command(resume=approve), config=config)
        except Exception as e:
            await channel.send_token(f"\n[错误] {e}")
        finally:
            await channel.close()


agent_runner = AgentRunner()
