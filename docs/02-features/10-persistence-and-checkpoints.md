# 10 持久化与 Checkpoint（Persistence & Checkpoints）

项目用两个独立的 SQLite 数据库做持久化，分别管 langgraph 对话状态和 deepagents 内置工具的 KV 存储。两者**生命周期对齐**——FastAPI lifespan 异步初始化，shutdown 异步清理。

## 一、两类持久化的分工

| 维度 | Checkpointer | Store |
|---|---|---|
| 用途 | langgraph 对话状态机 checkpoint（messages 数组、当前节点等） | deepagents 内置工具的 KV backend（virtual filesystem） |
| 实现 | `AsyncSqliteSaver` | `AsyncSqliteStore`（默认） |
| DB 文件 | `data/checkpoints.db` | `data/store.db` |
| 隔离粒度 | `thread_id` 行级 | namespace tuple |
| 生命周期管理 | `agent/checkpointer.py` | `agent/store.py` |
| 当前业务依赖 | ✅ 强依赖（每次对话都用） | ⏳ 弱依赖（业务流程未直接用） |

**Checkpointer 是核心**——没它每次对话都要全量重发历史。
**Store 是预留**——deepagents 的 `write_file` / `read_file` 等内置工具用它当 backend，
但当前业务流程都走 SKILL.md 包，没用到这些内置工具。保留正确实现是为未来扩展。

---

## 二、Checkpointer：`AsyncSqliteSaver`

### 2.1 生命周期

`agent/checkpointer.py:42-79`：

```python
_db_path = Path(load_config().persistence.data_dir) / "checkpoints.db"
_db_path.parent.mkdir(parents=True, exist_ok=True)

_exit_stack: Optional[AsyncExitStack] = None
_checkpointer: Optional[AsyncSqliteSaver] = None


async def init_checkpointer() -> AsyncSqliteSaver:
    global _checkpointer, _exit_stack
    if _checkpointer is not None:
        return _checkpointer
    _exit_stack = AsyncExitStack()
    _checkpointer = await _exit_stack.enter_async_context(
        AsyncSqliteSaver.from_conn_string(str(_db_path))
    )
    await _checkpointer.setup()
    return _checkpointer


async def close_checkpointer() -> None:
    global _checkpointer, _exit_stack
    if _exit_stack is not None:
        await _exit_stack.aclose()
    _exit_stack = None
    _checkpointer = None


def get_checkpointer() -> AsyncSqliteSaver:
    if _checkpointer is None:
        raise RuntimeError("Checkpointer not initialized. ...")
    return _checkpointer
```

### 2.2 为什么要 async init

`AsyncSqliteSaver.from_conn_string` 是 async context manager—`aiosqlite` 连接必须在
event loop 里建立。FastAPI lifespan 是 async 的，正好符合。

`AsyncExitStack` 管理 contextmanager 生命周期，shutdown 时 `_exit_stack.aclose()`
触发底层连接清理。

### 2.3 main.py lifespan 接入

```python
@asynccontextmanager
async def lifespan(_app: FastAPI):
    await init_checkpointer()
    await init_store()
    try:
        yield
    finally:
        await close_store()
        await close_checkpointer()
```

源码：`main.py:17-26`。

### 2.4 隔离粒度

每条对话 thread 由 `thread_id = "{user_id}_{conversation_id}"` 标识。SQLite checkpoint
表里 `thread_id` 是行键——不同 thread 互不可见，同一文件内可同时存上百万 thread
（受文件大小限制）。

详见 [06-multi-user-isolation.md](./06-multi-user-isolation.md)。

---

## 三、Store：`AsyncSqliteStore`（默认）

### 3.1 backend 选择器

`agent/store.py:32-71`：

```python
async def init_store() -> BaseStore:
    cfg = load_config()
    backend = cfg.persistence.store_backend

    if backend == "memory":
        _store = InMemoryStore()
        return _store

    if backend == "sqlite":
        db_path = Path(cfg.persistence.data_dir) / "store.db"
        _exit_stack = AsyncExitStack()
        _store = await _exit_stack.enter_async_context(
            AsyncSqliteStore.from_conn_string(str(db_path))
        )
        await _store.setup()
        return _store

    if backend == "mysql":
        raise NotImplementedError("MySQL store backend is a stub. ...")

    raise ValueError(f"Unknown persistence.store_backend: {backend!r}")
```

### 3.2 `get_store()` 懒初始化

```python
def get_store() -> BaseStore:
    """同步路径懒初始化为 InMemoryStore——避免单元测试必须 await init"""
    global _store
    if _store is None:
        _store = InMemoryStore()
    return _store
```

为什么不 raise like checkpointer？因为 store 当前业务**不直接用**——但 `agent/builder.py`
在 `create_deep_agent(store=get_store())` 时同步调它。如果生产路径没 init 就 raise，
单元测试会全炸（测试都没 await init_store）。

懒初始化让：
- 单元测试拿到 InMemoryStore，行为可预测
- 生产 lifespan await init_store 后，singleton 已经替换为 sqlite，build_agent 拿到 sqlite

### 3.3 backend 配置

```yaml
# config.yaml
persistence:
  type: local
  data_dir: ./data
  store_backend: sqlite     # memory | sqlite | mysql(stub)
```

| backend | 用途 |
|---|---|
| `memory` | 单元测试 / 快速本地调试 |
| `sqlite` | **生产默认**，进程重启不丢 |
| `mysql` | stub，多副本场景路径（待实现） |

### 3.4 测试

`tests/unit/agent/test_store.py`（6 个测试）：

- `test_get_store_lazy_inits_to_in_memory_for_sync_callers` — 懒初始化行为
- `test_init_store_memory_backend_returns_in_memory` — memory 路径
- `test_init_store_unknown_backend_raises` — 未知 backend 报 ValueError
- `test_init_store_mysql_backend_is_stub` — mysql 明确 NotImplementedError（避免误以为可用）
- `test_init_store_idempotent` — 多次 init 只创建一次实例
- `test_persistence_config_defaults_to_sqlite` — 默认 backend 是 sqlite

### 3.5 SQLite 烟测

```bash
.venv/Scripts/python.exe -c "
import asyncio
async def smoke():
    from agent.core import init_checkpointer, init_store, get_store, close_store, close_checkpointer
    await init_checkpointer()
    await init_store()
    s = get_store()
    print('store class:', type(s).__name__)
    await s.aput(('test',), 'key1', {'data': 'hello'})
    item = await s.aget(('test',), 'key1')
    print('roundtrip:', item.value if item else None)
    await close_store()
    await close_checkpointer()
asyncio.run(smoke())
"
```

期望输出：`store class: AsyncSqliteStore` + `roundtrip: {'data': 'hello'}`。

---

## 四、Singleton 进程间共享

`_checkpointer` 与 `_store` 都是模块级 singleton——同一 worker 内**所有** chat 请求共享。
不同 worker 各自有独立的 singleton 但指向同一 SQLite 文件，靠 SQLite 的进程级锁协调。

多副本部署（多 worker）的边界：
- ✅ 多个 worker 读写同一 `.db` 文件可行（aiosqlite 支持）
- ⚠️ 写并发受 SQLite **全局写锁**限制——并发量上来后写排队
- ⚠️ 必须挂同一持久卷（NFS / SAN），让所有副本看到同一文件
- ⚠️ 不同副本的 thread state 一致性靠 SQLite 事务，但 `read-modify-write` 模式下仍可能有
  race condition（同 thread_id 并发 chat 请求时）—— langgraph 内部用 lock 保护，但
  不一定覆盖所有场景

→ **多副本生产建议直接换 Postgres**。`PostgresSaver` 是 langgraph 官方支持，
`agent/checkpointer.py::init_checkpointer` 是唯一改动点；store 同理实现 MySQL/Postgres 适配。

---

## 五、Singleton 重置（测试用）

每个 store 测试前要重置模块级 singleton：

```python
def _reset_store_module():
    import agent.store as s
    s._store = None
    s._exit_stack = None
```

否则跨 test 状态泄漏（前一个 test init 了 sqlite store，后一个 test 期望 InMemoryStore 失败）。

---

## 设计决策记录（ADR）

### ADR-010：默认 store backend 改为 sqlite（之前是 InMemoryStore）

**Context**：早期版本 `agent/builder.py:_store = InMemoryStore()` 模块级单例。问题：
1. 进程重启即丢——任何 deepagents 内置工具写过的数据都消失
2. 多副本部署完全失同步——副本 A 写的数据副本 B 看不到
3. 隐式依赖："只要业务不用 store，进程内就没事"——风险藏起来等扩展时爆

**Decision**：默认 backend 改为 `sqlite`，`AsyncSqliteStore` 落 `data/store.db`。
保留 `memory` 作为可选 backend（单元测试 / 本地快速调试）。引入 lifespan 异步管理。

**Consequences**：
- ✅ 进程重启不丢数据
- ✅ 同卷多副本可读共享
- ✅ 行为对齐 checkpointer，认知一致
- ⚠️ 单元测试不能直接 build_agent 然后访问 store——需 await init 或用 lazy fallback
- ⚠️ 单 SQLite 文件多并发写有锁竞争——规模化前要换 Postgres/MySQL

---

### ADR-011：`get_store()` 懒初始化为 InMemoryStore 的 trade-off

**Context**：checkpointer 的 `get_checkpointer()` 在未 init 时直接 raise——强制调用方
先 await init。同样的契约用在 store 上会让所有不调 init 的单元测试全炸（27 个测试要改）。

**Decision**：`get_store()` 在 `_store is None` 时懒初始化为 `InMemoryStore`。
单元测试无感；生产 lifespan await init_store 后 singleton 已经被替换为 sqlite。

**Consequences**：
- ✅ 单元测试零改动
- ✅ 生产路径正常工作
- ⚠️ 静默 fallback 是隐性约定——新 contributor 可能不知道"忘了调 init 也能跑但跑成 InMemoryStore"
- ⚠️ 如果生产由于某种原因 lifespan 没跑（错误的启动方式），运行时会用 InMemoryStore，
  数据丢失但不报错——靠运维监控发现

**Alternatives**：
- raise like checkpointer：所有测试要改，迁移成本大
- 默认 backend 设 memory：违背 ADR-010 的"生产默认 sqlite"决策
- 启动时强制 init：增加新部署者的上手门槛
