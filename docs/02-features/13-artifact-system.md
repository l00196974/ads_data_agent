# 13 Artifact 系统（持久化大型分析产物）

> **前置**：先读 [01-skill-tool-channel-model.md](./01-skill-tool-channel-model.md)。

## 一、为什么需要

数据分析的真实输出经常超出 SSE 文本流的承载能力——多视角数据、多张图、完整洞察、Office 文件等。Artifact 系统让 skill 把这些大产物**持久化为用户长期资产**，跨 channel 差异化呈现。

详细动机见 [spec](../superpowers/specs/2026-04-30-artifact-system-design.md)。

## 二、核心概念

| 概念 | 含义 |
|---|---|
| **Artifact** | 一份分析产物，物理上 = 一个目录 + 一份 manifest.yaml |
| **Workspace** | per-user 的 artifact 集合（跨 conversation 持久共享） |
| **Manifest** | 强制契约文件，含 artifact 类型 / 标题 / 入口文件 / 元数据 |
| **immutable** | artifact 写完不变（同 id 后写覆盖前写） |

## 三、Skill 怎么写产物

runner 在每次工具调用前注入 env：
- `ADS_AGENT_ARTIFACT_DIR` — 候选 artifact 目录
- `ADS_AGENT_ARTIFACT_ID`  — 候选 id
- `ADS_AGENT_USER_ID`      — 用户标识

skill 自由写文件到 `ADS_AGENT_ARTIFACT_DIR`，写完发约定通知行（**只在 stderr**）：

```
<<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id=<id>>>>
```

参考实现：`skills/demo-artifact-writer/bin/write-demo-report.py`。

## 四、stderr 通知 → stdout sentinel 转换（关键设计）

**实施过程中发现**：原 spec 设计的 ContextVar 机制在 langchain `tool.arun()` 用
`create_task(coro, context=copied_ctx)` 跨 task 边界**不传播**——子 task 对 ContextVar
的写入对父 task（runner）不可见。

**修复**：`agent/skill_loader.py::_make_run_tool` 内的 `run_command` 协程，从 stderr 抽出
artifact_ids 后，用 human-friendly sentinel 行 `[已生成 artifact: <id>]` 拼到 stdout 末尾。
runner 在 `on_tool_end` 时从 `event["data"]["output"]` 抽 ids（用 `extract_artifact_ids_from_output`）推 SSE。

LLM 也会看到这个 sentinel——是 feature 不是 bug，可以在最终 markdown 引用 artifact。

详见 ADR-021。

## 五、Channel 呈现差异

| Channel | 呈现 |
|---|---|
| `WebSSEChannel` | 推 `event: artifact_updated` SSE → 对话流插入 ArtifactCard，点击展开 ArtifactBrowser |
| `CLIChannel`    | 打印 `📦 Artifact created: <id>` |
| 未来 Mobile     | zip 下载 / 服务端 PDF 转换 |

## 六、独立工作区页面

`/artifacts` 路由（顶栏入口"📦 我的工作区"）—— 独立访问 artifact 列表，列表 + tile + 删除。

## 七、Manifest schema

详见 [03-api-reference/data-contracts.md](../03-api-reference/data-contracts.md)。

## 八、关联源码

- 模型：`agent/artifact.py`（Artifact dataclass + create / load / list_summaries / manifest / tree / delete / safe_file_path）
- REST API：`api/artifacts.py`（list / manifest / file / zip / delete 5 个 endpoint）
- skill_loader 集成：`agent/skill_loader.py::_extract_artifact_ids` / `extract_artifact_ids_from_output`
- runner 集成：`api/channel/runner.py`（on_tool_start 注入 env / on_tool_end 抽 ids 推 SSE）
- channel 接口：`api/channel/base.py::send_artifact_updated`、WebSSE / CLI 实现
- 前端：`frontend/src/components/ArtifactCard.vue` + `ArtifactBrowser.vue` + `preview/*.vue`（6 个）+ `pages/Artifacts.vue`

## 九、安全

- `artifact_id` 必须匹配 `^\d{4}-\d{2}-\d{2}-\d{6}-[a-z0-9][a-z0-9-]*$`，否则 400
- `user_id` 含 `/` 或 `\\` 或 `..` 拒绝（Windows 反斜杠路径绕过修复）
- 文件路径用 `safe_file_path` 双层防护：字符串模式 + `is_relative_to()`

## 设计决策记录（ADR）

### ADR-019：Artifact 内容**不进 LLM context**

**Context**：让 LLM 直接看 artifact 内容会导致 token 飙升 + checkpoint 膨胀。

**Decision**：artifact 内容只存文件系统；LLM 只看到工具返回的简短摘要 + sentinel 行。LLM 在最终 markdown 里**引用** artifact 名而不是内容。

**Consequences**：
- ✅ token 省、checkpoint 干净
- ✅ sentinel 行（"[已生成 artifact: ...]"）反而让 LLM 自然提到 artifact，对 UX 友好
- ⚠️ LLM 不能"读完整报告再总结"——靠 skill 写的 INDEX.md 给用户导览

### ADR-020：manifest 是契约，目录结构自由

**Context**：固定子目录只适合"分析报告"形态，无法承载 PPT/Excel/Word 等单文件交付。

**Decision**：仅 `manifest.yaml` 强制；artifact_type + entry_files 让前端选合适的渲染策略。

**Consequences**：
- ✅ 灵活——综合报告、PPT、Excel、Markdown 单文档都能装
- ⚠️ skill 作者要正确写 manifest（缺 manifest 的目录被 list_summaries 静默跳过）

### ADR-021：用 stdout sentinel 而非 ContextVar 传 artifact_ids

**Context**：原设计用 ContextVar 让 skill 通知 runner 它产生了 artifact。但 langchain
`tool.arun()` 内部用 `set_config_context() + asyncio.create_task(coro, context=copied_ctx)`
跑 tool coroutine——这意味着子 task 对 ContextVar 的写入**不传播**回父 task（runner）。
实测：`await tool.arun(...)` 后 `cv.get()` 永远返回 default 值。

**Decision**：在 `run_command` 内部，从 stderr 抽出 artifact_ids 后，用 human-friendly
sentinel 行 `[已生成 artifact: <id>]` 拼到 stdout 末尾。Runner 在 `on_tool_end` 时从
`event["data"]["output"]` 抽 ids（这是工具返回值，langgraph 始终能拿到）。

**Consequences**：
- ✅ 跨 langchain task 边界稳定工作（output 是工具返回值，必然回到 runner）
- ✅ Sentinel 用人友好格式——LLM 看到也能自然引用 artifact 名（feature 不是 bug）
- ✅ 完全消除 ContextVar 隔离风险
- ⚠️ Skill 作者写 stderr 通知时必须用严格格式（`<<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id=...>>>`）—— 我们在 SKILL.md 模板说明
- ⚠️ 大量 artifact 时 sentinel 会让 stdout 变长——多 artifact 不常见，影响小
