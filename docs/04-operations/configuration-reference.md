# 配置项参考（Configuration Reference）

> ⏳ **占位**：本篇骨架已建，内容待补。

**关联源码**：`config.yaml`、`.env.example`、`agent/config.py`

**计划覆盖**：
- `config.yaml` 全字段（每段：类型 / 默认值 / 取值范围 / 影响）
  - server.* / agent.{max_iterations, timeout_seconds, interrupt_on, long_context, subagents} / persistence.* / skills.md_dir
- `.env` 全字段
  - LLM_API_KEY / LLM_BASE_URL / LLM_PROVIDER / LLM_MODEL / ANTHROPIC_API_KEY
- 配置优先级：env > yaml > pydantic 默认值
- 何时改 .env vs 改 yaml（敏感性 vs 静态性）
