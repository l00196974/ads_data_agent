# 测试策略（Testing Strategy）

项目把"自动化测试"分成三层独立目录，各自服务不同目的。理解这个分工对维护测试套件至关重要。

## 一、三层测试分工

| 目录 | 用途 | 是否真打 LLM | 跑频次 |
|---|---|---|---|
| `tests/unit/` | 离线单元测试 | ❌ 否（mock） | 每次提交 / CI |
| `scripts/` | E2E 验证（持久可重跑） | ✅ 是 | 按需手动 |
| `benchmarks/` | 评测 / 性能 profiling | ✅ 是 | 周期性 |

不要在 unit/ 里放真打 LLM 的测试——会变慢、不稳定、外部依赖多。
不要在 scripts/ 里放一次性诊断脚本——commit 一次结论后就该删除。

---

## 二、`tests/unit/` 单元测试

### 2.1 现状

- **39 个测试，~21 秒跑完**（离线，无 LLM 调用）
- 镜像源码结构：

```
tests/unit/
├── agent/
│   ├── test_config.py            ↔ agent/config.py
│   ├── test_core.py              ↔ agent/core.py / builder.py / llm.py
│   ├── test_store.py             ↔ agent/store.py
│   ├── test_tool_output_truncation.py  ↔ agent/middleware/tool_output_truncation.py
│   └── test_user_space.py        ↔ agent/user_space.py
└── api/
    ├── test_channel_registry.py  ↔ api/channel/registry.py
    └── test_runner_streaming.py  ↔ api/channel/runner.py
```

### 2.2 跑测试

```bash
# 全套
.venv/Scripts/python.exe -m pytest tests/

# 单文件
.venv/Scripts/python.exe -m pytest tests/unit/agent/test_core.py -v

# 单条
.venv/Scripts/python.exe -m pytest tests/unit/agent/test_core.py::test_build_model_openai_protocol -v
```

### 2.3 写测试约定

#### Patch "使用点"而不是"定义点"

```python
# ✅ 正确
patch("agent.builder.create_deep_agent")

# ❌ 错误（agent.core 是 re-export shim，名字早已绑定到 agent.builder 的命名空间）
patch("agent.core.create_deep_agent")
```

#### Mock 哪些 / 不 mock 哪些

| Mock | 原因 |
|---|---|
| ✅ `create_deep_agent` | 不想真起 langgraph |
| ✅ `_build_model` | 不想真打 LLM |
| ✅ `get_checkpointer` | 避免依赖 SQLite 文件 |
| ❌ `BaseChannel` 子类 | 测 channel 行为时直接实例化，不 mock |
| ❌ `Pydantic schemas` | mock 类型检查会被绕过，失去测试价值 |

#### Reset 模块级 singleton

`agent/store.py::_store` 等模块级单例要在每个测试前 reset，否则跨 test 状态泄漏：

```python
def _reset_store_module():
    import agent.store as s
    s._store = None
    s._exit_stack = None
```

### 2.4 当前覆盖空白

- `api/chat.py` 的 endpoint 逻辑（cancel / append / reset 等）—— 用 `httpx.AsyncClient` + lifespan 测试可以加上
- `api/skills.py` 的 zip 上传 / 解压安全 —— 应该补
- `agent/skill_loader.py` 的 SKILL.md 解析、subprocess 执行 —— 应该补
- `frontend/` —— 完全没有前端测试（Vue 组件 / SSE client）

---

## 三、`scripts/` E2E 验证

```
scripts/
├── verify_p0_e2e.py        SSE 时序回归（启 backend，发一次 chat，断言 plan + step 时序）
├── stress_long_context.py  5 轮压力测试（同 thread_id，inspect AsyncSqliteSaver 中累积曲线）
└── validate_summarization.py  monkey-patch trigger 到 10k，确认 SummarizationMiddleware 触发 + cutoff_index 正确
```

跑前提：
- 后端必须跑在 `:8000`
- `.env` 配好真实 LLM API key
- 每次跑会消耗 `~$0.01-0.10` LLM 费用（视测试规模）

不要把这些脚本接到 CI——成本不受控。

### 3.1 持久可重跑 vs 一次性诊断

写脚本时先问：**这个脚本明年还会跑吗**？

- 是 → 进 `scripts/`，加 docstring 说明用途，commit 入库
- 否 → 跑完一次得出结论后**删除**，commit 信息留下结论

### 3.2 何时跑

- 改了 SSE 协议 → `verify_p0_e2e.py`
- 改了 long_context 配置 / SummarizationMiddleware → `validate_summarization.py`
- 上线前回归 → 全部跑一遍

---

## 四、`benchmarks/` 评测与性能

```
benchmarks/
├── profile_latency.py    单次 chat 端到端耗时分解
└── ads_eval/             广告域评测题库（FDABench 风格）
    ├── README.md         详细用法
    ├── cases/            题目 yaml
    └── runner.py         批量跑题 + 打分
```

### 4.1 `profile_latency.py`

跑一次端到端调用，记录每阶段（LLM / 工具 / SSE 推送）耗时。详见
[observability.md::4.1](./observability.md#41-benchmarksprofile_latencypy)。

### 4.2 `ads_eval/`

广告域专用评测，独立 README。设计要点：
- 题库基于部署侧 SKILL.md 包构造（框架不内置题）
- 串行跑（避免 mock server 并发）
- 用 stub channel 捕获 send_plan 等 default tool 调用，不做 IO，但工具签名跟生产一致

跑评测：

```bash
.venv/Scripts/python.exe -m benchmarks.ads_eval.runner --cases benchmarks/ads_eval/cases/
```

输出每题的 LLM 调用次数、工具命中率、最终回答 GPT-4 评分。

---

## 五、依赖管理

仓库提供三个 requirements 文件，按场景组合：

| 文件 | 内容 | 用法 |
|---|---|---|
| `requirements.txt` | 生产 runtime 必需 | `pip install -r requirements.txt` |
| `requirements-dev.txt` | pytest 等 dev / 测试工具 | `pip install -r requirements-dev.txt` |
| `requirements-providers.txt` | 可选 LLM provider（注释形式，按需取消） | 编辑后 `pip install -r requirements-providers.txt` |

### 5.1 测试依赖

```bash
.venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt
```

含 `pytest` / `pytest-asyncio`。

### 5.2 LLM provider 依赖

`requirements-providers.txt` 默认全部注释——按用到的厂商取消注释再装。
默认走 OpenAI 兼容协议（`langchain-openai` 在主 requirements 里）就够。

直连 Anthropic / Google 等需要单独装：

```bash
.venv/Scripts/pip install langchain-anthropic       # 用 Claude 直连
.venv/Scripts/pip install langchain-google-genai    # 用 Gemini
```

详见 [configuration-reference.md::3.1 厂商对照示例](./configuration-reference.md)。

---

## 六、CI（GitHub Actions）

`.github/workflows/test.yml` 已配置两个 job：

| Job | 内容 | 触发 |
|---|---|---|
| `backend-tests` | `pip install -r requirements.txt -r requirements-dev.txt` + `pytest tests/ -v` | push / PR / workflow_dispatch |
| `frontend-build` | `npm ci` + `npm run build`（含 lint by Vite） | 同上 |

**默认不接入 E2E 测试**（`scripts/`）——它们会真打 LLM 消耗 token，CI 不可控。

PR ladder 建议：lint → unit test → frontend build → 合并允许。

启用：仓库推到 GitHub 后自动生效。本地预览 workflow 行为可用 [`act`](https://github.com/nektos/act)：

```bash
act -j backend-tests
act -j frontend-build
```

---

## 关联源码

- `tests/unit/` — 单元测试
- `scripts/` — E2E 验证脚本
- `benchmarks/` — 评测 / 性能
- `requirements.txt` — 主依赖（不含 dev / 可选 provider）
