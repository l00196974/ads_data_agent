"""自定义消息数据类——替代 langchain_core.messages 的轻量等价物。

设计：
  - 5 个 dataclass：BaseMessage / HumanMessage / SystemMessage / AIMessage / ToolMessage
  - 全部 keyword-only 构造，与 langchain_core 用法兼容
  - 字段命名 / 类名 / __name__ 全保持与 langchain 一致——业务代码用 type(m).__name__
    判定子类身份，迁移期不用改业务逻辑

为什么不直接用 langchain_core：
  - langchain_core 拉 langsmith / 多个 transitive 依赖，安装包多 ~20MB
  - 实际只用消息数据类，runtime framework 早已不依赖
  - 自管让"我们的消息形状"独立于 vendor 升级

兼容性：
  - `content` 支持 str（最常见）或 list（多模态——dict + str 混合）
  - `tool_calls`（AIMessage 特有）：list[dict]，每条形如
    {"id": str, "name": str, "args": dict, "type": "tool_call"}
  - `tool_call_id` / `name`（ToolMessage 特有）
  - isinstance 检查正常工作（dataclass 继承自 BaseMessage）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BaseMessage:
    """所有消息共有的基类。仅作类型标记；运行时按 type(m).__name__ 判定具体子类。"""
    content: Any = ""


@dataclass
class HumanMessage(BaseMessage):
    """用户输入消息。"""
    pass


@dataclass
class SystemMessage(BaseMessage):
    """系统级 prompt（PLAN_INSTRUCTION + skill prompt + DateReminder 注入等）。"""
    pass


@dataclass
class AIMessage(BaseMessage):
    """LLM 输出消息。tool_calls 是 list[dict]，每项 {id, name, args, type}。

    reasoning_content：Qwen3 / DeepSeek-R1 / 火山方舟 thinking 模式的扩展字段。
    模型把"思考过程"输出到这个字段，**下一轮必须原封不动传回**，否则 API 报
    `The reasoning_content in the thinking mode must be passed back to the API`。
    """
    tool_calls: list[dict] = field(default_factory=list)
    name: str | None = None  # 可选——某些链路（subagent 任务名标签等）用
    reasoning_content: str | None = None


@dataclass
class ToolMessage(BaseMessage):
    """工具调用结果。tool_call_id 必须对得上 AIMessage.tool_calls 里某条的 id。"""
    tool_call_id: str = ""
    name: str = ""


__all__ = [
    "BaseMessage",
    "HumanMessage",
    "SystemMessage",
    "AIMessage",
    "ToolMessage",
]
