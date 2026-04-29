# 02 架构总览（Architecture at a Glance）

> ⏳ **占位**：本篇骨架已建，内容待补。

**关联源码**：`README.md`、`main.py`、`agent/builder.py`、`api/channel/`、`agent/skill_loader.py`

**计划覆盖**：
- 单图覆盖：FastAPI ↔ AgentRunner ↔ deepagents ↔ AsyncSqliteSaver/Store ↔ 前端 SSE
- 关键术语速览（指向 `02-features/01-skill-tool-channel-model.md`）
- 请求生命周期（chat 请求 → SSE 流 → HitL → 持久化）
- 数据流向（用户 → channel → tool → skill → channel → 用户）

