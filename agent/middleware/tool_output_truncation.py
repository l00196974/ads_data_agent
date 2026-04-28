"""Middleware: truncate large business-tool outputs out of the message history.

Why: deepagents' built-in argument truncation only targets `write_file` /
`edit_file`, leaving raw `ToolMessage` content from business tools (e.g.
`query_campaign_report` returning thousands of campaigns) unbounded. Each
LLM round prefills the entire history, so a single 50KB tool output
repeated across 10 rounds = 500KB of redundant tokens.

Strategy: when a ToolMessage content exceeds `max_bytes`, write the full
content to `<data_dir>/<user_id>/tool_outputs/<tool_call_id>.json` and
replace the in-message content with a short preview + file pointer. The
truncated message is persisted to state via `Overwrite`, so all future
LLM rounds see the small version.

Only acts on `ToolMessage` — `AIMessage.tool_calls.args` (LLM's call
arguments) and `HumanMessage` are untouched.
"""
import json
from pathlib import Path
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime
from langgraph.types import Overwrite


class ToolOutputTruncationMiddleware(AgentMiddleware):
    def __init__(self, max_bytes: int, data_dir: str):
        self.max_bytes = max_bytes
        self.data_dir = Path(data_dir)

    def _user_id(self) -> str:
        try:
            cfg = get_config()
            thread_id = cfg.get("configurable", {}).get("thread_id", "unknown")
        except RuntimeError:
            thread_id = "unknown"
        # thread_id format: "user_id" or "user_id_conv_id" — first segment is user
        return str(thread_id).split("_", 1)[0] or "unknown"

    def before_model(self, state: AgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        if not messages:
            return None

        out_dir = self.data_dir / self._user_id() / "tool_outputs"

        modified = False
        new_messages = []
        for m in messages:
            if not isinstance(m, ToolMessage):
                new_messages.append(m)
                continue

            content = m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)
            if len(content.encode("utf-8")) <= self.max_bytes:
                new_messages.append(m)
                continue

            # Truncate: save full content to disk, replace with summary
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
            file_note = f"saved to tool_outputs/{saved_to}" if saved_to else "(disk write failed; data lost)"
            summary = (
                f"{preview}\n\n"
                f"…[truncated by ToolOutputTruncationMiddleware; "
                f"original {len(content)} bytes; {file_note}]"
            )
            new_messages.append(ToolMessage(
                content=summary,
                tool_call_id=m.tool_call_id,
                name=m.name,
            ))
            modified = True

        if modified:
            return {"messages": Overwrite(new_messages)}
        return None
