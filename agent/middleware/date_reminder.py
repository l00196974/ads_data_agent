"""DateReminder for agent.loop.AgentLoop（loop 版，不依赖 langchain.agents）。

每轮 model 调用前 reset 一次 messages 中的日期 system-reminder：
1. 删除所有老 reminder（按 marker 识别——可能是跨天残留）
2. 在 messages 头部插入今日新 reminder（HumanMessage 含 <system-reminder>）

为什么用 user role：ppt 4.3 / 6.2 引用 Anthropic 实测——模型对 user-role 的
<system-reminder> 依从率高于 system-role；且 OpenAI 协议里多条 system message
行为不可预期。

为什么不进 system_prompt：当前日期每天变，破坏 prompt cache 前缀。改成 messages
注入后 system_prompt 100% 跨天稳定。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from agent.messages import HumanMessage

from agent.loop import AgentState

# 默认时区——华为广告数据按 Asia/Shanghai 口径统计
try:
    from zoneinfo import ZoneInfo
    _DEFAULT_TZ: "ZoneInfo | None" = ZoneInfo("Asia/Shanghai")
except Exception:
    _DEFAULT_TZ = None

_REMINDER_MARKER = "[DATE-REMINDER]"
_WEEKDAY_CN = "一二三四五六日"


def _build_today_reminder(tz=None) -> str:
    """造今日 reminder 文本——含 marker + 当前日期 + 相对时间换算指南。"""
    effective_tz = tz if tz is not None else _DEFAULT_TZ
    now = datetime.now(effective_tz) if effective_tz else datetime.now()
    return (
        f"<system-reminder>{_REMINDER_MARKER}\n"
        f"## 当前时间\n"
        f"今天是 **{now.strftime('%Y年%m月%d日')}**（`{now.strftime('%Y-%m-%d')}`，"
        f"星期{_WEEKDAY_CN[now.weekday()]}）。\n"
        f"用户提到的相对时间一律以此为基准换算：\n"
        f"- \"最近一个月\" / \"近30天\" → `start = today - 30d, end = today`\n"
        f"- \"上个月\" → 上一个自然月（1日 ~ 月末）\n"
        f"- \"本月\" → 本月 1 日 ~ today\n"
        f"- \"昨天\" → today - 1d\n"
        f"- \"上周\" → 上一个自然周（周一 ~ 周日）\n"
        f"</system-reminder>"
    )


class DateReminder:
    """loop 版 middleware：注入今日日期 system-reminder。"""

    def __init__(self, tz=None):
        self.tz = tz

    def _has_marker(self, content: Any) -> bool:
        return isinstance(content, str) and _REMINDER_MARKER in content

    def before_model(self, state: AgentState) -> AgentState | None:
        today = _build_today_reminder(self.tz)
        # 过滤老 reminder + 头部插入新 reminder
        filtered = [m for m in state.messages if not self._has_marker(getattr(m, "content", None))]
        new_messages = [HumanMessage(content=today)] + filtered
        return AgentState(
            messages=new_messages,
            iter_count=state.iter_count,
            thread_id=state.thread_id,
            extras=state.extras,
        )
