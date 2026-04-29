# 09 LLM 双协议路由（LLM Dual-Protocol Routing）

> ⏳ **占位**：本篇骨架已建，内容待补。

**关联源码**：`agent/llm.py::_build_model`、`agent/config.py::LLMConfig`

**计划覆盖**：
- 路由分支：`base_url` 非空 OR `provider==openai` → `ChatOpenAI`；其他 → `init_chat_model`
- 已支持 provider 列表（OpenAI / DeepSeek / 通义千问 / 火山方舟 / Anthropic / Google）
- 为什么把 `cfg.llm.api_key` 显式传底层（旧 bug：anthropic 路径静默忽略 api_key）
- 厂商特性差异（prompt caching 当前不展开 — 暂不深入对比）

**ADR 占位**：
- ADR-009: 统一返回 `BaseChatModel` 实例而不是 `"provider:model"` 字符串
