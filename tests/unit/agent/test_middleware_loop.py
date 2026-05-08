"""middleware_loop 三套 middleware 的契约测试。

跟老 langgraph 版本的测试结构等价，差异：
  - 调用签名 `before_model(state)` 不带 runtime
  - state 是 dataclass AgentState 不是 dict
  - 返回 AgentState 不是 dict-update
  - 不依赖 langchain.agents 基类
"""
import json
from datetime import datetime
from pathlib import Path

import pytest
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from agent.loop import AgentState
from agent.middleware_loop import (
    DateReminder,
    IterationGuard,
    ToolOutputTruncation,
)
from agent.middleware_loop.date_reminder import _REMINDER_MARKER, _build_today_reminder


# ============================================================
# DateReminder
# ============================================================


def _state(messages=None, thread_id="t"):
    return AgentState(messages=messages or [], thread_id=thread_id)


def test_date_reminder_inserts_at_head_when_empty():
    mw = DateReminder()
    new_state = mw.before_model(_state([]))
    assert new_state is not None
    assert len(new_state.messages) == 1
    assert _REMINDER_MARKER in new_state.messages[0].content


def test_date_reminder_at_index_0_with_existing():
    mw = DateReminder()
    state = _state([HumanMessage(content="你好"), AIMessage(content="嗨")])
    new_state = mw.before_model(state)
    assert _REMINDER_MARKER in new_state.messages[0].content
    assert new_state.messages[1].content == "你好"
    assert new_state.messages[2].content == "嗨"


def test_date_reminder_replaces_old_marker():
    """messages 里有老 reminder → 删掉换上今天的，只留 1 条。"""
    mw = DateReminder()
    state = _state([
        HumanMessage(content=f"<system-reminder>{_REMINDER_MARKER}\n旧的"),
        HumanMessage(content="实际问题"),
    ])
    new_state = mw.before_model(state)
    cnt = sum(1 for m in new_state.messages
              if isinstance(m.content, str) and _REMINDER_MARKER in m.content)
    assert cnt == 1
    assert any(m.content == "实际问题" for m in new_state.messages)


def test_date_reminder_content_has_today_date():
    text = _build_today_reminder()
    assert _REMINDER_MARKER in text
    assert datetime.now().strftime("%Y年") in text
    assert "最近一个月" in text and "上周" in text


# ============================================================
# IterationGuard
# ============================================================


def _mk_state_with_tool_count(n: int):
    msgs = [HumanMessage(content="q"), AIMessage(content="ok")]
    for i in range(n):
        msgs.append(ToolMessage(content=f"r{i}", tool_call_id=f"c{i}"))
    return AgentState(messages=msgs, thread_id="t")


def test_iter_guard_below_soft_no_inject():
    mw = IterationGuard(recursion_limit=150)  # tool_limit=75, soft=52
    assert mw.before_model(_mk_state_with_tool_count(50)) is None


def test_iter_guard_soft_threshold_injects():
    mw = IterationGuard(recursion_limit=150)
    state = _mk_state_with_tool_count(52)
    new_state = mw.before_model(state)
    assert new_state is not None
    last = new_state.messages[-1]
    assert isinstance(last, SystemMessage)
    assert IterationGuard.SOFT_MARKER in last.content


def test_iter_guard_urgent_threshold_skips_soft():
    mw = IterationGuard(recursion_limit=150)
    new_state = mw.before_model(_mk_state_with_tool_count(67))
    last = new_state.messages[-1]
    assert IterationGuard.URGENT_MARKER in last.content
    assert IterationGuard.SOFT_MARKER not in last.content


def test_iter_guard_already_injected_no_duplicate():
    mw = IterationGuard(recursion_limit=150)
    msgs = [HumanMessage(content="q")] + [
        ToolMessage(content=f"r{i}", tool_call_id=f"c{i}") for i in range(53)
    ] + [SystemMessage(content=f"{IterationGuard.SOFT_MARKER} prior")]
    state = AgentState(messages=msgs, thread_id="t")
    assert mw.before_model(state) is None


def test_iter_guard_empty_messages_no_crash():
    mw = IterationGuard(recursion_limit=150)
    assert mw.before_model(AgentState(messages=[], thread_id="t")) is None


# ============================================================
# ToolOutputTruncation
# ============================================================


def test_truncation_small_message_passes_through(tmp_path):
    mw = ToolOutputTruncation(max_bytes=1000, data_dir=tmp_path)
    state = _state([
        HumanMessage(content="hi"),
        ToolMessage(content="small result", tool_call_id="c1", name="my_tool"),
    ], thread_id="alice_conv1")
    assert mw.before_model(state) is None  # 小内容不动


def test_truncation_oversized_writes_to_disk(tmp_path):
    mw = ToolOutputTruncation(max_bytes=200, data_dir=tmp_path)
    big = "x" * 1000
    state = _state([
        HumanMessage(content="q"),
        ToolMessage(content=big, tool_call_id="c1", name="my_tool"),
    ], thread_id="alice_conv1")
    new_state = mw.before_model(state)
    assert new_state is not None

    # 替换后的 ToolMessage 内容应是摘要 + 文件指针
    truncated_msg = new_state.messages[1]
    assert isinstance(truncated_msg, ToolMessage)
    assert "truncated" in truncated_msg.content
    assert "saved to tool_outputs/conv1/c1.json" in truncated_msg.content
    assert "original 1000 bytes" in truncated_msg.content

    # 真文件落盘了
    out_path = tmp_path / "alice" / "tool_outputs" / "conv1" / "c1.json"
    assert out_path.exists()
    assert out_path.read_text(encoding="utf-8") == big


def test_truncation_user_conv_split_from_thread_id(tmp_path):
    """thread_id 'user_conv' → user/conv 拆分；纯 'user' → conv='default'。"""
    mw = ToolOutputTruncation(max_bytes=10, data_dir=tmp_path)
    big = "y" * 100

    cases = [
        ("alice", "alice", "default"),
        ("user42_conv01", "user42", "conv01"),
        ("ab_de_fg", "ab", "de_fg"),
    ]
    for thread_id, expected_user, expected_conv in cases:
        state = _state([ToolMessage(content=big, tool_call_id=f"c_{thread_id}", name="t")],
                       thread_id=thread_id)
        mw.before_model(state)
        target = tmp_path / expected_user / "tool_outputs" / expected_conv / f"c_{thread_id}.json"
        assert target.exists(), f"thread_id={thread_id} expected {target}"


def test_truncation_only_tool_messages_affected(tmp_path):
    """大 Human/AIMessage 不动，只 ToolMessage 被截。"""
    mw = ToolOutputTruncation(max_bytes=10, data_dir=tmp_path)
    big = "z" * 500
    state = _state([
        HumanMessage(content=big),
        AIMessage(content=big),
    ], thread_id="x")
    assert mw.before_model(state) is None
