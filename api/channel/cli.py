from .base import BaseChannel


class CLIChannel(BaseChannel):
    async def send_token(self, token: str) -> None:
        print(token, end="", flush=True)

    async def send_step(
        self,
        msg: str,
        step_type: str,
        subagent: str | None = None,
        skill_subcmd: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        # CLI 直接忽略 tool_arriving——同栈连续输出场景下"准备中 → 执行中"重复
        # 抖动反而吵；CLI 用户已经直接看见整段流，不需要占位。
        if step_type == "tool_arriving":
            return
        icon = "▶️  执行中" if step_type == "tool_start" else "✅ 完成"
        # CLI 用缩进 + 子 Agent 名前缀展示嵌套；skill_subcmd 在 CLI 不单独显示
        # （msg 本身已含命令串），仅 web channel 需要这字段做 metrics 聚合
        if subagent:
            print(f"\n  ↳ [{subagent}] {icon}: {msg}")
        else:
            print(f"\n{icon}: {msg}")

    async def send_progress(self, message: str) -> None:
        print(f"[进度] {message}")

    async def send_artifact_updated(self, artifact_id: str, action: str) -> None:
        emoji = "📦" if action == "created" else "🔄"
        print(f"\n{emoji} Artifact {action}: {artifact_id}")

    async def send_reasoning(self, content: str) -> None:
        # CLI 用前缀提示这段是中间 reasoning，不是最终回复
        snippet = (content or '').strip().replace('\n', ' ')
        if snippet:
            print(f"\n  💭 [reasoning] {snippet[:120]}{'...' if len(snippet) > 120 else ''}")

    async def send_metrics(self, metrics: dict) -> None:
        # CLI 简洁一行，避免淹没正常输出
        in_t = metrics.get("input_tokens", "-")
        out_t = metrics.get("output_tokens", "-")
        tps = metrics.get("tps")
        tps_s = f"{tps:.1f} t/s" if isinstance(tps, (int, float)) else "-"
        sub = metrics.get("subagent")
        prefix = f"[{sub}] " if sub else ""
        print(f"\n  📊 {prefix}in={in_t} out={out_t} {tps_s}")

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
