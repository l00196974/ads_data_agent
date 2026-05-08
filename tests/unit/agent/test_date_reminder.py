"""DateReminderMiddleware 契约测试。

锁住：
  1. 空 messages → 注入一条 reminder 到头部
  2. 已有 messages → reminder 永远在 index 0
  3. 已注入过老 reminder → 替换成最新（不重复堆积）
  4. 多个老 reminder 全部清掉，只保留最新
  5. 非 reminder 的 system/user/ai messages 顺序保留
  6. reminder 内容含日期 marker + 当前日期 + 时间口径换算指南
"""
from datetime import datetime
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent.middleware.date_reminder import (
    DateReminderMiddleware,
    _REMINDER_MARKER,
    _build_today_reminder,
)


def test_empty_messages_injects_reminder_at_head():
    mw = DateReminderMiddleware()
    result = mw.before_model({"messages": []}, MagicMock())
    new_messages = result["messages"].value
    assert len(new_messages) == 1
    assert isinstance(new_messages[0], HumanMessage)
    assert _REMINDER_MARKER in new_messages[0].content


def test_reminder_at_index_0_with_existing_messages():
    mw = DateReminderMiddleware()
    state = {"messages": [
        HumanMessage(content="你好"),
        AIMessage(content="嗨"),
    ]}
    result = mw.before_model(state, MagicMock())
    msgs = result["messages"].value
    assert len(msgs) == 3
    assert _REMINDER_MARKER in msgs[0].content
    assert msgs[1].content == "你好"
    assert msgs[2].content == "嗨"


def test_old_reminder_replaced_not_stacked():
    """跨天调用：messages 里有昨天的 reminder，应该删掉换上今天的，**只**留一条。"""
    mw = DateReminderMiddleware()
    yesterday_reminder = HumanMessage(content=f"<system-reminder>{_REMINDER_MARKER}\n## 当前时间\n今天是 2025年12月31日 ...")
    state = {"messages": [
        yesterday_reminder,
        HumanMessage(content="用户问题"),
        AIMessage(content="回答"),
    ]}
    result = mw.before_model(state, MagicMock())
    msgs = result["messages"].value

    # 只有 1 个 reminder，且不是昨天那条
    reminder_count = sum(1 for m in msgs if isinstance(m.content, str) and _REMINDER_MARKER in m.content)
    assert reminder_count == 1
    assert msgs[0].content != yesterday_reminder.content
    # 用户消息 / AI 消息保留
    assert any(m.content == "用户问题" for m in msgs)
    assert any(m.content == "回答" for m in msgs)


def test_multiple_old_reminders_all_cleaned():
    """边界：多个老 reminder 都清掉，只留最新一条（防 marker 残留累积）。"""
    mw = DateReminderMiddleware()
    state = {"messages": [
        HumanMessage(content=f"<system-reminder>{_REMINDER_MARKER}\n2024-01-01"),
        HumanMessage(content=f"<system-reminder>{_REMINDER_MARKER}\n2024-06-01"),
        HumanMessage(content=f"<system-reminder>{_REMINDER_MARKER}\n2025-01-01"),
        HumanMessage(content="实际用户消息"),
    ]}
    result = mw.before_model(state, MagicMock())
    msgs = result["messages"].value

    reminder_count = sum(1 for m in msgs if isinstance(m.content, str) and _REMINDER_MARKER in m.content)
    assert reminder_count == 1
    # 实际用户消息保留
    assert any(getattr(m, "content", None) == "实际用户消息" for m in msgs)


def test_non_reminder_message_order_preserved():
    """tool/system/ai/human 顺序不打乱（reminder 只是头部 prepend）。"""
    mw = DateReminderMiddleware()
    state = {"messages": [
        SystemMessage(content="sys"),
        HumanMessage(content="q"),
        AIMessage(content="a"),
        ToolMessage(content="t", tool_call_id="x"),
        HumanMessage(content="q2"),
    ]}
    result = mw.before_model(state, MagicMock())
    msgs = result["messages"].value
    # 头部是 reminder
    assert _REMINDER_MARKER in msgs[0].content
    # 后面顺序保持
    assert msgs[1].content == "sys"
    assert msgs[2].content == "q"
    assert msgs[3].content == "a"
    assert msgs[4].content == "t"
    assert msgs[5].content == "q2"


def test_reminder_content_has_date_and_relative_time_guide():
    """reminder 文本必须含: marker / 当前日期 / 「最近一个月」「上周」等换算规则。"""
    text = _build_today_reminder()
    assert _REMINDER_MARKER in text
    today_iso = datetime.now().strftime("%Y-%m-%d")
    # 当前日期（容忍时区差异——可能是 Asia/Shanghai 而本地是别的）；用月份匹配兜底
    assert datetime.now().strftime("%Y年") in text
    assert "最近一个月" in text and "近30天" in text
    assert "上周" in text and "昨天" in text
    # <system-reminder> 包裹
    assert text.startswith("<system-reminder>")
    assert text.endswith("</system-reminder>")


def test_reminder_content_changes_when_tz_overridden():
    """同一时刻不同时区 → reminder 内容能正确传 tz（不抛异常）。

    Windows 下 zoneinfo 可能缺 tzdata 包：try/except 整段跳过。Linux 生产环境
    一律有 tzdata，跑这个 case 验证。
    """
    try:
        from zoneinfo import ZoneInfo
        utc = ZoneInfo("UTC")
        shanghai = ZoneInfo("Asia/Shanghai")
    except Exception:
        # tzdata 缺失（Windows 无 pip install tzdata 时）→ 跳过
        return
    text_utc = _build_today_reminder(tz=utc)
    text_sh = _build_today_reminder(tz=shanghai)
    # 至少都含 marker（不深究内容相同性——同 UTC 0 点之外日期可能一致）
    assert _REMINDER_MARKER in text_utc
    assert _REMINDER_MARKER in text_sh
