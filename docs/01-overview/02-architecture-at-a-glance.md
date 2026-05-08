# 02 架构总览（Architecture at a Glance）

> ⚠️ **2026-05 重构后**：本文架构图含 `deepagents.create_deep_agent` / `AsyncSqliteSaver`
> / `langgraph` 等字样——这些已被 `agent/loop.py::AgentLoop` / `agent/state.py::ThreadStore`
> / 直调 `openai SDK` 替代。当前架构看 `CLAUDE.md`，本文用于历史/概念参考。

> 30 秒读懂"项目长什么样"。深度内容指向 `02-features/`。

## 一、一张图

```
┌──────────────────────────────────────────────────────────────────┐
│                         Frontend (Vue 3 + ECharts)                │
│         登录 → Skill 面板 → 对话流（含计划面板 + 图表混排）       │
└────────────┬─────────────────────────────────────────────────────┘
             │ POST /api/chat (SSE)  ▲
             │ POST /confirm         │  event: token / step / plan
             │ POST /append          │  event: progress / interrupt
             │ POST /reset           │  event: done
             ▼                       │
┌──────────────────────────────────────────────────────────────────┐
│                       FastAPI (api/chat.py)                       │
│   Channel Registry ──── per-session WebSSEChannel ───────────┐   │
└────────────────────────────────┬───────────────────────────────┘   │
                                 │                                   │
                                 ▼                                   │
┌──────────────────────────────────────────────────────────────────┐ │
│              AgentRunner (api/channel/runner.py)                  │ │
│   消费 agent.astream_events("v2") → 转发到 channel skill         │ │
│   make_default_tools(channel) → [send_plan]                      │ │
└────────────────────────────────┬───────────────────────────────┘ │
                                 │                                   │
                                 ▼                                   │
┌──────────────────────────────────────────────────────────────────┐ │
│             deepagents.create_deep_agent (agent/builder.py)       │ │
│ ┌──────────────┐ ┌─────────────────┐ ┌─────────────────────────┐ │ │
│ │ tools:       │ │ middleware:     │ │ subagents:              │ │ │
│ │  run_command │ │  ToolOutputTrunc│ │  metric-analyzer        │ │ │
│ │  send_plan   │ │  Summarization  │ │  report-writer          │ │ │
│ │  task        │ │  + deepagents 默认│ │  ...                   │ │ │
│ │  write_file  │ └─────────────────┘ └─────────────────────────┘ │ │
│ │  ...         │                                                  │ │
│ └──────────────┘                                                  │ │
└─────────┬─────────────────┬─────────────────┬──────────────────┘ │
          │                 │                 │                       │
          ▼                 ▼                 ▼                       │
┌──────────────────┐ ┌─────────────┐ ┌──────────────────────────┐   │
│  AsyncSqliteSaver│ │AsyncSqliteSt│ │ SKILL.md 包（业务 skill）│   │
│ (checkpoints.db) │ │(store.db)   │ │  ./skills/<name>/        │   │
│  thread_id 隔离  │ │ namespace 隔│ │  data/{user}/skills/<>/  │   │
└──────────────────┘ └─────────────┘ │  → run_command subprocess│   │
                                     └──────────────────────────┘   │
                                                                    │
   ┌─────────────────────────────────────────────────────────────┐ │
   │  HitL 流：interrupt → ChannelRegistry.get(session_id)       │◀┘
   │            → channel.resolve_confirm(approve)               │
   └─────────────────────────────────────────────────────────────┘
```

---

## 二、关键术语速览

详细定义见 [02-features/01-skill-tool-channel-model.md](../02-features/01-skill-tool-channel-model.md)。

| 术语 | 含义 | 例子 |
|---|---|---|
| **Tool（工具）** | framework 提供的"原子操作" | `send_plan`、`task`、`write_file` |
| **Skill（技能）** | 业务领域能力（框架不内置） | `query-metrics`、`export-report` |
| **Channel（通道）** | Agent 对外通信渠道 | `WebSSEChannel`、`CLIChannel` |
| **SubAgent** | 主 Agent 派工的独立 context 子 Agent | `metric-analyzer`、`report-writer` |
| **Thread** | langgraph 的对话状态机隔离单元 | `thread_id = "{user_id}_{conversation_id}"` |
| **HitL** | Human-in-the-Loop，敏感操作前确认（**框架预留能力**，当前业务为纯分析未启用） | 未来扩展场景：删广告组 / 改预算等 |

---

## 三、请求生命周期

### 3.1 一次普通对话

```
1. 浏览器：POST /api/chat/{user_id}, body={message, conversation_id?}
2. FastAPI：
   - 创建 WebSSEChannel(session_id, queue)
   - channel_registry.register(session_id, channel)
   - asyncio.create_task(AgentRunner.run(channel, message, build_fn, config))
   - StreamingResponse(event_generator) 把 queue 内容 yield 给浏览器
3. AgentRunner.run：
   - extra_tools = make_default_tools(channel)  # [send_plan]
   - agent = build_fn(extra_tools)              # 含 SKILL.md 加载产物 + deepagents 默认
   - async for event in agent.astream_events("v2"):
       → 按 event kind 调 channel.send_token / send_step / send_progress / wait_for_confirm
4. LLM 与工具交互（langgraph 内部）：
   - LLM 调 send_plan → channel.send_plan() → SSE event "plan"
   - LLM 调 run_command(...)  → skill_loader subprocess → 返工具结果给 LLM
   - LLM 输出 markdown → channel.send_token() 逐 token → SSE event "token"
5. agent 结束 → channel.close() → SSE event "done"
6. event_generator finally 块 unregister 清理 channel_registry
```

### 3.2 HitL 中断（当前业务未启用，框架机制示意）

> 当前 `interrupt_on` 默认空列表，分析类工具不会触发 HitL。下面流程是**未来扩展到写操作时**的工作机制。

```
3.5 LLM 调 pause_campaign(...)（在 interrupt_on 列表）
   → langgraph 抛 GraphInterrupt / 输出 __interrupt__
   → runner 调 channel.wait_for_confirm() → SSE event "interrupt" + 阻塞
   
[新连接] POST /api/chat/{user_id}/confirm body={action, session_id}
   → channel_registry.get(session_id)
   → isinstance(channel, ExternallyConfirmable) ✓
   → channel.resolve_confirm(approve) → set asyncio.Event
   
[runner 恢复] _resume_safely(agent, approve, config)
   → langgraph 继续执行
   → ...回到 3.4 后续
```

### 3.3 执行中追加要求

```
[新连接] POST /api/chat/{user_id}/append body={message, conversation_id}
   → 找当前 conversation 的 active task
   → task.cancel() + asyncio.wait_for(task, timeout=3)
   → 在同一 channel + 同 thread 上启动新 runner.run(channel, new_msg, ...)
   
[原 SSE 连接] 继续接收新一轮的 event（不断流）
```

---

## 四、数据流向

### 4.1 用户输入 → LLM

```
用户消息
   → ChatRequest (Pydantic)
   → HumanMessage（langchain）
   → langgraph state messages 数组追加
   → LLM 调用前 middleware：
       - ToolOutputTruncationMiddleware（如有大 ToolMessage）
       - SummarizationMiddleware（如累计超阈值）
   → final messages → LLM API
```

### 4.2 工具调用 → 用户

```
LLM 输出 tool_call
   → langgraph node 派发到对应 tool
       - send_plan → channel.send_plan() → SSE
       - run_command → skill_loader subprocess → ToolMessage 回 langgraph
       - task → SubAgentMiddleware → 子 Agent 跑完 → ToolMessage
   → langgraph state messages 追加 ToolMessage
   → 下一轮 LLM 调用看到工具结果
```

### 4.3 Token 流 → 前端

```
LLM 输出每 chunk → langgraph emit on_chat_model_stream
   → runner 转发到 channel.send_token(chunk.content)
   → WebSSEChannel.send_token: queue.put("event: token\ndata: {...}\n\n")
   → FastAPI event_generator 从 queue yield
   → 浏览器 SSE 流读取
   → 前端 SSEClient 解析 → 调 token handler → 累加到 message bubble → marked.parse → DOM
```

---

## 五、模块依赖图

```
api/chat.py ─┬─→ api/channel/__init__.py ─┬─→ runner.py ──→ default_tools.py
             │                              ├─→ web_sse.py
             │                              ├─→ cli.py（CLI 模式）
             │                              ├─→ registry.py
             │                              └─→ base.py
             │
             ├─→ agent/builder.py ──→ deepagents.create_deep_agent
             │       ├─→ agent/llm.py::_build_model
             │       ├─→ agent/checkpointer.py::get_checkpointer
             │       ├─→ agent/store.py::get_store
             │       ├─→ agent/middleware/tool_output_truncation.py
             │       └─→ subagents 透传
             │
             ├─→ agent/skill_loader.py::load_md_skills
             │       └─→ skills/<dir>/SKILL.md + bin/...
             │
             └─→ agent/user_space.py::UserSpace（per-user 文件隔离）

api/skills.py ──→ agent/skill_loader.py + agent/user_space.py

main.py（lifespan）──→ agent/{checkpointer,store}.{init,close}_*
```

---

## 六、源码结构对照

```
ads_data_agent/
├── main.py                    FastAPI app entry + lifespan
├── config.yaml                runtime 配置
├── prompts/system_agent.md    系统级 agent 角色 prompt
│
├── agent/                     Agent 装配（核心 framework）
│   ├── builder.py             build_agent 主装配
│   ├── checkpointer.py        AsyncSqliteSaver lifecycle + Summarization 工厂 patch
│   ├── store.py               AsyncSqliteStore lifecycle
│   ├── llm.py                 _build_model（OpenAI 兼容协议，base_url 切后端）
│   ├── config.py              Pydantic 配置 schemas
│   ├── session.py             thread_id 生成
│   ├── user_space.py          per-user 文件空间
│   ├── skill_loader.py        SKILL.md 加载器
│   ├── core.py                兼容 re-export shim
│   └── middleware/
│       └── tool_output_truncation.py
│
├── api/                       HTTP 接入
│   ├── chat.py / skills.py    routers
│   ├── models.py              请求 schemas
│   └── channel/               传输适配层
│       ├── base.py            BaseChannel + ExternallyConfirmable
│       ├── default_tools.py   make_default_tools(channel)
│       ├── runner.py          AgentRunner + _is_meta_tool
│       ├── registry.py        ChannelRegistry ABC + InMemory + Redis stub
│       ├── web_sse.py         WebSSEChannel
│       └── cli.py             CLIChannel
│
├── skills/                    业务 SKILL.md 包（不内置任何业务）
│   └── <skill-name>/
│
├── tests/unit/                镜像源码结构
├── scripts/                   E2E 验证脚本
├── benchmarks/                评测 + 性能
├── docs/                      架构文档体系（本目录）
├── data/                      运行时数据（gitignored）
└── frontend/                  Vue 3 + Vite SPA
```

---

## 七、读完之后该看什么

- 想了解某个具体能力 → 跳到 [02-features/](../02-features/) 对应专题
- 想接入 API → 看 [03-api-reference/](../03-api-reference/)
- 想部署 → 看 [04-operations/](../04-operations/)
- 想了解风险 / 后续路线 → 看 [05-known-issues-and-roadmap/](../05-known-issues-and-roadmap/)
