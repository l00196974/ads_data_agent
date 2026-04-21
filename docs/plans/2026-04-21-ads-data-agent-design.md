# Ads Data Agent 设计文档

**日期：** 2026-04-21  
**目标用户：** 华为广告营销与运维人员  
**技术栈：** deepAgent SDK + LangGraph + FastAPI + Vue 3 + ECharts

---

## 一、项目目标

构建一个多用户隔离的广告数据分析 Agent 平台，服务华为广告的营销和运维人员。支持：
- 自然语言对话式数据分析
- 系统预置 + 用户自定义 Skills/SubAgents
- 实时流式执行进展展示
- Human-in-the-Loop 中断与确认
- 图表可视化输出

---

## 二、整体架构

```
ads_data_agent/
├── main.py                          # FastAPI 入口
├── agent/
│   ├── core.py                      # AdsDataAgent 主 Agent（deepAgent）
│   ├── session.py                   # 用户会话管理，thread_id=user_id
│   └── agents_md.py                 # Agents.md 加载（系统模板 merge 用户自定义）
├── skills/
│   ├── system/                      # 系统预置 Skills（只读）
│   │   ├── campaign_report.py       # 广告活动报表
│   │   ├── budget_analysis.py       # 预算分析
│   │   ├── anomaly_detection.py     # 异常检测
│   │   └── chart_skill.py           # 图表数据生成
│   └── user/
│       └── {user_id}/               # 用户自定义 Skills
├── sub_agents/
│   ├── system/                      # 系统预置子 Agent（只读）
│   │   ├── report_agent.py          # 报表子 Agent
│   │   └── anomaly_agent.py         # 异常检测子 Agent
│   └── user/
│       └── {user_id}/               # 用户自定义子 Agent
├── data/
│   └── {user_id}/                   # 用户数据目录（按 user_id 隔离）
│       ├── agents.md                # 用户自定义 Agents.md
│       ├── skills/                  # 用户自定义 Skills
│       └── memory/                  # 对话记忆
├── config/
│   ├── system_agents.md             # 系统级 Agents.md 基础模板
│   └── config.yaml                  # 应用配置
├── frontend/                        # Vue 3 前端
│   ├── src/
│   │   ├── pages/
│   │   │   ├── Login.vue            # 登录页（Mock user_id）
│   │   │   └── Chat.vue             # 主聊天界面
│   │   ├── components/
│   │   │   ├── MessageBubble.vue    # 消息气泡（支持文字+图表+表格混排）
│   │   │   ├── StepTracker.vue      # 左侧执行步骤面板
│   │   │   ├── ChartWidget.vue      # ECharts 图表组件
│   │   │   ├── ConfirmDialog.vue    # 危险操作确认弹窗
│   │   │   └── SkillPanel.vue       # Skills/SubAgents 管理面板
│   │   └── utils/
│   │       └── sse.js               # SSE 连接管理
│   └── vite.config.js
├── .env.example
└── config.yaml
```

---

## 三、用户隔离机制

| 隔离维度 | 实现方式 |
|---------|---------|
| 对话历史 | LangGraph `thread_id=user_id`（框架原生） |
| Agents.md | `data/{user_id}/agents.md`，启动时合并系统模板 + 用户自定义 |
| Skills | 加载时合并 `skills/system/` + `data/{user_id}/skills/` |
| SubAgents | 加载时合并 `sub_agents/system/` + `data/{user_id}/sub_agents/` |
| 用户数据 | `data/{user_id}/` 目录完全隔离 |

**Mock 阶段：** `user_id` 通过登录页输入直接传入，无需认证。后续接入 SSO/JWT 时只需替换 `user_id` 获取逻辑。

---

## 四、Skills & SubAgents 体系

### 系统预置（只读，所有用户共享）

| 名称 | 类型 | 功能 |
|------|------|------|
| campaign_report | Skill | 广告活动报表查询 |
| budget_analysis | Skill | 预算使用分析 |
| anomaly_detection | Skill | 投放异常检测 |
| chart_skill | Skill | 生成图表配置数据 |
| report_agent | SubAgent | 综合报表生成子 Agent |
| anomaly_agent | SubAgent | 异常分析专项子 Agent |

### 用户自定义（per user_id）

用户可通过：
1. 页面 UI 配置（填写 Skill 描述 + 参数）
2. 上传自定义 Python 脚本（后续支持）

---

## 五、流式执行 & 实时进展

后端使用 **SSE（Server-Sent Events）** 推送以下事件类型：

```
event: step
data: {"type":"tool_start", "tool":"anomaly_agent", "msg":"检测异常数据..."}

event: step
data: {"type":"tool_end", "tool":"anomaly_agent", "msg":"检测到3个异常点"}

event: token
data: {"token": "华"}

event: chart
data: {"type":"line", "title":"昨日点击量趋势", "x":[...], "series":[...]}

event: interrupt
data: {"type":"confirm_required", "action":"delete_adgroups", "preview":[...], "msg":"即将删除23个广告组"}

event: done
data: {"usage": {...}}
```

前端：
- 左侧 **StepTracker** 面板实时展示执行步骤
- 右侧对话区 token 逐字打印
- 图表事件触发 ECharts 渲染，与文字混排

---

## 六、Human-in-the-Loop 三场景

### 场景 A：危险操作确认（后端主动中断）

触发条件：Agent 执行 `config.yaml` 中 `agent.interrupt_on` 定义的操作。

流程：
1. 后端 `interrupt()` 暂停执行 → 推送 `event: interrupt`
2. 前端弹出 ConfirmDialog，冻结输入框
3. 用户点确认/取消 → `POST /api/chat/{user_id}/confirm {"action":"approve"/"cancel"}`
4. 后端恢复或终止执行

### 场景 B：用户主动停止

- 输入框旁常驻 **[停止]** 按钮，执行中可随时点击
- 触发 `POST /api/chat/{user_id}/cancel`
- 后端立即终止当前 thread，推送 `event: interrupted`

### 场景 C：执行中追加要求

- 执行中输入框**保持可用**
- 用户发送追加消息 → `POST /api/chat/{user_id}/append {"message":"..."}`
- 后端注入补充上下文，Agent 在下一个 checkpoint 动态调整计划
- 前端显示 `📝 已收到补充要求` 提示气泡

---

## 七、图表可视化

- **图表库：** ECharts（Apache 开源）
- **支持类型：** line / bar / pie / scatter / heatmap
- **渲染方式：** Agent 输出结构化 JSON，前端 ChartWidget 渲染
- **功能：** [下载图片] / [复制数据]
- **布局：** 图表嵌入对话气泡，与文字分析混排展示

---

## 八、API 接口清单

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/chat/{user_id}` | 发送消息（SSE 流式返回） |
| POST | `/api/chat/{user_id}/confirm` | 危险操作确认/取消 |
| POST | `/api/chat/{user_id}/cancel` | 主动停止执行 |
| POST | `/api/chat/{user_id}/append` | 执行中追加要求 |
| GET | `/api/skills/{user_id}` | 获取 Skills 列表 |
| POST | `/api/skills/{user_id}` | 添加用户自定义 Skill |
| DELETE | `/api/skills/{user_id}/{skill_id}` | 删除用户自定义 Skill |
| GET | `/api/agents/{user_id}` | 获取子 Agent 列表 |
| POST | `/api/agents/{user_id}` | 添加用户自定义子 Agent |
| GET | `/api/history/{user_id}` | 获取对话历史 |
| DELETE | `/api/history/{user_id}` | 清空对话历史 |

---

## 九、配置设计

### `.env`（敏感信息，不入库）

```bash
# LLM
ANTHROPIC_API_KEY=sk-ant-xxx
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-4-6

# 可观测性（可选）
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=ls__xxx
LANGSMITH_PROJECT=ads-data-agent

# 服务
SECRET_KEY=your-secret-key
```

### `config.yaml`（非敏感，入库）

```yaml
server:
  host: 0.0.0.0
  port: 8000
  cors_origins:
    - "http://localhost:5173"

agent:
  max_iterations: 20
  timeout_seconds: 120
  interrupt_on:
    - delete_adgroups
    - modify_budget
    - pause_campaign

persistence:
  type: local            # local / redis / postgres
  data_dir: ./data

skills:
  system_dir: ./skills/system
  user_dir: ./data/{user_id}/skills

sub_agents:
  system_dir: ./sub_agents/system
  user_dir: ./data/{user_id}/sub_agents
```

### 环境区分

| 文件 | 用途 |
|------|------|
| `.env.development` | 本地开发，in-memory 持久化 |
| `.env.staging` | 测试，本地文件持久化 |
| `.env.production` | 生产，postgres/redis，必填项严格校验 |

---

## 十、前端页面设计

### 登录页
- 输入 `user_id`（Mock 阶段）
- 点击进入聊天

### 聊天主界面布局

```
┌─────────────────────────────────────────────────────┐
│ 华为广告数据助手              [user: xxx]  [退出]    │
├──────────────┬──────────────────────────────────────┤
│              │                                      │
│  已注册能力   │         对话区域                     │
│  ─────────   │  文字 + 图表 + 表格混排               │
│  系统Skills  │  ECharts 图表内嵌在气泡中             │
│  • 活动报表  │                                      │
│  • 预算分析  │                                      │
│  • 异常检测  │                                      │
│              │                                      │
│  我的Skills  ├──────────────────────────────────────┤
│  • [自定义]  │  执行步骤实时进展（StepTracker）       │
│  + 添加      │  ✅ 步骤1  ⏳ 步骤2...               │
│              ├──────────────────────────────────────┤
│              │  [输入框（执行中保持可用）] [发送][停止]│
└──────────────┴──────────────────────────────────────┘
```
