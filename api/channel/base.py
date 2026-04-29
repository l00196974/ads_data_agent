from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable


@runtime_checkable
class ExternallyConfirmable(Protocol):
    """Capability protocol: channel supports HitL confirm via an externally
    delivered signal (e.g. a separate HTTP endpoint, Slack interaction
    callback, WebSocket frame from a different connection).

    The ``wait_for_confirm()`` coroutine of these channels blocks on an
    asyncio.Event / Future, and an out-of-band caller invokes
    ``resolve_confirm(approve)`` to release it.

    Same-stack channels (e.g. CLIChannel that blocks on stdin) do NOT need
    to satisfy this protocol — their confirm signal arrives in the same
    coroutine via direct IO.

    Use ``isinstance(channel, ExternallyConfirmable)`` in HTTP / RPC
    handlers to gate the call: same-stack channels return a clear
    "not supported" response instead of silently no-op'ing.
    """

    def resolve_confirm(self, approve: bool) -> None: ...


class BaseChannel(ABC):
    """Channel 抽象：Agent 与外部对话渠道的通信契约。

    Channel 暴露的这些方法构成它的 **skill 集合**——描述"如何向 channel
    推送信息 / 从 channel 拿到用户响应"。Agent 默认工具（见
    `default_tools.py`）内部调用这些 channel skill 完成实际 IO，agent 视角
    本身不感知 channel 切换。
    """

    def __init__(self, user_id: str, session_id: str):
        self.user_id = user_id
        self.session_id = session_id

    @abstractmethod
    async def send_token(self, token: str) -> None:
        """LLM 流式 token 输出（由 AgentRunner 直接调用）"""

    @abstractmethod
    async def send_step(self, msg: str, step_type: str) -> None:
        """工具执行步骤：tool_start / tool_end（显示在顶部步骤追踪区）"""

    @abstractmethod
    async def send_progress(self, message: str) -> None:
        """显示在顶部进度条的状态文字（不进消息流）"""

    @abstractmethod
    async def send_plan(self, tasks: list[dict]) -> None:
        """推送结构化执行计划。tasks: [{"id": "t1", "name": "..."}, ...]

        被 default_tools.send_plan 工具调用——agent 在动业务工具前先声明
        计划，前端据此渲染执行面板（pending → running → done）。
        """

    @abstractmethod
    async def wait_for_confirm(self, message: str, preview: list) -> bool:
        """HitL：等待用户确认，返回 True=approve"""

    async def close(self) -> None:
        pass
