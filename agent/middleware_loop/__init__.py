"""loop 版 middleware（适配 agent/loop.py::AgentLoop）。

跟 agent/middleware/ 下的 langgraph 老版本逻辑等价，但不依赖
langchain.agents.middleware.types / langgraph.types。

P3 期间两套并存：runner_v1（旧）走 agent/middleware/，runner_v2（新）走这里。
P4 切默认 + 删老 framework 后，agent/middleware/ 整目录删除，本目录会被
rename 成 agent/middleware/。
"""
from .date_reminder import DateReminder
from .iteration_guard import IterationGuard
from .tool_output_truncation import ToolOutputTruncation

__all__ = ["DateReminder", "IterationGuard", "ToolOutputTruncation"]
