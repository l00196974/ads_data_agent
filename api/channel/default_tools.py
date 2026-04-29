"""Agent 默认工具集（framework-provided tools）。

「默认工具」与「业务 skill」的边界：
- 业务 skill：通过 `agent/skill_loader.py` 从 `skills.md_dir` 扫描加载，
  描述"做什么业务"（query-metrics、export-report 等）。框架不内置任何业务 skill。
- 默认工具：framework 自带，描述"如何与外界沟通"——目前是 `send_plan`，
  未来可能扩展为 `send_progress` 等。Agent 视角下这些工具签名稳定，
  内部委托给 channel 的 skill 方法（`channel.send_plan` 等）实现差异化输出。

工厂模式：每次 build_agent 时调用 `make_default_tools(channel)`，
把 channel 实例闭包进 tool 实现。这样工具调用时不需要 contextvars 或
langgraph runtime 注入——直接捕获引用，简单可靠。
"""
from langchain_core.tools import StructuredTool

from .base import BaseChannel


def make_default_tools(channel: BaseChannel) -> list:
    """构造 agent 的默认工具列表，闭包绑定到给定 channel。

    返回工具会被 AgentRunner 作为 extra_tools 传入 build_agent，与业务
    skill 一同参与装配。工具名都以 `send_` 前缀开头，runner 的
    `_is_meta_tool` 会过滤这些工具不产生 step / tool_end 等业务事件。
    """

    async def send_plan(tasks: list) -> str:
        """在开始执行业务工具之前，**必须**先调用此工具声明任务计划。

        参数：
        - tasks: 任务列表，每项格式 {"id": "t1", "name": "任务描述"}
          示例: [{"id": "t1", "name": "查询点击数据"}, {"id": "t2", "name": "汇总并生成图表"}]

        规则：
        - 任务数量 2-5 个，颗粒度适中（不要把"调一次工具"拆成多步）
        - name 用简短中文描述，不超过 20 字
        - id 按 t1, t2, t3... 顺序命名
        - 计划只声明一次，不要在后续追加或重复调用此工具
        """
        await channel.send_plan(tasks)
        return "ok"

    return [
        StructuredTool.from_function(
            coroutine=send_plan,
            name="send_plan",
            description=send_plan.__doc__ or "声明任务执行计划",
        ),
    ]
