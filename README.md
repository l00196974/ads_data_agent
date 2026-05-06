# ads_data_agent

华为广告平台数据分析 AI Agent。FastAPI + Vue 3，基于 [`deepagents`](https://github.com/langchain-ai/deepagents) (建立在 LangGraph 之上) 实现的多轮对话、流式 SSE 输出的广告数据**分析**助手。

> ⚠️ **当前阶段聚焦"分析场景"**——所有输出是只读分析结果（表格 / 图表 / 诊断结论 / Markdown 报告），不涉及任何系统写操作。框架已实现 HitL 写操作守卫机制，预留给未来扩展时启用。

> 详细的架构、设计决策、长上下文策略写在 [`CLAUDE.md`](./CLAUDE.md) 里。本 README 只覆盖"用起来"。

---

## 能做什么

- **业务工具**: 广告活动报表查询、预算使用分析、投放异常检测、ECharts 图表生成
- **多轮对话**: 同一会话保留上下文，支持引用前几轮结果
- **执行计划面板**: LLM 在调用业务工具前先声明计划，前端实时渲染每个任务的 `pending → running → done`
- **HitL 写操作守卫**: 框架已实现（敏感操作触发前端确认弹窗），但**当前业务场景全是只读分析，未启用**。未来扩展到写操作（如批量暂停 / 改预算）时配置 `interrupt_on` 即可
- **多对话隔离**: "新对话"按钮真正切换后端 thread，旧对话历史不参与新会话的 LLM prefill
- **自动压缩**: 单 thread token 数超过阈值（默认 80k）自动触发 SummarizationMiddleware
- **工具输出截断**: 超大工具返回值（默认 5KB）自动卸载到 `data/{user_id}/tool_outputs/`，避免 thread 膨胀

---

## 架构一览

```
┌──────────────┐  SSE   ┌──────────────────────┐
│  Vue 3 前端  │◀──────▶│  FastAPI (api/chat)  │
│ (port 5173)  │  POST  │     (port 8000)      │
└──────────────┘        └──────────┬───────────┘
                                   │
                  ┌────────────────┴───────────────┐
                  │   AgentRunner (api/channel/)   │
                  │   消费 agent.astream_events    │
                  │   分发到 Web SSE 或 CLI Channel │
                  └────────────────┬───────────────┘
                                   │
                  ┌────────────────┴────────────────┐
                  │     deepagents.create_agent      │
                  │  ┌─────────┐ ┌─────────────────┐ │
                  │  │ skills/ │ │ middleware:     │ │
                  │  │ system  │ │ ToolOutput      │ │
                  │  │  (业务  │ │ Truncation      │ │
                  │  │  工具)  │ │ +Summarization  │ │
                  │  └─────────┘ └─────────────────┘ │
                  └────────────────┬─────────────────┘
                                   │
                          ┌────────┴─────────┐
                          │ AsyncSqliteSaver │
                          │ (data/checkpoints.db)
                          └──────────────────┘
```

关键设计决策（详见 [`docs/`](./docs/) 架构文档体系，特别是 `docs/02-features/`）：

- **Skill / Tool / Channel 三层模型** (`docs/02-features/01-skill-tool-channel-model.md`)：deepagents 默认 tool（`task` / `write_file` 等）+ SKILL.md 业务 skill（聚合到 `run_command`）+ Channel 通信契约——三者职责分离，agent 视角对 channel 切换透明
- **状态控制反转**：runner 监听 `on_tool_start/end` 推送 step 事件，**append-only**（不再有 `task_update`），LLM 不感知任务状态
- **SubAgent 长上下文主方案** (`docs/02-features/07-subagent-driven-context.md`)：主 Agent 派子 Agent 干脏活，子 Agent 独立 context 跑完只回摘要；Summarization 是兜底
- **LLM 客户端**（`agent/llm.py`）：统一 OpenAI 兼容协议（`ChatOpenAI`），`base_url` 切换不同后端（火山方舟 / DeepSeek / 通义 / OpenAI 自家）
- **持久化**：`AsyncSqliteSaver`（checkpoint）+ `AsyncSqliteStore`（store，默认）+ FastAPI lifespan async init；`thread_id = {user_id}_{conversation_id}`

---

## 快速开始

### 1. 准备环境

需要 Python ≥ 3.11、Node ≥ 18。

```bash
# Python
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt    # Windows
# 或 .venv/bin/pip install -r requirements.txt   # macOS/Linux

# Node
cd frontend && npm install && cd ..
```

### 2. 配置 LLM

复制 `.env.example` 为 `.env`，填一个 LLM provider：

```bash
# 方案 A：OpenAI 兼容代理（火山方舟 / DeepSeek / 通义千问 / OpenAI 自家）
LLM_API_KEY=<your-key>
LLM_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3   # 替换为你的 endpoint
LLM_PROVIDER=openai
LLM_MODEL=ark-code-latest                                       # 或 gpt-4o / deepseek-chat

# 方案 B：直连 Anthropic Claude
ANTHROPIC_API_KEY=<sk-ant-...>
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-4-6
```

### 3. 启动

```bash
# 后端（端口 8000）
.venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000

# 前端 dev server（端口 5173，已配 vite proxy /api → :8000）
cd frontend && npm run dev
```

浏览器打开 <http://localhost:5173> → 输入任意 user_id 登录 → 开始对话。

### 4. 部署（可选）

```bash
cd frontend && npm run build      # 产物在 frontend/dist/
.venv/Scripts/python.exe -m uvicorn main:app --port 8000
# 此时 main.py 会自动挂载 frontend/dist 到 / 路由，单端口同时提供 API + SPA
```

---

## 配置项

`config.yaml` 主要旋钮：

```yaml
agent:
  interrupt_on: []               # 命中即触发 HitL；当前分析场景留空。未来加写工具时按
                                 # LangChain tool name 列出（如 run_command / task）

  long_context:
    summarization_trigger_tokens: 80000   # 超此 token 数自动压缩历史
    summarization_keep_messages: 10        # 压缩后保留最近 N 条
    tool_output_max_bytes: 5000            # 工具单次返回超此字节数则截断卸盘
```

`.env` 主要变量：

| 变量 | 用途 |
|---|---|
| `LLM_API_KEY` | OpenAI 协议的通用 key（优先级高于 `ANTHROPIC_API_KEY`） |
| `LLM_BASE_URL` | OpenAI 兼容代理 endpoint（设置后自动切到 ChatOpenAI） |
| `LLM_PROVIDER` | `openai` / `anthropic` |
| `LLM_MODEL` | 模型名 |
| `ANTHROPIC_API_KEY` | 直连 Claude 时使用 |

---

## 测试与验证

```bash
# 单元测试（27 个，离线无 LLM 调用，~7s）
.venv/Scripts/pytest tests/

# 单文件
.venv/Scripts/pytest tests/unit/agent/test_core.py -v

# 端到端 SSE 时序回归（需要 backend 跑在 8000，会消耗 ~$0.01 LLM 调用）
.venv/Scripts/python.exe scripts/verify_p0_e2e.py

# 长上下文压力测试（5 轮连续对话，inspect thread state）
.venv/Scripts/python.exe scripts/stress_long_context.py

# Summarization 触发验证（monkey-patch trigger 到 10k，确认压缩 + 卸盘）
.venv/Scripts/python.exe scripts/validate_summarization.py
```

---

## 目录结构

```
ads_data_agent/
├── main.py                    FastAPI app entry (lifespan + routes)
├── config.yaml                runtime 配置
├── CLAUDE.md                  架构详细文档（本 README 的补充）
├── README.md                  你正在读的这份
│
├── agent/                     LLM agent 装配
│   ├── builder.py             build_agent (主装配)
│   ├── checkpointer.py        AsyncSqliteSaver 生命周期 + Summarization 工厂 patch
│   ├── llm.py                 _build_model (LLM 实例构造)
│   ├── config.py              Pydantic 配置 schema
│   ├── session.py             thread_id 生成
│   ├── user_space.py          per-user 文件空间
│   ├── core.py                兼容性 re-export shim
│   └── middleware/
│       └── tool_output_truncation.py
│
├── api/                       FastAPI 接入
│   ├── chat.py / skills.py    routers
│   ├── models.py              Pydantic request schemas
│   └── channel/               传输适配 (Web SSE / CLI)
│       ├── base.py registry.py runner.py web_sse.py cli.py
│
├── skills/                    业务 SKILL.md 包目录（框架不内置任何业务 skill）
│   └── <skill-name>/          每个子目录含 SKILL.md + package.json::bin + bin/*.{js,py,sh}
│
├── prompts/                   prompt 模板
│   └── system_agent.md
│
├── tests/unit/                按源码结构镜像（agent/ + api/）
│
├── scripts/                   验证脚本（持久可重跑，真打 LLM）
│   ├── verify_p0_e2e.py
│   ├── stress_long_context.py
│   └── validate_summarization.py
│
├── docs/                      架构文档体系（评审 / 对接读这里）
│   ├── 01-overview/           总览
│   ├── 02-features/           功能专题（每篇文末附 ADR）
│   ├── 03-api-reference/      接口契约
│   ├── 04-operations/         部署 / 配置 / 观测 / 测试策略
│   ├── 05-known-issues-and-roadmap/
│   └── plans/archive/         历史 plan（已过时，仅作时间线）
│
├── data/                      运行时数据（gitignored）
│   ├── checkpoints.db         Sqlite checkpointer
│   └── {user_id}/             per-user skills/memory/agents.md/tool_outputs
│
└── frontend/                  Vue 3 + Vite + ECharts SPA
    └── src/{pages, components, utils, router}/
```

---

## 已知限制 / 后续路线

完整问题清单与路线图见 [`docs/05-known-issues-and-roadmap/`](./docs/05-known-issues-and-roadmap/)。摘要：

- **`RedisChannelRegistry` 待实现**：当前 `InMemoryChannelRegistry` 是默认（单 worker），`RedisChannelRegistry` stub 已留接入点（含 docstring 设计方案），多 worker 部署前必须实现
- **`MysqlStore` 待实现**：`AsyncSqliteStore` 是默认，`mysql` backend stub 同上
- **认证缺失**：`user_id` 透传 mock 阶段，未接 SSO/JWT
- **业务 skill 是部署侧资产**：框架不内置任何业务 skill，`skills.md_dir` 下扫描的 SKILL.md 包决定 agent 实际能力——例如 `metric-data-extractor` 在当前环境依赖外部 mock 数据服务，部署到生产时需替换为真实数据源
- **LLM provider prompt caching 差异**：暂未深入对比，后续可能切其它供应商再处理

---

## 开发约定

- 测试**镜像源码结构**：`tests/unit/agent/test_*.py` 对应 `agent/*.py`
- 一次性诊断脚本不进 `scripts/`（commit 一次诊断完结论后删除）
- 持久可重跑的验证才放 `scripts/` 下
- 业务工具（`skills/`）**不依赖** `api/` 或 `agent/`——保持纯领域模块
