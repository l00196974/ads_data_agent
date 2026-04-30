# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

华为广告平台数据分析 AI Agent。FastAPI + Vue 3，基于 [`deepagents`](https://github.com/langchain-ai/deepagents)（langgraph 之上的封装）实现 LLM Agent，前后端通过 SSE 流式通信。**用户偏好中文回复**——产品本身、系统 prompt、注释都是中文。

## 常用命令

后端基于 `.venv/Scripts/...`（Windows 原生 venv，Python 3.13）。在 git-bash 里用正斜杠路径，PowerShell 里用反斜杠。

```bash
# 启动后端（dev）
.venv/Scripts/python.exe main.py
# 或者带 reload
.venv/Scripts/python.exe -m uvicorn main:app --reload

# 启动前端 dev server（默认 5173，已配 vite proxy /api → :8000）
cd frontend && npm run dev

# 前端构建（产物 frontend/dist 由 main.py 自动 mount 到 / 路由）
cd frontend && npm run build

# 跑测试
.venv/Scripts/python.exe -m pytest tests/
# 单个测试
.venv/Scripts/python.exe -m pytest tests/test_agent_core.py::test_build_model_openai_protocol -v

# 重建 venv（如果 pyvenv.cfg 又被切走或者依赖坏了）
rm -rf .venv && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
```

LLM 配置全在 `.env`（见 `.env.example`）。设了 `LLM_BASE_URL` 就自动切到 OpenAI 协议，可对接 DeepSeek / 通义千问 / OpenAI 兼容厂商。

## 架构核心

### LLM 双协议路由（agent/core.py）

`_build_model()` 统一返回 `BaseChatModel` 实例（早期版本对 anthropic 等 provider 返回字符串 `"provider:model"`，导致 `cfg.llm.api_key` 在那条路径被静默忽略——见 commit `1228c79`）。

路由分支：
- `cfg.llm.base_url` 非空 **或** `provider == "openai"` → `ChatOpenAI`（用于 OpenAI 兼容代理：火山方舟 / DeepSeek / 阿里通义 / OpenAI 自家）
- 其他 provider（anthropic / google_genai / ...）→ `langchain.chat_models.init_chat_model`，根据 provider 自动选 ChatX 实现

两条路径都把 `cfg.llm.api_key` 显式传给底层客户端。`agent/builder.py` 不再需要 `resolve_model` 转换。

### Skill / Tool / Channel 三层模型（详见 `docs/02-features/01-skill-tool-channel-model.md`）

项目把"agent 可调用的能力"切成三层（Tool / Skill / Channel）。Agent 看到的工具集由 framework 默认 tool（含 `send_plan`、`task` 等）+ 业务 skill 加载产物（单一 `run_command` 工具聚合所有 SKILL.md 包）组成：

| 概念 | 定位 | 来源 | 例子 |
|---|---|---|---|
| **Tool** | framework 提供的"原子操作"（默认 tool） | deepagents 默认 + `api/channel/default_tools.py` | `task` / `write_file` / `send_plan` |
| **Skill** | 业务领域能力（框架不内置） | `skills.md_dir` 扫描 + 用户上传，单一 `run_command` 工具聚合 | `query-metrics` / `export-report` |
| **Channel** | Agent 对外通信渠道 | `api/channel/` 下 BaseChannel 子类 | `WebSSEChannel` / `CLIChannel` |

Skill 是纯领域代码，不依赖 `api/` 也不依赖 `agent/`——通过 SKILL.md 描述 + 外部脚本，框架不感知业务。Channel 自身的方法（`send_token` / `send_step` / `send_progress` / `send_plan` / `wait_for_confirm`）是 channel 的 **skill 集合**，被 default tool 内部调用，不进 LLM 工具签名。**LLM 的 prompt 和工具签名在不同 channel 下完全相同——agent 感知不到差别**。

装配链路：

```
chat.py             AgentRunner.run()              build_agent()
   │                       │                            │
   │  _make_build_fn(uid)  │                            │
   ├──────────────────────▶│                            │
   │                       │  make_default_tools(channel)   ← 闭包绑定 channel
   │                       ├──────────────╮             │
   │                       │              │             │
   │                       │  extra_tools │             │
   │                       │              ▼             │
   │                       │  build_fn(extra_tools=[send_plan])
   │                       │                            │
   │                       │              SKILL.md 加载 │
   │                       │              ▼             │
   │                       │  load_md_skills() → md_pkg.tools=[run_command]
   │                       │                            │
   │                       │              ▼             │
   │                       │  create_deep_agent(tools=md_pkg.tools+extra_tools,
   │                       │                    subagents=cfg.agent.subagents, ...)
```

`AgentRunner.run()` 消费 `agent.astream_events(version="v2")`，把不同 event kind 转成 channel 调用：
- `on_chat_model_start` → `channel.send_progress`（覆盖 LLM 思考期的静默间隙）
- `on_chat_model_stream` → `channel.send_token`（流式 LLM 输出）
- `on_tool_start` → `channel.send_step("tool_start")`（**append-only**，不再推 task_update）
- `on_tool_end` → `channel.send_step("tool_end")`
- `on_chain_end` 中带 `__interrupt__` → 触发 HitL 流程

`send_*` 命名前缀是约定——runner 用 `_is_meta_tool()` 把它们从 step 推送中过滤掉（它们是默认 tool 不是业务步骤）。

**控制反转的关键设计**：早期版本让 LLM 主动调 `update_task` 工具来上报状态（每个业务工具 call 包夹两次），但 LLM 倾向于一次输出多个 tool_calls 让 langgraph 批量执行——结果前端看到 running 一闪而过。现在 runner 完全拥有 plan 状态机，`update_task` 工具被删除（见 commit `0a7ebf4`）。

### HitL（Human-in-the-Loop）流程

> ⚠️ **当前业务场景为纯分析（只读），HitL 不会被触发**。`config.yaml::agent.interrupt_on` 默认空列表。
> 本节描述的是**框架预留能力**，机制完整且测试通过——未来扩展到写操作时直接启用。

`config.yaml::agent.interrupt_on` 列敏感的 LangChain tool name（如 `run_command` / `task`）。deepagents 命中后抛 `GraphInterrupt`（或在 `on_chain_end` 输出里塞 `__interrupt__` payload）。

#### Runner 层统一接口
所有 channel 共用一个 contract：

```python
# api/channel/base.py — abstract，所有 channel 都实现
async def wait_for_confirm(self, message: str, preview: list) -> bool: ...
```

`AgentRunner` 不区分 channel 类型，看到 `__interrupt__` 就 `await channel.wait_for_confirm()`。

#### 实现层因 channel 跨连接性而异

| Channel | "等待"机制 | "信号"来源 | 跨连接？ | 用 registry？ |
|---|---|---|---|---|
| `CLIChannel` | `input(...)` 阻塞 stdin | 用户在**同一终端**按键 | ❌ 同栈 | ❌ 不需要 |
| `WebSSEChannel` | `await self._confirm_event.wait()` | 一个**独立的 HTTP 请求** `POST /confirm` 调 `channel.resolve_confirm(approve)` | ✅ 是 | ✅ 必须 |

CLI 的"信号"在同一执行栈里，朴素阻塞即可；WebSSE 的 confirm 请求是独立连接，必须有"反向引用入口"才能找到原 channel 实例的 `_confirm_event`——这就是 `channel_registry` 的唯一职责。

#### `ExternallyConfirmable` Protocol（`api/channel/base.py`）

跨连接确认能力被抽成一个结构化协议（`runtime_checkable Protocol`），而**不是**绑死到某个具体类：

```python
@runtime_checkable
class ExternallyConfirmable(Protocol):
    def resolve_confirm(self, approve: bool) -> None: ...
```

这样 confirm endpoint 守卫的是**能力**而非**类型**：

```python
if not isinstance(channel, ExternallyConfirmable):
    return {"status": "not_supported_on_this_channel"}
channel.resolve_confirm(req.action == "approve")
```

`ChannelRegistry.register()` 也用 Protocol 静默过滤——CLI channel 注册是 no-op，只有真正跨连接的 channel 进 dict。**未来加 Slack / WebSocket channel 只要实现 `resolve_confirm`，confirm endpoint 和 registry 零改动**。

#### 端到端跨请求流程（WebSSE）

1. `POST /api/chat/{user_id}` 创建 `WebSSEChannel`，注册到 `channel_registry[session_id]`（registry 自动判 `ExternallyConfirmable`），response header 带 `X-Session-Id`
2. agent 命中敏感工具 → `channel.wait_for_confirm()` → SSE 推 `event: interrupt` + 阻塞在 `asyncio.Event`
3. 前端弹确认框 → `POST /api/chat/{user_id}/confirm` 带 `session_id`
4. confirm 端点从 registry 取出 channel，`isinstance(..., ExternallyConfirmable)` 守卫通过 → `resolve_confirm(approve)` set event → runner 用 `Command(resume=approve)` 继续 agent

### PLAN_INSTRUCTION 是硬约束（api/chat.py 顶部）

system prompt 由 `PLAN_INSTRUCTION` + `UserSpace.get_agents_md()` + SKILL.md 加载产物拼成。`PLAN_INSTRUCTION` 强制 LLM 按以下序列执行：

```
send_plan(tasks=[...])  →  run_command(...)  →  run_command(...)  →  ...  →  最终 Markdown（含可选 ```chart``` 代码块）
```

`send_to_user` 工具已废弃——文本/图表都通过 LLM **直接输出最终 Markdown** 完成（图表内联为 ```chart``` 代码块由前端解析渲染），节省一整轮 LLM round trip。

LLM **不**主动调 `update_task`——任务的 `running` / `done` 状态变化由 `AgentRunner` 监听 `on_tool_start/end` 推送 step 事件，前端走 append-only 思考日志（详见 `docs/02-features/03-execution-panel.md`）。

### Per-user 数据空间（agent/user_space.py）

每个 `user_id` 一个 `data/{user_id}/` 目录：
- `skills/` — 用户自定义 SKILL.md 包（`POST /api/skills/{user_id}/upload` 上传 zip，解压成 SKILL.md + bin/ 标准结构）
- `memory/` — 用户记忆
- `tool_outputs/` — `ToolOutputTruncationMiddleware` 卸盘的超大工具返回值
- `agents.md` — 用户自定义系统指令，被拼到 `prompts/system_agent.md` 之后

User skill 的加载走与系统 skill 相同的 loader：`load_md_skills(cfg.skills.md_dir, us.skills_dir)`——同名 skill 时**用户覆盖系统**（`agent/skill_loader.py:183`）。

### 持久化（agent/checkpointer.py + agent/store.py）

- **Checkpointer**：`AsyncSqliteSaver`，DB 文件 `data/checkpoints.db`，存 langgraph 对话状态机 checkpoint。FastAPI lifespan 里 `await init_checkpointer()` 异步建立，shutdown 时 `await close_checkpointer()` 清理（`AsyncExitStack` 管 contextmanager）。
- **Store**：`AsyncSqliteStore`（默认），DB 文件 `data/store.db`，给 deepagents 内置工具（`write_file`/`read_file` 等）当 backend 用。生命周期模式与 checkpointer 相同——`agent/store.py::init_store` / `close_store`。配置项 `cfg.persistence.store_backend` 选 `memory` / `sqlite`（默认）/ `mysql`（stub）。同步路径下 `get_store()` 懒初始化为 `InMemoryStore` 让单测免于 await。
- 隔离：`thread_id = f"{user_id}_{conversation_id}"`，前端"新对话"按钮重置 `conversation_id` 让后端进入新 thread。`/api/chat/{user_id}/reset` 显式返回新 conv_id。
- 横向扩展：`AsyncSqliteSaver` 单机够用；多副本部署时换 `PostgresSaver` 共享同张表即可（`agent/checkpointer.py::init_checkpointer` 是唯一改动点）。Store 同理走 MySQL/Postgres 适配（见 `agent/store.py::init_store::backend == "mysql"` 占位）。

### Middleware 是什么 / 在哪一层（agent/middleware/）

LangChain Agent 框架的 middleware 是**装饰器模式**的应用——在 agent 一轮执行的多个时机（`before_agent` / `before_model` / `wrap_model_call` / `before_tool` / `wrap_tool_call` / `after_*`）插入 hook，让你**不动 framework 内核**就能加横切关注点（截断 / 缓存 / 限速 / 审计 / 权限 / 压缩）。位置感跟 FastAPI / Express HTTP middleware 完全一致。

每个 hook 拿到 `state`（含 messages 和私有字段）+ `runtime`（thread_id、config），可以返回 `{"messages": Overwrite(...)}` 或 `Command(update=...)` 修改 state。`wrap_*` hooks 还能完全替换被包裹的 callable（典型用途：缓存、重试、mock）。

`deepagents.create_deep_agent` 默认就注入一长串 middleware（`TodoListMiddleware` / `FilesystemMiddleware` / `SubAgentMiddleware` / `SummarizationMiddleware` / `PatchToolCallsMiddleware` / `AnthropicPromptCachingMiddleware` / `HumanInTheLoopMiddleware` / `_PermissionMiddleware`）。**我们的代码只需要管自己的 middleware 加 `middleware=[...]` 参数追加进去**——deepagents 默认那些是顺带获得的能力。

`agent/middleware/` 目录是项目自定义 middleware 的统一栖息地。当前只有一个 `ToolOutputTruncationMiddleware`，但凡未来要加（rate limiting / audit logging / per-user quota / response caching），都放这里——避免裸放在 `agent/` 顶层。

### 长上下文压缩 / 截断（agent/middleware/ + agent/checkpointer.py）

两个机制叠加，都可在 `config.yaml::agent.long_context` 调参：

1. **`ToolOutputTruncationMiddleware`**（`agent/middleware/tool_output_truncation.py`）：每次 LLM 调用前 scan messages，超过 `tool_output_max_bytes`（默认 5000）的 `ToolMessage.content` 写到 `data/{user_id}/tool_outputs/{tool_call_id}.json`，message 内容替换成 300 字符摘要 + 文件指针。Overwrite 持久化到 checkpointer 状态——后续轮也看到截断版。
2. **`SummarizationMiddleware`**（deepagents 内置）：累计 token 数超过 `summarization_trigger_tokens`（默认 80000）触发，生成 summary 替换早期 messages，保留最近 `summarization_keep_messages` 条原文。**`agent/checkpointer.py` monkey-patch 了 `deepagents.graph.create_summarization_middleware` 工厂**，让 deepagents 默认注入的那个 middleware 用我们的配置（不能通过 `middleware=` 参数追加自定义实例——langchain 会因 duplicate 校验失败）。

P0-4 截断会"前置"挡掉超大 ToolMessage——所以在真实场景下 80k summarization 触发要靠**对话长度累积**而不是单次工具大返回。

### 前端 SSE 是手写的（frontend/src/utils/sse.js）

没用浏览器原生 `EventSource`——因为需要：(1) POST 大 payload，(2) 读 response header 拿 `X-Session-Id`。改成用 `fetch` + `ReadableStream` reader 自己解析 `event:` / `data:` 帧。改 SSE 协议时两端都要同步。

### 关键 SSE event 类型

`WebSSEChannel` 推出的事件，前端 `Chat.vue` 各自有渲染逻辑：

| event | 用途 |
|---|---|
| `token` | LLM 流式 token |
| `step` | 工具开始/结束（顶部步骤追踪） |
| `plan` | 任务计划列表 |
| `task_update` | 单任务状态变化 `running`/`done`（runner 自动推，不是 LLM 主动调） |
| `progress` | 顶部进度条文字 |
| `text` | 最终文本（Markdown） |
| `render` | ECharts 配置（chart 渲染） |
| `interrupt` | HitL 确认请求 |
| `done` | 流结束信号（前端断开） |

## 配置文件分工

- `config.yaml` — 服务端口、CORS、interrupt 工具列表、数据目录。**不含 LLM 配置**
- `.env` — LLM 配置（API key、provider、model、base_url）；其他敏感变量
- `prompts/system_agent.md` — 系统级 agent 角色 prompt（中文，"华为广告数据分析助手"）
- `data/{user_id}/agents.md` — 用户级追加指令

## 测试约定

`tests/unit/` 镜像源码结构：
- `tests/unit/agent/` — 配置加载、build_agent、ToolOutputTruncation、UserSpace
- `tests/unit/api/` — runner 状态机
- `tests/unit/skills/` — 业务工具数据契约

跑全部：`.venv/Scripts/pytest tests/`（27 个测试，离线，~7s）。

测试 patch 路径用"使用点"而不是"定义点"——比如 `patch("agent.builder.create_deep_agent")`，**不是** `patch("agent.core.create_deep_agent")`，因为 `agent.core` 是 re-export shim，名字早已绑定到 `agent.builder` 的命名空间。

**注意**：`pytest` 和 `pytest-asyncio` **不在** `requirements.txt` 里（`langchain-anthropic` / `langchain-google-genai` 等可选 provider 也不在）。重建 venv 后按需装：`.venv/Scripts/pip install pytest pytest-asyncio`。后续可以建 `requirements-dev.txt` 固化。

## 验证脚本（`scripts/`）

跟 `tests/` 不同——这些是**真打 LLM**的端到端验证，按需手动跑：

- `verify_p0_e2e.py` — SSE 时序回归（启 backend，发一次 chat，断言 plan + task_update 时序）
- `stress_long_context.py` — 5 轮压力测试（同 thread_id，inspect AsyncSqliteSaver 中累积曲线）
- `validate_summarization.py` — monkey-patch trigger 到 10k，确认 SummarizationMiddleware 触发 + cutoff_index 正确

## 历史背景

`docs/plans/` 下三份按日期归档的设计/实施规划是项目主要文档：
- `2026-04-21-ads-data-agent-design.md` — 总体设计
- `2026-04-21-ads-data-agent-impl.md` — 后端实施
- `2026-04-22-agent-execution-panel.md` — 执行面板设计（plan/task_update 那套，注意：实施时把 `update_task` 工具的状态控制权从 LLM 拿回到 runner，文档里"LLM 主动调 update_task"那段是历史设计）

需要理解某个设计决策时优先看这些；它们比代码注释信息密度高得多。
