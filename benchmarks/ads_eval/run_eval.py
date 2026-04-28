"""Ads Agent 评测主程序。

跑法：
    .venv/Scripts/python.exe -m benchmarks.ads_eval.run_eval

它会：
  1. 启动 SKILL.md 包配套的 mock server（如果有 mock/mock-server.js）
  2. init checkpointer（async）
  3. 加载 tasks.yaml
  4. 对每条 task：
     - 用 task.query 当用户消息
     - 直接驱动 agent.astream_events（in-process，不走 SSE）
     - 捕获 on_tool_start / on_chat_model_stream 事件
     - 打分（Tool Recall / Answer Contains / Numerical EM）
  5. 输出汇总表 + 写 results.json

用 in-process 调用是为了：
  - 不依赖外部 uvicorn / SSE 解析
  - 避开 conversation_id 状态污染（每题独立 thread_id）
  - 跑得快、易诊断

代价是 SSE 链路本身（runner / channel）不被覆盖，但这一层有 unit tests 已经测过。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

import yaml
from langchain_core.messages import HumanMessage

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent.checkpointer import close_checkpointer, init_checkpointer  # noqa: E402
from agent.config import load_config  # noqa: E402
from agent.core import build_agent  # noqa: E402
from agent.mock_supervisor import start_skill_mocks, stop_skill_mocks  # noqa: E402
from agent.skill_loader import load_md_skills  # noqa: E402
from agent.user_space import UserSpace  # noqa: E402
from api.chat import PLAN_INSTRUCTION  # noqa: E402
from benchmarks.ads_eval.scorers import (  # noqa: E402
    aggregate,
    score_answer_contains,
    score_answer_numerical,
    score_tool_recall,
)


def _make_eval_channel_skills(captures: dict) -> list:
    """提供 send_plan / send_to_user 的 stub 实现，捕获调用而不做 IO。

    LLM 看到的 tool 签名 + docstring 跟 WebSSEChannel 完全一致——保证 agent
    行为不会因为 channel 类型变化而漂移。
    """
    async def send_plan(tasks: list) -> str:
        """在开始执行任何工具之前，必须首先调用此工具规划任务步骤。

        tasks: 任务列表，每项格式为 {"id": "t1", "name": "任务描述"}
        任务的 running/done 状态由系统自动推断，无需也无法手动上报。
        """
        captures["plans"].append(tasks)
        return "ok"

    async def send_to_user(
        action: str,
        content: str = "",
        chart_type: str = "bar",
        title: str = "",
        x_data: list = None,
        series: list = None,
    ) -> str:
        """向用户发送结构化产物。

        action: "chart" 渲染图表（需 chart_type/title/x_data/series）
                "progress" 顶部状态条文字（content 字段）
        """
        captures["outputs"].append({
            "action": action,
            "content": content,
            "chart_type": chart_type,
            "title": title,
            "x_data": x_data or [],
            "series": series or [],
        })
        return "ok"

    return [send_plan, send_to_user]


async def _run_task(task: dict, cfg, md_pkg, user_id: str) -> dict:
    """跑一条 task，返回 observation（待打分）。"""
    captures = {
        "plans": [],
        "outputs": [],
        "tools_called": [],
        "tokens": [],
    }

    skills = list(md_pkg.tools)
    extra_tools = _make_eval_channel_skills(captures)

    system_prompt = PLAN_INSTRUCTION
    if md_pkg.prompt_addition:
        system_prompt += "\n\n" + md_pkg.prompt_addition

    agent = build_agent(
        user_id=user_id,
        system_prompt=system_prompt,
        skills=skills,
        interrupt_on=cfg.agent.interrupt_on,
        cfg=cfg,
        extra_tools=extra_tools,
    )

    config = {"configurable": {"thread_id": f"eval_{task['id']}_{uuid.uuid4().hex[:8]}"}}

    t0 = time.monotonic()
    try:
        async for event in agent.astream_events(
            {"messages": [HumanMessage(content=task["query"])]},
            config=config,
            version="v2",
        ):
            kind = event["event"]
            if kind == "on_tool_start":
                captures["tools_called"].append({
                    "name": event.get("name"),
                    "raw_input": event.get("data", {}).get("input", {}),
                })
            elif kind == "on_chat_model_stream":
                chunk = event["data"].get("chunk")
                if chunk and getattr(chunk, "content", ""):
                    captures["tokens"].append(chunk.content)
    except Exception as e:
        captures["error"] = repr(e)

    captures["latency_s"] = time.monotonic() - t0
    captures["final_text"] = "".join(captures["tokens"])
    return captures


def _score_one(task: dict, observation: dict) -> dict:
    sub_scores = {
        "tool_recall": score_tool_recall(task, observation),
        "answer_contains": score_answer_contains(task, observation),
        "answer_numerical": score_answer_numerical(task, observation),
    }
    overall = aggregate(*sub_scores.values())
    return {"sub": sub_scores, "overall": overall}


def _print_summary(results: list[dict]) -> None:
    n = len(results)
    n_pass = sum(1 for r in results if r["scores"]["overall"]["passed"])
    avg_tr = sum(r["scores"]["sub"]["tool_recall"]["score"] for r in results) / max(n, 1)
    avg_ac = sum(r["scores"]["sub"]["answer_contains"]["score"] for r in results) / max(n, 1)
    avg_lat = sum(r["observation"]["latency_s"] for r in results) / max(n, 1)

    print()
    print("=" * 72)
    print(f"评测汇总：{n_pass}/{n} 通过")
    print("-" * 72)
    print(f"  Tool Recall (avg):     {avg_tr:.2f}")
    print(f"  Answer Contains (avg): {avg_ac:.2f}")
    print(f"  Latency (avg):         {avg_lat:.2f}s")
    print("-" * 72)
    print(f"{'ID':<8}{'Diff':<8}{'Pass':<6}{'TR':<6}{'AC':<6}{'Lat':<8}{'Tools called'}")
    for r in results:
        scores = r["scores"]
        obs = r["observation"]
        tools_str = ",".join(c.get("name", "?") for c in obs.get("tools_called", []))[:40]
        print(
            f"{r['task']['id']:<8}"
            f"{r['task'].get('difficulty', '-'):<8}"
            f"{'✓' if scores['overall']['passed'] else '✗':<6}"
            f"{scores['sub']['tool_recall']['score']:<6.2f}"
            f"{scores['sub']['answer_contains']['score']:<6.2f}"
            f"{obs['latency_s']:<8.2f}"
            f"{tools_str}"
        )
    print("=" * 72)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--tasks", default=str(Path(__file__).parent / "tasks.yaml"),
        help="路径到 tasks.yaml",
    )
    parser.add_argument(
        "--out", default=str(Path(__file__).parent / "results.json"),
        help="结果 JSON 输出路径",
    )
    parser.add_argument(
        "--filter", default=None, help="只跑 id 包含此子串的任务",
    )
    parser.add_argument(
        "--user-id", default="eval-bot", help="用户身份（决定加载哪个用户的私有 skill）",
    )
    args = parser.parse_args()

    cfg = load_config()

    # 1. 起 mock + checkpointer（与生产 lifespan 一致）
    await init_checkpointer()
    await start_skill_mocks(cfg.skills.md_dir)

    try:
        # 2. 加载 SKILL.md 包
        us = UserSpace(args.user_id, cfg.persistence.data_dir)
        md_pkg = load_md_skills(cfg.skills.md_dir, us.skills_dir)

        if not md_pkg.tools:
            print("[!] 没加载到任何 SKILL.md 技能包，评测意义有限")
        else:
            print(f"[i] 加载到 {len(md_pkg.summaries)} 个 skill，子命令：")
            cmds = set()
            for s in md_pkg.summaries:
                cmds.update(s.get("commands", []))
            for c in sorted(cmds):
                print(f"      - {c}")

        # 3. 加载题库
        with open(args.tasks, encoding="utf-8") as f:
            task_data = yaml.safe_load(f)
        tasks = task_data.get("tasks", [])
        if args.filter:
            tasks = [t for t in tasks if args.filter in t.get("id", "")]

        print(f"[i] 评测 {len(tasks)} 题")

        # 4. 串行跑（避免 mock server 并发干扰；后续要并行可加 semaphore）
        results: list[dict] = []
        for i, task in enumerate(tasks, 1):
            print(f"\n[{i}/{len(tasks)}] {task['id']} ({task.get('difficulty', '-')}): {task['query']}")
            obs = await _run_task(task, cfg, md_pkg, args.user_id)
            scores = _score_one(task, obs)
            results.append({"task": task, "observation": obs, "scores": scores})
            print(
                f"      pass={scores['overall']['passed']} "
                f"tr={scores['sub']['tool_recall']['score']:.2f} "
                f"ac={scores['sub']['answer_contains']['score']:.2f} "
                f"lat={obs['latency_s']:.2f}s"
            )

        _print_summary(results)

        # 5. 写 JSON 报告（可选给 CI / 趋势分析用）
        out_path = Path(args.out)
        out_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(f"\n详细结果写入：{out_path}")

        return 0 if all(r["scores"]["overall"]["passed"] for r in results) else 1
    finally:
        await stop_skill_mocks()
        await close_checkpointer()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
