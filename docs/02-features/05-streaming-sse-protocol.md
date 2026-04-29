# 05 流式 SSE 协议（Streaming SSE Protocol）

> ⏳ **占位**：本篇骨架已建，内容待补。

**关联源码**：`api/channel/web_sse.py`、`api/channel/runner.py`、`frontend/src/utils/sse.js`、`frontend/src/pages/Chat.vue`（SSE 客户端 handler）

**计划覆盖**：
- 完整事件类型表（token / step / progress / plan / interrupt / done / error）
- 为什么不用浏览器原生 EventSource（POST + 自定义 header）
- 前端 fetch + ReadableStream 帧解析
- 后端事件→channel 方法→SSE 字节流的映射
- 心跳 / 断线重连策略（当前是否实现）
- conversation_id 与 session_id 的差异

**ADR 占位**：
- ADR-005: 前端弃用 EventSource 改手写 SSE 解析（X-Session-Id 与 POST 大 payload）
