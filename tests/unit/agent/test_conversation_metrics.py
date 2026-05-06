"""LLM metrics 持久化 + 聚合契约。

锁住：
  1. append → list 顺序保留
  2. summary 聚合正确（含 None 字段安全 SUM）
  3. summary_for_user_conversations 一次 GROUP BY 拿全部
  4. 隔离：user / conversation 互不串
  5. 未 init 时 append no-op
"""
import pytest

from agent import conversation_metrics as cm


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    cm.init(tmp_path)
    yield


def _append(user, conv, **kwargs):
    """填默认值的便捷 append（避免每个 case 手写一长串参数）。"""
    defaults = dict(
        turn_id="t1", call_seq=1, subagent=None, model="gpt-4o",
        input_tokens=1000, output_tokens=200, total_tokens=1200,
        cache_read_tokens=800, duration_ms=4500, ttft_ms=850, tps=44.4,
    )
    defaults.update(kwargs)
    cm.append(user, conv, defaults.pop("turn_id"), **defaults)


def test_append_then_summary_aggregates():
    _append("alice", "c1", input_tokens=1000, output_tokens=200, total_tokens=1200)
    _append("alice", "c1", input_tokens=2000, output_tokens=300, total_tokens=2300)
    s = cm.summary_for_conversation("alice", "c1")
    assert s["calls"] == 2
    assert s["input_tokens"] == 3000
    assert s["output_tokens"] == 500
    assert s["total_tokens"] == 3500


def test_summary_handles_null_token_fields():
    """少数厂商不上报 usage：None 字段不能让 SUM 崩——COALESCE 兜底。"""
    cm.append("alice", "c1", "t1", call_seq=1, subagent=None, model="x",
              input_tokens=None, output_tokens=None, total_tokens=None,
              cache_read_tokens=None, duration_ms=None, ttft_ms=None, tps=None)
    _append("alice", "c1", input_tokens=500, output_tokens=100, total_tokens=600)
    s = cm.summary_for_conversation("alice", "c1")
    assert s["calls"] == 2
    assert s["input_tokens"] == 500
    assert s["total_tokens"] == 600


def test_summary_for_user_returns_per_conversation():
    _append("alice", "c1", total_tokens=1000)
    _append("alice", "c1", total_tokens=500)
    _append("alice", "c2", total_tokens=2000)
    summaries = cm.summary_for_user_conversations("alice")
    assert summaries["c1"]["total_tokens"] == 1500
    assert summaries["c1"]["calls"] == 2
    assert summaries["c2"]["total_tokens"] == 2000
    assert summaries["c2"]["calls"] == 1


def test_isolation_users_dont_leak():
    _append("alice", "c1", total_tokens=1000)
    _append("bob", "c1", total_tokens=9999)
    assert cm.summary_for_conversation("alice", "c1")["total_tokens"] == 1000
    assert cm.summary_for_conversation("bob", "c1")["total_tokens"] == 9999
    assert "c1" not in cm.summary_for_user_conversations("alice").get("__missing__", {})


def test_list_preserves_call_seq_and_subagent():
    _append("alice", "c1", call_seq=1, subagent=None)
    _append("alice", "c1", call_seq=2, subagent="data-analyst")
    _append("alice", "c1", call_seq=3, subagent="issue-diagnostician")
    rows = cm.list_for_conversation("alice", "c1")
    assert [r["call_seq"] for r in rows] == [1, 2, 3]
    assert [r["subagent"] for r in rows] == [None, "data-analyst", "issue-diagnostician"]


def test_delete_for_conversation_returns_count():
    _append("alice", "c1")
    _append("alice", "c1")
    _append("alice", "c2")
    assert cm.delete_for_conversation("alice", "c1") == 2
    assert cm.summary_for_conversation("alice", "c1")["calls"] == 0
    assert cm.summary_for_conversation("alice", "c2")["calls"] == 1


def test_empty_summary_for_missing_conversation():
    s = cm.summary_for_conversation("nobody", "no-such")
    assert s["calls"] == 0
    assert s["total_tokens"] == 0


def test_append_safe_when_not_initialized():
    cm._db_path = None
    cm.append("alice", "c1", "t1", call_seq=1, subagent=None, model="x",
              input_tokens=1, output_tokens=1, total_tokens=2,
              cache_read_tokens=0, duration_ms=1, ttft_ms=1, tps=1.0)
    assert cm.summary_for_conversation("alice", "c1") == {
        "calls": 0, "input_tokens": 0, "output_tokens": 0,
        "total_tokens": 0, "cache_read_tokens": 0, "duration_ms": 0,
    }
