import asyncio
import json

from .base import BaseChannel


class WebSSEChannel(BaseChannel):
    def __init__(self, user_id: str, session_id: str, queue: asyncio.Queue):
        super().__init__(user_id, session_id)
        self._queue = queue
        self._confirm_event = asyncio.Event()
        self._confirm_result: bool = None

    async def send_token(self, token: str) -> None:
        data = json.dumps({"token": token}, ensure_ascii=False)
        await self._queue.put(f"event: token\ndata: {data}\n\n")

    async def send_step(self, msg: str, step_type: str) -> None:
        data = json.dumps({"msg": msg, "type": step_type}, ensure_ascii=False)
        await self._queue.put(f"event: step\ndata: {data}\n\n")

    async def send_progress(self, message: str) -> None:
        data = json.dumps({"message": message}, ensure_ascii=False)
        await self._queue.put(f"event: progress\ndata: {data}\n\n")

    async def send_plan(self, tasks: list[dict]) -> None:
        data = json.dumps({"tasks": tasks}, ensure_ascii=False)
        await self._queue.put(f"event: plan\ndata: {data}\n\n")

    async def wait_for_confirm(self, message: str, preview: list) -> bool:
        # 每次进 wait 前先重置——防止上一次遗留的 set 状态让本次立即返回脏值
        self._confirm_event.clear()
        self._confirm_result = None
        data = json.dumps({"message": message, "preview": preview}, ensure_ascii=False)
        await self._queue.put(f"event: interrupt\ndata: {data}\n\n")
        await self._confirm_event.wait()
        return bool(self._confirm_result)

    async def close(self) -> None:
        await self._queue.put(f"event: done\ndata: {{}}\n\n")

    def resolve_confirm(self, approve: bool) -> None:
        # 幂等：双击 / 重复请求只认第一次，避免覆盖有效结果
        if self._confirm_event.is_set():
            return
        self._confirm_result = approve
        self._confirm_event.set()
