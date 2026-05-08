"""Pure Python agent loop — 不依赖 deepagents / langchain.agents / langgraph。

替代 `deepagents.create_deep_agent` + `agent.astream_events(v2)` 消费链。
保留 `langchain_core.messages.{Human/AI/Tool/System}Message` 作为数据载体（这些
是轻量数据类，没有切走的理由）。

设计要点：
1. **直接用 openai SDK** 不走 langchain_openai.ChatOpenAI——少一层包装、流式
   tool_calls 增量字段直接可见
2. **asyncio.gather 并行 tool 调度**——LLM 一轮 emit 多个 tool_calls 时直接全
   并发执行
3. **Middleware 协议**：纯 Python 协议（`before_model(state) -> state | None`），
   不依赖 langchain.agents.middleware.types.AgentMiddleware
4. **HitL via callback hook**：interrupt 通过用户传入的 `on_interrupt` 回调暴露
   决策权给上层；resume 直接传 approval 进 `resume()` 即可，不需要 Command/
   GraphInterrupt 等 langgraph 协议
5. **Subagent = 嵌套 AgentLoop 实例**：派工就是 spawn 子 loop，message 历史隔离

事件流（async generator yield）：
- {"type": "model_start", "call_seq": int}
- {"type": "token", "delta": str}
- {"type": "model_end", "usage": {...}, "ai_message": AIMessage}
- {"type": "tool_start", "tool_call_id": str, "tool_name": str, "args": dict}
- {"type": "tool_end", "tool_call_id": str, "output": str, "elapsed_ms": int}
- {"type": "interrupt", "tool_calls": list, "preview": ...}  ← 等 caller resume
- {"type": "complete", "final_message": AIMessage}
- {"type": "recursion_limit", "iters": int}
- {"type": "error", "exception": Exception}
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


# ============================================================
# 数据载体
# ============================================================


@dataclass
class AgentState:
    """主循环执行期的可变状态。在 middleware 间传递，可被替换返回。

    `messages` 是 LLM 看到的对话历史（含 system/user/ai/tool）；
    `iter_count` 是已执行的 model 调用次数（recursion_limit 用）。
    `thread_id` 透传给 middleware（DateReminder / IterationGuard 等可能需要）。
    """
    messages: list[BaseMessage] = field(default_factory=list)
    iter_count: int = 0
    thread_id: str = ""
    # 任意扩展字段（middleware 可塞自己的临时数据，不强制 schema）
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    """单次工具调用的解析结果。"""
    id: str
    name: str
    args: dict


@dataclass
class ToolSpec:
    """注册到 loop 的工具定义。

    `func`: async 调用 - 接收 args dict，返回 str（输出）
    `schema`: OpenAI tool schema dict - 直接喂给 LLM
    `is_concurrency_safe`: 是否能并行执行（read-only 工具默认 True，写工具 False）
    """
    name: str
    func: Callable[[dict], Awaitable[str]]
    schema: dict
    is_concurrency_safe: bool = True


# ============================================================
# Middleware 协议（替代 langchain.agents.middleware.types.AgentMiddleware）
# ============================================================


class Middleware(Protocol):
    """Middleware 协议——任何对象只要有 before_model / after_tool 方法之一即可。

    不强制继承基类（duck typing）。返回 None = 不修改 state；返回新 AgentState =
    替换。
    """

    async def before_model(self, state: AgentState) -> AgentState | None:
        ...


# ============================================================
# 主循环
# ============================================================


@dataclass
class AgentConfig:
    """主循环配置。"""
    model: str
    system_prompt: str
    base_url: str | None = None
    api_key: str | None = None
    recursion_limit: int = 150
    tool_timeout_seconds: float = 60.0
    # HitL 拦截：tool_name → (是否需要弹确认，附加 metadata)
    interrupt_tools: dict[str, dict] = field(default_factory=dict)


class AgentLoop:
    """async 主循环——单一事件流，按 step 推进。"""

    def __init__(
        self,
        config: AgentConfig,
        tools: list[ToolSpec],
        middlewares: list[Middleware] | None = None,
    ):
        self.config = config
        self.tools = {t.name: t for t in tools}
        self.middlewares = middlewares or []
        # openai client 在 run() 时按 config 实例化，避免 import 时副作用
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            kwargs: dict[str, Any] = {}
            if self.config.api_key:
                kwargs["api_key"] = self.config.api_key
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def _run_middlewares(self, state: AgentState) -> AgentState:
        """串行跑所有 middleware 的 before_model；返回 None 跳过该 mw。"""
        for mw in self.middlewares:
            if not hasattr(mw, "before_model"):
                continue
            try:
                ret = mw.before_model(state)
                # before_model 既支持 sync 又支持 async 实现——统一 await
                if asyncio.iscoroutine(ret):
                    ret = await ret
                if ret is not None:
                    state = ret
            except Exception as e:
                logger.warning("middleware %s.before_model failed: %s", type(mw).__name__, e)
        return state

    def _build_openai_messages(self, state: AgentState) -> list[dict]:
        """把 BaseMessage 列表转成 openai chat.completions 期望的 dict 形式。"""
        out: list[dict] = []
        # system_prompt 永远先放（保证 cache 前缀稳定）
        out.append({"role": "system", "content": self.config.system_prompt})
        for m in state.messages:
            cls = type(m).__name__
            if cls == "SystemMessage":
                out.append({"role": "system", "content": m.content})
            elif cls == "HumanMessage":
                out.append({"role": "user", "content": m.content})
            elif cls == "AIMessage":
                msg: dict = {"role": "assistant", "content": m.content or ""}
                tool_calls = getattr(m, "tool_calls", None) or []
                if tool_calls:
                    # langchain AIMessage.tool_calls 是 list[dict] 形如 {name, args, id}
                    msg["tool_calls"] = [
                        {
                            "id": tc.get("id"),
                            "type": "function",
                            "function": {
                                "name": tc.get("name"),
                                "arguments": json.dumps(tc.get("args", {}), ensure_ascii=False),
                            },
                        }
                        for tc in tool_calls
                    ]
                out.append(msg)
            elif cls == "ToolMessage":
                out.append({
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "content": m.content,
                })
        return out

    def _tool_schemas(self) -> list[dict]:
        """所有注册工具的 OpenAI schema 列表，喂给 LLM 让它知道能调什么。"""
        return [{"type": "function", "function": t.schema} for t in self.tools.values()]

    async def _consume_stream(
        self,
        stream: Any,
    ) -> AsyncIterator[dict]:
        """消费 openai 流式响应——yield token 增量 + 最后 yield 完整 ai_message + tool_calls。"""
        accumulated_content = ""
        # tool_calls 增量按 index 累积——OpenAI 协议每个 chunk 可能含部分 args JSON
        tool_calls_by_index: dict[int, dict] = {}
        usage: dict[str, Any] = {}
        finish_reason: str | None = None

        async for chunk in stream:
            if not chunk.choices:
                # 有些厂商把 usage 放在末尾的空 choices chunk 里
                if hasattr(chunk, "usage") and chunk.usage:
                    usage = {
                        "input_tokens": getattr(chunk.usage, "prompt_tokens", 0),
                        "output_tokens": getattr(chunk.usage, "completion_tokens", 0),
                        "total_tokens": getattr(chunk.usage, "total_tokens", 0),
                    }
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta.content:
                accumulated_content += delta.content
                yield {"type": "token", "delta": delta.content}
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_by_index:
                        tool_calls_by_index[idx] = {
                            "id": tc_delta.id or "",
                            "name": "",
                            "arguments": "",
                        }
                    if tc_delta.id:
                        tool_calls_by_index[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls_by_index[idx]["name"] += tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls_by_index[idx]["arguments"] += tc_delta.function.arguments
            if choice.finish_reason:
                finish_reason = choice.finish_reason

        # 解析累积的 tool_calls JSON
        parsed_tool_calls: list[ToolCall] = []
        for idx in sorted(tool_calls_by_index):
            raw = tool_calls_by_index[idx]
            try:
                args = json.loads(raw["arguments"]) if raw["arguments"] else {}
            except json.JSONDecodeError as e:
                logger.warning("tool_call args JSON parse failed: %s, raw=%r", e, raw["arguments"])
                args = {}
            parsed_tool_calls.append(ToolCall(
                id=raw["id"] or f"call_{uuid.uuid4().hex[:8]}",
                name=raw["name"],
                args=args,
            ))

        # 构造 AIMessage（langchain_core 期望 tool_calls 字段是 list[dict] 形）
        ai_msg = AIMessage(
            content=accumulated_content,
            tool_calls=[{"id": tc.id, "name": tc.name, "args": tc.args, "type": "tool_call"} for tc in parsed_tool_calls],
        )
        yield {
            "type": "model_end",
            "ai_message": ai_msg,
            "tool_calls": parsed_tool_calls,
            "finish_reason": finish_reason,
            "usage": usage,
        }

    async def _call_tool(self, tc: ToolCall) -> tuple[ToolCall, str, int, Exception | None]:
        """单个 tool 调用 + 计时 + 异常兜底。返回 (tc, output, elapsed_ms, exc)。"""
        spec = self.tools.get(tc.name)
        if spec is None:
            return tc, f"Error: 未注册的工具 '{tc.name}'", 0, None
        t0 = time.perf_counter()
        try:
            result = await asyncio.wait_for(spec.func(tc.args), timeout=self.config.tool_timeout_seconds)
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            return tc, str(result), elapsed_ms, None
        except asyncio.TimeoutError as e:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            return tc, f"Error: 工具 '{tc.name}' 超时 ({self.config.tool_timeout_seconds}s)", elapsed_ms, e
        except Exception as e:
            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            logger.exception("tool %s raised", tc.name)
            return tc, f"Error: 工具 '{tc.name}' 异常: {type(e).__name__}: {e}", elapsed_ms, e

    async def run(
        self,
        user_message: str,
        thread_id: str = "default",
        on_interrupt: Callable[[list[ToolCall]], Awaitable[bool]] | None = None,
        initial_messages: list[BaseMessage] | None = None,
    ) -> AsyncIterator[dict]:
        """主入口——yield 事件流到调用方（runner / channel）。

        Args:
            user_message: 用户当前轮的输入文本
            thread_id: 用于 middleware（如 DateReminder）的会话标识
            on_interrupt: HitL 回调——LLM 想调拦截工具时调用。返回 True=放行，False=拒绝
            initial_messages: 历史 messages（来自上轮 checkpoint）；不传走空白
        """
        state = AgentState(
            messages=list(initial_messages) if initial_messages else [],
            thread_id=thread_id,
        )
        state.messages.append(HumanMessage(content=user_message))

        client = self._get_client()

        for step in range(self.config.recursion_limit):
            state.iter_count = step

            # 跑 middleware 链
            state = await self._run_middlewares(state)

            yield {"type": "model_start", "call_seq": step + 1}

            # 调 LLM 流式
            try:
                stream = await client.chat.completions.create(
                    model=self.config.model,
                    messages=self._build_openai_messages(state),
                    tools=self._tool_schemas() if self.tools else None,
                    stream=True,
                    stream_options={"include_usage": True},  # 让最后一个 chunk 带 usage
                )
            except Exception as e:
                logger.exception("LLM call failed at step=%d", step)
                yield {"type": "error", "exception": e}
                return

            # 消费流，转发 token 事件，攒最后的 ai_message + tool_calls
            ai_msg: AIMessage | None = None
            tool_calls: list[ToolCall] = []
            async for ev in self._consume_stream(stream):
                if ev["type"] == "token":
                    yield ev
                elif ev["type"] == "model_end":
                    ai_msg = ev["ai_message"]
                    tool_calls = ev["tool_calls"]
                    yield ev

            assert ai_msg is not None, "stream 异常结束未给 ai_message"
            state.messages.append(ai_msg)

            # 没工具调用 = LLM 给最终答案了
            if not tool_calls:
                yield {"type": "complete", "final_message": ai_msg}
                return

            # HitL 拦截：列表里任一工具命中 interrupt_tools 就触发 callback
            intercepted = [tc for tc in tool_calls if tc.name in self.config.interrupt_tools]
            if intercepted and on_interrupt is not None:
                approved = await on_interrupt(tool_calls)
                yield {"type": "interrupt_resolved", "approved": approved}
                if not approved:
                    # 用户拒绝 = 把所有 tool_calls 标 ToolMessage("用户拒绝执行") 喂回 LLM
                    for tc in tool_calls:
                        state.messages.append(ToolMessage(
                            content="用户拒绝执行此操作。",
                            tool_call_id=tc.id,
                            name=tc.name,
                        ))
                    continue  # 让 LLM 看到拒绝消息后决定下一步

            # 并行调度所有 tool_calls
            for tc in tool_calls:
                yield {"type": "tool_start", "tool_call_id": tc.id, "tool_name": tc.name, "args": tc.args}

            results = await asyncio.gather(
                *[self._call_tool(tc) for tc in tool_calls],
                return_exceptions=False,
            )
            for tc, output, elapsed_ms, exc in results:
                tool_msg = ToolMessage(content=output, tool_call_id=tc.id, name=tc.name)
                state.messages.append(tool_msg)
                yield {
                    "type": "tool_end",
                    "tool_call_id": tc.id,
                    "tool_name": tc.name,
                    "output": output,
                    "elapsed_ms": elapsed_ms,
                    "error": exc is not None,
                }

        # 撞到 recursion_limit
        yield {"type": "recursion_limit", "iters": self.config.recursion_limit}
