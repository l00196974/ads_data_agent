"""LLM 调用级指标持久化：每次 on_chat_model_end 一行。

为什么单独建表（不和 conversation_events 合）：
  - events 是"思考过程"事件流（step / artifact_updated）—— 离散动作；
  - metrics 是"性能 / 用量"测量值（input/output tokens / TPS / TTFT）—— 数值数据。
  两类数据查询模式完全不同：events 顺序回放，metrics 聚合统计。
  混表会让 list 端点又要 filter kind 又要解 JSON，索引也不友好。

为什么单独存"每次调用"而不是直接存"会话累计"：
  - 子 Agent 一次派工常烧主 Agent 5-10x 的 tokens，需要按 subagent 拆分；
  - 时序图 / 性能下钻分析需要明细；
  - 累计值随时 SUM 出来：单一数据源，永远一致（不会有"累计字段和明细对不上"）。

存储：复用 data/checkpoints.db。表 conversation_metrics。
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
    global _db_path
    _db_path = Path(data_dir) / "checkpoints.db"
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    with _lock, _conn() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversation_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                call_seq INTEGER NOT NULL,
                subagent TEXT,
                model TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER,
                cache_read_tokens INTEGER,
                duration_ms INTEGER,
                ttft_ms INTEGER,
                tps REAL,
                ts TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_metrics_conv
                ON conversation_metrics(user_id, conversation_id, id);
            """
        )
        con.commit()
    logger.info("conversation_metrics init: db=%s", _db_path)


def _conn() -> sqlite3.Connection:
    if _db_path is None:
        raise RuntimeError("conversation_metrics not initialized; call init(data_dir) first")
    con = sqlite3.connect(str(_db_path))
    con.row_factory = sqlite3.Row
    return con


def append(
    user_id: str,
    conversation_id: str,
    turn_id: str,
    *,
    call_seq: int,
    subagent: str | None,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    total_tokens: int | None,
    cache_read_tokens: int | None,
    duration_ms: int | None,
    ttft_ms: int | None,
    tps: float | None,
) -> None:
    """落一条 LLM 调用 metrics。init 之前调用 = no-op（CLI 测试场景）。

    None 字段允许：少数 OpenAI-compat 厂商不上报 usage_metadata，前端显示"-"。
    """
    if _db_path is None:
        return
    with _lock, _conn() as con:
        con.execute(
            """INSERT INTO conversation_metrics (
                user_id, conversation_id, turn_id, call_seq, subagent, model,
                input_tokens, output_tokens, total_tokens, cache_read_tokens,
                duration_ms, ttft_ms, tps, ts
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                user_id, conversation_id, turn_id, call_seq, subagent, model,
                input_tokens, output_tokens, total_tokens, cache_read_tokens,
                duration_ms, ttft_ms, tps,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        con.commit()


def summary_for_conversation(user_id: str, conversation_id: str) -> dict:
    """聚合统计：sidebar 项展示用。SUM 几个数 + COUNT，sqlite 几百行毫秒级。"""
    if _db_path is None:
        return _empty_summary()
    with _conn() as con:
        row = con.execute(
            """SELECT
                COUNT(*) AS calls,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                COALESCE(SUM(duration_ms), 0) AS duration_ms
            FROM conversation_metrics
            WHERE user_id=? AND conversation_id=?""",
            (user_id, conversation_id),
        ).fetchone()
    return dict(row) if row else _empty_summary()


def summary_for_user_conversations(user_id: str) -> dict[str, dict]:
    """一次性聚合用户所有会话的累计指标。
    返回 {conversation_id: summary_dict}。
    用 GROUP BY 单查，给 list_conversations endpoint 减少 N+1 查询。"""
    if _db_path is None:
        return {}
    with _conn() as con:
        rows = con.execute(
            """SELECT
                conversation_id,
                COUNT(*) AS calls,
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(total_tokens), 0) AS total_tokens,
                COALESCE(SUM(cache_read_tokens), 0) AS cache_read_tokens,
                COALESCE(SUM(duration_ms), 0) AS duration_ms
            FROM conversation_metrics
            WHERE user_id=?
            GROUP BY conversation_id""",
            (user_id,),
        ).fetchall()
    return {r["conversation_id"]: dict(r) for r in rows}


def list_for_conversation(user_id: str, conversation_id: str) -> list[dict]:
    """按时间顺序拉 LLM 调用明细——会话详情面板（V2）画时序图用。"""
    if _db_path is None:
        return []
    with _conn() as con:
        rows = con.execute(
            """SELECT * FROM conversation_metrics
            WHERE user_id=? AND conversation_id=?
            ORDER BY id ASC""",
            (user_id, conversation_id),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_for_conversation(user_id: str, conversation_id: str) -> int:
    """永久删除会话时双删。"""
    if _db_path is None:
        return 0
    with _lock, _conn() as con:
        cur = con.execute(
            "DELETE FROM conversation_metrics WHERE user_id=? AND conversation_id=?",
            (user_id, conversation_id),
        )
        con.commit()
    return cur.rowcount


def _empty_summary() -> dict:
    return {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cache_read_tokens": 0,
        "duration_ms": 0,
    }
