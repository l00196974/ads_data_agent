# 02 Channel 抽象（Channel Abstraction）

> **前置**：先读 [01-skill-tool-channel-model.md](./01-skill-tool-channel-model.md) 理解 Channel 在三层概念中的位置。

## 一、设计目标

让**同一个 Agent 能服务多种前端**（Web SSE / CLI / 未来的 Slack / WebSocket），
而 LLM 的 system prompt、工具签名、调用顺序在不同前端下**完全不变**。

具体来说，我们希望以下三件事都是 channel 的"插拔点"：
1. **输出形态差异**：SSE 推 JSON 还是 CLI 打印纯文本
2. **HitL 确认信号来源**：独立 HTTP 请求还是同栈 stdin 输入
3. **多 worker 路由**：是否需要跨进程转发 confirm 信号

---

## 二、`BaseChannel` 接口契约

`api/channel/base.py:27-70`

```python
class BaseChannel(ABC):
    """Agent 与外部对话渠道的通信契约。
    
    本类暴露的方法构成 channel 的 skill 集合。"""

    def __init__(self, user_id: str, session_id: str): ...

    @abstractmethod
    async def send_token(self, token: str) -> None: ...
    
    @abstractmethod
    async def send_step(self, msg: str, step_type: str) -> None: ...
    
    @abstractmethod
    async def send_progress(self, message: str) -> None: ...
    
    @abstractmethod
    async def send_plan(self, tasks: list[dict]) -> None: ...
    
    @abstractmethod
    async def wait_for_confirm(self, message: str, preview: list) -> bool: ...
    
    async def close(self) -> None: ...
```

调用关系：
- **runner 直接调用**：`send_token` / `send_step` / `send_progress` / `wait_for_confirm`（在 `astream_events` 主循环里转发 langgraph event）
- **默认 Tool 间接调用**：`send_plan` 由 `default_tools.send_plan` 工具内部调用

源码：`api/channel/runner.py:79-128`（runner 主循环）、`api/channel/default_tools.py:30-50`（send_plan tool）。

---

## 三、两种实现的差异

### 3.1 `WebSSEChannel`

`api/channel/web_sse.py`

每个方法把消息序列化为 SSE 帧 (`event: <type>\ndata: <json>\n\n`) 入队 `asyncio.Queue`，
HTTP 路由层从队列里 yield 回前端：

```python
async def send_plan(self, tasks: list[dict]) -> None:
    data = json.dumps({"tasks": tasks}, ensure_ascii=False)
    await self._queue.put(f"event: plan\ndata: {data}\n\n")
```

**关键状态**：
- `_queue: asyncio.Queue` — 接 SSE 帧的传输管道（chat.py 的 `event_generator` 从此 yield）
- `_confirm_event: asyncio.Event` — HitL 阻塞唤醒（详见第 4 节）

### 3.2 `CLIChannel`

`api/channel/cli.py`

每个方法直接 `print()` 到 stdout：

```python
async def send_plan(self, tasks: list[dict]) -> None:
    print("\n📋 执行计划:")
    for t in tasks:
        print(f"  ⬜ {t.get('id', '?')}: {t.get('name', '')}")
```

无队列、无 Event——它跑在同一个执行栈，输出与等待全是直接 IO。

### 3.3 二者对照

| 维度 | WebSSEChannel | CLIChannel |
|---|---|---|
| 输出形态 | SSE 帧 (JSON) | 终端文本 |
| 状态持有 | Queue + Event | 无 |
| 跨连接？ | ✅ confirm 来自独立 HTTP 请求 | ❌ stdin 与执行同栈 |
| 注册到 ChannelRegistry？ | ✅ 是 | ❌ 否（不需要反向引用） |

---

## 四、HitL 确认机制：跨连接 vs 同栈

### 4.1 问题

HitL 流程的核心是：runner 在敏感工具被命中时阻塞，等待用户响应再决定 resume 还是 cancel。
"等待用户响应"在 CLI 与 Web 下的实现完全不同——

| Channel | "等待"机制 | "信号"来源 |
|---|---|---|
| CLI | `input(...)` 阻塞 stdin | 同终端按键 |
| WebSSE | `await self._confirm_event.wait()` | 一个**独立的** `POST /confirm` HTTP 请求 |

CLI 因为信号在同执行栈，朴素阻塞即可；WebSSE 必须有"反向引用入口"——
让 confirm 请求能找到原 channel 实例的 `_confirm_event`。这是 `ChannelRegistry` 存在的唯一原因。

### 4.2 `ExternallyConfirmable` Protocol

`api/channel/base.py:5-24`

跨连接确认能力被抽成一个**结构化协议**（`runtime_checkable Protocol`），不绑死到具体类：

```python
@runtime_checkable
class ExternallyConfirmable(Protocol):
    def resolve_confirm(self, approve: bool) -> None: ...
```

confirm endpoint 守卫**能力**而非**类型**：

```python
# api/chat.py:161-172
@router.post("/{user_id}/confirm")
async def confirm(user_id: str, req: ConfirmRequest):
    channel = channel_registry.get(req.session_id)
    if not channel:
        return {"status": "not_found"}
    if not isinstance(channel, ExternallyConfirmable):
        return {"status": "not_supported_on_this_channel"}
    channel.resolve_confirm(req.action == "approve")
    return {"status": "ok"}
```

`ChannelRegistry.register()` 也用 Protocol 静默过滤：CLI channel 注册是 no-op，
只有真正满足 `ExternallyConfirmable` 的 channel 才进 dict。

→ **未来加 Slack / WebSocket channel 只要实现 `resolve_confirm`，confirm endpoint 和 registry 零改动**。

### 4.3 端到端跨请求时序（WebSSE）

```
[1] POST /api/chat/{user_id}
    ├─ 创建 WebSSEChannel(user_id, session_id, queue)
    ├─ channel_registry.register(session_id, channel)   ← Protocol 守卫通过
    └─ response header: X-Session-Id, X-Conversation-Id

[2] runner 命中敏感工具 → channel.wait_for_confirm()
    ├─ self._confirm_event.clear()                       ← 重置避免脏值
    ├─ queue.put("event: interrupt\n...")                ← 推 SSE 给前端
    └─ await self._confirm_event.wait()                  ← 阻塞

[3] 前端弹确认框 → POST /api/chat/{user_id}/confirm
    Body: {"session_id": "...", "action": "approve"}

[4] confirm endpoint：
    channel = channel_registry.get(session_id)
    isinstance(channel, ExternallyConfirmable) ✓
    channel.resolve_confirm(approve=True)
        ├─ self._confirm_result = approve
        └─ self._confirm_event.set()                     ← 唤醒 [2] 的阻塞

[5] runner 拿到 approve=True
    └─ Command(resume=True) → langgraph 继续执行 agent
```

### 4.4 cancel 期间的 resume 安全

`api/channel/runner.py:46-58::_resume_safely`

resume 期间收到 cancel **不能**直接打断 `agent.ainvoke()`——会让 langgraph 写到一半就停，
checkpointer 留下半完成 state，下一轮 restart 读到 corrupt 状态。

解决：把 ainvoke 包成独立 task + `asyncio.shield`，cancel 时也等它跑完再 raise CancelledError：

```python
async def _resume_safely(agent, approve: bool, config: dict) -> None:
    inner = asyncio.create_task(agent.ainvoke(Command(resume=approve), config=config))
    try:
        await asyncio.shield(inner)
    except asyncio.CancelledError:
        try:
            await inner   # 等内部跑完
        except Exception:
            pass
        raise
```

---

## 五、ChannelRegistry：单 worker 与多 worker

`api/channel/registry.py`

### 5.1 类层级

```
ChannelRegistry (ABC)
   ├─ InMemoryChannelRegistry         ← 默认，单 worker
   └─ RedisChannelRegistry            ← stub，未实现
```

工厂选择：

```python
make_channel_registry("memory")  # 默认
make_channel_registry("redis", redis_url=..., worker_id=...)  # 待实现
```

### 5.2 多 worker 场景的限制与方案

InMemory 实现是**进程级 dict**——多 worker 部署时，confirm POST 可能落在没注册过 session 的
worker，`get()` 返回 None，confirm endpoint 回 `not_found`。

**Channel 对象不能跨进程序列化**——它持有 `asyncio.Event` / `asyncio.Queue`，
这些对象只在它们的 owning event loop 里有意义。所以多 worker 方案必然是"路由"而不是"复制"。

`RedisChannelRegistry` 的设计方案（在源码 docstring 详述）：

```
1. 各 worker 维护本地 InMemory 字典存自己的 channel
2. register 时同时 SET ads:channel:{session_id} = {worker_id} 到 Redis（带 TTL）
3. 每 worker 启动时订阅 ads:channel:resolve:{worker_id} pub/sub
4. 异 worker 收到 confirm POST 时：GET 找到 owner，PUBLISH 到对应 worker 的 resolve channel
5. owner worker 的订阅协程收到消息，调本地 channel.resolve_confirm(approve)
```

→ Channel 对象本身**不离开**自己的 worker；Redis 只存路由元数据。

详细约束 / 风险见 [05-known-issues](../05-known-issues-and-roadmap/README.md) 中"多 worker 化"条目。

---

## 六、新增 Channel 的 5 步走

实现一个新 channel 类型（如 SlackChannel）的最小工作量：

1. **`api/channel/slack.py`**：写 `SlackChannel(BaseChannel)`，实现 5 个 channel skill 方法
2. **如果支持跨连接确认**：再实现 `resolve_confirm(approve: bool)` 方法（自动满足 `ExternallyConfirmable` Protocol）
3. **`api/channel/__init__.py`**：导出新类
4. **接入层**：在 Slack event handler 里实例化 SlackChannel 并 `agent_runner.run(channel, ...)`
5. **不要动**：`agent/`、`skills/`、`runner.py` 主循环、prompt 文件——这些都不感知 channel 切换

---

## 设计决策记录（ADR）

### ADR-003：用 `Protocol` 而不是继承表达"跨连接确认能力"

**Context**：早期版本想让所有 channel 都实现 `resolve_confirm`，但 CLIChannel 没有这个能力——
强行实现就会写一个空方法或 raise NotImplementedError，既不优雅也容易误导。

**Decision**：用 `typing.Protocol` + `runtime_checkable` 表达"能力"而非"类型"。confirm endpoint
和 ChannelRegistry 都通过 `isinstance(channel, ExternallyConfirmable)` 守卫——
*结构匹配* 而不是 *类型继承*。

```python
@runtime_checkable
class ExternallyConfirmable(Protocol):
    def resolve_confirm(self, approve: bool) -> None: ...
```

**Consequences**：
- ✅ CLIChannel 不需要假装自己有 confirm 能力
- ✅ 未来加 Slack/WebSocket channel 只要实现 `resolve_confirm` 方法就自动满足 Protocol——零中间层
- ✅ confirm endpoint 的代码读起来像"能力守卫"而不是"类型 switch"，意图清晰
- ⚠️ Protocol 检查是结构性的——理论上有同名异义方法的类型会"误中"，但实际项目里不会发生

**Alternatives**：
- 抽象基类 + `NotImplementedError`：每加新 channel 都要决定"用不用 confirm"，有继承负担
- 工厂模式 + `confirm_strategy` 参数：把能力外置但增加装配复杂度，违背"channel 自描述"原则

---

### ADR-007：ChannelRegistry 用 ABC + 默认 InMemory + Redis Stub 而不是直接上 Redis

**Context**：实际部署量当前是单 worker。如果一开始就强制 Redis，会引入：
- 一个进程外依赖（开发 / 测试都要起 Redis）
- pub/sub 协程的生命周期管理复杂度
- 序列化 / 路由 metadata 的额外代码

但**架构上**必须为多 worker 留口子，否则到时候要返工 confirm endpoint。

**Decision**：把 `ChannelRegistry` 升为 ABC，提供 `InMemoryChannelRegistry` 默认实现 +
`RedisChannelRegistry` stub。stub 的 `__init__` 直接 `raise NotImplementedError`，
docstring 详述实现方案——明确"这条路通了，但实现工时还在外面"。

**Consequences**：
- ✅ 单 worker 部署零外部依赖
- ✅ 多 worker 化的接入点清晰，未来实现 Redis 时不动调用方代码
- ✅ Stub 的 `NotImplementedError` 让"以为已实现"的 bug 不会偷偷溜到生产
- ⚠️ docstring 设计方案会随时间漂移——实际实现时要先核对方案是否仍最优

**Alternatives**：
- 直接上 Redis 实现：开发 / 测试摩擦高
- 不抽象，等需要时再重构：confirm endpoint / register 调用方都要改
