"""Probe whether the configured LLM provider returns cached_tokens
in usage_metadata.

OpenAI's official API auto-caches prompts > 1024 tokens and reports the
cached portion in `usage.prompt_tokens_details.cached_tokens`. LangChain
exposes this via `AIMessage.usage_metadata["input_token_details"]["cache_read"]`.

OpenAI-compatible proxies (volcengine ark, deepseek, alibaba qwen, etc.)
may or may not implement this — we make two consecutive calls with the
same long prefix and check the second response's cached count.
"""
import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

# Long prefix to make caching worthwhile (OpenAI's threshold is 1024 tokens;
# we want to be well above that for any provider).
LONG_PREFIX = """\
你是一个专业的中文广告数据分析助手。请严格遵守以下规则：

1. 始终使用中文回答。
2. 数字必须用千分位逗号分隔（例如 12,400）。
3. 引用工具返回的数据时，必须保留原始精度。
4. 输出结构化结果时，使用 Markdown 表格。
5. 禁止编造数据，如果工具未返回某项指标，明确说明"未提供"。
""" * 20  # ~ 6000+ chars to push token count comfortably over 1024


async def main():
    llm = ChatOpenAI(
        model=os.getenv("LLM_MODEL"),
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_BASE_URL"),
    )

    print(f"model={os.getenv('LLM_MODEL')}, base_url={os.getenv('LLM_BASE_URL')}\n")

    messages = [
        SystemMessage(content=LONG_PREFIX),
        HumanMessage(content="请说一个简短的笑话。"),
    ]

    for i in (1, 2):
        resp = await llm.ainvoke(messages)
        usage = resp.usage_metadata or {}
        details = usage.get("input_token_details", {}) or {}
        print(f"Call {i}:")
        print(f"  input_tokens   = {usage.get('input_tokens')}")
        print(f"  output_tokens  = {usage.get('output_tokens')}")
        print(f"  cache_read     = {details.get('cache_read', 'NOT REPORTED')}")
        print(f"  cache_creation = {details.get('cache_creation', 'NOT REPORTED')}")
        print(f"  raw input_token_details = {details}")
        print()
        # Slight pause to let any async cache materialize
        await asyncio.sleep(1)

    print("Interpretation:")
    print("  - If 'cache_read' on call #2 > 0:  provider supports auto prompt caching ✓")
    print("  - If 'NOT REPORTED' or 0:          provider doesn't expose / doesn't cache")


if __name__ == "__main__":
    asyncio.run(main())
