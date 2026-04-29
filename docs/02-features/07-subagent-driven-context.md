# 07 SubAgent 驱动的上下文隔离

> 这是项目长上下文管理的**主方案**。
> 兜底机制（截断 / 摘要压缩）见 [08-context-fallback.md](./08-context-fallback.md)。

## 一、要解决的问题

数据分析对话天然产生海量上下文：

- 一次"昨日全平台广告报表"工具返回可能 50KB+ JSON
- 多轮交互后，主对话的 messages 数组累积到几十轮，每轮都带工具调用 + 工具返回
- 单 thread token 数线性飙升 → prefill 慢、token 成本高、上下文窗口爆掉

**朴素的"摘要压缩"不够好**——压缩本质是有损操作，摘要无法保留"用户原话 + 中间结果 + 工具
返回的精确数字"等关键信息。等到压缩触发时，可能已经丢了用户三轮前提到过但临时没用上的
约束（"我们只看华南区域"）。

---

## 二、设计意图：让主 Agent 像项目经理一样工作

灵感来自 Claude Code 的 `Task` 工具——主 Agent **派子 Agent 干脏活**：

```
主 Agent context:
   用户: 帮我分析昨天的活动数据，找出 CTR 异常的活动并生成报告
   
   主 Agent 思考: 我应该把"找异常"和"写报告"分别派给两个子 Agent
   
   主 Agent 调 task(subagent_type="anomaly-detector",
                   description="分析昨天活动 CTR，找 3-σ 外活动")
        │
        │  ┌─────────────────────────────────────────┐
        │  │ 子 Agent 独立 context 启动              │
        │  │   ├─ run_command(query-metrics ...)     │ ← 50KB JSON 进子 context
        │  │   ├─ run_command(query-baselines ...)   │
        │  │   ├─ ...分析逻辑...                     │
        │  │   └─ 输出: "异常活动: A1, A3, A7（CTR 偏低 30%+）"
        │  └─────────────────────────────────────────┘
        │
        ▼ 主 Agent 收到的 ToolMessage 仅 80 字符
   ToolMessage(content="异常活动: A1, A3, A7（CTR 偏低 30%+）")
   
   主 Agent 调 task(subagent_type="report-writer",
                   description="基于异常活动 A1/A3/A7 写诊断报告")
        │
        │  ┌─────────────────────────────────────────┐
        │  │ 另一个子 Agent 独立 context             │
        │  │   ├─ run_command(query-metrics A1 ...)  │
        │  │   ├─ run_command(query-metrics A3 ...)  │
        │  │   ├─ ...写报告...                       │
        │  │   └─ 输出: 完整 markdown 报告
        │  └─────────────────────────────────────────┘
        │
        ▼
   ToolMessage(content="<完整报告 markdown>")
```

主 Agent 整个会话只看到**两条 ToolMessage**——大量原始数据进了子 Agent 的 context 又被
丢弃，主 context 保持干净。

---

## 三、为什么这是主方案而不是兜底

| 维度 | SubAgent 隔离 | Summarization 压缩 |
|---|---|---|
| 触发时机 | 主 Agent **主动**派工 | 累积 token 超阈值**被动**触发 |
| 信息保真 | 子 Agent 内全保留，只摘要返回 | 主 Agent 历史被有损压缩 |
| 失败恢复 | 子 Agent 失败可重派 | 压缩误丢信息无法恢复 |
| 用户控制 | 通过 prompt 引导主 Agent 派工策略 | 完全无法干预 |
| 成本 | 多发起一次 LLM 调用 | 一次额外 summarize 调用 |

SubAgent 是**结构性**解决方案——通过架构让脏数据不进主 context；
Summarization 是**事后**补救——脏数据已经在主 context 里了再做有损压缩。

但 SubAgent **不能取代**兜底：
- 当主 Agent **不**派工（工具返回直接落主 context），还是会涨上去
- 当用户多轮追问累积，每轮都不大但叠加变长

所以两者并存：SubAgent 是日常工作模式；Summarization 是兜底护栏。

---

## 四、装配机制

### 4.1 deepagents 的内置支持

`deepagents.create_deep_agent` 接受 `subagents` 参数，类型 `Sequence[SubAgent]`。
deepagents 内部会注入一个 `task` 工具到主 Agent 工具集里——LLM 看到的是 `task(subagent_type, description)` 这种调用形式。

`SubAgent` 是 TypedDict（在 `deepagents.middleware.subagents`）：

```python
class SubAgent(TypedDict):
    name: str                                # 唯一 id，task() 第一参数
    description: str                         # 主 Agent 据此判断何时派工
    system_prompt: str                       # 子 Agent 自己的角色 prompt
    tools: NotRequired[Sequence]             # 默认继承主 Agent 工具集
    model: NotRequired[str | BaseChatModel]  # 默认继承主 Agent 模型
    middleware: NotRequired[list]
    interrupt_on: NotRequired[dict]
    skills: NotRequired[list[str]]
```

### 4.2 项目侧透传

`agent/config.py::SubAgentSpec`（Pydantic）：

```python
class SubAgentSpec(BaseModel):
    name: str
    description: str
    system_prompt: str
    model: str | None = None
```

只暴露最常用四个字段——其它（tools/middleware/interrupt_on/skills）默认继承主 Agent。
需要时再扩 schema。

`agent/builder.py:42-48`：

```python
subagents = [
    {k: v for k, v in s.model_dump().items() if v is not None}
    for s in cfg.agent.subagents
]
agent = create_deep_agent(
    ...,
    subagents=subagents if subagents else None,
    ...
)
```

注意：`{...if v is not None}` 必须剔除 None 值，否则 deepagents 收到 `model=None` 会尝试加载 `'None:None'` 模型导致报错（已加测试守住，见 `tests/unit/agent/test_core.py::test_build_agent_passes_subagents_when_configured`）。

### 4.3 默认空列表 = 关闭 task 工具

`config.yaml::agent.subagents` 默认是空列表 `[]`——空列表时 `build_agent` 传 `None`，
deepagents 不注入 task 工具，主 Agent 看不到这个能力。

→ **要启用 SubAgent，部署侧必须显式声明子 Agent 列表**。框架本身不假设你需要它。

---

## 五、配置示例

```yaml
# config.yaml
agent:
  subagents:
    - name: metric-analyzer
      description: 深度分析指标数据，独立上下文，返回 ≤300 字结论摘要
      system_prompt: |
        你是指标分析专家。仅分析输入数据，不做任何 IO；输出结论 ≤300 字，
        直奔结论不解释过程。

    - name: report-writer
      description: 整合多份分析结论生成最终结构化 Markdown 报表
      system_prompt: |
        你是报表撰写专家。读输入的多份分析结论，输出 Markdown 报表，
        包含：执行摘要 / 关键指标 / 异常诊断 / 建议措施 四个段落。
      model: openai:gpt-4o    # 报表偏长，可选用更大模型
```

LLM 看到的对应 task 工具描述（deepagents 自动生成）：

```
task(subagent_type, description) — 把任务委派给子 Agent。

可用子 Agent:
  - metric-analyzer: 深度分析指标数据，独立上下文，返回 ≤300 字结论摘要
  - report-writer:   整合多份分析结论生成最终结构化 Markdown 报表
```

---

## 六、使用建议（给 system prompt 编写者）

`prompts/system_agent.md` 需要明确告诉主 Agent **何时派工 vs 自己干**。建议规则：

- **派工**：单步逻辑超过 3 个工具调用、需要多次工具数据互相对照、产出长文本（如报告）
- **自己干**：单工具就能拿到答案、纯总结类追问、用户直接 ack 的简单问题

不写这层引导，LLM 会要么：
- 全自己干 → context 涨爆
- 全派工 → 简单问题也多发一轮 LLM 调用，慢又贵

---

## 七、与其它机制的协同

| 机制 | 关系 |
|---|---|
| **Tool Output Truncation** | 子 Agent 内部仍跑这个 middleware——超大工具返回卸盘，子 Agent 自己也减负 |
| **Summarization** | 主 Agent 长跑后兜底；子 Agent 一次性的，通常用不上 summarization |
| **Checkpointer** | 子 Agent 的 state 也走 thread_id（同 conversation_id 内分隔），可恢复 |
| **HitL** | 子 Agent 也可以触发 interrupt——通过 `subagent_spec.interrupt_on` 单独配置 |

---

## 八、当前限制与待办

- ⚠️ 配置侧只暴露 4 个字段（name/description/system_prompt/model），未支持 tools / interrupt_on / skills 的 yaml 表达——需要时再扩 schema
- ⚠️ 当前 `prompts/system_agent.md` 中**没有**针对 task 工具的使用引导——启用 subagents 时建议先补 prompt
- ⏳ "Agent markdown 配置"（仿 Claude Code `agents/*.md` + frontmatter）未实现，要在多 yaml 内联 prompt 时再做（见 [roadmap.md](../05-known-issues-and-roadmap/roadmap.md)）

---

## 设计决策记录（ADR）

### ADR-014：SubAgent 是长上下文的主方案，Summarization 是兜底

**Context**：长上下文有两类典型解法：
- **结构化隔离**：通过架构让数据"流过"主 Agent 而不"沉淀"在主 Agent
- **事后压缩**：让数据沉淀，超阈值时用 LLM 总结历史

deepagents 同时支持两者。本项目要选一个作为"主路径"——决定哪些场景默认走哪种机制。

**Decision**：SubAgent 隔离作为主方案，Summarization 作为兜底。理由：

1. **数据分析场景的工具返回特别大**——单次查询往往返 50KB+ 原始 JSON，全压进主 context
   一次就够触发 summarization；不如在子 Agent 里就消化掉
2. **Summarization 有损**——业务关键数字（如某指标的精确值）可能在压缩时丢失，
   后续追问就拿不回来
3. **SubAgent 模式可被 prompt 引导**——什么任务派工、什么自己干，由 system prompt 控制；
   而 summarization 触发完全自动，无干预空间
4. **失败可恢复性**——子 Agent 调用失败可重派；摘要错丢了原文是不可逆的

**Consequences**：
- ✅ 主 Agent context 稳定
- ✅ 关键数字保真
- ⚠️ 部署侧必须配置子 Agent 列表 + 编写 prompt 引导（增加上手摩擦）
- ⚠️ task 工具的 latency 是一次完整 LLM round trip（子 Agent 启动 + 自己跑完）
  ——比直接调工具慢，简单任务派工就是浪费

**Alternatives**：
- 只用 Summarization：简单但失真严重，被否
- 不接长上下文管理（让用户手动新对话）：UX 太差
- 自研 context-window manager（基于检索）：复杂度爆炸，无人力做

---

### ADR-015：`SubAgentSpec` 仅暴露 4 个字段，复杂配置不进 yaml

**Context**：deepagents `SubAgent` TypedDict 有 9 个字段（含 tools/middleware/permissions/skills 等），
全部映射到 yaml 会让配置文件膨胀且容易写错（特别是 middleware / permissions 是 Python 对象，yaml 里表达不了）。

**Decision**：只暴露 `name / description / system_prompt / model` 4 字段；
其它字段默认继承主 Agent，需要时再扩 schema 或走 markdown 文件方案（见 roadmap）。

**Consequences**：
- ✅ yaml 配置干净，新手能照着模板写
- ✅ 主 Agent 的 tools / middleware 改动自动应用到所有子 Agent（一致性）
- ⚠️ 需要"子 Agent 用更小工具集"的高级场景目前要改 Python 代码

**Alternatives**：
- 完全 yaml 化（含 middleware）：技术上不可行
- 完全 Python 化（不进 yaml）：部署侧改 skill 也要改 Python，违背"配置/代码分离"

为复杂场景留的口子：未来加 `agents/*.md` + frontmatter 文件加载（见
[roadmap.md::P2](../05-known-issues-and-roadmap/roadmap.md)）。
