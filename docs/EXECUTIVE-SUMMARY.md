# 华为广告 Data Agent — 技术架构评审概要

> ⚠️ **2026-05 重构后**：本文写于 deepagents/langgraph 时代，提及的 `create_deep_agent`
> / `AsyncSqliteSaver` / `langgraph` 字样已不再适用。当前架构为纯 Python agent loop
> （`agent/loop.py` + `agent/state.py`）。映射详见 [`docs/README.md`](./README.md) 顶部表格，
> 新架构入门看 `CLAUDE.md`。本文当历史背景。

> 用于内部技术评审 / 对外技术品牌的高密度版本。
> 详细文档体系见 [docs/](./README.md)；本概要约 12 屏，每屏一段，用 `---` 分隔。

---

## 0. 一句话定位

> **一个让营销 / 运维 / 数据分析师通过自然语言对话，完成数据查询、异常诊断、深度分析（指标 / 人群画像 / 行为）、报表生成的智能助手框架——业务能力可插拔（SKILL.md 包），框架与业务零耦合。**

```
用户：「查询昨天中国区 KA 客户的 CTR 异常数据」
   ↓
Agent: 调 query-metrics → 调 detect-anomaly → 输出 Markdown 报告 + 折线图
```

⚠️ **当前阶段聚焦"分析场景"，不涉及任何系统写操作**——所有输出是只读分析结果（表格 / 图表 / 诊断 / 报告）。框架已实现 HitL 写操作守卫机制，预留给未来需要时启用。

服务对象**仅限内部人员**——不针对 C 端用户、广告主自助平台、实时投放决策、端侧/广告主配置维护。

技术栈：FastAPI + Vue 3 + LangGraph/deepagents + OpenAI 兼容协议 LLM 客户端 + AsyncSqlite 持久化。

---

## 1. 业务背景与价值

### 目标用户（仅内部，仅分析场景）

| 角色 | 典型诉求 |
|---|---|
| 营销专员 | 查活动报表、看 CTR / 转化率 / ROI 异动、生成周报与日报、跨周期对比 |
| 运维 | 监控异常诊断、运行状态查询、问题活动定位、链路瓶颈分析（**只读**——不涉及配置/维护/操作） |
| 数据分析师 | 深度广告数据分析：指标钻取、**人群画像**、**行为分析**、跨维度交叉、自定义 SKILL.md |

### 痛点

| 现状 | 问题 |
|---|---|
| 高频查报表走 BI dashboard | 临时性 / 跨数据源 / 异动诊断不灵活 |
| 临时分析靠人写 SQL + Python | 慢、依赖数据团队 |
| 深度分析（人群 / 行为）多平台跳转 | 上下文切换成本高、跨数据源对照难 |
| 异常定位需要多系统跳转查 | 时段对比 + 维度钻取 + 链路诊断分散，定位慢 |

### 我们做什么

- 自然语言入口 → 一次解决"查 + 析"
- LLM 编排多个业务工具，自动给出诊断 + 可视化
- 输出只读分析结果（表格 / 图表 / 诊断结论 / Markdown 报告），不执行任何系统操作

### 不做什么

- ❌ **任何系统写操作**（删除 / 暂停 / 修改预算 / 调整出价等）—— 当前阶段**完全不做**，交由现有运营平台
- ❌ 替代 BI（高频标准报表仍走 dashboard）
- ❌ 自动决策 / 实时投放策略调整
- ❌ 广告主自助平台（外部）、C 端用户产品
- ❌ 端侧 / 广告主配置维护、素材审核、账户开通
- ❌ 端到端归因模型训练
- ❌ 多语言（仅中文场景）

> 💡 HitL 写操作守卫**框架能力已实现**，预留给未来扩展到写操作时启用——
> 详见 [02-features/04-human-in-the-loop.md](./02-features/04-human-in-the-loop.md)。

---

## 2. 架构总览（一张图）

```
┌────────────────────────────────────────────────────────────┐
│           Frontend (Vue 3 + Vite + ECharts)                │
│  登录 → Skill 面板 → 对话流（计划面板 + 图表混排 + HitL）  │
└──────────────────────┬─────────────────────────────────────┘
              POST /api/chat (SSE 流式)
                       ▼
┌────────────────────────────────────────────────────────────┐
│        FastAPI + AgentRunner (api/channel/runner.py)       │
│  ChannelRegistry ── per-session Channel ── SSE / CLI / ... │
└──────────────────────┬─────────────────────────────────────┘
                       ▼
┌────────────────────────────────────────────────────────────┐
│           deepagents.create_deep_agent (Python)            │
│  Tools: send_plan + run_command + task + write_file ...    │
│  Middleware: ToolOutputTrunc + Summarization (兜底)        │
│  SubAgents: metric-analyzer / report-writer / ...（主方案）│
└────────┬──────────────────┬──────────────────┬─────────────┘
         ▼                  ▼                  ▼
   AsyncSqliteSaver   AsyncSqliteStore   SKILL.md 包目录
   (checkpoints)      (KV store)         (业务能力可插拔)
```

源码层级：~3500 行 Python + ~1500 行 Vue/JS。

---

## 3. 核心创新点①：Skill / Tool / Channel 三层概念

```
┌────────────────────────────────────────────────────────────┐
│  Tool  = 框架默认"螺丝刀"（send_plan / task / write_file） │
│  Skill = 部署侧业务能力（query-metrics / export-report）   │
│  Channel = 对外通信渠道（Web SSE / CLI / Slack / ...）     │
└────────────────────────────────────────────────────────────┘
```

| 概念 | 跨部署稳定？ | 是否进 LLM 工具签名 | 谁负责定义 |
|---|---|---|---|
| Tool | ✅ | ✅ | 框架 |
| Skill | ❌（部署侧） | ✅（聚合到 `run_command`） | **部署方** |
| Channel | 运行时 | ❌ | 框架（接入方加新通道） |

**关键收益**：90% 的业务变更只动 Skill 包（部署侧资产），**零 Python 修改**——加业务 = 拷文件，重启即生效。

详见 [docs/02-features/01-skill-tool-channel-model.md](./02-features/01-skill-tool-channel-model.md)。

---

## 4. 核心创新点②：SubAgent 驱动的上下文管理

### 问题

数据分析对话的工具返回常达 50KB+ JSON，多轮累积让主 context 飙升 → token 成本爆炸 / 模型上下文窗口爆掉。

### 主方案：主 Agent 派工给子 Agent（仿 Claude Code Task）

```
用户："找昨天 CTR 异常的活动并写诊断报告"
   ↓
主 Agent → 派工 metric-analyzer 子 Agent
              ↓
         [子 Agent 独立 context, 跑完只回摘要]
              ↓ ToolMessage(content="异常活动: A1 A3 A7")
   ↓ 主 context 干净
主 Agent → 派工 report-writer 子 Agent
              ↓
         [又一次独立 context]
              ↓ ToolMessage(content="<完整报告 markdown>")
```

主 Agent 整个会话只看到**两条 ToolMessage**——50KB 的原始数据进了子 Agent context 又被丢弃。

### 兜底：Summarization（仅在 SubAgent 没接住时触发）

详见 [docs/02-features/07-subagent-driven-context.md](./02-features/07-subagent-driven-context.md)。

---

## 5. 核心创新点③：Channel 抽象 = 多前端复用同一 Agent

```
                ┌─────────────────┐
                │   Same Agent    │
                │   Same Prompt   │
                │   Same Tools    │
                └────────┬────────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
   WebSSEChannel    CLIChannel    SlackChannel(未来)
   (HTTP SSE)       (终端)        (Slack interaction)
```

LLM **感知不到** channel 切换：`send_plan` Tool 内部调 `channel.send_plan()`，不同 channel 实现把同一调用转成不同输出（SSE event / 终端打印 / Slack 消息）。

### 跨连接确认能力用 Protocol 守卫，不用类型继承

```python
@runtime_checkable
class ExternallyConfirmable(Protocol):
    def resolve_confirm(self, approve: bool) -> None: ...

# 守卫能力，不守卫类型——加新 channel 只要实现 resolve_confirm，confirm endpoint 零改动
if isinstance(channel, ExternallyConfirmable):
    channel.resolve_confirm(req.action == "approve")
```

详见 [docs/02-features/02-channel-abstraction.md](./02-features/02-channel-abstraction.md)。

---

## 6. 关键设计决策摘要（精选 6 条 ADR）

| ADR | 决策 | 为什么重要 |
|---|---|---|
| **001** | Skill / Tool / Channel 三分而非合并 | 让"加业务"和"加渠道"不互相污染，业务变更不动框架 |
| **002** | 所有 SKILL.md 共用单一 `run_command` 工具 | 加业务零 Python 修改，多语言 skill 自由（Node / Python / Bash） |
| **003** | 用 `Protocol` 表达跨连接确认能力 | 加新 channel 零改动 confirm endpoint |
| **002** | 状态控制反转——runner 拥有任务状态机 | 修复 LLM 批处理输出 tool_calls 导致 "running 一闪而过" 的 UX bug |
| **004** | Monkey-patch deepagents 的 Summarization 工厂 | 让阈值可配置（langchain duplicate 校验绕过） |
| **014** | SubAgent 是长上下文主方案，Summarization 是兜底 | 结构性隔离 > 事后压缩，关键数字不丢失 |

完整 18 条 ADR 嵌入对应 features 文末，详见 [docs/02-features/](./02-features/)。

---

## 7. 工程能力清单（已实现）

| 能力 | 状态 | 说明 |
|---|---|---|
| 自然语言对话 | ✅ | LLM 流式 token 输出 + Markdown 渲染 |
| 执行计划面板 | ✅ | `send_plan` 默认工具 + plan SSE event |
| 业务工具调用 | ✅ | 单一 `run_command` + SKILL.md 配置目录加载 |
| 用户上传 skill | ✅ | zip 上传 + 安全校验 + 同名覆盖 |
| HitL 三场景 | ✅ 框架就绪，业务未启用 | 危险操作确认 / 用户主动停止 / 执行中追加要求；当前阶段无写操作，预留给未来扩展 |
| 流式 SSE 协议 | ✅ | 7 种 event 类型，跨 chunk 解析 |
| 多用户隔离 | ✅ | thread_id 复合键 + UserSpace 文件隔离 |
| LLM 客户端 | ✅ | OpenAI 兼容协议（base_url 切后端：火山方舟 / DeepSeek / 通义 / OpenAI） |
| 长上下文管理 | ✅ | SubAgent 主方案 + ToolOutputTruncation + Summarization 兜底 |
| 持久化 | ✅ | AsyncSqliteSaver（checkpoint）+ AsyncSqliteStore（store） |
| Channel 抽象 | ✅ | Web SSE / CLI 已实现，Slack/WebSocket 接入零改动 |
| 单元测试 | ✅ | 42 个测试，~11 秒，离线无 LLM 调用 |
| E2E 验证 | ✅ | `scripts/` 下 3 个端到端验证脚本 |
| 性能 profiling | ✅ | `benchmarks/profile_latency.py` |

---

## 8. 已知风险（按等级）

### A. 阻塞生产（必须解决）

| # | 问题 | 缓解 |
|---|---|---|
| A1 | 认证缺失（user_id 透传） | 仅可信内网部署；接 SSO/JWT 才能上线（**用户决策：当前暂不实现**） |
| A2 | 业务 skill 是 mock | 部署侧工作，框架不动 |
| A3 | `interrupt_on` 默认配置可能不匹配实际 SKILL.md | ✅ 已加启动校验（warn 不 fail-fast） |

### B. Stub 待实现（多副本部署阻塞）

| # | 组件 | 当前状态 | 实现工时估计 |
|---|---|---|---|
| B1 | `RedisChannelRegistry` | stub + docstring 设计方案 | 2-3 天 |
| B2 | `MysqlStore` | stub | 1-2 天 |
| B3 | `PostgresSaver` 切换 | langgraph 已支持，改一处即可 | 0.5 天 |

### C. 设计 trade-off（接受但需明确）

- `send_plan` 强制调用增加一轮 LLM round trip（换计划面板可视化）
- SKILL.md 全文进 prompt（多 skill 时 token 成本可见）
- SubAgent 派工增加 LLM round trip（换主 context 干净）

详见 [docs/05-known-issues-and-roadmap/README.md](./05-known-issues-and-roadmap/README.md)。

---

## 9. 路线图

### P0（阻塞生产，~6 周）

```
Week 1-2: P0.1 auth (SSO/JWT 中间件) + P0.3 interrupt 校验 ✅已完成
Week 3-4: P0.2 真实数据源 SKILL.md 包（业务团队配合）
Week 5-6: 观测入门（log + 健康检查）+ deps + CI ✅已完成基础
```

### P1（多副本化，~6 周）

```
Week 7-8:  P1.1 RedisChannelRegistry 实现
Week 9-10: P1.2 PostgresSaver 切换 + P1.3 MysqlStore
Week 11-12: P1.4 Docker + docker-compose + 多副本 E2E 验证
```

### P2（体验优化，按需挑选）

观测能力 / 服务端 cancel / SSE 心跳 / Skill 沙箱 / Skill 热加载 / 子 Agent markdown 配置 ...

详见 [docs/05-known-issues-and-roadmap/roadmap.md](./05-known-issues-and-roadmap/roadmap.md)。

---

## 10. 度量与现状

### 代码

```
后端: ~3500 行 Python
前端: ~1500 行 Vue/JS
配置: ~150 行 YAML
文档: ~8000 行 Markdown
```

### 测试

```
单元测试:    42 个，~11 秒，离线无 LLM 调用
E2E 验证:    3 个脚本（按需手动跑，真打 LLM）
性能:        1 个 profiling 脚本
评测:        ads_eval 题库框架（部署侧自建题）
```

### CI

GitHub Actions workflow 已配置（pytest + frontend build），仓库推到 GitHub 自动启用。

### 文档

```
docs/
├── README.md                            ✅ 总导航
├── 01-overview/  (3 篇)                 ✅ 项目定位 / 架构总览 / 技术选型
├── 02-features/  (12 篇)                ✅ 全部完整 + 18 条 ADR
├── 03-api-reference/  (3 篇)            ✅ REST / SSE / data contracts
├── 04-operations/  (4 篇)               ✅ 部署 / 配置 / 观测 / 测试
├── 05-known-issues-and-roadmap/  (2 篇)  ✅ 问题清单 + 路线图
└── plans/archive/  (3 份)               ✅ 历史 plan 归档
```

---

## 11. 决策请求（What we need）

### 立即决策

- [ ] **生产部署时间窗** —— 决定 P0/P1 推进节奏
- [ ] **认证方案选型** —— SSO（华为内部）vs 自建 JWT vs 其他
- [ ] **数据源接入团队对接人** —— P0.2 真实 SKILL.md 包谁负责
- [ ] **观测平台** —— Prometheus / OpenTelemetry / 华为内部已有平台

### 后续决策

- [ ] 多副本部署目标规模（决定 P1 时间线）
- [ ] Skill 沙箱必要性（决定 P2.7 是否做）
- [ ] 子 Agent 配置方式（继续 yaml 内联 / 切 markdown 文件方案）
- [ ] LLM 厂商选型策略（影响 prompt caching / cost）

### 待评审输出

- [ ] 本概要的呈现方式（PPT / 文档评审 / 技术分享）
- [ ] 对外发布范围（内部 / 集团内部 / 公开）

---

## 12. 附录：核心源码索引

| 关键文件 | 内容 |
|---|---|
| `agent/builder.py` | `build_agent` 主装配 + `interrupt_on` 启动校验 |
| `agent/checkpointer.py` | AsyncSqliteSaver 生命周期 + Summarization monkey-patch |
| `agent/store.py` | AsyncSqliteStore 生命周期 + backend 选择器 |
| `agent/skill_loader.py` | SKILL.md 包加载（含安全模型） |
| `agent/llm.py` | LLM 客户端构造（OpenAI 兼容协议） |
| `api/channel/base.py` | BaseChannel 抽象 + ExternallyConfirmable Protocol |
| `api/channel/default_tools.py` | `make_default_tools(channel)` 工厂（send_plan） |
| `api/channel/runner.py` | AgentRunner 事件流转发 |
| `api/channel/registry.py` | ChannelRegistry ABC + InMemory 实现 + Redis stub |
| `api/chat.py` | REST endpoints + PLAN_INSTRUCTION |
| `frontend/src/utils/sse.js` | 手写 SSE 客户端（fetch + ReadableStream） |

---

## End — 欢迎提问

详细文档导航见 [docs/README.md](./README.md)。

如对某个具体设计点有疑问，可直接跳转对应专题：

- "为什么 send_plan 是默认 Tool 不是 Channel skill？" → [02-features/03-execution-panel.md](./02-features/03-execution-panel.md)
- "为什么 `update_task` 工具被删了？" → [02-features/03-execution-panel.md::ADR-002](./02-features/03-execution-panel.md)
- "如何加新业务能力？" → [02-features/11-system-skills-catalog.md](./02-features/11-system-skills-catalog.md)
- "多副本部署怎么做？" → [04-operations/deployment-modes.md::3](./04-operations/deployment-modes.md)
- "什么时候 Summarization 兜底会触发？" → [02-features/08-context-fallback.md](./02-features/08-context-fallback.md)
