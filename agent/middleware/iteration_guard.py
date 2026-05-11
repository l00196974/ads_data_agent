"""IterationGuard for agent.loop.AgentLoop（loop 版，不依赖 langchain.agents）。

数 state.messages 中 ToolMessage 数量作为"已用步数"代理。70% 软提示，90% 紧急
提示。同 marker 已注入则不重复。

跟 langgraph 老版本逻辑等价，差异仅：
  - 不继承 langchain.agents.middleware.types.AgentMiddleware
  - 返回 AgentState（含 append 后的新 messages 列表）而非 dict-update
"""
from __future__ import annotations

from typing import Any

from agent.messages import SystemMessage, ToolMessage

from agent.loop import AgentState


class IterationGuard:
    """步数感知警告 middleware (loop 版)。"""

    SOFT_MARKER = "[ITER-GUARD-SOFT]"
    URGENT_MARKER = "[ITER-GUARD-URGENT]"

    def __init__(
        self,
        recursion_limit: int = 150,
        soft_pct: float = 0.7,
        urgent_pct: float = 0.9,
    ):
        self.recursion_limit = recursion_limit
        self.tool_call_limit = recursion_limit // 2
        self.soft_threshold = max(int(self.tool_call_limit * soft_pct), 1)
        self.urgent_threshold = max(int(self.tool_call_limit * urgent_pct), 2)

    def _has_marker(self, messages: list, marker: str) -> bool:
        for m in messages:
            content = getattr(m, "content", None)
            if isinstance(content, str) and marker in content:
                return True
        return False

    def before_model(self, state: AgentState) -> AgentState | None:
        if not state.messages:
            return None

        tool_count = sum(1 for m in state.messages if isinstance(m, ToolMessage))

        # urgent 优先
        if tool_count >= self.urgent_threshold:
            if self._has_marker(state.messages, self.URGENT_MARKER):
                return None
            warning = (
                f"{self.URGENT_MARKER} ⚠️ 系统提醒：你已调用工具 {tool_count} 次，"
                f"接近本次会话工具调用上限（约 {self.tool_call_limit} 次）。"
                f"再调用至多 1-2 次工具就必须给出最终回答——基于已收集的数据"
                f"做出最佳推断，**不要再继续探索新维度**。"
                f"如果信息确实不足，直接告知用户并请求缩小问题范围。"
            )
            return AgentState(
                messages=state.messages + [SystemMessage(content=warning)],
                iter_count=state.iter_count,
                thread_id=state.thread_id,
                extras=state.extras,
            )

        if tool_count >= self.soft_threshold:
            if self._has_marker(state.messages, self.SOFT_MARKER):
                return None
            warning = (
                f"{self.SOFT_MARKER} 系统提示：你已调用工具 {tool_count} 次"
                f"（上限约 {self.tool_call_limit} 次，已用 70%）。"
                f"如果信息已足够回答用户问题，请尽快收敛——避免不必要的额外查询。"
                f"复杂诊断场景请优先复用已有数据，再考虑新查询。"
            )
            return AgentState(
                messages=state.messages + [SystemMessage(content=warning)],
                iter_count=state.iter_count,
                thread_id=state.thread_id,
                extras=state.extras,
            )

        return None
