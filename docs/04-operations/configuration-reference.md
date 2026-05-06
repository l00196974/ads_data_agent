# 配置项参考（Configuration Reference）

`config.yaml` 与 `.env` 字段全表。

## 一、配置文件分工

| 文件 | 用途 | 入库？ |
|---|---|---|
| `config.yaml` | 服务端口、CORS、interrupt 工具列表、数据目录、长上下文阈值、子 Agent 列表 | ✅ |
| `.env` | LLM API key、provider、model、base_url、敏感凭证 | ❌（`.gitignore`） |
| `prompts/system_agent.md` | 系统级 agent 角色 prompt（中文） | ✅ |
| `data/{user_id}/agents.md` | 用户级追加指令，运行时拼到 system prompt | ❌（运行时数据） |

加载优先级：**env > yaml > Pydantic 默认值**。

源码：`agent/config.py::load_config`。

---

## 二、`config.yaml` 字段

完整模板见仓库根 `config.yaml`。每段说明如下。

### 2.1 `server`

```yaml
server:
  host: 0.0.0.0
  port: 8000
  cors_origins:
    - "http://localhost:5173"
    - "http://localhost:8000"
```

| 字段 | 默认 | 说明 |
|---|---|---|
| `host` | `0.0.0.0` | uvicorn 监听地址。生产建议放反向代理后绑 `127.0.0.1` |
| `port` | `8000` | 监听端口 |
| `cors_origins` | `[]` | 允许跨域的前端 origin。生产改为实际域名，**不要**放 `*` |

### 2.2 `agent`

```yaml
agent:
  max_iterations: 20
  timeout_seconds: 120
  interrupt_on:
    - delete_adgroups
    - modify_budget
    - pause_campaign
  long_context: ...     # 见 2.2.1
  subagents: []         # 见 2.2.2
```

| 字段 | 默认 | 说明 |
|---|---|---|
| `max_iterations` | `20` | agent 单次运行最大迭代轮数（langgraph 限制） |
| `timeout_seconds` | `120` | agent 单次运行总超时（保留，未严格生效） |
| `interrupt_on` | `[]` | 命中即触发 HitL 的工具/子命令名列表 |

> ⚠️ `interrupt_on` 当前默认值（`delete_adgroups` 等）是**占位词汇**，
> 与实际 SKILL.md 包里注册的子命令名可能不匹配。部署前要按你装的 skill 实际命令名改。

#### 2.2.1 `agent.long_context`

```yaml
long_context:
  summarization_trigger_tokens: 80000
  summarization_keep_messages: 10
  tool_output_max_bytes: 5000
```

| 字段 | 默认 | 说明 |
|---|---|---|
| `summarization_trigger_tokens` | `80000` | 单 thread 累计 token 超此值触发 SummarizationMiddleware；32k context 模型建议下调到 ~20000 |
| `summarization_keep_messages` | `10` | 压缩后保留最近 N 条原文。<6 易丢上下文，>30 压缩效果有限 |
| `tool_output_max_bytes` | `5000` | 单条工具返回超此字节数时 messages 替换为摘要+文件指针，原文落 `data/{user_id}/tool_outputs/` |

详见 [02-features/08-context-fallback.md](../02-features/08-context-fallback.md)。

#### 2.2.2 `agent.subagents`

```yaml
subagents:
  - name: metric-analyzer
    description: 深度分析指标数据
    system_prompt: |
      你是指标分析专家...
    model: openai:gpt-4o    # 可选
```

每项是一个 `SubAgentSpec`。空列表 = 关闭 task 工具，主 Agent 不会派工。
详见 [02-features/07-subagent-driven-context.md](../02-features/07-subagent-driven-context.md)。

### 2.3 `persistence`

```yaml
persistence:
  type: local
  data_dir: ./data
  store_backend: sqlite
```

| 字段 | 默认 | 说明 |
|---|---|---|
| `type` | `local` | 历史字段，目前未细分使用，保留 |
| `data_dir` | `./data` | 用户数据 / SQLite 文件根目录 |
| `store_backend` | `sqlite` | `memory` / `sqlite` / `mysql`（stub） |

### 2.4 `skills`

```yaml
skills:
  md_dir: ./skills
```

| 字段 | 默认 | 说明 |
|---|---|---|
| `md_dir` | `./skills` | 扫描 SKILL.md 包的根目录。每个子目录含 SKILL.md 即识别为一个 skill |

详见 [02-features/11-system-skills-catalog.md](../02-features/11-system-skills-catalog.md)。

---

## 三、`.env` 字段

`.env.example` 是模板，真实部署 `cp .env.example .env` 后填值。

### 3.1 LLM 配置

| 字段 | 用途 | 推荐场景 |
|---|---|---|
| `ANTHROPIC_API_KEY` | Anthropic Claude 直连 | 默认场景 |
| `LLM_API_KEY` | 通用 key（优先级高于 `ANTHROPIC_API_KEY`） | OpenAI 协议厂商 |
| `LLM_BASE_URL` | OpenAI 兼容代理 endpoint | 设了即走 ChatOpenAI 路径 |
| `LLM_PROVIDER` | `openai` / `anthropic` / `google_genai` 等 | 路由路径选择 |
| `LLM_MODEL` | 模型名 | `claude-sonnet-4-6` / `gpt-4o` / `deepseek-chat` 等 |

**路由规则**（`agent/llm.py::_build_model`）：

```
所有 provider 统一走 ChatOpenAI(api_key, base_url, model)
不同后端通过 base_url 切换；provider 字段保留只为兼容旧 .env，不影响逻辑分支。
```

详见 `agent/llm.py` 实现（10 行代码）。历史曾支持 anthropic / google_genai 直连
（双协议路由），已删——多协议是过度设计。

#### 厂商对照示例

| 厂商 | LLM_PROVIDER | LLM_BASE_URL | LLM_MODEL |
|---|---|---|---|
| OpenAI | `openai` | `https://api.openai.com/v1` | `gpt-4o` |
| DeepSeek | `openai` | `https://api.deepseek.com/v1` | `deepseek-chat` |
| 通义千问 | `openai` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-max` |
| 火山方舟 | `openai` | `https://ark.cn-beijing.volces.com/api/coding/v3` | `ark-code-latest` |
| Anthropic 直连 | `anthropic` | （空） | `claude-sonnet-4-6` |

### 3.2 可观测性（可选）

| 字段 | 用途 |
|---|---|
| `LANGSMITH_TRACING` | `true` / `false`，开启 LangSmith trace |
| `LANGSMITH_API_KEY` | LangSmith API key |
| `LANGSMITH_PROJECT` | trace 归属 project（默认 `ads-data-agent`） |

详见 [observability.md](./observability.md)。

### 3.3 服务

| 字段 | 用途 |
|---|---|
| `SECRET_KEY` | 预留给认证层（当前未使用） |

---

## 四、何时改 `.env` vs `config.yaml`

简单规则：

- **敏感** 的、**部署环境差异**的 → `.env`（API key、外部服务 URL）
- **静态业务规则** 的 → `config.yaml`（interrupt_on、子 Agent 列表、长上下文阈值）

例外：`prompts/system_agent.md` 是 prompt 模板，不在两类配置里——它是产品逻辑的一部分，
应该和代码一起 review / 入库。

---

## 五、配置加载链路

```
启动 main.py
  └─ from agent.config import load_config
        ├─ from dotenv import load_dotenv → 自动读 .env 进 os.environ
        ├─ yaml.safe_load("config.yaml") → 拿 server/agent/persistence/skills 段
        ├─ os.getenv 读 LLM 段（覆盖 yaml 里的 llm 段）
        └─ AppConfig(**raw) → Pydantic 校验 + 默认值填充
```

源码：`agent/config.py:78-90`。

---

## 关联源码

- `agent/config.py` — Pydantic schemas + 加载逻辑
- `config.yaml` — yaml 模板
- `.env.example` — env 模板
- `agent/llm.py` — `_build_model` 路由逻辑
