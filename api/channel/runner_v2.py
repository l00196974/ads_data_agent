"""AgentRunnerV2 - 集成新链路（agent.loop + agent.state + agent.middleware）。

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

from agent.messages import AIMessage, BaseMessage

from agent.loop import AgentConfig, AgentLoop, ToolCall, ToolSpec
from agent.skill_loader import extract_artifact_ids_from_output
from agent.state import ThreadStore
from api.channel.base import BaseChannel

logger = logging.getLogger(__name__)


# 子 Agent 角色中文标签——v2 暂未接 SubAgent，但 _format_tool_label 见到 task 工具
# 时仍按 v1 逻辑显示"派给 XXX"避免 LLM 输出错乱
_SUBAGENT_CN_LABELS = {
    "data-fetcher": "取数员",
    "data-analyst": "数据分析师",
    "issue-diagnostician": "问题诊断师",
    "general-purpose": "通用助手",
}


def _enrich_metrics(
    usage: dict[str, Any],
    *,
    call_seq: int,
    subagent: str | None,
    model: str,
    context_trigger: int,
) -> dict[str, Any]:
    """把 loop 给的原始 usage（含 ttft_ms / duration_ms / cache_read_tokens）补全
    成前端 metrics-bar 需要的完整 schema：算 TPS、context_used_pct、context_trigger。

    - **tps（端到端）** = output_tokens / 总耗时秒数。这是用户主观感受的速度：
      "等 N 秒拿到 M token，平均 M/N t/s"。
      之前算的是 output_tokens / (duration - ttft) = "首 token 后生成阶段速度"，
      thinking 模式下首 token 延迟很大，gen 阶段很短，会得到 100+ t/s 但实际
      用户体感只有 10 t/s，对不上。端到端口径跟用户感觉一致。
      想看"生成阶段瞬时速度"用 output_tokens / (duration_ms - ttft_ms)，前端可
      自己算。
    - context_used_pct: 仅主 Agent（subagent=None）算，子 Agent 跑独立 context
      跟主 context bar 无关
    """
    out = {
        "call_seq": call_seq,
        "subagent": subagent,
        "model": model,
        **usage,
    }
    # 端到端 TPS = output_tokens / 总耗时秒数
    out_tokens = usage.get("output_tokens") or 0
    duration_ms = usage.get("duration_ms") or 0
    if out_tokens > 0 and duration_ms > 0:
        out["tps"] = out_tokens * 1000 / duration_ms
    # 上下文使用率——只主 Agent 算，子 Agent 不进度条
    if subagent is None and usage.get("input_tokens") and context_trigger:
        out["context_used_pct"] = usage["input_tokens"] * 100.0 / context_trigger
        out["context_trigger"] = context_trigger
    return out


def _format_tool_label(tool_name: str, input_data) -> str:
    """前端展示用的工具名 + 实际参数（与 v1 行为完全一致）。

    - run_command: 把完整 CLI 命令串露出来（`query-metrics --metrics ...`）
    - task: 显示"派给 XXX（中文角色名）"
    - 通用工具：原名 + JSON 紧凑参数（截 200 字符）
    """
    if not isinstance(input_data, dict) or not input_data:
        return tool_name

    if tool_name == "run_command":
        cmd = input_data.get("command", "")
        if isinstance(cmd, str) and cmd.strip():
            return cmd.strip()
        return tool_name

    if tool_name == "task":
        st = input_data.get("subagent_type", "")
        cn = _SUBAGENT_CN_LABELS.get(st, st or "子 Agent")
        return f"🤖 派给 {cn}"

    import json as _json
    try:
        snippet = _json.dumps(input_data, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        snippet = str(input_data)
    if len(snippet) > 200:
        snippet = snippet[:200] + "..."
    return f"{tool_name} {snippet}"


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
        context_trigger: int = 80000,
    ) -> None:
        """主入口：跑一轮 user_message，把 loop 事件翻译成 channel SSE 事件。

        Args:
            channel: SSE 事件目的地（WebSSE / CLI）
            thread_id: 会话标识（用于 ThreadStore load/save + HitL）
            user_message: 用户当前轮输入
            loop_config: AgentLoop 配置（含 system_prompt / model / interrupt_tools）
            tools: ToolSpec 列表（来自 SkillsPackage.tools）
            middlewares: middleware 实例列表
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
                        await channel.send_metrics(
                            _enrich_metrics(
                                latest_usage,
                                call_seq=chat_call_seq,
                                subagent=None,
                                model=loop_config.model,
                                context_trigger=context_trigger,
                            )
                        )
                elif ev_type == "tool_call_arriving":
                    # LLM 流到了工具 name 但 args 还在累积——先发占位条，让前端
                    # 在首问 LLM 慢吞吞拼 args JSON 的几秒内就有可见反馈。msg 只传
                    # 工具名（不拼"准备中..."后缀），前端用 entry-arriving 样式
                    # 通过 CSS ::after 加装饰文字——文案/视觉调整不用回后端。
                    # 等 tool_start 时按 tool_call_id 把同一条目升级为带完整
                    # CLI 命令的"执行中..."。
                    await channel.send_step(
                        ev["tool_name"],
                        "tool_arriving",
                        subagent=None,
                        tool_call_id=ev["tool_call_id"],
                    )
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
                        tool_call_id=ev.get("tool_call_id"),
                    )
                elif ev_type == "tool_end":
                    label = _format_tool_label(ev["tool_name"], {"command": ev.get("args", {}).get("command", "")})
                    await channel.send_step(
                        label, "tool_end",
                        tool_call_id=ev.get("tool_call_id"),
                    )
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
                    # 空答兜底：content 仅含空白（如 "\n\n"）、又没工具调用 → 给用户明确
                    # 提示而不是静默关闭。常见原因：
                    # - finish_reason="content_filter"（模型拒答）
                    # - 推理模型把内容全输到 reasoning_content（loop 已尝试 fallback；
                    #   若 fallback 也失败说明 reasoning 也是空）
                    # - 模型 endpoint 配置问题
                    final_content_raw = getattr(latest_ai_message, "content", "") or ""
                    final_content = final_content_raw if isinstance(final_content_raw, str) else str(final_content_raw)
                    final_tool_calls = getattr(latest_ai_message, "tool_calls", None) or []
                    if not final_content.strip() and not final_tool_calls:
                        await channel.send_token(
                            "\n\n⚠️ LLM 返回空响应（无内容、无工具调用）。可能原因：\n"
                            "- 模型触发 content_filter 拒答\n"
                            "- 模型 endpoint 配置异常（推理模式 / chat_template 问题）\n"
                            "- 上下文超限被截断\n"
                            "看后端日志 `model_end: finish_reason=...` 一行可定位。"
                        )
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
