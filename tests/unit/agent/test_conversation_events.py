"""会话事件流：append + 按 turn_id 分组的契约。

锁住：
  1. append 可重复；list 按时间顺序返回
  2. 按 user / conv 隔离
  3. 未 init 时 append 是 no-op，不 raise（CLI 测试场景安全）
  4. delete_for_conversation 只删该 conv 的事件
"""
import pytest

from agent import conversation_events


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    conversation_events.init(tmp_path)
    yield


def test_append_then_list_preserves_order():
    conversation_events.append("alice", "c1", "turn1", "step", {"msg": "step 1", "type": "tool_start"})
    conversation_events.append("alice", "c1", "turn1", "step", {"msg": "step 1", "type": "tool_end"})
    conversation_events.append("alice", "c1", "turn1", "step", {"msg": "step 2", "type": "tool_start"})
    events = conversation_events.list_for_conversation("alice", "c1")
    assert len(events) == 3
    # 按 id 升序 → 写入顺序
    assert [e["payload"]["msg"] for e in events] == ["step 1", "step 1", "step 2"]
    assert [e["payload"]["type"] for e in events] == ["tool_start", "tool_end", "tool_start"]


def test_list_groups_by_turn_id_in_payload():
    """events 带 turn_id，前端按它分组——回看时把"思考分组"插到对应 user/ai 之间。"""
    conversation_events.append("alice", "c1", "turn1", "step", {"msg": "a", "type": "tool_start"})
    conversation_events.append("alice", "c1", "turn2", "step", {"msg": "b", "type": "tool_start"})
    events = conversation_events.list_for_conversation("alice", "c1")
    turn_ids = [e["turn_id"] for e in events]
    assert turn_ids == ["turn1", "turn2"]


def test_isolation_by_user_and_conversation():
    conversation_events.append("alice", "c1", "t1", "step", {"x": 1})
    conversation_events.append("alice", "c2", "t1", "step", {"x": 2})
    conversation_events.append("bob",   "c1", "t1", "step", {"x": 3})
    assert len(conversation_events.list_for_conversation("alice", "c1")) == 1
    assert conversation_events.list_for_conversation("alice", "c1")[0]["payload"]["x"] == 1
    assert len(conversation_events.list_for_conversation("alice", "c2")) == 1
    assert len(conversation_events.list_for_conversation("bob", "c1")) == 1


def test_artifact_kind_persists():
    conversation_events.append(
        "alice", "c1", "turn1", "artifact_updated",
        {"artifact_id": "art_xyz", "action": "created"},
    )
    events = conversation_events.list_for_conversation("alice", "c1")
    assert events[0]["kind"] == "artifact_updated"
    assert events[0]["payload"]["artifact_id"] == "art_xyz"


def test_payload_with_chinese_chars_roundtrip():
    """JSON 序列化 ensure_ascii=False，中文不转义——sqlite 里也是可读 UTF-8。"""
    conversation_events.append(
        "alice", "c1", "t1", "step",
        {"msg": "🤖 派给 问题诊断师", "type": "tool_start"},
    )
    events = conversation_events.list_for_conversation("alice", "c1")
    assert events[0]["payload"]["msg"] == "🤖 派给 问题诊断师"


def test_delete_for_conversation_only_targets_that_conv():
    conversation_events.append("alice", "c1", "t1", "step", {"x": 1})
    conversation_events.append("alice", "c1", "t1", "step", {"x": 2})
    conversation_events.append("alice", "c2", "t1", "step", {"x": 3})
    deleted = conversation_events.delete_for_conversation("alice", "c1")
    assert deleted == 2
    assert conversation_events.list_for_conversation("alice", "c1") == []
    # c2 不受影响
    assert len(conversation_events.list_for_conversation("alice", "c2")) == 1


def test_append_safe_when_not_initialized():
    """init 之前 append → no-op（不 raise）——CLI 模式下方便。"""
    conversation_events._db_path = None  # 强制模拟未 init
    # 不 raise 即视为通过
    conversation_events.append("alice", "c1", "t1", "step", {"x": 1})
    assert conversation_events.list_for_conversation("alice", "c1") == []
