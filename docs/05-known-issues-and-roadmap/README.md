# 已知问题清单（Known Issues）

> ⏳ **占位**：本篇骨架已建，内容待补。

按"框架就绪但未启用"、"功能 stub"、"待实现"、"trade-off 接受"四类分组。每条覆盖：
- 现象 / 影响范围
- 当前 workaround（如有）
- 建议的解决路径（指向 roadmap.md 对应条目）

**计划覆盖（草稿）**：

## A. Stub / 未实现

- `RedisChannelRegistry`：实例化即 `NotImplementedError`，多 worker 部署前必须实现
- `MysqlStore`：同上，按 `BaseStore` 接口仿 `AsyncSqliteStore` 实现
- 认证缺失：`user_id` 透传 mock，未接 SSO/JWT
- requirements-dev.txt 缺失（pytest 等不在主 requirements）

## B. 框架就绪但未启用

- SubAgent：装配链路接好（`config.yaml::agent.subagents`），默认空列表（关闭 task 工具）
- 业务 skill：框架不内置，部署侧通过 SKILL.md 包注入

## C. Trade-off 接受

- LLM round trip：`send_plan` 强制调用增加一次 LLM 往返（换执行计划面板可视化）
- SKILL.md 全文塞 prompt：上下文成本可见，多 skill 时 prompt 膨胀

## D. 待优化

- 火山方舟 ARK + GLM 系列不支持 prompt caching（暂不深入对比）
- SQLite 多副本写并发受全局锁限制，规模化要换 Postgres
