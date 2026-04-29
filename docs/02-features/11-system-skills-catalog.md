# 11 系统业务 Skill（System Skills via SKILL.md）

> **前置**：先读 [01-skill-tool-channel-model.md](./01-skill-tool-channel-model.md)
> 理解 Skill 在三层模型中的位置。

## 一、核心原则：框架不内置任何业务 skill

这是项目的根本设计——本框架是一个**通用的广告 Data Agent 平台**，不绑定任何特定业务。
业务能力全部通过部署侧装入 SKILL.md 包注入：

```
框架代码（Python，不动）   ↔   SKILL.md 包（部署侧资产）
- Channel 抽象                 ↔   query-metrics（指标查询）
- Skill loader                 ↔   export-report（报表导出）
- HitL / SubAgent / 持久化      ↔   audit-anomaly（异常审计）
- ...                          ↔   <你的业务>
```

加业务能力的标准流程是**只动 skill 包，零 Python 改动**——不重启都能加（实际上需重启
后端，因为 SKILL.md 在启动时一次性加载到 prompt）。

---

## 二、SKILL.md 包标准结构

```
{md_dir}/
└── <skill-name>/
    ├── SKILL.md                必需，frontmatter + Markdown 用法文档
    ├── package.json            可选，bin 字段映射子命令 → 脚本
    ├── bin/                    实际可执行（任意语言）
    │   └── <subcommand>.{js,py,sh,...}
    ├── config/                 可选，skill 私有配置（CSV / JSON 等）
    └── node_modules/           可选，Node 依赖（如果是 Node skill）
```

### 2.1 `SKILL.md`

```
---
name: query-metrics
description: 查询广告投放各项指标数据（曝光、点击、消费、CTR 等）
---

## 用法

query-metrics --metrics <list> --start-date <YYYY-MM-DD> --end-date <YYYY-MM-DD> [--dimensions <list>]

### 参数

- --metrics: 逗号分隔的指标名，如 click,exposure,cost
- --start-date / --end-date: 日期区间
- --dimensions: 可选，按维度分组（如 day,region）

### 示例
...
```

frontmatter（YAML 头）必填字段：
- `name`：显示名（可选，缺省用目录名）
- `description`：用于 `GET /api/skills` 的简介（取第一段，截 200 字）

正文是**完整的 CLI 用法 Markdown 文档**——loader 抽出来塞进 system prompt，LLM 看到
的就是部署方写的原文。

### 2.2 `package.json::bin`

```json
{
  "name": "query-metrics",
  "bin": {
    "query-metrics": "bin/query-metrics.js",
    "list-metrics": "bin/list-metrics.js"
  }
}
```

- `bin` 是字符串：自动用 `package.name` 做子命令名
- `bin` 是对象：每个 key 是子命令名，value 是相对 skill 目录的脚本路径
- 没有 `package.json`：loader 回退到扫 `bin/` 目录（每个文件 stem 是子命令名）

源码：`agent/skill_loader.py:67-88::_read_package_bin`。

### 2.3 解释器选择

按脚本扩展名自动选（`agent/skill_loader.py:91-101`）：

| 扩展名 | 调用方式 |
|---|---|
| `.js` | 用 `node` 执行 |
| `.py` | 用 `<sys.executable>` 执行（与本项目 venv 同一个 Python） |
| `.sh` / `.bash` | 用 `bash` 执行 |
| 其它 | 直接调用（要求文件可执行） |

---

## 三、Loader 加载链路

### 3.1 入口

`agent/skill_loader.py::load_md_skills(system_dir, user_dir)`：

1. 扫 `system_dir`（`config.yaml::skills.md_dir`）下所有子目录
2. 扫 `user_dir`（`data/{user_id}/skills/`）下所有子目录（用户上传）
3. 每个含 SKILL.md 的子目录解析 frontmatter + body + bin 注册

### 3.2 单一 `run_command` 工具

**所有 skill 共用一个工具**——LLM 看到的是 `run_command(command: str)`：

```python
SkillsPackage(
    prompt_addition="<拼进 system prompt 的 SKILL.md 全文>",
    tools=[run_command],   # 仅一个 StructuredTool
    summaries=[...],       # 给前端面板用的元信息
)
```

LLM 调用时把整条 CLI 命令串当 command 参数：

```
run_command(command="query-metrics --metrics click --start-date 2026-04-21 --end-date 2026-04-27")
```

`run_command` 内部步骤：
1. 用 `shlex.split(command, posix=True)` 拆参数
2. 第一个 token 是子命令名，校验在已注册列表内
3. 选解释器 + 拼参数列表 → 通过 `asyncio.create_subprocess_exec` 启动子进程（list-form 调用，不经过 shell）
4. 60 秒超时硬限制

源码：`agent/skill_loader.py:104-161`。

### 3.3 命名冲突：用户覆盖系统

加载顺序：

```python
for source_tag, src_dir in [("system", system_dir), ("user", user_dir)]:
    ...
    for sub in sorted(src_dir.iterdir()):
        ...
        for subcmd, script_path in bin_map.items():
            commands[subcmd] = script_path   # 用户后写覆盖系统先写
```

→ 用户能用同名 skill 替换系统实现，对开发者很友好（本地试改不动系统代码）。

---

## 四、Prompt 拼装

`SkillsPackage.prompt_addition` 长这样：

```
## 业务技能（通过 run_command 工具调用）

下列 CLI 子命令已注册。调用方式：

    run_command(command="<子命令> <参数...>")

已注册的子命令：
- query-metrics
- list-metrics
- export-report

完整用法文档（每个 skill 一段，按 CLI 命令分组说明）：

### Skill: query-metrics (system)

<SKILL.md 正文 1>

---

### Skill: export-report (user)

<SKILL.md 正文 2>
```

这段被拼到 `PLAN_INSTRUCTION + system_agent.md + agents.md` 之后，进入最终 system prompt。

LLM 启动时一次看到所有 skill 的 CLI 文档，调用时按 shell 习惯写命令——零 Python 转换。

---

## 五、安全模型

### 5.1 LLM 控制不了执行什么

`run_command` 只接受**已注册的子命令名**——LLM 想跑 `rm -rf /` 不行（不在注册列表）。
能控制的只有传给已注册脚本的**参数**。

### 5.2 list-form 子进程调用

子进程启动用参数列表（`["node", abs_path, ...args]`），**不经过 shell**——无注入风险。
即使 LLM 传 `--metrics 'foo;rm -rf /'`，脚本拿到的也只是带分号的字符串字面量，
不会被当 shell 命令执行。

### 5.3 业务脚本自己负责参数验证

LLM 可能传奇怪的参数（拼写错、注入尝试）——脚本必须自己做参数校验，同 CLI 工具的安全实践。
框架不替脚本做这层。

### 5.4 60 秒超时硬限制

通过 `asyncio.wait_for(proc.communicate(), timeout=60)` 包裹——防止单个工具调用卡死整个 agent。
超时返回 `Error: 命令 ... 执行超时（60s）` 给 LLM，LLM 可决定重试或换路径。

---

## 六、Mock 服务的位置

> 例：当前仓库提供的 `skills/metric-data-extractor` 包是 Node 实现的指标查询 skill。
> 在当前开发环境里它依赖一个**外部 mock 服务**（由开发者本地启动）来返回测试数据。
> 这个 mock 服务**与本框架无关**——部署到生产时换成真实数据源即可，框架代码完全不感知。

> SKILL.md 包是**部署侧资产**，框架不规定它怎么实现：
> - 开发环境可以打 mock 服务
> - 生产环境打真实数据库 / API
> - 测试环境可以写假数据脚本

---

## 七、加 skill 的标准流程

1. **写 SKILL.md** — 描述 CLI 用法（LLM 看的就是这个）
2. **写 `package.json`**（如果是 Node 包）或直接放 `bin/` 下脚本
3. **实现脚本** — 任意语言，从 stdin/argv 读参数，stdout 输出结果
4. **拷到 `skills.md_dir`** 或 zip 上传到 `/api/skills/{user_id}/upload`
5. **重启后端** — SKILL.md 在 process 启动时被一次性加载到 prompt
6. **验证** — `GET /api/skills/{user_id}` 看到 skill 出现在列表

---

## 八、当前限制

- **重启才生效**：SKILL.md 加载是模块级一次性，加 skill 后必须重启后端
- **prompt 膨胀**：每个 skill 一段 SKILL.md 全文进 prompt，多 skill 时 token 成本可见
- **单一 `run_command` 接口**：LLM 看不到精确参数 schema（无 typed tool args），靠
  SKILL.md 文档质量保证调用合规
- **subprocess 启动开销**：每次工具调用 fork 一个进程，对 Node 包影响明显（npm/node 启动慢）

详细 trade-off 见文末 ADR-002（在 [01-skill-tool-channel-model.md::ADR-002](./01-skill-tool-channel-model.md)）。

---

## 设计决策记录（ADR）

### ADR-006：`skills/` 不依赖 `api/` 也不依赖 `agent/`

**Context**：framework 代码（`api/` + `agent/`）和业务代码（`skills/`）应该单向依赖——
否则一改框架就要改业务，反之亦然，维护性灾难。

**Decision**：约定 `skills/` 下任何代码不 import `api.` 或 `agent.`——只通过 SKILL.md
+ 标准 stdin/stdout/argv 协议与框架交互。

**Consequences**：
- ✅ Skill 包可以独立打包、跨项目复用（只要目标项目也用同一个 SKILL.md 协议）
- ✅ 框架升级、重构都不会破坏现有 skill
- ✅ Skill 用任何语言写都行（Node / Python / Bash / Rust ...）
- ⚠️ 失去了 Python 直接调用的便利性——所有交互必须 subprocess
- ⚠️ 共享逻辑（如统一日志格式）只能通过约定，不能共享代码

**Alternatives**：
- 让 skill 写 Python `@tool` 装饰器：违背"零 Python 修改"目标
- 只支持 Python skill：失去多语言能力，特别是数据分析 Node 生态有 sharp / d3 等

---

### ADR-002：所有 SKILL.md 共用单一 `run_command` 工具

详见 [01-skill-tool-channel-model.md::ADR-002](./01-skill-tool-channel-model.md)。
