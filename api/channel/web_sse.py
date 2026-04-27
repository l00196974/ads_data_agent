import asyncio
import json
from typing import Literal

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

    async def send_plan(self, tasks: list) -> None:
        data = json.dumps({"tasks": tasks}, ensure_ascii=False)
        await self._queue.put(f"event: plan\ndata: {data}\n\n")

    async def send_task_update(self, task_id: str, status: str) -> None:
        data = json.dumps({"task_id": task_id, "status": status}, ensure_ascii=False)
        await self._queue.put(f"event: task_update\ndata: {data}\n\n")

    async def send_progress(self, message: str) -> None:
        data = json.dumps({"message": message}, ensure_ascii=False)
        await self._queue.put(f"event: progress\ndata: {data}\n\n")

    def get_skill(self):
        channel = self

        async def send_plan(tasks: list) -> str:
            """
            在开始执行任何工具之前，必须首先调用此工具规划任务步骤。

            tasks: 任务列表，每项格式为 {"id": "t1", "name": "任务描述"}
            示例: [{"id": "t1", "name": "查询广告数据"}, {"id": "t2", "name": "生成图表"}]

            规则：
            - 任务数量 2-5 个，与你后续将调用的业务工具一一对应
            - name 用简短中文描述，不超过 20 字
            - id 按 t1, t2, t3... 顺序命名
            - **任务的 running/done 状态由系统根据工具执行自动推断，你不需要也无法手动上报**
            """
            await channel.send_plan(tasks)
            return "ok"

        async def send_to_user(
            action: Literal["text", "chart", "progress"],
            content: str = "",
            chart_type: str = "bar",
            title: str = "",
            x_data: list = [],
            series: list = [],
        ) -> str:
            """
            向 Web 聊天界面发送输出。分析完成后必须主动调用此工具展示结果。

            action 参数：
            - "text"：发送 Markdown 文本（支持表格、代码块、加粗）
            - "chart"：渲染交互图表，需提供 chart_type/title/x_data/series
              chart_type: bar（柱状图）| line（折线图）| pie（饼图）| scatter（散点图）
              x_data: X 轴标签列表，如 ["渠道A", "渠道B"]
              series: 数据系列列表，每项 {"name": "RPM", "data": [10.2, 8.5]}
            - "progress"：发送进度状态，显示为顶部状态栏，不占消息流
              如 "正在下钻定向包维度..."

            Web Channel 独有能力：
            - 支持 ECharts 交互图表（可缩放、hover tooltip）
            - 支持 Markdown 富文本渲染
            - 支持多图表连续展示
            """
            if action == "chart":
                data = json.dumps(
                    {"component": chart_type, "title": title, "x_data": x_data, "series": series},
                    ensure_ascii=False,
                )
                await channel._queue.put(f"event: render\ndata: {data}\n\n")
            elif action == "progress":
                data = json.dumps({"message": content}, ensure_ascii=False)
                await channel._queue.put(f"event: progress\ndata: {data}\n\n")
            elif action == "text":
                data = json.dumps({"content": content}, ensure_ascii=False)
                await channel._queue.put(f"event: text\ndata: {data}\n\n")
            return "ok"

        return [send_plan, send_to_user]

    async def wait_for_confirm(self, message: str, preview: list) -> bool:
        data = json.dumps({"message": message, "preview": preview}, ensure_ascii=False)
        await self._queue.put(f"event: interrupt\ndata: {data}\n\n")
        await self._confirm_event.wait()
        self._confirm_event.clear()
        return self._confirm_result

    async def close(self) -> None:
        await self._queue.put(f"event: done\ndata: {{}}\n\n")

    def resolve_confirm(self, approve: bool) -> None:
        self._confirm_result = approve
        self._confirm_event.set()
