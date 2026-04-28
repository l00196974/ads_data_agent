"""Main agent assembly. Glue between config / model / middleware / tools.

Importing this module pulls in `agent/checkpointer.py` first (top of imports
below), which in turn monkey-patches deepagents' default Summarization
factory before any `create_deep_agent` call happens.
"""
from deepagents import create_deep_agent
from deepagents._models import resolve_model
from langgraph.store.memory import InMemoryStore

from agent.checkpointer import get_checkpointer
from agent.config import AppConfig, load_config
from agent.llm import _build_model
from agent.middleware import ToolOutputTruncationMiddleware


# Module-level singleton (shared by all users; thread_id isolates per-user data).
_store = InMemoryStore()


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

    raw_model = _build_model(cfg)
    # SummarizationMiddleware 需要 BaseChatModel 实例，而非 "provider:model" 字符串
    model = resolve_model(raw_model) if isinstance(raw_model, str) else raw_model
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

    # 注意：deepagents 默认会注入一个 SummarizationMiddleware，
    # 我们已经在 agent/checkpointer.py 模块顶部 monkey-patch 了它的工厂函数，
    # 所以默认那个会按 config.yaml 里 agent.long_context 的参数初始化。
    agent = create_deep_agent(
        model=model,
        tools=skills,
        system_prompt=system_prompt,
        interrupt_on=interrupt_tools if interrupt_tools else None,
        checkpointer=get_checkpointer(),
        store=_store,
        middleware=user_middleware,
    )
    return agent
