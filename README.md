# ads_data_agent

华为广告平台数据分析 AI Agent。FastAPI + Vue 3，基于 [`deepagents`](https://github.com/langchain-ai/deepagents) (建立在 LangGraph 之上) 实现的多轮对话、HitL（Human-in-the-Loop）确认、流式 SSE 输出的广告数据分析助手。

> 详细的架构、设计决策、长上下文策略写在 [`CLAUDE.md`](./CLAUDE.md) 里。本 README 只覆盖"用起来"。

---

## 能做什么

- **业务工具**: 广告活动报表查询、预算使用分析、投放异常检测、ECharts 图表生成
- **多轮对话**: 同一会话保留上下文，支持引用前几轮结果
- **执行计划面板**: LLM 在调用业务工具前先声明计划，前端实时渲染每个任务的 `pending → running → done`
- **HitL 确认**: 敏感操作（删除广告组、修改预算等）会拉起前端确认弹窗，用户拒绝就回滚
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

关键设计决策（详见 `CLAUDE.md`）：

- **Channel 抽象** (`api/channel/`)：同一个 agent 可服务 Web SSE 或 CLI——`send_plan` / `send_to_user` 工具由 channel 注入闭包，业务代码无感
- **状态控制反转**：runner 监听 `on_tool_start/end` 自动推送 `task_update`，**不**让 LLM 主动调状态工具，避免批量 tool_call 导致"running 一闪而过"
- **LLM 双协议路由** (`agent/llm.py`)：`base_url` 或 `provider=openai` → `ChatOpenAI`；否则字符串 `"provider:model"` 走 deepagents 原生
- **持久化**：`AsyncSqliteSaver` + FastAPI lifespan async init；`thread_id = {user_id}_{conversation_id}`

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
  interrupt_on:                  # 这些工具调用会触发 HitL 确认弹窗
    - delete_adgroups
    - modify_budget
    - pause_campaign

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
├── skills/system/             业务工具 (LLM tools)
│   └── {campaign_report, budget_analysis, anomaly_detection, chart_skill}.py
│
├── prompts/                   prompt 模板
│   └── system_agent.md
│
├── tests/unit/                按源码结构镜像
│   ├── agent/  api/  skills/
│
├── scripts/                   验证脚本（持久可重跑）
│   ├── verify_p0_e2e.py
│   ├── stress_long_context.py
│   └── validate_summarization.py
│
├── docs/plans/                设计/实施规划文档
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

- **LLM provider 端 prompt caching**：火山方舟 ARK + GLM 系列**不实现**自动 prompt caching（实测 `cache_read=0`）；OpenAI 自家、DeepSeek、Anthropic Claude 都自动支持
- **进程内 InMemoryStore**：deepagents 的 store 仍是单进程内存，多副本部署会失同步；要水平扩展时把 `agent/builder.py::_store` 换成持久后端
- **`channel_registry` 是模块级 dict**：多 worker 部署时 HitL confirm 请求可能落到没注册过的实例；要支持时把 registry 换成 Redis
- **业务工具是 mock**：`skills/system/*.py` 当前返 mock 数据，加 800ms / 300ms async sleep 模拟延迟。接真实数据库时把异步 IO 换成 SQL/HTTP 即可

---

## 开发约定

- 测试**镜像源码结构**：`tests/unit/agent/test_*.py` 对应 `agent/*.py`
- 一次性诊断脚本不进 `scripts/`（commit 一次诊断完结论后删除）
- 持久可重跑的验证才放 `scripts/` 下
- 业务工具（`skills/`）**不依赖** `api/` 或 `agent/`——保持纯领域模块
