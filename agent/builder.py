"""Main agent assembly. Glue between config / model / middleware / tools.

Importing this module pulls in `agent/checkpointer.py` first (top of imports
below), which in turn monkey-patches deepagents' default Summarization
factory before any `create_deep_agent` call happens.
"""
from deepagents import create_deep_agent

from agent.checkpointer import get_checkpointer
from agent.config import AppConfig, load_config
from agent.llm import _build_model
from agent.middleware import ToolOutputTruncationMiddleware
from agent.store import get_store


def build_agent(
    user_id: str,
    system_prompt: str,
    skills: list,
    interrupt_on: list,
    cfg: AppConfig = None,
    extra_tools: list = None,
):
    if cfg is None:
        cfg = load_config()

    if extra_tools:
        skills = skills + extra_tools

    # _build_model 现在统一返回 BaseChatModel 实例（含 cfg.llm.api_key），
    # 无需再判断 isinstance / 调 resolve_model。
    model = _build_model(cfg)
    interrupt_tools = {tool_name: True for tool_name in interrupt_on}

    # 业务工具大返回值截断：deepagents 默认只截 write_file/edit_file 的 args，
    # 不管 ToolMessage.content。我们补一个 middleware 把超阈值的工具输出写到磁盘。
    lc = cfg.agent.long_context
    user_middleware = [
        ToolOutputTruncationMiddleware(
            max_bytes=lc.tool_output_max_bytes,
            data_dir=cfg.persistence.data_dir,
        ),
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
