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
        # OpenAI / OpenAI-compat 在 stream 模式下默认**不返回 usage**——必须显式
        # 开启 stream_options={"include_usage": True}（langchain ChatOpenAI 暴露成
        # stream_usage 参数）。不开的话每次 LLM 调用 usage_metadata 都是 None，
        # runner 推 metrics 时 input/output tokens 全空，前端只能显示 "-"。
        # 兼容性：所有主流 OpenAI-compat 厂商（火山方舟 / DeepSeek / 通义 / OpenAI 自家）
        # 都支持这个 stream option；个别小厂商代理可能忽略，但开了不会报错。
        "stream_usage": True,
    }
    if llm.base_url:
        kwargs["base_url"] = llm.base_url
    return ChatOpenAI(**kwargs)
