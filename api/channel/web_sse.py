import asyncio
import json

from .base import BaseChannel


class WebSSEChannel(BaseChannel):
    def __init__(self, user_id: str, session_id: str, queue: asyncio.Queue):
        super().__init__(user_id, session_id)
        self._queue = queue
        self._confirm_event = asyncio.Event()
        self._confirm_result: bool = None
        # 用户在弹窗里是否勾选"以后不再确认 [工具名]"。runner 收到 True
        # 且当前 tool 是 medium_risk 时，写全局 auto_approve 白名单。
        self._confirm_remember: bool = False

    async def send_token(self, token: str) -> None:
        data = json.dumps({"token": token}, ensure_ascii=False)
        await self._queue.put(f"event: token\ndata: {data}\n\n")

    async def send_step(self, msg: str, step_type: str, subagent: str | None = None) -> None:
        payload: dict = {"msg": msg, "type": step_type}
        if subagent:
            payload["subagent"] = subagent
        data = json.dumps(payload, ensure_ascii=False)
        await self._queue.put(f"event: step\ndata: {data}\n\n")

    async def send_progress(self, message: str) -> None:
        data = json.dumps({"message": message}, ensure_ascii=False)
        await self._queue.put(f"event: progress\ndata: {data}\n\n")

    async def send_artifact_updated(self, artifact_id: str, action: str) -> None:
        data = json.dumps({"artifact_id": artifact_id, "action": action}, ensure_ascii=False)
        await self._queue.put(f"event: artifact_updated\ndata: {data}\n\n")

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
