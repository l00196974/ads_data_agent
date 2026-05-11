"""ThreadStore — agent/loop.py 的状态持久化层。

替代 langgraph.checkpoint.sqlite.aio.AsyncSqliteSaver。直接两张表搞定，schema
透明、可手 SQL 调试、HitL resume 简单。

**和老链路共存**：表名前缀 `loop_*` 跟 langgraph 内部表 `checkpoints` / `writes`
完全独立。同一个 data/checkpoints.db 文件里两套 schema 并行，P2 改动不影响 P1
之前的功能。

两张表的 schema：

```sql
CREATE TABLE loop_messages (
    thread_id    TEXT NOT NULL,
    seq          INTEGER NOT NULL,    -- append 顺序（thread 内自增）
    role         TEXT NOT NULL,       -- 'system' / 'user' / 'assistant' / 'tool'
    content      TEXT NOT NULL,       -- 主内容（多模态 content 序列化为 JSON）
    tool_calls   TEXT,                -- AIMessage.tool_calls 的 JSON（其它角色为 NULL）
    tool_call_id TEXT,                -- ToolMessage.tool_call_id（其它角色为 NULL）
    name         TEXT,                -- ToolMessage.name 或 AIMessage 函数调用名（可空）
    created_at   REAL NOT NULL,       -- Unix 时间戳秒（debug 用）
    PRIMARY KEY (thread_id, seq)
)

CREATE TABLE loop_interrupts (
    thread_id    TEXT PRIMARY KEY,
    payload      TEXT NOT NULL,       -- JSON: {tool_calls: [...], created_at: ...}
    created_at   REAL NOT NULL
)
```

为什么不用二进制 blob 序列化（langgraph 老链路那种）：
  - 跨版本脆弱（升级 langchain 后老二进制可能反序列化失败）
  - 调试痛——SELECT 不出消息内容
  - schema 演进难——加一列要改 framework 代码

设计取舍：
  - **Append-only messages**：load 时按 (thread_id, seq) 顺序读出来；不允许中间
    修改某条消息。需要"覆盖"语义时（如 ToolOutputTruncationMiddleware 把大
    ToolMessage 改成摘要）由上层 middleware 在 load 时按需替换，不动磁盘。
    这是一个有意识的设计选择——保留完整原始历史方便审计。
  - **Interrupt 单行 upsert**：每个 thread 最多一条 pending interrupt，新中断
    覆盖旧的（理论上不会有：一个 thread 同时只能有一个 LLM 在跑）。
  - **aiosqlite + WAL**：跟 langgraph 老链路同一个文件能并行读写。
"""
from __future__ import annotations

import json
import logging
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Optional

import aiosqlite
from agent.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

logger = logging.getLogger(__name__)

# ============================================================
# Schema
# ============================================================

_SCHEMA = """
CREATE TABLE IF NOT EXISTS loop_messages (
    thread_id        TEXT NOT NULL,
    seq              INTEGER NOT NULL,
    role             TEXT NOT NULL,
    content          TEXT NOT NULL,
    tool_calls       TEXT,
    tool_call_id     TEXT,
    name             TEXT,
    reasoning_content TEXT,
    created_at       REAL NOT NULL,
    PRIMARY KEY (thread_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_loop_messages_thread_seq
    ON loop_messages (thread_id, seq);

CREATE TABLE IF NOT EXISTS loop_interrupts (
    thread_id    TEXT PRIMARY KEY,
    payload      TEXT NOT NULL,
    created_at   REAL NOT NULL
);
"""


# ============================================================
# 序列化 / 反序列化
# ============================================================


def _content_to_str(content: Any) -> str:
    """统一 content 为 str。多模态（list of dict）走 JSON 序列化。"""
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    return json.dumps(content, ensure_ascii=False)


def _content_from_str(text: str) -> Any:
    """读回 content。优先尝试 JSON 解多模态结构，失败按 str 处理。"""
    if not text:
        return ""
    if text.startswith("[") or text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text
    return text


def _message_to_row(message: BaseMessage) -> dict:
    """BaseMessage → dict（对应 loop_messages 的列）。"""
    cls = type(message).__name__
    role_map = {
        "SystemMessage": "system",
        "HumanMessage": "user",
        "AIMessage": "assistant",
        "ToolMessage": "tool",
    }
    role = role_map.get(cls)
    if role is None:
        raise ValueError(f"unsupported message class: {cls}")

    row: dict[str, Any] = {
        "role": role,
        "content": _content_to_str(message.content),
        "tool_calls": None,
        "tool_call_id": None,
        "name": None,
        "reasoning_content": None,
    }
    # AIMessage 可能含 tool_calls 列表 + reasoning_content（Qwen3/DeepSeek-R1 thinking 模式）
    if cls == "AIMessage":
        tcs = getattr(message, "tool_calls", None)
        if tcs:
            row["tool_calls"] = json.dumps(tcs, ensure_ascii=False)
        nm = getattr(message, "name", None)
        if nm:
            row["name"] = nm
        rc = getattr(message, "reasoning_content", None)
        if rc:
            row["reasoning_content"] = rc
    elif cls == "ToolMessage":
        row["tool_call_id"] = getattr(message, "tool_call_id", None)
        row["name"] = getattr(message, "name", None)
    return row


def _row_to_message(row: dict) -> BaseMessage:
    """dict → BaseMessage 子类实例。"""
    role = row["role"]
    content = _content_from_str(row["content"])

    if role == "system":
        return SystemMessage(content=content)
    if role == "user":
        return HumanMessage(content=content)
    if role == "assistant":
        kwargs: dict[str, Any] = {"content": content}
        if row.get("tool_calls"):
            try:
                kwargs["tool_calls"] = json.loads(row["tool_calls"])
            except json.JSONDecodeError:
                logger.warning("tool_calls JSON parse failed, dropping: %r", row["tool_calls"])
        if row.get("reasoning_content"):
            kwargs["reasoning_content"] = row["reasoning_content"]
        if row.get("name"):
            kwargs["name"] = row["name"]
        return AIMessage(**kwargs)
    if role == "tool":
        return ToolMessage(
            content=content,
            tool_call_id=row.get("tool_call_id") or "",
            name=row.get("name") or "",
        )
    raise ValueError(f"unknown role: {role}")


# ============================================================
# ThreadStore
# ============================================================


class ThreadStore:
    """async SQLite 持久化——单 connection + WAL，跨 thread_id 共享。

    生命周期：
      - 应用启动时实例化一次，调 `await store.init()` 建表
      - 业务路径调 save/load/delete
      - 应用关闭时调 `await store.close()` 释放 connection

    线程安全：aiosqlite 内部串行化所有写入，多 coroutine 同时调 save_messages
    安全。但**同一 thread_id 内** seq 由调用方保证有序——append 是 INSERT
    `MAX(seq) + 1`，并发 append 同一 thread 可能 PRIMARY KEY 冲突。生产路径
    一个 thread 同时只有一个 loop 在跑，无冲突。
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._conn: Optional[aiosqlite.Connection] = None
        self._exit_stack: Optional[AsyncExitStack] = None

    async def init(self) -> None:
        """建表 + 启用 WAL。幂等。"""
        if self._conn is not None:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._exit_stack = AsyncExitStack()
        self._conn = await self._exit_stack.enter_async_context(
            aiosqlite.connect(str(self.db_path))
        )
        # WAL 让多个 reader 不阻塞 writer——和 langgraph 老链路用同一文件能共存
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.executescript(_SCHEMA)
        # 兼容老 DB：reasoning_content 列是后加的——SQLite ADD COLUMN IF NOT EXISTS
        # 要 3.35+，老版本兜底用 PRAGMA table_info 检查。先 try-add，已存在就吞错。
        try:
            await self._conn.execute("ALTER TABLE loop_messages ADD COLUMN reasoning_content TEXT")
        except aiosqlite.OperationalError:
            pass  # column 已存在
        await self._conn.commit()

    async def close(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._exit_stack = None
        self._conn = None

    def _require_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("ThreadStore not initialized. Call await store.init() first.")
        return self._conn

    # ------------------------------------------------------------
    # messages
    # ------------------------------------------------------------

    async def save_messages(self, thread_id: str, messages: list[BaseMessage]) -> None:
        """append-only 写入。**不**写已存在的 seq——只 append 新消息。

        实现：先查当前 thread 的 max(seq)，新消息从 max+1 开始。如果传入的
        messages 比已存的多，多出来的当作新消息 append；少则不动（不删）；
        相等则什么都不做。
        """
        if not messages:
            return
        conn = self._require_conn()
        now = time.time()

        cur = await conn.execute(
            "SELECT COALESCE(MAX(seq), -1) FROM loop_messages WHERE thread_id = ?",
            (thread_id,),
        )
        max_seq_row = await cur.fetchone()
        await cur.close()
        existing_count = (max_seq_row[0] + 1) if max_seq_row else 0

        new_messages = messages[existing_count:]
        if not new_messages:
            return  # 啥都不用做，messages 是 load 后没新增的副本

        rows = []
        for i, msg in enumerate(new_messages):
            row = _message_to_row(msg)
            rows.append((
                thread_id,
                existing_count + i,
                row["role"],
                row["content"],
                row["tool_calls"],
                row["tool_call_id"],
                row["name"],
                row["reasoning_content"],
                now,
            ))
        await conn.executemany(
            "INSERT INTO loop_messages "
            "(thread_id, seq, role, content, tool_calls, tool_call_id, name, reasoning_content, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        await conn.commit()
        logger.debug("save_messages thread=%s appended=%d total_now=%d",
                     thread_id, len(new_messages), existing_count + len(new_messages))

    async def load_messages(self, thread_id: str) -> list[BaseMessage]:
        """按 seq 升序 load 整个 thread 的 messages。"""
        conn = self._require_conn()
        cur = await conn.execute(
            "SELECT role, content, tool_calls, tool_call_id, name, reasoning_content "
            "FROM loop_messages WHERE thread_id = ? ORDER BY seq ASC",
            (thread_id,),
        )
        rows = await cur.fetchall()
        await cur.close()
        messages = []
        for r in rows:
            messages.append(_row_to_message({
                "role": r[0],
                "content": r[1],
                "tool_calls": r[2],
                "tool_call_id": r[3],
                "name": r[4],
                "reasoning_content": r[5],
            }))
        return messages

    async def overwrite_messages(self, thread_id: str, messages: list[BaseMessage]) -> None:
        """**慎用**——清空 thread 现有 messages 全部重写。

        只用于 SummarizationMiddleware 类把老 messages 替换成 summary 的场景。
        正常 append 走 save_messages。
        """
        conn = self._require_conn()
        now = time.time()
        await conn.execute("DELETE FROM loop_messages WHERE thread_id = ?", (thread_id,))
        if messages:
            rows = []
            for i, msg in enumerate(messages):
                row = _message_to_row(msg)
                rows.append((
                    thread_id, i, row["role"], row["content"],
                    row["tool_calls"], row["tool_call_id"], row["name"],
                    row["reasoning_content"], now,
                ))
            await conn.executemany(
                "INSERT INTO loop_messages "
                "(thread_id, seq, role, content, tool_calls, tool_call_id, name, reasoning_content, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        await conn.commit()

    # ------------------------------------------------------------
    # interrupts (HitL pending state)
    # ------------------------------------------------------------

    async def save_interrupt(self, thread_id: str, payload: dict) -> None:
        """保存当前 thread 的待确认 interrupt。upsert——同 thread 旧的覆盖。"""
        conn = self._require_conn()
        now = time.time()
        payload_json = json.dumps(payload, ensure_ascii=False)
        await conn.execute(
            "INSERT INTO loop_interrupts (thread_id, payload, created_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(thread_id) DO UPDATE SET payload=excluded.payload, created_at=excluded.created_at",
            (thread_id, payload_json, now),
        )
        await conn.commit()

    async def load_interrupt(self, thread_id: str) -> dict | None:
        """读出 pending interrupt；无返 None。"""
        conn = self._require_conn()
        cur = await conn.execute(
            "SELECT payload FROM loop_interrupts WHERE thread_id = ?",
            (thread_id,),
        )
        row = await cur.fetchone()
        await cur.close()
        if row is None:
            return None
        try:
            return json.loads(row[0])
        except json.JSONDecodeError as e:
            logger.warning("interrupt payload JSON parse failed thread=%s: %s", thread_id, e)
            return None

    async def clear_interrupt(self, thread_id: str) -> None:
        """用户确认/拒绝后清掉 pending interrupt。无则 no-op。"""
        conn = self._require_conn()
        await conn.execute(
            "DELETE FROM loop_interrupts WHERE thread_id = ?",
            (thread_id,),
        )
        await conn.commit()

    # ------------------------------------------------------------
    # 整 thread 管理
    # ------------------------------------------------------------

    async def delete_thread(self, thread_id: str) -> None:
        """永久删除 thread 所有 messages + 任何 pending interrupt。"""
        conn = self._require_conn()
        await conn.execute("DELETE FROM loop_messages WHERE thread_id = ?", (thread_id,))
        await conn.execute("DELETE FROM loop_interrupts WHERE thread_id = ?", (thread_id,))
        await conn.commit()

    async def list_threads(self) -> list[str]:
        """所有有 messages 的 thread_id。运维 / 调试用。"""
        conn = self._require_conn()
        cur = await conn.execute(
            "SELECT DISTINCT thread_id FROM loop_messages ORDER BY thread_id",
        )
        rows = await cur.fetchall()
        await cur.close()
        return [r[0] for r in rows]
