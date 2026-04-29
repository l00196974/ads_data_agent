# 09 LLM 双协议路由（LLM Dual-Protocol Routing）

`agent/llm.py::_build_model` 把 LLM 配置统一映射成 `BaseChatModel` 实例。本项目支持两条
路径：**OpenAI 兼容协议**（覆盖国内主流厂商）+ **langchain 原生 provider**（Anthropic / Google 等）。

## 一、为什么有两条路径

| 维度 | OpenAI 兼容路径 | langchain 原生路径 |
|---|---|---|
| 触发条件 | `base_url` 非空 OR `provider == "openai"` | 其它 provider |
| 实现 | `ChatOpenAI(api_key, base_url, model)` | `langchain.chat_models.init_chat_model("provider:model", api_key=...)` |
| 覆盖厂商 | OpenAI / DeepSeek / 通义千问 / 火山方舟 / 任意 OpenAI 兼容代理 | Anthropic / Google / Bedrock / Cohere ... |

国内厂商基本都暴露 OpenAI 兼容 endpoint，所以 OpenAI 协议路径覆盖 80% 的部署场景。
Anthropic 直连 + Google 走 langchain 原生路径——它们各有自己的 API 协议。

---

## 二、路由代码

`agent/llm.py:23-42`：

```python
def _build_model(cfg: AppConfig) -> BaseChatModel:
    llm = cfg.llm

    if llm.base_url or llm.provider == "openai":
        from langchain_openai import ChatOpenAI
        kwargs = {
            "model": llm.model,
            "api_key": llm.api_key or "sk-placeholder",
        }
        if llm.base_url:
            kwargs["base_url"] = llm.base_url
        return ChatOpenAI(**kwargs)

    from langchain.chat_models import init_chat_model
    return init_chat_model(
        model=llm.model,
        model_provider=llm.provider,
        api_key=llm.api_key or None,
    )
```

两条路径都返回 `BaseChatModel` 实例——上层 `build_agent` 不区分 LLM 类型，
直接传给 `create_deep_agent(model=...)`。

---

## 三、为什么显式传 `api_key`

旧实现里 anthropic 路径返字符串 `"anthropic:claude-sonnet-4-6"`，让 deepagents 内部
负责构造模型实例。问题：

- deepagents 不知道项目的 `cfg.llm.api_key`，**静默回退**到 `os.environ["ANTHROPIC_API_KEY"]`
- 配置读取时如果忘了把 yaml 的 api_key 写进 env，整个对话用错的 key 跑下去都不会报错
- bug 隐蔽且难发现（commit `1228c79` 修复）

现在统一返实例 + 显式传 `api_key`——**配置读到啥就用啥，不依赖任何 env fallback**。

测试：`tests/unit/agent/test_core.py::test_build_model_anthropic_returns_chatanthropic_instance`。

---

## 四、配置示例

### 4.1 OpenAI 协议厂商

`.env`：

```bash
LLM_API_KEY=<your-key>
LLM_BASE_URL=https://ark.cn-beijing.volces.com/api/coding/v3
LLM_PROVIDER=openai
LLM_MODEL=ark-code-latest
```

→ 路由到 `ChatOpenAI(model="ark-code-latest", api_key=..., base_url=...)`

### 4.2 OpenAI 自家

```bash
LLM_API_KEY=sk-...
LLM_BASE_URL=                         # 留空
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
```

→ ChatOpenAI 用默认 base_url（`https://api.openai.com/v1`）

### 4.3 Anthropic 直连

```bash
ANTHROPIC_API_KEY=sk-ant-...
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-4-6
```

→ 路由到 `init_chat_model("anthropic:claude-sonnet-4-6", api_key=...)`

### 4.4 配置加载逻辑

`agent/config.py:84-89`：

```python
raw["llm"] = {
    "provider": os.getenv("LLM_PROVIDER", "anthropic"),
    "model": os.getenv("LLM_MODEL", "claude-sonnet-4-6"),
    "api_key": os.getenv("LLM_API_KEY", os.getenv("ANTHROPIC_API_KEY", "")),
    "base_url": os.getenv("LLM_BASE_URL", ""),
}
```

`LLM_API_KEY` 优先级**高于** `ANTHROPIC_API_KEY`——这样部署 Anthropic 时旧 env 名仍兼容，
而通用路径只需 `LLM_API_KEY`。

---

## 五、厂商兼容性差异（暂不深入）

> 本项目暂不深入对比 prompt caching / tool calling / structured output 等厂商差异。
> 后续切其它供应商时再补。

已知差异（仅作记录，**未在本项目深度处理**）：

- **Prompt caching**：Anthropic / OpenAI / DeepSeek 自动支持；火山方舟 ARK + GLM 系列实测 `cache_read=0`
- **Tool calling 准确度**：相同 prompt 下不同模型的工具调用合规度差异较大，需要部署侧 prompt 调优
- **流式 tool_call**：部分模型在流式 mode 下 tool_call 拆 chunk 顺序不同

---

## 六、`init_chat_model` 的可用 provider

`langchain.chat_models.init_chat_model` 支持的 provider 名（节选）：

| provider | 库依赖 | 说明 |
|---|---|---|
| `anthropic` | `langchain-anthropic` | Claude 直连 |
| `google_genai` | `langchain-google-genai` | Gemini |
| `bedrock` | `langchain-aws` | AWS Bedrock |
| `cohere` | `langchain-cohere` | Cohere |
| `openai` | `langchain-openai` | （**会被前置分支拦截**走 ChatOpenAI 路径） |

> ⚠️ 库依赖**不在** `requirements.txt`——按需安装：
> ```bash
> .venv/Scripts/pip install langchain-anthropic
> .venv/Scripts/pip install langchain-google-genai
> ```

详见 [04-operations/testing-strategy.md::5](../04-operations/testing-strategy.md)。

---

## 七、单元测试

`tests/unit/agent/test_core.py`：

- `test_build_model_openai_protocol` — base_url 配置走 ChatOpenAI
- `test_build_model_anthropic_returns_chatanthropic_instance` — anthropic 路径返 ChatAnthropic 实例（不是字符串），api_key 被传入

无 LLM 调用，纯结构验证（mock 不到底层 SDK，但能验证类型与构造函数参数）。

---

## 设计决策记录（ADR）

### ADR-009：统一返回 `BaseChatModel` 实例而不是 `"provider:model"` 字符串

**Context**：deepagents.create_deep_agent 接受 `model` 参数为字符串或 BaseChatModel
实例。早期我们走字符串路径——简单，让 deepagents 内部解析 + 构造。

但发现：anthropic 路径下，传字符串导致 deepagents **不知道**我们的 `cfg.llm.api_key`，
静默回退到 `os.environ["ANTHROPIC_API_KEY"]`。如果用户在 yaml 配了 api_key 但忘了
同步到 env，跑起来用错的 key，没有报错。

**Decision**：所有 provider 都构造 `BaseChatModel` 实例返回，并**显式**把 `cfg.llm.api_key`
传给底层客户端。

**Consequences**：
- ✅ api_key 来源唯一明确（`cfg.llm.api_key`），不依赖 env fallback
- ✅ 可测——单元测试能 assert 返回的实例类型与 api_key 字段
- ✅ 代码路径统一（不再"字符串 vs 实例"分支）
- ⚠️ 加新 provider 需要确认它在 langchain `init_chat_model` 里支持——绝大多数都支持

参考 commit `1228c79` 修复历史。

---

### ADR-017：OpenAI 兼容路径优先级高于 langchain 原生

**Context**：当 `provider == "openai"` 时，理论上 langchain 原生 `init_chat_model("openai:gpt-4o")`
也能跑。为什么前置分支拦截？

**Decision**：让 `base_url` 配置永远走 ChatOpenAI 路径——一致的 base_url 处理逻辑。
原生路径不暴露 base_url 参数，会回退到 OpenAI 默认 endpoint，破坏国内代理场景。

**Consequences**：
- ✅ "国内 OpenAI 兼容代理"一个分支处理所有厂商
- ✅ base_url 参数语义统一
- ⚠️ OpenAI 自家走 ChatOpenAI（不是原生 langchain），但行为一致——不是问题
