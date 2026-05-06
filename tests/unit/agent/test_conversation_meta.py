"""会话元数据：列表 / 标题 / 归档 / 永久删除核心契约。

锁住三件事：
  1. upsert 首次创建 INSERT，再次同 user/conv 调用 UPDATE，不会创建多行
  2. archive → list_for_user(archived=False) 不再出现，archived=True 能列到
  3. rename / restore / delete_permanent 行为正确
"""
import pytest

from agent import conversation_meta


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    """每个测试用独立 tmp_path，避免互相污染。"""
    conversation_meta.init(tmp_path)
    yield


def test_upsert_first_time_creates_with_title():
    conv = conversation_meta.upsert("alice", "conv1", "查询最近一个月的流水")
    assert conv["user_id"] == "alice"
    assert conv["conversation_id"] == "conv1"
    assert conv["message_count"] == 1
    assert conv["archived_at"] is None
    # 标题取首条消息前 20 字
    assert conv["title"] == "查询最近一个月的流水"


def test_upsert_long_message_truncated_with_ellipsis():
    long_msg = "这是一条很长的消息超过了二十个字符所以应该被截断"
    conv = conversation_meta.upsert("alice", "conv1", long_msg)
    # 截到 20 字 + "…"
    assert conv["title"].endswith("…")
    assert len(conv["title"]) == 21


def test_upsert_idempotent_increments_count():
    conversation_meta.upsert("alice", "conv1", "第一条")
    conversation_meta.upsert("alice", "conv1", "第二条")  # 不影响标题
    conversation_meta.upsert("alice", "conv1", "第三条")
    conv = conversation_meta.get("alice", "conv1")
    assert conv["message_count"] == 3
    assert conv["title"] == "第一条"  # 标题保留首条


def test_list_for_user_orders_by_last_active_desc():
    import time
    conversation_meta.upsert("alice", "old", "旧对话")
    time.sleep(1.1)  # iso second-level resolution，1s 间隔保证排序差异
    conversation_meta.upsert("alice", "new", "新对话")
    convs = conversation_meta.list_for_user("alice")
    assert [c["conversation_id"] for c in convs] == ["new", "old"]


def test_list_for_user_isolates_users():
    conversation_meta.upsert("alice", "c1", "a 的对话")
    conversation_meta.upsert("bob", "c1", "b 的对话")  # 同 conv_id 不同 user
    assert len(conversation_meta.list_for_user("alice")) == 1
    assert len(conversation_meta.list_for_user("bob")) == 1
    # alice 看不到 bob 的，反之亦然
    assert conversation_meta.list_for_user("alice")[0]["title"] == "a 的对话"


def test_archive_hides_from_default_list():
    conversation_meta.upsert("alice", "c1", "对话1")
    assert len(conversation_meta.list_for_user("alice")) == 1
    assert conversation_meta.archive("alice", "c1") is True
    assert conversation_meta.list_for_user("alice", archived=False) == []
    archived = conversation_meta.list_for_user("alice", archived=True)
    assert len(archived) == 1
    assert archived[0]["archived_at"] is not None


def test_archive_idempotent_returns_false_second_time():
    conversation_meta.upsert("alice", "c1", "x")
    assert conversation_meta.archive("alice", "c1") is True
    assert conversation_meta.archive("alice", "c1") is False  # 已归档


def test_restore_brings_back_to_active_list():
    conversation_meta.upsert("alice", "c1", "x")
    conversation_meta.archive("alice", "c1")
    assert conversation_meta.restore("alice", "c1") is True
    assert len(conversation_meta.list_for_user("alice")) == 1
    assert conversation_meta.list_for_user("alice", archived=True) == []


def test_upsert_after_archive_auto_restores():
    """归档了的对话再发消息 → 自动恢复（用户在归档里继续追问 = 想恢复的意图）。"""
    conversation_meta.upsert("alice", "c1", "原对话")
    conversation_meta.archive("alice", "c1")
    conversation_meta.upsert("alice", "c1", "继续聊")
    conv = conversation_meta.get("alice", "c1")
    assert conv["archived_at"] is None
    assert conv["message_count"] == 2


def test_rename_updates_title():
    conversation_meta.upsert("alice", "c1", "原标题")
    assert conversation_meta.rename("alice", "c1", "新名字") is True
    assert conversation_meta.get("alice", "c1")["title"] == "新名字"


def test_rename_rejects_empty_title():
    conversation_meta.upsert("alice", "c1", "原标题")
    assert conversation_meta.rename("alice", "c1", "  ") is False
    assert conversation_meta.get("alice", "c1")["title"] == "原标题"


def test_rename_truncates_to_50_chars():
    conversation_meta.upsert("alice", "c1", "x")
    long_title = "新" * 100
    conversation_meta.rename("alice", "c1", long_title)
    assert len(conversation_meta.get("alice", "c1")["title"]) == 50


def test_delete_permanent_removes_from_db():
    conversation_meta.upsert("alice", "c1", "x")
    assert conversation_meta.delete_permanent("alice", "c1") is True
    assert conversation_meta.get("alice", "c1") is None
    assert conversation_meta.list_for_user("alice", archived=True) == []


def test_get_returns_none_for_missing():
    assert conversation_meta.get("nobody", "no-such-conv") is None


def test_default_title_for_empty_message():
    """前端如果传了空字符串当 first_message——别让标题变成空串。"""
    conv = conversation_meta.upsert("alice", "c1", "")
    assert conv["title"] == "新对话"
