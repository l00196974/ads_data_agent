# REST Endpoints

> ⏳ **占位**：本篇骨架已建，内容待补。

**关联源码**：`api/chat.py`、`api/skills.py`、`api/models.py`

**计划覆盖**（每个 endpoint 一节）：
- POST `/api/chat/{user_id}` — 发送消息（SSE 流式返回）
- POST `/api/chat/{user_id}/confirm` — HitL 确认/取消
- POST `/api/chat/{user_id}/cancel` — 主动停止
- POST `/api/chat/{user_id}/append` — 执行中追加要求（cancel + 重起）
- POST `/api/chat/{user_id}/reset` — 开新对话（生成新 conversation_id）
- GET `/api/skills/{user_id}` — 列出系统 + 用户 skills
- POST `/api/skills/{user_id}/upload` — 上传用户自定义 skill 包
- DELETE `/api/skills/{user_id}/{name}` — 删除用户 skill

每条覆盖：URL / method / 请求 schema / 响应 schema / SSE 事件序列 / 错误码 / 示例
