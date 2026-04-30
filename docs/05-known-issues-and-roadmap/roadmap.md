# 路线图（Roadmap）

按 P0 / P1 / P2 优先级分组，每条引用对应的 known-issue 条目。
**P0 = 阻塞生产；P1 = 多副本化；P2 = 体验优化与运维能力**。

> 已知问题清单见 [README.md](./README.md)。

---

## P0：阻塞生产

必须完成才能上生产环境。

### P0.1 — auth：接 SSO/JWT 认证层

- **背景**：[A1 认证缺失](./README.md#a1-认证缺失)
- **范围**：
  - FastAPI 中间件解 JWT，注入 user_id 到 request state
  - 改 `api/chat.py` 等 router 从 request state 拿 user_id（弃用 path 参数 user_id 或强制一致）
  - 前端登录页改对接 SSO 跳转
- **影响**：所有 endpoint 路径与前端登录流程
- **测试**：单元测试加 auth middleware；E2E 验证未授权请求 401

### P0.2 — data-source：替换 mock 服务为真实数据源

- **背景**：[A2 业务 skill 是部署侧资产](./README.md#a2-业务-skill-是部署侧资产框架不内置)
- **范围**：
  - 真实指标查询 SKILL.md 包（替换 `metric-data-extractor` 对 mock 服务的依赖）
  - 真实异常检测 / 报表导出 SKILL.md 包
  - 数据源接入凭据管理（不能写死 SKILL.md 包内部）
- **影响**：纯部署侧工作，框架代码不动
- **测试**：每个新 skill 包独立的 E2E 用例

### P0.3 — interrupt-validation：interrupt_on 启动校验

- **背景**：[A3 interrupt_on 默认配置与实际 skill 不匹配](./README.md#a3-interrupt_on-默认配置与实际-skill-不匹配)
- **范围**：
  - `agent/builder.py` 启动时校验 `interrupt_on` 列表里的工具名都在已加载工具集（含 SKILL.md 子命令）里
  - 不在则 fail-fast 启动报错（避免静默放过）
- **影响**：本地一处改动 + 测试

---

## P1：多副本化

单 worker 跑稳后，要扩多副本部署解决的问题。

### P1.1 — redis-registry：实现 RedisChannelRegistry

- **背景**：[B1 RedisChannelRegistry 未实现](./README.md#b1-redischannelregistry-未实现)
- **设计方案**：见 `api/channel/registry.py::RedisChannelRegistry` docstring
- **范围**：
  - 实现 register / get / unregister 三方法（local dict + Redis SET 路由 metadata）
  - 实现 pub/sub 跨 worker 转发 confirm 信号
  - 配置项 `agent.channel_registry.{type, redis_url}`
  - 修改 `make_channel_registry("redis", ...)` 工厂
- **测试**：用 fakeredis 写单元测试；多副本 E2E 验证 confirm 路由

### P1.2 — postgres-checkpoint：切 PostgresSaver

- **背景**：[B3 PostgresSaver 未启用](./README.md#b3-postgressaver-checkpointer-未启用)
- **范围**：
  - 安装 `langgraph-checkpoint-postgres`
  - 改 `agent/checkpointer.py::init_checkpointer` 按 `cfg.persistence.checkpoint_backend` 选 backend
  - 加 `postgres` 配置（host / port / db / user / password / pool_size）
  - 数据迁移：从 SQLite 导出 checkpoint 到 Postgres（仅必要时，否则丢历史让用户重开对话）
- **测试**：E2E 验证 checkpoint 跨副本一致

### P1.3 — mysql-store：实现 MysqlStore

- **背景**：[B2 MysqlStore 未实现](./README.md#b2-mysqlstore--postgresstore-未实现)
- **范围**：仿 `langgraph.store.sqlite` 包装 aiomysql 实现 `BaseStore`
- **测试**：与 `tests/unit/agent/test_store.py` 对齐

### P1.4 — docker：Dockerfile + docker-compose

- **背景**：当前部署需要手动准备 Python venv + Node + 数据卷
- **范围**：
  - 多阶段 Dockerfile（node build 前端 → python runtime）
  - docker-compose 含 backend + redis + postgres
  - 数据卷挂载 `./data:/app/data`
  - 健康检查 endpoint
- **测试**：本地起 docker-compose 走通端到端

---

## P2：体验优化与运维能力

不阻塞但应处理。

### P2.1 — server-side-cancel：实现服务端 cancel

- **背景**：[B4 /cancel 是 stub](./README.md#b4-apichatuser_idcancel-是-stub)
- **范围**：建 task registry 按 conversation_id 索引 active task，cancel endpoint 拿 task 直接 cancel

### P2.2 — sse-keepalive：SSE 心跳与断线重连

- **背景**：[D3 SSE 心跳与断线重连未实现](./README.md#d3-sse-心跳与断线重连未实现)
- **范围**：
  - 后端：每 30s 推 `: keepalive\n\n`（SSE 注释，client 自动忽略）
  - 前端：检测断流 + 重发 chat 请求（带 conversation_id 续接历史）

### P2.3 — error-event：规范化 event: error

- **背景**：[D4 event: error 没规范化](./README.md#d4-event-error-没规范化)
- **范围**：
  - runner 区分异常类型，错误走 `channel.send_error(msg)` 专用方法
  - 前端 error handler 渲染独立的错误气泡（区别于普通 token）

### P2.4 — skill-hot-reload：skill 上传热加载

- **背景**：[D5 user skill 上传重启才生效](./README.md#d5-user-skill-上传重启才生效)
- **范围**：
  - upload endpoint 触发对应 user 的 build cache 失效
  - 下次该 user chat 时强制重 load_md_skills（不是模块级 cache）
- **风险**：进行中的对话仍用旧 prompt，新对话用新 prompt——文档明确告知

### P2.5 — observability：接 OpenTelemetry + Prometheus

- **背景**：[D8 观测能力有限](./README.md#d8-观测能力有限)
- **范围**：
  - OpenTelemetry SDK 埋点（HTTP 入口 / langgraph 事件 / 工具调用）
  - 暴露 `/metrics` Prometheus endpoint
  - 业务指标：每用户对话次数 / 活跃 skill 排行 / HitL approve 比例 / token 用量分布
  - 健康检查：`/healthz` / `/readyz`

### P2.6 — caching-strategy：prompt caching 策略

- **背景**：[D6 火山方舟 ARK + GLM 不支持 prompt caching](./README.md#d6-火山方舟-ark--glm-不支持-prompt-caching)
- **范围**：实测主要 provider 的 caching 行为，加 caching profile 配置

### P2.7 — skill-sandbox：用户 skill 执行沙箱

- **背景**：[D7 前端 zip 上传无沙箱](./README.md#d7-前端-zip-上传无沙箱)
- **范围**：
  - Docker per-skill 容器
  - 文件系统 chroot
  - 网络 ACL（只能访问指定数据源域名）
  - 资源限额（CPU / 内存 / 磁盘）
- **优先级低**：仅在公开互联网部署时必需

### P2.8 — deps：依赖管理整理

- **背景**：[D1 requirements-dev.txt 缺失](./README.md#d1-requirements-devtxt-缺失)
- **范围**：
  - `requirements.txt`：生产 runtime 依赖（精简）
  - `requirements-dev.txt`：pytest / pytest-asyncio / mypy / ruff
  - `requirements-providers.txt`：可选 LLM provider（langchain-anthropic / langchain-google-genai 等）
  - 文档化"按 provider 选装"的流程

### P2.9 — ci：GitHub Actions CI

- **背景**：[D2 CI 暂未配置](./README.md#d2-ci-暂未配置)
- **范围**：
  - pytest 单元测试
  - frontend build
  - PR ladder：lint → unit test → frontend build → 合并允许

### P2.10 — agent-md-config：子 Agent markdown 配置

- **背景**：当前 `config.yaml::agent.subagents` 内联 system_prompt，多个 / 长 prompt 时 yaml 难维护
- **设计**：
  - `agents/` 目录扫描 `.md` 文件 + frontmatter（仿 Claude Code）
  - frontmatter 字段对齐 SubAgentSpec
  - 实现 `agent/subagent_loader.py`（仿 `agent/skill_loader.py`）
- **触发条件**：当某个部署有 5+ 子 Agent 时再做

### P2.11 — artifact-v2: 共享 / 整理 / 搜索

- **背景**：v1 artifact 是 immutable + 扁平列表
- **范围**：collection 概念 + rename/move + 全文搜索 + retention policy
- **触发条件**：用户工作区累积 ≥50 份 artifact 时

---

## 时间线建议（参考）

如果以"上生产"为目标的最小路径：

```
Sprint 1 (2 周): P0.1 (auth) + P0.3 (interrupt-validation)
Sprint 2 (2 周): P0.2 (真实数据源 SKILL.md 包，跟业务团队配合)
Sprint 3 (2 周): P2.5 (observability 入门：log + 健康检查)
                 + P2.8 (deps 整理) + P2.9 (CI)
———— 上线门槛达标 ————

Sprint 4-5 (4 周): P1.1 (redis-registry) + P1.2 (postgres-checkpoint)
Sprint 6 (2 周):   P1.4 (docker) + 多副本验证
———— 多副本能力达标 ————

后续：按需挑 P2 条目
```

> ⚠️ 这个时间线只是结构示意，实际优先级和工作量需结合团队配置 / 业务诉求 / 上线时间窗调整。

---

## 关联文档

- 已知问题清单：[README.md](./README.md)
- 总体架构：[01-overview/02-architecture-at-a-glance.md](../01-overview/02-architecture-at-a-glance.md)
- 各 feature 现状：[02-features/](../02-features/)
