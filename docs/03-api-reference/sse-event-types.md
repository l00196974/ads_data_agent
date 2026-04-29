# SSE Event Types

`/api/chat/{user_id}` 返回 `text/event-stream`。本文档列出所有事件类型、schema、触发时机。

## SSE 帧格式

每个事件由 `event:` 行 + `data:` 行 + 空行边界组成：

```
event: token
data: {"token": "你好"}

event: step
data: {"msg": "query-metrics --metrics click", "type": "tool_start"}

```

> ⚠️ **跨 chunk 边界处理**：事件可能被 TCP 拆到不同 chunk，frontend 的
> `SSEClient` 必须维护持久化的 `eventType` 状态，遇到空行才重置。
> 早期版本在每次 read 迭代里都重置 eventType，导致跨 chunk 的 data 行
> 落到 default `'message'` handler 被静默吞掉。详见 `frontend/src/utils/sse.js:25-53`。

---

## 事件类型清单

| event | 触发时机 | data schema |
|---|---|---|
| [`token`](#token) | LLM 流式输出每个 chunk | `{"token": str}` |
| [`step`](#step) | 业务工具开始/结束 | `{"msg": str, "type": "tool_start" \| "tool_end"}` |
| [`progress`](#progress) | LLM 思考期填充文案 | `{"message": str}` |
| [`plan`](#plan) | LLM 调 `send_plan` 工具 | `{"tasks": [{"id": str, "name": str}, ...]}` |
| [`interrupt`](#interrupt) | HitL 触发，等待 confirm | `{"message": str, "preview": list}` |
| [`done`](#done) | 流正常结束 | `{}` |
| [`error`](#error) | 流异常结束（runner 捕获到 Exception） | `{"error": str}` —— 当前实现以 token 形式输出错误文本，`event: error` 是预留 |

---

## token

LLM 输出的每个流式 chunk 直接转发到前端，**逐字渲染最终回复**。

**Schema**：
```json
{"token": "你好"}
```

- `token`：本次 chunk 的文本内容（可能是单字、数 token、或半句）

**触发**：runner 在 `astream_events` 收到 `on_chat_model_stream` 时调
`channel.send_token(chunk.content)`。

**前端处理**：累加到当前 assistant 气泡的 `content` 字段，触发 markdown 渲染（
含 `\`\`\`chart\`\`\`` 代码块自动识别）。

---

## step

业务工具的 start / end 事件。**append-only 模型**——每个 start/end 都是独立条目，
不再有 task_update 状态变化。

**Schema**：
```json
{"msg": "query-metrics --metrics click --start-date 2026-04-21", "type": "tool_start"}
```

- `msg`：工具调用的人类可读标签（runner 通过 `_format_tool_label` 生成）
  - `run_command` 工具：直接展示完整 CLI 命令串
  - 其它工具：`<tool_name> <JSON-紧凑参数，截断 200 字符>`
- `type`：`"tool_start"` 或 `"tool_end"`

**触发**：runner 在 `on_tool_start` / `on_tool_end` 时调 `channel.send_step()`。
**注意**：`send_*` 前缀的 meta tool（如 `send_plan`）被 `_is_meta_tool()` 过滤，**不**产生 step 事件。

**前端处理**：append 一条 thinking entry。`tool_end` 时把上一条 `running` 标 `done`。

---

## progress

LLM 思考期（工具调用之间）的填充文案，给用户看到"还活着"。

**Schema**：
```json
{"message": "AI 正在规划任务..."}
```

**触发**：runner 在 `on_chat_model_start` 时调 `channel.send_progress`。
当前文案规则：
- 第一次（业务工具尚未开始）："AI 正在规划任务..."
- 后续："AI 正在准备下一步..."

**前端处理**：渲染在顶部进度条文字区，**不进消息流**。`done` 事件时清空。

---

## plan

LLM 调 `send_plan` 默认工具时推送，包含本次任务的 2-5 项执行计划。

**Schema**：
```json
{
  "tasks": [
    {"id": "t1", "name": "查询点击数据"},
    {"id": "t2", "name": "汇总并生成图表"}
  ]
}
```

- `tasks`：list[dict]，每项 `{id: str, name: str}`
  - `id`：顺序 id（t1, t2, ...）
  - `name`：≤20 字中文任务名

**触发**：`api/channel/default_tools.py::send_plan` 工具内部调
`channel.send_plan(tasks)`。

**前端处理**：作为单条 entry append 到当前思考分组，按设计**只声明一次，不更新状态**。

详细见 [02-features/03-execution-panel.md](../02-features/03-execution-panel.md)。

---

## interrupt

agent 命中 `interrupt_on` 配置的敏感工具时，runner 阻塞等待用户响应。

**Schema**：
```json
{
  "message": "即将删除 23 个广告组，请确认",
  "preview": ["adgroup_001", "adgroup_002", "..."]
}
```

- `message`：给用户看的确认提示文案
- `preview`：可选的数据预览列表（如即将删除的对象 id），前端可渲染成 list

**触发**：runner 检测到 `__interrupt__` 后调 `channel.wait_for_confirm()`，
推送本事件并 `await self._confirm_event.wait()`。

**前端处理**：弹 `ConfirmDialog`，用户选 approve/cancel 后调 `POST /confirm` 解锁后端。

详细见 [02-features/04-human-in-the-loop.md](../02-features/04-human-in-the-loop.md)。

---

## done

流正常结束信号。前端可关闭 SSE 连接、把 `isStreaming` 置 false。

**Schema**：
```json
{}
```

**触发**：runner 主循环正常退出 → `channel.close()` 推送本事件。

**前端处理**：清空 progress 文字、关闭流式状态指示器。

> **特例**：cancel + 重起场景下（`/append` endpoint 触发），runner 在 `CancelledError`
> 时**不**调 `channel.close()`——SSE 连接保持开着，让新一轮 agent 在同连接上继续推事件。
> 此时前端不会看到中间的 `done`，直到第二轮真正结束。详见 `api/channel/runner.py:118-133`。

---

## error

流异常结束。

**Schema**（预留）：
```json
{"error": "<exception message>"}
```

**当前实现差异**：runner 捕获到 `Exception` 时实际是通过 `channel.send_token`
推一段文本（`\n[错误] {e}`），而**不**单独发 `event: error` 帧。前端 SSE handler 里有
`error: (data) => {...}` 占位，但目前不会被 fired。这是 known issue（参见
[05-known-issues](../05-known-issues-and-roadmap/README.md)），未来要规范化。

---

## 完整事件流示例

用户问"查询最近7天点击数据并生成折线图"：

```
event: progress
data: {"message": "AI 正在规划任务..."}

event: plan
data: {"tasks": [{"id":"t1","name":"查询点击数据"},{"id":"t2","name":"汇总并生成图表"}]}

event: progress
data: {"message": "AI 正在准备下一步..."}

event: step
data: {"msg": "query-metrics --metrics click --start-date 2026-04-21 --end-date 2026-04-27", "type": "tool_start"}

event: step
data: {"msg": "query-metrics --metrics click --start-date 2026-04-21 --end-date 2026-04-27", "type": "tool_end"}

event: progress
data: {"message": "AI 正在准备下一步..."}

event: token
data: {"token": "**最近"}

event: token
data: {"token": "7天"}

(... 持续多个 token ...)

event: token
data: {"token": "```chart\n{\"type\":\"line\",..."}

event: token
data: {"token": "}\n```"}

event: done
data: {}
```

前端在 token 流里识别 `\`\`\`chart\`\`\`` 代码块，自动渲染 ECharts 图表。

---

## 关联源码

- 后端推送：`api/channel/web_sse.py`、`api/channel/runner.py`
- 前端解析：`frontend/src/utils/sse.js`
- 前端 handler：`frontend/src/pages/Chat.vue::sendOrAppend`
