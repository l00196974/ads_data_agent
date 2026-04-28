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

### Channel 抽象 = 多前端复用同一个 Agent（api/channel/）

这是项目最关键的设计。**Agent 看到的工具集由两类独立的 skills 组成**——职责、生命周期、定义位置都不同：

| 类别 | 来源 | 生命周期 | 例子 |
|---|---|---|---|
| **System skills**（业务工具） | `skills/system/SYSTEM_SKILLS` 模块级常量 | 进程级，所有用户共享 | `query_campaign_report`, `analyze_budget`, `detect_anomaly`, `build_chart` |
| **Channel skills**（输出/规划工具） | `BaseChannel.get_skill()` 返回的闭包列表 | per-session（每次 chat 请求新建 channel） | `send_plan`, `send_to_user` |

System skills 是纯领域代码，不感知前端形态——`skills/system/` 不依赖 `api/` 也不依赖 `agent/`。Channel skills 是闭包，捕获了具体 channel 实例：`send_to_user` 在 `WebSSEChannel` 下推 SSE event（含 ECharts JSON），在 `CLIChannel` 下 print 纯文本表格。**LLM 的 prompt 和工具签名在两种 channel 下完全相同——agent 感知不到差别**。

装配链路：

```
chat.py             AgentRunner.run()         build_agent()
   │                       │                       │
   │  _make_build_fn(uid)  │                       │
   ├──────────────────────▶│                       │
   │                       │  channel.get_skill()  │
   │                       ├─────────────╮         │
   │                       │             │         │
   │                       │ extra_tools │         │
   │                       │             ▼         │
   │                       │  build_fn(extra_tools=[send_plan, send_to_user])
   │                       │                       │
   │                       │                       │  skills = SYSTEM_SKILLS + extra_tools
   │                       │                       │  create_deep_agent(tools=skills, ...)
   │                       │                       │
```

`AgentRunner.run()` 消费 `agent.astream_events(version="v2")`，把不同 event kind 转成 channel 调用：
- `on_chat_model_start` → `channel.send_progress`（覆盖 LLM 思考期的静默间隙）
- `on_chat_model_stream` → `channel.send_token`（流式 LLM 输出）
- `on_tool_start` → `channel.send_step("tool_start")` **+** 自动推 `task_update("running")`
- `on_tool_end` → `channel.send_step("tool_end")` **+** 自动推 `task_update("done")`
- `on_chain_end` 中带 `__interrupt__` → 触发 HitL 流程

`send_*` 命名前缀是约定——runner 用 `_is_meta_tool()` 把它们从 step / task_update 推送中过滤掉（它们是 channel skills 不是业务步骤）。

**控制反转的关键设计**：早期版本让 LLM 主动调 `update_task` 工具来上报状态（每个业务工具 call 包夹两次），但 LLM 倾向于一次输出多个 tool_calls 让 langgraph 批量执行——结果前端看到 running 一闪而过。现在 runner 完全拥有 plan 状态机，`update_task` 工具被删除（见 commit `0a7ebf4`）。

### HitL（Human-in-the-Loop）流程

`config.yaml::agent.interrupt_on` 列敏感工具名（如 `delete_adgroups`）。deepagents 命中后抛 `GraphInterrupt`（或在 `on_chain_end` 输出里塞 `__interrupt__` payload）。

跨请求等待用户确认靠 `channel_registry`：
1. `POST /api/chat/{user_id}` 创建 channel，注册到 `channel_registry[session_id]`，response header 带 `X-Session-Id`
2. agent 命中敏感工具 → `channel.wait_for_confirm()` → SSE 推 `event: interrupt` + 阻塞在 `asyncio.Event`
3. 前端弹确认框，用户点了 → `POST /api/chat/{user_id}/confirm` 带 `session_id`
4. confirm 端点从 registry 取出 channel，调 `resolve_confirm(approve)` → set event → runner 用 `Command(resume=approve)` 继续 agent

不同 SSE 请求是独立 HTTP 连接，确认能跨连接关联**全靠 session_id 在 registry 里的引用**。

### PLAN_INSTRUCTION 是硬约束（api/chat.py 顶部）

system prompt 由 `PLAN_INSTRUCTION` + `UserSpace.get_agents_md()` 拼成。`PLAN_INSTRUCTION` 强制 LLM 按以下序列执行：

```
send_plan(tasks=[...])  →  业务工具调用 1  →  业务工具调用 2  →  ...  →  send_to_user(action="text"/"chart"/"progress")
```

LLM **不**主动调 `update_task`——任务的 `running` / `done` 状态变化由 `AgentRunner` 监听 `on_tool_start/end` 自动推送（plan 顺序 = 业务工具调用顺序）。`PLAN_INSTRUCTION` 也明确禁止把 `send_to_user` 列入 plan tasks，因为 runner 的 `_is_meta_tool` 会过滤它，会导致最后一个 task 永远 stuck pending。

特别注意"进度文字必须用 `action="progress"` 而不是 `action="text"`"——前者显示在顶部 tips 区，后者会进消息流。改这条 prompt 前先看前端 `Chat.vue` 是否还按此假设渲染，否则会破坏 UX。

### Per-user 数据空间（agent/user_space.py）

每个 `user_id` 一个 `data/{user_id}/` 目录：
- `skills/` — 用户自定义 skill 的 .py 文件（`POST /api/skills/{user_id}` 写入）
- `memory/` — 用户记忆
- `agents.md` — 用户自定义系统指令，被拼到 `prompts/system_agent.md` 之后

注意：`data/{user_id}/skills/*.py` 通过 `GET /api/skills/{user_id}` 可以列出，但 `chat.py::_make_build_fn` 把 `skills=SYSTEM_SKILLS` 写死，**user skills 没有被注入到 agent 的工具集**。改用户 skill 注册逻辑前先确认这是有意还是待补。

### 持久化（agent/checkpointer.py + agent/builder.py）

- `_checkpointer` 是 `AsyncSqliteSaver`，DB 文件 `data/checkpoints.db`，**进程重启不丢历史**。在 FastAPI lifespan 里 `await init_checkpointer()` 异步建立，shutdown 时 `await close_checkpointer()` 清理（`AsyncExitStack` 管 contextmanager）。
- `_store = InMemoryStore()` 仍是模块级单例，给 deepagents 内置工具（`write_file`/`read_file` 等）当 backend 用——目前业务流程没用到这些工具。
- 隔离：`thread_id = f"{user_id}_{conversation_id}"`，前端"新对话"按钮重置 `conversation_id` 让后端进入新 thread。`/api/chat/{user_id}/reset` 显式返回新 conv_id。
- 横向扩展：`AsyncSqliteSaver` 单机够用；多副本部署时换 `PostgresSaver` 共享同张表即可（`agent/checkpointer.py::init_checkpointer` 是唯一改动点）。

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
