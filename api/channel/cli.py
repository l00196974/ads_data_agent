from .base import BaseChannel


class CLIChannel(BaseChannel):
    async def send_token(self, token: str) -> None:
        print(token, end="", flush=True)

    async def send_step(self, msg: str, step_type: str) -> None:
        icon = "▶️  执行中" if step_type == "tool_start" else "✅ 完成"
        print(f"\n{icon}: {msg}")

    async def send_progress(self, message: str) -> None:
        print(f"[进度] {message}")

    async def send_plan(self, tasks: list[dict]) -> None:
        print("\n📋 执行计划:")
        for t in tasks:
            print(f"  ⬜ {t.get('id', '?')}: {t.get('name', '')}")

    async def send_artifact_updated(self, artifact_id: str, action: str) -> None:
        emoji = "📦" if action == "created" else "🔄"
        print(f"\n{emoji} Artifact {action}: {artifact_id}")

    async def wait_for_confirm(self, message: str, preview: list) -> bool:
        print(f"\n[需要确认] {message}")
        return input("approve/cancel: ").strip() == "approve"
