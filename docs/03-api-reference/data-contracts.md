# Data Contracts

REST 请求 / 响应 schema、配置 schema、skill 包契约的精确定义。

## 一、HTTP 请求 schemas（`api/models.py`）

```python
class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None     # 不传则后端 uuid 生成

class ConfirmRequest(BaseModel):
    action: str                             # "approve" | "cancel"
    session_id: str

class AppendRequest(BaseModel):
    message: str
    conversation_id: str | None = None     # 必须与首次 chat 一致

class SkillCreateRequest(BaseModel):       # 遗留，新功能用 /upload
    name: str
    description: str
    code: str
```

字段语义见 [rest-endpoints.md](./rest-endpoints.md) 各 endpoint 节。

---

## 二、配置 schemas（`agent/config.py`）

`AppConfig` 是顶层；其它子段都通过 `config.yaml` + `.env` 加载。

### `AppConfig`

```python
class AppConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    agent: AgentConfig = AgentConfig()
    llm: LLMConfig = LLMConfig()
    persistence: PersistenceConfig = PersistenceConfig()
    skills: SkillsConfig = SkillsConfig()
```

### `ServerConfig`

```python
host: str = "0.0.0.0"
port: int = 8000
cors_origins: list[str] = []
```

### `AgentConfig`

```python
max_iterations: int = 20
timeout_seconds: int = 120
interrupt_on: list[str] = []                # 命中即触发 HitL 的工具名
long_context: LongContextConfig
subagents: list[SubAgentSpec] = []          # 默认空 = 关闭 task 工具
```

### `LongContextConfig`

```python
summarization_trigger_tokens: int = 80000   # 累计 token 超此值自动压缩
summarization_keep_messages: int = 10        # 压缩后保留最近 N 条原文
tool_output_max_bytes: int = 5000            # 工具单次返回超此字节数则截断卸盘
```

### `SubAgentSpec`

```python
name: str                                    # 唯一 id
description: str                             # 主 Agent 据此判断何时派工
system_prompt: str                           # 子 Agent 角色 prompt
model: str | None = None                     # "provider:model" 覆盖主模型
```

详见 [02-features/07-subagent-driven-context.md](../02-features/07-subagent-driven-context.md)。

### `LLMConfig`

```python
provider: str = "anthropic"
model: str = "claude-sonnet-4-6"
api_key: str = ""                            # 来自 env (LLM_API_KEY / ANTHROPIC_API_KEY)
base_url: str = ""                           # 设了即走 ChatOpenAI 路径
```

### `PersistenceConfig`

```python
type: str = "local"
data_dir: str = "./data"
store_backend: str = "sqlite"                # memory | sqlite | mysql(stub)
```

### `SkillsConfig`

```python
md_dir: str = "./skills"                     # 扫描 SKILL.md 包的根目录
```

---

## 三、SKILL.md 包契约

### 标准目录结构

```
{md_dir}/
└── <skill-name>/
    ├── SKILL.md                必需
    ├── package.json            可选
    ├── bin/                    实际可执行（任意语言）
    │   └── ...
    ├── config/                 可选，skill 私有配置
    └── node_modules/           可选，Node 依赖
```

### `SKILL.md` frontmatter

```yaml
---
name: query-metrics              # 显示名（可选，缺省用目录名）
description: 查询广告投放各项指标数据
---
```

frontmatter 后面是**完整的 CLI 用法 Markdown 文档**——这部分会被 loader 抽出来塞进
agent system prompt，LLM 看到的就是部署方写的 SKILL.md 原文。

### `package.json::bin` 子命令注册

```json
{
  "name": "query-metrics",
  "bin": {
    "query-metrics": "bin/query-metrics.js",
    "list-metrics": "bin/list-metrics.js"
  }
}
```

- `bin` 是字符串：自动用 `package.name` 作为子命令名
- `bin` 是对象：每个 key 是子命令名，value 是相对 skill 目录的脚本路径
- 没有 `package.json`：loader 回退到扫描 `bin/` 目录，每个文件 stem 作为子命令名

### 解释器选择

按脚本扩展名自动选解释器（`agent/skill_loader.py:91-101`）：

| 扩展名 | 调用方式 |
|---|---|
| `.js` | `node <abs_path>` |
| `.py` | `<sys.executable> <abs_path>` |
| `.sh` / `.bash` | `bash <abs_path>` |
| 其它 | 直接执行（要求文件可执行） |

### 输入约定

LLM 通过 `run_command(command="<subcmd> <args...>")` 调用：
- 第一个 token 是子命令名（必须在已注册列表）
- 后面是 shell 风格参数（用 `shlex.split(posix=True)` 拆分）
- subprocess **list-form** 调用，无 shell 注入风险

### 输出约定

- 进程的 stdout 直接作为工具返回值给 LLM
- 非零 exit code：返回 `[exit {code}] {subcmd}\nstderr:\n{err}\nstdout:\n{out}`
- 60 秒超时硬限制（`asyncio.wait_for`），超时返回 `Error: 命令 ... 执行超时（60s）`

详见 `agent/skill_loader.py::run_command`。

---

## 四、SkillsPackage 内部数据结构

`agent/skill_loader.py::load_md_skills()` 返回：

```python
@dataclass
class SkillsPackage:
    prompt_addition: str              # 拼进 system prompt 的 SKILL.md 全文
    tools: list                       # [run_command StructuredTool] 或 []
    summaries: list[dict]             # 给 GET /api/skills 用的摘要
```

`summaries` 每项：

```python
{
    "name": "query-metrics",
    "description": "<frontmatter description 的第一段，截 200 字>",
    "type": "skill_md",
    "commands": ["query-metrics", "list-metrics"],
    "source": "system" | "user"
}
```

---

## 五、SSE Event Schemas

完整定义见 [sse-event-types.md](./sse-event-types.md)。

---

## 六、checkpoint / store 数据契约

LangGraph 的 BaseCheckpointSaver / BaseStore 协议——本项目不直接持久化业务数据到这两个后端，
完全交给 langgraph + deepagents middleware 管理。

- Checkpoint 表：`data/checkpoints.db`（SQLite），按 `thread_id={user_id}_{conversation_id}` 行级分隔
- Store 表：`data/store.db`（SQLite），namespace tuple 隔离

详细 schema 见 langgraph 库文档；本项目不依赖它们的内部结构。

---

## 关联源码

- 请求 schemas：`api/models.py`
- 配置 schemas：`agent/config.py`
- skill loader：`agent/skill_loader.py`
- SSE event：`api/channel/web_sse.py`、`api/channel/runner.py`
