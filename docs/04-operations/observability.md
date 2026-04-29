# 可观测性（Observability）

> ⏳ **占位**：本篇骨架已建，内容待补。

**关联源码**：`backend.log`（生产日志）、`scripts/verify_p0_e2e.py`、`benchmarks/profile_latency.py`

**计划覆盖**：
- 日志：FastAPI / uvicorn 日志位置与级别
- LangSmith trace 接入（`.env::LANGSMITH_*`）
- SSE 调试：浏览器 DevTools Network 看 event stream
- 性能 profiling：`benchmarks/profile_latency.py` 用法
- 已知卡点诊断（LLM 调用慢 / SQLite 写锁 / 工具 subprocess 超时）
- 缺：指标导出（Prometheus / OpenTelemetry）—— 暂未实现
