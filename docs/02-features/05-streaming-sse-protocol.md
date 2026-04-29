# 05 流式 SSE 协议（Streaming SSE Protocol）

> 与 [03-api-reference/sse-event-types.md](../03-api-reference/sse-event-types.md) 是
> "**机制 vs 字典**"的关系——本篇讲为什么这么设计、客户端实现要注意什么；
> 字典（每个 event 的 schema 表）查那篇。

## 一、为什么手写 SSE 而不用 EventSource

浏览器原生 `EventSource` 是 SSE 的"官方"客户端，但它有两个限制让我们没法用：

| 限制 | 我们的需求 |
|---|---|
| 只支持 GET，不支持 POST | 用户消息 `message: str` 可能很长，URL 放不下 |
| 不暴露 response header | 我们要读 `X-Session-Id` / `X-Conversation-Id` 做 follow-up 请求 |

所以前端用 `fetch` + `response.body.getReader()` 自己解析 SSE 帧。

源码：`frontend/src/utils/sse.js`。

---

## 二、SSE 帧格式

```
event: <type>\n
data: <json>\n
\n                          ← 空行 = 事件边界
```

后端用 asyncio.Queue 装这些字符串，FastAPI `StreamingResponse` 从 queue yield 推给客户端。

`api/channel/web_sse.py:14-16`：

```python
async def send_token(self, token: str) -> None:
    data = json.dumps({"token": token}, ensure_ascii=False)
    await self._queue.put(f"event: token\ndata: {data}\n\n")
```

注意 `ensure_ascii=False`——中文不转义成 `\uXXXX`，前端拿到原文。

---

## 三、跨 chunk 边界处理（前端）

### 3.1 问题

SSE 规范用空行做事件边界，`event:` 和 `data:` 行可能被 TCP 拆到不同 chunk：

```
chunk1: "event: token\ndata: {\"token\":\"你"
chunk2: "好\"}\n\nevent: token\n..."
```

如果客户端在每次 read 迭代里都重置 `eventType = 'message'`，跨 chunk 的 `data` 行
就会落到默认 `message` handler 被静默吞掉。

### 3.2 修复

`frontend/src/utils/sse.js:25-53`：

```js
let eventType = 'message'        // 在 while 循环外，跨 chunk 持久化

while (true) {
  const { done, value } = await reader.read()
  if (done) break
  buffer += decoder.decode(value, { stream: true })
  const lines = buffer.split('\n')
  buffer = lines.pop()           // 不完整行留到下次

  for (const raw of lines) {
    const line = raw.endsWith('\r') ? raw.slice(0, -1) : raw
    if (line === '') {
      eventType = 'message'      // 只在空行（事件边界）重置
      continue
    }
    if (line.startsWith('event:')) {
      eventType = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      try {
        const data = JSON.parse(line.slice(5).trim())
        this.handlers[eventType]?.(data)
      } catch (_) {}
    }
  }
}
```

关键点：
- `eventType` 在 while 循环**外**初始化，跨 read 迭代保持
- 只在**空行**才 reset eventType（事件边界）
- `\r` 兼容（CRLF / LF）
- `data:` 行解析失败静默 `catch`——避免一条坏数据把整流挂掉

---

## 四、Header 传递（连接元信息）

后端在 `chat.py:148-157`：

```python
return StreamingResponse(
    event_generator(),
    media_type="text/event-stream",
    headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "X-Session-Id": session_id,
        "X-Conversation-Id": conversation_id,
        "Access-Control-Expose-Headers": "X-Session-Id, X-Conversation-Id",
    },
)
```

`X-Accel-Buffering: no`：让 nginx 等反向代理**不缓冲** SSE 流（否则用户要等到流结束才看到数据）。
`Access-Control-Expose-Headers`：CORS 默认不暴露自定义 header，必须显式列出。

前端 SSEClient 在 fetch 后立即抓：

```js
this.sessionId = resp.headers.get('X-Session-Id')
this.conversationId = resp.headers.get('X-Conversation-Id')
```

后续 `/confirm` 请求 body 带 session_id；`/append` 带 conversation_id。

---

## 五、事件 → channel 方法映射

runner 把 langgraph 的 `astream_events("v2")` 输出转成 channel 调用：

| langgraph event | runner 处理 | channel 方法 | SSE event |
|---|---|---|---|
| `on_chat_model_start` | "AI 正在规划任务..." | `send_progress` | `progress` |
| `on_chat_model_stream` | 逐 chunk 转发 | `send_token` | `token` |
| `on_tool_start` (业务工具) | 格式化 label | `send_step` (tool_start) | `step` |
| `on_tool_end` (业务工具) | 同上 | `send_step` (tool_end) | `step` |
| `on_tool_start/end` (`send_*` 前缀) | 跳过（meta tool） | — | — |
| `on_chain_end` 含 `__interrupt__` | 阻塞 wait | `wait_for_confirm` | `interrupt` |
| 流正常退出 | — | `close` | `done` |
| 流异常 | 错误文本 | `send_token`（带 `[错误]` 前缀） | `token`（非 `error`，详见 sse-event-types.md） |

源码：`api/channel/runner.py:79-128`。

---

## 六、Meta tool 过滤：`_is_meta_tool`

```python
def _is_meta_tool(name: str) -> bool:
    return name.startswith("send_")
```

所有 `send_*` 前缀的工具被认为是"框架默认 IO 工具"——不是业务步骤，不在 step 流里展示。
当前默认 tool 集只有 `send_plan`，但前缀过滤是预留的——未来加 `send_progress` /
`send_artifact` 等不需要改 `_is_meta_tool`。

业务工具（`run_command` 等）就不会被过滤，正常推 step 事件。

---

## 七、心跳与断线重连

**当前未实现**——SSE 流没有心跳，nginx / 中间代理可能在长时间无数据时关连接。

应对策略（待实现）：
- 后端：每 30 秒推一条注释帧 `: keepalive\n\n`（SSE 规范允许，client 忽略）
- 前端：检测 `reader.read()` 返 done 且 agent 未结束时自动重连（带 conversation_id 续对话）

参见 [roadmap.md](../05-known-issues-and-roadmap/roadmap.md::P2)。

---

## 八、为什么 SSE 而不是 WebSocket

考虑过 WebSocket，最终用 SSE：

| 维度 | SSE | WebSocket |
|---|---|---|
| 协议复杂度 | HTTP 1.1，简单 | 独立协议 + upgrade |
| 客户端代码量 | fetch + 行解析（几十行） | 库依赖（socket.io 等） |
| 断线重连 | 客户端易实现（拿 conversation_id 重发 POST） | 需要 ack / 重传机制 |
| 双向通信 | 单向（这正合适——下行 token，上行用独立 POST 已够） | 双向 |
| 反代友好 | 普通 HTTP，nginx 默认支持 | 需 upgrade 配置 |

我们的场景**只需要服务端推**——用户输入用单独的 HTTP POST 即可（chat / append / confirm）。
SSE 的简洁性比 WebSocket 的双向能力更值。

---

## 设计决策记录（ADR）

### ADR-005：前端弃用 EventSource 改手写 fetch + ReadableStream

**Context**：原本想直接用浏览器原生 `EventSource`——零依赖、自动重连、SSE 协议合规。
但发现：
1. EventSource 只支持 GET，用户消息要塞 URL 太丑
2. EventSource 不暴露 response header，拿不到 `X-Session-Id`

**Decision**：用 `fetch` + `response.body.getReader()` 自己解析 SSE 帧。约 60 行
JavaScript 实现。

**Consequences**：
- ✅ 支持 POST + 大 payload + 自定义 header
- ✅ AbortController 控制取消（用户"停止"按钮直接 abort）
- ✅ 可读响应 header
- ⚠️ 失去 EventSource 的自动重连（需要自己实现，目前未实现）
- ⚠️ 跨 chunk 解析容易写错——曾经有过 eventType 在循环内重置导致丢事件的 bug，
  详见本文第三节

**Alternatives**：
- 用 `EventSourcePolyfill`：解决 POST 但不解决 header 问题
- 改成"先 POST 拿 session_id，再 GET / events?session=..."两段式：增加一次往返延迟，
  且 GET 长连接的 query string 同样难看

---

### ADR-016：用 SSE 而非 WebSocket

详见本文第八节。
