# Artifact 系统手动 UX 测试 checklist

跑前提：
- 后端：`.venv/Scripts/python.exe -m uvicorn main:app --port 8000`
- 前端：`cd frontend && npm run dev`（5173）
- 注：完整 LLM 链路验证需要 .env 配真实 LLM key。如果只想验证 UI，可以直接在
  artifacts 目录里手工 mkdir 一个 artifact 再访问 /artifacts 看效果。

## 测试用例

### 1. 工作区入口

- [ ] 浏览器访问 http://localhost:5173 登录 user_id `alice`
- [ ] 顶栏看到「📦 我的工作区」按钮
- [ ] 点击 → 跳到 /artifacts 页面
- [ ] 空状态显示 "📂 还没有任何 artifact" + 友好提示

### 2. Artifact 出现在对话流

需要 LLM 链路：
- [ ] 在 chat 里发送：「调用 demo-artifact-writer 的 write-demo-report，title 用'测试'」
- [ ] LLM 思考流过程正常
- [ ] 工具结束后对话流出现 artifact 卡片（蓝色边框、📦 图标、含 title + 时间）

如果不通过 LLM，可以手工在 `data/alice/artifacts/2026-04-30-XXXXXX-test/` 下放
SKILL 包脚本生成的产物 + manifest.yaml，然后刷新 /artifacts 页面验证 UI。

### 3. ArtifactBrowser 弹层

- [ ] 点击 artifact 卡片 → 弹出全屏 modal
- [ ] 左侧文件树展开 manifest.yaml / INDEX.md / insights/ / charts/ / data/
- [ ] 默认打开 INDEX.md（来自 manifest.entry_files[0]）
- [ ] 点 charts/sample.echarts.json → 折线图渲染（标题 "示例趋势"）
- [ ] 点 data/sample.csv → 表格化展示
- [ ] 点 manifest.yaml → JSON/YAML 美化展示
- [ ] 点 insights/summary.md → markdown 渲染
- [ ] 点 ⬇下载 zip → 浏览器下载 .zip
- [ ] 点 × 关闭 → 弹层消失
- [ ] 点空白处 → 弹层消失

### 4. 工作区列表

- [ ] /artifacts 页面显示 artifact tile（含 title / 类型 / 文件数 / 时间）
- [ ] 多个 artifact 时按 created_at 倒序
- [ ] tile hover 出现阴影
- [ ] 点 tile → 同样的浏览器弹层

### 5. 删除

- [ ] tile 上"删除"按钮 → confirm 弹窗
- [ ] 确认后 tile 消失
- [ ] data/{user}/artifacts/{id}/ 目录被清空

### 6. 边界情况

- [ ] /artifacts 路由要求登录（未登录跳到 /）
- [ ] 不同 user_id 的工作区隔离（switch 后看不到别人的）
- [ ] 浏览器 DevTools Network 看到 /api/artifacts/... 请求成功
