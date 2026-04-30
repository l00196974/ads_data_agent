# 12 用户自定义 Skill 上传（User Skill Upload）

> **前置**：先读 [11-system-skills-catalog.md](./11-system-skills-catalog.md)
> 理解 SKILL.md 包的标准结构。本篇讲"如何让普通用户上传新 skill"。

## 一、设计意图

用户应该能**自己扩展** Agent 能力——不必动框架代码、不必通过运维。

典型场景：
- **数据分析师**有一段自己写的人群画像 / 行为分析脚本（Python / Node），想让 Agent 在对话里直接调用
- **营销专员**有一段定制周报生成脚本，想让 Agent 一句话生成周报
- **运维**有一段巡检脚本（异常活动批量诊断），想集成到对话流

---

## 二、流程

```
[1] 用户在前端 Skill 面板点"+ 添加"
       │
       ▼
[2] 选 .zip 文件（含单一顶层目录 + SKILL.md）
       │
       ▼
[3] POST /api/skills/{user_id}/upload  multipart/form-data
       │
       ▼
[4] 后端校验 zip 结构 + 安全 → 解压到 data/{user_id}/skills/<dir>/
       │
       ▼
[5] 下次 chat 该 user 的 agent build 时，loader 会扫到这个 skill 并注入
       │
       ▼
[6] LLM 在 SKILL.md 文档段看到新命令，可以调用
```

---

## 三、上传 endpoint

`api/skills.py:29-88::upload_skill`

### 3.1 请求

`multipart/form-data`，单个 `file` 字段，`.zip` 文件，≤50MB。

### 3.2 zip 内容要求

```
my-skill.zip
└── my-skill/                 ← **单一顶层目录**
    ├── SKILL.md              ← 必需
    ├── package.json          ← 可选
    └── bin/
        └── ...
```

约束：
- **单一顶层目录**：zip 解压后只能有一个顶层条目（避免污染 `data/{user_id}/skills/`）
- **必须有 SKILL.md**：顶层目录里必须存在 `SKILL.md`
- **禁止路径穿越**：所有条目名不能含 `..` 或以 `/` 开头

### 3.3 安全校验代码

```python
# api/skills.py:58-77
for name in namelist:
    if name.startswith("/") or ".." in Path(name).parts:
        raise HTTPException(400, f"非法路径：{name}")

top_components = set()
for name in namelist:
    parts = name.split("/")
    if parts[0]:
        top_components.add(parts[0])
if len(top_components) != 1:
    raise HTTPException(400, f"zip 应包含单一顶层目录，但发现 {len(top_components)} 个")
skill_dir_name = top_components.pop()

skill_md_entry = f"{skill_dir_name}/SKILL.md"
if skill_md_entry not in namelist:
    raise HTTPException(400, f"找不到 {skill_md_entry}")
```

### 3.4 命名冲突

如果用户已有同名 skill：

```python
target = us.skills_dir / skill_dir_name
if target.exists():
    shutil.rmtree(target)         # 直接覆盖
zf.extractall(us.skills_dir)
```

→ **覆盖语义**：用户的"再次上传 my-skill"等价于"替换 my-skill"。
原 skill 数据 / config 全部清空。

如果用户名跟系统 skill 同名：见 [11-system-skills-catalog.md::3.3](./11-system-skills-catalog.md)
（user skill 在加载阶段优先）。

---

## 四、列出 endpoint

`api/skills.py:20-26::list_skills`

```python
@router.get("/{user_id}")
async def list_skills(user_id: str):
    us = UserSpace(user_id, cfg.persistence.data_dir)
    pkg = load_md_skills(cfg.skills.md_dir, us.skills_dir)
    system_md = [s for s in pkg.summaries if s.get("source") == "system"]
    user_md = [s for s in pkg.summaries if s.get("source") == "user"]
    return {"system": system_md, "user": user_md}
```

返回 system + user 分组的 skill 摘要。前端 Skill 面板按这个数据渲染。

详见 [03-api-reference/rest-endpoints.md::6](../03-api-reference/rest-endpoints.md)。

---

## 五、删除 endpoint

`api/skills.py:91-101::delete_skill`

```python
@router.delete("/{user_id}/{skill_name}")
async def delete_skill(user_id: str, skill_name: str):
    if "/" in skill_name or ".." in skill_name:
        raise HTTPException(400, "非法 skill_name")
    us = UserSpace(user_id, cfg.persistence.data_dir)
    target = us.skills_dir / skill_name
    if not target.exists() or not target.is_dir():
        raise HTTPException(404, "skill 不存在")
    shutil.rmtree(target)
```

只能删用户自己的 skill（路径限制在 `data/{user_id}/skills/`）。
删 system skill？做不到——`md_dir` 路径不在 user 范围内。

---

## 六、前端集成

`frontend/src/pages/Chat.vue` 的 Skill 面板：

```html
<aside class="skill-panel">
  <div class="panel-title">已注册能力</div>
  
  <div class="skill-section">
    <p class="section-label">系统 Skills</p>
    <div v-for="s in systemSkills" :key="s.name" class="skill-item">
      <span class="skill-dot">●</span>{{ s.name }}
    </div>
  </div>
  
  <div class="skill-section">
    <p class="section-label">我的 Skills</p>
    <div v-for="s in userSkills" :key="s.name" class="skill-item user-skill-item">
      <span class="skill-dot">●</span>
      <span class="skill-name">{{ s.name }}</span>
      <button class="skill-delete" @click="deleteSkill(s.name)">×</button>
    </div>
    <div class="skill-item add-btn" @click="triggerUpload">+ 添加</div>
    <input ref="fileInputRef" type="file" accept=".zip" style="display:none" @change="onFileChange"/>
  </div>
</aside>
```

交互：
- 鼠标 hover user skill 显示删除按钮
- 点 "+ 添加" 弹文件选择器
- 选 .zip 后自动 POST 上传 + 刷新 skill 列表

---

## 七、为什么用 zip 上传而不是单文件

考虑过让用户上传单个 SKILL.md 文件，但 SKILL.md 包是**多文件结构**：
- 至少 SKILL.md
- 通常有 `package.json` + `bin/` + 可能有 `node_modules/`
- Python 包可能有 `requirements.txt` + 多个 `.py`

zip 是天然的"目录打包"格式，前端文件 input 一次选一个 zip 也比"一次选多个文件"友好。

---

## 八、当前限制 / 安全考虑

### 8.1 没有沙箱

上传的 zip 解压即原样落盘。**bin 脚本**会被 subprocess 直接执行（`run_command` 调用时），
脚本能：
- 访问任何后端进程能访问的资源（数据库密码、env vars 等）
- 网络请求（联外网拉数据 / 回传）
- 写盘到任意路径（脚本本身没沙箱限制）

→ **当前实现适合"信任内部用户"场景**。如果要让公开互联网用户上传 skill，必须加：
- Docker 容器隔离每个 skill 执行
- 文件系统 chroot
- 网络 ACL
- 资源限额

详见 [05-known-issues](../05-known-issues-and-roadmap/README.md::"安全考虑")。

### 8.2 没有依赖管理

Node skill 上传时如果带 `node_modules/`，zip 体积会爆（容易超 50MB）。
如果不带，需要服务端在解压后跑 `npm install`——但当前没实现。

实际经验：直接打包含 `node_modules/` 的 zip 上传——50MB 限制对小型 Node skill 够用。

### 8.3 用户级 skill 数量

当前没有 per-user skill 数量限制，理论上一个用户能堆满磁盘。运维侧应配监控 +
（未来）加配额。

### 8.4 重启依赖

加 / 删 skill 后**必须重启后端**才让 LLM 看到 SKILL.md 文档段更新——SKILL.md 加载是
进程启动时一次性的（`load_md_skills` 在每次 chat 请求都调，但 prompt 已经缓存到 LLM
上下文里）。

实际上 `_make_build_fn(uid)` 每次都重新调 `load_md_skills`，所以**新对话**的 prompt 是最新的；
但**已经在跑的对话**仍用旧 prompt——这种半新半旧的状态可能误导用户。建议加 skill 后
让用户开新对话。

---

## 九、用户上传 vs 系统 skill 的边界

| 维度 | 系统 skill | 用户 skill |
|---|---|---|
| 位置 | `skills.md_dir`（默认 `./skills`） | `data/{user_id}/skills/` |
| 注入路径 | 框架启动扫一次 | 每次 build_agent 扫一次 |
| 可见范围 | 所有用户 | 仅该 user_id |
| 加 / 删方式 | 文件系统操作（运维） | HTTP API（用户自助） |
| 信任级别 | 已审核 | 未审核（按 8.1 警示） |

---

## 设计决策记录（ADR）

### ADR-018：用户 skill 走与系统 skill 完全相同的 SKILL.md 协议

**Context**：早期想让用户 skill 用更简化的格式（如单个 Python 函数提交）。`POST /api/skills/{user_id}` 的 `SkillCreateRequest`（name + description + code）就是这种早期设计的残留。

但这种"专用格式"导致两套加载逻辑、两套 prompt 拼装——维护成本翻倍且容易 drift。

**Decision**：用户 skill 与系统 skill 走完全相同的 SKILL.md 协议。loader 的入口
`load_md_skills(system_dir, user_dir)` 一次扫两个目录，加载逻辑零分支。

`POST /api/skills/{user_id}` 的简化接口标记为遗留，**不再注入到 agent**——保留只为
向后兼容（避免破坏旧前端代码），后续可移除。

**Consequences**：
- ✅ 单一加载路径，行为可预测
- ✅ 用户上传的 skill 跟系统 skill 享同等能力（多语言 / 多子命令 / config 目录等）
- ⚠️ 用户上传门槛高了一点——必须打包 zip 而不是糙写一段代码
- ⚠️ 旧 `POST /api/skills/{user_id}` 仍然能 200 OK 但实际无效——这是 stale endpoint
  的常见问题，应在文档明确标注（已在 [rest-endpoints.md::9](../03-api-reference/rest-endpoints.md) 标注）
