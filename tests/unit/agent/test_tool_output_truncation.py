"""Unit tests for ToolOutputTruncationMiddleware.

We exercise `before_model` directly with hand-crafted state. No LLM calls,
no langgraph runtime — we just verify the middleware:
  1. leaves small ToolMessages alone,
  2. truncates oversized ones,
  3. writes the full content to disk under data/{user_id}/tool_outputs/,
  4. replaces the in-message content with a preview + file pointer,
  5. returns a Command-style dict with `Overwrite(messages)` so langgraph
     persists the change to checkpointer state.
"""
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.middleware.tool_output_truncation import ToolOutputTruncationMiddleware


@pytest.fixture
def tmp_data_dir(tmp_path):
    return str(tmp_path / "data")


def _patch_thread_id(thread_id: str):
    """Mock langgraph's get_config() so the middleware sees our thread_id."""
    return patch(
        "agent.middleware.tool_output_truncation.get_config",
        return_value={"configurable": {"thread_id": thread_id}},
    )


def test_small_tool_message_passes_through(tmp_data_dir):
    mw = ToolOutputTruncationMiddleware(max_bytes=1000, data_dir=tmp_data_dir)
    state = {
        "messages": [
            HumanMessage(content="hi"),
            ToolMessage(content="small result", tool_call_id="call_1", name="q"),
        ]
    }
    with _patch_thread_id("alice_conv1"):
        result = mw.before_model(state, MagicMock())
    assert result is None, "small content should not trigger truncation"


def test_oversized_tool_message_truncated_and_persisted(tmp_data_dir):
    mw = ToolOutputTruncationMiddleware(max_bytes=200, data_dir=tmp_data_dir)
    big = "x" * 1000
    state = {
        "messages": [
            HumanMessage(content="give me data"),
            ToolMessage(content=big, tool_call_id="call_42", name="query_campaign_report"),
        ]
    }
    with _patch_thread_id("alice_conv1"):
        result = mw.before_model(state, MagicMock())

    assert result is not None, "oversized content must trigger Overwrite"
    new_messages = result["messages"].value  # Overwrite.value is the new list
    assert len(new_messages) == 2

    truncated = new_messages[1]
    assert isinstance(truncated, ToolMessage)
    assert "truncated by ToolOutputTruncationMiddleware" in truncated.content
    assert "original 1000 bytes" in truncated.content
    assert "saved to tool_outputs/call_42.json" in truncated.content
    assert len(truncated.content) < 600, "summary should be much shorter than original"

    # Verify the full original content was written to disk under the user's dir
    out_path = Path(tmp_data_dir) / "alice" / "tool_outputs" / "call_42.json"
    assert out_path.exists()
    assert out_path.read_text(encoding="utf-8") == big


def test_user_id_extracted_from_thread_id(tmp_data_dir):
    """thread_id formats: bare 'user_id' or 'user_id_conversation_id' both
    must yield the right per-user directory."""
    mw = ToolOutputTruncationMiddleware(max_bytes=10, data_dir=tmp_data_dir)
    big = "y" * 100

    cases = [
        ("alice", "alice"),
        ("alice_abc123def456", "alice"),
        ("user42_conv01", "user42"),
    ]
    for thread_id, expected_user in cases:
        state = {"messages": [
            ToolMessage(content=big, tool_call_id=f"call_{thread_id}", name="t")
        ]}
        with _patch_thread_id(thread_id):
            mw.before_model(state, MagicMock())
        target = Path(tmp_data_dir) / expected_user / "tool_outputs" / f"call_{thread_id}.json"
        assert target.exists(), f"thread_id={thread_id} should map to user {expected_user}"


def test_json_string_content_persisted_as_is(tmp_data_dir):
    """In practice langchain stringifies tool returns (langgraph's ToolNode
    calls json.dumps on dict returns), so middleware sees JSON strings.
    The full JSON blob should be written to disk untouched."""
    mw = ToolOutputTruncationMiddleware(max_bytes=100, data_dir=tmp_data_dir)
    payload = {"campaigns": [{"name": "x" * 200, "spend": 1000}]}
    content_str = json.dumps(payload)
    state = {"messages": [
        ToolMessage(content=content_str, tool_call_id="dict_call", name="q")
    ]}
    with _patch_thread_id("bob_conv2"):
        result = mw.before_model(state, MagicMock())
    assert result is not None

    out_path = Path(tmp_data_dir) / "bob" / "tool_outputs" / "dict_call.json"
    assert out_path.exists()
    parsed = json.loads(out_path.read_text(encoding="utf-8"))
    assert parsed == payload


def test_only_tool_messages_affected(tmp_data_dir):
    """AIMessage / HumanMessage with large content stay untouched —
    only ToolMessage is the truncation target."""
    mw = ToolOutputTruncationMiddleware(max_bytes=10, data_dir=tmp_data_dir)
    big = "z" * 500
    state = {"messages": [
        HumanMessage(content=big),  # large but not a tool result
        AIMessage(content=big),
    ]}
    with _patch_thread_id("alice"):
        result = mw.before_model(state, MagicMock())
    assert result is None
