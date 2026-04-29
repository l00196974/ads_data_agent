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

## 二、两层兜底

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

## 三、`ToolOutputTruncationMiddleware`

源码：`agent/middleware/tool_output_truncation.py`

### 3.1 触发逻辑

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

### 3.2 配置

```yaml
# config.yaml
agent:
  long_context:
    tool_output_max_bytes: 5000     # 默认 5KB，超此值即截断卸盘
```

### 3.3 user_id 来源

middleware hook 从 langgraph runtime 拿 `thread_id`，按 `{user_id}_{conversation_id}`
拆出 user_id，路径是 `data/{user_id}/tool_outputs/`——天然按用户隔离。

测试：`tests/unit/agent/test_tool_output_truncation.py::test_user_id_extracted_from_thread_id`。

### 3.4 为什么放在 middleware 层而不是 skill 内

skill 是部署侧资产，框架不能假设它们会自我截断。Middleware 在所有工具调用之后统一拦
——任意 skill 返大 JSON 都不会漏。

---

## 四、`SummarizationMiddleware`（deepagents 内置 + 我们的 patch）

### 4.1 deepagents 默认行为

`SummarizationMiddleware` 在 `before_model` hook 累计 messages 总 token 数，超阈值时
触发：

1. 取最早的 N 条 messages（除最近 `keep` 条外）
2. 用一个独立的 LLM 调用 summarize 它们
3. 把这些 messages 替换为单条 summary message
4. checkpointer 持久化新的 messages 数组

deepagents 默认阈值是 `("tokens", 170000)` 或 `("fraction", 0.85)` of model profile。

### 4.2 为什么 monkey-patch 工厂

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

### 4.3 配置

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

### 4.4 触发的可见性

LangSmith trace 里能看到 summarization 调用——一次独立的 LLM 调用，prompt 包含旧 messages，
completion 是 summary。压缩本身有 token 成本（约 500-2000 tokens of summary 输入 +
小输出）。

---

## 五、与 SubAgent 主方案的协同

| 场景 | 主方案介入？ | 兜底介入？ |
|---|---|---|
| 主 Agent 调子 Agent，子 Agent 内大数据流转 | ✅ 数据不进主 context | 子 Agent 内仍跑 ToolOutputTruncation |
| 主 Agent 自己调大工具（没派工） | ❌ | ✅ ToolOutputTruncation 截断 |
| 多轮累积 messages | ❌ | ✅ Summarization 触发 |
| 用户每轮都派工 | ✅ | 几乎不会触发兜底 |

**理想运行模式**：主 Agent 派工良好 → 兜底极少被触发 → token 成本低。
**最坏情况**：主 Agent 不派工 → 两层兜底全部触发 → token 成本可控但 UX 略差（compress 有 latency）。

---

## 六、当前限制

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
