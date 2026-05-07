import asyncio
import json
import logging
import os
import time
from datetime import datetime

from langchain_core.messages import HumanMessage
from langgraph.errors import GraphInterrupt
from langgraph.types import Command

from .base import BaseChannel
from agent import auto_approve
from agent.config import load_config
from agent.skill_loader import extract_artifact_ids_from_output
from agent.user_space import UserSpace


logger = logging.getLogger(__name__)


def _safe_json(obj, max_chars: int = 1500) -> str:
    """工具 input/output 进日志时的紧凑序列化，超长截断。"""
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        s = repr(obj)
    return s if len(s) <= max_chars else s[:max_chars] + f"...<+{len(s)-max_chars}>"


def _extract_usage(output) -> dict:
    """从 on_chat_model_end 的 output 拿 usage_metadata。

    output 可能是 AIMessage 或 dict（不同 langchain 版本 / chunk 结构不一）。
    缺字段时返回 {}—— 少数 OpenAI-compat 厂商不上报 usage，前端按"-"显示。
    cache_read_tokens 走 langchain 标准的 input_token_details.cache_read（多数厂商都填）。
    """
    if output is None:
        return {}
    usage = getattr(output, "usage_metadata", None)
    if usage is None and isinstance(output, dict):
        usage = output.get("usage_metadata")
    if not usage:
        return {}
    try:
        details = usage.get("input_token_details") or {}
        cache_read = details.get("cache_read", 0) if isinstance(details, dict) else 0
    except Exception:
        cache_read = 0
    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "cache_read_tokens": cache_read,
    }


def _extract_model_name(metadata: dict, output) -> str | None:
    """从 langgraph event metadata 或 AIMessage.response_metadata 拿模型名。"""
    if isinstance(metadata, dict):
        # langgraph 把 model_name / ls_model_name 塞进 metadata
        for k in ("ls_model_name", "model_name"):
            v = metadata.get(k)
            if v:
                return str(v)
    if output is None:
        return None
    rm = getattr(output, "response_metadata", None)
    if isinstance(rm, dict):
        return rm.get("model_name") or rm.get("model")
    return None


# tiktoken encoder 进程级缓存——按 (model_name) 索引，避免每次 LLM 调用重新加载
# 同名 encoder（cl100k_base 几兆，加载几十毫秒）。
_ENCODER_CACHE: dict[str, object] = {}


def _get_encoder(model: str | None):
    """按模型名拿 tiktoken encoder，未识别的模型 fallback 到 cl100k_base 估算。

    各厂商 tokenizer 不完全相同（OpenAI 用 tiktoken；DeepSeek / 通义 / 火山方舟
    各有自己的），但 token 数量级与 cl100k 偏差通常 <5%——对"占比可视化"够用。
    精确数字以 LLM 厂商 usage_metadata 上报的 input_tokens 总数为准（metrics.input_tokens）。

    返回 None 时调用方走"无估算"分支（不给 breakdown）。
    """
    try:
        import tiktoken
    except ImportError:
        return None
    key = model or "__default__"
    if key in _ENCODER_CACHE:
        return _ENCODER_CACHE[key]
    enc = None
    if model:
        try:
            enc = tiktoken.encoding_for_model(model)
        except Exception:
            pass
    if enc is None:
        try:
            enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            enc = None
    _ENCODER_CACHE[key] = enc
    return enc


def _count_text(enc, text) -> int:
    """安全计数：text 可能是 str / list（多模态）/ 其它，统一转 str 兜底。"""
    if not text:
        return 0
    if isinstance(text, list):
        # 多模态 content：取所有 text 部分拼起来；图像等忽略不计 token
        text = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in text)
    elif not isinstance(text, str):
        text = str(text)
    if not text:
        return 0
    try:
        return len(enc.encode(text))
    except Exception:
        # encoder 偶尔遇到特殊符号可能 raise，按字符数粗估（中文约 1 字符 ≈ 1 token）
        return len(text)


def _breakdown_input_tokens(messages, model: str | None) -> dict | None:
    """把传给 LLM 的 messages 按角色分桶，估算各类 tokens 占用。

    分桶策略（与 langchain message class 对齐）：
      - system:       SystemMessage —— PLAN_INSTRUCTION + 当前日期 + skill prompt
      - tool_results: ToolMessage —— 之前轮工具调用的返回值（往往是大头）
      - history:      除最后一条 HumanMessage 之外的 Human/AIMessage —— 多轮历史对话
      - current:      列表里**最后一条** HumanMessage —— 当前轮的新输入

    说明：
      - AIMessage 的 tool_calls（工具调用 JSON）也算 history 一部分（langchain 序列化
        到 messages 里发给 LLM）。当前简化：只 count content 部分，tool_calls 偏差几十
        token，对 % 显示无影响。
      - encoder 用 tiktoken cl100k_base 估算（多数厂商偏差 <5%），完全不一致时退化按
        字符数粗估。

    返回 None 当 tiktoken 不可用 / messages 空 —— 前端按"无 breakdown"显示。
    """
    enc = _get_encoder(model)
    if enc is None or not messages:
        return None
    # langgraph 给 batch（外层 list[list[BaseMessage]]）；通常 batch_size=1
    if messages and isinstance(messages[0], list):
        messages = messages[0]

    # 找出"最后一条 HumanMessage"——它代表当前轮的新输入，单独算一桶
    last_human_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if type(messages[i]).__name__ == "HumanMessage":
            last_human_idx = i
            break

    buckets = {"system": 0, "tool_results": 0, "history": 0, "current": 0}
    for i, m in enumerate(messages):
        cls = type(m).__name__
        n = _count_text(enc, getattr(m, "content", ""))
        if cls == "SystemMessage":
            buckets["system"] += n
        elif cls == "ToolMessage":
            buckets["tool_results"] += n
        elif cls == "HumanMessage" and i == last_human_idx:
            buckets["current"] += n
        else:
            # HumanMessage（非最后） + AIMessage —— 历史对话
            buckets["history"] += n
    buckets["estimated_total"] = sum(buckets.values())
    return buckets


# deepagents 默认 middleware 注入的"自我管理"工具——对最终用户而言不是业务进度，
# 是 agent 在 langgraph state 上做内部簿记（更新 todo 状态 / 读虚拟文件系统）。
# 把这些从 step 事件里过滤掉，避免前端步骤列表被噪音淹没。
#
# 注意：**write_file / edit_file 故意不在过滤名单**——它们在用户工作区写文件，是
# 用户真关心的"agent 在我空间里做了什么"业务事件，必须展示。同时它们在 config.yaml
# 默认被列为 medium_risk，会触发 HitL 弹窗确认。
_FRAMEWORK_META_TOOLS = frozenset({
    "write_todos", "read_todos",     # TodoListMiddleware：agent 自己维护 TODO 状态
    "read_file", "ls",               # FilesystemMiddleware 只读类：read/list 噪音多无价值
    "glob", "grep",                  # FilesystemMiddleware 检索类：LLM 在和 ToolOutputTruncation
                                     # 卸盘文件搏斗时调用（找 call_xxx.json），用户看到困惑
})


def _is_meta_tool(name: str) -> bool:
    """识别 deepagents 默认 middleware 注入的状态管理工具——对用户没业务含义，
    不进 step 事件流。新增此类工具时把名字加进 `_FRAMEWORK_META_TOOLS`。
    """
    return name in _FRAMEWORK_META_TOOLS


# 子 Agent 技术 name → 中文角色名（前端步骤展示用）。新增子 Agent 时在 config.yaml
# 加，同时这里加映射。映射不到时直接显示技术 name（不致命，只是不太友好）。
_SUBAGENT_CN_LABELS = {
    "data-fetcher": "取数员",
    "data-analyst": "数据分析师",
    "issue-diagnostician": "问题诊断师",
    "general-purpose": "通用助手",
}


def _format_tool_label(tool_name: str, input_data) -> str:
    """前端展示用的工具名 + 实际参数。

    `run_command` 是 SKILL.md 系统统一的派发器，工具名本身不可读——直接把
    LLM 传给它的完整 CLI 命令串露出来（`query-metrics --metrics revenue
    --start-date 2026-03-27 ...`），用户看到具体在跑什么、传了什么参数，
    不再黑盒。

    `task` 是派工到子 Agent 的工具——直接显示成"派给 XXX（中文角色名）"，
    不要把整个 task description 露出来（动辄上千字）。子 Agent 内部的工具
    调用在 SSE 事件里独立带 `subagent` 字段，由前端嵌套展示。

    其他业务工具：原名 + 主要参数（用 JSON 紧凑表示，截断到 200 字符）。"""
    if not isinstance(input_data, dict) or not input_data:
        return tool_name

    if tool_name == "run_command":
        cmd = input_data.get("command", "")
        if isinstance(cmd, str) and cmd.strip():
            return cmd.strip()
        return tool_name

    if tool_name == "task":
        st = input_data.get("subagent_type", "")
        cn = _SUBAGENT_CN_LABELS.get(st, st or "子 Agent")
        return f"🤖 派给 {cn}"

    # 通用工具：把全部 input 紧凑序列化展出，让 LLM 传的参数透明可见
    try:
        import json
        snippet = json.dumps(input_data, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        snippet = str(input_data)
    if len(snippet) > 200:
        snippet = snippet[:200] + "..."
    return f"{tool_name} {snippet}"


async def _resume_safely(agent, resume_value, config: dict) -> None:
    """resume 期间收到 cancel 不能直接打断 ainvoke——会让 langgraph 写到一半就停，
    checkpointer 留下半完成 state，下一轮 restart 读到 corrupt 状态。
    把 ainvoke 包成独立 task + shield，cancel 时也等它跑完再 raise。

    `resume_value` 直接透传给 `Command(resume=...)`：
      - langchain HumanInTheLoopMiddleware 抛的 interrupt 期望 HITLResponse 格式：
          `{"decisions": [{"type": "approve"} or {"type": "reject", "message": ...}, ...]}`
        decision 数量必须 = interrupt 时的 action_requests 数量（middleware 会校验）。
      - 历史/简单 interrupt 直接传 bool 也能工作（langgraph 把 resume value 原样
        返回给 interrupt() 调用点）。
      _handle_interrupt 现在统一构造 HITLResponse dict 传进来。
    """
    inner = asyncio.create_task(agent.ainvoke(Command(resume=resume_value), config=config))
    try:
        await asyncio.shield(inner)
    except asyncio.CancelledError:
        try:
            await inner
        except Exception:
            pass
        raise


def _build_hitl_response(approve: bool, num_actions: int, reject_message: str = "") -> dict:
    """构造 langchain HITLResponse 格式：每个 action 一个 decision。

    我们当前 UX 是"全部一起 approve / reject"——所有待确认的 action 共享一个用户决定。
    后续如果想做"逐个审批"（一个弹窗里勾每个 action），改这个函数即可，runner 上层
    不用动。

    `num_actions` 至少为 1——HITL 不会触发 0 actions 的 interrupt。
    """
    n = max(1, num_actions)
    if approve:
        decisions = [{"type": "approve"} for _ in range(n)]
    else:
        d = {"type": "reject"}
        if reject_message:
            d["message"] = reject_message
        decisions = [dict(d) for _ in range(n)]
    return {"decisions": decisions}


def _extract_tool_name_from_interrupt(iv) -> str:
    """从 langchain HITLRequest payload 拿被拦截工具名。

    标准格式（HumanInTheLoopMiddleware 抛的）:
        {"action_requests": [{"action": "tool_name", "args": {...}, "description": "..."}], ...}
    旧/简化格式（项目内自定义可能用过）:
        {"message": "...", "preview": [...], "tool_name": "..."}

    都尝试一下，拿不到就返回空串（runner 决策时安全降级到"不在白名单 → 弹窗"）。
    """
    if not isinstance(iv, dict):
        return ""
    # 旧格式直接读
    if iv.get("tool_name"):
        return str(iv["tool_name"])
    # langchain HITLRequest
    requests = iv.get("action_requests") or []
    if requests and isinstance(requests, list):
        first = requests[0]
        if isinstance(first, dict) and first.get("action"):
            return str(first["action"])
    return ""


async def _handle_interrupt(
    iv,
    channel: BaseChannel,
    agent,
    config: dict,
    interrupt_cfg,
) -> None:
    """统一的 interrupt 处理：解 payload → 决策（白名单/弹窗）→ 加白名单 → resume agent。

    决策流程：
      1. 拿工具名（拿不到就当"未知工具"走弹窗，安全保守）
      2. 工具是 high_risk → 强制弹窗，**忽略**白名单（即使勾过也再问）
      3. 工具是 medium_risk + 在 auto_approve 白名单 → 直接 approve，跳过弹窗
      4. 弹窗 → 用户 approve + 勾"以后不再问" → 写白名单
    """
    if not isinstance(iv, dict):
        iv = {"message": str(iv), "preview": []}
    tool_name = _extract_tool_name_from_interrupt(iv)
    risk = interrupt_cfg.classify(tool_name) if tool_name else "medium"
    message = iv.get("message", "") or iv.get("description", "") or f"即将执行:{tool_name or '未知工具'}"
    preview = iv.get("preview", [])
    # action_requests 长度 = LLM 一次输出的并行 medium/high tool_calls 数。
    # langchain HumanInTheLoopMiddleware 校验 decisions 数 == action_requests 数，
    # 不一致直接 raise——所以构造 HITLResponse 时必须按这个长度。
    num_actions = len(iv.get("action_requests") or []) or 1

    # medium_risk + 在白名单 → 自动通过（high_risk 即使在白名单也不跳过——硬约束）
    if risk == "medium" and auto_approve.contains(tool_name):
        logger.info("hitl auto-approved tool=%s (in whitelist)", tool_name)
        await _resume_safely(agent, _build_hitl_response(True, num_actions), config)
        return

    logger.info("hitl prompting tool=%s risk=%s actions=%d", tool_name, risk, num_actions)
    approve, remember = await channel.wait_for_confirm(
        message, preview, tool_name=tool_name, risk_level=risk,
    )
    # 只有 medium_risk 才允许加白名单——high_risk 永远不能"以后不再问"
    if approve and remember and risk == "medium" and tool_name:
        auto_approve.add(tool_name)
        logger.info("hitl added to auto_approve: %s", tool_name)
    logger.info("hitl resolved tool=%s approve=%s remember=%s risk=%s", tool_name, approve, remember, risk)
    reject_msg = "" if approve else f"用户拒绝执行 {tool_name or '此操作'}"
    await _resume_safely(agent, _build_hitl_response(approve, num_actions, reject_msg), config)


class AgentRunner:
    async def run(
        self,
        channel: BaseChannel,
        message: str,
        build_agent_fn,
        config: dict,
    ) -> None:
        # 当前没有 channel-bound 工具——send_plan 已删（仅推一次性"计划预告"列表
        # 没有状态更新，价值低于多 1 轮 LLM round trip 的代价；任务进度由 step 事件
        # 自动承载，用户在思考分组里直接看到每个工具的 start/end）。
        # 未来如果要加 send_progress / send_chart 等同类工具，在这里 inline 构造
        # 闭包绑定 channel 的 StructuredTool 即可——参考 git log 找 send_plan 的实现样板。
        agent = build_agent_fn(extra_tools=[])

        # 解析 thread_id 拿 user_id（thread_id = "{user_id}_{conversation_id}"）
        thread_id = config.get("configurable", {}).get("thread_id", "")
        user_id = thread_id.split("_", 1)[0] if thread_id else "anon"
        cfg = load_config()
        user_space = UserSpace(user_id, cfg.persistence.data_dir)

        # 仅用于 progress 文案区分"规划中"vs"执行中"——状态推断已经下放给前端。
        business_tools_started = 0

        logger.info(
            "agent_run START thread=%s message_chars=%d",
            thread_id, len(message),
        )

        # 被 cancel 时不要关 channel——chat handler 在追问场景下要复用它继续推 SSE。
        # 只有正常结束 / 普通异常时才发 done 事件。
        should_close = True
        # tool_call_id → 调用元信息（tool_name / parent / start_t）。on_tool_end 时配对，
        # 实在配不上才退化到 tool_name 本身。run_id 在 langgraph v2 stream 里是稳定的。
        tool_calls_inflight: dict[str, dict] = {}
        chat_model_calls = 0
        # run_id → { t_start, ttft_ms, call_seq, subagent }——LLM metrics 配对所需。
        # on_chat_model_start 写入，stream 第一个 chunk 记 ttft，end 时取出 + 推 send_metrics。
        chat_calls_inflight: dict[str, dict] = {}
        # context 进度条所需阈值——SummarizationMiddleware 的 trigger，超过即压缩
        summarization_trigger = cfg.agent.long_context.summarization_trigger_tokens or 0
        # 子 Agent 嵌套栈：on_tool_start tool=task 时压栈（subagent_type），on_tool_end
        # 时弹栈。栈非空时 = 当前在某个子 Agent 内部，子 Agent 内部触发的所有 step 事件
        # 都会带上栈顶 subagent name，前端据此做嵌套展示。
        # 用栈而不是单个变量是为了未来扩展（虽然当前架构 deepagents 不允许 task 嵌套）。
        subagent_stack: list[str] = []
        # 子 Agent 当前 LLM 调用累计字数（每次 on_chat_model_start 重置）。
        # 用于在子 Agent reasoning 阶段（工具调用之间、生成最终 ToolMessage 时）
        # 给前端推 progress 字数心跳——否则用户看到"派给 XXX 执行中..."几十秒
        # 没动静，以为卡死。
        subagent_thinking_chars = 0
        try:
            async for event in agent.astream_events(
                {"messages": [HumanMessage(content=message)]},
                config=config,
                version="v2",
            ):
                kind = event["event"]
                if kind == "on_chat_model_start":
                    chat_model_calls += 1
                    # input.messages 是 [[msg, msg, ...]] 嵌套结构（langgraph 给 batch 的）
                    input_data = event.get("data", {}).get("input", {})
                    msgs = input_data.get("messages") if isinstance(input_data, dict) else None
                    msgs_count = len(msgs[0]) if msgs and isinstance(msgs, list) and msgs and isinstance(msgs[0], list) else 0
                    metadata = event.get("metadata", {}) or {}
                    node = metadata.get("langgraph_node", "?")
                    run_id = event.get("run_id", "?")
                    # breakdown：把传给 LLM 的 messages 按角色分类估算 tokens（tiktoken），
                    # end 时合并到 metrics payload —— 让前端能拆出 system / tool_results /
                    # history / current 各占多少。input_data 是 dict 形如 {"messages": [...]}。
                    msgs_for_breakdown = input_data.get("messages") if isinstance(input_data, dict) else None
                    model_name = metadata.get("ls_model_name") or metadata.get("model_name")
                    breakdown = _breakdown_input_tokens(msgs_for_breakdown, model_name) if msgs_for_breakdown else None
                    # 记录 LLM 调用开始时间，等 on_chat_model_end 时算 duration / TPS；
                    # 第一个 stream chunk 到达时把 ttft_ms 填进来。
                    chat_calls_inflight[run_id] = {
                        "t_start": time.perf_counter(),
                        "ttft_ms": None,
                        "call_seq": chat_model_calls,
                        "subagent": subagent_stack[-1] if subagent_stack else None,
                        "breakdown": breakdown,
                    }
                    logger.info(
                        "on_chat_model_start #%d node=%s msgs_in_context=%d run_id=%s subagent_ctx=%s",
                        chat_model_calls, node, msgs_count, run_id,
                        subagent_stack[-1] if subagent_stack else "-",
                    )
                    # 子 Agent 内部 LLM 调用：在 thinking 分组里加一条"思考分析中"占位 step。
                    # 否则子 Agent 在工具调用之间（甚至生成最终 ToolMessage 时）reasoning
                    # 几十秒，前端只看到"派给 XXX 执行中..."一条 stuck 标记，像卡死了。
                    # 这条 step 在 on_chat_model_end 时配对关闭。
                    if subagent_stack:
                        cn = _SUBAGENT_CN_LABELS.get(subagent_stack[-1], subagent_stack[-1])
                        await channel.send_step(f"🧠 {cn}思考分析", "tool_start", subagent=subagent_stack[-1])
                        subagent_thinking_chars = 0
                    # Cover the silent gap before the LLM emits its first tool_call.
                    msg = "AI 正在规划任务..." if business_tools_started == 0 else "AI 正在准备下一步..."
                    await channel.send_progress(msg)
                elif kind == "on_chat_model_end":
                    output = event.get("data", {}).get("output")
                    tool_calls = []
                    if output is not None:
                        # AIMessage / dict 都可能
                        tc = getattr(output, "tool_calls", None) or (
                            output.get("tool_calls") if isinstance(output, dict) else None
                        )
                        if tc:
                            tool_calls = [
                                {"name": c.get("name"), "args_keys": list((c.get("args") or {}).keys())}
                                for c in tc
                            ]
                    metadata = event.get("metadata", {}) or {}
                    run_id = event.get("run_id", "?")
                    inflight = chat_calls_inflight.pop(run_id, {})
                    # 算 metrics 并推：duration / TPS / context_used_pct + 厂商上报的 usage
                    t_start = inflight.get("t_start")
                    elapsed_ms = int((time.perf_counter() - t_start) * 1000) if t_start else None
                    usage = _extract_usage(output)
                    out_tokens = usage.get("output_tokens") or 0
                    tps = (out_tokens * 1000.0 / elapsed_ms) if elapsed_ms and elapsed_ms > 0 and out_tokens else None
                    in_tokens = usage.get("input_tokens") or 0
                    ctx_pct = (in_tokens * 100.0 / summarization_trigger) if summarization_trigger and in_tokens else None
                    metrics_payload = {
                        "call_seq": inflight.get("call_seq"),
                        "subagent": inflight.get("subagent"),
                        "model": _extract_model_name(metadata, output),
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "total_tokens": usage.get("total_tokens"),
                        "cache_read_tokens": usage.get("cache_read_tokens"),
                        "duration_ms": elapsed_ms,
                        "ttft_ms": inflight.get("ttft_ms"),
                        "tps": round(tps, 2) if tps else None,
                        "context_used_pct": round(ctx_pct, 2) if ctx_pct else None,
                        "context_trigger": summarization_trigger or None,
                        # tiktoken 估算的各部分 tokens 占用——前端进度条分段叠加用。
                        # 与厂商上报的 input_tokens 总数可能有 <5% 偏差（不同 tokenizer），
                        # 仅用作"占比可视化"，不参与计费 / 触发判断。
                        "breakdown": inflight.get("breakdown"),
                    }
                    logger.info(
                        "on_chat_model_end node=%s tool_calls=%d subagent_ctx=%s in=%s out=%s tps=%s ttft=%s ctx=%s%% %s",
                        metadata.get("langgraph_node", "?"),
                        len(tool_calls),
                        subagent_stack[-1] if subagent_stack else "-",
                        usage.get("input_tokens"), usage.get("output_tokens"),
                        metrics_payload["tps"], metrics_payload["ttft_ms"], metrics_payload["context_used_pct"],
                        _safe_json(tool_calls, 600),
                    )
                    # 推 metrics 给 channel —— WebSSE 既推 SSE 也落库；CLI 打印一行
                    await channel.send_metrics(metrics_payload)

                    # **reasoning_finalize**：如果这次 LLM 调用产出了 tool_calls，说明它的
                    # content 是中间 reasoning（"我先找文件位置"、"切小 page-size 重试"等），
                    # 不该留在主气泡里。流式期间已经把 token 推到了 channel.send_token，
                    # 这里通知前端把当前流式气泡降级为思考分组里的一条，避免污染最终回复。
                    # 子 Agent 内部 token 本来就没推（line on_chat_model_stream），不需要降级。
                    if not subagent_stack and tool_calls:
                        c = getattr(output, "content", None) if output is not None else None
                        if c is None and isinstance(output, dict):
                            c = output.get("content")
                        if isinstance(c, list):
                            # 多模态 content：取 text 部分拼起来
                            c = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)
                        await channel.send_reasoning(c if isinstance(c, str) else "")

                    # 子 Agent 内部 LLM 调用结束：关闭对应的"思考分析"step
                    if subagent_stack:
                        cn = _SUBAGENT_CN_LABELS.get(subagent_stack[-1], subagent_stack[-1])
                        await channel.send_step(f"🧠 {cn}思考分析", "tool_end", subagent=subagent_stack[-1])
                elif kind == "on_chat_model_stream":
                    # TTFT 测量：第一个 chunk 到达时记下首 token 延迟
                    run_id = event.get("run_id", "?")
                    inflight = chat_calls_inflight.get(run_id)
                    if inflight and inflight.get("ttft_ms") is None and inflight.get("t_start"):
                        inflight["ttft_ms"] = int((time.perf_counter() - inflight["t_start"]) * 1000)

                    # 子 Agent 内部的 LLM token 不推给用户——子 Agent 的输出是
                    # 给主 Agent 的 ToolMessage，会被主 Agent 消化后再以最终
                    # markdown token 流的形式产出。如果在这里把子 Agent 的 token
                    # 也推到前端，用户会看到"子 Agent 自己的报告 + 主 Agent 又
                    # 总结一份"两份重复内容。
                    if subagent_stack:
                        # ============================================================
                        # 字数心跳：当前**不会**实际触发，但保留实现 + 单测锁住契约。
                        # ------------------------------------------------------------
                        # 原因：deepagents 的 task 工具内部用
                        #   `await subagent.ainvoke(state)`  (subagents.py:439)
                        # 而不是 astream_events——子 Agent 的 LLM token stream 事件
                        # **不冒泡**到外层 astream_events 循环，所以这个分支在生产里
                        # 几乎拿不到 chunk。后果：用户在子 Agent 长时间 reasoning 时
                        # 看不到字数滚动；好在我们已经通过 on_chat_model_start/end
                        # 推出"🧠 思考分析"占位 step，节奏感够了。
                        #
                        # 留着这段代码是为了：
                        # 1) 如果 deepagents 上游改成 astream 透传 stream 事件，
                        #    或我们后续 monkey-patch _build_task_tool，这段会自动
                        #    生效，不需要再改 runner；
                        # 2) test_subagent_progress_heartbeat_with_char_count 用 mock
                        #    注入 stream 事件，锁住"如果 stream 触发了，runner 按预期
                        #    推字数心跳"这个契约。
                        #
                        # 删掉的话以后想恢复就要重新论证 + 写代码，回归成本更高。
                        # ============================================================
                        chunk = event["data"].get("chunk")
                        content = chunk.content if chunk and hasattr(chunk, "content") else ""
                        if content:
                            prev = subagent_thinking_chars
                            subagent_thinking_chars += len(content)
                            if subagent_thinking_chars // 50 > prev // 50:
                                cn = _SUBAGENT_CN_LABELS.get(subagent_stack[-1], subagent_stack[-1])
                                await channel.send_progress(
                                    f"🧠 {cn}思考中（已生成 {subagent_thinking_chars} 字）..."
                                )
                        continue
                    chunk = event["data"].get("chunk")
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        await channel.send_token(chunk.content)
                elif kind == "on_tool_start":
                    tool_name = event.get("name", "tool")
                    input_data = event.get("data", {}).get("input", {})
                    metadata = event.get("metadata", {}) or {}
                    node = metadata.get("langgraph_node", "?")
                    run_id = event.get("run_id", "?")
                    tool_calls_inflight[run_id] = {
                        "tool_name": tool_name,
                        "node": node,
                        "t_start": asyncio.get_event_loop().time(),
                    }
                    logger.info(
                        "on_tool_start tool=%s node=%s meta=%s subagent_ctx=%s input=%s",
                        tool_name, node, _is_meta_tool(tool_name),
                        subagent_stack[-1] if subagent_stack else "-",
                        _safe_json(input_data),
                    )
                    # task 工具自身的 step 事件**在压栈之前**发出——它的 subagent 上下文
                    # 是"派给谁"，不是"谁内部触发的"，所以仍然属于上层（栈顶或主 Agent）。
                    # 然后再压栈，让 task 内部的工具 step 都带新栈顶的 subagent 标记。
                    parent_subagent = subagent_stack[-1] if subagent_stack else None
                    if not _is_meta_tool(tool_name):
                        business_tools_started += 1
                        step_msg = _format_tool_label(tool_name, input_data)
                        # run_command 时把命令首 token（=skill 子命令名）单独传给前端，
                        # 前端 metrics 栏聚合"本轮调用的 skill 链"靠这字段，不解析 msg
                        skill_subcmd = None
                        if tool_name == "run_command" and isinstance(input_data, dict):
                            cmd = (input_data.get("command") or "").strip()
                            if cmd:
                                skill_subcmd = cmd.split(maxsplit=1)[0]
                        await channel.send_step(
                            step_msg, "tool_start",
                            subagent=parent_subagent,
                            skill_subcmd=skill_subcmd,
                        )
                    if tool_name == "task" and isinstance(input_data, dict):
                        st = input_data.get("subagent_type")
                        if st:
                            subagent_stack.append(st)
                elif kind == "on_tool_end":
                    tool_name = event.get("name", "tool")
                    input_data = event.get("data", {}).get("input", {})
                    output = event.get("data", {}).get("output", "")
                    output_text = output if isinstance(output, str) else getattr(output, "content", "")
                    run_id = event.get("run_id", "?")
                    inflight = tool_calls_inflight.pop(run_id, {})
                    elapsed = (asyncio.get_event_loop().time() - inflight["t_start"]) if "t_start" in inflight else None
                    # 弹栈要在 send_step 之前——task 自身的 tool_end step 应该
                    # 归属于"派工方"（栈顶不含自己）的层级。
                    if tool_name == "task" and subagent_stack:
                        subagent_stack.pop()
                    parent_subagent = subagent_stack[-1] if subagent_stack else None
                    logger.info(
                        "on_tool_end tool=%s node=%s elapsed=%s subagent_ctx=%s output_chars=%d output_head=%r",
                        tool_name, inflight.get("node", "?"),
                        f"{elapsed:.2f}s" if elapsed is not None else "?",
                        parent_subagent or "-",
                        len(output_text or ""), (output_text or "")[:300],
                    )
                    if not _is_meta_tool(tool_name):
                        await channel.send_step(_format_tool_label(tool_name, input_data), "tool_end", subagent=parent_subagent)
                    # 工具结束后从 output 抽 artifact_ids（用 sentinel 解析），推 SSE。
                    # langgraph 在 on_tool_end 给的 output 通常是 ToolMessage（含 content 字段），
                    # 而 langchain 裸 tool astream 给的是 str——两种都要兼容。
                    if output_text:
                        for artifact_id in extract_artifact_ids_from_output(output_text):
                            await channel.send_artifact_updated(artifact_id, "created")
                elif kind == "on_chain_end":
                    outputs = event.get("data", {}).get("output", {})
                    if isinstance(outputs, dict) and "__interrupt__" in outputs:
                        interrupts = outputs["__interrupt__"]
                        iv = interrupts[0].value if interrupts else {}
                        await _handle_interrupt(iv, channel, agent, config, cfg.agent.interrupt_on)
        except asyncio.CancelledError:
            logger.info("agent_run CANCELLED thread=%s", thread_id)
            should_close = False  # chat handler 会在同一 channel 上接着跑新一轮
            raise
        except GraphInterrupt as e:
            logger.info("agent_run INTERRUPT thread=%s", thread_id)
            interrupts = e.args[0] if e.args else []
            iv = interrupts[0].value if interrupts else {}
            await _handle_interrupt(iv, channel, agent, config, cfg.agent.interrupt_on)
        except Exception as e:
            logger.exception("agent_run ERROR thread=%s err=%s", thread_id, e)
            await channel.send_token(f"\n[错误] {e}")
        finally:
            logger.info(
                "agent_run END thread=%s chat_model_calls=%d business_tools=%d inflight_leftover=%d",
                thread_id, chat_model_calls, business_tools_started, len(tool_calls_inflight),
            )
            if should_close:
                await channel.close()


agent_runner = AgentRunner()
