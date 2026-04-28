"""Validate that SummarizationMiddleware actually triggers at the
configured threshold.

Strategy:
  1. Monkey-patch agent.core.load_config to return a cfg with
     summarization_trigger_tokens lowered to 10k (so we hit it within a
     few rounds) and tool_output_max_bytes raised to disable P0-4
     (otherwise big tool returns get truncated before summarization sees
     them).
  2. Monkey-patch skills.system.query_campaign_report to return ~30KB JSON.
     Function name preserved so LLM tool calling matches.
  3. Run 4-5 rounds, inspect thread state for `_summarization_event`.

Cost: ~5 LLM calls plus summarization's own LLM call ≈ 6-7 LLM calls total.
"""
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from dotenv import load_dotenv
load_dotenv()

# ---- 1. monkey-patch config BEFORE agent.core is imported ----
import agent.config as _config_mod
_orig_load = _config_mod.load_config


def _override_load_config(*args, **kwargs):
    cfg = _orig_load(*args, **kwargs)
    cfg.agent.long_context.summarization_trigger_tokens = 10_000
    cfg.agent.long_context.summarization_keep_messages = 4
    cfg.agent.long_context.tool_output_max_bytes = 10_000_000
    return cfg


_config_mod.load_config = _override_load_config

# Also rebind in any module that already imported `from agent.config import load_config`
import agent.core as _core_mod  # noqa: E402

_core_mod.load_config = _override_load_config


# ---- 2. monkey-patch the heavy tool, preserving its name ----
import skills.system.campaign_report as _cr_mod  # noqa: E402

_orig_query = _cr_mod.query_campaign_report


async def query_campaign_report(date: str, user_id: str) -> dict:
    """查询广告活动报表（巨型 mock，~30KB JSON）"""
    campaigns = []
    for i in range(800):
        campaigns.append({
            "id": i,
            "name": f"campaign_{i}_华南-移动端-关键词组A{i % 26}",
            "impressions": 10000 + i * 17,
            "clicks": 380 + i * 5,
            "ctr": round(0.025 + i * 0.00005, 5),
            "spend": round(1240.0 + i * 9.3, 2),
            "region": ["华南", "华北", "华东", "西部"][i % 4],
            "device": ["mobile", "desktop", "tablet"][i % 3],
        })
    return {
        "date": date,
        "user_id": user_id,
        "total_count": len(campaigns),
        "campaigns": campaigns,
    }


_cr_mod.query_campaign_report = query_campaign_report
import skills.system as _sk_mod  # noqa: E402
_sk_mod.query_campaign_report = query_campaign_report
_sk_mod.SYSTEM_SKILLS = [
    query_campaign_report,
    _sk_mod.analyze_budget,
    _sk_mod.detect_anomaly,
    _sk_mod.build_chart,
]


# ---- 3. now import agent + run ----
from langchain_core.messages import HumanMessage  # noqa: E402
from langchain_core.messages.utils import count_tokens_approximately  # noqa: E402

from agent.core import build_agent, get_checkpointer, init_checkpointer  # noqa: E402
from agent.session import SessionManager  # noqa: E402
from agent.user_space import UserSpace  # noqa: E402
from api.channel.base import BaseChannel  # noqa: E402
from api.chat import PLAN_INSTRUCTION  # noqa: E402


class BufferChannel(BaseChannel):
    def __init__(self):
        super().__init__("validate_user", "validate_session")

    async def send_token(self, token): pass
    async def send_step(self, msg, step_type): pass
    async def send_plan(self, tasks): pass
    async def send_task_update(self, task_id, status): pass
    async def send_progress(self, message): pass
    async def wait_for_confirm(self, message, preview): return True

    def get_skill(self):
        async def send_plan(tasks: list) -> str:
            """声明任务 plan。"""
            return "ok"

        async def send_to_user(action: str = "text", content: str = "", **kw) -> str:
            """向用户输出文本/图表/进度。"""
            return "ok"

        return [send_plan, send_to_user]


async def inspect_thread(user_id: str) -> dict:
    cp_tuple = await get_checkpointer().aget_tuple(
        {"configurable": {"thread_id": user_id}}
    )
    if not cp_tuple or not cp_tuple.checkpoint:
        return {"messages": 0, "tokens": 0, "summarization_event": None}
    msgs = cp_tuple.checkpoint.get("channel_values", {}).get("messages", [])
    summ_event = cp_tuple.checkpoint.get("channel_values", {}).get("_summarization_event")
    try:
        tokens = count_tokens_approximately(msgs)
    except Exception:
        tokens = sum(len(str(getattr(m, "content", ""))) for m in msgs) // 4
    return {
        "messages": len(msgs),
        "tokens": tokens,
        "summarization_event": summ_event,
    }


async def main():
    await init_checkpointer()
    cfg = _config_mod.load_config()
    print(f"[config override] trigger={cfg.agent.long_context.summarization_trigger_tokens}, "
          f"keep={cfg.agent.long_context.summarization_keep_messages}, "
          f"max_bytes={cfg.agent.long_context.tool_output_max_bytes}")
    print()

    user_id = f"validate_summ_{int(time.time())}"
    sess = SessionManager()
    config = sess.get_config(user_id)

    us = UserSpace(user_id, cfg.persistence.data_dir)
    system_prompt = PLAN_INSTRUCTION + "\n\n" + us.get_agents_md()

    prompts = [
        "调用 query_campaign_report 工具查询 date='2026-04-25', user_id='heavy' 的全部数据。",
        "再调用 query_campaign_report 查 date='2026-04-26', user_id='heavy'。",
        "再调用 query_campaign_report 查 date='2026-04-27', user_id='heavy'。",
        "再调用 query_campaign_report 查 date='2026-04-28', user_id='heavy'。",
        "刚才查了 4 天数据，用一句话总结整体趋势。",
    ]

    print(f"{'rd':<4}{'elapsed':>9}{'msgs':>6}{'tokens':>8}{'summ_event':>14}")
    print("-" * 50)

    triggered_at = None
    for i, prompt in enumerate(prompts, start=1):
        ch = BufferChannel()
        agent = build_agent(
            user_id=user_id,
            system_prompt=system_prompt,
            skills=_sk_mod.SYSTEM_SKILLS,
            interrupt_on=cfg.agent.interrupt_on,
            cfg=cfg,
            extra_tools=ch.get_skill(),
        )

        t0 = time.monotonic()
        try:
            async for _ in agent.astream_events(
                {"messages": [HumanMessage(content=prompt)]},
                config=config,
                version="v2",
            ):
                pass
        except Exception as e:
            print(f"  [error] round {i}: {type(e).__name__}: {str(e)[:80]}")
        elapsed = time.monotonic() - t0

        info = await inspect_thread(user_id)
        summ = info["summarization_event"]
        marker = "TRIGGERED" if summ else "-"
        if summ and triggered_at is None:
            triggered_at = i

        print(f"{i:<4}{elapsed:>8.1f}s{info['messages']:>6}{info['tokens']:>8}{marker:>14}")

        if summ and triggered_at == i:
            print(f"  └─ cutoff_index={summ.get('cutoff_index')}")
            sm = summ.get("summary_message")
            if sm and hasattr(sm, "content"):
                preview = str(sm.content)[:150].replace("\n", " ")
                print(f"  └─ summary preview: {preview!r}")

    print()
    if triggered_at:
        print(f"✓ SummarizationMiddleware triggered at round {triggered_at}")
    else:
        print("✗ SummarizationMiddleware never triggered. "
              f"Final tokens={info['tokens']} (target was {cfg.agent.long_context.summarization_trigger_tokens})")


if __name__ == "__main__":
    asyncio.run(main())
