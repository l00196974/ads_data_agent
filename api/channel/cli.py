from typing import Literal

from .base import BaseChannel


class CLIChannel(BaseChannel):
    async def send_token(self, token: str) -> None:
        print(token, end="", flush=True)

    async def send_step(self, msg: str, step_type: str) -> None:
        icon = "▶️  执行中" if step_type == "tool_start" else "✅ 完成"
        print(f"\n{icon}: {msg}")

    async def send_plan(self, tasks: list) -> None:
        print("\n📋 执行计划:")
        for t in tasks:
            print(f"  ⬜ {t.get('id', '?')}: {t.get('name', '')}")

    async def send_progress(self, message: str) -> None:
        print(f"[进度] {message}")

    def get_skill(self):
        channel = self

        async def send_plan(tasks: list) -> str:
            """在开始执行任何工具之前，必须首先调用此工具规划任务步骤。任务状态由系统自动推断，无需手动上报。"""
            await channel.send_plan(tasks)
            return "ok"

        async def send_to_user(
            action: Literal["chart", "progress"],
            content: str = "",
            title: str = "",
            x_data: list = [],
            series: list = [],
            **kwargs,
        ) -> str:
            """
            向命令行终端发送结构化产物。

            action 参数：
            - "chart"：终端不支持图形渲染，改为打印数据摘要表格
            - "progress"：打印 [进度] 前缀的状态行
            """
            if action == "chart":
                print(f"\n[图表: {title}]")
                for s in series:
                    for label, val in zip(x_data, s.get("data", [])):
                        print(f"  {label}: {val}")
            elif action == "progress":
                print(f"[进度] {content}")
            return "ok"

        return [send_plan, send_to_user]

    async def wait_for_confirm(self, message: str, preview: list) -> bool:
        print(f"\n[需要确认] {message}")
        return input("approve/cancel: ").strip() == "approve"
