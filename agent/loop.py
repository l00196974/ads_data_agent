"""Pure Python agent loop — 不依赖 deepagents / langchain.agents / langgraph。

替代 `deepagents.create_deep_agent` + `agent.astream_events(v2)` 消费链。
消息数据类来自 `agent.messages`（自管的 dataclass，替代 langchain_core.messages）。

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
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

from agent.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from openai import AsyncOpenAI

from agent.token_breakdown import estimate_breakdown

logger = logging.getLogger(__name__)


def _dump_request(messages: list[dict], model: str, step: int, thread_id: str) -> None:
    """ADS_AGENT_DEBUG_DUMP=1 时把当前 LLM 请求 dump 到 data/debug_dumps/ 供事后分析。

    复现"LLM 返空"问题时设这个 env var 重跑，文件会落到磁盘，对照日志里
    `model_end: finish_reason=...` 一行能拼出完整故事：发了什么形状的 prompt
    → LLM 怎么响应的。仅 debug 用，生产别开（消息含敏感数据）。
    """
    try:
        ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        out_dir = Path("data") / "debug_dumps"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"req-{thread_id}-step{step}-{ts}.json"
        out_path.write_text(
            json.dumps({"model": model, "messages": messages}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("debug dump: %s", out_path)
    except Exception as e:
        logger.warning("debug dump failed: %s", e)


def _read_attr(obj: Any, dotted_path: str) -> Any:
    """安全读取嵌套属性（如 'prompt_tokens_details.cached_tokens'）——任意环节
    缺失返回 None。OpenAI 兼容 provider 字段不齐时不抛错。"""
    cur = obj
    for part in dotted_path.split("."):
        cur = getattr(cur, part, None)
        if cur is None:
            return None
    return cur


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

            # 企业内网 SSL 自签证书拦截场景（Huawei 内网 / 公司 HTTPS 代理）：
            # PyInstaller bundle 用包内 certifi 不含公司 CA → SSL CERTIFICATE_VERIFY_FAILED。
            # 设 LLM_VERIFY_SSL=false 关掉 SSL 校验绕过——内网默认安全，OpenAI cert 反正
            # 被公司代理截掉了，校验也没意义。
            verify_env = os.environ.get("LLM_VERIFY_SSL", "").lower()
            if verify_env in ("false", "0", "no", "off"):
                import httpx
                logger.warning("LLM_VERIFY_SSL=false —— SSL 证书校验已关闭")
                kwargs["http_client"] = httpx.AsyncClient(verify=False)

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
                # Qwen3 / DeepSeek-R1 等 thinking 模式必须把上一轮的 reasoning_content
                # 原样回传，否则 server 返 400 "must be passed back to the API"
                reasoning = getattr(m, "reasoning_content", None)
                if reasoning:
                    msg["reasoning_content"] = reasoning
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
        t_request_start: float,
    ) -> AsyncIterator[dict]:
        """消费 openai 流式响应——yield token 增量 + 最后 yield 完整 ai_message + tool_calls。

        `t_request_start` 是上层调 `client.chat.completions.create` 之前的 perf_counter()——
        用来算 ttft_ms（首 token 延迟）和 duration_ms（整段响应耗时），喂给 runner 算 TPS。
        """
        accumulated_content = ""
        # tool_calls 增量按 index 累积——OpenAI 协议每个 chunk 可能含部分 args JSON
        tool_calls_by_index: dict[int, dict] = {}
        usage: dict[str, Any] = {}
        finish_reason: str | None = None
        ttft_ms: int | None = None  # 首 token 延迟（ms）
        # reasoning_content：Qwen3 / DeepSeek-R1 / 火山方舟某些推理模型把"思考过程"
        # 单独输出到这个字段（不在 delta.content 里）。如果模型 reasoning-only 输出
        # 而我们只读 content，content 就是空的，但 usage 仍报输入 tokens——
        # 表现为"LLM 没返回任何东西"。我们把 reasoning 也累积起来，至少能 fallback
        # 当 content 时给用户看，避免静默空答。
        accumulated_reasoning = ""

        async for chunk in stream:
            # **每个 chunk 都试一遍抓 usage**——OpenAI 官方 stream_options=include_usage
            # 把 usage 放在尾包（空 choices）；但部分兼容 endpoint（DeepSeek 自托管 /
            # 火山方舟 CodePlan 等）会放到带 choices 的 chunk 上，或在中间某帧。
            # 全部 chunk 看一眼，取到非空就覆盖（最后一次写入是最新数据）。
            if hasattr(chunk, "usage") and chunk.usage:
                # cache 命中字段——不同厂商命名各异，全试一遍取最大非零值
                cache_read = (
                    _read_attr(chunk.usage, "prompt_tokens_details.cached_tokens")
                    or _read_attr(chunk.usage, "prompt_cache_hit_tokens")
                    or _read_attr(chunk.usage, "cached_tokens")
                    or 0
                )
                usage = {
                    "input_tokens": getattr(chunk.usage, "prompt_tokens", 0) or 0,
                    "output_tokens": getattr(chunk.usage, "completion_tokens", 0) or 0,
                    "total_tokens": getattr(chunk.usage, "total_tokens", 0) or 0,
                    "cache_read_tokens": int(cache_read or 0),
                }
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta.content:
                if ttft_ms is None:
                    ttft_ms = int((time.perf_counter() - t_request_start) * 1000)
                accumulated_content += delta.content
                yield {"type": "token", "delta": delta.content}
            # 推理模型的 reasoning_content 字段（OpenAI 标准之外的扩展）
            # isinstance str 检查：openai SDK 真返 None 时 None；某些 chunk 类型
            # auto-attr 可能给非 str（如 mock 的 MagicMock）——只接受 str
            reasoning_delta = getattr(delta, "reasoning_content", None)
            if reasoning_delta and isinstance(reasoning_delta, str):
                accumulated_reasoning += reasoning_delta
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

        # 统计耗时——duration_ms 是从请求开始到最后一个 chunk
        duration_ms = int((time.perf_counter() - t_request_start) * 1000)

        # API 没返 usage 时，本地用累积字符长度估算 output——保证前端 metrics-bar
        # 有数字看，不至于全 None。input_tokens 在 run() 里用 breakdown 估算填补
        # （那里能拿到 breakdown）。estimated=True 标记让前端能区分"厂商精确" vs
        # "本地估算"。部分自托管 endpoint（某些 DeepSeek / Qwen 自部署）会无视
        # stream_options=include_usage，根本不传 usage。
        if not usage:
            approx_output_chars = len(accumulated_content) + len(accumulated_reasoning)
            approx_output_tokens = max(1, approx_output_chars // 2) if approx_output_chars else 0
            usage = {
                "input_tokens": 0,  # 待 run() 用 breakdown 填补
                "output_tokens": approx_output_tokens,
                "total_tokens": approx_output_tokens,
                "cache_read_tokens": 0,
                "estimated": True,
            }
            logger.warning(
                "API 没返 usage（厂商不报 token 数）——本地估算 output≈%d（基于字符长度）",
                approx_output_tokens,
            )

        if usage:
            usage["ttft_ms"] = ttft_ms or duration_ms  # 工具调用模式下没 token 流，ttft=duration
            usage["duration_ms"] = duration_ms

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

        # 关键诊断日志——LLM 返了什么 finish_reason、content 多大、reasoning 多大、
        # 有几个 tool_calls。空答场景（output_tokens=0 且无 tool_calls）这条 log
        # 直接定位是 finish_reason 异常 / reasoning-only / 还是别的
        logger.info(
            "model_end: finish_reason=%s content_chars=%d reasoning_chars=%d tool_calls=%d output_tokens=%d",
            finish_reason, len(accumulated_content), len(accumulated_reasoning),
            len(parsed_tool_calls), (usage or {}).get("output_tokens", 0),
        )

        # 空答兜底：content 空（或仅含空白如 "\n\n"）、无 tool_calls，但 reasoning
        # 不空——说明是推理模型把全部内容输到了 reasoning_content 字段。
        # 把 reasoning 当作 content 给用户，至少不要显示"什么都没有"。
        # 注意用 .strip() 判空——thinking 模式有时只输出 "\n\n" 占位
        effective_content = accumulated_content
        if not accumulated_content.strip() and not parsed_tool_calls and accumulated_reasoning:
            effective_content = accumulated_reasoning
            logger.warning(
                "LLM content empty/whitespace-only (%d chars) but reasoning_content has %d chars — falling back to reasoning",
                len(accumulated_content), len(accumulated_reasoning),
            )

        # 构造 AIMessage——reasoning_content 必须存进来，因为 Qwen3/DeepSeek-R1 等
        # thinking 模式 API 要求"下一轮请求里 assistant message 必须原样回传 reasoning_content"，
        # 否则 server 返 400 "reasoning_content must be passed back to the API"
        ai_msg = AIMessage(
            content=effective_content,
            tool_calls=[{"id": tc.id, "name": tc.name, "args": tc.args, "type": "tool_call"} for tc in parsed_tool_calls],
            reasoning_content=accumulated_reasoning or None,
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

            # 算 breakdown：在 middleware 跑完后、send to LLM 之前——messages 已是
            # 真实将被打包发出的最终形态（含 DateReminder 注入等）
            breakdown = estimate_breakdown(
                state.messages, self.config.system_prompt, self.config.model
            )

            # 调 LLM 流式
            t_request_start = time.perf_counter()
            openai_messages = self._build_openai_messages(state)

            # 诊断日志：打 messages 结构摘要（不打内容避免日志爆炸）。
            # 排查"LLM 返空"时 这条 log + 后面 model_end log 能拼出完整故事：
            #   "发了什么形状的 prompt" → "LLM 返了什么 finish_reason"
            roles_summary = {}
            for m in openai_messages:
                r = m.get("role", "?")
                roles_summary[r] = roles_summary.get(r, 0) + 1
            tool_calls_count = sum(len(m.get("tool_calls") or []) for m in openai_messages if m.get("role") == "assistant")
            logger.info(
                "model_start step=%d roles=%s tool_calls_in_history=%d total_msgs=%d",
                step + 1, dict(roles_summary), tool_calls_count, len(openai_messages),
            )

            # ADS_AGENT_DEBUG_DUMP=1 → 把完整 messages dump 到磁盘供事后调试
            if os.environ.get("ADS_AGENT_DEBUG_DUMP", "").lower() in ("1", "true", "yes"):
                _dump_request(openai_messages, self.config.model, step + 1, state.thread_id)

            try:
                stream = await client.chat.completions.create(
                    model=self.config.model,
                    messages=openai_messages,
                    tools=self._tool_schemas() if self.tools else None,
                    stream=True,
                    stream_options={"include_usage": True},  # 让最后一个 chunk 带 usage
                )
            except Exception as e:
                logger.exception("LLM call failed at step=%d", step)
                # final_messages 暴露给 caller 持久化——含 user message + 已执行工具结果，
                # 即使 LLM 失败也别丢上下文（下次 resume 还能看到已查到的数据）
                yield {"type": "error", "exception": e, "final_messages": list(state.messages)}
                return

            # 消费流，转发 token 事件，攒最后的 ai_message + tool_calls
            ai_msg: AIMessage | None = None
            tool_calls: list[ToolCall] = []
            async for ev in self._consume_stream(stream, t_request_start):
                if ev["type"] == "model_end" and breakdown is not None:
                    # 把 breakdown 合到 usage 里——runner_v2 / subagent 走 **usage 自动透传给 channel
                    usage = ev.setdefault("usage", {})
                    usage["breakdown"] = breakdown
                    # API 没返 usage 时，用 breakdown.estimated_total 补 input_tokens
                    # 让前端能显示数字（标 estimated 区分精确口径）
                    if usage.get("estimated") and not usage.get("input_tokens"):
                        est_in = breakdown.get("estimated_total", 0)
                        if est_in:
                            usage["input_tokens"] = est_in
                            usage["total_tokens"] = est_in + (usage.get("output_tokens") or 0)
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
                # final_messages: 完整的本轮 messages 列表（含 system reminder /
                # user / 多轮 ai+tool / 最后 ai）。caller 拿这个落库或回填上下文。
                yield {
                    "type": "complete",
                    "final_message": ai_msg,
                    "final_messages": list(state.messages),
                }
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
        yield {
            "type": "recursion_limit",
            "iters": self.config.recursion_limit,
            "final_messages": list(state.messages),
        }
