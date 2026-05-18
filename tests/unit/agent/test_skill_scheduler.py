"""SkillScheduler 单元测试——验证通用调度器的行为契约。

测试覆盖：
- on_startup=True 时立即跑一次
- interval 循环重复触发
- cancel 优雅退出
- 脚本不存在时只记 warning 不崩
- 子进程失败（非零退出）后下个 interval 继续跑（不中断 loop）
- 超时被 kill 后下个 interval 继续跑
- 路径越界（script 写 "../..."）被拒
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.skill_scheduler import (
    ScheduledTask,
    _run_task_once,
    start_scheduler,
    stop_scheduler,
)


# ============================================================
# fixtures
# ============================================================


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    """造一个临时 skills 目录，里面塞一个最小 skill 包含 ok / fail / slow 三个 py 脚本。"""
    skill = tmp_path / "test-skill" / "bin"
    skill.mkdir(parents=True)

    (skill / "ok.py").write_text("import sys; sys.exit(0)\n", encoding="utf-8")
    (skill / "fail.py").write_text("import sys; sys.exit(1)\n", encoding="utf-8")
    (skill / "slow.py").write_text(
        "import time; time.sleep(30)\n", encoding="utf-8",
    )
    # touch sentinel 脚本——每次跑写一行到文件，测试可数行数判断"跑了几次"
    (skill / "touch.py").write_text(
        f"open(r'{tmp_path / 'sentinel.txt'}', 'a').write('x')\n",
        encoding="utf-8",
    )

    return tmp_path


# ============================================================
# _run_task_once：单次执行的各种情形
# ============================================================


@pytest.mark.asyncio
async def test_run_task_once_success(skills_dir: Path):
    """脚本正常退出（exit 0）—— 不抛、不卡。"""
    task = ScheduledTask(skill="test-skill", script="bin/ok.py", timeout_seconds=5)
    await _run_task_once(task, skills_dir)


@pytest.mark.asyncio
async def test_run_task_once_nonzero_exit_logged_not_raised(skills_dir: Path, caplog):
    """脚本非零退出—— 只 log warning，不抛（loop 才能继续下一轮）。"""
    task = ScheduledTask(skill="test-skill", script="bin/fail.py", timeout_seconds=5)
    await _run_task_once(task, skills_dir)
    assert any("失败" in r.message for r in caplog.records), \
        "非零退出应该被记 warning"


@pytest.mark.asyncio
async def test_run_task_once_script_not_found(skills_dir: Path, caplog):
    """脚本路径不存在—— warning，不抛。"""
    task = ScheduledTask(skill="test-skill", script="bin/nonexistent.py")
    await _run_task_once(task, skills_dir)
    assert any("不存在" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_task_once_path_traversal_rejected(skills_dir: Path, caplog):
    """script 写 "../../etc/passwd" 之类越界路径—— 必须被拒。安全防线。"""
    task = ScheduledTask(skill="test-skill", script="../../../../../etc/passwd")
    await _run_task_once(task, skills_dir)
    assert any("越界" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_run_task_once_timeout_kills_process(skills_dir: Path, caplog):
    """脚本超时——被 kill 不卡 loop。"""
    task = ScheduledTask(
        skill="test-skill", script="bin/slow.py",
        timeout_seconds=1,  # 脚本要 sleep 30s，1s 必超时
    )
    await _run_task_once(task, skills_dir)
    assert any("超时" in r.message for r in caplog.records)


# ============================================================
# start_scheduler / stop_scheduler：lifespan 集成行为
# ============================================================


@pytest.mark.asyncio
async def test_start_empty_tasks_returns_empty_list():
    """空 task 列表 → 返回 [] 不报错；caller 也无需特殊判空。"""
    asyncio_tasks = await start_scheduler([], Path("/tmp"))
    assert asyncio_tasks == []


@pytest.mark.asyncio
async def test_on_startup_runs_immediately(skills_dir: Path):
    """on_startup=True：start 后等极短时间，sentinel 已经被写。"""
    sentinel = skills_dir / "sentinel.txt"
    assert not sentinel.exists()

    task = ScheduledTask(
        skill="test-skill", script="bin/touch.py",
        interval_seconds=3600,  # interval 拉很长，确保首次写来自 on_startup
        on_startup=True,
        timeout_seconds=5,
    )
    asyncio_tasks = await start_scheduler([task], skills_dir)

    # 等 touch.py 跑完——subprocess spawn + 写文件，给 1.5s 应该足够
    for _ in range(30):
        if sentinel.exists():
            break
        await asyncio.sleep(0.05)

    try:
        assert sentinel.exists(), "on_startup=True 应该立即跑"
        assert sentinel.read_text() == "x"
    finally:
        await stop_scheduler(asyncio_tasks)


@pytest.mark.asyncio
async def test_no_startup_skips_first_immediate_run(skills_dir: Path):
    """on_startup=False：start 后短时间内不应执行（要等 interval）。"""
    sentinel = skills_dir / "sentinel.txt"

    task = ScheduledTask(
        skill="test-skill", script="bin/touch.py",
        interval_seconds=3600,
        on_startup=False,   # ★ 关键：不立即跑
        timeout_seconds=5,
    )
    asyncio_tasks = await start_scheduler([task], skills_dir)
    await asyncio.sleep(0.3)  # 等一下，确认没跑
    try:
        assert not sentinel.exists(), "on_startup=False 不应该立即跑"
    finally:
        await stop_scheduler(asyncio_tasks)


@pytest.mark.asyncio
async def test_interval_loop_repeats(skills_dir: Path):
    """interval 循环：on_startup + 短 interval，应该被触发多次。"""
    sentinel = skills_dir / "sentinel.txt"

    task = ScheduledTask(
        skill="test-skill", script="bin/touch.py",
        interval_seconds=1,   # ★ 短 interval 测重复
        on_startup=True,
        timeout_seconds=5,
    )
    asyncio_tasks = await start_scheduler([task], skills_dir)
    # 等约 2.5s：on_startup 1 次 + 1s 后 1 次 + 接近 2s 后 1 次 ≈ 至少 2 次
    await asyncio.sleep(2.5)
    await stop_scheduler(asyncio_tasks)

    runs = len(sentinel.read_text()) if sentinel.exists() else 0
    assert runs >= 2, f"2.5s 内 interval=1s 应至少跑 2 次，实际 {runs} 次"


@pytest.mark.asyncio
async def test_stop_scheduler_cancels_tasks(skills_dir: Path):
    """stop_scheduler 必须让所有 asyncio.Task 进入 done 状态——避免 lifespan 退出时挂任务。"""
    task = ScheduledTask(
        skill="test-skill", script="bin/touch.py",
        interval_seconds=3600, on_startup=False,
    )
    asyncio_tasks = await start_scheduler([task], skills_dir)
    assert all(not t.done() for t in asyncio_tasks)

    await stop_scheduler(asyncio_tasks)
    assert all(t.done() for t in asyncio_tasks), "stop 后所有 task 应该 done"


@pytest.mark.asyncio
async def test_failure_does_not_stop_loop(skills_dir: Path):
    """单次失败（脚本非零退出）后循环不能死——下个 interval 必须继续。"""
    counter = {"runs": 0}

    async def fake_run(task, skills_dir):
        counter["runs"] += 1
        if counter["runs"] == 1:
            raise RuntimeError("simulated failure")  # 第一次抛意外异常
        # 后续正常返回

    task = ScheduledTask(
        skill="test-skill", script="bin/touch.py",
        interval_seconds=1, on_startup=True, timeout_seconds=5,
    )
    with patch("agent.skill_scheduler._run_task_once", side_effect=fake_run):
        asyncio_tasks = await start_scheduler([task], skills_dir)
        await asyncio.sleep(2.5)
        await stop_scheduler(asyncio_tasks)

    assert counter["runs"] >= 2, \
        f"第一次抛异常不应该让 loop 停掉，实际跑了 {counter['runs']} 次"
