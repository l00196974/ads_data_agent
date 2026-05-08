"""AgentRunnerV2 - 集成新链路（agent.loop + agent.state + agent.middleware_loop）。

跟 api/channel/runner.py（v1，走 deepagents/langgraph）并存。本 commit 只实现
**核心链路 + channel SSE 事件映射**（含 HitL on_interrupt 回调），但 chat.py
端口还没切到这——P3d 加 feature flag 切流量时才正式启用。

事件映射（loop 事件 → channel 方法）：
- model_start            → channel.send_progress("AI 正在规划任务...")
- token                  → channel.send_token(delta)
- model_end + usage      → channel.send_metrics(usage_payload)
- tool_start             → channel.send_step(label, "tool_start", subagent=None)
- tool_end + output      → channel.send_step(label, "tool_end")
                         + 提取 artifact_ids → channel.send_artifact_updated(...)
- interrupt_resolved     → 由 on_interrupt 回调内部处理（channel.wait_for_confirm）
- complete + final_msg   → save_messages 落库 + 不发额外事件（token 已流完）
- recursion_limit        → channel.send_token("⚠️ 已达工具调用上限...")
- error                  → channel.send_token("\\n[错误] {exception}")

HitL：on_interrupt 回调签名 `(tool_calls) -> bool`——内部调
channel.wait_for_confirm() 阻塞等待用户确认，返回 approve/reject。
WebSSEChannel 走"独立 HTTP 请求 → channel_registry → resolve_confirm"流程，
跟老 runner 一致，channel 抽象层不需要改。
"""
from __future__ import annotations

import logging
import time
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

from agent.loop import AgentConfig, AgentLoop, ToolCall, ToolSpec
from agent.skill_loader import extract_artifact_ids_from_output
from agent.state import ThreadStore
from api.channel.base import BaseChannel
from api.channel.runner import _format_tool_label  # 复用 v1 的 label 格式化

logger = logging.getLogger(__name__)


class AgentRunnerV2:
    """async 主循环 → SSE 事件流的桥（loop 直接版，不走 langgraph）。"""

    def __init__(self, store: ThreadStore):
        self.store = store

    async def run(
        self,
        *,
        channel: BaseChannel,
        thread_id: str,
        user_message: str,
        loop_config: AgentConfig,
        tools: list[ToolSpec],
        middlewares: list[Any],
    ) -> None:
        """主入口：跑一轮 user_message，把 loop 事件翻译成 channel SSE 事件。

        Args:
            channel: SSE 事件目的地（WebSSE / CLI）
            thread_id: 会话标识（用于 ThreadStore load/save + HitL）
            user_message: 用户当前轮输入
            loop_config: AgentLoop 配置（含 system_prompt / model / interrupt_tools）
            tools: ToolSpec 列表（loop_tools 来自 SkillsPackage）
            middlewares: middleware_loop 实例列表
        """
        # 从历史 load——P2 ThreadStore 已经稳定
        initial_messages = await self.store.load_messages(thread_id)

        loop = AgentLoop(config=loop_config, tools=tools, middlewares=middlewares)

        # HitL 回调：channel 提供等待用户决策的能力
        async def on_interrupt(tool_calls: list[ToolCall]) -> bool:
            # 转成 channel.wait_for_confirm 期望的 preview 列表
            preview = [
                {"tool_name": tc.name, "args": tc.args, "tool_call_id": tc.id}
                for tc in tool_calls
            ]
            risk_level = "high"  # 当前简化：拦截到的都按 high 处理；P3d 接精细分级
            tool_name_label = ", ".join(tc.name for tc in tool_calls)
            try:
                approve, _remember = await channel.wait_for_confirm(
                    message=f"AI 想调用 {tool_name_label}，是否批准？",
                    preview=preview,
                    tool_name=tool_name_label,
                    risk_level=risk_level,
                )
            except TypeError:
                # CLI channel 没有 keyword 参数；fallback 简化签名
                approve = await channel.wait_for_confirm(
                    f"AI 想调用 {tool_name_label}，是否批准？", preview,
                )
            return approve

        # 累积本轮所有 messages（complete 时一并写库）
        final_state_messages: list[BaseMessage] = list(initial_messages)
        # 保存 user 输入；loop 内部也会 append 但我们这边算完整快照
        from langchain_core.messages import HumanMessage
        final_state_messages.append(HumanMessage(content=user_message))

        # loop 跑下来收集 ai_message + tool_messages 顺序
        # 但 loop 是 async generator——我们一边消费一边映射 channel 事件
        chat_call_seq = 0
        latest_usage: dict[str, Any] = {}
        latest_ai_message: AIMessage | None = None

        try:
            async for ev in loop.run(
                user_message=user_message,
                thread_id=thread_id,
                on_interrupt=on_interrupt,
                initial_messages=initial_messages,
            ):
                ev_type = ev["type"]
                if ev_type == "model_start":
                    chat_call_seq = ev["call_seq"]
                    await channel.send_progress("AI 正在规划任务...")
                elif ev_type == "token":
                    await channel.send_token(ev["delta"])
                elif ev_type == "model_end":
                    latest_ai_message = ev["ai_message"]
                    latest_usage = ev.get("usage") or {}
                    if latest_usage:
                        await channel.send_metrics({
                            "call_seq": chat_call_seq,
                            "subagent": None,
                            "model": loop_config.model,
                            **latest_usage,
                        })
                elif ev_type == "tool_start":
                    label = _format_tool_label(ev["tool_name"], {"command": ev["args"].get("command", "")})
                    skill_subcmd = None
                    if ev["tool_name"] == "run_command":
                        cmd = (ev["args"].get("command") or "").strip()
                        if cmd:
                            skill_subcmd = cmd.split(maxsplit=1)[0]
                    await channel.send_step(
                        label, "tool_start",
                        subagent=None,
                        skill_subcmd=skill_subcmd,
                    )
                elif ev_type == "tool_end":
                    label = _format_tool_label(ev["tool_name"], {"command": ev.get("args", {}).get("command", "")})
                    await channel.send_step(label, "tool_end")
                    # 提取 artifact_ids 从工具 output（沿用 skill_loader 已有逻辑）
                    artifact_ids = extract_artifact_ids_from_output(ev.get("output", ""))
                    for aid in artifact_ids:
                        await channel.send_artifact_updated(aid, "created")
                elif ev_type == "interrupt_resolved":
                    # on_interrupt 回调里已发了用户决策；channel 端透传一条记录
                    logger.info("interrupt resolved approved=%s thread=%s",
                                ev.get("approved"), thread_id)
                elif ev_type == "complete":
                    latest_ai_message = ev["final_message"]
                    # 不发额外事件——token 已流完
                    break
                elif ev_type == "recursion_limit":
                    await channel.send_token(
                        f"\n\n⚠️ **已达本轮工具调用上限**（约 {loop_config.recursion_limit // 2} 次）。"
                        f"建议：缩小问题范围 / 拆成多个简单问题 / 调高 agent.recursion_limit。"
                    )
                    break
                elif ev_type == "error":
                    await channel.send_token(f"\n[错误] {ev['exception']}")
                    break
        finally:
            # save 不管成功失败——本轮 messages 都落库（含 LLM 调用前的 user message
            # + 实际跑的 ai_message + tool_messages）。loop 内部维护 state.messages
            # 但 generator 跑完后我们没法直接拿 final state；用最后一个 ai_message
            # 简化保留——下次 load 时会带上之前所有持久化消息。
            #
            # P3 期间 ThreadStore 的角色是 append-only，loop 的 state 是单次跑的快照——
            # 持久化"第一手 user message + 最后 ai_message"已能让下一轮 load 出连续历史。
            # tool_messages 也保留（紧跟 ai_message 在 messages 列表里）——但因为本骨架
            # 没拿到 loop 内部完整 state，这里**最低保真**：只 save user + 最后 ai。
            # P3d 优化：让 loop 暴露 final_state hook，把完整 state.messages 传出来。
            if latest_ai_message is not None:
                final_state_messages.append(latest_ai_message)
            try:
                await self.store.save_messages(thread_id, final_state_messages)
            except Exception as e:
                logger.warning("ThreadStore.save_messages failed thread=%s: %s", thread_id, e)
