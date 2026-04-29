# 08 上下文兜底机制（Context Fallback）

> ⏳ **占位**：本篇骨架已建，内容待补。

**关联源码**：`agent/middleware/tool_output_truncation.py`、`agent/checkpointer.py:20-39`（Summarization 工厂 patch）、`agent/config.py::LongContextConfig`

**计划覆盖**：
- 与 `07-subagent-driven-context` 的关系：兜底而非主方案
- ToolOutputTruncationMiddleware：超大工具返回值卸载到 `data/{user_id}/tool_outputs/`
- SummarizationMiddleware monkey-patch 的必要性（不能通过 `middleware=` 参数追加）
- 阈值调参（80k token / 5KB 工具输出）
- 何时主方案（SubAgent）失效需要兜底

**ADR 占位**：
- ADR-004: 为什么 monkey-patch deepagents 的 Summarization 工厂（duplicate 校验绕过）
