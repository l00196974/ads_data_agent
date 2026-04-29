# SSE Event Types

> ⏳ **占位**：本篇骨架已建，内容待补。

**关联源码**：`api/channel/web_sse.py`、`api/channel/runner.py`、`frontend/src/utils/sse.js`

**计划覆盖**（每个事件类型一节）：

| event | 触发时机 | data schema |
|---|---|---|
| `token` | LLM 流式输出每个 chunk | `{"token": str}` |
| `step` | 业务工具 start/end | `{"msg": str, "type": "tool_start"\|"tool_end"}` |
| `progress` | runner 推送的 LLM 思考期填充文案 | `{"message": str}` |
| `plan` | LLM 调用 `send_plan` 工具 | `{"tasks": [{"id": str, "name": str}, ...]}` |
| `interrupt` | HitL 触发，等待 confirm | `{"message": str, "preview": list}` |
| `done` | 流正常结束 | `{}` |
| `error` | 流异常结束 | `{"error": str}` |

每条覆盖：JSON schema / 后端推送代码位置 / 前端处理代码位置 / 序列示例。
