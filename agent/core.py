from deepagents import create_deep_agent
from langgraph.checkpoint.memory import MemorySaver
from langgraph.store.memory import InMemoryStore
from agent.config import AppConfig, load_config

# 模块级单例（同进程内所有用户共用，thread_id 隔离数据）
_store = InMemoryStore()
_checkpointer = MemorySaver()


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
):
    if cfg is None:
        cfg = load_config()

    model = _build_model(cfg)
    interrupt_tools = {tool_name: True for tool_name in interrupt_on}

    agent = create_deep_agent(
        model=model,
        tools=skills,
        system_prompt=system_prompt,
        interrupt_on=interrupt_tools if interrupt_tools else None,
        checkpointer=_checkpointer,
        store=_store,
    )
    return agent
