from .base import BaseChannel


class CLIChannel(BaseChannel):
    async def send_token(self, token: str) -> None:
        print(token, end="", flush=True)

    async def send_step(self, msg: str, step_type: str, subagent: str | None = None) -> None:
        icon = "▶️  执行中" if step_type == "tool_start" else "✅ 完成"
        # CLI 用缩进 + 子 Agent 名前缀展示嵌套
        if subagent:
            print(f"\n  ↳ [{subagent}] {icon}: {msg}")
        else:
            print(f"\n{icon}: {msg}")

    async def send_progress(self, message: str) -> None:
        print(f"[进度] {message}")

    async def send_artifact_updated(self, artifact_id: str, action: str) -> None:
        emoji = "📦" if action == "created" else "🔄"
        print(f"\n{emoji} Artifact {action}: {artifact_id}")

    async def wait_for_confirm(
        self,
        message: str,
        preview: list,
        *,
        tool_name: str = "",
        risk_level: str = "medium",
    ) -> tuple[bool, bool]:
        risk_tag = "[⚠️ 高危]" if risk_level == "high" else "[需要确认]"
        print(f"\n{risk_tag} {message}")
        if tool_name:
            print(f"  工具：{tool_name}")
        ans = input("approve / approve_remember (medium 类才生效) / cancel: ").strip()
        approve = ans in ("approve", "approve_remember")
        remember = ans == "approve_remember" and risk_level == "medium"
        return approve, remember
