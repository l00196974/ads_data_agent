# 01 三层概念模型：Skill / Tool / Channel

> 本篇是文档体系的**术语基石**。后续所有 features 文档都建立在这三个概念上，
> 跳过这篇看其它会迷路。

## 一、为什么要明确分三层

LangChain / langgraph 生态的术语很模糊——任何能被 LLM 调用的东西都被泛称 "tool"，
任何注入到 agent 的能力都被泛称 "skill"。在一个**框架不内置业务能力、对接多种前端通道**
的项目里，这种模糊性会让人无法回答"这块代码该放哪"。

我们参考 Claude Code 的 mental model，把"agent 可调用的能力"明确切成三层：

| 维度 | **Tool（工具）** | **Skill（技能）** | **Channel（通道）** |
|---|---|---|---|
| **定位** | 系统内置的"原子操作" | 业务领域能力封装 | Agent 与外界对话的渠道 |
| **稳定性** | 跨部署稳定 | 按部署而异 | 运行时按请求实例化 |
| **来源** | framework 提供（含 deepagents 默认 + 项目默认） | `skills.md_dir` 扫描 + 用户上传 | `api/channel/` 下的 BaseChannel 子类 |
| **生命周期** | 进程级 | 进程级（重启时重扫目录） | per-session（每次 chat 请求新建） |
| **是否进 LLM 工具签名** | ✅ 是 | ✅ 是（聚合到 `run_command`） | ❌ 否（暴露给 tool 内部调用） |
| **典型例子** | `send_plan`、`write_file`、`task` | `query-metrics`、`export-report` | `WebSSEChannel`、`CLIChannel` |

**口诀**：
- **Tool 是 framework 提供的"螺丝刀"**（含 framework 自带的"系统工具"）；
- **Skill 是部署侧装进项目的"业务能力"**（框架不内置）；
- **Channel 是 Agent 的"对外接口契约"**（决定输出形态）。

---

## 二、Tool（工具）

### 2.1 来源

Tool 分**两类**——agent 装配时合并：

| 类别 | 注入位置 | 例子 |
|---|---|---|
| **deepagents 默认工具** | `create_deep_agent` 自动注入 | `task`（SubAgent 委派）、`write_file` / `read_file`（virtual filesystem）、`todo` 等 |
| **项目默认工具** | `api/channel/default_tools.py::make_default_tools(channel)` 工厂 | `send_plan` |

项目默认工具的特点：**签名稳定**（agent 视角恒定），但**实现绑定到当前 channel**——
工厂闭包捕获 channel 实例，工具内部调用 `channel.send_plan(tasks)` 完成实际 IO。

### 2.2 装配链路

```
api/chat.py::chat()
    │
    │   每个请求新建 channel 实例
    ▼
WebSSEChannel(user_id, session_id, queue)
    │
    ▼
api/channel/runner.py::AgentRunner.run(channel, ...)
    │
    │   ① 调工厂构造默认工具
    ▼
extra_tools = make_default_tools(channel)   ← 闭包绑 channel
    │
    │   ② 透传给 build_agent
    ▼
agent/builder.py::build_agent(extra_tools=...)
    │
    │   ③ skill loader 同时加载业务 skill
    ▼
md_pkg.tools = [run_command]                ← 来自 SKILL.md 加载
all_tools = md_pkg.tools + extra_tools
    │
    ▼
deepagents.create_deep_agent(tools=all_tools, subagents=..., ...)
    │
    │   create_deep_agent 内部还会注入 task / write_file / ... 等默认工具
    ▼
Agent 看到的最终工具集 = run_command + send_plan + deepagents 内置默认工具
```

源码定位：
- `api/channel/default_tools.py:1-50`
- `api/channel/runner.py:65-72`
- `agent/builder.py:20-65`
- `agent/skill_loader.py::load_md_skills`

---

## 三、Skill（技能）

### 3.1 来源

**Skill 都来自配置目录扫描，框架不内置任何业务 skill**。

```yaml
# config.yaml
skills:
  md_dir: ./skills    # 扫描这个目录下所有子目录
```

```
{md_dir}/
├── query-metrics/             ← 一个 skill 包
│   ├── SKILL.md               必须，frontmatter + Markdown 用法文档
│   ├── package.json           可选，bin 字段映射子命令 → 脚本
│   └── bin/
│       └── query-metrics.js   实际可执行（任意语言）
└── export-report/             ← 另一个 skill 包
    └── ...
```

加 user skill 时，loader 会同时扫 `data/{user_id}/skills/`，**用户 skill 命名冲突时覆盖系统**。

### 3.2 加载产物

`agent/skill_loader.py::load_md_skills()` 返回 `SkillsPackage`：

```python
@dataclass
class SkillsPackage:
    prompt_addition: str           # 拼进 system prompt 的 SKILL.md 全文
    tools: list                    # [run_command]（如果有 skill）或 []
    summaries: list[dict]          # 给前端面板展示的摘要
```

**注意**：所有 skill 共用一个 `run_command` 工具——LLM 看到的是 SKILL.md 的 CLI 用法，
调用时 `run_command(command="query-metrics --metrics click ...")`，
loader 解析子命令名，subprocess 执行对应脚本。

为什么不每个 skill 一个 LangChain tool？见文末 ADR-002。

### 3.3 边界守则

`skills/` 下的代码 **不依赖 `api/` 也不依赖 `agent/`**——保持纯领域模块。
这让 skill 可以被独立打包、跨项目复用，框架更换时不需要改 skill。

源码定位：
- `agent/skill_loader.py`（loader 全部逻辑）
- `agent/config.py::SkillsConfig`（配置 schema）
- `api/skills.py`（用户上传 / 列表 / 删除 endpoints）

---

## 四、Channel（通道）

### 4.1 Channel 自己的"Skill"

Channel 是 Agent 与外界对话**渠道**的抽象。`BaseChannel` 暴露的下列方法构成
**Channel 的 skill 集合**——它们描述"如何向 channel 推消息 / 从 channel 拿响应"：

| Channel skill | 用途 |
|---|---|
| `send_token(token)` | LLM 流式 token 输出 |
| `send_step(msg, step_type)` | 业务工具开始/结束 |
| `send_progress(message)` | LLM 思考期填充文案 |
| `send_plan(tasks)` | 推送结构化执行计划 |
| `wait_for_confirm(message, preview)` | HitL 阻塞等待用户响应 |

**Channel 的 skill 不是 Tool**——它们是 channel 实现自己的接口方法，被 Tool 内部调用。
`send_plan` Tool（agent 看到的）→ 调用 `channel.send_plan` skill（channel 自己的方法）。

### 4.2 为什么这层独立存在

同一个 Agent 既要服务 Web 前端的 SSE 流，也要在 CLI / Slack / WebSocket 等渠道下工作。
如果让"输出格式"渗进 agent 装配代码，每加一种渠道都要动一遍 prompt 和工具。

Channel 抽象把"输出格式差异"装进 `BaseChannel` 子类：

```
WebSSEChannel.send_plan  → SSE event "plan" + JSON tasks
CLIChannel.send_plan     → print "📋 执行计划:\n  ⬜ t1: ..."
```

LLM 的 prompt 和工具签名在两种 channel 下**完全相同**——agent 感知不到差别。

详细机制见 [02-channel-abstraction.md](./02-channel-abstraction.md)。

源码定位：
- `api/channel/base.py`（BaseChannel 抽象）
- `api/channel/web_sse.py`（Web SSE 实现）
- `api/channel/cli.py`（CLI 实现）

---

## 五、完整 trace：一次用户提问的三层走位

```
用户发送：「查询最近7天点击数据并生成折线图」
   │
   ▼
[1] FastAPI 收到 POST /api/chat/{user_id}
      创建 WebSSEChannel 实例 → channel_registry.register()
   │
   ▼
[2] AgentRunner.run(channel, message, build_fn, config)
      构造 extra_tools = make_default_tools(channel)   ← Tool 注入
      build_agent(extra_tools=[send_plan])            ← Tool + Skill 装配
   │
   ▼
[3] Agent 启动，按 PLAN_INSTRUCTION 流程：
      ┌─ LLM 调 send_plan(tasks=[{id:t1, name:查询点击数据}, ...])
      │     └─ Tool 内部调 channel.send_plan(tasks)
      │           └─ Channel skill：SSE event "plan" 入队 → 前端
      │
      ├─ LLM 调 run_command(command="query-metrics --metrics click ...")
      │     └─ skill_loader 找到 query-metrics 子命令
      │           └─ subprocess 执行 → 返回 stdout 给 LLM
      │
      └─ LLM 输出 markdown（含 ```chart``` 代码块）
            └─ runner 监听 on_chat_model_stream
                  └─ channel.send_token(token)（每个 chunk）
                        └─ Channel skill：SSE event "token" 入队 → 前端
```

每步执行的"东西"分清属于哪一层：
- `send_plan`、`run_command`、`task`（如果调子 agent）= **Tool**
- `query-metrics`（外部脚本）= **Skill**
- `WebSSEChannel.send_plan`、`send_token` = **Channel skill**

---

## 六、配置 / 部署对照表

| 我想... | 改哪 |
|---|---|
| 新增一个业务功能（如导出报表） | 写一个 `skill 包`，放进 `skills/`，重启即生效——**不改 Python** |
| 新增一种前端（如 Slack） | 在 `api/channel/` 加 `SlackChannel(BaseChannel)`，实现 5 个 skill 方法 |
| 新增一个默认工具（如 send_progress） | 改 `api/channel/default_tools.py::make_default_tools` |
| 新增一个子 Agent | 改 `config.yaml::agent.subagents` 列表 |

记住：**90% 的业务变更应该只改 skill 包**——它是这个架构的灵魂层。

---

## 设计决策记录（ADR）

### ADR-001：Skill / Tool / Channel 三分而非合并

**Context**：早期版本把所有 LLM 可调能力统称 "skill"，channel 也通过 `get_skill()` 返回工具列表。
术语混乱导致：
1. 业务变更动到 framework 代码的概率高（应该只动 skill 包）
2. 加新 channel 时要改 prompt（实际不应该）
3. 评审者要反复问"这个 send_plan 到底属于哪一层"

**Decision**：按"稳定性 / 来源 / 是否进 LLM 工具签名"三个维度切分：
- 跨部署稳定且 framework 提供 → **Tool**
- 部署侧注入的业务能力 → **Skill**
- 对外渠道契约 → **Channel**

**Consequences**：
- `BaseChannel` 不再返回 tool 列表（移除 `get_skill()`），只暴露通信接口方法
- 默认 tool（`send_plan`）改由 `make_default_tools(channel)` 工厂构造
- Skill 加载严格走配置目录扫描，框架内零内置 skill

**Alternatives**：
- 保留单层 "skill"：术语简单但层次不清——已尝试，被推翻
- 抄 LangChain "tool/agent/chain" 三分：与本项目实际架构不对应（我们没有 chain 概念）

---

### ADR-002：所有 SKILL.md 共用一个 `run_command` 工具，而非每 skill 一个 LangChain tool

**Context**：常规做法是把每个 skill 注册成独立 LangChain tool（`@tool` 装饰器），
让 LLM 看到精确的参数 schema。但本项目的 skill 是**外部脚本**（Node / Python / Bash），
按这条路要求：
1. 为每个 skill 写 Python 包装层（`@tool` + 参数校验 + subprocess 调用）
2. 每加一个 skill 都要改 Python 代码并重启

**Decision**：所有 skill 共用一个 `run_command(command: str)` 工具，参数是完整的 shell
风格 CLI 调用串。LLM 看到的"用法"通过把 SKILL.md 全文塞进 system prompt 提供。

**Consequences**：
- ✅ 加新 skill 只需要拷目录到 `md_dir`，重启后端，**零 Python 修改**
- ✅ Skill 用任何语言实现都行（只要有 SKILL.md + 可执行脚本）
- ⚠️ system prompt 随 skill 数量膨胀（每个 skill 一段文档）
- ⚠️ 参数校验从"框架层"下沉到"脚本自己"——脚本必须做参数验证（同 CLI 安全实践）

**Alternatives**：
- 每 skill 一个 LangChain tool：上下文成本低但部署摩擦高，违背"框架不内置业务"原则
- 提供一个 schema 注册表 + 单一调用 tool：复杂度比 SKILL.md 还高，没收益

参考：Claude Code 的 SKILL 体系 / Anthropic 的 "Skills" 提案。
