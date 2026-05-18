import asyncio
import json
import logging

from agent import conversation_events, conversation_metrics

from .base import BaseChannel


logger = logging.getLogger(__name__)


class WebSSEChannel(BaseChannel):
    def __init__(
        self,
        user_id: str,
        session_id: str,
        queue: asyncio.Queue,
        conversation_id: str = "",
    ):
        super().__init__(user_id, session_id)
        self._queue = queue
        self._confirm_event = asyncio.Event()
        self._confirm_result: bool = None
        # 用户在弹窗里是否勾选"以后不再确认 [工具名]"。runner 收到 True
        # 且当前 tool 是 medium_risk 时，写全局 auto_approve 白名单。
        self._confirm_remember: bool = False
        # 历史会话回看需要的"思考过程"持久化所需上下文：
        #   conversation_id 在 channel 构造时就有（chat 入口决定）；
        #   turn_id 每次新一轮发问/追问时由 chat.py 调 set_turn_id 更新——
        #   一个 channel 可以连续承载多轮（追问场景），但每轮事件要按 turn_id 分组
        #   才能让前端把"思考分组"插到正确的 user/ai 消息对之间。
        self._conversation_id = conversation_id
        self._turn_id = ""

    def set_turn_id(self, turn_id: str) -> None:
        """每次新一轮（chat / append）调用前设置，让事件持久化能区分回合。"""
        self._turn_id = turn_id

    def _persist_event(self, kind: str, payload: dict) -> None:
        """把事件落一份到 conversation_events 表。失败不影响 SSE 推送
        （持久化是"事后回看"功能，不能拖累实时通信）。"""
        if not (self._conversation_id and self._turn_id):
            return
        try:
            conversation_events.append(
                self.user_id, self._conversation_id, self._turn_id, kind, payload,
            )
        except Exception as e:
            logger.warning("persist event failed kind=%s: %s", kind, e)

    async def send_token(self, token: str) -> None:
        # token 不持久化——量大、且 langgraph checkpointer 已把累积后的
        # AIMessage.content 存了，回看时直接从 messages 拿即可
        data = json.dumps({"token": token}, ensure_ascii=False)
        await self._queue.put(f"event: token\ndata: {data}\n\n")

    async def send_step(
        self,
        msg: str,
        step_type: str,
        subagent: str | None = None,
        skill_subcmd: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        """skill_subcmd: 仅当工具是 run_command 时由 runner 解析出来传入——
        前端用它在顶部 metrics 栏聚合"本轮调用的 skill 链"，省得自己再 parse msg。

        tool_call_id: 让前端在 tool_arriving → tool_start → tool_end 三态间按 id
        定位同一条目。tool_arriving 不持久化（事后回看意义不大，回看时只看到
        最终的 tool_start/tool_end 即可），其它两类仍写 conversation_events。"""
        payload: dict = {"msg": msg, "type": step_type}
        if subagent:
            payload["subagent"] = subagent
        if skill_subcmd:
            payload["skill_subcmd"] = skill_subcmd
        if tool_call_id:
            payload["tool_call_id"] = tool_call_id
        if step_type != "tool_arriving":
            self._persist_event("step", payload)
        data = json.dumps(payload, ensure_ascii=False)
        await self._queue.put(f"event: step\ndata: {data}\n\n")

    async def send_progress(self, message: str) -> None:
        # progress 不持久化——是"AI 正在规划任务..."这种过渡文字，事后无价值
        data = json.dumps({"message": message}, ensure_ascii=False)
        await self._queue.put(f"event: progress\ndata: {data}\n\n")

    async def send_artifact_updated(self, artifact_id: str, action: str) -> None:
        payload = {"artifact_id": artifact_id, "action": action}
        self._persist_event("artifact_updated", payload)
        data = json.dumps(payload, ensure_ascii=False)
        await self._queue.put(f"event: artifact_updated\ndata: {data}\n\n")

    async def send_reasoning(self, content: str) -> None:
        """推 reasoning_finalize SSE event。前端把当前流式气泡降级为思考分组项。"""
        data = json.dumps({"content": content or ""}, ensure_ascii=False)
        await self._queue.put(f"event: reasoning_finalize\ndata: {data}\n\n")

    async def send_metrics(self, metrics: dict) -> None:
        """每次 LLM 调用结束推一条 metrics 到前端 + 落库。

        前端用它实时刷新顶部状态栏的 token / TPS / 上下文进度条。
        持久化用于会话 sidebar 累计统计 + V2 时序图。
        """
        # 持久化（同步写 sqlite ~1ms，WAL 下并发友好）
        if self._conversation_id and self._turn_id:
            try:
                conversation_metrics.append(
                    self.user_id, self._conversation_id, self._turn_id,
                    call_seq=metrics.get("call_seq", 0),
                    subagent=metrics.get("subagent"),
                    model=metrics.get("model"),
                    input_tokens=metrics.get("input_tokens"),
                    output_tokens=metrics.get("output_tokens"),
                    total_tokens=metrics.get("total_tokens"),
                    cache_read_tokens=metrics.get("cache_read_tokens"),
                    duration_ms=metrics.get("duration_ms"),
                    ttft_ms=metrics.get("ttft_ms"),
                    tps=metrics.get("tps"),
                )
            except Exception as e:
                logger.warning("persist metrics failed: %s", e)
        data = json.dumps(metrics, ensure_ascii=False)
        await self._queue.put(f"event: metrics\ndata: {data}\n\n")

    async def wait_for_confirm(
        self,
        message: str,
        preview: list,
        *,
        tool_name: str = "",
        risk_level: str = "medium",
    ) -> tuple[bool, bool]:
        # 每次进 wait 前先重置——防止上一次遗留的 set 状态让本次立即返回脏值
        self._confirm_event.clear()
        self._confirm_result = None
        self._confirm_remember = False
        # SSE event payload 带 tool_name + risk_level，前端据此渲染弹窗：
        #   - high_risk 类把"以后不再确认"checkbox 灰掉 + 提示
        #   - medium_risk 类正常显示 checkbox（用户勾选 → confirm endpoint 带 add_to_auto_approve=true）
        data = json.dumps({
            "message": message,
            "preview": preview,
            "tool_name": tool_name,
            "risk_level": risk_level,
        }, ensure_ascii=False)
        await self._queue.put(f"event: interrupt\ndata: {data}\n\n")
        await self._confirm_event.wait()
        return bool(self._confirm_result), bool(self._confirm_remember)

    async def close(self) -> None:
        await self._queue.put(f"event: done\ndata: {{}}\n\n")

    def resolve_confirm(self, approve: bool, add_to_auto_approve: bool = False) -> None:
        # 幂等：双击 / 重复请求只认第一次，避免覆盖有效结果
        if self._confirm_event.is_set():
            return
        self._confirm_result = approve
        self._confirm_remember = add_to_auto_approve
        self._confirm_event.set()
