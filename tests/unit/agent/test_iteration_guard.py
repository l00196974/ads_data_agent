"""IterationGuardMiddleware 契约测试。

锁住：
  1. 工具调用次数低于 soft 阈值 → 不注入
  2. 达到 soft 阈值 → 注入 SOFT 警告
  3. 达到 urgent 阈值 → 直接注入 URGENT（跳过 SOFT）
  4. 已注入过的级别不重复注入（messages 里已有 marker 时返 None）
  5. 阈值计算：tool_call_limit = recursion_limit // 2
  6. 空 messages → 不崩
"""
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent.middleware.iteration_guard import IterationGuardMiddleware


def _mk_state(tool_count: int, extra_messages: list = None):
    """造一个含 tool_count 个 ToolMessage 的 state。"""
    msgs = [HumanMessage(content="q"), AIMessage(content="working")]
    for i in range(tool_count):
        msgs.append(ToolMessage(content=f"result {i}", tool_call_id=f"call_{i}"))
    if extra_messages:
        msgs.extend(extra_messages)
    return {"messages": msgs}


def test_below_soft_threshold_no_injection():
    mw = IterationGuardMiddleware(recursion_limit=150)  # soft=52, urgent=67
    # 50 次工具调用还在 soft 之下
    result = mw.before_model(_mk_state(50), MagicMock())
    assert result is None


def test_soft_threshold_injects_soft_warning():
    mw = IterationGuardMiddleware(recursion_limit=150)
    # 52 次 = soft（70% * 75）
    result = mw.before_model(_mk_state(52), MagicMock())
    assert result is not None
    msg = result["messages"][0]
    assert isinstance(msg, SystemMessage)
    assert IterationGuardMiddleware.SOFT_MARKER in msg.content
    assert IterationGuardMiddleware.URGENT_MARKER not in msg.content


def test_urgent_threshold_injects_urgent_skips_soft():
    """达到 urgent 阈值时直接注入 urgent，不再走 soft 路径。"""
    mw = IterationGuardMiddleware(recursion_limit=150)
    # 67 次 = urgent（90% * 75）
    result = mw.before_model(_mk_state(67), MagicMock())
    assert result is not None
    msg = result["messages"][0]
    assert IterationGuardMiddleware.URGENT_MARKER in msg.content
    assert IterationGuardMiddleware.SOFT_MARKER not in msg.content


def test_already_injected_soft_not_re_injected():
    mw = IterationGuardMiddleware(recursion_limit=150)
    # 已经有一条带 SOFT marker 的 system 消息
    prior = SystemMessage(content=f"{IterationGuardMiddleware.SOFT_MARKER} earlier warning")
    state = _mk_state(53, extra_messages=[prior])
    result = mw.before_model(state, MagicMock())
    assert result is None  # 不重复注入


def test_already_injected_urgent_not_re_injected():
    mw = IterationGuardMiddleware(recursion_limit=150)
    prior = SystemMessage(content=f"{IterationGuardMiddleware.URGENT_MARKER} earlier")
    state = _mk_state(70, extra_messages=[prior])
    result = mw.before_model(state, MagicMock())
    assert result is None


def test_soft_already_injected_does_not_block_urgent():
    """注入过 SOFT 后，工具次数继续涨到 urgent，应该再注入 URGENT。"""
    mw = IterationGuardMiddleware(recursion_limit=150)
    prior = SystemMessage(content=f"{IterationGuardMiddleware.SOFT_MARKER} prior soft")
    state = _mk_state(70, extra_messages=[prior])
    result = mw.before_model(state, MagicMock())
    assert result is not None
    assert IterationGuardMiddleware.URGENT_MARKER in result["messages"][0].content


def test_thresholds_scale_with_recursion_limit():
    """改 recursion_limit 时阈值自动重算。"""
    mw_small = IterationGuardMiddleware(recursion_limit=50)  # tool_limit=25, soft=17, urgent=22
    mw_big = IterationGuardMiddleware(recursion_limit=300)   # tool_limit=150, soft=105, urgent=135

    # 小 limit 的 17 次就触发 soft；大 limit 17 次远低于 soft
    assert mw_small.before_model(_mk_state(17), MagicMock()) is not None
    assert mw_big.before_model(_mk_state(17), MagicMock()) is None
    # 大 limit 要 105 次才触发 soft
    assert mw_big.before_model(_mk_state(105), MagicMock()) is not None


def test_empty_messages_does_not_crash():
    mw = IterationGuardMiddleware(recursion_limit=150)
    assert mw.before_model({"messages": []}, MagicMock()) is None


def test_only_human_no_tool_messages():
    """纯对话（没调过工具）不注入。"""
    mw = IterationGuardMiddleware(recursion_limit=150)
    state = {"messages": [HumanMessage(content="hello"), AIMessage(content="hi")]}
    assert mw.before_model(state, MagicMock()) is None
