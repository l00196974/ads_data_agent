from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable


@runtime_checkable
class ExternallyConfirmable(Protocol):
    """Capability protocol: channel supports HitL confirm via an externally
    delivered signal (e.g. a separate HTTP endpoint, Slack interaction
    callback, WebSocket frame from a different connection).

    The ``wait_for_confirm()`` coroutine of these channels blocks on an
    asyncio.Event / Future, and an out-of-band caller invokes
    ``resolve_confirm(approve, add_to_auto_approve)`` to release it.

    Same-stack channels (e.g. CLIChannel that blocks on stdin) do NOT need
    to satisfy this protocol — their confirm signal arrives in the same
    coroutine via direct IO.

    Use ``isinstance(channel, ExternallyConfirmable)`` in HTTP / RPC
    handlers to gate the call: same-stack channels return a clear
    "not supported" response instead of silently no-op'ing.

    `add_to_auto_approve`: 用户在前端确认弹窗里勾选了"以后不再确认 [工具名]"。
        runner 收到后会调用 agent.auto_approve.add(tool_name) 加全局白名单。
        high_risk 的工具即使 True 也会被 runner 忽略（硬约束）。
    """

    def resolve_confirm(self, approve: bool, add_to_auto_approve: bool = False) -> None: ...


class BaseChannel(ABC):
    """Channel 抽象：Agent 与外部对话渠道的通信契约。

    Channel 暴露的这些方法构成它的 **skill 集合**——描述"如何向 channel
    推送信息 / 从 channel 拿到用户响应"。LLM 直接调的工具（task / write_file /
    业务 run_command 等）通过这些 channel skill 完成实际 IO，agent 视角不感知
    channel 切换。当前没有 LLM 主动调的 channel-bound 工具——之前的 send_plan
    已删（推一次性"计划预告"价值低于多 1 轮 round trip 的代价）。
    """

    def __init__(self, user_id: str, session_id: str):
        self.user_id = user_id
        self.session_id = session_id

    @abstractmethod
    async def send_token(self, token: str) -> None:
        """LLM 流式 token 输出（由 AgentRunner 直接调用）"""

    @abstractmethod
    async def send_step(self, msg: str, step_type: str, subagent: str | None = None) -> None:
        """工具执行步骤：tool_start / tool_end（显示在顶部步骤追踪区）

        subagent: 当工具在某个子 Agent 内部执行时传该子 Agent 的 name
            （如 "issue-diagnostician"）。主 Agent 直接调用时为 None。
            前端据此做嵌套展示，让用户看清"是谁在干这个活"。
        """

    @abstractmethod
    async def send_progress(self, message: str) -> None:
        """显示在顶部进度条的状态文字（不进消息流）"""

    @abstractmethod
    async def send_artifact_updated(self, artifact_id: str, action: str) -> None:
        """通知用户产生 / 更新了一个 artifact。

        action: "created" | "updated"
        Channel 自行决定怎么呈现：WebSSE 推 SSE event；CLI 打印简短通知。
        """

    async def send_reasoning(self, content: str) -> None:
        """LLM 这次调用是「中间 reasoning」（接着 tool_calls），把已经流到前端的
        token 内容降级——主气泡里**不该**有这段（它是思考过程，不是最终回复）。

        前端约定：收到 reasoning_finalize 时把"当前流式气泡"的 content 取出，
        删掉气泡本身，把内容摘要追加到当前思考分组的 entries 里（"💭 ..."）。
        这样主气泡只保留 tool_calls 为空的最终回复 LLM 调用产出的文字。

        默认实现 = no-op（CLI / 测试 mock 可不实现）。
        """

    async def send_metrics(self, metrics: dict) -> None:
        """LLM 调用 metrics（每次 on_chat_model_end 推一条）。

        metrics 字段约定（runner 构造，见 api/channel/runner.py）：
            input_tokens / output_tokens / total_tokens / cache_read_tokens
            duration_ms / ttft_ms / tps
            context_used_pct  (input / summarization_trigger * 100)
            subagent          (None = 主 Agent)
            model             (LLM 模型名)
            call_seq          (该 turn 内第几次 LLM 调用)

        默认实现 = no-op，channel 按需 override（WebSSE 推 SSE event 给前端
        实时更新顶部状态栏；CLI 可以 print 一行）。设计成 non-abstract 方便
        旧 channel / 测试 mock 不强制实现。
        """

    @abstractmethod
    async def wait_for_confirm(
        self,
        message: str,
        preview: list,
        *,
        tool_name: str = "",
        risk_level: str = "medium",
    ) -> tuple[bool, bool]:
        """HitL：等待用户确认。

        参数：
            message: 给用户的确认提示文案
            preview: 操作预览数据（可选，前端按需展示）
            tool_name: 待确认的工具名（用于"加入白名单"功能；前端展示用）
            risk_level: "high" | "medium" —— high 类不允许"以后不再确认"

        返回：(approve, add_to_auto_approve)
            approve: 用户是否同意执行
            add_to_auto_approve: 用户是否勾选了"以后不再确认 [工具名]"。
                只对 medium_risk 有意义；high_risk 即使 True 调用方也会忽略。
        """

    async def close(self) -> None:
        pass
