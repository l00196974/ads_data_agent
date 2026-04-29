# 10 持久化与 Checkpoint（Persistence & Checkpoints）

> ⏳ **占位**：本篇骨架已建，内容待补。

**关联源码**：`agent/checkpointer.py`、`agent/store.py`、`main.py`（lifespan）、`agent/config.py::PersistenceConfig`

**计划覆盖**：
- 两类持久化的分工：
  - `AsyncSqliteSaver`（checkpointer）：langgraph 对话状态机 checkpoint，按 thread_id 隔离
  - `AsyncSqliteStore`（store）：deepagents 内置工具的 KV 存储（virtual filesystem）
- FastAPI lifespan 异步初始化模式（aiosqlite 必须在事件循环里）
- backend 选择器：memory / sqlite（默认）/ mysql（stub）
- get_store() 同步路径懒初始化为 InMemoryStore（让单元测试免于 await）
- 多副本部署边界

**ADR 占位**：
- ADR-010: 为什么默认 backend 改为 sqlite（InMemoryStore 进程重启即丢、多副本失同步）
- ADR-011: get_store() 懒初始化的 trade-off
