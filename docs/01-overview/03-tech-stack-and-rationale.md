# 03 技术选型与理由（Tech Stack & Rationale）

> ⚠️ **2026-05 重构后**：本文写于 deepagents/langgraph 时代，列出的"deepagents 选型理由"
> / "langgraph checkpointer 选型理由" 已不再成立——项目已去框架，改为纯 Python
> 自实现。具体迁移决策见 `agent/loop.py` 顶部 docstring。本文当历史决策记录。

每项选型背后都有对应的"否则会怎样"。本篇不重复列功能，只列**为什么这么选**。

## 一、技术栈速览

| 层 | 技术 | 版本 |
|---|---|---|
| 后端语言 | Python | ≥ 3.11（实测 3.13） |
| 后端框架 | FastAPI | latest |
| Agent 编排 | langgraph + deepagents | langgraph 1.1.x / deepagents latest |
| LLM 客户端 | langchain-openai（统一 OpenAI 兼容协议） | base_url 切换不同后端 |
| 持久化 | AsyncSqliteSaver / AsyncSqliteStore | langgraph-checkpoint-sqlite |
| 前端语言 | JavaScript（无 TS） | — |
| 前端框架 | Vue 3 + Vite | latest |
| 图表库 | ECharts | latest |
| Markdown 渲染 | marked + DOMPurify | latest |

---

## 二、关键选型

### 2.1 后端：Python + FastAPI

**为什么选 Python**：

- LLM 生态（langchain / langgraph / deepagents）几乎全在 Python。Node 选项有但成熟度差距大
- 数据分析场景天然亲 Python（pandas / numpy 在 SKILL.md 包内可直接用）
- 团队熟悉 Python

**为什么选 FastAPI**：

- 原生 async 支持（langgraph / aiosqlite 都需要 async）—— Flask / Django 走 sync 不合适
- Pydantic 内建（请求 schema 自动校验 + OpenAPI 文档生成）
- SSE / StreamingResponse 直接支持，无需额外库
- uvicorn 单进程性能足够（小规模）

**否则会怎样**：

- 用 Flask + asgiref 桥接 sync→async：能跑但性能差，并发 100 就开始堵
- 用 FastAPI 替代品 Litestar / BlackSheep：FastAPI 已是事实标准，社区资源最丰富

### 2.2 Agent 编排：deepagents（不直接用 langgraph）

**为什么不直接用 langgraph**：

- langgraph 是底层状态机框架，写一个 chat agent 至少需要：
  - 自己定义 state schema
  - 自己写 node 函数（model node + tool node + decision node）
  - 自己接 checkpointer
  - 自己实现长上下文压缩、HitL、SubAgent 等
- 这些是每个 agent 都要的能力，重复造轮子

**deepagents 提供什么**：

- `create_deep_agent` 一行创建可用 agent
- 默认注入：TodoListMiddleware / FilesystemMiddleware / SubAgentMiddleware /
  SummarizationMiddleware / HumanInTheLoopMiddleware / AnthropicPromptCachingMiddleware ...
- 我们只需要"加自己的 middleware（如 ToolOutputTruncation）"

**否则会怎样**：

- 自研：Time to first agent 从 1 周变 1-2 月，且要自己维护 SubAgent / Summarization 等
- 用 LangGraph Platform：托管成本高、私有化困难

### 2.3 LLM 客户端：langchain-openai 单协议

**为什么只走 OpenAI 兼容协议**：

- 国内厂商（火山方舟 / DeepSeek / 通义）大都暴露 OpenAI 兼容 endpoint，`base_url` 一切就行
- 业务上当前不需要原生 Anthropic / Google Gemini——多协议是过度设计，删掉简化心智
- 实现：`agent/llm.py` 一个 `_build_model()` 函数 + 一种 client（`ChatOpenAI`），`base_url` 切后端
- 历史曾支持 anthropic 直连 + google `init_chat_model` 路径（双协议路由），已删——`provider` 字段保留兼容旧 config，但不再影响逻辑分支

未来真要原生 Anthropic / Google Gemini 时再加分支即可（10 行代码）。

### 2.4 持久化：AsyncSqliteSaver / AsyncSqliteStore

**为什么 SQLite 而不是 Postgres**：

- 单机部署足够用，零外部依赖（部署门槛低）
- aiosqlite 性能在低并发场景完全够（< 100 QPS）
- 多副本部署需要时切 PostgresSaver/MysqlStore——`init_*` 函数是唯一改动点

**为什么不用 InMemoryStore（早期默认）**：

- 进程重启即丢
- 多副本失同步

详见 [02-features/10-persistence-and-checkpoints.md](../02-features/10-persistence-and-checkpoints.md)。

### 2.5 前端：Vue 3 + Vite，不用 TypeScript

**为什么 Vue 不是 React**：

- 团队熟悉 Vue
- Vue 3 Composition API 表达力够，无 hooks 心智负担
- Vite 构建速度快

**为什么没用 TypeScript**：

- 项目当前规模（~10 Vue 组件）下 TS 收益小
- 业务 prompt 在中文写为主，类型对中文 prompt 帮助有限
- 决策：MVP 阶段 JS 够用，规模化时再上 TS

### 2.6 SSE 而不是 WebSocket

详见 [02-features/05-streaming-sse-protocol.md::8](../02-features/05-streaming-sse-protocol.md)。

要点：
- SSE 协议复杂度低
- 客户端实现简单
- 我们只需要"服务端推"，用户输入用普通 POST 即可
- nginx / 反向代理对 SSE 默认友好

### 2.7 前端弃用 EventSource，手写 fetch + ReadableStream

详见 [02-features/05-streaming-sse-protocol.md::ADR-005](../02-features/05-streaming-sse-protocol.md)。

要点：
- EventSource 只支持 GET，用户消息要塞 URL 太丑
- EventSource 不暴露 response header，拿不到 X-Session-Id

### 2.8 SKILL.md 加载（仿 Claude Code）

详见 [02-features/11-system-skills-catalog.md](../02-features/11-system-skills-catalog.md)。

要点：
- 框架不内置业务能力
- 加业务 = 拷文件，零 Python 改动
- LLM 看 SKILL.md 原文 + 单一 `run_command` 工具

### 2.9 ECharts 而不是 Chart.js / Recharts / D3

**为什么 ECharts**：

- 中文社区资源最丰富
- 默认样式适合数据分析场景（不像 Chart.js 偏简洁）
- 配置 JSON 表达力强（散点 / 热力图 / 桑基图都内建）

**否则会怎样**：

- D3：表达力最强但开发成本高
- Chart.js：默认样式偏弱，定制样式工作量大
- Recharts：React 绑定，跟 Vue 不搭

LLM 输出图表用 `\`\`\`chart` 代码块嵌 ECharts 配置 JSON——前端解析渲染，省一轮 LLM round trip。

### 2.10 Marked + DOMPurify 渲染 Markdown

**为什么不用 Vue-Markdown / vditor**：

- marked 体积小、API 简单
- DOMPurify 是 XSS 防护事实标准
- LLM 输出可能含恶意片段（即使是自家 LLM 也可能被 prompt injection 操纵），必须 sanitize

---

## 三、未选型（明确不用）

| 技术 | 不用的理由 |
|---|---|
| TypeScript（前端） | 当前规模收益低；规模化时再上 |
| WebSocket | SSE 已够用，复杂度更低 |
| EventSource | POST 限制 + header 限制 |
| Chart.js / Recharts | ECharts 中文社区 + 表达力优势 |
| Postgres / MySQL（默认） | 单机 SQLite 足够，多副本时再切 |
| Redis（默认） | 单 worker InMemoryChannelRegistry 足够 |
| Docker / k8s | 当前部署量小，未实施容器化（roadmap） |
| OpenTelemetry / Prometheus | 当前观测能力有限（roadmap） |
| LangSmith Platform | 自部署 langgraph + 可选 LangSmith trace（不强依赖 platform） |

详细路线见 [05-known-issues-and-roadmap/roadmap.md](../05-known-issues-and-roadmap/roadmap.md)。

---

## 四、依赖管理现状

`requirements.txt` 当前**只含核心 runtime 依赖**——dev / 可选 provider 依赖未固化：

```bash
# 缺失项（按需手动装）
.venv/Scripts/pip install pytest pytest-asyncio          # 跑测试需要
.venv/Scripts/pip install langchain-anthropic            # 用 Claude 直连需要
.venv/Scripts/pip install langchain-google-genai         # 用 Gemini 需要
```

应建 `requirements-dev.txt` + `requirements-providers.txt` 把这些固化下来——见
[roadmap.md](../05-known-issues-and-roadmap/roadmap.md::P2)。

---

## 五、版本兼容性策略

- Python：要求 ≥3.11（用了 `dict | None` 等 PEP 604 语法）
- langgraph：跟着上游主版本，每次升级要跑全套 unit + E2E 测试（特别是 `validate_summarization.py`，
  因为我们 monkey-patch 了 deepagents 内部函数，上游改名会静默失效）
- deepagents：同上
- Node（前端构建时）：≥ 18

升级 langgraph / deepagents 的 SOP：
1. 先升级 in 一个分支
2. 跑 `pytest tests/`
3. 跑 `scripts/validate_summarization.py` 确认 monkey-patch 没失效
4. 跑 `scripts/verify_p0_e2e.py` 确认 SSE 时序无回归
5. 看 frontend build 不破

---

## 六、扩展点

要换底层组件时的"唯一改动点"：

| 组件 | 唯一改动点 |
|---|---|
| Checkpointer SQLite → Postgres | `agent/checkpointer.py::init_checkpointer` |
| Store SQLite → MySQL | `agent/store.py::init_store` 加 `mysql` 分支 |
| ChannelRegistry InMemory → Redis | `api/channel/registry.py::RedisChannelRegistry` 实现 + `make_channel_registry` 切 |
| LLM provider 加新 | 看 langchain 是否支持，是的话直接配 `LLM_PROVIDER` |
| 新前端通道（Slack/...） | 在 `api/channel/` 加 BaseChannel 子类 |
| 新业务能力 | 加 SKILL.md 包到 `skills.md_dir` |

这些扩展点的背后哲学：**业务变更不应该波及 framework；framework 升级不应该破坏业务**。
