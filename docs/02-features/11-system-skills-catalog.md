# 11 系统业务 Skill（System Skills via SKILL.md）

> ⏳ **占位**：本篇骨架已建，内容待补。

**关联源码**：`agent/skill_loader.py`、`skills/`（系统 skill 目录）、`config.yaml::skills.md_dir`

**计划覆盖**：
- 加载机制：`skills.md_dir` 下扫描子目录，含 `SKILL.md` 即识别为技能包
- 标准目录结构（SKILL.md + package.json::bin + bin/*.js|*.py|*.sh）
- 单一 `run_command` 通用工具：LLM 看 SKILL.md 全文 + 调 CLI 命令，零 Python 转换
- 安全模型：subprocess list-form + 已注册命令白名单
- **框架不内置任何业务 skill**——部署时拷入 md_dir 重启即可
- 与 `12-user-skill-upload.md` 的协同：用户上传走相同 loader（`load_md_skills(system_dir, user_dir)`）

**ADR 占位**：
- ADR-006: skills/ 不依赖 api/agent/，保持纯领域
- ADR-012: 为什么用单一 `run_command` 而不是每个 SKILL 一个 LangChain tool
