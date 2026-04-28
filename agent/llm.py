"""LLM instance construction. Pure config → BaseChatModel mapping.

Returns a `BaseChatModel` instance for **all** providers — `cfg.llm.api_key`
is honored uniformly (no longer falls back to provider-specific environment
variables silently).
"""
from langchain_core.language_models import BaseChatModel

from agent.config import AppConfig


def _build_model(cfg: AppConfig) -> BaseChatModel:
    """根据配置构建 LLM BaseChatModel 实例。

    - `base_url` 非空 或 `provider == "openai"` → `ChatOpenAI`（兼容 OpenAI
      协议代理：火山方舟 / DeepSeek / 阿里通义 / OpenAI 自家）
    - 其他 provider（anthropic / google_genai / ...）→ langchain
      `init_chat_model`，根据 `provider` 自动选对应 ChatX 实现

    `cfg.llm.api_key` 在所有路径下都会被显式传给底层客户端，不再依赖
    provider 特定的环境变量（如 `ANTHROPIC_API_KEY`）静默回退。
    """
    llm = cfg.llm

    if llm.base_url or llm.provider == "openai":
        from langchain_openai import ChatOpenAI

        kwargs = {
            "model": llm.model,
            "api_key": llm.api_key or "sk-placeholder",
        }
        if llm.base_url:
            kwargs["base_url"] = llm.base_url
        return ChatOpenAI(**kwargs)

    from langchain.chat_models import init_chat_model

    return init_chat_model(
        model=llm.model,
        model_provider=llm.provider,
        api_key=llm.api_key or None,
    )
