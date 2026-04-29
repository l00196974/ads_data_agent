# 已知问题清单（Known Issues）

按"严重度"分四组：阻塞生产 / Stub 待实现 / 设计 trade-off / 待优化。
每条标注：现象 / 影响 / workaround / 跟踪条目。

> 路线图见 [roadmap.md](./roadmap.md)。

---

## A. 阻塞生产（必须解决才能上线）

### A1. 认证缺失

- **现象**：`user_id` 通过登录页直接输入，前端 `localStorage` 存，每个 API 请求路径里带 `user_id`。
- **影响**：任何人输入任意 user_id 即可访问该用户的所有对话历史 / 上传的 skill / 内部数据。
- **workaround**：当前版本仅可在**完全可信内网环境**部署，用户名能识别但不防伪造。
- **解决路径**：接 SSO / JWT 中间件，从 token 派生 user_id，**忽略** URL path 里的 user_id（或要求一致）。
- **跟踪**：[roadmap.md::P0::auth](./roadmap.md)

### A2. 业务 skill 是部署侧资产，框架不内置

- **现象**：仓库自带的 `skills/metric-data-extractor` 只是 mock—真实部署需要：
  1. 替换为对接真实数据源的 skill 包
  2. 或在生产环境保留外部 mock 服务
- **影响**：直接拉仓库部署看到的"指标查询"是假数据。
- **workaround**：本来就是设计——framework 与业务解耦，部署侧自己装业务 skill。
- **解决路径**：部署前对接真实数据源，写新的 SKILL.md 包替换 mock。
- **跟踪**：[roadmap.md::P0::data-source](./roadmap.md)

### A3. `interrupt_on` 默认配置与实际 skill 不匹配

- **现象**：`config.yaml::agent.interrupt_on` 默认值是 `[delete_adgroups, modify_budget, pause_campaign]`——
  这些**词汇是占位**，实际部署的 SKILL.md 包注册的子命令名通常不一致（如真实是 `delete-adgroup`）。
- **影响**：如果不改，敏感操作不会触发 HitL，**直接静默执行**。
- **workaround**：部署前必须按真实 skill 的子命令名改 yaml。
- **解决路径**：加启动校验——`interrupt_on` 列表里的工具名应该都在已加载工具集里，否则 warn / fail-fast。
- **跟踪**：[roadmap.md::P0::interrupt-validation](./roadmap.md)

---

## B. Stub 待实现（多副本部署阻塞）

### B1. `RedisChannelRegistry` 未实现

- **现象**：`api/channel/registry.py:75-115` 是 stub，`__init__` 直接 `raise NotImplementedError`。
- **影响**：当前只有 `InMemoryChannelRegistry`（进程内 dict）。多 worker 部署时 HitL confirm POST
  落到没注册过 session 的 worker，agent 一直阻塞 `wait_for_confirm` 永不返回。
- **workaround**：单 worker 部署（uvicorn `--workers 1`）。
- **解决路径**：
  1. Worker 维护本地 InMemory dict
  2. register 时同时 SET 到 Redis（key=`ads:channel:{session_id}`，value=worker_id）
  3. 各 worker 订阅 `ads:channel:resolve:{worker_id}` pub/sub
  4. confirm 落错 worker 时通过 Redis publish 转发到 owner worker
- **跟踪**：[roadmap.md::P1::redis-registry](./roadmap.md)

### B2. `MysqlStore` / `PostgresStore` 未实现

- **现象**：`agent/store.py:71-78` 当 `store_backend="mysql"` 时 raise `NotImplementedError`。
- **影响**：默认 `AsyncSqliteStore` 在多副本场景下，写并发受 SQLite 全局写锁限制。
- **workaround**：单机或挂同一 NFS 卷的低并发副本。
- **解决路径**：仿 `langgraph.store.sqlite` 包装 aiomysql 实现 `BaseStore` 接口，或直接用
  langgraph 上游 `PostgresStore`（如果存在）。
- **跟踪**：[roadmap.md::P1::mysql-store](./roadmap.md)

### B3. `PostgresSaver` checkpointer 未启用

- **现象**：默认 `AsyncSqliteSaver`，多副本部署的 checkpoint 写并发受 SQLite 全局锁限制。
- **影响**：高并发下 chat 请求间歇性变慢（等写锁）。
- **workaround**：单机或 NFS 多副本低并发。
- **解决路径**：langgraph 已支持 `PostgresSaver`，改 `agent/checkpointer.py::init_checkpointer` 一处即可。
- **跟踪**：[roadmap.md::P1::postgres-checkpoint](./roadmap.md)

### B4. `/api/chat/{user_id}/cancel` 是 stub

- **现象**：endpoint 返 `{"status": "cancelled"}` 但**不实际取消 agent**。
- **影响**：服务端无主动 cancel 能力。前端"停止"按钮的真正机制是 `AbortController.abort()`
  关闭 SSE 连接 → SSE generator 退出 → runner CancelledError → 自然结束。
- **workaround**：前端关连接的方式实际有效，"主动停止"功能在前端视角下是 work 的。
- **解决路径**：建 task registry 按 conversation_id 索引，cancel endpoint 拿到 task 直接 task.cancel()。
- **跟踪**：[roadmap.md::P2::server-side-cancel](./roadmap.md)

---

## C. 设计 trade-off（接受但需明确）

### C1. `send_plan` 强制调用增加一轮 LLM round trip

- **trade-off**：换计划面板可视化，首字节时间慢 1-3 秒。
- **决策记录**：[02-features/03-execution-panel.md::ADR-013](../02-features/03-execution-panel.md)

### C2. SKILL.md 全文进 system prompt 导致 prompt 膨胀

- **trade-off**：换"加 skill 零 Python 改动"，多 skill 时 token 成本可见上涨。
- **缓解**：未来加 skill_id 到 prompt 节选机制（按用户问题相关性筛选 skill）。
- **决策记录**：[02-features/01-skill-tool-channel-model.md::ADR-002](../02-features/01-skill-tool-channel-model.md)

### C3. SubAgent 调用增加一次 LLM round trip

- **trade-off**：换主 Agent context 不被脏数据污染。简单任务派工就是浪费。
- **缓解**：靠 system prompt 引导主 Agent 何时派工 / 何时自己干。
- **决策记录**：[02-features/07-subagent-driven-context.md::ADR-014](../02-features/07-subagent-driven-context.md)

### C4. SummarizationMiddleware monkey-patch 是隐式约定

- **trade-off**：deepagents 升级时如果改了工厂函数名 / 签名，patch 静默失效。
- **缓解**：`scripts/validate_summarization.py` 守住，每次升级必跑。
- **决策记录**：[02-features/08-context-fallback.md::ADR-004](../02-features/08-context-fallback.md)

### C5. `get_store()` 懒初始化为 InMemoryStore

- **trade-off**：避免单元测试改动，代价是"忘了调 init_store 时静默 fallback 到内存"。
- **缓解**：lifespan 启动时强制 init，运维监控验证。
- **决策记录**：[02-features/10-persistence-and-checkpoints.md::ADR-011](../02-features/10-persistence-and-checkpoints.md)

---

## D. 待优化（不阻塞但应处理）

### D1. requirements-dev.txt 缺失

- **现象**：`pytest` / `pytest-asyncio` 等不在 `requirements.txt`，重建 venv 后必须手动装。
- **影响**：新人上手摩擦；CI 配置费劲。
- **解决**：建 `requirements-dev.txt` + `requirements-providers.txt`。
- **跟踪**：[roadmap.md::P2::deps](./roadmap.md)

### D2. CI 暂未配置

- **现象**：每次 commit 后人工跑 `pytest`。
- **影响**：易漏测试；多人协作时 PR review 慢。
- **解决**：GitHub Actions 跑 pytest + frontend build。
- **跟踪**：[roadmap.md::P2::ci](./roadmap.md)

### D3. SSE 心跳与断线重连未实现

- **现象**：长时间无数据时 nginx 等代理可能关连接，前端 SSEClient 不会自动重连。
- **影响**：长任务（如复杂 SubAgent 调用）可能被中断。
- **解决**：后端 30s 推 `: keepalive`；前端检测断流 + 重发请求（带 conversation_id 续接）。
- **跟踪**：[roadmap.md::P2::sse-keepalive](./roadmap.md)

### D4. event: error 没规范化

- **现象**：runner 捕获 Exception 时通过 `send_token` 推 `[错误] {e}` 文本，**没**单独发 `event: error` 帧。
- **影响**：前端 SSE handler 的 `error` 占位 handler 不会被 fired，错误信息混在普通 token 里。
- **解决**：runner 区分异常类型，错误走专用 channel 方法（如 `channel.send_error(msg)`）。
- **跟踪**：[roadmap.md::P2::error-event](./roadmap.md)

### D5. user skill 上传重启才生效

- **现象**：上传成功后必须重启后端，LLM 才看到 SKILL.md 文档段更新。
- **影响**：用户体验：上传后立即去问，agent "不知道"新 skill。
- **workaround**：前端在 upload 成功后提示用户"开新对话即可使用"——新对话会重新 build_agent 含最新 skill。
- **跟踪**：[roadmap.md::P2::skill-hot-reload](./roadmap.md)

### D6. 火山方舟 ARK + GLM 不支持 prompt caching

- **现象**：实测 `cache_read=0`。OpenAI / DeepSeek / Anthropic Claude 都自动支持。
- **影响**：用 ARK + GLM 时长 prompt 成本高、延迟大。
- **workaround**：暂未深入处理（用户后续会切其它供应商）。
- **跟踪**：[roadmap.md::P2::caching-strategy](./roadmap.md)

### D7. 前端 zip 上传无沙箱

- **现象**：上传的 SKILL.md 包脚本会被 subprocess 直接执行，访问后端进程能访问的所有资源。
- **影响**：仅适合"信任内部用户"场景；公开网络部署需加沙箱。
- **解决**：Docker per-skill 容器 + 文件系统 chroot + 网络 ACL + 资源限额。
- **跟踪**：[roadmap.md::P2::skill-sandbox](./roadmap.md)

### D8. 观测能力有限

- **现象**：只有 stdout 日志 + 可选 LangSmith trace。无 Prometheus / OpenTelemetry / 业务指标。
- **影响**：生产环境无法监控 QPS / 错误率 / token 用量 / 响应时间分布。
- **解决**：接入 OpenTelemetry SDK + 暴露 Prometheus 指标 endpoint。
- **跟踪**：[roadmap.md::P2::observability](./roadmap.md)

---

## E. 安全考虑（明确边界）

| 风险 | 当前状态 | 缓解 |
|---|---|---|
| LLM prompt injection | 部分缓解（DOMPurify 防 XSS） | 未做输入分类 / 输出过滤 |
| Skill 脚本恶意行为 | 未隔离 | 仅信任内部用户上传，公开互联网用户需 D7 沙箱 |
| user_id 伪造 | 完全可被伪造（A1） | 内网部署 + 接 SSO 后修复 |
| SSRF / 数据泄漏（skill 脚本访问外网） | 不限制 | 需要时加网络 ACL |
| 资源滥用（单用户失控调用） | 无限额 | 需要时加 rate limiting middleware |

---

## F. 信息完整性（明确未知）

以下领域**未深入测试**，已知不知道：

- 大规模并发（>100 QPS）下 SQLite checkpoint 写锁的实际表现
- LLM 工具调用的极端边界（参数列表 > 1000 项时 langgraph 行为）
- 长 SubAgent 链（主 → 子 → 孙）的 context 累积速度
- 不同模型之间切换时 conversation 历史的兼容性（如 Claude → GPT）

需要时再做实测。
