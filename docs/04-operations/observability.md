# 可观测性（Observability）

当前观测能力**有限**——只有日志 + 可选 LangSmith trace。Prometheus / OpenTelemetry / 业务指标埋点都未实现，留作 [roadmap P2](../05-known-issues-and-roadmap/roadmap.md)。

## 一、日志

### 1.1 后端日志

uvicorn 启动后日志输出到 stdout，没有显式日志配置——继承 uvicorn 默认。

启动命令（生产）：

```bash
.venv/Scripts/python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000 \
    --log-level info \
    --access-log
```

生产环境建议重定向：

```bash
.venv/Scripts/python.exe -m uvicorn main:app ... > backend.log 2>&1 &
```

仓库根的 `backend.log` 是开发调试时的日志输出（已 .gitignore）。

### 1.2 前端 console

浏览器 DevTools Console 看：
- SSE 连接状态（`SSEClient.connect` 中的 fetch 错误）
- Vue 组件运行时报错
- 网络面板：SSE 长连接的 event stream（点 `EventStream` tab 看每个事件帧）

### 1.3 缺什么

- 结构化日志（JSON 格式 / 字段化）：未实现，当前都是字符串
- 日志分级（INFO / WARN / ERROR）：未规范化
- 日志轮转：未配置，长期跑 `backend.log` 会无限增长
- 日志聚合（ELK / Loki）：未接入

---

## 二、LangSmith Trace（可选）

LangSmith 是 langchain 生态的 trace 服务。配置后每次 LLM 调用 / 工具调用 / agent step
都会上报，可以在 web 端看完整 trace。

### 2.1 启用

`.env`：

```bash
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_pt_xxx       # 从 https://smith.langchain.com 拿
LANGSMITH_PROJECT=ads-data-agent
```

启用后 `pip install langsmith` 已自动随 langchain 装好，无额外依赖。

### 2.2 看什么

- 每次 chat 请求一条 trace
- trace 内嵌套：LLM 调用（含 prompt / completion / token 数）→ 工具调用（含 args / result）
- HitL interrupt 也会被记录
- subagent 调用作为子 trace

### 2.3 调试用法

LLM 输出诡异时，到 LangSmith 找对应 trace，**直接看 prompt 全文**——是 PLAN_INSTRUCTION
被覆盖了？还是 SKILL.md 拼错了？还是工具返回带乱码？trace 把所有中间状态可视化。

LangSmith 是当前**最有效**的调试入口，强烈建议生产开启。

---

## 三、SSE 调试

### 3.1 浏览器 DevTools

1. 打开 Network 面板
2. 发起一次 chat 请求
3. 找 `/api/chat/{user_id}` 那条记录，点 `EventStream` tab
4. 实时看每个 SSE 事件帧（event 类型 + JSON data）

### 3.2 命令行 curl

```bash
curl -N -X POST http://localhost:8000/api/chat/test_user \
  -H "Content-Type: application/json" \
  -d '{"message":"查询昨天点击数据"}' 2>&1
```

`-N` 关闭 curl 缓冲，让 SSE 帧实时打印。

### 3.3 后端推送代码定位

每个 SSE event 对应 channel 方法：

| event | 推送代码 |
|---|---|
| `token` | `WebSSEChannel.send_token` (`api/channel/web_sse.py:14-16`) |
| `step` | `WebSSEChannel.send_step` (`:18-20`) |
| `progress` | `WebSSEChannel.send_progress` (`:22-24`) |
| `plan` | `WebSSEChannel.send_plan` (`:26-28`) |
| `interrupt` | `WebSSEChannel.wait_for_confirm` 内推 (`:35-39`) |
| `done` | `WebSSEChannel.close` (`:41-42`) |

事件没出现时去对应代码加 `print()` 或 LangSmith trace 看 runner 是否真的发了。

---

## 四、性能 profiling

### 4.1 `benchmarks/profile_latency.py`

跑一次端到端调用，记录每个阶段的耗时（LLM 调用 / 工具调用 / SSE 推送等）：

```bash
.venv/Scripts/python.exe benchmarks/profile_latency.py
```

输出结构化的耗时分解，定位"卡在哪一步"。

### 4.2 SQLite 写锁观察

如果发现 chat 响应间歇性变慢，可能是 SQLite checkpoint 写锁阻塞。诊断：

```bash
# Windows: 看 data/checkpoints.db 是否有 .db-journal 长时间不消失
ls D:/workplace/ads_data_agent/data/
```

如果 `.db-journal` / `.db-wal` 长时间存在，说明有写事务卡住。
解决：要么减少并发（`uvicorn --workers 1`）、要么换 Postgres。

### 4.3 LLM 调用慢

LangSmith trace 里看 LLM 节点的 latency。可能原因：
- 模型本身慢（换更小模型，如 haiku）
- prompt 太长（开 SummarizationMiddleware，下调 trigger_tokens）
- 上下游网络（用 `LLM_BASE_URL` 切到内网代理）

---

## 五、HitL confirm 失败诊断

confirm POST 返 `not_found` 时排查顺序：

1. session_id 拼错？看 chat response header 的 `X-Session-Id`，前端 SSEClient 应保存好
2. agent 已结束？只在 `event: interrupt` 之后、`event: done` 之前 confirm 才有效
3. 多 worker 路由错？confirm 请求被 LB 派到没注册过 session 的 worker——这是当前
   `InMemoryChannelRegistry` 的设计限制，多 worker 部署需要 `RedisChannelRegistry`

---

## 六、未实现的观测能力（roadmap）

- Prometheus 指标：QPS / 错误率 / LLM 调用延迟分布 / token 用量
- OpenTelemetry：分布式 trace（与 LangSmith 互补：LangSmith 是 LLM 视角，OTel 是请求视角）
- 业务指标：每个 user 的对话次数、活跃 skill 排行、HitL approve/cancel 比例
- 健康检查 endpoint（`/healthz` / `/readyz`）
- 慢查询日志（哪些 SKILL.md 调用 > 5s）

详见 [roadmap.md](../05-known-issues-and-roadmap/roadmap.md::P2)。

---

## 关联源码

- `backend.log` — 开发期日志输出（生产应重定向到独立日志文件）
- `benchmarks/profile_latency.py` — 性能 profiling 工具
- `api/channel/web_sse.py` — SSE 帧推送代码
- `.env::LANGSMITH_*` — LangSmith trace 配置
