"""Backward-compat shim.

The implementation has been split into three focused modules:

- `agent/llm.py` — LLM instance construction (`_build_model`)
- `agent/checkpointer.py` — AsyncSqliteSaver lifecycle + Summarization
  factory monkey-patch (`init_checkpointer`, `close_checkpointer`,
  `get_checkpointer`)
- `agent/builder.py` — main `build_agent` + module-level `_store`

Existing imports `from agent.core import build_agent` etc. continue to
work unchanged via the re-exports below.
"""
from agent.builder import build_agent
from agent.checkpointer import (
    close_checkpointer,
    get_checkpointer,
    init_checkpointer,
)
from agent.llm import _build_model
from agent.store import close_store, get_store, init_store

__all__ = [
    "build_agent",
    "close_checkpointer",
    "get_checkpointer",
    "init_checkpointer",
    "close_store",
    "get_store",
    "init_store",
    "_build_model",
]
