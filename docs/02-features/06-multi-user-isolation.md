# 06 多用户隔离（Multi-User Isolation）

> ⚠️ **当前是 mock 阶段**——`user_id` 直接从前端透传，**没有**认证层。
> 生产部署前必须接 SSO/JWT，详见 [05-known-issues](../05-known-issues-and-roadmap/README.md)。

## 一、隔离的三层

| 层级 | 隔离手段 | 实现位置 |
|---|---|---|
| 对话状态（messages / checkpoint） | `thread_id = "{user_id}_{conversation_id}"` 行级隔离 | `agent/session.py` + `data/checkpoints.db` |
| 文件资产（自定义 skill / agents.md / tool_outputs） | `data/{user_id}/` 目录隔离 | `agent/user_space.py` |
| Store KV（deepagents virtual filesystem） | LangGraph store namespace 隔离 | `agent/store.py`（默认 SQLite） |

---

## 二、`thread_id` 复合键

LangGraph 的 checkpointer 按 `thread_id` 做行级隔离——同一个 SQLite 文件，不同 thread
互不干扰。我们让 `thread_id` 由两段拼成：

```
{user_id}_{conversation_id}
   │              │
   │              └─ 同一用户的不同对话（"新对话"按钮重置）
   └─ 不同用户
```

源码：`agent/session.py::SessionManager.get_thread_id`。

### 2.1 conversation_id 的作用

同一 user 可以并存多个对话——比如"昨日报表"是一个 conversation，"异常诊断"是另一个。
切对话时前端调 `POST /api/chat/{user_id}/reset` 拿一个新 uuid 进新 thread，
旧 thread 的 checkpoint 不删（按 conversation_id 隔离，互不干扰）。

源码：`api/chat.py:221-226::reset_conversation`。

### 2.2 为什么要 conversation_id 而不是仅 user_id

最早只用 user_id 当 thread_id。但用户经常想"开新话题"——如果不切 thread，新话题会
继承上一个话题的全部 messages 历史，token 成本高且容易跑偏。引入 conversation_id 后，
"新对话"按钮真正切了 langgraph thread，新对话从空白历史开始。

---

## 三、文件资产隔离：`UserSpace`

`agent/user_space.py::UserSpace`：

```python
class UserSpace:
    def __init__(self, user_id: str, data_dir: str = "./data"):
        self.user_id = user_id
        self.data_dir = Path(data_dir) / user_id
        self.skills_dir = self.data_dir / "skills"
        self.memory_dir = self.data_dir / "memory"
        self.agents_md_path = self.data_dir / "agents.md"
        self._ensure_dirs()
```

每 user_id 有自己的目录：

```
data/
└── {user_id}/
    ├── skills/                 用户上传的 SKILL.md 包
    │   └── <skill-name>/
    ├── memory/                 用户记忆（保留位，未实现）
    ├── tool_outputs/           ToolOutputTruncation 卸盘的超大工具返回值
    └── agents.md               用户自定义指令（拼到 system prompt 后）
```

### 3.1 `agents.md` 合并机制

```python
# UserSpace.get_agents_md
def get_agents_md(self, system_md_path="prompts/system_agent.md") -> str:
    parts = []
    if Path(system_md_path).exists():
        parts.append(Path(system_md_path).read_text())
    if self.agents_md_path.exists():
        user_content = self.agents_md_path.read_text().strip()
        if user_content:
            parts.append("\n\n## 用户自定义指令\n" + user_content)
    return "\n\n".join(parts)
```

最终的 system prompt 是：

```
PLAN_INSTRUCTION
+
prompts/system_agent.md（系统级 agent 角色）
+
data/{user_id}/agents.md（用户级追加指令，可选）
+
SKILL.md 全文段（来自 skill loader）
```

### 3.2 user skill 隔离

`api/skills.py::upload_skill` 把上传的 zip 解压到 `data/{user_id}/skills/<dir>/`。
loader 加载时同时扫系统 dir + 用户 dir：

```python
load_md_skills(cfg.skills.md_dir, us.skills_dir)
                    │                  │
                    系统 skill          用户 skill
```

同名冲突时**用户覆盖系统**（`agent/skill_loader.py:183` 的循环顺序保证）。

---

## 四、Store namespace 隔离

deepagents 的 `FilesystemMiddleware`（virtual filesystem）按 `user_id` 做 namespace 隔离。
KV 存储的 key 是 `(namespace_tuple, key_str)`——namespace tuple 通常包含 user_id。

底层 backend 默认 `AsyncSqliteStore`（`data/store.db`）——同一文件、不同 namespace 的
行不会互相看到。

详见 [10-persistence-and-checkpoints.md](./10-persistence-and-checkpoints.md)。

---

## 五、user_id 从哪来

### 5.1 当前（mock 阶段）

前端登录页让用户**自己输入** user_id：

```js
// frontend/src/pages/Login.vue
function login() {
  if (userId.value.trim()) {
    localStorage.setItem('user_id', userId.value.trim())
    router.push('/chat')
  }
}
```

后续所有 API 请求把 user_id 拼到 URL path：`/api/chat/{user_id}` / `/api/skills/{user_id}` 等。

**安全风险**：任何人都能伪造 user_id 访问别人的 skill / 对话历史。**生产不可用**。

### 5.2 生产路径（待实现）

接 SSO/JWT 后：
- 前端登录走单点：拿到 JWT 存 cookie 或 localStorage
- API 请求带 `Authorization: Bearer <jwt>`
- 后端中间件解 JWT 拿到 user_id，**忽略** URL path 里的 user_id（或让两者必须一致）
- 也可以从 user_id 派生 conversation_id 列表，前端不再随便指定

详见 [roadmap.md](../05-known-issues-and-roadmap/roadmap.md::P0)。

---

## 六、横向扩展边界

### 6.1 单机 → 多副本

| 组件 | 单机（当前） | 多副本路径 |
|---|---|---|
| Checkpoint | `AsyncSqliteSaver` | `PostgresSaver` |
| Store | `AsyncSqliteStore` | `MysqlStore`（stub） / `PostgresStore` |
| 文件资产 (`data/{user_id}/`) | 本地 fs | NFS / S3（要改 `agent/user_space.py`） |
| ChannelRegistry | `InMemoryChannelRegistry` | `RedisChannelRegistry`（stub） |

### 6.2 数据隔离的物理实现

无论 backend 怎么换，**逻辑隔离单元**始终是 `(user_id, conversation_id)`——
切 backend 不影响隔离粒度，只影响存储介质。

这是设计上的有意选择：让"用什么 DB"和"怎么隔离"完全解耦。

---

## 七、当前未做的隔离

- **资源限额**：没有 per-user QPS / token 用量限制——单个用户的失控调用会影响其他用户。
  需要加 rate limiting middleware。
- **删用户**：没有"删除 user_id 全部数据"的 endpoint。手动 `rm -rf data/{user_id}` +
  `DELETE FROM checkpoints WHERE thread_id LIKE '{user_id}_%'` 即可，但缺自动化。
- **审计**：哪个 user 在哪个时间做了什么操作没有结构化日志——要做合规审计需补。

详见 [roadmap.md](../05-known-issues-and-roadmap/roadmap.md)。

---

## 设计决策记录（ADR）

### ADR-006：`thread_id` 用 `{user_id}_{conversation_id}` 而非纯 `user_id`

**Context**：早期版本 `thread_id = user_id`，用户每次都看到完整历史。问题：
1. 用户开新话题时旧话题历史被全量 prefill，token 成本飙升
2. LLM 容易被旧话题污染（如旧话题在分析昨天数据，新话题问预算时 LLM 会带回昨天数据的视角）

**Decision**：`thread_id = "{user_id}_{conversation_id}"`，由前端"新对话"按钮触发
`/reset` 拿一个新 conversation_id 进新 thread。

**Consequences**：
- ✅ 用户可显式切对话，避免历史污染
- ✅ 旧 thread checkpoint 不删，未来可加"对话历史回溯"功能（按 conversation_id 列）
- ⚠️ user 必须管理 conversation_id（前端 localStorage 存）—— 实际上无感
- ⚠️ thread 数量随时间无限增长——需要加 retention policy（超 30 天的 conversation 归档/删除）

**Alternatives**：
- 滚动总结（同一 thread + summarization）：实测信息丢失严重，被否
- 按时间窗口自动切 thread（每小时一个新 thread）：用户感知混乱，被否
