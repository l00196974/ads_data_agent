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
        # 同 WebSSEChannel：图表内联 markdown，无需展示型工具。
        # CLI 拿到 ```chart``` 块时会以原始 JSON 文本打印，方便调试。
        return []

    async def wait_for_confirm(self, message: str, preview: list) -> bool:
        print(f"\n[需要确认] {message}")
        return input("approve/cancel: ").strip() == "approve"
