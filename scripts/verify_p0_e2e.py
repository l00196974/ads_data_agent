"""End-to-end SSE streaming verification for the P0 refactor.

Uses httpx.ASGITransport to call the FastAPI app in-process — no need to
run uvicorn separately, no port binding. Sends one real chat request to
the configured LLM and asserts the SSE event stream:

  1. exactly one `plan` event
  2. each declared task receives a `task_update(running)` BEFORE
     `task_update(done)`
  3. `running -> done` interval is wide enough to be visually perceptible
     (we sleep ~500ms-1.5s in mock skills, so this proves the runner is
     emitting in real time, not in a final batch)

Cost: one LLM call (~$0.01 with Anthropic Sonnet, less with OpenAI-compat
providers like DeepSeek). Configure via .env (LLM_API_KEY etc.).

Run:
    .venv/Scripts/python.exe scripts/verify_p0_e2e.py
"""
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

# Make project root importable when run as `python scripts/verify_p0_e2e.py`
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

import httpx
from dotenv import load_dotenv

load_dotenv()

# Default to real HTTP (uvicorn must be running on :8000) because
# httpx.ASGITransport buffers SSE responses end-to-end, defeating any
# streaming verification. Set BASE_URL env to override.
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")


PROMPT = (
    "请按以下步骤执行：先用 query_campaign_report 工具查询 2026-04-25 这一天 "
    "user_id='e2e-test' 的广告数据，然后用 build_chart 工具基于查询结果生成 "
    "一个柱状图（chart_type='bar', title='活动曝光对比'），最后用 send_to_user "
    "把分析结论和图表展示出来。"
)


async def _consume_sse(prompt: str) -> list[dict]:
    """Hit /api/chat/{user_id} via in-process ASGI transport, parse SSE."""
    user_id = f"e2e_{uuid.uuid4().hex[:6]}"
    events: list[dict] = []

    async with httpx.AsyncClient(base_url=BASE_URL, timeout=120, trust_env=False) as client:
        t0 = time.monotonic()
        print(f"[t=0.000s] sending request...")
        async with client.stream(
            "POST",
            f"/api/chat/{user_id}",
            json={"message": prompt},
        ) as resp:
            assert resp.status_code == 200, f"chat request failed: {resp.status_code}"

            current_event = "message"
            async for line in resp.aiter_lines():
                if line.strip():
                    elapsed = time.monotonic() - t0
                    preview = line[:60].replace("\n", " ")
                    print(f"[t={elapsed:6.2f}s] raw: {preview}")
                else:
                    continue
                if line.startswith("event:"):
                    current_event = line[6:].strip()
                elif line.startswith("data:"):
                    raw = line[5:].strip()
                    try:
                        data = json.loads(raw) if raw else {}
                    except json.JSONDecodeError:
                        data = {"raw": raw}
                    events.append({
                        "event": current_event,
                        "data": data,
                        "ts": time.monotonic() - t0,
                    })
                    if current_event == "done":
                        break
    return events


def _format_report(events: list[dict]) -> tuple[bool, str]:
    """Return (passed, human-readable report)."""
    plans = [e for e in events if e["event"] == "plan"]
    task_updates = [e for e in events if e["event"] == "task_update"]
    tokens = [e for e in events if e["event"] == "token"]
    steps = [e for e in events if e["event"] == "step"]
    text_outs = [e for e in events if e["event"] == "text"]
    chart_outs = [e for e in events if e["event"] == "render"]

    lines = [
        "=" * 72,
        "P0 E2E SSE Stream Verification",
        "=" * 72,
        "",
        f"Total events: {len(events)}",
        f"  - plan: {len(plans)}",
        f"  - task_update: {len(task_updates)}",
        f"  - step: {len(steps)}",
        f"  - token: {len(tokens)}",
        f"  - text (final): {len(text_outs)}",
        f"  - render (chart): {len(chart_outs)}",
        "",
    ]

    failures: list[str] = []

    if len(plans) != 1:
        failures.append(f"expected exactly 1 plan event, got {len(plans)}")
        lines.append("[FAIL] no plan event found — LLM did not call send_plan")
        return False, "\n".join(lines + failures)

    plan_tasks = plans[0]["data"].get("tasks", [])
    plan_ts = plans[0]["ts"]
    lines.append(f"plan declared at +{plan_ts:.2f}s with {len(plan_tasks)} tasks:")
    for t in plan_tasks:
        lines.append(f"  - {t.get('id')}: {t.get('name')}")
    lines.append("")

    lines.append("Per-task timing:")
    for t in plan_tasks:
        tid = t.get("id")
        ups = [u for u in task_updates if u["data"].get("task_id") == tid]
        running = next((u for u in ups if u["data"].get("status") == "running"), None)
        done = next((u for u in ups if u["data"].get("status") == "done"), None)

        if not running:
            failures.append(f"task {tid} never emitted task_update(running)")
            lines.append(f"  [FAIL] {tid}: no running event")
            continue
        if not done:
            failures.append(f"task {tid} never emitted task_update(done)")
            lines.append(f"  [FAIL] {tid}: no done event (still running at end)")
            continue
        if running["ts"] > done["ts"]:
            failures.append(f"task {tid}: running came AFTER done")
            lines.append(f"  [FAIL] {tid}: running ({running['ts']:.2f}s) > done ({done['ts']:.2f}s)")
            continue

        duration = done["ts"] - running["ts"]
        lines.append(
            f"  [OK]   {tid}: running at +{running['ts']:.2f}s, done at +{done['ts']:.2f}s, "
            f"running duration {duration*1000:.0f}ms"
        )

        if duration < 0.05:
            failures.append(
                f"task {tid}: running window only {duration*1000:.0f}ms — too short to be "
                f"perceived as streaming (was the mock skill made async with sleep?)"
            )

    lines.append("")
    if text_outs:
        last_text = text_outs[-1]["data"].get("content", "")
        preview = last_text[:80].replace("\n", " ")
        lines.append(f"Final text (preview): {preview}...")
    if chart_outs:
        chart = chart_outs[-1]["data"]
        lines.append(f"Final chart: type={chart.get('component')}, title={chart.get('title')}")

    lines.append("")
    if failures:
        lines.append("=" * 72)
        lines.append("RESULT: FAIL")
        lines.append("=" * 72)
        for f in failures:
            lines.append(f"  - {f}")
        return False, "\n".join(lines)
    else:
        lines.append("=" * 72)
        lines.append("RESULT: PASS — task_update streaming works end-to-end")
        lines.append("=" * 72)
        return True, "\n".join(lines)


async def main() -> int:
    if not (os.getenv("LLM_API_KEY") or os.getenv("ANTHROPIC_API_KEY")):
        print("ERROR: no LLM_API_KEY or ANTHROPIC_API_KEY in environment.")
        print("Configure .env (see .env.example) before running.")
        return 2

    print("Sending chat request via in-process ASGI transport...")
    print(f"Prompt: {PROMPT[:80]}...")
    print()

    try:
        events = await _consume_sse(PROMPT)
    except Exception as e:
        print(f"ERROR during SSE consumption: {e!r}")
        import traceback
        traceback.print_exc()
        return 3

    passed, report = _format_report(events)
    print(report)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
