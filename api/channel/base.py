from abc import ABC, abstractmethod
from typing import Callable


class BaseChannel(ABC):
    def __init__(self, user_id: str, session_id: str):
        self.user_id = user_id
        self.session_id = session_id

    @abstractmethod
    def get_skill(self) -> Callable:
        """返回注入 Agent 的 send_to_user Skill（闭包捕获 self）"""

    @abstractmethod
    async def send_token(self, token: str) -> None:
        """LLM 流式 token 输出（由 AgentRunner 直接调用）"""

    @abstractmethod
    async def send_step(self, msg: str, step_type: str) -> None:
        """工具执行步骤：tool_start / tool_end（显示在顶部步骤追踪区）"""

    @abstractmethod
    async def send_plan(self, tasks: list) -> None:
        """发送任务计划列表 [{"id": "t1", "name": "..."}]"""

    @abstractmethod
    async def send_task_update(self, task_id: str, status: str) -> None:
        """更新单个任务状态：pending / running / done"""

    @abstractmethod
    async def send_progress(self, message: str) -> None:
        """显示在顶部进度条的状态文字（不进消息流）"""

    @abstractmethod
    async def wait_for_confirm(self, message: str, preview: list) -> bool:
        """HitL：等待用户确认，返回 True=approve"""

    async def close(self) -> None:
        pass
