# 部署模式（Deployment Modes）

本项目支持三种部署形态：本地开发 / 单端口生产 / 多副本集群。多副本场景目前**框架就绪但实现 stub**——`RedisChannelRegistry` 与 `MysqlStore` 仍待实现（详见 [05-known-issues](../05-known-issues-and-roadmap/README.md)）。

---

## 一、本地开发（前后端分离）

最常用形态，前后端独立 dev server，互通过 vite proxy。

```bash
# 终端 1：后端 (8000)
.venv/Scripts/python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000

# 终端 2：前端 (5173)
cd frontend && npm run dev
```

vite proxy 配置（`frontend/vite.config.js`）把 `/api/*` 请求转发到 `:8000`，
浏览器只需打开 `http://localhost:5173`。

**特点**：
- 前端热重载（修改 .vue 文件即时生效）
- 后端 `--reload` 监听 .py 文件变更自动重启
- CORS 默认放通 `5173` / `8000`

---

## 二、单端口生产部署

`npm run build` 把前端打成静态文件到 `frontend/dist/`，FastAPI 启动时自动 mount。

```bash
# 1. 构建前端
cd frontend && npm run build && cd ..

# 2. 启动后端（同时提供 API + SPA）
.venv/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
```

`main.py:39-46`：

```python
frontend_dist = Path("frontend/dist")
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        return FileResponse(str(frontend_dist / "index.html"))
```

访问 `http://<host>:8000`：API 走 `/api/*`，前端走其它任意路径（SPA 路由由 Vue Router 处理）。

**特点**：
- 单端口，反向代理 / 防火墙规则简化
- 不需要单独跑 frontend dev server
- 适合小规模生产 / 内网部署

---

## 三、多副本部署（待实现）

多 worker / 多副本场景需要解决三个共享状态问题：

| 状态 | 当前默认（单 worker） | 多副本路径 | 状态 |
|---|---|---|---|
| Checkpoint（langgraph thread state） | `AsyncSqliteSaver` (data/checkpoints.db) | `PostgresSaver` 共享同张表 | ⏳ 待实现 |
| Store（FilesystemMiddleware backend） | `AsyncSqliteStore` (data/store.db) | `MysqlStore` / `PostgresStore` | ⏳ stub，参见 `agent/store.py:78-83` |
| ChannelRegistry（HitL confirm 路由） | `InMemoryChannelRegistry`（进程内 dict） | `RedisChannelRegistry`（pub/sub 跨 worker） | ⏳ stub，参见 `api/channel/registry.py:75-115` |

> ⚠️ **不要直接在多 worker 跑当前默认配置**——会出现 confirm 失败、agent 状态分裂等问题。
> 部署前必须实现 / 启用上述三个组件的对应 backend。详细策略见
> [05-known-issues-and-roadmap/README.md](../05-known-issues-and-roadmap/README.md)。

### 多副本时的 SQLite 注意事项

如果**保留 SQLite** 想做多副本（不切 Postgres）：

- 必须挂同一持久卷（NFS / SAN），让所有副本看到同一 `.db` 文件
- 写并发受 SQLite **全局写锁**限制，并发写多了会排队
- aiosqlite 的连接池 + langgraph 的事务粒度尚未压测过——规模化前建议直接切 Postgres

---

## 四、容器化（占位）

当前未提供 Dockerfile 与 docker-compose 文件。规划见 [roadmap.md](../05-known-issues-and-roadmap/roadmap.md::P1)。

最小思路（未实施）：

```dockerfile
FROM python:3.13-slim
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . /app
WORKDIR /app
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

需要解决的事项：
- 前端构建：多阶段 build（node:20 → python:3.13）
- SKILL.md 包的 Node 依赖：每个 skill 都跑 `npm install`，或在镜像里预装 node + sharp 等原生依赖
- 数据卷：`data/` 必须挂 persistent volume

---

## 五、生产部署 checklist

| 项 | 默认 | 上线前应确认 |
|---|---|---|
| `LLM_API_KEY` / `ANTHROPIC_API_KEY` | 空 | 已配置真实 key |
| `cors_origins` | `["http://localhost:5173", "http://localhost:8000"]` | 改为实际前端域名（避免 `*`） |
| `interrupt_on` 工具列表 | `[delete_adgroups, modify_budget, pause_campaign]`（占位） | 改为部署侧 SKILL.md 实际暴露的敏感子命令 |
| `data_dir` | `./data` | 改为持久卷路径 |
| `skills.md_dir` | `./skills` | 部署侧 skill 包目录 |
| `subagents` | `[]` | 按业务需要配置 / 或保持空（关闭 task 工具） |
| 认证 | 透传 user_id | **mock 阶段不可生产用** —— 接 SSO/JWT 后再上线 |
| 多副本 | 未实现 stub | 实现 RedisChannelRegistry + MysqlStore 后再上 |
| Prometheus / OpenTelemetry | 未实现 | 占位 |

---

## 关联源码

- `main.py` — FastAPI app + lifespan + 静态文件 mount
- `frontend/vite.config.js` — vite proxy 配置
- `agent/checkpointer.py` — checkpoint lifecycle
- `agent/store.py` — store lifecycle
- `api/channel/registry.py` — registry 抽象
