# Artifact 系统设计文档

**日期**：2026-04-30
**作者**：Brainstorming with chiky0123（auto mode）
**状态**：Draft → 待用户 review 后进入 writing-plans 阶段

---

## 一、背景与动机

当前华为广告 Data Agent 的输出能力局限在 SSE 文本流：LLM 通过 `send_token` 推 markdown
文本（含 `\`\`\`chart\`\`\`` 代码块前端解析），通过 `send_plan` 推执行计划。这套机制对**单段
回答 + 1-2 张图表**够用，但数据分析的真实场景往往**远超**这个边界：

- 一份完整的 KA 客户 CTR 异常分析 = 多视角原始数据（按时段 / 渠道 / 行业切片）
  + 多张可视化图表 + 漏斗诊断 + 文字洞察 + 建议动作
- 周报 / 月报场景 = 跨周期对照表 + 几十张图 + 详细的洞察与解释
- 数据分析师跨周持续探索一个主题 = 多次会话累积的产物

**SSE 文本流不适合承载这类输出**：
- 单次 LLM 输出过大 → token 成本飙升 + checkpoint 状态膨胀
- 用户读体验差（一长串 markdown 在对话里）
- 不能跨会话回看（LLM 重新生成总有差异）
- 不能脱离对话独立访问产物（导出 / 下载 / 分享）

→ 需要一个**文件系统支撑的 artifact 体系**，把"大型分析产物"持久化为用户长期资产，
跨 channel 提供差异化呈现（Web 目录浏览 / CLI 文件路径 / 未来 Mobile 下载）。

---

## 二、目标

### v1 必须做到

1. 数据分析 skill 可以把多文件产物（数据 / 图表 / 文档 / Office 文件等）写到一个独立的
   "artifact 目录"
2. 用户在 **per-user 共享工作区**里持续累积 artifact（跨 conversation / 跨时间）
3. 每次分析对应**独立 immutable 目录**（写完不变，老目录受保护）
4. Web 端有目录浏览器 UI，CLI 端给出文件路径
5. artifact 是独立资源，可脱离 chat 独立访问（"我的工作区"页面）
6. LLM 不直接吐大数据——artifact 内容不进 thread / context

### v1 不做（明确不在范围）

- ❌ 多 artifact 合并 / 重组（每个 artifact 写完即固化）
- ❌ 用户 rename / move / share artifact
- ❌ 跨用户分享 artifact
- ❌ 自动清理 / retention policy（用户手动删）
- ❌ artifact 内容全文搜索
- ❌ artifact 版本历史（同一 id 不同时间的快照）
- ❌ "项目 / 集合"概念（多个 artifact 归为一个项目）—— 后续叠加，v1 用扁平列表

---

## 三、已确认的关键设计前提

| 前提 | 决策 |
|---|---|
| 颗粒度 | 一次分析 = 一个 artifact 目录（**A 选项**） |
| 共享范围 | per-user（跨 conversation 持久共享） |
| 可变性 | immutable（写完不变） |
| 触发时机 | skill 决定（写就生成，不写就没有），LLM 不主动触发"生成 artifact" |

---

## 四、详细设计（10 项）

### ① 存储位置

```
data/
└── {user_id}/
    └── artifacts/                         ← 用户工作区
        ├── 2026-04-30-093015-ka-ctr-anomaly/
        ├── 2026-04-29-141022-weekly-report/
        └── ...
```

- 复用现有 `agent/user_space.py::UserSpace`，跟 `skills/` / `tool_outputs/` / `memory/` 同层
- 文件系统起步，多副本部署时再切共享卷或 S3（v1 不做）

---

### ② artifact_id 命名

**格式**：`{YYYY-MM-DD-HHmmss}-{LLM-生成的 slug}`

例：`2026-04-30-093015-ka-ctr-anomaly`

- 时间戳保证唯一 + 可排序
- slug 让用户在文件浏览器里能一眼识别内容
- slug 由 LLM 在调用 skill 前生成（runner 注入到环境变量），取自当前用户问题的语义

冲突处理：理论上同一秒的多次调用可能撞名，但实际罕见（LLM 调用本身有秒级延迟）；
若发生则后写覆盖前写——immutable 假设下用户层面看不到这种 race。

---

### ③ Artifact 内部结构（manifest 是契约，目录自由）

```
<artifact_id>/
├── manifest.yaml          [强制] 元数据 + 入口声明 + 类型 hint
└── ...                    [自由] skill 想怎么放怎么放
                           常见模式（推荐但不强制）：
                           - 综合报告:  INDEX.md + insights/ + data/ + charts/
                           - Office 交付: quarterly-report.pptx
                           - 单数据集:  cohort-ltv-2026q2.xlsx
                           - 单文档:    result.md
```

**关键决策**：只规定 `manifest.yaml` 是强制契约文件，**其他完全自由**。skill 想交付什么
形态都行——一份 PPT、一个 Excel、一个 Word、一份 markdown 报告，都按需放。

#### `manifest.yaml` schema

```yaml
artifact_id: 2026-04-30-093015-ka-ctr-anomaly
title: KA 客户 CTR 异常分析
created_at: 2026-04-30T09:30:15+08:00
user_id: alice
artifact_type: report | document | spreadsheet | presentation | dataset | mixed
description: |
  本次分析的 2-3 行简要说明，给前端 artifact 卡片用。
entry_files:
  - INDEX.md            # 用户打开 artifact 时默认看这个文件（按顺序，第一个是主入口）
related_skills:
  - query-metrics
  - detect-anomaly
related_conversation_id: <可选>     # 来自哪次对话；不依赖此字段做关键路由
```

#### 字段说明

- **artifact_type**：是 hint，让前端选合适的渲染策略：
  - `report` → 文件树浏览器（左树 + 右预览）
  - `document` → 单文档预览（如 `INDEX.md` 全屏 markdown）
  - `spreadsheet` → 表格化预览（CSV / XLSX）
  - `presentation` → 内嵌 PPT 预览（或直接下载）
  - `dataset` → 文件下载为主
  - `mixed` → 文件树浏览器（多类型混合）
- **entry_files**：用户打开 artifact 时默认看到什么；前端按顺序尝试打开第一个能渲染的
- **related_skills**：哪些 skill 贡献了这次产出，用于追溯
- **related_conversation_id**：可选，来自哪次对话；artifact 是独立资源，不依赖此字段
  做关键路由（用户可以独立访问 artifact，不一定经过 chat）

#### INDEX.md（推荐但不强制）

约定：复杂 artifact 推荐写一份 `INDEX.md` 作为人可读的导览页，列出本次产出的主要内容
和文件指引。前端在 `entry_files` 包含 INDEX.md 时优先渲染它。

简单 artifact（如单 PPT）可以不写 INDEX.md，`entry_files` 直接指向 .pptx。

---

### ④ Skill 写产物的方式：env 注入 + 约定目录

runner 在调用 SKILL.md 子命令前，通过环境变量注入：

| env | 用途 |
|---|---|
| `ADS_AGENT_ARTIFACT_DIR` | 当前分析的 artifact 目录绝对路径（已创建好空目录） |
| `ADS_AGENT_USER_ID` | 用户标识 |
| `ADS_AGENT_ARTIFACT_ID` | 当前 artifact_id（skill 在 stderr 通知时引用） |

skill 脚本（任意语言）按 ③ 自由结构往 `ADS_AGENT_ARTIFACT_DIR` 里写文件。

**关键不变量**：
- skill 脚本不需要知道"agent / chat / channel"概念，只需要会写文件
- LLM 不感知 artifact 存在——它只看 `run_command` stdout 的工具返回摘要
- skill 是部署侧资产，框架不耦合具体业务

#### 例：Node skill 写综合报告

```js
// skills/ka-ctr-anomaly/bin/analyze.js
const fs = require('node:fs');
const path = require('node:path');
const yaml = require('js-yaml');

const artifactDir = process.env.ADS_AGENT_ARTIFACT_DIR;
const artifactId = process.env.ADS_AGENT_ARTIFACT_ID;

// 1. 写数据
fs.mkdirSync(path.join(artifactDir, 'data'), { recursive: true });
fs.writeFileSync(path.join(artifactDir, 'data/raw-query.json'), JSON.stringify(rawData));

// 2. 写图表
fs.mkdirSync(path.join(artifactDir, 'charts'), { recursive: true });
fs.writeFileSync(path.join(artifactDir, 'charts/ctr-trend.echarts.json'), JSON.stringify(chartConfig));

// 3. 写洞察
fs.mkdirSync(path.join(artifactDir, 'insights'), { recursive: true });
fs.writeFileSync(path.join(artifactDir, 'insights/summary.md'), summaryText);

// 4. 写 INDEX.md
fs.writeFileSync(path.join(artifactDir, 'INDEX.md'), indexContent);

// 5. 写 manifest（必须）
fs.writeFileSync(path.join(artifactDir, 'manifest.yaml'), yaml.dump({
    artifact_id: artifactId,
    title: 'KA 客户 CTR 异常分析',
    created_at: new Date().toISOString(),
    user_id: process.env.ADS_AGENT_USER_ID,
    artifact_type: 'report',
    description: '基于昨日数据定位 CTR 异常的 KA 客户与时间分布。',
    entry_files: ['INDEX.md'],
    related_skills: ['query-metrics', 'detect-anomaly'],
}));

// 6. 通知 runner（stderr）
process.stderr.write(`<<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id=${artifactId}>>>\n`);

// 7. stdout 输出工具返回值（给 LLM 看的简短摘要）
console.log(`已生成分析报告：${artifactId}（含 8 个文件，主要看 INDEX.md）`);
```

---

### ⑤ Artifact 通知：skill stderr 约定 + 前端按需拉

#### 写入 / 通知 / 读取分离

```
[skill 端]
1. skill 按 ④ env 写 artifact 文件（自由）
2. 写完后向 stderr 输出约定标识行：
       <<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id=<id>>>>

[runner 端]
3. skill subprocess 退出后，runner 拿到 stdout + stderr
4. 解析 stderr 中所有 <<<ADS_AGENT:...>>> 行抽取 artifact_id（可能多行/多个 id）
5. 把这些行**从 stderr 中过滤掉**（不污染日志/用户视图）
6. 对每个 artifact_id 推一条 SSE event:

   event: artifact_updated
   data: {"artifact_id": "<id>", "action": "created"}

[前端]
7. 前端收到 event: artifact_updated:
   - 在对话流插入/刷新一个 artifact 卡片（按 artifact_id 聚合）
   - 调 GET /api/artifacts/{user_id}/{artifact_id}/manifest 拉最新 manifest 渲染
8. 用户也可独立访问"我的工作区"页面（不通过 chat），调 GET /api/artifacts/{user_id}
```

#### 关键变化（vs 早期 runner 扫目录方案）

- artifact API 是**独立资源**，不强绑定 chat 流——用户能在任何时候访问
- runner 只做"消息中转"，不做文件系统扫描（解耦）
- skill 主动通知（stderr 约定行），异步 / 多次写都能通知到
- 前端拉取是 pull 模式（按需 GET 拉最新内容），SSE 只是"刷新触发器"

#### LLM 看到什么

runner 把 stderr 中 `<<<ADS_AGENT:...>>>` 行过滤掉后，把简短摘要拼进给 LLM 的 ToolMessage：

```
<原始 stdout>

[已生成 artifact: 2026-04-30-093015-ka-ctr-anomaly]
```

LLM 在最终 markdown 里引用 artifact 名（如"详细数据见报告 KA-ctr-anomaly"），**不直接吐
artifact 内容**——artifact 内容不进 LLM context。

---

### ⑥ Channel 呈现策略

| Channel | 呈现方式 |
|---|---|
| **WebSSEChannel** | 收到 `event: artifact_updated` → 在对话流插入/刷新 artifact 卡片，点击展开**目录浏览器**（左侧文件树 + 右侧预览：md / ECharts / CSV 表格化 / PPT 内嵌 / 通用文件下载） |
| **CLIChannel** | 打印 artifact 路径 + 顶层文件清单 + entry_files 提示，用户用本地工具打开 |
| **未来 Mobile** | 默认调 zip endpoint 触发下载，或服务端预渲染 PDF |

#### Web ArtifactBrowser 组件最小渲染矩阵

| 文件类型 | 渲染策略 |
|---|---|
| `.md` | marked + DOMPurify 渲染 markdown |
| `*.echarts.json` | ECharts 直接渲染（复用现有 ChartWidget） |
| `.csv` | 解析成表格（前 1000 行） |
| `.json` | 语法高亮 + 折叠（小于 100KB） |
| `.png` / `.jpg` / `.svg` | 图片预览 |
| `.pdf` | 浏览器内置预览或下载 |
| `.pptx` / `.docx` / `.xlsx` | 提供下载（v1 不做内嵌预览，需要 Office Online 或 LibreOffice 转换） |
| 其他 / 大文件 | "下载到本地查看"按钮 |

---

### ⑦ REST API（最小集）

| Method | Path | 用途 |
|---|---|---|
| GET | `/api/artifacts/{user_id}` | 列出该用户全部 artifact（manifest 摘要列表，按 created_at 倒序） |
| GET | `/api/artifacts/{user_id}/{artifact_id}/manifest` | 拿单个 artifact 的 manifest + 完整目录树 |
| GET | `/api/artifacts/{user_id}/{artifact_id}/file/{relative_path}` | 拿单文件（图表 / data / md / .pptx 等） |
| GET | `/api/artifacts/{user_id}/{artifact_id}/zip` | 整包下载 zip |
| DELETE | `/api/artifacts/{user_id}/{artifact_id}` | 用户删除 artifact |

**不做**：rename / move / share / patch（v1 immutable 假设下用不上 patch）

#### Response schema 示例

```json
// GET /api/artifacts/{user_id}
{
  "artifacts": [
    {
      "artifact_id": "2026-04-30-093015-ka-ctr-anomaly",
      "title": "KA 客户 CTR 异常分析",
      "created_at": "2026-04-30T09:30:15+08:00",
      "artifact_type": "report",
      "description": "...",
      "file_count": 8
    }
  ]
}

// GET /api/artifacts/{user_id}/{artifact_id}/manifest
{
  "manifest": {
    "artifact_id": "...",
    "title": "...",
    "created_at": "...",
    "artifact_type": "report",
    "description": "...",
    "entry_files": ["INDEX.md"],
    "related_skills": [...]
  },
  "tree": [
    { "path": "INDEX.md", "size": 1234, "type": "file" },
    { "path": "manifest.yaml", "size": 456, "type": "file" },
    { "path": "insights", "type": "dir", "children": [
      { "path": "insights/summary.md", "size": 2345, "type": "file" }
    ]},
    ...
  ]
}
```

---

### ⑧ 生命周期

**v1**：不自动清理。用户手动删（DELETE endpoint）。

**v2 路线**（不在本 spec 范围）：
- 90 天 retention policy 自动归档
- 归档形态可选：zip 落到冷存储 / 直接删
- 用户 quota 限制（per-user 总大小上限）

---

### ⑨ 安全 / 隔离

#### 路径校验

所有 endpoint 校验：
- `user_id` 格式（无 `/`、无 `..`）
- `artifact_id` 格式（必须匹配 `^\d{4}-\d{2}-\d{2}-\d{6}-[a-z0-9-]+$`）
- `relative_path`（GET file 时）
  - 拼成绝对路径后必须仍在 `data/{user_id}/artifacts/{artifact_id}/` 下（防穿越）
  - 用 `Path(...).resolve()` 后 `is_relative_to()` 校验

#### 权限

v1：复用现有 user_id 透传（mock 阶段限制——任何人输入 user_id 即可访问该用户 artifact）。
SSO/JWT 上线后自然继承新认证模型，artifact API 不需要单独改造。

#### Skill 写入

`ADS_AGENT_ARTIFACT_DIR` 是绝对路径，skill 脚本理论上可以 `cd ..` 出去写其他位置。这是
信任问题——和现有 SKILL.md 包安全模型一致（v1 适合"信任内部用户"场景，外部用户上传需
沙箱，见 [05-known-issues::D7](../../05-known-issues-and-roadmap/README.md)）。

---

### ⑩ SSE Event Schema（修订）

```
event: artifact_updated
data: {"artifact_id": "<id>", "action": "created" | "updated"}
```

**精简**：只通知 id + 动作，详情前端按需拉 manifest。

不在事件里塞 manifest 数据——理由：
- 一致性：前端永远拉最新 manifest（避免事件 schema 与 manifest 漂移）
- 扩展性：以后加新字段不用改 SSE schema
- 流量：SSE 帧小，前端按需才拉

---

## 五、与现有架构整合点

### 新增代码

| 文件 | 用途 |
|---|---|
| `agent/artifact.py` | Artifact 模型（id 生成、manifest 读写、目录树扫描）+ 文件系统操作 |
| `api/artifacts.py` | REST endpoints（list / manifest / file / zip / delete） |
| `frontend/src/components/ArtifactBrowser.vue` | 目录浏览器组件（左树右预览） |
| `frontend/src/components/ArtifactCard.vue` | 对话流里的 artifact 卡片 |
| `frontend/src/components/preview/*.vue` | 各文件类型预览器（MarkdownPreview / EChartsPreview / CsvPreview / ...） |

### 改动现有代码

| 文件 | 改动 |
|---|---|
| `agent/skill_loader.py::run_command` | 调 subprocess 前注入 `ADS_AGENT_ARTIFACT_DIR` / `ADS_AGENT_USER_ID` / `ADS_AGENT_ARTIFACT_ID` env；subprocess 退出后解析 stderr 抽取 `<<<ADS_AGENT:...>>>` 行 |
| `api/channel/runner.py` | 收到 skill 返回的 artifact 通知后调 `channel.send_artifact_updated(id, action)` |
| `api/channel/base.py` | 新增 abstract method `async def send_artifact_updated(artifact_id, action)` |
| `api/channel/web_sse.py` | 实现 send_artifact_updated 推 SSE event |
| `api/channel/cli.py` | 实现 send_artifact_updated 打印路径与文件清单 |
| `agent/user_space.py` | 加 `artifacts_dir` 属性 + 创建目录 |
| `frontend/src/pages/Chat.vue` | 加 artifact_updated SSE handler，渲染 artifact 卡片 |
| `frontend/src/router/index.js` | 加 `/artifacts` 路由（独立工作区页面） |
| `frontend/src/pages/Artifacts.vue` | 新增独立工作区页面（列表 + 浏览） |
| `main.py` | 挂 artifacts router |

### 零改动

- langgraph / deepagents
- SKILL.md 加载机制本身（loader 不感知 artifact，只是注入额外 env）
- Channel ↔ Tool ↔ Skill 三层概念
- 现有 SSE event 类型（plan / token / step / progress / interrupt / done）

---

## 六、关键 Trade-off

| 维度 | 选择 | 代价 |
|---|---|---|
| Artifact 内容**不进 LLM context** | ✅ token 省、checkpoint 不爆 | LLM 不能"读完整报告再总结"——LLM 只看简短工具返回 |
| **immutable** | ✅ 简单可预测 | 用户改文件需要复制新版本（v1 不做） |
| **文件系统起步** | ✅ 部署零依赖 | 多副本部署时要换共享卷 / S3 |
| **skill 用 env 写** | ✅ 任意语言、零 Python 耦合 | skill 文档要清楚写明约定 |
| **manifest 是契约 + 目录自由** | ✅ 灵活（PPT / Excel / Word / 综合报告都行） | skill 作者要正确写 manifest |
| **stderr 通知** | ✅ 解耦 runner 与文件系统、支持异步 / 多次写 | skill 必须懂这个约定（写在 SKILL.md 模板里） |
| **前端按需拉** | ✅ schema 漂移容忍 | 通知后多一次 HTTP 往返 |
| **per-user 共享** | ✅ 跨会话累积，符合分析师工作流 | 长期累积后需要"项目"概念整理（v2） |

---

## 七、用户体验流（端到端示例）

### 场景：分析师跨会话累积 KA 客户分析

```
[第一次会话]
用户："分析昨天 KA 客户 CTR 异常"
→ Agent 调 query-metrics + detect-anomaly + ka-ctr-analyzer skill
→ ka-ctr-analyzer 写到 data/alice/artifacts/2026-04-30-093015-ka-ctr-anomaly/
→ runner 收到 stderr 通知 → 推 event: artifact_updated
→ Chat.vue 在对话流显示 artifact 卡片 + LLM 文字摘要
→ 用户点击卡片 → ArtifactBrowser 展开目录浏览器 → 看 INDEX.md / 各图表 / 数据

[第二天，新会话]
用户回到首页 → 进"我的工作区"页面
→ GET /api/artifacts/alice 返昨天的 artifact
→ 用户点开重新看，或继续问"对比昨天和今天的 KA CTR"
→ Agent 生成新 artifact: 2026-05-01-...-ka-ctr-comparison
→ 老 artifact 不动，新 artifact 独立目录

[一周后]
用户工作区累积 5 份 KA 客户相关 artifact
→ 列表按时间倒序，描述清晰，能找回历史
→ v1 没有"项目"概念，靠时间 + 标题区分；v2 可加 collection 归类
```

### CLI 视角

```bash
$ python -m benchmarks.cli_runner "分析昨天 KA 客户 CTR 异常" --user alice
[计划] 1. 查询指标  2. 检测异常  3. 生成报告
[工具] query-metrics --metrics click,exposure --start-date 2026-04-29 ...
[工具] detect-anomaly --threshold 3sigma ...
[工具] ka-ctr-analyzer --input ... --output $ADS_AGENT_ARTIFACT_DIR

[已生成报告]
  artifact_id: 2026-04-30-093015-ka-ctr-anomaly
  title:       KA 客户 CTR 异常分析
  路径:        data/alice/artifacts/2026-04-30-093015-ka-ctr-anomaly/
  文件清单:
    - INDEX.md
    - manifest.yaml
    - insights/summary.md
    - insights/deep-dive.md
    - data/raw-query.json
    - charts/ctr-trend.echarts.json
    - charts/heatmap.png
  入口:        INDEX.md

[AI 回答]
昨日 KA 客户 CTR 异常分析已生成报告 (2026-04-30-093015-ka-ctr-anomaly)：
- 发现 3 家 KA 客户 CTR 同比下跌 30%+
- 主要集中在华南区移动端
- 详见报告 INDEX.md
```

---

## 八、工作量估计（粗略）

| 模块 | 工时 |
|---|---|
| 后端 `agent/artifact.py` | 0.5 天 |
| 后端 `api/artifacts.py` REST endpoints | 0.5 天 |
| Runner / channel 集成 | 0.5 天 |
| 前端 ArtifactBrowser + 各文件预览器 | 1.5 天 |
| 前端 Chat.vue 集成 + 工作区页面 | 0.5 天 |
| 单元测试 + E2E 验证 | 0.5 天 |
| 文档（更新 docs/02-features 加新章节 + REST API + SSE event 表） | 0.5 天 |
| **总计** | **~4-5 天** |

---

## 九、风险与缓解

| 风险 | 缓解 |
|---|---|
| skill 作者忘了写 manifest.yaml | runner 收到 stderr 通知时校验 manifest 存在，不存在则不推 SSE event 并记日志 |
| skill 多次通知同一 artifact_id（multiple `<<<...>>>` 行）| 推同样 id 的 SSE event，前端去重（按 artifact_id 聚合到同一卡片） |
| 大文件下载阻塞 SSE 连接 | 文件下载走独立 endpoint（GET /file），与 SSE 流无关 |
| 用户大量历史 artifact 拖慢列表 API | 列表 API 加分页（v1 简单 limit/offset） |
| 路径穿越攻击 | Path.resolve() + is_relative_to() 严格校验 |
| skill 写到一半失败导致 manifest 缺失 | 推荐 skill 先写完所有内容，最后 atomic 写 manifest（"manifest 存在 = artifact 完整"约定） |

---

## 十、与已有 known-issues 的关系

新加几条到 [05-known-issues-and-roadmap/README.md](../../05-known-issues-and-roadmap/README.md)：

- **(待实现)** v2 retention policy（自动归档 / 删除 90 天前 artifact）
- **(待实现)** 跨用户 artifact share
- **(待实现)** "项目 / collection" 概念把多个 artifact 归类
- **(待实现)** Office 文件（pptx / docx / xlsx）内嵌预览（需要 LibreOffice 转换或 Office Online）
- **(限制)** v1 manifest 缺失时 artifact 不会被索引——视为"未完成"，靠 skill 作者保证

---

## 十一、未来扩展方向（v2+，不在本 spec 范围）

- **collection 概念**：用户把多个 artifact 归到一个 collection（"KA 客户分析项目"），跨多个时间点的 artifact 在 collection 视图里聚合
- **artifact diff**：对比同一类分析的两次结果（如这周和上周的 KA CTR）
- **artifact 全文搜索**：按 title / description / 文件内容索引
- **共享 / 权限**：跨用户分享 artifact（带权限模型）
- **从 artifact 派生新分析**：用户在 artifact 浏览器里点"基于这份数据再分析"，agent 加载 artifact 数据作为上下文

---

## 文档历史

- 2026-04-30：初版（chiky0123 + Claude，brainstorming session）
