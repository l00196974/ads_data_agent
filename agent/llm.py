"""LLM instance construction. Pure config → ChatOpenAI mapping.

只走 OpenAI 兼容协议（ChatOpenAI）。`base_url` 切换不同后端：
  - 不设 base_url → OpenAI 自家
  - https://ark.cn-beijing.volces.com/...   → 火山方舟
  - https://api.deepseek.com/v1             → DeepSeek
  - https://dashscope.aliyuncs.com/...      → 阿里通义
  - 任何 OpenAI-compatible proxy

历史上曾支持 anthropic 直连 + google langchain 原生路径，已删——业务上只用 OpenAI
兼容代理，多协议是过度设计。如未来真要原生 Anthropic / Google Gemini，再加分支。
"""
from langchain_openai import ChatOpenAI

from agent.config import AppConfig


def _build_model(cfg: AppConfig) -> ChatOpenAI:
    llm = cfg.llm
    kwargs = {
        "model": llm.model,
        "api_key": llm.api_key or "sk-placeholder",
    }
    if llm.base_url:
        kwargs["base_url"] = llm.base_url
    return ChatOpenAI(**kwargs)
