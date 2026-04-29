# Data Contracts

> ⏳ **占位**：本篇骨架已建，内容待补。

**关联源码**：`api/models.py`（Pydantic schemas）、`agent/config.py`、`agent/skill_loader.py::SkillsPackage`

**计划覆盖**：
- 请求 schemas：`ChatRequest` / `ConfirmRequest` / `AppendRequest` / `SkillCreateRequest`
- 配置 schemas：`AppConfig` / `AgentConfig` / `LLMConfig` / `LongContextConfig` / `SubAgentSpec` / `PersistenceConfig` / `SkillsConfig`
- skill 包契约：SKILL.md frontmatter + `package.json::bin`
- `run_command` 工具的输入输出格式
