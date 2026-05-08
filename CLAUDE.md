# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目定位

华为广告平台数据分析 AI Agent。FastAPI + Vue 3，**纯 Python agent loop**（不依赖 deepagents/langgraph 等框架，2026-05 完成去 framework 重构），前后端通过 SSE 流式通信。**用户偏好中文回复**——产品本身、系统 prompt、注释都是中文。

## 常用命令

后端基于 `.venv/Scripts/...`（Windows 原生 venv，Python 3.13）。在 git-bash 里用正斜杠路径，PowerShell 里用反斜杠。

```bash
# 启动后端（dev）
.venv/Scripts/python.exe -m uvicorn main:app --reload

# 启动前端 dev server（默认 5173，已配 vite proxy /api → :8000）
cd frontend && npm run dev

# 前端构建（产物 frontend/dist 由 main.py 自动 mount 到 / 路由）
cd frontend && npm run build

# 跑测试
.venv/Scripts/python.exe -m pytest tests/
# 单个测试
.venv/Scripts/python.exe -m pytest tests/unit/agent/test_loop.py -v

# 重建 venv
rm -rf .venv && python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
```

LLM 配置全在 `.env`（见 `.env.example`）。设了 `LLM_BASE_URL` 就走对应 OpenAI 兼容协议——可对接 DeepSeek / 通义千问 / 火山方舟 / OpenAI 自家 / MiniMax 等。

## 架构核心

### 主循环：`agent/loop.py`（pure Python，0 framework dependency）

`AgentLoop.run()` 是一个 async generator——直接消费 openai SDK 的 stream chunks，自己解析 partial tool_calls JSON，asyncio.gather 并行 dispatch tools，yield 事件给上层（runner）。事件类型：

- `model_start` / `token` / `model_end`（含 usage payload）
- `tool_start` / `tool_end`（含 output / elapsed_ms）
- `interrupt_resolved`（HitL 决策完成）
- `complete`（含 final_message + final_messages 完整 state 快照）
- `recursion_limit` / `error`（也带 final_messages 兜底）

LLM client 是直接 `openai.AsyncOpenAI`——不走 `langchain_openai.ChatOpenAI` 包装，少一层抽象、流式 tool_calls 增量字段直接可见。`langchain_core.messages` 仅作为数据载体（`HumanMessage`/`AIMessage`/`ToolMessage`/`SystemMessage`），是轻量 dataclass，无运行时开销。

### 状态持久化：`agent/state.py::ThreadStore`

替代 langgraph 的 `AsyncSqliteSaver`。两张表：

```sql
loop_messages    (thread_id, seq, role, content, tool_calls, tool_call_id, name, created_at)
loop_interrupts  (thread_id, payload, created_at)   -- HitL pending state
```

JSON 字段透明可手 SQL 调试（不像老链路的二进制 blob）。append-only 写入；overwrite_messages 仅在 SummarizationMiddleware 类替换历史时用。aiosqlite + WAL 让多 reader 不阻塞 writer。

`thread_id = f"{user_id}_{conversation_id}"`，前端"新对话"按钮重置 conversation_id 让后端进入新 thread。

### Channel 抽象：`api/channel/`

Agent 对外的多种通信渠道。当前实现：
- `WebSSEChannel`（生产用）：SSE 推流到浏览器
- `CLIChannel`（开发/测试）：直接 print

`api/channel/runner_v2.py::AgentRunnerV2` 是 loop 事件 → channel SSE 事件的桥：

| loop event | channel method |
|---|---|
| `model_start` | `send_progress("AI 正在规划任务...")` |
| `token` | `send_token(delta)` |
| `model_end + usage` | `send_metrics(payload)` |
| `tool_start` | `send_step(label, "tool_start", skill_subcmd=...)` |
| `tool_end` | `send_step(label, "tool_end")` + 提取 artifact_ids → `send_artifact_updated` |
| `interrupt_resolved` | （已在 `on_interrupt` 回调内调用 `wait_for_confirm`） |
| `complete` | 跑完落库（ThreadStore.overwrite_messages） |
| `recursion_limit` | `send_token(警告文案)` |
| `error` | `send_token("[错误] {exception}")` |

### Skill / Tool / Channel 三层模型

| 概念 | 定位 | 来源 | 例子 |
|---|---|---|---|
| **业务 Tool（loop ToolSpec）** | LLM 看到的工具——schema + async callable | `agent/skill_loader.py::_make_run_tool_spec` | `run_command`（per-user 闭包：commands + workdirs + user_id + artifacts_root） |
| **Skill** | 业务领域能力（框架不内置） | `skills.md_dir` 扫描 + 用户上传，子命令通过 `run_command` 派发 | `query-metrics` / `export-report` |
| **Channel** | Agent 对外通信渠道 | `api/channel/` 下 BaseChannel 子类 | `WebSSEChannel` / `CLIChannel` |

LLM 看到的工具集 = **单一 `run_command`**（统一派发），实际跑哪个子命令由 LLM 传入的 `command` 参数第一个 token 决定。Skill 是纯领域代码，不依赖 `api/` 也不依赖 `agent/`——通过 SKILL.md 描述 + 外部脚本（Node/Python/Bash），框架不感知业务。Channel 自身的方法（`send_token` / `send_step` 等）是 channel 的 **skill 集合**，被 runner 内部调用，**不进 LLM 工具签名**。

装配链路：

```
chat.py::_start_v2_agent_run()
   │
   ├─→ load_md_skills() → SkillsPackage(prompt_addition, tools, summaries)
   │                      tools = [ToolSpec(run_command, schema, ...)]
   │
   ├─→ system_prompt = PLAN_INSTRUCTION + agents.md + skill prompt
   │   (日期由 DateReminder middleware 每轮注入到 messages 头部，**不**进 system_prompt——
   │    保 cache 前缀稳定，详见 docs/02-features/09-prompt-cache.md)
   │
   ├─→ middlewares = [ToolOutputTruncation, IterationGuard, DateReminder]
   │
   ├─→ AgentLoop(config=loop_config, tools=skills_tools, middlewares=middlewares)
   │
   └─→ AgentRunnerV2(store).run(channel, thread_id, user_message, ...)
       └─→ async for ev in loop.run(...): map ev → channel.send_*
```

### Middleware：`agent/middleware/`

纯 Python 协议（duck typing）——任何对象只要有 `before_model(state: AgentState) -> AgentState | None` 方法即可。**不**强制继承基类。返回 None = 不修改 state；返回新 AgentState = 替换。

当前 3 个 middleware：

1. **`DateReminder`**：每轮 model 前，在 messages 头部注入 `<system-reminder>今天是 X 月 Y 日...</system-reminder>`（user role HumanMessage）。先按 marker 删掉所有老 reminder 再插入今日的，避免跨天残留。**为什么不进 system_prompt**：日期每天变会破坏 OpenAI 兼容自动前缀 cache。
2. **`IterationGuard`**：数 state.messages 中 ToolMessage 数量。70%（默认 ~52）软提示，90%（~67）紧急提示，注入 SystemMessage。同 marker 已注入则不重复——避免每步 noisy 警告。
3. **`ToolOutputTruncation`**：scan ToolMessage，超过 `tool_output_max_bytes`（默认 5000）的 content 写到 `data/{user_id}/tool_outputs/{conv_id}/{tool_call_id}.json`，message 内容替换为摘要 + 文件指针。下次 LLM 调用看到的是截断版（不重 prefill 大返回）。

### HitL（Human-in-the-Loop）流程

> ⚠️ **当前业务场景为纯分析（只读），HitL 不会被触发**。`config.yaml::agent.interrupt_on` 默认空列表。
> 本节描述的是**框架预留能力**，机制完整且测试通过——未来扩展到写操作时直接启用。

`config.yaml::agent.interrupt_on` 列敏感的工具名（high_risk / medium_risk 分级）。LLM 想调拦截工具时，AgentLoop 调 `on_interrupt(tool_calls)` 回调，runner 在内部等 `channel.wait_for_confirm()` 决策。

#### Runner 层统一接口

```python
# api/channel/base.py — abstract，所有 channel 都实现
async def wait_for_confirm(self, message: str, preview: list, *, tool_name="", risk_level="medium") -> tuple[bool, bool]: ...
```

`AgentRunnerV2` 不区分 channel 类型，看到 LLM 想调拦截工具就 `await channel.wait_for_confirm()`。

#### 实现层因 channel 跨连接性而异

| Channel | "等待"机制 | "信号"来源 | 跨连接？ | 用 registry？ |
|---|---|---|---|---|
| `CLIChannel` | `input(...)` 阻塞 stdin | 用户在**同一终端**按键 | ❌ 同栈 | ❌ 不需要 |
| `WebSSEChannel` | `await self._confirm_event.wait()` | 一个**独立的 HTTP 请求** `POST /confirm` 调 `channel.resolve_confirm(approve)` | ✅ 是 | ✅ 必须 |

CLI 的"信号"在同一执行栈里，朴素阻塞即可；WebSSE 的 confirm 请求是独立连接，必须有"反向引用入口"才能找到原 channel 实例的 `_confirm_event`——这就是 `channel_registry` 的唯一职责。

#### `ExternallyConfirmable` Protocol（`api/channel/base.py`）

跨连接确认能力被抽成一个结构化协议（`runtime_checkable Protocol`），而**不是**绑死到某个具体类。confirm endpoint 守卫的是**能力**而非**类型**：

```python
if not isinstance(channel, ExternallyConfirmable):
    return {"status": "not_supported_on_this_channel"}
channel.resolve_confirm(req.action == "approve")
```

未来加 Slack / WebSocket channel 只要实现 `resolve_confirm`，confirm endpoint 和 registry 零改动。

### PLAN_INSTRUCTION 是硬约束（api/chat.py 顶部）

system prompt 由 `PLAN_INSTRUCTION` + `UserSpace.get_agents_md()` + SKILL.md 加载产物拼成。`PLAN_INSTRUCTION` 强制 LLM 按以下序列执行：

```
run_command(...)  →  run_command(...)  →  ...  →  最终 Markdown（含可选 ```chart``` 代码块）
```

LLM 鼓励**独立查询并行 emit**（一轮 emit 多个 tool_calls，AgentLoop 用 asyncio.gather 自动并发执行）——节省 LLM round-trip。

`send_to_user` 工具已废弃——文本/图表都通过 LLM **直接输出最终 Markdown** 完成（图表内联为 ```chart``` 代码块由前端解析渲染），节省一整轮 LLM round trip。

### Per-user 数据空间（agent/user_space.py）

每个 `user_id` 一个 `data/{user_id}/` 目录：
- `skills/` — 用户自定义 SKILL.md 包（`POST /api/skills/{user_id}/upload` 上传 zip，解压成 SKILL.md + bin/ 标准结构）
- `memory/` — 用户记忆
- `tool_outputs/` — `ToolOutputTruncation` 卸盘的超大工具返回值（按 conv_id 分组）
- `agents.md` — 用户自定义系统指令，被拼到 `prompts/system_agent.md` 之后

User skill 的加载走与系统 skill 相同的 loader：`load_md_skills(cfg.skills.md_dir, us.skills_dir)`——同名 skill 时**用户覆盖系统**。

### 持久化（agent/state.py + 其它）

- **ThreadStore**：`agent/state.py`，`AsyncSqliteSaver` 的纯 Python 替代。FastAPI lifespan 里 lazy init（首次请求需要时才建 connection），shutdown 时 close。
- **conversation_meta / events / metrics**：3 张独立 SQLite 表（共用 `data/checkpoints.db` 文件，跟 ThreadStore 表互不冲突）：
  - `conversation_meta`：会话元数据（标题、归档状态、消息计数）
  - `conversation_events`：思考事件流（plan/step/artifact_updated）按 turn_id 分组
  - `conversation_metrics`：每次 LLM 调用的 metrics（token、TPS、cache 命中等）
- 隔离：`thread_id = f"{user_id}_{conversation_id}"`
- 横向扩展：单机 SQLite 够用；多副本部署时把 ThreadStore 后端换成 Postgres（`agent/state.py::ThreadStore` 是唯一改动点——schema 跟 SQL 表完全自描述）

### 长上下文压缩 / 截断

两个机制叠加，都可在 `config.yaml::agent.long_context` 调参：

1. **`ToolOutputTruncation`**：每次 LLM 调用前 scan messages，超过 `tool_output_max_bytes`（默认 5000）的 `ToolMessage.content` 写到磁盘，message 内容替换为摘要 + 文件指针。
2. **应用级 summarization**（待实现）：当前 v2 链路尚未接入跨轮摘要——历史超过 `summarization_trigger_tokens`（默认 80000）时应当生成 summary 替换早期 messages。这是 P4 之后的待办（老 langgraph 链路里有 `SummarizationMiddleware` 兜底，重构期间一并删除，下次需要时新建）。

`ToolOutputTruncation` 截断会"前置"挡掉超大 ToolMessage——所以单次工具大返回不会让 context 直接爆。

### 前端 SSE 是手写的（frontend/src/utils/sse.js）

没用浏览器原生 `EventSource`——因为需要：(1) POST 大 payload，(2) 读 response header 拿 `X-Session-Id`。改成用 `fetch` + `ReadableStream` reader 自己解析 `event:` / `data:` 帧。改 SSE 协议时两端都要同步。

### 关键 SSE event 类型

| event | 用途 |
|---|---|
| `token` | LLM 流式 token |
| `step` | 工具开始/结束（顶部步骤追踪） |
| `progress` | 顶部进度条文字（如"AI 正在规划任务..."） |
| `metrics` | 单次 LLM 调用 metrics（input/output tokens、TPS、cache_read 等） |
| `artifact_updated` | 工具执行产生 artifact，前端拉详情 |
| `interrupt` | HitL 确认请求 |
| `done` | 流结束信号（前端断开） |

## 配置文件分工

- `config.yaml` — 服务端口、CORS、interrupt 工具列表、数据目录、recursion_limit。**不含 LLM 配置**
- `.env` — LLM 配置（API key、provider、model、base_url）；其他敏感变量
- `prompts/system_agent.md` — 系统级 agent 角色 prompt（中文，"华为广告数据分析助手"）
- `data/{user_id}/agents.md` — 用户级追加指令
- `vendor/tiktoken_cache/` — 离线 BPE 词表（公司内网无法连 OpenAI CDN 时 tiktoken 仍可工作）

## 测试约定

`tests/unit/` 镜像源码结构：
- `tests/unit/agent/` — loop / state / middleware / config / skill_loader 等纯逻辑
- `tests/unit/api/` — runner_v2 + chat 装配 + channel registry

跑全部：`.venv/Scripts/pytest tests/`（约 165 个测试，离线，~9s）。

测试 patch 路径用"使用点"——比如 `patch("agent.loop.AgentLoop._get_client")` 替换 mock OpenAI client，**不是** patch openai 库本身。

**注意**：`pytest` 和 `pytest-asyncio` **不在** `requirements.txt` 里。重建 venv 后按需装：`.venv/Scripts/pip install pytest pytest-asyncio`。后续可以建 `requirements-dev.txt` 固化。

## 验证脚本（`scripts/`）

跟 `tests/` 不同——这些是**真打 LLM**的端到端验证，按需手动跑（部分脚本是老链路时代写的，删 framework 后未必都还能跑——按需修）。

## 历史背景

`docs/plans/` 下三份按日期归档的设计/实施规划：
- `2026-04-21-ads-data-agent-design.md` — 总体设计
- `2026-04-21-ads-data-agent-impl.md` — 后端实施
- `2026-04-22-agent-execution-panel.md` — 执行面板设计

需要理解某个设计决策时优先看这些 + `docs/02-features/` 下分主题的功能文档。

**重要：去 framework 重构（2026-05）**——之前基于 deepagents/langgraph，现已完全替换为纯 Python（`agent/loop.py` + `agent/state.py`）。`docs/` 下许多老文档仍带 langgraph/deepagents 字样——逐步修订中，遇到不一致以 `agent/` 当前代码为准。
