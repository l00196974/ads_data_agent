# 文档导航

华为广告 Data Agent 技术架构设计文档。本目录是项目的稳定文档体系，按"What（功能专题）"
与"Why（决策记录）"分离的原则组织。CLAUDE.md / README.md 是给 IDE-Agent 与初次接入者
看的入口，本目录给评审、对接方、贡献者看。

> **想要 12 屏概要？** → [`EXECUTIVE-SUMMARY.md`](./EXECUTIVE-SUMMARY.md) — 评审 / 对外宣讲用的高密度版本（项目定位 / 架构亮点 / 6 条核心 ADR / 已知风险 / 路线图 / 待决策事项）。

## 阅读路径建议

**第一次接触项目** → `01-overview/` 三篇按顺序读完即可建立全局印象（10 分钟）。

**关心某个具体能力** → 直接看 `02-features/` 下对应专题。**入口必须是 `01-skill-tool-channel-model`**——
它定义了贯穿全文档的术语 Skill / Tool / Channel，跳过这篇看其它会迷路。

**接入 API / 写 client** → `03-api-reference/` 看 REST 与 SSE 协议契约。

**部署 / 运维** → `04-operations/` 覆盖部署模式、配置项、观测、测试策略。

**评估风险 / 看后续路线** → `05-known-issues-and-roadmap/`。

---

## 目录索引

### 01 总览（Overview）

| 文档 | 内容 |
|---|---|
| [01-project-positioning](01-overview/01-project-positioning.md) | 产品定位 / 业务场景 / 目标用户 |
| [02-architecture-at-a-glance](01-overview/02-architecture-at-a-glance.md) | 架构大图 + 关键术语 |
| [03-tech-stack-and-rationale](01-overview/03-tech-stack-and-rationale.md) | 技术选型表 + 选型理由 |

### 02 功能专题（Features）

每篇文末附 ADR（Architecture Decision Record）记录关键设计决策的 *Context / Decision /
Consequences / Alternatives*。

| 文档 | 主题 |
|---|---|
| [01-skill-tool-channel-model](02-features/01-skill-tool-channel-model.md) | ★ Skill / Tool / Channel 三层概念边界（**先读这篇**） |
| [02-channel-abstraction](02-features/02-channel-abstraction.md) | Channel 通道（Web SSE / CLI 复用同 Agent） |
| [03-execution-panel](02-features/03-execution-panel.md) | 执行计划面板（send_plan + plan SSE event） |
| [04-human-in-the-loop](02-features/04-human-in-the-loop.md) | HitL 三场景 + ExternallyConfirmable Protocol |
| [05-streaming-sse-protocol](02-features/05-streaming-sse-protocol.md) | SSE 事件协议 |
| [06-multi-user-isolation](02-features/06-multi-user-isolation.md) | thread_id + UserSpace 多用户隔离 |
| [07-subagent-driven-context](02-features/07-subagent-driven-context.md) | ★ SubAgent 主→子任务委派（长上下文主方案） |
| [08-context-fallback](02-features/08-context-fallback.md) | ToolOutputTruncation + Summarization 兜底 |
| [10-persistence-and-checkpoints](02-features/10-persistence-and-checkpoints.md) | AsyncSqliteSaver + AsyncSqliteStore |
| [11-system-skills-catalog](02-features/11-system-skills-catalog.md) | 系统内置业务 skill（SKILL.md 加载） |
| [12-user-skill-upload](02-features/12-user-skill-upload.md) | 用户自定义 skill 上传 |
| [13-artifact-system](02-features/13-artifact-system.md) | Artifact 系统（持久化大型分析产物） |

### 03 接口参考（API Reference）

| 文档 | 内容 |
|---|---|
| [rest-endpoints](03-api-reference/rest-endpoints.md) | REST endpoints 全表 |
| [sse-event-types](03-api-reference/sse-event-types.md) | SSE event 类型完整表 |
| [data-contracts](03-api-reference/data-contracts.md) | Pydantic schemas + 业务工具 I/O |

### 04 运维（Operations）

| 文档 | 内容 |
|---|---|
| [deployment-modes](04-operations/deployment-modes.md) | 单端口一体化 / 前后端分离 |
| [configuration-reference](04-operations/configuration-reference.md) | config.yaml 与 .env 全字段 |
| [observability](04-operations/observability.md) | 日志、LangSmith trace、SSE 调试 |
| [testing-strategy](04-operations/testing-strategy.md) | tests/ vs scripts/ vs benchmarks/ 分工 |

### 05 已知问题与路线图（Known Issues & Roadmap）

| 文档 | 内容 |
|---|---|
| [README](05-known-issues-and-roadmap/README.md) | 已知问题清单 + 优先级 + workaround |
| [roadmap](05-known-issues-and-roadmap/roadmap.md) | 后续路线 |

### 历史归档（plans/archive/）

2026-04-21 ~ 04-22 的早期设计 plan 归档于 `plans/archive/`，仅作时间线证据保留，
**与当前实现已不一致**，不要按它们对照代码。
