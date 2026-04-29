# 04 Human-in-the-Loop（HitL 确认流）

> **前置**：先读 [02-channel-abstraction.md](./02-channel-abstraction.md) 第四节理解
> `ExternallyConfirmable` Protocol 与 ChannelRegistry。

## 一、解决的问题

广告投放场景里有大量**不可逆 / 高风险**操作——删广告组、改预算、暂停活动。
这些操作如果让 LLM 自由调用，遇到误识别用户意图就是事故。

HitL 在工具调用前**自动暂停**，把决策权交回用户：

```
用户：「把昨天 CTR 不到 1% 的广告组都暂停掉」
↓
LLM 调 pause_campaign(...)
↓
deepagents 检测到 interrupt_on 命中 → 抛 GraphInterrupt
↓
runner 推 event: interrupt → 前端弹确认框
↓
用户点 approve / cancel
↓
runner 收到信号 → Command(resume=approve) 继续 / 终止
```

---

## 二、配置触发

`config.yaml::agent.interrupt_on` 列敏感工具/子命令名：

```yaml
agent:
  interrupt_on:
    - delete_adgroups
    - modify_budget
    - pause_campaign
```

deepagents 内部用 `HumanInTheLoopMiddleware` 把这些工具名注册到一个 dict（`{tool_name: True}`）。
LLM 调用命中即拦截，抛 `GraphInterrupt` 异常或在 `on_chain_end` 输出里塞 `__interrupt__` payload。

源码：`agent/builder.py:37`、deepagents 库的 `HumanInTheLoopMiddleware`。

> ⚠️ `interrupt_on` 默认值（`delete_adgroups` 等）是占位，**与实际部署的 SKILL.md 包注册的子命令名通常不一致**。部署前务必按真实 skill 命令名调整。

---

## 三、三个使用场景

### 3.1 场景 A：危险操作确认（runner 主动中断）

最常见路径。`interrupt_on` 命中即触发，详见第一节流程图。

`runner.py:108-128`：

```python
elif kind == "on_chain_end":
    outputs = event.get("data", {}).get("output", {})
    if isinstance(outputs, dict) and "__interrupt__" in outputs:
        interrupts = outputs["__interrupt__"]
        iv = interrupts[0].value if interrupts else {}
        if not isinstance(iv, dict):
            iv = {"message": str(iv), "preview": []}
        approve = await channel.wait_for_confirm(iv.get("message", ""), iv.get("preview", []))
        await _resume_safely(agent, approve, config)
```

GraphInterrupt 异常路径同步处理（runner.py:122-128）。

### 3.2 场景 B：用户主动停止

前端的"停止"按钮：

```js
async function stopStream() {
  sseClient?.abort()                          // 关闭 SSE 连接
  await fetch(`/api/chat/${userId}/cancel`)   // stub endpoint
  isStreaming.value = false
}
```

机制差异：通过**关闭 SSE 连接**触发 backend 的 `event_generator` 退出。runner 的协程被
GC，最终 `astream_events` 抛 `CancelledError`，`finally` 块清理 channel registry。

⚠️ `/cancel` endpoint 当前是 stub，不做实际 cancel。真正起作用的是前端 `AbortController.abort()`。
详见 [03-api-reference/rest-endpoints.md::3](../03-api-reference/rest-endpoints.md)。

### 3.3 场景 C：执行中追加要求

用户输入框在执行中**保持可用**——发新消息走 `/api/chat/{user_id}/append`，**不**关 SSE 连接：

```
1. /append POST 到达 → 后端 cancel 当前 task
2. runner 在 CancelledError 时 跳过 channel.close()（保留 SSE 连接）
3. chat handler 在同一 channel 上启动新一轮 runner.run(channel, new_message, ...)
4. 前端透明地接收第二轮事件，不需要重连
```

源码：`api/chat.py:180-218::append_message`。

---

## 四、跨连接 vs 同栈：两种确认机制

详见 [02-channel-abstraction.md::4](./02-channel-abstraction.md)。简表：

| Channel | "等待"机制 | "信号"来源 | ChannelRegistry |
|---|---|---|---|
| `CLIChannel` | `input(...)` 阻塞 stdin | 同终端按键 | ❌ 不需要 |
| `WebSSEChannel` | `await self._confirm_event.wait()` | 独立 `POST /confirm` 请求 | ✅ 必须 |

未来加 Slack / WebSocket channel：只要实现 `resolve_confirm(approve)` 方法即自动满足
`ExternallyConfirmable` Protocol，confirm endpoint + registry **零改动**。

---

## 五、resume 安全：`_resume_safely`

resume 期间收到 cancel 信号是个尖锐场景——直接打断 `agent.ainvoke()` 会让 langgraph
checkpointer 停在写到一半的状态，下一轮 restart 读到 corrupt state。

`runner.py:46-58`：

```python
async def _resume_safely(agent, approve: bool, config: dict) -> None:
    inner = asyncio.create_task(agent.ainvoke(Command(resume=approve), config=config))
    try:
        await asyncio.shield(inner)
    except asyncio.CancelledError:
        try:
            await inner   # 等内部 ainvoke 跑完
        except Exception:
            pass
        raise
```

`asyncio.shield` 让外部 cancel **不**传播到 inner task，但 await 已经被打断会冒
`CancelledError`——except 块再 `await inner` 等 inner 自己跑完才让 raise 生效。
最终结果：cancel 信号被捕获，但 langgraph state 完整写入。

测试：单元测试无法覆盖这个场景（要并发 cancel + ainvoke 的 race），靠 `scripts/verify_p0_e2e.py` 端到端验证。

---

## 六、`_confirm_event` 的幂等性

`WebSSEChannel.resolve_confirm`：

```python
def resolve_confirm(self, approve: bool) -> None:
    if self._confirm_event.is_set():
        return                          # 幂等：已 resolved 就忽略
    self._confirm_result = approve
    self._confirm_event.set()
```

幂等保护两个场景：
- 用户双击确认按钮 → 第二次调用静默 no-op
- `wait_for_confirm` 重新进入时已先 `clear()`：

```python
async def wait_for_confirm(self, ...) -> bool:
    self._confirm_event.clear()           # 防止上一次遗留的 set 让本次立即返回脏值
    self._confirm_result = None
    ...
```

---

## 七、SSE Event 协议

`event: interrupt` schema 见 [03-api-reference/sse-event-types.md::interrupt](../03-api-reference/sse-event-types.md)。

`POST /confirm` 请求体见 [rest-endpoints.md::2](../03-api-reference/rest-endpoints.md)。

---

## 八、当前限制

- **多 worker 路由**：`InMemoryChannelRegistry` 只在单 worker 工作。多副本场景下 confirm
  POST 落到没注册过 session 的 worker 会 `not_found`——必须实现 `RedisChannelRegistry`
  才能解决（见 [05-known-issues](../05-known-issues-and-roadmap/README.md)）。
- **`/cancel` stub**：服务端无法主动 cancel，只靠前端关 SSE 连接。如需真正服务端 cancel
  须建 task registry。
- **`interrupt_on` 默认值不通用**：默认列的工具名（`delete_adgroups` 等）与 SKILL.md 包
  注册的子命令名通常不匹配，部署前必须改。

---

## 设计决策记录（ADR）

### ADR-003：用 `Protocol` 表达跨连接确认能力

详见 [02-channel-abstraction.md::ADR-003](./02-channel-abstraction.md)。

### ADR-008：cancel 期间用 shield + 等待 inner task 退出

**Context**：早期版本直接让 cancel 信号传播到 `agent.ainvoke()`，导致 langgraph 写
checkpoint 写到一半就停，下一次 restart 读到 corrupt state，agent 行为异常。

**Decision**：用 `asyncio.shield` 保护 `agent.ainvoke()` 不被外部 cancel 中断。
外层 await 被 cancel 时进 except 块，先 `await inner` 让 ainvoke 自然结束（写完 checkpoint），
再 raise CancelledError 上抛。

**Consequences**：
- ✅ checkpoint 状态始终完整，restart 不会读到 corrupt
- ✅ 用户取消请求体感正常（取消信号最终到达）
- ⚠️ cancel 后到 ainvoke 真正结束之间有 ~1-N 秒延迟（取决于 LLM 当前 step 还有多远）
- ⚠️ 这段延迟期内用户看不到任何反馈，可能误以为没取消成功

**Alternatives**：
- 直接 cancel：corrupt state 风险，已尝试，被推翻
- 把 ainvoke 改成"check cancel flag 主动退出"：要 fork langgraph 内部，复杂度爆炸
