# Agent Execution Panel Implementation Plan

> ⚠️ **历史归档 (Archived)**：本文为 2026-04-22 执行面板首版实施 plan。其中描述的
> "LLM 主动调 `update_task` 工具上报状态" 已被推翻 —— 当前架构由 `AgentRunner` 自主
> 监听 `on_tool_start/end` 推送状态变化（**append-only 模型**），`update_task` 工具
> 已删除，`send_to_user` 也已废弃（图表内联到 markdown 由前端解析）。仅 `send_plan`
> 工具保留。**最新架构请参阅 `docs/02-features/03-execution-panel.md`**。

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 用户提问后，在消息流中插入固定的执行面板，显示 Agent 规划的任务列表（逐步更新状态）和实时 tips 区域（显示当前正在做什么）。

**Architecture:** 新增 `plan` SSE 事件携带任务列表；Agent 通过新工具 `send_plan` 在执行前先规划；前端将 `plan` 消息作为特殊气泡插入对话流，`step` 事件更新任务状态和 tips 文字。

**Tech Stack:** Vue 3 + SSE（FastAPI）、LangGraph deepagents、Python async

---

### Task 1: 后端 — WebSSEChannel 新增 send_plan 工具

**Files:**
- Modify: `api/channel/web_sse.py`
- Modify: `api/channel/cli.py`
- Modify: `api/channel/base.py`

**Step 1: 在 BaseChannel 新增抽象方法 send_plan**

在 `api/channel/base.py` 的 `send_step` 方法后面添加：

```python
@abstractmethod
async def send_plan(self, tasks: list) -> None:
    """发送任务计划列表 [{"id": "t1", "name": "..."}]"""
```

**Step 2: 在 WebSSEChannel 实现 send_plan**

在 `api/channel/web_sse.py` 的 `send_step` 方法后面添加：

```python
async def send_plan(self, tasks: list) -> None:
    data = json.dumps({"tasks": tasks}, ensure_ascii=False)
    await self._queue.put(f"event: plan\ndata: {data}\n\n")
```

**Step 3: 在 WebSSEChannel.get_skill() 中新增 send_plan 工具**

在现有 `send_to_user` 函数定义之前，添加 `send_plan` 工具：

```python
async def send_plan(
    tasks: list,
) -> str:
    """
    在开始执行任何工具之前，必须首先调用此工具规划任务步骤。

    tasks: 任务列表，每项格式为 {"id": "t1", "name": "任务描述"}
    示例: [{"id": "t1", "name": "查询广告数据"}, {"id": "t2", "name": "生成图表"}]

    规则：
    - 任务数量 2-5 个，不要过细也不要过粗
    - name 用简短中文描述，不超过 20 字
    - id 按 t1, t2, t3... 顺序命名
    """
    await channel.send_plan(tasks)
    return "ok"
```

然后修改 `get_skill` 的返回值，返回两个工具的列表：

```python
return [send_plan, send_to_user]
```

**Step 4: 在 CLIChannel 实现 send_plan**

在 `api/channel/cli.py` 的 `send_step` 方法后面添加：

```python
async def send_plan(self, tasks: list) -> None:
    print("\n📋 执行计划:")
    for t in tasks:
        print(f"  ⬜ {t['id']}: {t['name']}")
```

**Step 5: 验证后端不报错**

```bash
cd D:/workplace/ads_data_agent
python -c "from api.channel.web_sse import WebSSEChannel; print('OK')"
python -c "from api.channel.cli import CLIChannel; print('OK')"
```

Expected: 两行都输出 `OK`，无报错

**Step 6: Commit**

```bash
cd D:/workplace/ads_data_agent
git add api/channel/base.py api/channel/web_sse.py api/channel/cli.py
git commit -m "feat: add send_plan tool and plan SSE event to channels"
```

---

### Task 2: 后端 — runner.py 传递 task_id，system prompt 要求先规划

**Files:**
- Modify: `api/channel/runner.py`
- Modify: `agent/user_space.py` 或 system prompt 所在文件

**Step 1: 找到 system prompt 的位置**

```bash
cd D:/workplace/ads_data_agent
grep -r "system_prompt\|agents\.md\|AGENT" agent/ --include="*.py" -l
cat agent/user_space.py
```

**Step 2: 修改 system prompt，要求 Agent 第一步必须调用 send_plan**

在 `agent/user_space.py` 的 `get_agents_md()` 返回内容中，或在 `api/chat.py` 的 `_make_build_fn` 中，在 system_prompt 前面追加以下指令：

```python
PLAN_INSTRUCTION = """
## 执行规则

每次收到用户问题后，必须严格按照以下顺序执行：

1. **首先调用 send_plan 工具**，列出本次任务的执行步骤（2-5个），不得跳过
2. 再按照计划逐步执行各工具
3. 最后调用 send_to_user 展示结果

示例：用户问"查询曝光数据并生成图表"
→ 先调用 send_plan(tasks=[{"id":"t1","name":"查询曝光数据"},{"id":"t2","name":"生成柱状图"},{"id":"t3","name":"展示分析结论"}])
→ 再调用 query_campaign_report(...)
→ 再调用 build_chart(...)
→ 最后调用 send_to_user(action="text", ...) + send_to_user(action="chart", ...)
"""
```

在 `api/chat.py` 的 `_make_build_fn` 函数里，将 `system_prompt` 改为：

```python
system_prompt = PLAN_INSTRUCTION + "\n\n" + us.get_agents_md()
```

**Step 3: runner.py 的 send_step 补充 task_id（可选字段，暂传空）**

`runner.py` 的 `on_tool_start` 已经有 tool_name，目前不做 task_id 推断，task_id 由前端按顺序匹配。此步骤确认无需改动 runner.py。

**Step 4: 验证导入**

```bash
cd D:/workplace/ads_data_agent
python -c "from api.chat import router; print('OK')"
```

Expected: `OK`

**Step 5: Commit**

```bash
git add api/chat.py
git commit -m "feat: inject send_plan instruction into system prompt"
```

---

### Task 3: 前端 — Chat.vue 新增 plan 事件处理和数据结构

**Files:**
- Modify: `frontend/src/pages/Chat.vue`

**Step 1: 在 script setup 中新增 planTasks 和 tipText ref**

在现有 ref 声明区域（`const messages = ref([])` 附近）新增：

```js
const planTasks = ref([])   // [{id, name, status: 'pending'|'running'|'done'}]
const tipText = ref('')
```

**Step 2: 在 SSE handlers 中新增 plan 事件处理**

在 `sseClient = new SSEClient(...)` 的 handlers 对象中，在 `step:` handler 之前新增：

```js
plan: (data) => {
  planTasks.value = data.tasks.map(t => ({
    id: t.id,
    name: t.name,
    status: 'pending',
  }))
  // 将执行面板作为特殊消息插入对话流
  messages.value.push({ role: 'plan', type: 'plan', content: planTasks })
  scrollToBottom()
},
```

**Step 3: 修改 step handler，更新任务状态和 tipText**

将现有 step handler 替换为：

```js
step: (data) => {
  tipText.value = data.msg
  // 更新任务状态：按顺序找第一个 pending/running 的任务
  if (data.type === 'tool_start') {
    const idx = planTasks.value.findIndex(t => t.status === 'pending')
    if (idx !== -1) planTasks.value[idx].status = 'running'
  } else if (data.type === 'tool_end') {
    const idx = planTasks.value.findIndex(t => t.status === 'running')
    if (idx !== -1) planTasks.value[idx].status = 'done'
  }
  scrollToBottom()
},
```

**Step 4: 修改 done handler，清空 tipText**

在现有 `done:` handler 中，`isStreaming.value = false` 之前加：

```js
tipText.value = ''
```

**Step 5: 修改 clearChat，重置 planTasks 和 tipText**

在 `clearChat()` 函数中补充：

```js
planTasks.value = []
tipText.value = ''
```

**Step 6: Commit**

```bash
cd D:/workplace/ads_data_agent/frontend
# 不提交，等模板改完一起提交
```

---

### Task 4: 前端 — 渲染执行面板模板和样式

**Files:**
- Modify: `frontend/src/pages/Chat.vue`

**Step 1: 在消息列表模板中新增 plan 类型气泡的渲染**

找到现有的 `<template v-if="msg.type === 'text'">` 块，在 `<ChartWidget>` 的 `v-else-if` 之后新增：

```html
<template v-else-if="msg.type === 'plan'">
  <div class="exec-panel">
    <div class="exec-panel-title">📋 执行计划</div>
    <div class="task-list">
      <div
        v-for="task in msg.content.value"
        :key="task.id"
        :class="['task-item', task.status]"
      >
        <span class="task-icon">
          {{ task.status === 'done' ? '✅' : task.status === 'running' ? '🔄' : '⬜' }}
        </span>
        <span class="task-name">{{ task.name }}</span>
        <span v-if="task.status === 'running'" class="task-badge">执行中</span>
      </div>
    </div>
    <div v-if="tipText && msg.content === planTasks" class="tip-area">
      <span class="tip-dot">●</span>
      <span class="tip-text">{{ tipText }}</span>
    </div>
  </div>
</template>
```

注意：`msg.content === planTasks` 用于判断是否是**最新一次**的执行面板（历史记录中的面板不显示 tip）。

**Step 2: 在 style scoped 中新增执行面板样式**

在现有 `.bubble.system` 样式后面添加：

```css
/* 执行面板 */
.exec-panel {
  width: 100%;
  background: rgba(255, 255, 255, 0.0);
  border-radius: 0;
  padding: 0;
}
.exec-panel-title {
  font-size: 12px;
  font-weight: 600;
  color: #8c6a00;
  margin-bottom: 10px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.task-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 10px;
}
.task-item {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #595959;
  padding: 6px 10px;
  border-radius: 6px;
  background: rgba(0,0,0,0.03);
  transition: background 0.2s;
}
.task-item.running {
  background: rgba(199, 0, 11, 0.06);
  color: #c7000b;
}
.task-item.done {
  color: #52c41a;
  background: rgba(82, 196, 26, 0.05);
}
.task-icon {
  font-size: 14px;
  flex-shrink: 0;
}
.task-name {
  flex: 1;
}
.task-badge {
  font-size: 11px;
  padding: 2px 8px;
  background: rgba(199, 0, 11, 0.1);
  color: #c7000b;
  border-radius: 10px;
  font-weight: 500;
}
/* Tips 区域 */
.tip-area {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  background: rgba(240, 245, 255, 0.8);
  border-radius: 6px;
  border-left: 3px solid #2f54eb;
}
.tip-dot {
  color: #2f54eb;
  font-size: 8px;
  animation: blink 1s infinite;
  flex-shrink: 0;
}
.tip-text {
  font-size: 12px;
  color: #2f54eb;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}
```

**Step 3: 调整 bubble 样式，plan 类型气泡不需要背景和 padding**

在 `.bubble.assistant` 之后新增：

```css
.bubble.plan {
  background: rgba(255, 251, 230, 0.85);
  backdrop-filter: blur(10px);
  border: 1px solid #ffe58f;
  max-width: 92%;
  padding: 14px 16px;
}
```

**Step 4: Build 验证无报错**

```bash
cd D:/workplace/ads_data_agent/frontend
npm run build 2>&1 | tail -5
```

Expected: `✓ built in ...ms`，无 error

**Step 5: Commit**

```bash
cd D:/workplace/ads_data_agent
git add frontend/src/pages/Chat.vue
git commit -m "feat: add execution panel UI with task list and tips"
```

---

### Task 5: 集成测试

**Step 1: 启动后端**

```bash
cd D:/workplace/ads_data_agent
pkill -f "python main.py" 2>/dev/null
python main.py &
sleep 3
curl -s http://localhost:8000/api/skills/test_user | python3 -c "import sys,json; print('OK', len(json.load(sys.stdin)['system']), 'skills')"
```

Expected: `OK 4 skills`（或更多，取决于 send_plan 是否计入）

**Step 2: 用 Playwright 测试完整流程**

打开浏览器 `http://localhost:5173/`，登录 `test_user`，发送：

> 帮我查询2024-12-23到2024-12-29各广告活动的曝光数据报告，并生成柱状图展示

**Step 3: 验证以下预期效果**

- [ ] 用户消息气泡下方出现黄色执行面板
- [ ] 面板显示 2-5 个任务（⬜ 状态）
- [ ] 随执行进度，任务变为 🔄（执行中）→ ✅（完成）
- [ ] tips 区域显示当前工具调用信息，执行完后消失
- [ ] 最终 Markdown 摘要 + 图表正常渲染
- [ ] 点击"新对话"后面板清空

**Step 4: 如有问题检查后端日志**

```bash
tail -50 backend.log
```

---

### Task 6: 最终 Commit

```bash
cd D:/workplace/ads_data_agent
git add -A
git commit -m "feat: agent execution panel with task planning and live tips"
```
