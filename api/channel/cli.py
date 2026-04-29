from .base import BaseChannel


class CLIChannel(BaseChannel):
    async def send_token(self, token: str) -> None:
        print(token, end="", flush=True)

    async def send_step(self, msg: str, step_type: str) -> None:
        icon = "▶️  执行中" if step_type == "tool_start" else "✅ 完成"
        print(f"\n{icon}: {msg}")

    async def send_progress(self, message: str) -> None:
        print(f"[进度] {message}")

    def get_skill(self):
        async def send_to_user(
            action: str = "chart",
            chart_type: str = "bar",
            title: str = "",
            x_data: list = [],
            series: list = [],
        ) -> str:
            """渲染图表（CLI 模式打印数据摘要表）。

            action 固定 "chart"；chart_type / title / x_data / series 同 Web 端语义。
            """
            print(f"\n[图表: {title}]")
            for s in series:
                for label, val in zip(x_data, s.get("data", [])):
                    print(f"  {label}: {val}")
            return "ok"

        return [send_to_user]

    async def wait_for_confirm(self, message: str, preview: list) -> bool:
        print(f"\n[需要确认] {message}")
        return input("approve/cancel: ").strip() == "approve"
