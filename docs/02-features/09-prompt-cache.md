# Prompt Cache 友好的 system_prompt 拼装

> 关联 commit：[`333f800`](https://github.com/l00196974/ads_data_agent/commit/333f800)
> 关联文件：`api/chat.py:170-180`（`_make_build_fn` 内 system_prompt 拼装）

## 背景

OpenAI 兼容 LLM 网关（DeepSeek 官方 / Qwen / 火山方舟 / SiliconFlow 等）多数支持
**自动前缀匹配 prompt cache**——如果连续两次请求的 prompt 前缀字节流完全相同，
后请求按 cache 价格付费（通常打 1-3 折），且 prefill 延迟下降。

cache 命中的关键约束：**字节级前缀完全一致**。prompt 第一字节变就整段失效。

## 当前拼装顺序

`api/chat.py::_make_build_fn` 把 system_prompt 拼成下面这个顺序（最稳定 → 最易变）：

```python
parts = [
    PLAN_INSTRUCTION,         # 1. 执行流程（几乎从不变）
    us.get_agents_md(),        # 2. system_agent.md 项目级 prompt + 用户 agents.md
]
if md_pkg.prompt_addition:
    parts.append(md_pkg.prompt_addition)  # 3. SKILL.md 业务工具描述
parts.append(_today_context())             # 4. 当前日期（每天变，固定放最末尾）
system_prompt = "\n\n".join(parts)
```

| 段 | 大小（tokens） | 何时变化 | cache 影响 |
|---|---|---|---|
| 1. PLAN_INSTRUCTION | ~1500 | 改 prompt 工程时 | 改一次跨所有用户失效 |
| 2. agents.md (system + user) | ~2000 | 用户改自己的 agents.md 时 | 仅该用户失效 |
| 3. SKILL.md prompt addition | ~7500 | skill 列表 / SKILL.md 内容变更 | 改一次跨所有用户失效 |
| 4. 当前日期 | ~150 | **每天 0 点变** | 跨天首次 prefill 时只末尾 150 tokens |

跨天前缀稳定性：~11K / 11.15K ≈ **98.6% 字节流不变**——理论上前 11K 应命中 cache。

## 历史问题（已修）

修复前 `api/chat.py:172`：

```python
system_prompt = _today_context() + "\n\n" + PLAN_INSTRUCTION + "\n\n" + us.get_agents_md()
              ^^^^^^^^^^^^^^^^^
              当前日期放最前 → 第一字节每天变 → 整段 cache miss
```

效果：每天首次会话 system prompt 11K+ tokens 全价 prefill；改完后仅末尾 150 tokens
不命中。

## 当前 endpoint 实测

测试场景：两轮相同问题（`{"message": "你好"}`）连续发送，观察第二轮 `cache_read_tokens`。

**测试 endpoint**：火山方舟 `https://ark.cn-beijing.volces.com/api/coding/v3` + `minimax-latest`（CodePlan 模式）

| 指标 | Round 1 | Round 2 |
|---|---|---|
| input_tokens | 13338 | 13338 |
| **cache_read_tokens** | **0** | **0** |
| TTFT (ms) | 7703 | 5990 |

**结论**：火山方舟 CodePlan 模式**确认不支持 prompt cache**——查阅官方文档无相关
开关 / 参数。`api/coding/v3` 端点专为 coding 场景优化但不暴露 cache 控制，整轮
prompt 全价 prefill。

改动本身**结构性正确**，等切到支持 cache 的端点会立刻生效——不需要回滚。

## 切换到支持 cache 端点的路径

### 选项 A：DeepSeek 官方（自动前缀缓存）

```bash
# .env
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

DeepSeek 官方 `chat.completions` API 自动启用 prompt cache，命中部分按 1 折计费，
返回字段名为 `prompt_cache_hit_tokens`。**注意**：当前 runner 解析的字段名是
`cache_read_tokens`（langchain `usage_metadata.input_token_details.cache_read`），
切 DeepSeek 时要确认 langchain 是否把 `prompt_cache_hit_tokens` 映射到这个字段。

### 选项 B：阿里通义千问（DashScope）

```bash
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-max
```

通义部分模型支持 context cache，需要传 `enable_context_cache=true` 参数（透传到
ChatOpenAI 的 `extra_body`）。

### 选项 C：火山方舟其它端点

CodePlan 模式（`/api/coding/v3`）确认不支持 cache。火山方舟 `/api/v3` 通用端点 +
`doubao-*` 系列模型可能支持，需要查官方文档具体到模型。

## 验证步骤

切端点后跑这个最小验证：

```bash
# 启动 backend
.venv/Scripts/python.exe -m uvicorn main:app --port 8003

# 等 warmup ready 后连发两个相同问题
curl -sN -m 90 -X POST http://127.0.0.1:8003/api/chat/cache_round1 \
  -H "Content-Type: application/json" \
  -d '{"message":"你好"}' 2>&1 | grep input_tokens

sleep 3

curl -sN -m 90 -X POST http://127.0.0.1:8003/api/chat/cache_round2 \
  -H "Content-Type: application/json" \
  -d '{"message":"你好"}' 2>&1 | grep input_tokens
```

**期望**：Round 2 的 `cache_read_tokens` 应该 > 10000（system_prompt 主体命中）。
如果仍是 0，去 langchain 文档查目标 LLM provider 的 cache 字段名映射。

## 下一步优化（更激进，未实施）

如果切端点后仍有部分 cache miss，下一步可考虑（参考 `D:/workplace/ppt/claude-code-insights-draft.md` 4.3 节）：

把 `_today_context()` 完全从 system_prompt 移除，改成**每轮 LLM 调用前由
middleware 注入到 messages 头部**作为 user role `<system-reminder>`：

```python
# 伪代码：DateReminderMiddleware
class DateReminderMiddleware(AgentMiddleware):
    REMINDER_MARKER = "[DATE-REMINDER]"

    def before_model(self, state, runtime):
        today = build_today_reminder()
        messages = state.get("messages", [])
        # 删除老 reminder
        new_messages = [m for m in messages if not _has_marker(m)]
        # 头部插入新 reminder
        new_messages.insert(0, HumanMessage(content=today))
        return {"messages": Overwrite(new_messages)}
```

收益：system_prompt 100% 跨天稳定；日期作为 user message 跨天变化只影响**那条
message** 的 token，system prompt 整段 cache 永远命中。

代价：langgraph messages 列表里多一条"系统提示"消息，可能影响某些 middleware
对 messages 数量的判断（如 IterationGuardMiddleware 数 ToolMessage 不受影响）。

**当前不实施**：等观察到选项 A/B/C 切换后仍有 cache miss 才考虑。
