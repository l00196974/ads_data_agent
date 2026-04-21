# Ads Data Agent 实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 构建华为广告数据分析 Agent 平台，支持多用户隔离、流式执行、Human-in-the-Loop 中断、图表可视化。

**Architecture:** FastAPI 后端通过 deepAgent SDK 创建主 Agent，以 `thread_id=user_id` 实现对话隔离，文件系统按 `data/{user_id}/` 隔离 Agents.md/Skills；Vue 3 前端通过 SSE 接收流式事件，渲染步骤进展、文字、图表混排。

**Tech Stack:** Python 3.11+, deepAgent, LangGraph, FastAPI, Vue 3 (Vite), ECharts, uvicorn

---

## Task 1：项目骨架 & 依赖

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `config.yaml`
- Create: `.gitignore`

**Step 1: 创建 pyproject.toml**

```toml
[project]
name = "ads-data-agent"
version = "0.1.0"
requires-python = ">=3.11"
```

**Step 2: 创建 requirements.txt**

```
deepagents
langgraph
langgraph-checkpoint
fastapi
uvicorn[standard]
python-dotenv
pyyaml
pydantic>=2.0
```

**Step 3: 创建 .env.example**

```bash
# LLM
ANTHROPIC_API_KEY=sk-ant-xxx
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-4-6

# 可观测性（可选）
LANGSMITH_TRACING=false
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=ads-data-agent

# 服务
SECRET_KEY=change-me-in-production
```

**Step 4: 创建 config.yaml**

```yaml
server:
  host: 0.0.0.0
  port: 8000
  cors_origins:
    - "http://localhost:5173"
    - "http://localhost:8000"

agent:
  max_iterations: 20
  timeout_seconds: 120
  interrupt_on:
    - delete_adgroups
    - modify_budget
    - pause_campaign

persistence:
  type: local
  data_dir: ./data

skills:
  system_dir: ./skills/system

sub_agents:
  system_dir: ./sub_agents/system
```

**Step 5: 创建 .gitignore**

```
.env
.env.*
!.env.example
data/
__pycache__/
*.pyc
.venv/
node_modules/
dist/
```

**Step 6: 安装依赖**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

**Step 7: 验证安装**

```bash
python -c "from deepagents import create_deep_agent; print('deepagents OK')"
```

Expected: `deepagents OK`

**Step 8: Commit**

```bash
git init
git add pyproject.toml requirements.txt .env.example config.yaml .gitignore
git commit -m "chore: project skeleton and dependencies"
```

---

## Task 2：配置加载模块

**Files:**
- Create: `agent/config.py`
- Create: `tests/test_config.py`

**Step 1: 写测试**

```python
# tests/test_config.py
import os
import pytest
from agent.config import load_config, AppConfig

def test_load_config_defaults():
    cfg = load_config("config.yaml")
    assert cfg.server.port == 8000
    assert cfg.agent.max_iterations == 20
    assert "delete_adgroups" in cfg.agent.interrupt_on

def test_env_override(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = load_config("config.yaml")
    assert cfg.llm.api_key == "test-key"
```

**Step 2: 运行测试确认失败**

```bash
pytest tests/test_config.py -v
```

Expected: FAIL with `ModuleNotFoundError`

**Step 3: 实现 agent/config.py**

```python
import os
import yaml
from pathlib import Path
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = []

class AgentConfig(BaseModel):
    max_iterations: int = 20
    timeout_seconds: int = 120
    interrupt_on: list[str] = []

class LLMConfig(BaseModel):
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    api_key: str = ""

class PersistenceConfig(BaseModel):
    type: str = "local"
    data_dir: str = "./data"

class AppConfig(BaseModel):
    server: ServerConfig = ServerConfig()
    agent: AgentConfig = AgentConfig()
    llm: LLMConfig = LLMConfig()
    persistence: PersistenceConfig = PersistenceConfig()

def load_config(path: str = "config.yaml") -> AppConfig:
    raw = {}
    if Path(path).exists():
        with open(path) as f:
            raw = yaml.safe_load(f) or {}
    
    llm_raw = {
        "provider": os.getenv("LLM_PROVIDER", "anthropic"),
        "model": os.getenv("LLM_MODEL", "claude-sonnet-4-6"),
        "api_key": os.getenv("ANTHROPIC_API_KEY", ""),
    }
    raw["llm"] = llm_raw
    return AppConfig(**raw)
```

**Step 4: 运行测试确认通过**

```bash
pytest tests/test_config.py -v
```

Expected: 2 passed

**Step 5: Commit**

```bash
git add agent/config.py tests/test_config.py
git commit -m "feat: config loading with pydantic and env override"
```

---

## Task 3：用户数据目录 & Agents.md 隔离

**Files:**
- Create: `agent/user_space.py`
- Create: `config/system_agents.md`
- Create: `tests/test_user_space.py`

**Step 1: 写测试**

```python
# tests/test_user_space.py
import pytest
from pathlib import Path
from agent.user_space import UserSpace

def test_user_dirs_created(tmp_path):
    us = UserSpace("user_001", data_dir=str(tmp_path))
    assert us.data_dir.exists()
    assert us.skills_dir.exists()
    assert us.memory_dir.exists()

def test_agents_md_merged(tmp_path):
    us = UserSpace("user_001", data_dir=str(tmp_path))
    # 写入用户自定义 agents.md
    us.agents_md_path.write_text("# 用户自定义指令\n- 始终用中文回答")
    content = us.get_agents_md(system_md_path="config/system_agents.md")
    assert "华为广告数据分析助手" in content   # 来自系统模板
    assert "始终用中文回答" in content          # 来自用户自定义

def test_different_users_isolated(tmp_path):
    us1 = UserSpace("user_001", data_dir=str(tmp_path))
    us2 = UserSpace("user_002", data_dir=str(tmp_path))
    assert us1.data_dir != us2.data_dir
```

**Step 2: 运行测试确认失败**

```bash
pytest tests/test_user_space.py -v
```

**Step 3: 创建 config/system_agents.md**

```markdown
# 华为广告数据分析助手

你是华为广告平台的专业数据分析助手，服务于营销和运维人员。

## 能力
- 广告活动报表查询与分析
- 预算使用分析与优化建议
- 投放异常检测与告警
- 数据可视化图表生成

## 原则
- 始终使用中文回答
- 数据分析结论需给出具体数字支撑
- 涉及资金操作时主动提示风险
```

**Step 4: 实现 agent/user_space.py**

```python
from pathlib import Path

class UserSpace:
    def __init__(self, user_id: str, data_dir: str = "./data"):
        self.user_id = user_id
        self.data_dir = Path(data_dir) / user_id
        self.skills_dir = self.data_dir / "skills"
        self.memory_dir = self.data_dir / "memory"
        self.agents_md_path = self.data_dir / "agents.md"
        self._ensure_dirs()

    def _ensure_dirs(self):
        for d in [self.data_dir, self.skills_dir, self.memory_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def get_agents_md(self, system_md_path: str = "config/system_agents.md") -> str:
        parts = []
        sys_path = Path(system_md_path)
        if sys_path.exists():
            parts.append(sys_path.read_text())
        if self.agents_md_path.exists():
            user_content = self.agents_md_path.read_text().strip()
            if user_content:
                parts.append("\n\n## 用户自定义指令\n" + user_content)
        return "\n\n".join(parts)
```

**Step 5: 运行测试确认通过**

```bash
pytest tests/test_user_space.py -v
```

Expected: 3 passed

**Step 6: Commit**

```bash
git add agent/user_space.py config/system_agents.md tests/test_user_space.py
git commit -m "feat: per-user data directory and agents.md isolation"
```

---

## Task 4：系统预置 Skills

**Files:**
- Create: `skills/system/campaign_report.py`
- Create: `skills/system/budget_analysis.py`
- Create: `skills/system/anomaly_detection.py`
- Create: `skills/system/chart_skill.py`
- Create: `skills/system/__init__.py`
- Create: `tests/test_skills.py`

**Step 1: 写测试**

```python
# tests/test_skills.py
from skills.system.campaign_report import query_campaign_report
from skills.system.budget_analysis import analyze_budget
from skills.system.anomaly_detection import detect_anomaly
from skills.system.chart_skill import build_chart

def test_campaign_report_returns_data():
    result = query_campaign_report(date="2026-04-20", user_id="user_001")
    assert "campaigns" in result
    assert isinstance(result["campaigns"], list)

def test_budget_analysis_returns_summary():
    result = analyze_budget(user_id="user_001")
    assert "total_budget" in result
    assert "spend_rate" in result

def test_anomaly_detection_returns_list():
    result = detect_anomaly(date="2026-04-20", region="华南", user_id="user_001")
    assert "anomalies" in result
    assert isinstance(result["anomalies"], list)

def test_build_chart_line():
    result = build_chart(
        chart_type="line",
        title="测试折线图",
        x_data=["00:00", "01:00", "02:00"],
        series=[{"name": "点击量", "data": [10, 20, 15]}]
    )
    assert result["type"] == "line"
    assert result["title"] == "测试折线图"
```

**Step 2: 运行测试确认失败**

```bash
pytest tests/test_skills.py -v
```

**Step 3: 实现 skills/system/campaign_report.py（Mock 数据）**

```python
def query_campaign_report(date: str, user_id: str) -> dict:
    """查询广告活动报表（Mock 数据）"""
    return {
        "date": date,
        "user_id": user_id,
        "campaigns": [
            {"name": "华南-移动端-关键词A", "impressions": 12400, "clicks": 380, "ctr": 0.031, "spend": 1240.0},
            {"name": "华南-PC端-品牌词", "impressions": 8200, "clicks": 210, "ctr": 0.026, "spend": 820.0},
        ]
    }
```

**Step 4: 实现 skills/system/budget_analysis.py**

```python
def analyze_budget(user_id: str) -> dict:
    """分析预算使用情况（Mock 数据）"""
    return {
        "user_id": user_id,
        "total_budget": 100000.0,
        "total_spend": 67500.0,
        "spend_rate": 0.675,
        "remaining": 32500.0,
        "warning": spend_rate > 0.8
    }

# Fix: 直接计算
def analyze_budget(user_id: str) -> dict:
    total_budget = 100000.0
    total_spend = 67500.0
    spend_rate = total_spend / total_budget
    return {
        "user_id": user_id,
        "total_budget": total_budget,
        "total_spend": total_spend,
        "spend_rate": spend_rate,
        "remaining": total_budget - total_spend,
        "warning": spend_rate > 0.8,
    }
```

**Step 5: 实现 skills/system/anomaly_detection.py**

```python
def detect_anomaly(date: str, region: str, user_id: str) -> dict:
    """检测投放异常（Mock 数据）"""
    return {
        "date": date,
        "region": region,
        "user_id": user_id,
        "anomalies": [
            {"campaign": "华南-移动端-关键词A", "metric": "CTR", "change": -0.42, "severity": "high"},
            {"campaign": "华南-PC端-品牌词", "metric": "CPC", "change": 0.35, "severity": "medium"},
        ]
    }
```

**Step 6: 实现 skills/system/chart_skill.py**

```python
def build_chart(chart_type: str, title: str, x_data: list, series: list) -> dict:
    """生成 ECharts 图表配置"""
    return {
        "type": chart_type,
        "title": title,
        "x": x_data,
        "series": series,
    }
```

**Step 7: 创建 skills/system/__init__.py**

```python
from .campaign_report import query_campaign_report
from .budget_analysis import analyze_budget
from .anomaly_detection import detect_anomaly
from .chart_skill import build_chart

SYSTEM_SKILLS = [
    query_campaign_report,
    analyze_budget,
    detect_anomaly,
    build_chart,
]
```

**Step 8: 运行测试确认通过**

```bash
pytest tests/test_skills.py -v
```

Expected: 4 passed

**Step 9: Commit**

```bash
git add skills/ tests/test_skills.py
git commit -m "feat: system preset skills with mock data"
```

---

## Task 5：Agent 核心 & 会话管理

**Files:**
- Create: `agent/core.py`
- Create: `agent/session.py`
- Create: `tests/test_agent_core.py`

**Step 1: 写测试**

```python
# tests/test_agent_core.py
import pytest
from unittest.mock import patch, MagicMock
from agent.core import build_agent
from agent.session import SessionManager

def test_build_agent_returns_agent():
    with patch("agent.core.create_deep_agent") as mock_create:
        mock_create.return_value = MagicMock()
        agent = build_agent(
            user_id="user_001",
            system_prompt="测试提示词",
            skills=[],
            interrupt_on=[]
        )
        assert agent is not None
        mock_create.assert_called_once()

def test_session_manager_thread_id():
    sm = SessionManager()
    tid = sm.get_thread_id("user_001")
    assert tid == "user_001"
    # 同一用户始终返回同一 thread_id
    assert sm.get_thread_id("user_001") == tid

def test_session_manager_config():
    sm = SessionManager()
    cfg = sm.get_config("user_001")
    assert cfg["configurable"]["thread_id"] == "user_001"
```

**Step 2: 运行测试确认失败**

```bash
pytest tests/test_agent_core.py -v
```

**Step 3: 实现 agent/session.py**

```python
class SessionManager:
    def get_thread_id(self, user_id: str) -> str:
        return user_id

    def get_config(self, user_id: str) -> dict:
        return {"configurable": {"thread_id": self.get_thread_id(user_id)}}
```

**Step 4: 实现 agent/core.py**

```python
import os
from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
from agent.config import AppConfig, load_config

_store = InMemoryStore()
_checkpointer = MemorySaver()

def build_agent(
    user_id: str,
    system_prompt: str,
    skills: list,
    interrupt_on: list,
    cfg: AppConfig = None,
):
    if cfg is None:
        cfg = load_config()

    model_str = f"{cfg.llm.provider}:{cfg.llm.model}"

    interrupt_tools = {tool_name: True for tool_name in interrupt_on}

    agent = create_deep_agent(
        model=model_str,
        tools=skills,
        system_prompt=system_prompt,
        interrupt_on=interrupt_tools if interrupt_tools else None,
        checkpointer=_checkpointer,
        store=_store,
    )
    return agent
```

**Step 5: 运行测试确认通过**

```bash
pytest tests/test_agent_core.py -v
```

Expected: 3 passed

**Step 6: Commit**

```bash
git add agent/core.py agent/session.py tests/test_agent_core.py
git commit -m "feat: agent core builder and session manager"
```

---

## Task 6：FastAPI 后端 & SSE 流式接口

**Files:**
- Create: `main.py`
- Create: `api/chat.py`
- Create: `api/skills.py`
- Create: `api/models.py`
- Create: `tests/test_api.py`

**Step 1: 创建 api/models.py**

```python
from pydantic import BaseModel

class ChatRequest(BaseModel):
    message: str

class ConfirmRequest(BaseModel):
    action: str  # "approve" | "cancel"

class AppendRequest(BaseModel):
    message: str

class SkillCreateRequest(BaseModel):
    name: str
    description: str
    code: str  # Python 函数代码字符串（后续支持）
```

**Step 2: 实现 api/chat.py（核心 SSE 流）**

```python
import json
import asyncio
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from api.models import ChatRequest, ConfirmRequest, AppendRequest
from agent.core import build_agent, _checkpointer
from agent.session import SessionManager
from agent.user_space import UserSpace
from agent.config import load_config
from skills.system import SYSTEM_SKILLS

router = APIRouter()
session_mgr = SessionManager()
cfg = load_config()

# 全局保存 interrupt 状态
_interrupted: dict[str, bool] = {}

async def _stream_agent(user_id: str, message: str):
    us = UserSpace(user_id, cfg.persistence.data_dir)
    system_prompt = us.get_agents_md()
    agent = build_agent(
        user_id=user_id,
        system_prompt=system_prompt,
        skills=SYSTEM_SKILLS,
        interrupt_on=cfg.agent.interrupt_on,
        cfg=cfg,
    )
    langgraph_config = session_mgr.get_config(user_id)
    input_msg = {"messages": [HumanMessage(content=message)]}

    async for event in agent.astream_events(input_msg, config=langgraph_config, version="v2"):
        kind = event["event"]

        if kind == "on_tool_start":
            data = {"type": "tool_start", "tool": event["name"], "msg": f"调用 {event['name']}..."}
            yield f"event: step\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        elif kind == "on_tool_end":
            data = {"type": "tool_end", "tool": event["name"], "msg": f"{event['name']} 执行完成"}
            # 检测图表数据
            output = event.get("data", {}).get("output", {})
            if isinstance(output, dict) and "type" in output and output.get("type") in ["line","bar","pie","scatter","heatmap"]:
                yield f"event: chart\ndata: {json.dumps(output, ensure_ascii=False)}\n\n"
            else:
                yield f"event: step\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        elif kind == "on_chat_model_stream":
            chunk = event["data"]["chunk"]
            if hasattr(chunk, "content") and chunk.content:
                data = {"token": chunk.content}
                yield f"event: token\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"

        elif kind == "on_checkpoint" and event.get("data", {}).get("config", {}).get("configurable", {}).get("__interrupt__"):
            # 触发 interrupt
            interrupt_data = event["data"]
            _interrupted[user_id] = True
            data = {
                "type": "confirm_required",
                "action": interrupt_data.get("action", "unknown"),
                "msg": f"即将执行: {interrupt_data.get('action', '操作')}，请确认",
                "preview": interrupt_data.get("preview", [])
            }
            yield f"event: interrupt\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            return

    yield f"event: done\ndata: {json.dumps({'status': 'complete'}, ensure_ascii=False)}\n\n"


@router.post("/{user_id}")
async def chat(user_id: str, req: ChatRequest):
    return StreamingResponse(
        _stream_agent(user_id, req.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{user_id}/confirm")
async def confirm(user_id: str, req: ConfirmRequest):
    us = UserSpace(user_id, cfg.persistence.data_dir)
    system_prompt = us.get_agents_md()
    agent = build_agent(
        user_id=user_id,
        system_prompt=system_prompt,
        skills=SYSTEM_SKILLS,
        interrupt_on=cfg.agent.interrupt_on,
        cfg=cfg,
    )
    langgraph_config = session_mgr.get_config(user_id)
    if req.action == "approve":
        cmd = Command(resume=True)
    else:
        cmd = Command(resume=False)
    result = agent.invoke(cmd, config=langgraph_config)
    _interrupted.pop(user_id, None)
    return {"status": "ok", "action": req.action}


@router.post("/{user_id}/cancel")
async def cancel(user_id: str):
    _interrupted[user_id] = False
    return {"status": "cancelled"}


@router.post("/{user_id}/append")
async def append_message(user_id: str, req: AppendRequest):
    # 注入补充上下文到下一轮（通过 thread state 追加）
    us = UserSpace(user_id, cfg.persistence.data_dir)
    system_prompt = us.get_agents_md()
    agent = build_agent(
        user_id=user_id,
        system_prompt=system_prompt,
        skills=SYSTEM_SKILLS,
        interrupt_on=cfg.agent.interrupt_on,
        cfg=cfg,
    )
    langgraph_config = session_mgr.get_config(user_id)
    cmd = Command(resume={"additional_context": req.message})
    agent.invoke(cmd, config=langgraph_config)
    return {"status": "appended", "message": req.message}
```

**Step 3: 实现 api/skills.py**

```python
from fastapi import APIRouter
from api.models import SkillCreateRequest
from agent.user_space import UserSpace
from agent.config import load_config
from skills.system import SYSTEM_SKILLS

router = APIRouter()
cfg = load_config()

@router.get("/{user_id}")
async def list_skills(user_id: str):
    system = [{"name": f.__name__, "description": f.__doc__ or "", "type": "system"} for f in SYSTEM_SKILLS]
    us = UserSpace(user_id, cfg.persistence.data_dir)
    user_skills_dir = us.skills_dir
    user = []
    for f in user_skills_dir.glob("*.py"):
        user.append({"name": f.stem, "description": "", "type": "user"})
    return {"system": system, "user": user}

@router.post("/{user_id}")
async def create_skill(user_id: str, req: SkillCreateRequest):
    us = UserSpace(user_id, cfg.persistence.data_dir)
    skill_file = us.skills_dir / f"{req.name}.py"
    skill_file.write_text(f'"""{req.description}"""\n\n{req.code}')
    return {"status": "created", "name": req.name}
```

**Step 4: 实现 main.py**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path
from agent.config import load_config
from api import chat, skills

cfg = load_config()

app = FastAPI(title="Ads Data Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.server.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(skills.router, prefix="/api/skills", tags=["skills"])

# 前端静态文件（构建后）
frontend_dist = Path("frontend/dist")
if frontend_dist.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        return FileResponse(str(frontend_dist / "index.html"))
```

**Step 5: 启动测试**

```bash
cp .env.example .env
# 填入真实 ANTHROPIC_API_KEY
uvicorn main:app --reload --port 8000
```

访问 http://localhost:8000/docs 验证接口文档正常显示。

**Step 6: Commit**

```bash
git add main.py api/ tests/
git commit -m "feat: FastAPI backend with SSE streaming and HitL endpoints"
```

---

## Task 7：Vue 3 前端骨架

**Files:**
- Create: `frontend/` (Vite + Vue 3 项目)

**Step 1: 初始化 Vue 3 项目**

```bash
cd /home/linxiankun/ads_data_agent
npm create vite@latest frontend -- --template vue
cd frontend
npm install
npm install echarts axios
```

**Step 2: 创建 frontend/src/utils/sse.js**

```javascript
export class SSEClient {
  constructor(url, handlers) {
    this.url = url
    this.handlers = handlers  // { step, token, chart, interrupt, done }
    this.abortController = null
  }

  async connect(payload) {
    this.abortController = new AbortController()
    const resp = await fetch(this.url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: this.abortController.signal,
    })

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop()

      let eventType = 'message'
      for (const line of lines) {
        if (line.startsWith('event:')) {
          eventType = line.slice(6).trim()
        } else if (line.startsWith('data:')) {
          const data = JSON.parse(line.slice(5).trim())
          this.handlers[eventType]?.(data)
          eventType = 'message'
        }
      }
    }
  }

  abort() {
    this.abortController?.abort()
  }
}
```

**Step 3: 创建 frontend/src/pages/Login.vue**

```vue
<template>
  <div class="login-container">
    <div class="login-card">
      <h1>华为广告数据助手</h1>
      <p class="subtitle">请输入您的账号</p>
      <input
        v-model="userId"
        placeholder="请输入 user_id"
        @keyup.enter="login"
      />
      <button @click="login" :disabled="!userId">进入</button>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'

const router = useRouter()
const userId = ref('')

function login() {
  if (userId.value.trim()) {
    localStorage.setItem('user_id', userId.value.trim())
    router.push('/chat')
  }
}
</script>
```

**Step 4: 创建 frontend/src/components/ChartWidget.vue**

```vue
<template>
  <div ref="chartEl" class="chart-widget"></div>
</template>

<script setup>
import { ref, onMounted, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({ config: Object })
const chartEl = ref(null)
let chart = null

onMounted(() => {
  chart = echarts.init(chartEl.value)
  renderChart()
})

watch(() => props.config, renderChart)

function renderChart() {
  if (!chart || !props.config) return
  const { type, title, x, series } = props.config
  chart.setOption({
    title: { text: title },
    tooltip: {},
    xAxis: type !== 'pie' ? { data: x } : undefined,
    yAxis: type !== 'pie' ? {} : undefined,
    series: series.map(s => ({ ...s, type })),
  })
}
</script>

<style scoped>
.chart-widget { width: 100%; height: 300px; }
</style>
```

**Step 5: 创建 frontend/src/components/ConfirmDialog.vue**

```vue
<template>
  <div v-if="visible" class="dialog-overlay">
    <div class="dialog-card">
      <h3>⚠️ 需要确认</h3>
      <p>{{ message }}</p>
      <ul v-if="preview.length">
        <li v-for="item in preview" :key="item">{{ item }}</li>
      </ul>
      <div class="actions">
        <button class="btn-danger" @click="$emit('confirm', 'approve')">确认执行</button>
        <button class="btn-cancel" @click="$emit('confirm', 'cancel')">取消</button>
      </div>
    </div>
  </div>
</template>

<script setup>
defineProps({ visible: Boolean, message: String, preview: Array })
defineEmits(['confirm'])
</script>
```

**Step 6: 创建 frontend/src/pages/Chat.vue（主界面）**

```vue
<template>
  <div class="chat-layout">
    <!-- 顶栏 -->
    <header>
      <span>华为广告数据助手</span>
      <span class="user-tag">{{ userId }}</span>
      <button @click="logout">退出</button>
    </header>

    <div class="main">
      <!-- 左侧 Skills 面板 -->
      <aside class="skill-panel">
        <h4>已注册能力</h4>
        <div class="skill-section">
          <p class="label">系统 Skills</p>
          <div v-for="s in systemSkills" :key="s.name" class="skill-item">{{ s.name }}</div>
        </div>
        <div class="skill-section">
          <p class="label">我的 Skills</p>
          <div v-for="s in userSkills" :key="s.name" class="skill-item">{{ s.name }}</div>
          <button class="btn-add">+ 添加</button>
        </div>
      </aside>

      <!-- 右侧主区 -->
      <div class="chat-area">
        <!-- 步骤进展 -->
        <div v-if="steps.length" class="step-tracker">
          <div v-for="(step, i) in steps" :key="i" class="step-item">
            <span>{{ step.done ? '✅' : '⏳' }}</span>
            <span>{{ step.msg }}</span>
          </div>
        </div>

        <!-- 消息列表 -->
        <div class="messages" ref="messagesEl">
          <div v-for="(msg, i) in messages" :key="i" :class="['bubble', msg.role]">
            <template v-if="msg.type === 'text'">{{ msg.content }}</template>
            <ChartWidget v-else-if="msg.type === 'chart'" :config="msg.content" />
          </div>
        </div>

        <!-- 输入区 -->
        <div class="input-area">
          <input
            v-model="inputText"
            placeholder="输入问题..."
            @keyup.enter="sendMessage"
            :disabled="isInterrupted"
          />
          <button @click="sendMessage" :disabled="isInterrupted || !inputText">发送</button>
          <button v-if="isStreaming" @click="stopStream" class="btn-stop">停止</button>
        </div>
      </div>
    </div>

    <!-- 确认弹窗 -->
    <ConfirmDialog
      :visible="isInterrupted"
      :message="interruptMsg"
      :preview="interruptPreview"
      @confirm="handleConfirm"
    />
  </div>
</template>

<script setup>
import { ref, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { SSEClient } from '../utils/sse.js'
import ChartWidget from '../components/ChartWidget.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'

const router = useRouter()
const userId = localStorage.getItem('user_id') || ''
const messages = ref([])
const steps = ref([])
const inputText = ref('')
const isStreaming = ref(false)
const isInterrupted = ref(false)
const interruptMsg = ref('')
const interruptPreview = ref([])
const systemSkills = ref([])
const userSkills = ref([])
const messagesEl = ref(null)
let sseClient = null
let currentToken = ''

onMounted(async () => {
  const resp = await fetch(`/api/skills/${userId}`)
  const data = await resp.json()
  systemSkills.value = data.system
  userSkills.value = data.user
})

async function sendMessage() {
  if (!inputText.value.trim()) return
  const msg = inputText.value.trim()
  inputText.value = ''
  messages.value.push({ role: 'user', type: 'text', content: msg })
  steps.value = []
  currentToken = ''
  isStreaming.value = true

  // 占位助手消息
  messages.value.push({ role: 'assistant', type: 'text', content: '' })
  const assistantIdx = messages.value.length - 1

  sseClient = new SSEClient(`/api/chat/${userId}`, {
    step: (data) => {
      steps.value.push({ msg: data.msg, done: data.type === 'tool_end' })
    },
    token: (data) => {
      currentToken += data.token
      messages.value[assistantIdx].content = currentToken
      scrollToBottom()
    },
    chart: (data) => {
      messages.value.push({ role: 'assistant', type: 'chart', content: data })
      scrollToBottom()
    },
    interrupt: (data) => {
      isInterrupted.value = true
      isStreaming.value = false
      interruptMsg.value = data.msg
      interruptPreview.value = data.preview || []
    },
    done: () => {
      isStreaming.value = false
    },
  })

  await sseClient.connect({ message: msg })
  isStreaming.value = false
}

async function handleConfirm(action) {
  isInterrupted.value = false
  await fetch(`/api/chat/${userId}/confirm`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action }),
  })
}

async function stopStream() {
  sseClient?.abort()
  await fetch(`/api/chat/${userId}/cancel`, { method: 'POST' })
  isStreaming.value = false
}

function scrollToBottom() {
  nextTick(() => {
    if (messagesEl.value) messagesEl.value.scrollTop = messagesEl.value.scrollHeight
  })
}

function logout() {
  localStorage.removeItem('user_id')
  router.push('/')
}
</script>
```

**Step 7: 配置路由 frontend/src/router/index.js**

```javascript
import { createRouter, createWebHistory } from 'vue-router'
import Login from '../pages/Login.vue'
import Chat from '../pages/Chat.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: Login },
    { path: '/chat', component: Chat, meta: { requiresAuth: true } },
  ],
})
```

**Step 8: 更新 frontend/src/main.js**

```javascript
import { createApp } from 'vue'
import App from './App.vue'
import router from './router/index.js'

router.beforeEach((to, from, next) => {
  if (to.meta.requiresAuth && !localStorage.getItem('user_id')) {
    next('/')
  } else {
    next()
  }
})

createApp(App).use(router).mount('#app')
```

**Step 9: 启动前端验证**

```bash
cd frontend
npm run dev
```

访问 http://localhost:5173，验证登录页正常显示，输入 user_id 可跳转聊天页。

**Step 10: Commit**

```bash
cd ..
git add frontend/
git commit -m "feat: Vue3 frontend with SSE chat, charts, interrupt dialog"
```

---

## Task 8：集成联调 & 端到端验证

**Step 1: 启动后端**

```bash
source .venv/bin/activate
cp .env.example .env
# 编辑 .env 填入真实 ANTHROPIC_API_KEY
uvicorn main:app --reload --port 8000
```

**Step 2: 启动前端**

```bash
cd frontend && npm run dev
```

**Step 3: 验证场景 A（流式输出）**

1. 打开 http://localhost:5173
2. 输入 user_id: `test_user_001`
3. 发送: `帮我分析昨天的广告活动数据`
4. 验证：步骤面板出现进展，回答逐字流式输出

**Step 4: 验证场景 B（图表渲染）**

发送: `用折线图展示昨天各小时的点击趋势`
验证：ECharts 折线图出现在对话气泡中

**Step 5: 验证场景 C（用户隔离）**

1. 用 `user_001` 登录发送几条消息
2. 退出，用 `user_002` 登录
3. 验证 `user_002` 看不到 `user_001` 的历史

**Step 6: 构建前端生产包**

```bash
cd frontend && npm run build
# 构建产物在 frontend/dist/
```

**Step 7: 验证一体化部署**

```bash
uvicorn main:app --port 8000
# 访问 http://localhost:8000 直接打开前端
```

**Step 8: 最终 Commit**

```bash
git add -A
git commit -m "feat: full integration, end-to-end verified"
```

---

## 验收标准

- [ ] 不同 user_id 对话历史完全隔离
- [ ] Agents.md 按用户合并系统模板 + 用户自定义
- [ ] Skills 列表显示系统预置 + 用户自定义分类
- [ ] 消息发送后流式显示执行步骤进展
- [ ] 最终回答逐字 token 打印
- [ ] 图表类消息用 ECharts 渲染在气泡中
- [ ] 危险操作弹出确认对话框（场景 A）
- [ ] [停止] 按钮可随时中断执行（场景 B）
- [ ] 执行中追加消息正常被 Agent 感知（场景 C）
- [ ] 构建后前后端可一体化部署
