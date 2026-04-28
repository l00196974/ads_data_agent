from contextlib import AsyncExitStack
from pathlib import Path
from typing import Optional

import deepagents.graph as _deepagents_graph
from deepagents import create_deep_agent
from deepagents._models import resolve_model
from deepagents.middleware.summarization import SummarizationMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.memory import InMemoryStore

from agent.config import AppConfig, load_config
from agent.tool_output_truncation import ToolOutputTruncationMiddleware


def _custom_summarization_factory(model, backend):
    """覆盖 deepagents 默认的 SummarizationMiddleware 工厂。

    deepagents 默认阈值是 ("tokens", 170000) 或 ("fraction", 0.85) of model profile。
    对 32k–128k 上下文的模型（如 GLM-5.1, ark-code-latest）这个阈值过高，
    实际触发时 prefill 已经很慢且接近 context 上限。

    我们改成读 config.yaml 的 agent.long_context 段，让用户自行调整。
    """
    lc = load_config().agent.long_context
    return SummarizationMiddleware(
        model=model,
        backend=backend,
        trigger=("tokens", lc.summarization_trigger_tokens),
        keep=("messages", lc.summarization_keep_messages),
    )


# Monkey-patch 必须在任何 build_agent 之前生效。模块级 import 即执行。
_deepagents_graph.create_summarization_middleware = _custom_summarization_factory


# 模块级单例（同进程内所有用户共用，thread_id 隔离数据）。
# AsyncSqliteSaver 持久化 checkpointer：进程重启不丢历史；
# 必须在事件循环里 init（langgraph 1.x async 链路要求 aget_tuple 等异步 API）。
_store = InMemoryStore()
_db_path = Path(load_config().persistence.data_dir) / "checkpoints.db"
_db_path.parent.mkdir(parents=True, exist_ok=True)

_exit_stack: Optional[AsyncExitStack] = None
_checkpointer: Optional[AsyncSqliteSaver] = None


async def init_checkpointer() -> AsyncSqliteSaver:
    """Idempotent async init. Call once on FastAPI lifespan startup, or at
    the top of any async script that uses build_agent."""
    global _checkpointer, _exit_stack
    if _checkpointer is not None:
        return _checkpointer
    _exit_stack = AsyncExitStack()
    _checkpointer = await _exit_stack.enter_async_context(
        AsyncSqliteSaver.from_conn_string(str(_db_path))
    )
    await _checkpointer.setup()
    return _checkpointer


async def close_checkpointer() -> None:
    global _checkpointer, _exit_stack
    if _exit_stack is not None:
        await _exit_stack.aclose()
    _exit_stack = None
    _checkpointer = None


def get_checkpointer() -> AsyncSqliteSaver:
    if _checkpointer is None:
        raise RuntimeError(
            "Checkpointer not initialized. Call `await init_checkpointer()` first "
            "(handled automatically by main.py's lifespan in production)."
        )
    return _checkpointer


def _build_model(cfg: AppConfig):
    """根据配置构建 LLM 实例。
    - 若配置了 base_url 或 provider=openai，使用 ChatOpenAI（兼容 OpenAI 协议的厂商）
    - 否则使用 deepAgent 原生字符串格式 "provider:model"
    """
    llm = cfg.llm
    if llm.base_url or llm.provider == "openai":
        from langchain_openai import ChatOpenAI

        kwargs = {"model": llm.model, "api_key": llm.api_key or "sk-placeholder"}
        if llm.base_url:
            kwargs["base_url"] = llm.base_url
        return ChatOpenAI(**kwargs)
    return f"{llm.provider}:{llm.model}"


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
    # 我们已经在模块顶部 monkey-patch 了它的工厂函数（_custom_summarization_factory），
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
