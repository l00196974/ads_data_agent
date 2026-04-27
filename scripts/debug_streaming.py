"""Diagnostic: pinpoint where the streaming bottleneck lives.

We bypass the SSE channel entirely and listen directly to
`agent.astream_events()` — printing the wall-clock time each event arrives.
If event timestamps are bunched at the end, the bottleneck is in
deepagents/langgraph (or the LLM provider). If they arrive at uniform
intervals here but the SSE client sees them bunched, the bottleneck is in
the FastAPI/ASGI layer.
"""
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from langchain_core.messages import HumanMessage

from agent.config import load_config
from agent.core import build_agent
from agent.session import SessionManager
from agent.user_space import UserSpace
from skills.system import SYSTEM_SKILLS

PROMPT = (
    "请用 query_campaign_report 工具查询 2026-04-25 user_id='dbg' 的数据，"
    "然后用 build_chart 生成柱状图（chart_type='bar', title='测试'），"
    "最后说一句话总结即可，不需要调用 send_to_user。"
)


async def main():
    cfg = load_config()
    us = UserSpace("dbg", cfg.persistence.data_dir)
    session_mgr = SessionManager()
    agent = build_agent(
        user_id="dbg",
        system_prompt="请按用户指示调用工具。",  # bare-bones, no PLAN_INSTRUCTION
        skills=SYSTEM_SKILLS,
        interrupt_on=[],
        cfg=cfg,
    )

    config = session_mgr.get_config("dbg_" + str(int(time.time())))
    t0 = time.monotonic()
    last_t = t0
    counts = {}

    print(f"[t=0.000s] starting astream_events ...")
    async for event in agent.astream_events(
        {"messages": [HumanMessage(content=PROMPT)]},
        config=config,
        version="v2",
    ):
        kind = event.get("event", "?")
        name = event.get("name", "")
        now = time.monotonic()
        delta = now - last_t
        elapsed = now - t0
        counts[kind] = counts.get(kind, 0) + 1

        # Only print interesting events
        if kind in ("on_chat_model_start", "on_chat_model_end", "on_tool_start",
                    "on_tool_end", "on_chain_start", "on_chain_end"):
            print(f"[t={elapsed:6.2f}s | +{delta*1000:5.0f}ms] {kind:25s} {name[:30]}")
        elif kind == "on_chat_model_stream":
            chunk = event["data"].get("chunk")
            content = getattr(chunk, "content", "") if chunk else ""
            if content:
                preview = content[:40].replace("\n", " ")
                # Only print first stream and every 20th to avoid flood
                if counts[kind] in (1, 5, 20, 50, 100, 200) or counts[kind] % 50 == 0:
                    print(f"[t={elapsed:6.2f}s | +{delta*1000:5.0f}ms] stream #{counts[kind]:3d} | {preview!r}")
        last_t = now

    print()
    print(f"Done in {time.monotonic()-t0:.2f}s. Event counts:")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")


if __name__ == "__main__":
    asyncio.run(main())
