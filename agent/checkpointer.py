"""AsyncSqliteSaver lifecycle + deepagents Summarization factory patch.

Importing this module immediately monkey-patches deepagents' default
Summarization factory so that downstream `create_deep_agent` calls pick up
the configurable thresholds from `config.yaml::agent.long_context`. Any
module that ends up creating an agent must therefore import this module
first; `agent/builder.py` does so at the top of its imports.
"""
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Optional

import deepagents.graph as _deepagents_graph
from deepagents.middleware.summarization import SummarizationMiddleware
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agent.config import load_config


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


# AsyncSqliteSaver 持久化 checkpointer：进程重启不丢历史；
# 必须在事件循环里 init（langgraph 1.x async 链路要求 aget_tuple 等异步 API）。
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
