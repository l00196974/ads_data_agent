# 08 上下文兜底机制（Context Fallback）

> 长上下文管理**主方案**是 SubAgent 隔离，详见 [07-subagent-driven-context.md](./07-subagent-driven-context.md)。
> 本篇讲**兜底**——主方案没接住的场景下怎么避免上下文爆炸。

## 一、为什么需要兜底

主方案靠主 Agent **主动派工**给子 Agent。但有三类场景主方案接不住：

1. **LLM 没派工**：主 Agent 判断错误，把脏活留给自己——这时主 context 会涨
2. **多轮累积**：单次调用都不大，但 N 轮交互后 messages 数组越来越长
3. **用户直接调用大工具**：第一轮就调一个返 50KB 的工具，无 SubAgent 介入机会

兜底必须保证：哪怕 LLM 用法不规范、用户提问多轮缠绕，agent 也不会因为 context window
爆炸而崩溃。

---

## 二、Context 组成与可压缩性评估

兜底之前先看 context 装了什么——不知道每部分的成本和压缩空间，调参都是猜的。

每次 LLM 调用 prefill 的 input token = `[1] System Prompt + [2] Messages 历史`，分别拆开看。

### 2.1 System Prompt 部分（每轮 LLM 调用全量送）

| 组成 | 来源 | 典型大小 | 是否常驻 | 压缩手段 | 收益/风险 |
|---|---|---|---|---|---|
| **PLAN_INSTRUCTION** | `api/chat.py` 顶部硬编码 | ~1k tokens | ✅ 常驻 | 精简 prompt 文字（已删 send_plan 那段） | 收益小（占比低）；删过头 LLM 行为不可控 |
| **deepagents `BASE_AGENT_PROMPT`** | deepagents 库内 | ~1k tokens | ✅ 常驻 | 不动（框架自带） | — |
| **SubAgentMiddleware 注入** | `task` 工具描述 + 4 个子 Agent 的 description + system_prompt | ~2-3k tokens（按当前 4 个子 Agent） | ✅ 常驻 | 减少子 Agent 数、缩短 description | 节省 1-2k；但派工准确度可能下降 |
| **`UserSpace.get_agents_md()`** | `data/{user_id}/agents.md`（用户自定义） | 0–2k tokens | ✅ 常驻 | 不压（用户写的） | — |
| **`md_pkg.prompt_addition`** | 每个加载的 SKILL.md 全文 + 子命令清单 | **3-15k tokens**（按已装 skill 线性增长） | ✅ 常驻 | RAG 风格按需检索（未实现） | 单 skill 装的少时不优化；装了 5+ skill 收益显著 |
| **`HumanInTheLoopMiddleware`** | 仅当 `interrupt_on` 非空时注入 | ~200 tokens | 条件常驻 | — | — |

**典型 system prompt 总量**：
- 当前生产配置（4 子 Agent + metric-data-extractor + demo-artifact-writer）≈ **8-12k tokens** 的常驻开销
- 每轮 LLM 调用都得 prefill 这一段——不管对话多短

### 2.2 Messages 历史部分（累积、可压）

| 内容 | 单条大小 | 累积特性 | 压缩手段 | 收益/风险 |
|---|---|---|---|---|
| **HumanMessage** | ~50–500 tokens | 每轮 1 条 | Summarization 整体摘要 | 用户感知"AI 不记得我之前说过 XX" |
| **AIMessage**（含 reasoning + tool_calls） | ~100–800 tokens | 每次 LLM 调用 1 条 | 同上 | 同上 + 丢失工具调用上下文 |
| **ToolMessage**（业务工具返回值） | **0.5–50 KB**（query-metrics 一次返几 KB JSON 是常态） | 大头！每次工具调用 1 条 | 1) ToolOutputTruncation 单条 >5KB 卸盘<br>2) SubAgent 隔离让大数据不进主 context | 卸盘后 LLM 看不到原文；SubAgent 是主方案 |
| **SystemMessage**（summarization 产物） | ~500–2000 tokens | 触发后注入 1 条 | — | summary 本身有 token 成本 |

**典型 messages 增长曲线**（基于 metric-data-extractor 实测）：
- 简单 1 次查询完成 ≈ 5 条 messages，~3-5k tokens
- 多步诊断（issue-diagnostician 触发 5-8 次切片）≈ 20+ 条 messages，~30-50k tokens 进主 context **如果不派子 Agent**
- 派子 Agent 后主 context 只多 1 条 task ToolMessage（≤500 字结论），~600 tokens

### 2.3 一次 LLM 调用的 token 总量估算

| 场景 | system | messages | 合计 input | 备注 |
|---|---|---|---|---|
| 新对话第 1 轮 | ~10k | ~100（user msg） | **~10k** | system 占 99% |
| 1 次工具调用后第 2 轮 | ~10k | ~3-5k | ~13-15k | 加 1 条 ToolMessage |
| 多步诊断第 8 轮（不派子 Agent） | ~10k | ~30-50k | **~40-60k** | 撑爆 32k 模型 |
| 多步诊断第 8 轮（派子 Agent） | ~10k | ~3-5k | ~13-15k | 主 context 干净 |
| 多轮对话第 20 轮（typical） | ~10k | ~40-80k | **~50-90k** | 接近 summarization 阈值 |

**两个临界点**：
- **~32k**：32k context 模型撞墙——必须配置 `summarization_trigger_tokens=20000` 才能撑住多轮
- **~80k**：当前默认 summarization 触发点——80k+ 触发 LLM 摘要早期 messages

### 2.4 可压缩性总结：按优化收益排序

按 "占比 × 可减空间 × 实现成本" 排序，从高 ROI 到低：

1. **SubAgent 派工**（主方案，已实现，详见 [07](./07-subagent-driven-context.md)）—— ROI 最高，把多步活的中间数据全部关进子 Agent 独立 context
2. **ToolOutputTruncation**（已实现）—— 防御单次大返回炸 context；本文档 §4 详解
3. **SummarizationMiddleware**（已实现）—— 多轮累积兜底；本文档 §5 详解
4. **SKILL.md 按需加载**（未实现）—— 当前所有 skill 全文进 prompt。如果未来装了 5+ skill，prompt 可能膨胀到 30k+，这时值得做 RAG 风格检索（用户问题 → 匹配相关 skill 文档段落 → 只把相关段塞进 prompt）。但**当前 2 个 skill 占 ~6k**，做 RAG 检索的实现成本（embedding + 召回 + 排序）比省下来的 token 贵
5. **PLAN_INSTRUCTION 精简**（已部分做）—— send_plan 删掉省了 ~300 tokens；进一步精简空间小

**关键观察**：System prompt 那 ~10k 是**永远省不掉**的固定成本，所以 context 优化的真正战场是 messages 累积。SubAgent 派工是主方案，两个 middleware 是兜底——三者协同把 messages 增长从 O(N) 压成 O(1)。

---

## 三、两层兜底

```
LLM 调用前
   │
   ▼
[1] ToolOutputTruncationMiddleware (我们自己写的)
       超大 ToolMessage.content → 卸盘 + 摘要 + 文件指针
       defense in depth：单条工具返回不会瞬间炸 context
   │
   ▼
[2] SummarizationMiddleware (deepagents 内置，我们 monkey-patch 阈值)
       累计 token > trigger_tokens → LLM summarize 早期 messages
       保留最近 N 条原文，更早的合并为摘要
   │
   ▼
LLM 实际调用
```

第一层先挡掉单次大返回，第二层兜底累积膨胀。

---

## 四、`ToolOutputTruncationMiddleware`

源码：`agent/middleware/tool_output_truncation.py`

### 4.1 触发逻辑

每次 LLM 调用前 scan messages：

```python
# 伪代码
for msg in messages:
    if isinstance(msg, ToolMessage):
        if len(msg.content.encode("utf-8")) > max_bytes:
            # 卸盘：写到 data/{user_id}/tool_outputs/{tool_call_id}.json
            file_path = persist_to_disk(msg.content, tool_call_id)
            # 替换为摘要 + 文件指针
            msg.content = f"<工具返回过大，已保存到 {file_path}>\n{content[:300]}..."
```

源码细节：把替换后的 messages 用 `Overwrite(...)` 写回 state，让后续轮也看到截断版。

### 4.2 配置

```yaml
# config.yaml
agent:
  long_context:
    tool_output_max_bytes: 5000     # 默认 5KB，超此值即截断卸盘
```

### 4.3 user_id 来源

middleware hook 从 langgraph runtime 拿 `thread_id`，按 `{user_id}_{conversation_id}`
拆出 user_id，路径是 `data/{user_id}/tool_outputs/`——天然按用户隔离。

测试：`tests/unit/agent/test_tool_output_truncation.py::test_user_id_extracted_from_thread_id`。

### 4.4 为什么放在 middleware 层而不是 skill 内

skill 是部署侧资产，框架不能假设它们会自我截断。Middleware 在所有工具调用之后统一拦
——任意 skill 返大 JSON 都不会漏。

---

## 五、`SummarizationMiddleware`（deepagents 内置 + 我们的 patch）

### 5.1 deepagents 默认行为

`SummarizationMiddleware` 在 `before_model` hook 累计 messages 总 token 数，超阈值时
触发：

1. 取最早的 N 条 messages（除最近 `keep` 条外）
2. 用一个独立的 LLM 调用 summarize 它们
3. 把这些 messages 替换为单条 summary message
4. checkpointer 持久化新的 messages 数组

deepagents 默认阈值是 `("tokens", 170000)` 或 `("fraction", 0.85)` of model profile。

### 5.2 为什么 monkey-patch 工厂

> **monkey-patch = 运行时替换别人代码里的函数引用——表面 API 不变，内部行为换成你写的版本。**
> Python 里函数/模块属性都是动态可变的，`module.func = my_func` 一行就把 `module.func` 替换成
> `my_func`，之后任何代码（包括 module 自己内部代码）调 `module.func()` 都调到 `my_func`。
> 名字源自 "guerrilla patch"（游击补丁），强调"非正式、临时的打补丁"性质。
> 类比：偷偷给别人家水龙头换内胆——外观（API）一样，但水的口感（行为）不一样，
> 主人家的水管（源码）没动。

deepagents 默认阈值对 32k–128k 上下文模型（GLM-5.1 / ark-code-latest 等）过高——实际
触发时 prefill 已经接近 context 上限了，触发已经太晚。

我们想让阈值可配置（按部署侧的实际模型调小），但**不能**通过 `middleware=[...]` 参数追加自定义实例：langchain 校验 middleware 列表，duplicate 类型会报错（deepagents 默认已经注入了一个 SummarizationMiddleware）。

**解决方案**：monkey-patch deepagents 的工厂函数。

`agent/checkpointer.py:20-39`：

```python
import deepagents.graph as _deepagents_graph
from deepagents.middleware.summarization import SummarizationMiddleware
from agent.config import load_config

def _custom_summarization_factory(model, backend):
    lc = load_config().agent.long_context
    return SummarizationMiddleware(
        model=model,
        backend=backend,
        trigger=("tokens", lc.summarization_trigger_tokens),
        keep=("messages", lc.summarization_keep_messages),
    )

# 模块顶部立即生效——必须在任何 build_agent 之前
_deepagents_graph.create_summarization_middleware = _custom_summarization_factory
```

### 5.3 配置

```yaml
agent:
  long_context:
    summarization_trigger_tokens: 80000     # 默认 80k；32k context 模型建议下调到 ~20000
    summarization_keep_messages: 10          # 压缩后保留最近 N 条原文
```

调参建议：
- `trigger_tokens` 应留余量给 prefill + 当前轮 output（如 128k 模型用 80k 触发，留 48k）
- `keep_messages` < 6 容易丢工具调用上下文（plan / 上一次工具结果）
- `keep_messages` > 30 压缩效果有限

### 5.4 触发的可见性

LangSmith trace 里能看到 summarization 调用——一次独立的 LLM 调用，prompt 包含旧 messages，
completion 是 summary。压缩本身有 token 成本（约 500-2000 tokens of summary 输入 +
小输出）。

---

## 六、与 SubAgent 主方案的协同

| 场景 | 主方案介入？ | 兜底介入？ |
|---|---|---|
| 主 Agent 调子 Agent，子 Agent 内大数据流转 | ✅ 数据不进主 context | 子 Agent 内仍跑 ToolOutputTruncation |
| 主 Agent 自己调大工具（没派工） | ❌ | ✅ ToolOutputTruncation 截断 |
| 多轮累积 messages | ❌ | ✅ Summarization 触发 |
| 用户每轮都派工 | ✅ | 几乎不会触发兜底 |

**理想运行模式**：主 Agent 派工良好 → 兜底极少被触发 → token 成本低。
**最坏情况**：主 Agent 不派工 → 两层兜底全部触发 → token 成本可控但 UX 略差（compress 有 latency）。

---

## 七、当前限制

- **summarization 是有损的**：用户能感知（"啊我之前提过 XX，现在 LLM 不记得了"）。
  缓解：keep_messages 调大；从根本上靠 SubAgent 派工避免触发
- **truncation 卸盘后 LLM 看不到原文**：摘要里只有前 300 字符 + 文件路径，LLM 拿不到
  完整数据。如需完整数据要重调工具
- **跨用户压缩参数无法独立**：`config.yaml::long_context` 是全局配置，不能 per-user。
  扩展点：把配置移到 thread state（langgraph runtime 可读）

---

## 设计决策记录（ADR）

### ADR-004：用 monkey-patch 替换 deepagents 的 SummarizationMiddleware 工厂

**Context**：deepagents 内部默认注入一个 SummarizationMiddleware，阈值不可配置（来自模型 profile）。我们需要按 `config.yaml::long_context` 自定义阈值。

直接通过 `create_deep_agent(middleware=[CustomSummarization])` 追加？langchain 会校验
middleware 列表里没有重复类型，duplicate 会 raise。

**Decision**：在 `agent/checkpointer.py` 模块顶部 monkey-patch
`deepagents.graph.create_summarization_middleware` 工厂函数，让 deepagents 内部装配
时调用我们的工厂——返回的实例使用我们的阈值。

模块级执行 → 任何 `from agent.builder import ...` 必然先 import `agent.checkpointer`
→ patch 在 `create_deep_agent` 调用之前生效。

**Consequences**：
- ✅ 阈值完全可配
- ✅ 不动 deepagents 源码（pip install 后无修改）
- ✅ 测试可以 reset 重新 patch
- ⚠️ deepagents 升级时如果改了工厂函数名 / 签名，patch 会静默失效——必须有测试守住
  （`scripts/validate_summarization.py`）
- ⚠️ "import 顺序敏感"是 monkey-patch 的通病，新 contributor 容易踩坑——靠 `agent/builder.py`
  顶部 import 保证顺序

**Alternatives**：
- Fork deepagents：维护成本高，每次升级要 rebase
- 在 `create_deep_agent` 之后手动替换 middleware list：langchain 不暴露这个 API
- 让 deepagents 上游加配置开关：等不起，且本项目已经依赖了 monkey-patch 模式
