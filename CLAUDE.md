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

`_build_model()` 有一个不显然的分支：
- 若 `cfg.llm.base_url` 非空，**或** `provider == "openai"` → 返回 `ChatOpenAI` 实例
- 否则返回 deepagents 原生格式的字符串 `"provider:model"`（例如 `"anthropic:claude-sonnet-4-6"`）

deepagents 的 `create_deep_agent` 接受这两种格式，但下游行为路径不同。改 LLM 配置时要意识到这点。

### Channel 抽象 = 多前端复用同一个 Agent（api/channel/）

这是项目最关键的设计。Agent 的"输出工具"不是固定的——它由当前会话的 channel 注入：

- `BaseChannel.get_skill()` 返回一组工具函数（`send_plan` / `update_task` / `send_to_user`），这些工具用闭包捕获 channel 自身
- `AgentRunner.run()` 把 channel 的 skills 拼到 `SYSTEM_SKILLS` 后面，再给 deepagent
- 同一个 LLM prompt + 业务 skill，在 `WebSSEChannel` 下输出 SSE event（含 ECharts JSON），在 `CLIChannel` 下打印纯文本——agent 完全感知不到差别

`AgentRunner.run()` 消费 `agent.astream_events(version="v2")`，把不同 event kind 转成 channel 调用：
- `on_chat_model_stream` → `channel.send_token`（流式 LLM 输出）
- `on_tool_start/end` → `channel.send_step`（顶部步骤追踪；**会过滤掉 `send_*` 工具**，因为它们不是业务工具）
- `on_chain_end` 中带 `__interrupt__` → 触发 HitL 流程

### HitL（Human-in-the-Loop）流程

`config.yaml::agent.interrupt_on` 列敏感工具名（如 `delete_adgroups`）。deepagents 命中后抛 `GraphInterrupt`（或在 `on_chain_end` 输出里塞 `__interrupt__` payload）。

跨请求等待用户确认靠 `channel_registry`：
1. `POST /api/chat/{user_id}` 创建 channel，注册到 `channel_registry[session_id]`，response header 带 `X-Session-Id`
2. agent 命中敏感工具 → `channel.wait_for_confirm()` → SSE 推 `event: interrupt` + 阻塞在 `asyncio.Event`
3. 前端弹确认框，用户点了 → `POST /api/chat/{user_id}/confirm` 带 `session_id`
4. confirm 端点从 registry 取出 channel，调 `resolve_confirm(approve)` → set event → runner 用 `Command(resume=approve)` 继续 agent

不同 SSE 请求是独立 HTTP 连接，确认能跨连接关联**全靠 session_id 在 registry 里的引用**。

### PLAN_INSTRUCTION 是硬约束（api/chat.py 顶部）

system prompt 由 `PLAN_INSTRUCTION` + `UserSpace.get_agents_md()` 拼成。`PLAN_INSTRUCTION` 强制 LLM 必须按以下序列执行：

```
send_plan(tasks=[...])  →  update_task(t1, "running")  →  业务工具  →  update_task(t1, "done")  →  ...  →  send_to_user(action="text"/"chart"/"progress")
```

特别注意"进度文字必须用 `action="progress"` 而不是 `action="text"`"——前者显示在顶部 tips 区，后者会进消息流。改这条 prompt 前先看前端 `Chat.vue` 是否还按此假设渲染，否则会破坏 UX。

### Per-user 数据空间（agent/user_space.py）

每个 `user_id` 一个 `data/{user_id}/` 目录：
- `skills/` — 用户自定义 skill 的 .py 文件（`POST /api/skills/{user_id}` 写入）
- `memory/` — 用户记忆
- `agents.md` — 用户自定义系统指令，被拼到 `prompts/system_agent.md` 之后

注意：`data/{user_id}/skills/*.py` 通过 `GET /api/skills/{user_id}` 可以列出，但 `chat.py::_make_build_fn` 把 `skills=SYSTEM_SKILLS` 写死，**user skills 没有被注入到 agent 的工具集**。改用户 skill 注册逻辑前先确认这是有意还是待补。

### 持久化是进程内的（agent/core.py）

`_store = InMemoryStore()` 和 `_checkpointer = MemorySaver()` 是模块级单例，所有用户共享，**靠 `thread_id == user_id` 隔离**（见 `SessionManager.get_thread_id`）。

后果：**重启进程 = 所有会话状态丢失**。要换持久 checkpointer（如 `SqliteSaver`/`PostgresSaver`），改 `agent/core.py` 即可。

### 前端 SSE 是手写的（frontend/src/utils/sse.js）

没用浏览器原生 `EventSource`——因为需要：(1) POST 大 payload，(2) 读 response header 拿 `X-Session-Id`。改成用 `fetch` + `ReadableStream` reader 自己解析 `event:` / `data:` 帧。改 SSE 协议时两端都要同步。

### 关键 SSE event 类型

`WebSSEChannel` 推出的事件，前端 `Chat.vue` 各自有渲染逻辑：

| event | 用途 |
|---|---|
| `token` | LLM 流式 token |
| `step` | 工具开始/结束（顶部步骤追踪） |
| `plan` | 任务计划列表 |
| `task_update` | 单任务状态变化 `running`/`done` |
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

`tests/` 用 pytest，重点测：`test_config.py`（YAML + env 合并）、`test_user_space.py`（per-user 目录隔离）、`test_agent_core.py`（用 `unittest.mock.patch` mock `create_deep_agent`，验证 LLM 协议路由）、`test_skills.py`。Skill 函数本身是 mock 数据（见 `skills/system/budget_analysis.py` 等），所以这层基本是数据契约测试。

**注意**：`pytest` 和 `pytest-asyncio` **不在** `requirements.txt` 里。重建 venv 后要单独装：`.venv/Scripts/pip install pytest pytest-asyncio`。如果要把测试依赖固化下来，建议加到 `requirements.txt` 末尾或新建 `requirements-dev.txt`。

## 历史背景

`docs/plans/` 下三份按日期归档的设计/实施规划是项目主要文档：
- `2026-04-21-ads-data-agent-design.md` — 总体设计
- `2026-04-21-ads-data-agent-impl.md` — 后端实施
- `2026-04-22-agent-execution-panel.md` — 执行面板（plan/task_update 那套）

需要理解某个设计决策时优先看这些；它们比代码注释信息密度高得多。
