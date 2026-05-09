"""token_breakdown.estimate_breakdown 单测——分桶逻辑 + 兜底行为。"""
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent.token_breakdown import estimate_breakdown


def test_buckets_into_four_categories():
    """system / tool_results / history / current 各分一桶；最后一条 Human 是 current。"""
    messages = [
        SystemMessage(content="历史 system"),         # 进 system 桶
        HumanMessage(content="第一轮用户问题"),         # 不是最后一条 Human → history
        AIMessage(content="第一轮 AI 回答"),            # AI → history
        ToolMessage(content="工具返回值大堆数据", tool_call_id="t1"),  # tool_results
        HumanMessage(content="第二轮用户追问"),         # 最后一条 Human → current
    ]
    bd = estimate_breakdown(messages, system_prompt="PLAN_INSTRUCTION", model=None)

    assert bd is not None
    assert bd["system"] > 0          # system_prompt + 历史 SystemMessage
    assert bd["tool_results"] > 0
    assert bd["history"] > 0          # 第一轮 Human + 第一轮 AI
    assert bd["current"] > 0          # 第二轮 Human
    assert bd["estimated_total"] == bd["system"] + bd["tool_results"] + bd["history"] + bd["current"]
    assert bd["method"] in ("tiktoken", "char-estimate")


def test_only_last_human_counts_as_current():
    """3 条 HumanMessage 时只有最后一条进 current 桶，前 2 条进 history。"""
    messages = [
        HumanMessage(content="第 1 问"),
        HumanMessage(content="第 2 问"),
        HumanMessage(content="第 3 问 - 当前"),
    ]
    bd = estimate_breakdown(messages, system_prompt="", model=None)

    assert bd["current"] > 0
    assert bd["history"] > 0
    # 3 个 Human content 长度差不多——current 应明显小于 history（history 里有 2 条）
    # 不严格等比，但 current < history
    assert bd["current"] < bd["history"]


def test_empty_returns_none():
    """messages 空 + system_prompt 空 → 返 None；前端按"无 breakdown"显示。"""
    assert estimate_breakdown([], "", None) is None


def test_only_system_prompt():
    """单独的 system_prompt 也算 system 桶。"""
    bd = estimate_breakdown([], "你是数据分析助手", None)
    assert bd is not None
    assert bd["system"] > 0
    assert bd["tool_results"] == 0
    assert bd["history"] == 0
    assert bd["current"] == 0


def test_method_label_present():
    """method 字段必填——前端 tooltip 区分'±5%'与'±30%'文案靠它。"""
    bd = estimate_breakdown(
        [HumanMessage(content="hi")], "system", None
    )
    assert bd["method"] in ("tiktoken", "char-estimate")
