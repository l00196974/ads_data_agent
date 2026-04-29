# 04 Human-in-the-Loop（HitL 确认流）

> ⏳ **占位**：本篇骨架已建，内容待补。

**关联源码**：`api/channel/base.py:5-24`（ExternallyConfirmable Protocol）、`api/channel/web_sse.py:31-48`、`api/channel/cli.py:20-22`、`api/chat.py:161-172`（confirm endpoint）、`api/channel/runner.py:46-58`、`api/channel/registry.py`

**计划覆盖**：
- HitL 三场景（敏感操作确认 / 用户主动停止 / 执行中追加要求）
- `ExternallyConfirmable` Protocol：能力守卫而非类型守卫
- 跨连接确认（WebSSE）vs 同栈确认（CLI）的实现差异
- ChannelRegistry 路由：单 worker InMemory / 多 worker Redis stub
- `_resume_safely`：cancel 安全 resume 防 corrupt state
- `interrupt_on` 配置触发条件

**ADR 占位**：
- ADR-003: 为什么用 `Protocol` + `runtime_checkable` 而不是基类继承
- ADR-008: cancel 期间不直接打断 ainvoke（用 shield + 等待 inner task 退出）
