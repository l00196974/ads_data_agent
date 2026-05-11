"""单次 chat 请求的延迟分析。

目的：把"问题→响应"的总时间拆成各个阶段，看时间到底花在哪
（LLM TTFT / agent 构建 / 工具子进程 / 最终答案流式 / ...），
据此决定下一步优化往哪儿动。

直接走 agent.astream_events（in-process），不走 SSE / HTTP——
减少干扰变量，时间更准。

跑法：
    .venv/Scripts/python.exe -m benchmarks.profile_latency
    .venv/Scripts/python.exe -m benchmarks.profile_latency --query "..." --runs 2
    .venv/Scripts/python.exe -m benchmarks.profile_latency --no-skills  # 不注入 SKILL.md prompt（baseline）
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

from agent.messages import HumanMessage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent.checkpointer import close_checkpointer, init_checkpointer  # noqa: E402
from agent.config import load_config  # noqa: E402
from agent.core import build_agent  # noqa: E402
from agent.skill_loader import load_md_skills  # noqa: E402
from agent.user_space import UserSpace  # noqa: E402
from api.chat import PLAN_INSTRUCTION  # noqa: E402


"""图表已改成 markdown ```chart``` 内联，channel 不再注入展示工具——profile 也保持空。"""


async def profile_one(query: str, user_id: str, with_skills: bool) -> dict:
    cfg = load_config()

    breakdown: dict = {"events": []}
    t_start = time.monotonic()

    def mark(label: str, **extra) -> None:
        breakdown["events"].append({
            "label": label,
            "elapsed": time.monotonic() - t_start,
            **extra,
        })

    mark("request_start")

    us = UserSpace(user_id, cfg.persistence.data_dir)
    md_pkg = load_md_skills(cfg.skills.md_dir, us.skills_dir) if with_skills else None
    mark("skills_loaded")

    system_prompt = PLAN_INSTRUCTION + "\n\n" + us.get_agents_md()
    if md_pkg and md_pkg.prompt_addition:
        system_prompt += "\n\n" + md_pkg.prompt_addition

    breakdown["system_prompt_chars"] = len(system_prompt)
    breakdown["registered_commands"] = []
    if md_pkg:
        for s in md_pkg.summaries:
            breakdown["registered_commands"].extend(s.get("commands", []))

    skills = list(md_pkg.tools) if md_pkg else []
    extra_tools = []

    mark("build_start")
    agent = build_agent(
        user_id=user_id,
        system_prompt=system_prompt,
        skills=skills,
        interrupt_on=cfg.agent.interrupt_on,
        cfg=cfg,
        extra_tools=extra_tools,
    )
    mark("build_done")

    config = {"configurable": {"thread_id": f"profile_{uuid.uuid4().hex[:8]}"}}

    first_token = False
    first_event = False
    tool_starts: dict[str, float] = {}
    token_chunks = 0
    total_token_chars = 0

    try:
        async for event in agent.astream_events(
            {"messages": [HumanMessage(content=query)]},
            config=config,
            version="v2",
        ):
            kind = event["event"]

            if not first_event:
                first_event = True
                mark("first_event_received", kind=kind)

            if kind == "on_chat_model_start":
                mark("on_chat_model_start")
            elif kind == "on_chat_model_stream":
                chunk = event["data"].get("chunk")
                content = getattr(chunk, "content", "") if chunk else ""
                if content:
                    if not first_token:
                        first_token = True
                        mark("first_token_emitted")
                    token_chunks += 1
                    total_token_chars += len(content)
            elif kind == "on_tool_start":
                tool_name = event.get("name", "tool")
                tool_starts[tool_name] = time.monotonic()
                mark(f"tool_start:{tool_name}")
            elif kind == "on_tool_end":
                tool_name = event.get("name", "tool")
                t0 = tool_starts.pop(tool_name, None)
                dur = round(time.monotonic() - t0, 3) if t0 else None
                mark(f"tool_end:{tool_name}", duration=dur)
    except Exception as e:
        mark(f"error:{type(e).__name__}", detail=repr(e)[:200])

    mark("complete")
    breakdown["token_chunks"] = token_chunks
    breakdown["total_token_chars"] = total_token_chars
    breakdown["total_seconds"] = round(time.monotonic() - t_start, 3)
    return breakdown


def print_breakdown(b: dict, query: str, run_label: str) -> None:
    print()
    print("=" * 80)
    print(f"[{run_label}] Query: {query!r}")
    print(f"  System prompt: {b['system_prompt_chars']:,} chars (~{b['system_prompt_chars']//4:,} tokens estimate)")
    print(f"  Registered commands: {b['registered_commands']}")
    print(f"  Token chunks: {b['token_chunks']}, final text chars: {b['total_token_chars']}")
    print("-" * 80)
    print(f"  {'Event':<38}{'Elapsed':>11}{'Δ':>10}  Note")
    print("-" * 80)

    prev = 0.0
    for ev in b["events"]:
        delta = ev["elapsed"] - prev
        note = ""
        if ev.get("duration") is not None:
            note = f"subprocess={ev['duration']}s"
        elif "kind" in ev:
            note = ev["kind"]
        elif "detail" in ev:
            note = ev["detail"]
        print(f"  {ev['label']:<38}{ev['elapsed']:>10.3f}s{delta:>9.3f}s  {note}")
        prev = ev["elapsed"]

    print("-" * 80)
    print(f"  TOTAL: {b['total_seconds']}s")
    print("=" * 80)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default="查询最近7天点击数据")
    parser.add_argument("--user-id", default="profile-bot")
    parser.add_argument("--runs", type=int, default=2,
                        help="跑几次（>1 用来观察热缓存效果）")
    parser.add_argument("--no-skills", action="store_true",
                        help="不加载 SKILL.md（baseline，看纯 prompt + LLM 时间）")
    args = parser.parse_args()

    await init_checkpointer()
    try:
        for i in range(args.runs):
            label = "cold" if i == 0 else f"warm #{i}"
            b = await profile_one(args.query, args.user_id, with_skills=not args.no_skills)
            print_breakdown(b, args.query, label)
    finally:
        await close_checkpointer()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
