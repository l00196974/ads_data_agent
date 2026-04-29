# 12 用户自定义 Skill 上传（User Skill Upload）

> ⏳ **占位**：本篇骨架已建，内容待补。

**关联源码**：`api/skills.py`、`agent/user_space.py`、`agent/skill_loader.py`（`load_md_skills(system_dir, user_dir)` 第二参数）

**计划覆盖**：
- 上传协议：POST `/api/skills/{user_id}/upload`（zip 包）
- 用户 skill 目录：`data/{user_id}/skills/`
- 命名冲突：用户覆盖系统（同名 skill 时用户实现优先）
- 删除：DELETE `/api/skills/{user_id}/{name}`
- 前端集成（Chat.vue 的"+ 添加" / "× 删除" UI）
- 安全考虑：zip 解压路径校验、bin 脚本来源信任边界
