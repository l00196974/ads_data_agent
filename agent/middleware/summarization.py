"""Summarization middleware——messages 累计 tokens 超阈值时压缩早期对话。

跟其他 middleware 不同，本 middleware 在 before_model 钩子里**主动调一次 LLM**
生成 summary，把早期 messages 替换成一条 SystemMessage(summary)。代价是每次
触发多 1 次 LLM 调用（约 2-5s + 几百到几千 tokens 成本），但避免后续每轮都
prefill 全量历史。

触发条件：
  - state.messages 估算总 tokens 超过 trigger_tokens（默认 80000）
  - 且老 messages 段（除了最近 keep_messages 条）非空（否则没什么可压的）

替换后的 messages 结构：
  [SystemMessage(summary)] + state.messages[-keep_messages:]

为什么放 before_model 而非 after_tool：
  - tools 可能并行执行 N 个，after 钩子要算 N 次去重——复杂
  - before_model 时整个 messages list 已稳定，是干净的检查点

设计取舍：
  - **同步串行调 LLM**：跟主链路用同一个 client + model（避免再开 connection）；
    summary 期间主 chat 阻塞 2-5s，但这是预期——正常对话不会经常触发，触发了
    就是为了避免 context 爆炸的代价
  - **summary 是 SystemMessage 而非 HumanMessage**：用户 role 容易让模型把
    summary 当成新 user 输入；system role 更明确"这是历史摘要"
  - **仅触发一次/轮**：避免 summary 后又 trigger 又 summary 的 loop——已含
    SUMMARY_MARKER 的 SystemMessage 跳过判定
"""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from agent.messages import BaseMessage, SystemMessage

from agent.loop import AgentState

if TYPE_CHECKING:
    from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_SUMMARY_MARKER = "[CONVERSATION-SUMMARY]"

# 9 段结构化摘要模板——参考 ppt 5.2.5 给广告 Agent 的版本调整
_SUMMARY_PROMPT = """你正在对一段长对话做归档摘要，目的是让模型继续工作时不丢上下文但不需 prefill 全量。

读完用户和 assistant 的全部对话后，输出一份 markdown 摘要，**严格按以下 9 段**：

1. **用户原始诉求**（用户最初问什么，逐字保留关键词）
2. **关键业务背景**（行业、品类、目标对象、时间窗口、统计口径——分析数据时这些是基石）
3. **当前涉及的对象**（具体的指标 / 维度 / 时间范围 / artifact_id 等业务对象快照）
4. **出错与修正**（哪些查询参数错过、哪些口径被纠正、用户否决过什么）
5. **决策过程**（尝试过的分析角度、最终选择的方法）
6. **用户原话**（关键的用户原话逐字保留——尤其是补充澄清和推翻的话）
7. **待办事项**（用户问了但还没答 / 还没查的）
8. **当前工作**（对话中断瞬间在做什么——哪个查询执行到一半 / 哪个分析正进行）
9. **下一步建议**（必须引用用户原话或明确 KPI——不要空泛建议）

格式：每段一行小标题 + 内容；总长度控制在 500-800 字。**不要**罗列工具调用流水账。
**不要**加结尾客套话。直接以"# 对话摘要"开头。
"""


class Summarization:
    """LLM-based 跨轮摘要 middleware（loop 版）。

    使用：
      mw = Summarization(
          openai_client=client,           # 主 loop 共用的 AsyncOpenAI
          model="...",                    # 跟主 loop 同模型
          trigger_tokens=80000,
          keep_messages=10,
      )
    """

    def __init__(
        self,
        openai_client: "AsyncOpenAI",
        model: str,
        trigger_tokens: int = 80000,
        keep_messages: int = 10,
    ):
        self.client = openai_client
        self.model = model
        self.trigger_tokens = trigger_tokens
        self.keep_messages = keep_messages

    def _has_summary(self, messages: list[BaseMessage]) -> bool:
        for m in messages:
            content = getattr(m, "content", None)
            if isinstance(content, str) and _SUMMARY_MARKER in content:
                return True
        return False

    def _estimate_tokens(self, messages: list[BaseMessage]) -> int:
        """快速估算 messages 总 token——用 char-based fallback（不依赖 tiktoken
        加载，summarization 触发本身就是高负载场景，不要再加额外依赖）。"""
        total_chars = 0
        for m in messages:
            content = getattr(m, "content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for p in content:
                    if isinstance(p, dict):
                        total_chars += len(str(p.get("text", "")))
        # 中文 1 char ≈ 1 token；英文 4 char ≈ 1 token；混合大致 2 char ≈ 1 token
        return total_chars // 2

    def _format_for_summary(self, messages: list[BaseMessage]) -> str:
        """把 messages 拼成给 LLM 看的对话稿。"""
        lines = []
        for m in messages:
            cls = type(m).__name__
            content = getattr(m, "content", "")
            if isinstance(content, list):
                content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
            content = str(content)[:5000]  # 单条截断防 LLM input 爆
            if cls == "SystemMessage":
                lines.append(f"[SYSTEM] {content}")
            elif cls == "HumanMessage":
                lines.append(f"[USER] {content}")
            elif cls == "AIMessage":
                lines.append(f"[ASSISTANT] {content}")
            elif cls == "ToolMessage":
                tool_name = getattr(m, "name", "tool")
                lines.append(f"[TOOL:{tool_name}] {content}")
        return "\n\n".join(lines)

    async def before_model(self, state: AgentState) -> AgentState | None:
        if not state.messages:
            return None
        # 已 summary 过的不再二次触发——避免重复 LLM 调用
        if self._has_summary(state.messages):
            return None
        # 评估是否需要触发
        token_count = self._estimate_tokens(state.messages)
        if token_count < self.trigger_tokens:
            return None

        # 早期 messages 段（被压缩掉的部分）
        if len(state.messages) <= self.keep_messages:
            return None  # 全部要保留，没什么可压
        old_messages = state.messages[: -self.keep_messages]
        recent_messages = state.messages[-self.keep_messages :]

        if not old_messages:
            return None

        logger.info(
            "summarization triggered: total_tokens~%d, old_msgs=%d, keep=%d",
            token_count, len(old_messages), len(recent_messages),
        )

        # 调 LLM 生成 summary——单次非流式调用，省事
        try:
            transcript = self._format_for_summary(old_messages)
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _SUMMARY_PROMPT},
                    {"role": "user", "content": transcript},
                ],
                stream=False,
            )
            summary_text = response.choices[0].message.content or ""
        except Exception as e:
            logger.warning("summarization LLM call failed, skipping: %s", e)
            return None

        if not summary_text.strip():
            return None

        # 替换：[summary system msg] + 最近 keep_messages 条原文
        marker_summary = SystemMessage(content=f"{_SUMMARY_MARKER}\n\n{summary_text}")
        new_messages = [marker_summary] + recent_messages

        logger.info(
            "summarization done: replaced %d old msgs with 1 summary (%d chars)",
            len(old_messages), len(summary_text),
        )

        return AgentState(
            messages=new_messages,
            iter_count=state.iter_count,
            thread_id=state.thread_id,
            extras=state.extras,
        )
