"""Store lifecycle + backend selector.

Store 是 deepagents 内置 ``FilesystemMiddleware`` 等工具的后端——存放 agent
持久化数据（virtual file system，按 namespace 隔离）。当前业务流程没用到 store，
但保留正确的实现以备未来 user data / 长期 memory 扩展。

为什么不再用 ``InMemoryStore`` 做生产默认：进程重启即丢、多副本部署失同步。
``AsyncSqliteStore`` 落 SQLite 文件，进程重启不丢；多副本若挂同一卷可读共享
（写并发受 SQLite 全局锁）。生产规模需要 MySQL/Postgres 时按相同 BaseStore
接口扩展（见 ``MysqlStore`` stub）。

生命周期模式与 ``agent/checkpointer.py`` 对齐：FastAPI lifespan 启动时
``await init_store()``、关闭时 ``await close_store()``。同步单元测试场景下
``get_store()`` 会懒初始化为 ``InMemoryStore``——避免测试必须跑 event loop。
"""
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Optional

from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore
from langgraph.store.sqlite import AsyncSqliteStore

from agent.config import load_config


_exit_stack: Optional[AsyncExitStack] = None
_store: Optional[BaseStore] = None


async def init_store() -> BaseStore:
    """Idempotent async init. 调一次即可（FastAPI lifespan 上做）。

    根据 ``cfg.persistence.store_backend`` 选择 backend：
      - ``"memory"``：InMemoryStore（单 worker 测试或快速本地用）
      - ``"sqlite"``（默认）：AsyncSqliteStore，落 ``data/store.db``
      - ``"mysql"``：未实现（stub，raise NotImplementedError）
    """
    global _store, _exit_stack
    if _store is not None:
        return _store

    cfg = load_config()
    backend = cfg.persistence.store_backend

    if backend == "memory":
        _store = InMemoryStore()
        return _store

    if backend == "sqlite":
        db_path = Path(cfg.persistence.data_dir) / "store.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _exit_stack = AsyncExitStack()
        _store = await _exit_stack.enter_async_context(
            AsyncSqliteStore.from_conn_string(str(db_path))
        )
        await _store.setup()
        return _store

    if backend == "mysql":
        raise NotImplementedError(
            "MySQL store backend is a stub. 实现思路：仿 langgraph.store.sqlite "
            "包装 aiomysql connection，实现 BaseStore 的 aput/aget/abatch 等接口。"
        )

    raise ValueError(f"Unknown persistence.store_backend: {backend!r}")


async def close_store() -> None:
    global _store, _exit_stack
    if _exit_stack is not None:
        await _exit_stack.aclose()
    _exit_stack = None
    _store = None


def get_store() -> BaseStore:
    """Return the active store. 同步单元测试懒初始化为 InMemoryStore——
    生产路径上 lifespan 已经 await init_store()，此处直接拿到 sqlite 实例。"""
    global _store
    if _store is None:
        _store = InMemoryStore()
    return _store
