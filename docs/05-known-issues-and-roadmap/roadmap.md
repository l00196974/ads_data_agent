# 路线图（Roadmap）

> ⏳ **占位**：本篇骨架已建，内容待补。

按 P0 / P1 / P2 优先级分类，每条引用对应的 known-issues 条目与设计文档。

**计划覆盖（草稿）**：

## P0（阻塞生产）

- 接真实指标查询数据源（替换 SKILL.md 包对外部 mock 服务的依赖）
- 实现认证层（user_id → JWT/SSO）

## P1（多 worker 化）

- `RedisChannelRegistry` 实现
- 切 Postgres checkpoint / store（替换 SQLite）
- 部署 Docker + Helm chart

## P2（功能扩展）

- 子 Agent markdown 配置加载（`agents/*.md` + frontmatter，仿 Claude Code）
- Prompt caching 厂商对照与切换策略
- 工具调用观测：每条 tool_call 的 latency / token 计入 metrics
- CI（pytest + 前端构建）
