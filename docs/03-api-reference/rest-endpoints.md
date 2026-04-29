# REST Endpoints

> 接入 Agent 的 client（前端 / 第三方系统）必读。**SSE 流的事件协议**单独见
> [sse-event-types.md](./sse-event-types.md)。**请求 / 响应 schema 类型**见
> [data-contracts.md](./data-contracts.md)。

## Base URL

- 开发环境：`http://localhost:8000`
- 前端 dev server (5173) 通过 vite proxy 转发 `/api/*` → `:8000`
- 生产单端口部署：前后端共用 `:8000`，前端构建产物直接 mount 在 `/`

CORS 由 `config.yaml::server.cors_origins` 控制，默认放通 `5173` / `8000`。

---

## 1. POST `/api/chat/{user_id}` — 发起对话

发送一条用户消息，**SSE 流式返回** Agent 执行过程。

### Request

| 项 | 值 |
|---|---|
| Path | `/api/chat/{user_id}` |
| Method | `POST` |
| Content-Type | `application/json` |
| Body schema | `ChatRequest`：`{message: str, conversation_id?: str}` |

```json
{
  "message": "查询最近7天点击数据并生成折线图",
  "conversation_id": "a1b2c3d4e5f6"
}
```

- `message`：用户自然语言提问（必填）
- `conversation_id`：同一对话的多轮请求传同一个值；**不传时后端生成新 uuid 进入新 thread**

### Response

`Content-Type: text/event-stream`，长连接持续到 agent 完成或出错。

**关键 response header**：

| Header | 用途 |
|---|---|
| `X-Session-Id` | 用于 `/confirm` 等 follow-up 请求 |
| `X-Conversation-Id` | 后端实际使用的 conversation_id（首次未传时返回新生成的） |

事件流类型见 [sse-event-types.md](./sse-event-types.md)。

### 错误情况

- LLM API key 无效 / 网络故障：流中以 `event: error` 推送后 `event: done`
- 参数校验失败：HTTP 422（FastAPI 自动响应）

---

## 2. POST `/api/chat/{user_id}/confirm` — HitL 确认

回应 `event: interrupt`，告知 agent 用户的 approve/cancel 决定。

### Request

```json
{
  "action": "approve",
  "session_id": "u_a1b2c3d4"
}
```

- `action`：`"approve"` 或 `"cancel"`（其它值视为 cancel）
- `session_id`：从 `/chat` 响应 header `X-Session-Id` 拿到

### Response

```json
{"status": "ok"}
```

### 其它状态

| `status` 值 | 含义 |
|---|---|
| `"ok"` | confirm 已转发，agent 继续执行 |
| `"not_found"` | session_id 不存在（可能已结束、或多 worker 路由错） |
| `"not_supported_on_this_channel"` | channel 不支持外部确认（例如 CLIChannel） |

详见 [02-features/04-human-in-the-loop.md](../02-features/04-human-in-the-loop.md)。

---

## 3. POST `/api/chat/{user_id}/cancel` — 主动停止

> ⚠️ 当前实现为 stub：返回 `{"status": "cancelled"}`，**不实际停止 agent**。
> 真实停止 chat 流需要前端 `AbortController.abort()` 关闭 SSE 连接，
> 后端 SSE generator 的 `finally` 块会释放资源。

预留路径，未来接入 task registry 后可实现服务端主动 cancel。

---

## 4. POST `/api/chat/{user_id}/append` — 执行中追加要求

执行中追加新要求 = **取消当前 agent task + 在同 channel 同 thread 上启动新一轮**。
checkpointer 保留完整历史，LLM 在新一轮看到原问题 + 已执行的工具调用 + 新追问。

### Request

```json
{
  "message": "改成只看华南区域",
  "conversation_id": "a1b2c3d4e5f6"
}
```

`conversation_id` 必须与首次 chat 的相同——否则无法定位 active task，返回 `no_active_run`。

### Response

```json
{"status": "redirected", "message": "改成只看华南区域"}
```

### 其它状态

- `{"status": "no_conversation_id", "hint": "..."}`：缺 conversation_id
- `{"status": "no_active_run", "hint": "..."}`：当前没有跑中的 agent，应直接 `/chat` 发新问题

### 设计说明

此前用过 `aupdate_state` 直接把追加消息写进 checkpointer，但 langgraph 在 LLM
"生成最终回复"阶段不会回 model 节点，新消息只能等下一轮才被看到——表现为"被忽略"。
现在的 cancel + 重起方案保证 LLM 立刻感知。

详见 `api/chat.py:180-218` 的 docstring。

---

## 5. POST `/api/chat/{user_id}/reset` — 开新对话

清空 conversation_id，下次 chat 进入全新 langgraph thread。**旧 thread 的 checkpoint 不删**——按 conversation_id 隔离，互不干扰。

### Request

无 body。

### Response

```json
{"conversation_id": "f1e2d3c4b5a6"}
```

前端拿到新 id 后存 localStorage，作为后续 chat 的 conversation_id。

---

## 6. GET `/api/skills/{user_id}` — 列出 skill

返回当前 user 视角下可用的 skill 清单（系统 + 用户自定义）。

### Response

```json
{
  "system": [
    {
      "name": "metric-data-extractor",
      "description": "查询广告投放各项指标数据...",
      "type": "skill_md",
      "commands": ["query-metrics", "list-metrics"],
      "source": "system"
    }
  ],
  "user": [
    {
      "name": "my-custom-report",
      "description": "...",
      "type": "skill_md",
      "commands": ["custom-export"],
      "source": "user"
    }
  ]
}
```

每条来自 `agent/skill_loader.py::SkillsPackage.summaries`。

---

## 7. POST `/api/skills/{user_id}/upload` — 上传用户自定义 skill

接收 SKILL.md 风格的技能包 zip 文件，解压到 `data/{user_id}/skills/`。

### Request

`multipart/form-data`：

| Field | 值 |
|---|---|
| `file` | `.zip` 文件，≤ 50MB |

### zip 包结构要求

zip 内**单一顶层目录**，目录里**必须**有 `SKILL.md`：

```
my-skill.zip
└── my-skill/                 ← 单一顶层目录
    ├── SKILL.md              ← 必需
    ├── package.json          ← 可选
    └── bin/                  ← 实际可执行
        └── ...
```

### 安全校验

- 禁止绝对路径（`/...`）和路径穿越（`..`）
- 必须有单一顶层目录
- 必须有 `<dir>/SKILL.md`

### Response

```json
{"status": "uploaded", "name": "my-skill"}
```

### 错误码

| 状态码 | 触发条件 |
|---|---|
| 400 | 非 zip / 空 zip / 多顶层目录 / 缺 SKILL.md / 非法路径 |
| 413 | 文件 > 50MB |

### 命名冲突

如果用户已有同名 skill 或 system 已有同名 skill：
- 同名 user skill：**先 rmtree 再解压**（覆盖）
- 同名 system skill：用户 skill 在加载阶段优先（loader 后写覆盖前写）

---

## 8. DELETE `/api/skills/{user_id}/{skill_name}` — 删除用户 skill

### Path 参数

- `skill_name`：skill 目录名（不能含 `/` 或 `..`）

### Response

```json
{"status": "deleted", "name": "my-skill"}
```

### 错误码

- 400：非法 skill_name
- 404：skill 不存在或不是目录

---

## 9. POST `/api/skills/{user_id}` — 遗留接口（不推荐）

> ⚠️ 历史遗留——通过 JSON 提交 Python 代码作为 user skill。
> 但 user `.py` 文件**当前不会被 agent 加载**（loader 只识别 SKILL.md 包）。
> 仅为向后兼容保留，新功能用 `/upload`。

---

## 错误响应通用约定

- HTTP 4xx：FastAPI 标准 `{"detail": "..."}` 格式
- HTTP 5xx：未捕获异常，FastAPI 默认返 `{"detail": "Internal Server Error"}`
- SSE 流内错误：`event: error`（详见 [sse-event-types.md](./sse-event-types.md)）

---

## 调用顺序示例（端到端）

### 场景 A：普通对话

```
1. POST /api/chat/{user}                  → SSE 流，响应 header 带 X-Session-Id 与 X-Conversation-Id
2. 前端持续读 SSE 直到 event: done
```

### 场景 B：HitL 确认

```
1. POST /api/chat/{user}                  → SSE 流
2. 接收 event: interrupt                  → 弹确认框
3. POST /api/chat/{user}/confirm          → 用户选 approve/cancel
4. 继续读 SSE 直到 event: done
```

### 场景 C：执行中追加

```
1. POST /api/chat/{user}                  → 第一轮 SSE 流
2. (流未结束时) POST /api/chat/{user}/append    → 后端 cancel 第一轮 + 启动第二轮
3. **同一 SSE 连接** 接收第二轮事件
4. 直到 event: done
```

### 场景 D：新对话

```
1. POST /api/chat/{user}/reset            → 拿到新 conversation_id
2. POST /api/chat/{user}                  → 用新 id 发起，进入全新 thread
```

---

## 关联源码

- `api/chat.py` — chat / confirm / cancel / append / reset
- `api/skills.py` — skill 列表 / 上传 / 删除
- `api/models.py` — 请求 schemas
- `main.py` — router 挂载、CORS 配置
