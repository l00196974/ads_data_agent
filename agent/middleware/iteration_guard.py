"""IterationGuardMiddleware：步数感知软警告。

为什么需要：
  LangGraph 的 recursion_limit 是硬上限——撞了直接抛 GraphRecursionError，用户体验差。
  这个 middleware 在接近上限时往 messages 里**注入 system 警告**，让 LLM 提前感知压力
  自我收敛，避免硬撞墙。比单纯加大 recursion_limit 更可控。

策略：
  - 数 state.messages 中 ToolMessage 数量作为"已用步数"代理（每次工具调用 ≈ 2 graph steps）
  - 70% 阈值（默认 ~52 次工具调用）：软提示——"如果信息够了就尽快收敛"
  - 90% 阈值（默认 ~67 次）：紧急提示——"再调 1-2 次就必须给最终回答"
  - 同一 marker 在 messages 里出现过就不再注入（避免每步 noisy 警告）

为什么用 ToolMessage 数而不是 messages 总数：
  ToolMessage 数最直接反映"调了多少次工具"——LLM 自我评估时这是它能感知的工具循环次数。
  消息总数还包括 system / assistant / human，跟"工具调用次数"语义不一致。
"""
from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import SystemMessage, ToolMessage
from langgraph.runtime import Runtime


class IterationGuardMiddleware(AgentMiddleware):
    """步数感知警告：在 messages 里注入 system 提示让 LLM 提前收敛。"""

    # 标记字符串——用于检测"是否已注入过同级警告"。LLM 看到这两个串没意义，
    # 但它们对人类调试友好（grep 立刻定位）。
    SOFT_MARKER = "[ITER-GUARD-SOFT]"
    URGENT_MARKER = "[ITER-GUARD-URGENT]"

    def __init__(
        self,
        recursion_limit: int = 150,
        soft_pct: float = 0.7,
        urgent_pct: float = 0.9,
    ):
        self.recursion_limit = recursion_limit
        # graph step → 工具调用次数：粗算除 2（一次工具调用 ≈ 1 AI msg + 1 ToolMessage）
        # 实测 deepagents 默认还有 plan / summarization 等 step，比例略高于 2，所以这里用 2 偏保守
        # （阈值会比真实"撞墙剩余空间"略小一点，更早警告，更安全）
        self.tool_call_limit = recursion_limit // 2
        self.soft_threshold = max(int(self.tool_call_limit * soft_pct), 1)
        self.urgent_threshold = max(int(self.tool_call_limit * urgent_pct), 2)

    def _has_marker(self, messages: list, marker: str) -> bool:
        """messages 里出现过这个 marker 就当作"已注入过"——避免重复打扰 LLM。"""
        for m in messages:
            content = getattr(m, "content", None)
            if isinstance(content, str) and marker in content:
                return True
        return False

    def before_model(self, state: AgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if not messages:
            return None

        tool_count = sum(1 for m in messages if isinstance(m, ToolMessage))

        # 三层判定：达到 urgent 阈值后**只**走 urgent 分支（即使 URGENT 已注入也不回退到 soft，
        # 否则会出现"已警告 URGENT 后还接着推 SOFT"的倒退）
        if tool_count >= self.urgent_threshold:
            if self._has_marker(messages, self.URGENT_MARKER):
                return None
            warning = (
                f"{self.URGENT_MARKER} ⚠️ 系统提醒：你已调用工具 {tool_count} 次，"
                f"接近本次会话工具调用上限（约 {self.tool_call_limit} 次）。"
                f"再调用至多 1-2 次工具就必须给出最终回答——基于已收集的数据"
                f"做出最佳推断，**不要再继续探索新维度**。"
                f"如果信息确实不足，直接告知用户并请求缩小问题范围。"
            )
            return {"messages": [SystemMessage(content=warning)]}

        if tool_count >= self.soft_threshold:
            if self._has_marker(messages, self.SOFT_MARKER):
                return None
            warning = (
                f"{self.SOFT_MARKER} 系统提示：你已调用工具 {tool_count} 次"
                f"（上限约 {self.tool_call_limit} 次，已用 70%）。"
                f"如果信息已足够回答用户问题，请尽快收敛——避免不必要的额外查询。"
                f"复杂诊断场景请优先复用已有数据，再考虑新查询。"
            )
            return {"messages": [SystemMessage(content=warning)]}

        return None
