"""会话元数据：标题 / 创建时间 / 最近活跃 / 消息数 / 归档状态。

为什么单独建一张表而不是从 langgraph checkpointer 反推：
  - AsyncSqliteSaver 没有 `list_threads` API，强行从 checkpoints 表反推
    既慢（要遍历所有 thread 解 messages 取首条 user msg 当标题）也拿不到
    "归档状态"等运行时元数据；
  - 单独表能让前端 sidebar 列表查询一行 SQL（按 last_active_at 倒序）
    毫秒级返回，且支持重命名 / 软删 / 永久删除。

写入触发点（最少侵入）：
  - 首次 / 后续发消息 → `api/chat.py::chat()` 调用 upsert
  - 重命名 / 归档 / 恢复 / 永久删除 → 各自专属 endpoint

存储位置：复用 `data/checkpoints.db` 同一个 SQLite 文件——单文件备份方便、
事务一致性强、不引入新文件。表名 `conversations`，schema 见 init()。
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import Lock


logger = logging.getLogger(__name__)

_lock = Lock()
_db_path: Path | None = None


def init(data_dir: str | Path) -> None:
    """启动时调一次。复用 data/checkpoints.db，新增 conversations 表（IF NOT EXISTS）。
    幂等：重复调 = 重新指向同一文件 + 确保表存在。"""
    global _db_path
    _db_path = Path(data_dir) / "checkpoints.db"
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, _conn() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                user_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_active_at TEXT NOT NULL,
                message_count INTEGER NOT NULL DEFAULT 0,
                archived_at TEXT,
                PRIMARY KEY (user_id, conversation_id)
            );
            CREATE INDEX IF NOT EXISTS idx_conv_user_active
                ON conversations(user_id, archived_at, last_active_at DESC);
            """
        )
        con.commit()
    logger.info("conversation_meta init: db=%s", _db_path)


def _conn() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("conversation_meta not initialized; call init(data_dir) first")
    con = sqlite3.connect(str(_db_path))
    con.row_factory = sqlite3.Row
    return con


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _make_title(message: str) -> str:
    """首条消息前 20 字作为默认标题——零成本、能识别 90% 场景。
    用户可在 sidebar 里随时改。"""
    s = (message or "").strip().replace("\n", " ")
    if not s:
        return "新对话"
    return s[:20] + ("…" if len(s) > 20 else "")


def upsert(user_id: str, conversation_id: str, first_message: str = "") -> dict | None:
    """记录一次发消息：第一次 INSERT（标题取 first_message 前 20 字），
    后续 UPDATE last_active_at + message_count++。

    幂等：同 user/conv 多次调用不会创建多行。
    upsert 同时会**清掉 archived_at**——用户在归档里继续追问意味着想恢复。
    """
    now = _now()
    with _lock, _conn() as con:
        row = con.execute(
            "SELECT message_count FROM conversations WHERE user_id=? AND conversation_id=?",
            (user_id, conversation_id),
        ).fetchone()
        if row is None:
            con.execute(
                "INSERT INTO conversations (user_id, conversation_id, title, created_at, "
                "last_active_at, message_count, archived_at) VALUES (?,?,?,?,?,?,NULL)",
                (user_id, conversation_id, _make_title(first_message), now, now, 1),
            )
        else:
            con.execute(
                "UPDATE conversations SET last_active_at=?, message_count=message_count+1, "
                "archived_at=NULL WHERE user_id=? AND conversation_id=?",
                (now, user_id, conversation_id),
            )
        con.commit()
    return get(user_id, conversation_id)


def list_for_user(user_id: str, archived: bool = False, limit: int = 50) -> list[dict]:
    """列出用户的会话（默认未归档），按 last_active_at 倒序。"""
    where = "user_id=? AND archived_at IS " + ("NOT NULL" if archived else "NULL")
    with _conn() as con:
        rows = con.execute(
            f"SELECT * FROM conversations WHERE {where} ORDER BY last_active_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get(user_id: str, conversation_id: str) -> dict | None:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM conversations WHERE user_id=? AND conversation_id=?",
            (user_id, conversation_id),
        ).fetchone()
    return dict(row) if row else None


def rename(user_id: str, conversation_id: str, title: str) -> bool:
    """重命名会话标题。返回是否更新成功（False = 找不到 / 标题为空）。
    标题截断到 50 字符防止极端输入。"""
    title = (title or "").strip()
    if not title:
        return False
    with _lock, _conn() as con:
        cur = con.execute(
            "UPDATE conversations SET title=? WHERE user_id=? AND conversation_id=?",
            (title[:50], user_id, conversation_id),
        )
        con.commit()
    return cur.rowcount > 0


def archive(user_id: str, conversation_id: str) -> bool:
    """软删：标记 archived_at=now。仍在 db 里，只是默认列表不显示。"""
    with _lock, _conn() as con:
        cur = con.execute(
            "UPDATE conversations SET archived_at=? WHERE user_id=? AND conversation_id=? AND archived_at IS NULL",
            (_now(), user_id, conversation_id),
        )
        con.commit()
    return cur.rowcount > 0


def restore(user_id: str, conversation_id: str) -> bool:
    """从归档恢复（archived_at = NULL）。"""
    with _lock, _conn() as con:
        cur = con.execute(
            "UPDATE conversations SET archived_at=NULL WHERE user_id=? AND conversation_id=?",
            (user_id, conversation_id),
        )
        con.commit()
    return cur.rowcount > 0


def delete_permanent(user_id: str, conversation_id: str) -> bool:
    """硬删 meta 行。**注意**：调用方需自行调 checkpointer.adelete_thread() 双删
    checkpointer 数据，否则该 thread 的 langgraph 状态会残留。"""
    with _lock, _conn() as con:
        cur = con.execute(
            "DELETE FROM conversations WHERE user_id=? AND conversation_id=?",
            (user_id, conversation_id),
        )
        con.commit()
    return cur.rowcount > 0
