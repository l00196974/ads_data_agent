"""Main agent assembly. Glue between config / model / middleware / tools.

Importing this module pulls in `agent/checkpointer.py` first (top of imports
below), which in turn monkey-patches deepagents' default Summarization
factory before any `create_deep_agent` call happens.
"""
import logging

from deepagents import create_deep_agent

from agent.checkpointer import get_checkpointer
from agent.config import AppConfig, load_config
from agent.llm import _build_model
from agent.middleware import IterationGuardMiddleware, ToolOutputTruncationMiddleware
from agent.store import get_store


logger = logging.getLogger(__name__)


# deepagents 自己注入但我们看不见的默认工具名（用于 interrupt_on 校验白名单）。
# 来源：deepagents 库的 TodoListMiddleware / FilesystemMiddleware / SubAgentMiddleware 等。
# 升级 deepagents 时如果默认工具集变化，这里要同步更新。
_DEEPAGENTS_DEFAULT_TOOL_NAMES = frozenset({
    "task",         # SubAgentMiddleware 注入（仅 subagents 非空时）
    "write_file",   # FilesystemMiddleware
    "read_file",
    "edit_file",
    "ls",
    "write_todos",  # TodoListMiddleware
    "read_todos",
})


def _validate_interrupt_on(interrupt_on: list, project_tools: list) -> None:
    """启动时校验 interrupt_on 列表的工具名是否在加载的工具集里。

    interrupt_on 配置错（例如填了 SKILL.md 子命令名而不是 LangChain tool name）会
    导致敏感操作**不会**触发 HitL，**直接静默执行**——这是高风险 silent bug。
    本函数把已知工具集与配置对比，发现不匹配的名字就 warn（不 fail-fast，因为
    deepagents 默认工具集随版本可能变，过严的校验反而误伤）。

    参数:
        interrupt_on: 来自 config.yaml::agent.interrupt_on 的工具名列表
        project_tools: 项目自己 + 默认工具组成的列表（当前主要是 SKILL.md 加载出的 run_command）
    """
    if not interrupt_on:
        return

    available = set(_DEEPAGENTS_DEFAULT_TOOL_NAMES)
    for tool in project_tools:
        name = getattr(tool, "name", None)
        if name:
            available.add(name)

    unknown = [name for name in interrupt_on if name not in available]
    if unknown:
        logger.warning(
            "interrupt_on 列表中以下工具名不在已加载工具集里：%s。这些条目不会触发 HitL，"
            "敏感操作可能直接执行。已知工具集：%s。"
            "注意：interrupt_on 应该是 LangChain tool name（如 'run_command', 'task'），"
            "**不是** SKILL.md 子命令名（如 'delete-adgroup'）。"
            "如要拦截特定子命令请在 SKILL.md 脚本内自行实现确认逻辑，或拦截整个 'run_command'。",
            unknown,
            sorted(available),
        )


def build_agent(
    user_id: str,
    system_prompt: str,
    skills: list,
    interrupt_on,
    cfg: AppConfig = None,
    extra_tools: list = None,
):
    """
    interrupt_on: 接受 InterruptConfig（推荐）或 list[str]（向后兼容）。
        list 形式自动归到 medium_risk——和旧语义"敏感但默认可放行"一致。
    """
    if cfg is None:
        cfg = load_config()

    if extra_tools:
        skills = skills + extra_tools

    # 兼容 list 形式（旧 caller / 测试）→ 归到 medium_risk
    from agent.config import InterruptConfig
    if isinstance(interrupt_on, list):
        interrupt_on = InterruptConfig(high_risk=[], medium_risk=interrupt_on)
    intercepted = interrupt_on.all_intercepted()

    # 启动校验：interrupt_on 配置错会让敏感操作静默执行（A3 known issue）。
    # 在 create_deep_agent 之前 warn，让运维及早发现配置错误。
    _validate_interrupt_on(intercepted, skills)

    # _build_model 现在统一返回 BaseChatModel 实例（含 cfg.llm.api_key），
    # 无需再判断 isinstance / 调 resolve_model。
    model = _build_model(cfg)
    # deepagents HumanInTheLoopMiddleware 期望 dict[tool_name, bool|InterruptOnConfig]。
    # 这里 high/medium 对 deepagents 都是"拦截"——风险分级是 runner 层做的（决定是否
    # 强制弹窗 / 是否查白名单），deepagents 不区分。
    interrupt_tools = {name: True for name in intercepted}

    # 业务工具大返回值截断：deepagents 默认只截 write_file/edit_file 的 args，
    # 不管 ToolMessage.content。我们补一个 middleware 把超阈值的工具输出写到磁盘。
    lc = cfg.agent.long_context
    user_middleware = [
        ToolOutputTruncationMiddleware(
            max_bytes=lc.tool_output_max_bytes,
            data_dir=cfg.persistence.data_dir,
        ),
        # 步数感知：接近 recursion_limit 时往 messages 里注入 system 警告，让 LLM 自我收敛
        IterationGuardMiddleware(recursion_limit=cfg.agent.recursion_limit),
    ]

    # 子 Agent 列表（独立 context window，主→子任务委派靠 deepagents 内置 task 工具）。
    # SubAgentSpec.dict() 直接对齐 deepagents.SubAgent TypedDict 结构；空字典字段（如
    # model 为 None）要剔除，避免 "provider:None" 这种非法值传到底层。
    subagents = [
        {k: v for k, v in s.model_dump().items() if v is not None}
        for s in cfg.agent.subagents
    ]

    # 注意：deepagents 默认会注入一个 SummarizationMiddleware，
    # 我们已经在 agent/checkpointer.py 模块顶部 monkey-patch 了它的工厂函数，
    # 所以默认那个会按 config.yaml 里 agent.long_context 的参数初始化。
    agent = create_deep_agent(
        model=model,
        tools=skills,
        system_prompt=system_prompt,
        subagents=subagents if subagents else None,
        interrupt_on=interrupt_tools if interrupt_tools else None,
        checkpointer=get_checkpointer(),
        store=get_store(),
        middleware=user_middleware,
    )
    return agent
