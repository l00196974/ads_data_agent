from deepagents import create_deep_agent
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
from agent.config import AppConfig, load_config

# 模块级单例（同进程内所有用户共用，thread_id 隔离数据）
_store = InMemoryStore()
_checkpointer = MemorySaver()


def build_agent(
    user_id: str,
    system_prompt: str,
    skills: list,
    interrupt_on: list,
    cfg: AppConfig = None,
):
    if cfg is None:
        cfg = load_config()

    model_str = f"{cfg.llm.provider}:{cfg.llm.model}"
    interrupt_tools = {tool_name: True for tool_name in interrupt_on}

    agent = create_deep_agent(
        model=model_str,
        tools=skills,
        system_prompt=system_prompt,
        interrupt_on=interrupt_tools if interrupt_tools else None,
        checkpointer=_checkpointer,
        store=_store,
    )
    return agent
