"""ThreadStore 单测——纯 SQLite 持久化层契约。

覆盖：
  1. init/close 幂等
  2. 4 种 BaseMessage 子类 round-trip（System/Human/AI/Tool）
  3. AIMessage.tool_calls 字段保留
  4. ToolMessage.tool_call_id + name 保留
  5. append-only：第二次 save 同样的 messages 不重复写
  6. append-only：第二次 save 多了几条会接续 append
  7. overwrite_messages 真清空再写
  8. 多 thread 隔离不串
  9. 空 thread load 返空 list
  10. interrupt save / load / clear 生命周期
  11. interrupt upsert 覆盖旧的
  12. delete_thread 删 messages + interrupt
  13. list_threads 列出所有有 messages 的 thread_id
  14. 多模态 content（list of dict）round-trip 不丢
"""
import pytest
import pytest_asyncio
from agent.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent.state import ThreadStore


# 注：pytest-asyncio strict 模式下 async fixture 必须用 pytest_asyncio.fixture
@pytest_asyncio.fixture
async def store(tmp_path):
    s = ThreadStore(tmp_path / "test.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_init_idempotent(tmp_path):
    """init 多次调用不报错（幂等）。"""
    s = ThreadStore(tmp_path / "x.db")
    await s.init()
    await s.init()  # 第二次不应抛异常或重建
    await s.close()


@pytest.mark.asyncio
async def test_save_load_roundtrip_all_message_types(store):
    """4 种 message 类型保 / 取一致。"""
    messages = [
        SystemMessage(content="你是助手"),
        HumanMessage(content="你好"),
        AIMessage(content="Hello!"),
        ToolMessage(content="result", tool_call_id="call_1", name="my_tool"),
    ]
    await store.save_messages("t1", messages)
    loaded = await store.load_messages("t1")

    assert len(loaded) == 4
    assert isinstance(loaded[0], SystemMessage) and loaded[0].content == "你是助手"
    assert isinstance(loaded[1], HumanMessage) and loaded[1].content == "你好"
    assert isinstance(loaded[2], AIMessage) and loaded[2].content == "Hello!"
    assert isinstance(loaded[3], ToolMessage)
    assert loaded[3].content == "result"
    assert loaded[3].tool_call_id == "call_1"
    assert loaded[3].name == "my_tool"


@pytest.mark.asyncio
async def test_ai_message_tool_calls_preserved(store):
    """AIMessage.tool_calls 字段（含 id/name/args）保留完整。"""
    ai = AIMessage(
        content="",
        tool_calls=[
            {"id": "c1", "name": "query", "args": {"q": "test"}, "type": "tool_call"},
            {"id": "c2", "name": "search", "args": {"k": "abc"}, "type": "tool_call"},
        ],
    )
    await store.save_messages("t_tc", [ai])
    loaded = await store.load_messages("t_tc")

    assert len(loaded) == 1
    assert isinstance(loaded[0], AIMessage)
    tcs = loaded[0].tool_calls
    assert len(tcs) == 2
    assert tcs[0]["id"] == "c1"
    assert tcs[0]["name"] == "query"
    assert tcs[0]["args"] == {"q": "test"}


@pytest.mark.asyncio
async def test_append_only_no_duplicate(store):
    """二次 save 同一份 messages → 不重复写（append-only 语义）。"""
    msgs = [HumanMessage(content="q1"), AIMessage(content="a1")]
    await store.save_messages("t", msgs)
    await store.save_messages("t", msgs)  # 二次 save 同样长度

    loaded = await store.load_messages("t")
    assert len(loaded) == 2  # 不该变成 4


@pytest.mark.asyncio
async def test_append_only_extends_on_growth(store):
    """messages 列表增长 → 新增部分被 append。"""
    msgs1 = [HumanMessage(content="q1")]
    await store.save_messages("t", msgs1)

    msgs2 = msgs1 + [AIMessage(content="a1"), HumanMessage(content="q2")]
    await store.save_messages("t", msgs2)

    loaded = await store.load_messages("t")
    assert len(loaded) == 3
    assert loaded[0].content == "q1"
    assert loaded[1].content == "a1"
    assert loaded[2].content == "q2"


@pytest.mark.asyncio
async def test_overwrite_messages_truly_replaces(store):
    """overwrite 清空老的换新的。"""
    await store.save_messages("t", [HumanMessage(content="old1"), HumanMessage(content="old2")])
    await store.overwrite_messages("t", [SystemMessage(content="new")])

    loaded = await store.load_messages("t")
    assert len(loaded) == 1
    assert loaded[0].content == "new"
    assert isinstance(loaded[0], SystemMessage)


@pytest.mark.asyncio
async def test_thread_isolation(store):
    """两个 thread 数据互不影响。"""
    await store.save_messages("a", [HumanMessage(content="for_a")])
    await store.save_messages("b", [HumanMessage(content="for_b")])

    a = await store.load_messages("a")
    b = await store.load_messages("b")
    assert len(a) == 1 and a[0].content == "for_a"
    assert len(b) == 1 and b[0].content == "for_b"


@pytest.mark.asyncio
async def test_load_empty_thread_returns_empty_list(store):
    """没存过的 thread → 返空 list。"""
    loaded = await store.load_messages("never_used")
    assert loaded == []


@pytest.mark.asyncio
async def test_interrupt_lifecycle(store):
    """save → load → clear → load None 全流程。"""
    payload = {"tool_calls": [{"id": "c1", "name": "kill", "args": {}}], "preview": "..."}
    await store.save_interrupt("t", payload)

    loaded = await store.load_interrupt("t")
    assert loaded == payload

    await store.clear_interrupt("t")
    assert await store.load_interrupt("t") is None


@pytest.mark.asyncio
async def test_interrupt_upsert_replaces_old(store):
    """二次 save 覆盖老的。"""
    await store.save_interrupt("t", {"v": 1})
    await store.save_interrupt("t", {"v": 2})

    assert (await store.load_interrupt("t"))["v"] == 2


@pytest.mark.asyncio
async def test_delete_thread_clears_both_messages_and_interrupt(store):
    """delete_thread 把 messages + interrupt 都清。"""
    await store.save_messages("t", [HumanMessage(content="x")])
    await store.save_interrupt("t", {"pending": True})

    await store.delete_thread("t")

    assert await store.load_messages("t") == []
    assert await store.load_interrupt("t") is None


@pytest.mark.asyncio
async def test_list_threads(store):
    """list_threads 列出所有有 messages 的 thread。"""
    await store.save_messages("t1", [HumanMessage(content="x")])
    await store.save_messages("t2", [HumanMessage(content="y")])
    await store.save_messages("t3", [HumanMessage(content="z")])

    threads = await store.list_threads()
    assert set(threads) == {"t1", "t2", "t3"}


@pytest.mark.asyncio
async def test_multimodal_content_roundtrip(store):
    """list-of-dict 形式的 content 不丢失结构。"""
    msg = HumanMessage(content=[
        {"type": "text", "text": "看这张图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,XXX"}},
    ])
    await store.save_messages("t_mm", [msg])
    loaded = await store.load_messages("t_mm")

    assert len(loaded) == 1
    content = loaded[0].content
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["image_url"]["url"].startswith("data:image/png")


@pytest.mark.asyncio
async def test_save_empty_messages_noop(store):
    """传空 list 不写表也不抛。"""
    await store.save_messages("t", [])
    assert await store.load_messages("t") == []


@pytest.mark.asyncio
async def test_require_conn_before_init():
    """没 init 直接调操作 → 友好报错。"""
    s = ThreadStore("/tmp/never_inited.db")  # 不调 init
    with pytest.raises(RuntimeError, match="not initialized"):
        await s.load_messages("t")


@pytest.mark.asyncio
async def test_coexists_with_langgraph_db(tmp_path):
    """同一 db 文件里跑 langgraph 老链路 + ThreadStore，两套 schema 互不干扰。"""
    db = tmp_path / "shared.db"
    # 模拟 langgraph 已建过自己的表
    import aiosqlite
    async with aiosqlite.connect(str(db)) as conn:
        await conn.execute("""CREATE TABLE checkpoints (id TEXT PRIMARY KEY, blob BLOB)""")
        await conn.execute("INSERT INTO checkpoints (id, blob) VALUES ('lg_test', X'010203')")
        await conn.commit()

    # ThreadStore 在同一 db 上 init + 用
    s = ThreadStore(db)
    await s.init()
    await s.save_messages("t1", [HumanMessage(content="from loop")])
    assert (await s.load_messages("t1"))[0].content == "from loop"
    await s.close()

    # 验证老表数据没动
    async with aiosqlite.connect(str(db)) as conn:
        cur = await conn.execute("SELECT id FROM checkpoints WHERE id = 'lg_test'")
        row = await cur.fetchone()
        assert row is not None and row[0] == "lg_test"
