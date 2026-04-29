# 06 多用户隔离（Multi-User Isolation）

> ⏳ **占位**：本篇骨架已建，内容待补。

**关联源码**：`agent/session.py`、`agent/user_space.py`、`api/chat.py:115-158`（thread_id 构造）

**计划覆盖**：
- 三层隔离机制：
  - LangGraph `thread_id = "{user_id}_{conversation_id}"` 隔离对话状态
  - 文件系统 `data/{user_id}/` 隔离 skill 上传与 agents.md
  - SQLite checkpoint 按 thread_id 行级隔离（同一文件，不同 thread）
- 多对话切换（`/api/chat/{user_id}/reset` 生成新 conversation_id）
- 当前认证状态：mock 阶段，user_id 透传，**未实现 SSO/JWT**
- 横向扩展边界：单机 SQLite ↔ 多副本要换 Postgres

**ADR 占位**：
- ADR-006: 为什么 thread_id 用 `{user_id}_{conversation_id}` 复合键
