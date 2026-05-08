"""middleware for agent/loop.py::AgentLoop（纯 Python 协议，不依赖 framework）。

每个 middleware 只要有 `before_model(state: AgentState) -> AgentState | None`
方法即可——返回 None 不修改 state；返回新 state 则替换。

当前 4 个：
- DateReminder       每轮注入 <system-reminder> 当前日期到 messages 头部
- IterationGuard     工具调用次数到阈值时注入 system 警告
- ToolOutputTruncation  超大 ToolMessage 卸盘 + messages 替换为摘要+指针
- Summarization      messages 累计 tokens 超阈值时 LLM 压缩早期对话
"""
from .date_reminder import DateReminder
from .iteration_guard import IterationGuard
from .summarization import Summarization
from .tool_output_truncation import ToolOutputTruncation

__all__ = ["DateReminder", "IterationGuard", "Summarization", "ToolOutputTruncation"]
