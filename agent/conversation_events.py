"""会话内"思考过程"事件流持久化（step / artifact_updated）。

为什么需要：
  langgraph checkpointer 只存 messages（user/ai/tool）——它不知道 runner 层
  在 SSE 上推过哪些"工具开始/结束 / artifact 创建"事件。历史会话回看时
  用户希望看到当时的工具调用记录（"原来这个数字是 query-metrics 跑出来的"），
  所以把 step / artifact_updated 事件也持久化到独立表。

为什么不持久化 token：
  token 量大（每条 ai 消息百到千个 token），频繁写 sqlite 开销大；而且
  token 累积起来就是 AIMessage.content，langgraph checkpointer 已经存了——
  历史回看时直接从 messages 拿 ai 文本即可。

为什么不持久化 progress：
  progress 是"AI 正在规划任务..."这种过渡状态文字，事后回看无价值。

turn_id 的作用：
  一次 chat 请求 = 一个 turn（user 提问 + agent 回复）。同一会话多轮时
  事件按 turn_id 归簇，前端能正确把"思考分组"插到对应的 user/ai 之间。
  turn_id 由 chat() endpoint 在每次请求开始生成，传给 channel。

存储：复用 `data/checkpoints.db`。表 `conversation_events`。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import Lock


logger = logging.getLogger(__name__)

_lock = Lock()
_db_path: Path | None = None


def init(data_dir: str | Path) -> None:
    """启动时调一次。复用 data/checkpoints.db，新增表（IF NOT EXISTS）。"""
    global _db_path
    _db_path = Path(data_dir) / "checkpoints.db"
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, _conn() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                ts TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_conv
                ON conversation_events(user_id, conversation_id, id);
            """
        )
        con.commit()
    logger.info("conversation_events init: db=%s", _db_path)


def _conn() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("conversation_events not initialized; call init(data_dir) first")
    con = sqlite3.connect(str(_db_path))
    con.row_factory = sqlite3.Row
    return con


def append(
    user_id: str,
    conversation_id: str,
    turn_id: str,
    kind: str,
    payload: dict,
) -> None:
    """落一条事件。同步写 sqlite（~1ms）。

    init 之前调用 = no-op（CLI 测试场景方便，不强制要求 init）。
    """
    if _db_path is None:
        return
    with _lock, _conn() as con:
        con.execute(
            "INSERT INTO conversation_events (user_id, conversation_id, turn_id, kind, payload, ts) "
            "VALUES (?,?,?,?,?,?)",
            (
                user_id,
                conversation_id,
                turn_id,
                kind,
                json.dumps(payload, ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        con.commit()


def list_for_conversation(user_id: str, conversation_id: str) -> list[dict]:
    """按时间顺序拉取该会话的所有事件，每条带 turn_id 让前端能按回合分组。"""
    if _db_path is None:
        return []
    with _conn() as con:
        rows = con.execute(
            "SELECT turn_id, kind, payload, ts FROM conversation_events "
            "WHERE user_id=? AND conversation_id=? ORDER BY id ASC",
            (user_id, conversation_id),
        ).fetchall()
    return [
        {
            "turn_id": r["turn_id"],
            "kind": r["kind"],
            "payload": json.loads(r["payload"]),
            "ts": r["ts"],
        }
        for r in rows
    ]


def delete_for_conversation(user_id: str, conversation_id: str) -> int:
    """删除该会话所有事件（永久删除会话时调，与 conversation_meta.delete_permanent 配套）。"""
    if _db_path is None:
        return 0
    with _lock, _conn() as con:
        cur = con.execute(
            "DELETE FROM conversation_events WHERE user_id=? AND conversation_id=?",
            (user_id, conversation_id),
        )
        con.commit()
    return cur.rowcount
