"""SubAgent task 工具——v2 版本（spawn 嵌套 AgentLoop 跑专家角色）。

主 Agent 调 task(subagent_type=..., description=...) 派工给某个专家子 Agent。
子 Agent 跑在**独立的 messages context** 里（不与主 Agent 共享历史），跑完后只
把最终回答字符串作为 ToolMessage 内容返回给主 Agent——这是缓解长 context 的
"主方案"，主 Agent 只看到子 Agent 的结论而非中间过程。

设计要点：

1. **子 Agent 的 messages 完全隔离**：子 loop 自己起一个 AgentLoop 实例，
   initial_messages 不传（空白上下文），只塞一条 HumanMessage(description)。

2. **不允许子 Agent 套娃**：子 loop 的 tools 列表只有 skill_tools，**不**含 task
   工具——避免子→孙→曾孙 递归 spawn。

3. **事件透传**：子 loop 内部的 tool_start/tool_end 事件被转发到 channel 时
   带 subagent={subagent_type} 标记，让前端能嵌套展示"这是子 Agent 干的活"。

4. **子 loop 无 middleware**：DateReminder / IterationGuard / ToolOutputTruncation
   都不装到子 loop——子任务通常较短（< 10 轮），不需要这些保护机制；middleware
   也会让子上下文混入额外消息（如 reminder）干扰专家 prompt。

5. **recursion_limit 严格**：子 loop 默认 30（≈ 15 个 tool 调用）。专家任务超
   过这个步数说明任务过大或卡死，应当回主 Agent 让它重新规划。
"""
from __future__ import annotations

from typing import Any

from agent.config import SubAgentSpec
from agent.loop import AgentConfig, AgentLoop, ToolSpec


def _format_label_for_step(tool_name: str, args: dict) -> str:
    """从 args 拼 tool label 用作 channel send_step 的 msg 字段。"""
    if tool_name == "run_command":
        cmd = (args.get("command") or "").strip()
        return cmd or tool_name
    import json as _json
    try:
        snippet = _json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        snippet = str(args)
    if len(snippet) > 200:
        snippet = snippet[:200] + "..."
    return f"{tool_name} {snippet}"


def make_task_tool(
    subagents: list[SubAgentSpec],
    base_loop_config: AgentConfig,
    skill_tools: list[ToolSpec],
    channel,
    thread_id: str,
) -> ToolSpec | None:
    """构造 `task` ToolSpec。

    Args:
        subagents: cfg.agent.subagents 解析出的列表
        base_loop_config: 主 loop 的配置（model / api_key / base_url 复用）
        skill_tools: 主 loop 的 skill 工具集（子 loop 共用）
        channel: 主 channel——子 loop 事件透传到此
        thread_id: 主 thread——子 loop 用 "{thread_id}__sub_{subagent_type}" 派生

    返回 None 当 subagents 为空（chat.py 检测到 None 就不把 task 加入 tools）。
    """
    if not subagents:
        return None

    subagents_by_name = {s.name: s for s in subagents}

    async def task_func(args: dict) -> str:
        subagent_type = args.get("subagent_type")
        description = args.get("description", "")
        if not subagent_type:
            return "Error: task 调用缺 subagent_type 参数"
        spec = subagents_by_name.get(subagent_type)
        if spec is None:
            return (
                f"Error: 未注册的 subagent_type '{subagent_type}'。"
                f"可用：{list(subagents_by_name.keys())}"
            )

        # 构造子 loop 的配置——继承主 loop 的 model/api_key/base_url，
        # 但 system_prompt 用专家 prompt，recursion_limit 严格收紧
        sub_model = spec.model or base_loop_config.model
        sub_config = AgentConfig(
            model=sub_model,
            api_key=base_loop_config.api_key,
            base_url=base_loop_config.base_url,
            system_prompt=spec.system_prompt,
            recursion_limit=30,  # 子 Agent 步数严格——超出说明任务过大
            interrupt_tools={},  # 子 Agent 不直接 HitL（写操作回主 Agent 拦）
        )

        # 子 loop tools = 只 skill tools（不含 task 自身——禁止递归 spawn）
        sub_loop = AgentLoop(
            config=sub_config,
            tools=list(skill_tools),
            middlewares=[],  # 子 loop 不装 middleware（短任务无需 + 避免污染上下文）
        )

        sub_thread_id = f"{thread_id}__sub_{subagent_type}"
        final_text: str = ""

        async for ev in sub_loop.run(
            user_message=description,
            thread_id=sub_thread_id,
            on_interrupt=None,
        ):
            ev_type = ev["type"]

            # 子 loop 工具事件转发到 channel（带 subagent= 标记，前端嵌套展示）
            if ev_type == "tool_start":
                label = _format_label_for_step(ev["tool_name"], ev.get("args", {}))
                skill_subcmd = None
                if ev["tool_name"] == "run_command":
                    cmd = (ev.get("args", {}).get("command") or "").strip()
                    if cmd:
                        skill_subcmd = cmd.split(maxsplit=1)[0]
                try:
                    await channel.send_step(
                        label, "tool_start",
                        subagent=subagent_type,
                        skill_subcmd=skill_subcmd,
                    )
                except Exception:
                    pass  # 转发失败不阻塞子任务

            elif ev_type == "tool_end":
                label = _format_label_for_step(ev["tool_name"], ev.get("args", {}))
                try:
                    await channel.send_step(label, "tool_end", subagent=subagent_type)
                except Exception:
                    pass

            elif ev_type == "complete":
                final_msg = ev.get("final_message")
                final_text = (final_msg.content or "").strip() if final_msg else ""

            elif ev_type == "recursion_limit":
                final_text = (
                    "[子 Agent 步数超限——任务过大或不收敛，请回主 Agent 拆细任务]"
                )

            elif ev_type == "error":
                exc = ev.get("exception")
                final_text = f"[子 Agent 异常: {type(exc).__name__}: {exc}]"

        return final_text or "[子 Agent 未给出回答]"

    available = list(subagents_by_name.keys())
    schema = {
        "name": "task",
        "description": (
            "派工给专家子 Agent。子 Agent 跑在独立 context（不污染主 Agent 历史），"
            f"跑完只回结论。可用专家：{available}。"
            "只有需要派给专家的复杂任务才调 task；简单查询主 Agent 直接调 run_command。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subagent_type": {
                    "type": "string",
                    "enum": available,
                    "description": "子 Agent 角色名",
                },
                "description": {
                    "type": "string",
                    "description": "派给子 Agent 的具体任务描述（背景 + 目标 + 输出要求）",
                },
            },
            "required": ["subagent_type", "description"],
        },
    }
    return ToolSpec(
        name="task",
        func=task_func,
        schema=schema,
        is_concurrency_safe=False,  # task 不能跟其他 task 并发（避免一次 spawn N 个子 loop 资源失控）
    )
