# 部署模式（Deployment Modes）

> ⏳ **占位**：本篇骨架已建，内容待补。

**关联源码**：`main.py`（lifespan + 静态文件 mount）、`frontend/vite.config.js`

**计划覆盖**：
- 开发模式：前端 dev server (5173) + 后端 (8000) 通过 vite proxy 通信
- 生产单端口模式：`npm run build` 后 main.py 自动 mount `frontend/dist` 到 `/`
- 多副本部署的当前限制：
  - InMemoryStore → 必须切到 SqliteStore（已默认）
  - channel_registry 必须切到 RedisChannelRegistry（stub，待实现）
  - SQLite checkpoint 多副本只能挂同一 NFS 卷（写并发受 SQLite 全局锁）
- Docker 容器化（占位）
- 生产部署 checklist
