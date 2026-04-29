# 03 执行计划面板（Execution Plan Panel）

> **前置**：先读 [01-skill-tool-channel-model.md](./01-skill-tool-channel-model.md)。

## 一、用户视角

用户提问后，前端在思考过程区域插入一段固定的"任务计划"列表，让用户**在工具开始执行前**
就看到 Agent 打算做什么、分几步：

```
📋 任务计划：
  ⬜ 查询点击数据
  ⬜ 汇总并生成图表
  ⬜ 输出分析结论
```

这块面板的价值不是炫技，而是解决两个真问题：
1. **LLM 思考期 + 工具调用期可能有数十秒**——没有可视化反馈，用户会以为系统挂了
2. **让用户尽早察觉跑偏**——"我要的是 7 天数据，怎么计划里写的是当天？"，可以在工具真跑起来前 stop

---

## 二、端到端链路

```
[1] LLM 收到 PLAN_INSTRUCTION 强约束流程
       │
       ▼
[2] LLM 第一步必须调 send_plan(tasks=[...])
       │
       │   send_plan 是项目默认 Tool（make_default_tools 闭包绑 channel）
       ▼
[3] Tool 内部 await channel.send_plan(tasks)
       │
       │   channel 是 WebSSEChannel：序列化 SSE event "plan" 入 queue
       ▼
[4] FastAPI event_generator 从 queue yield 出去
       │
       ▼
[5] 前端 SSEClient 把 plan event 路由到 plan handler
       │
       ▼
[6] Chat.vue 把任务列表 append 进当前思考分组（一条 entry-plan kind）
```

涉及源码：
- `api/chat.py::PLAN_INSTRUCTION` — LLM 输出契约
- `api/channel/default_tools.py:22-50` — send_plan Tool 工厂
- `api/channel/web_sse.py:22-25` — WebSSEChannel.send_plan（推 SSE event）
- `api/channel/cli.py:19-22` — CLIChannel.send_plan（终端打印）
- `api/channel/runner.py:65-72` — make_default_tools 装配点
- `frontend/src/pages/Chat.vue` — plan event handler + UI

---

## 三、PLAN_INSTRUCTION：LLM 输出契约

`api/chat.py::PLAN_INSTRUCTION`

LLM 必须按以下顺序输出，否则面板渲染会错位：

```
1. 调 send_plan(tasks=[...])         ← 计划只声明一次
2. 按计划逐项调 run_command(...)      ← 业务工具
3. 输出 Markdown 文本（含可选 ```chart``` 代码块）
4. (可选) 一行追问引导
```

**显式禁止**的事：
- ❌ 在工具调用之间输出"我现在要查询..."散文（前端会自己显示工具进度）
- ❌ 重复调 send_plan / 中途追加 task（计划只声明一次，避免面板状态机复杂化）
- ❌ 调任何额外的"进度 / 图表 / 摘要"工具——除 `send_plan` 外，所有结果走最终 markdown 输出

### 3.1 任务粒度约定

- 数量 2-5 项（少了不必要、多了用户失焦）
- name ≤20 字简短中文
- id 顺序 t1, t2, t3...

这些约定写在 `default_tools.py::send_plan` 的 docstring 里——LLM 看到的是工具描述，
不是项目内部备注。docstring 越精确，LLM 输出越合规。

---

## 四、为什么 `send_plan` 是 Tool 不是 Channel skill

早期版本里，`send_plan` 是 `channel.get_skill()` 返回的闭包工具——每个 channel 自己注入，
agent 看到的工具列表会随 channel 切换而变。这违背了"agent 视角 channel 透明"的设计目标
（见 [02-channel-abstraction.md](./02-channel-abstraction.md)）。

当前架构把 `send_plan` 提升为**项目默认 Tool**：

| 维度 | 旧（channel.get_skill） | 新（make_default_tools） |
|---|---|---|
| Tool 定义位置 | 散在 channel 子类里 | `api/channel/default_tools.py` 单文件 |
| Agent 看到的工具签名 | 跨 channel 不一致（理论上） | 跨 channel 完全一致 |
| Channel 子类职责 | 既定义工具又实现 IO | 只实现 IO（send_plan 方法） |
| 加新默认工具 | 改所有 channel 子类 | 只改 default_tools.py |

具体见 [01-skill-tool-channel-model.md](./01-skill-tool-channel-model.md) 第二节。

---

## 五、状态控制反转：runner 拥有任务状态机

### 5.1 问题

最早设计期望让 LLM **主动**调 `update_task(task_id, status)` 工具，每个业务工具调用前后
各调一次：

```
send_plan(...) 
→ update_task(t1, "running") 
→ query_metrics(...) 
→ update_task(t1, "done") 
→ update_task(t2, "running") 
→ ...
```

实际跑起来发现 LLM 倾向**一次输出多个 tool_calls 让 langgraph 批量执行**：

```
LLM 一次输出: [
  update_task(t1, "running"),
  query_metrics(...),
  update_task(t1, "done"),
  update_task(t2, "running"),
  query_chart(...),
  update_task(t2, "done"),
]
```

→ 前端看到的是几乎同时收到一连串 task_update 事件，**running 状态一闪而过**，根本看不到执行进度。

### 5.2 解决：把状态机从 LLM 拿回 runner

`api/channel/runner.py::AgentRunner.run` 监听 langgraph 的 `astream_events("v2")` 事件流，
**runner 自己在 `on_tool_start / on_tool_end` 时推送状态**——LLM 不再调 `update_task`：

```python
elif kind == "on_tool_start":
    tool_name = event.get("name", "tool")
    if _is_meta_tool(tool_name):    # send_* 前缀过滤
        continue
    await channel.send_step(step_msg, "tool_start")
elif kind == "on_tool_end":
    ...
    await channel.send_step(label, "tool_end")
```

`update_task` 工具被删（commit `0a7ebf4`）。

`_is_meta_tool` 过滤所有 `send_*` 前缀——`send_plan` 调用本身**不**产生 step 事件，
因为它是元工具不是业务步骤。

### 5.3 当前模型：append-only

前端不再做"状态推断"——每个 step 事件就是一条独立的 thinking entry：

```
step (tool_start, "query-metrics --metrics click ...") → append entry "查询执行中..."
step (tool_end,   "query-metrics --metrics click ...") → 把上一条 running entry 标 done
```

简单、健壮、不会因为 LLM 批量调用打乱顺序。详见 `tests/unit/api/test_runner_streaming.py::test_no_task_update_events_emitted`。

---

## 六、前端渲染

`frontend/src/pages/Chat.vue` 的 `plan` event handler：

```js
plan: (data) => {
  closeStreaming()
  const tasks = Array.isArray(data.tasks) ? data.tasks : []
  if (!tasks.length) return
  const lines = tasks.map(t => `  ⬜ ${t.name || t.id || ''}`).join('\n')
  appendLog({ label: `📋 任务计划：\n${lines}`, kind: 'plan' })
},
```

把任务列表作为单条 entry append 到当前思考分组（thinking-group）——这与 step / token
事件共用同一容器，是"append-only 思考日志"模式的自然扩展。

CSS 方面，`.entry-plan` 类用 `white-space: pre-line` 保留换行符，font-family 改回中文友好体
（覆盖默认的 monospace）。

> **不**再为每个 task 维护独立的 status icon（pending/running/done）—— 状态机已被
> append-only 模型取代。如果未来要恢复"状态变化"的可视化，应基于 SubAgent 的 task 工具
> 边界，而不是回到旧的 update_task 设计。

---

## 七、SSE Event Schema

完整定义见 [03-api-reference/sse-event-types.md](../03-api-reference/sse-event-types.md)。
本节仅列 plan 事件：

```
event: plan
data: {"tasks": [{"id": "t1", "name": "查询点击数据"}, {"id": "t2", "name": "汇总并生成图表"}]}
```

字段：
- `tasks: list[dict]` 必填
  - `id: str` — 顺序 id（t1, t2, ...）
  - `name: str` — 任务名（≤20 字中文）

---

## 设计决策记录（ADR）

### ADR-002：状态控制反转——runner 拥有任务状态机，废弃 LLM 主动调 update_task

**Context**：早期设计让 LLM 通过 `update_task(task_id, status)` 工具上报每个任务的状态
变化，前端按 task_update 事件渲染 pending → running → done。

实际运行发现 LLM 倾向把多个 tool_call 合并成一批输出（langgraph 会批量执行），
导致 "running" 状态消息几乎与 "done" 同时到达前端，状态过渡效果完全消失。
而 LLM 模型规模越大（推理越快），这个问题越严重。

**Decision**：把任务状态机从 LLM 移到 runner——runner 监听 langgraph 的
`on_tool_start` / `on_tool_end` 事件，**根据真实工具执行边界**推送 step 事件。
`update_task` 工具删除，LLM 不再感知任务状态。

为简化前端，干脆放弃"状态推断"——前端按 append-only 模式渲染 thinking 日志，
每个 tool_start 加一条 "running" entry、tool_end 把它改 "done"。

**Consequences**：
- ✅ 状态变化时序与真实执行时序绑定，前端 UX 不再被 LLM 批处理打散
- ✅ LLM 工具调用 prompt 简化（少一个工具 + 少一组规则）
- ✅ 前端逻辑大幅简化（不需要 task → entry 的索引匹配）
- ⚠️ 任务"完成度"不再可视化（不再有 1/5 → 2/5 进度条），换来稳定的 thinking 日志
- ⚠️ `send_plan` 仅一次性声明，**计划本身不更新**——LLM 中途想加任务也不行

**Alternatives**：
- 强制 LLM 每个 update_task 单独输出（修 prompt）：实测无效，模型仍合并
- 让 langgraph 串行化 tool_calls：违背 langgraph 设计，性能损失大
- 引入"虚拟"状态机基于 send_plan 的 task id 与 step 事件名匹配：复杂、易错位

参考 commit `0a7ebf4`（删除 update_task 工具）。

---

### ADR-013：`send_plan` 强制调用增加一轮 LLM round trip 的 trade-off

**Context**：项目曾经有过更激进的简化版本（commit `28737a9`），完全删掉了 send_plan，
让 LLM 直接调业务工具不预声明计划。这样省一轮 LLM round trip（首次调用更快）。

但产品反馈：去掉计划面板后，用户在 LLM 思考期看到的是"...正在思考"占位文案，
不知道 Agent 打算做什么——预期管理失败，体感"卡了"。

**Decision**：恢复 send_plan 作为项目默认 Tool，PLAN_INSTRUCTION 强制 LLM 第一步调它。
代价是首次响应慢一轮 LLM round trip（~1-3 秒），换来计划面板可视化。

**Consequences**：
- ✅ 计划面板可见，用户预期管理稳定
- ✅ 用户可在工具真跑起来前 stop（如发现计划跑偏）
- ⚠️ 单次问答的"首字节时间"（TTFB）变长一轮 LLM round trip
- ⚠️ token 成本略增加（计划列表的输入 token 永远是 prompt 一部分）

**Alternatives**：
- "仅计划复杂任务才调 send_plan"：让 LLM 判断需要计划与否——LLM 经常误判，跳过
- 完全去掉 send_plan：体验变差，已尝试，被推翻
