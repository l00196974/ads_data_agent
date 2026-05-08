"""DateReminderMiddleware：每轮 LLM 调用前往 messages 注入「今天是 X 月 Y 日」。

为什么不直接拼进 system_prompt：
  当前日期每天变一次，放 system_prompt 里第一字节流就变化，破坏 OpenAI 兼容
  自动前缀匹配 cache（DeepSeek / Qwen 等支持 prompt cache 的端点会跨天 100%
  miss）。改成 user role <system-reminder> 注入到 messages 头部：system_prompt
  保持永远稳定可缓存，日期变化只影响 messages 里那一条。

注入策略：
  - 每次 before_model 检查 messages 是否含老 reminder（用 marker 识别），有就删掉
  - 在 messages 最前面 insert 一条 HumanMessage 含最新 reminder + marker
  - 跨天前后两次调用看到的 system_prompt 字节相同 → cache 命中

为什么用 HumanMessage 而非 SystemMessage：
  ppt 4.3 / 6.2 引用 Anthropic 实测——模型对 user-role 的 <system-reminder>
  依从率高于 system-role；且 OpenAI 协议里多条 system message 行为不可预期，
  user-role 标签更稳。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime
from langgraph.types import Overwrite

# 默认 Asia/Shanghai——华为广告数据按这个口径统计；zoneinfo 在 Windows 部分发行版缺失
# 时降级用系统时区（生产环境 Linux 一律有 zoneinfo）
try:
    from zoneinfo import ZoneInfo
    _DEFAULT_TZ: "ZoneInfo | None" = ZoneInfo("Asia/Shanghai")
except Exception:
    _DEFAULT_TZ = None

# Marker 字符串——用于识别 messages 里已注入过的旧 reminder（每轮要删除老的换上最新的）
_REMINDER_MARKER = "[DATE-REMINDER]"
_WEEKDAY_CN = "一二三四五六日"


def _build_today_reminder(tz=None) -> str:
    """构造今日 reminder 文本——内容跟原 _today_context() 等价，只是多个 marker 标识。"""
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


class DateReminderMiddleware(AgentMiddleware):
    """每轮 model 调用前注入最新的日期 system-reminder 到 messages 头部。"""

    def __init__(self, tz=None):
        # tz 让单测能注入固定时区；不传走 _DEFAULT_TZ（Asia/Shanghai）
        self.tz = tz

    def _has_reminder(self, content: Any) -> bool:
        return isinstance(content, str) and _REMINDER_MARKER in content

    def before_model(self, state: AgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        messages = state.get("messages", [])
        today_reminder = _build_today_reminder(self.tz)

        # 删除所有老 reminder（按 marker 识别，可能有跨天残留），再头部插入今日的。
        # 注意：哪怕 marker 命中的内容跟 today_reminder 一字不差，也照常重建——
        # 这样保证最终 messages[0] 一定是最新 reminder，不依赖时区/夏令时等边界。
        new_messages = [m for m in messages if not self._has_reminder(getattr(m, "content", None))]
        new_messages.insert(0, HumanMessage(content=today_reminder))

        return {"messages": Overwrite(new_messages)}
