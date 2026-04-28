"""LLM instance construction. Pure config → ChatOpenAI / model-string mapping."""
from agent.config import AppConfig


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
