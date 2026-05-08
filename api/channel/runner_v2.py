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
            # HitL 精细分级：从 loop_config.interrupt_tools 拿每个工具的 risk
            # 多工具一轮 emit 时取最高级（任一 high 整批按 high 处理）
            risks = [
                loop_config.interrupt_tools.get(tc.name, {}).get("risk", "medium")
                for tc in tool_calls
            ]
            risk_level = "high" if "high" in risks else "medium"
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

        # final_messages 由 loop 在 complete/recursion_limit/error 三个出口带上来
        # （含 user / system-reminder / 多轮 ai+tool）。runner 拿这个直接落库，
        # 不用自己拼装顺序。
        final_messages_from_loop: list[BaseMessage] | None = None

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
                    final_messages_from_loop = ev.get("final_messages")
                    # 不发额外事件——token 已流完
                    break
                elif ev_type == "recursion_limit":
                    await channel.send_token(
                        f"\n\n⚠️ **已达本轮工具调用上限**（约 {loop_config.recursion_limit // 2} 次）。"
                        f"建议：缩小问题范围 / 拆成多个简单问题 / 调高 agent.recursion_limit。"
                    )
                    final_messages_from_loop = ev.get("final_messages")
                    break
                elif ev_type == "error":
                    await channel.send_token(f"\n[错误] {ev['exception']}")
                    final_messages_from_loop = ev.get("final_messages")
                    break
        finally:
            # 持久化策略：loop 在 complete/recursion_limit/error 三个出口都吐
            # final_messages 完整快照（含 user / system-reminder / 多轮 ai+tool）。
            # 这里走 overwrite_messages 而不是 append——因为 final_messages 包含的
            # 是「load 出来的历史 + 本轮 + middleware 注入的 reminder」整段，append
            # 会跟历史已存的 messages 重复。
            #
            # 注意：DateReminder 注入的 <system-reminder> 也会进 final_messages——
            # 我们故意保留落库（让下次 load 看到上轮的 reminder marker），下轮
            # DateReminder 会按 marker 删掉换最新。这样不会无限堆积。
            if final_messages_from_loop is not None:
                try:
                    await self.store.overwrite_messages(thread_id, final_messages_from_loop)
                except Exception as e:
                    logger.warning("ThreadStore.overwrite_messages failed thread=%s: %s", thread_id, e)
            # 关闭 channel 以让 SSE 流给客户端发 'done' 事件——跟老 runner 行为对齐
            try:
                await channel.close()
            except Exception as e:
                logger.warning("channel.close failed: %s", e)
