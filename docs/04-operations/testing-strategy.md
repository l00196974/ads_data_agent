# 测试策略（Testing Strategy）

> ⏳ **占位**：本篇骨架已建，内容待补。

**关联源码**：`tests/`、`scripts/`、`benchmarks/`

**计划覆盖**：
- 三类测试的分工：
  - `tests/unit/`：离线单测（39 个，~20s 跑完，无 LLM 调用，pytest）
  - `scripts/`：持久可重跑的 E2E 验证（真实打 LLM，按需手动跑）
  - `benchmarks/`：性能 / 评测（profile_latency / ads_eval）
- 镜像源码结构的目录约定（`tests/unit/agent/test_*.py` 对应 `agent/*.py`）
- patch "使用点"而不是"定义点"（agent.builder.create_deep_agent 而非 agent.core.create_deep_agent）
- mock 哪些 / 不 mock 哪些（不 mock checkpointer——会失去类型保证）
- requirements-dev.txt 缺失的现状（pytest / pytest-asyncio 未在主 requirements）
- CI（暂未配置）

