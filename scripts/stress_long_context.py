"""Long-context stress test.

Drives the agent through N consecutive rounds on the SAME thread_id, then
inspects the in-memory MemorySaver to see what's actually accumulating.

What we measure each round:
  - wall-clock latency
  - LLM round trips (on_chat_model_start count)
  - business-tool calls
  - cumulative messages in thread
  - cumulative thread bytes (rough proxy for input tokens)
  - whether the LLM successfully referenced earlier rounds (round 4/5)

Bypasses SSE: invokes agent.astream_events directly. Uses a buffer-only
channel to satisfy send_plan / send_to_user tool calls without I/O.

Cost: ~$0.05 with ark-code-latest at the time of writing (5 rounds).
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv()

from langchain_core.messages import HumanMessage

from agent.config import load_config
from agent.core import build_agent, get_checkpointer, init_checkpointer
from agent.session import SessionManager
from agent.user_space import UserSpace
from api.channel.base import BaseChannel
from api.chat import PLAN_INSTRUCTION
from skills.system import SYSTEM_SKILLS


# ---------- buffer channel: collects events, no real I/O ----------

class BufferChannel(BaseChannel):
    def __init__(self):
        super().__init__("stress_user", "stress_session")
        self.events: list[tuple] = []

    async def send_token(self, token: str) -> None:
        self.events.append(("token", token))

    async def send_step(self, msg: str, step_type: str) -> None:
        self.events.append(("step", msg, step_type))

    async def send_plan(self, tasks: list) -> None:
        self.events.append(("plan", tasks))

    async def send_task_update(self, task_id: str, status: str) -> None:
        self.events.append(("task_update", task_id, status))

    async def send_progress(self, message: str) -> None:
        self.events.append(("progress", message))

    async def wait_for_confirm(self, message: str, preview: list) -> bool:
        return True

    def get_skill(self):
        ch = self

        async def send_plan(tasks: list) -> str:
            """在开始执行任何工具之前，必须首先调用此工具规划任务步骤。tasks 列表，每项 {"id":"t1","name":"..."}。"""
            await ch.send_plan(tasks)
            return "ok"

        async def send_to_user(action: str, content: str = "", chart_type: str = "bar",
                               title: str = "", x_data: list = (), series: list = ()) -> str:
            """向用户输出文本/图表/进度。action ∈ {text, chart, progress}。"""
            ch.events.append(("send_to_user", action, content[:80] if isinstance(content, str) else ""))
            return "ok"

        return [send_plan, send_to_user]


# ---------- per-round runner (mimics AgentRunner but tracks metrics) ----------

async def run_one_round(prompt: str, user_id: str, cfg, build_fn) -> dict:
    channel = BufferChannel()
    skill = channel.get_skill()
    extra_tools = skill if isinstance(skill, list) else [skill]
    agent = build_fn(extra_tools=extra_tools)

    session_mgr = SessionManager()
    config = session_mgr.get_config(user_id)

    metrics = {
        "llm_calls": 0,
        "tool_starts": 0,
        "stream_chunks": 0,
        "non_empty_chunks": 0,
        "first_token_at": None,
    }
    t0 = time.monotonic()

    async for event in agent.astream_events(
        {"messages": [HumanMessage(content=prompt)]},
        config=config,
        version="v2",
    ):
        kind = event["event"]
        if kind == "on_chat_model_start":
            metrics["llm_calls"] += 1
        elif kind == "on_chat_model_stream":
            metrics["stream_chunks"] += 1
            chunk = event["data"].get("chunk")
            content = getattr(chunk, "content", None) if chunk else None
            if content:
                metrics["non_empty_chunks"] += 1
                if metrics["first_token_at"] is None:
                    metrics["first_token_at"] = time.monotonic() - t0
        elif kind == "on_tool_start":
            name = event.get("name", "")
            if not name.startswith("send_"):
                metrics["tool_starts"] += 1

    metrics["elapsed"] = time.monotonic() - t0
    metrics["events"] = channel.events
    return metrics


# ---------- thread inspection ----------

async def inspect_thread(user_id: str) -> dict:
    config = {"configurable": {"thread_id": user_id}}
    cp_tuple = await get_checkpointer().aget_tuple(config)
    checkpoint = cp_tuple.checkpoint if cp_tuple else None
    if not checkpoint:
        return {"messages": 0, "total_bytes": 0, "by_type": {}}

    msgs = checkpoint.get("channel_values", {}).get("messages", [])
    by_type: dict[str, dict] = {}
    total_bytes = 0
    for m in msgs:
        cls = type(m).__name__
        # rough size: serialize content + tool_calls
        size = len(str(getattr(m, "content", "")))
        if hasattr(m, "tool_calls") and m.tool_calls:
            size += sum(len(json.dumps(tc, default=str)) for tc in m.tool_calls)
        by_type.setdefault(cls, {"count": 0, "bytes": 0})
        by_type[cls]["count"] += 1
        by_type[cls]["bytes"] += size
        total_bytes += size

    return {
        "messages": len(msgs),
        "total_bytes": total_bytes,
        "by_type": by_type,
    }


# ---------- prompts ----------

PROMPTS = [
    # Round 1: tools work, generate baseline data
    ("查询 2026-04-25 user_id='stress' 的广告数据并生成柱状图（chart_type='bar', title='第一轮'）。", "round 1: query + chart"),
    # Round 2: switch tool family
    ("用 analyze_budget 工具看 stress 用户的预算情况。", "round 2: budget"),
    # Round 3: yet another tool
    ("用 detect_anomaly 看 stress 用户在 2026-04-25 华南区的异常。", "round 3: anomaly"),
    # Round 4: explicitly references round 1 — tests memory recall
    ("把第一轮查到的曝光数据再用饼图（chart_type='pie', title='复现第一轮'）展示一遍。", "round 4: recall round 1"),
    # Round 5: pure summary, no tool calls
    ("回顾我们刚才看的所有数据，用一段中文文字总结一下，不要再调用任何工具。", "round 5: text summary"),
]


# ---------- main ----------

async def main():
    await init_checkpointer()
    cfg = load_config()
    user_id = f"stress_{int(time.time())}"  # fresh thread each run
    print(f"thread_id = {user_id}\n")

    us = UserSpace(user_id, cfg.persistence.data_dir)
    system_prompt = PLAN_INSTRUCTION + "\n\n" + us.get_agents_md()

    def build_fn(extra_tools=None):
        return build_agent(
            user_id=user_id,
            system_prompt=system_prompt,
            skills=SYSTEM_SKILLS,
            interrupt_on=cfg.agent.interrupt_on,
            cfg=cfg,
            extra_tools=extra_tools,
        )

    rows = []
    print(f"{'round':<6}{'desc':<26}{'elapsed':>9}{'TTFT':>9}{'LLM':>5}{'tools':>7}"
          f"{'msgs':>7}{'bytes':>9}{'Δbytes':>9}")
    print("-" * 92)

    prev_bytes = 0
    for i, (prompt, desc) in enumerate(PROMPTS, start=1):
        m = await run_one_round(prompt, user_id, cfg, build_fn)
        thread = await inspect_thread(user_id)
        d_bytes = thread["total_bytes"] - prev_bytes
        prev_bytes = thread["total_bytes"]

        ttft = f"{m['first_token_at']:.1f}s" if m['first_token_at'] else "-"
        print(f"{i:<6}{desc:<26}{m['elapsed']:>8.1f}s{ttft:>9}{m['llm_calls']:>5}"
              f"{m['tool_starts']:>7}{thread['messages']:>7}{thread['total_bytes']:>9}{d_bytes:>+9}")
        rows.append({"round": i, "desc": desc, **m, "thread": thread})

    # Final breakdown
    print()
    print("Final thread breakdown:")
    final = await inspect_thread(user_id)
    for cls, info in sorted(final["by_type"].items(), key=lambda x: -x[1]["bytes"]):
        avg = info["bytes"] // max(info["count"], 1)
        print(f"  {cls:<25} count={info['count']:<3} bytes={info['bytes']:<7} (avg {avg}/msg)")

    # Round 5 LLM output preview
    print()
    print("Round 5 (summary) — events captured:")
    r5 = rows[-1]
    print(f"  total events: {len(r5['events'])}, types: "
          f"{set(e[0] for e in r5['events'])}")
    print(f"  stream_chunks={r5['stream_chunks']}, non_empty={r5['non_empty_chunks']}")

    # Dump last 4 messages from thread to see what actually got stored
    print()
    print("Last 4 messages in thread (raw):")
    config = {"configurable": {"thread_id": user_id}}
    cp_tuple = await get_checkpointer().aget_tuple(config)
    if cp_tuple and cp_tuple.checkpoint:
        msgs = cp_tuple.checkpoint.get("channel_values", {}).get("messages", [])
        for m in msgs[-4:]:
            content = str(getattr(m, "content", ""))[:200]
            tcs = getattr(m, "tool_calls", None)
            tc_summary = ""
            if tcs:
                tc_summary = f" tool_calls={[tc.get('name') for tc in tcs]}"
            print(f"  [{type(m).__name__}] content={content!r}{tc_summary}")


if __name__ == "__main__":
    asyncio.run(main())
