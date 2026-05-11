"""上下文 tokens 分桶估算——供 metrics-bar 进度条分段显示用。

把传给 LLM 的 messages（含 system_prompt）按"逻辑用途"分 4 桶：
  - system:       PLAN_INSTRUCTION + skill prompt + DateReminder 注入等
  - tool_results: 之前轮工具调用的返回值（往往是大头）
  - history:      多轮历史对话（除最后一条 HumanMessage 外的 Human/AIMessage）
  - current:      最后一条 HumanMessage = 当前轮的新输入

和 LLM 厂商上报的 input_tokens 总数可能差 <5%（tokenizer 不同），仅用于
"占比可视化"，不参与计费 / 触发判断。

为什么 fallback 到字符粗估而不是直接 None：用户在没有 tiktoken / 公司内网拦
CDN 时也想看大致占比——粗估 ±30% 也比"完全看不到"强。`method` 字段告知前端
当前精度，前端 tooltip 区分显示（"tiktoken 估算 ±5%" vs "字符粗估 ±30%"）。
"""
from __future__ import annotations

from typing import Any

from agent.messages import BaseMessage


# 进程级 encoder 缓存——每个 model 名字只 load 一次（cl100k_base 几兆，约 100ms）
_ENCODER_CACHE: dict[str, Any] = {}


def _get_encoder(model: str | None) -> tuple[Any, str]:
    """按模型名拿 tiktoken encoder + 返回估算方法标签。

    返回:
        (encoder, "tiktoken")     —— tiktoken 可用，encoder 加载成功
        (None,    "char-estimate") —— tiktoken 没装 / encoder 加载失败，走字符粗估
    """
    try:
        import tiktoken
    except ImportError:
        return None, "char-estimate"

    key = model or "__default__"
    if key in _ENCODER_CACHE:
        cached = _ENCODER_CACHE[key]
        return cached, ("tiktoken" if cached else "char-estimate")

    enc = None
    if model:
        try:
            enc = tiktoken.encoding_for_model(model)
        except Exception:
            pass
    if enc is None:
        # 未识别的模型（多数国内厂商）—— 用 cl100k_base 估算（偏差 <5%）
        try:
            enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            enc = None
    _ENCODER_CACHE[key] = enc
    return enc, ("tiktoken" if enc else "char-estimate")


def _normalize_text(text: Any) -> str:
    """多模态 content 可能是 list[dict|str]——统一压成 str 后估算。"""
    if not text:
        return ""
    if isinstance(text, list):
        return "".join(
            p.get("text", "") if isinstance(p, dict) else str(p)
            for p in text
        )
    return text if isinstance(text, str) else str(text)


def _count_chars_estimate(text: str) -> int:
    """字符粗估：CJK 1:1，其它 4:1（OpenAI 经验值）。"""
    if not text:
        return 0
    cjk = sum(1 for c in text if ord(c) > 127)
    ascii_n = len(text) - cjk
    return cjk + max(1, ascii_n // 4)


def _count(enc: Any, text: Any) -> int:
    """统一计数入口：encoder 在用 tiktoken，否则字符粗估。"""
    s = _normalize_text(text)
    if not s:
        return 0
    if enc is None:
        return _count_chars_estimate(s)
    try:
        return len(enc.encode(s))
    except Exception:
        # 部分特殊符号 encoder 会 raise——退回字符粗估
        return _count_chars_estimate(s)


def estimate_breakdown(
    messages: list[BaseMessage],
    system_prompt: str,
    model: str | None,
) -> dict | None:
    """估算 messages + system_prompt 各部分占的 tokens。

    返回 dict（前端按 key 分段渲染进度条）：
        {
            "system": int,            # SystemMessage + system_prompt 拼起来
            "tool_results": int,      # ToolMessage
            "history": int,           # 旧 Human + AI
            "current": int,           # 最后一条 HumanMessage
            "estimated_total": int,   # 上面 4 桶之和
            "method": "tiktoken" | "char-estimate"
        }

    返回 None 仅当 messages 空且 system_prompt 也空——前端按"无 breakdown"显示。
    """
    if not messages and not system_prompt:
        return None

    enc, method = _get_encoder(model)

    # 找出"最后一条 HumanMessage"——它代表当前轮的新输入，单独算一桶
    last_human_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if type(messages[i]).__name__ == "HumanMessage":
            last_human_idx = i
            break

    buckets = {"system": 0, "tool_results": 0, "history": 0, "current": 0}

    # system_prompt（loop 单独拼到 openai messages 头部，不在 state.messages 里）
    if system_prompt:
        buckets["system"] += _count(enc, system_prompt)

    for i, m in enumerate(messages):
        cls = type(m).__name__
        n = _count(enc, getattr(m, "content", ""))
        if cls == "SystemMessage":
            buckets["system"] += n
        elif cls == "ToolMessage":
            buckets["tool_results"] += n
        elif cls == "HumanMessage" and i == last_human_idx:
            buckets["current"] += n
        else:
            # HumanMessage（非最后） + AIMessage —— 历史对话
            buckets["history"] += n

    buckets["estimated_total"] = sum(buckets.values())
    buckets["method"] = method
    return buckets
