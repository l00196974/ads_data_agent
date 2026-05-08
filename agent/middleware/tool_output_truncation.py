"""ToolOutputTruncation for agent.loop.AgentLoop（loop 版，不依赖 langgraph）。

每次 model 调用前 scan messages，超过 max_bytes 的 ToolMessage.content 写到
data/{user_id}/tool_outputs/{conv_id}/{tool_call_id}.json，message 内容替换成
300 字符摘要 + 文件指针。

跟老版本主要差异：
  - 不依赖 `langgraph.config.get_config()` 拿 thread_id——直接读 state.thread_id
  - 不返回 `Overwrite(...)` dict update——直接构造新 AgentState
  - 不需 `langgraph.runtime.Runtime` 参数
"""
from __future__ import annotations

import json
from pathlib import Path

from langchain_core.messages import ToolMessage

from agent.loop import AgentState


class ToolOutputTruncation:
    """大 ToolMessage 卸盘 + messages 替换为摘要+指针 (loop 版)。"""

    def __init__(self, max_bytes: int, data_dir: str | Path):
        self.max_bytes = max_bytes
        self.data_dir = Path(data_dir)

    def _user_and_conv(self, thread_id: str) -> tuple[str, str]:
        """从 thread_id 拆出 (user_id, conv_id)。
        thread_id 格式 "{user_id}_{conv_id}"；只有 user_id 时 conv 兜底为 'default'。"""
        s = str(thread_id) if thread_id else "unknown"
        if "_" in s:
            user, conv = s.split("_", 1)
            return user or "unknown", conv or "default"
        return s or "unknown", "default"

    def before_model(self, state: AgentState) -> AgentState | None:
        if not state.messages:
            return None

        user_id, conv_id = self._user_and_conv(state.thread_id)
        out_dir = self.data_dir / user_id / "tool_outputs" / conv_id

        modified = False
        new_messages = []
        for m in state.messages:
            if not isinstance(m, ToolMessage):
                new_messages.append(m)
                continue

            content = m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)
            if len(content.encode("utf-8")) <= self.max_bytes:
                new_messages.append(m)
                continue

            # 超阈值——卸盘 + 替换
            tool_call_id = m.tool_call_id or "unknown"
            saved_to: str | None = None
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / f"{tool_call_id}.json"
                out_path.write_text(content, encoding="utf-8")
                saved_to = out_path.name
            except OSError:
                pass

            preview = content[:300].rstrip()
            file_note = (
                f"saved to tool_outputs/{conv_id}/{saved_to}"
                if saved_to else "(disk write failed; data lost)"
            )
            summary = (
                f"{preview}\n\n"
                f"…[truncated by ToolOutputTruncation; "
                f"original {len(content)} bytes; {file_note}]"
            )
            new_messages.append(ToolMessage(
                content=summary,
                tool_call_id=m.tool_call_id,
                name=m.name,
            ))
            modified = True

        if not modified:
            return None
        return AgentState(
            messages=new_messages,
            iter_count=state.iter_count,
            thread_id=state.thread_id,
            extras=state.extras,
        )
