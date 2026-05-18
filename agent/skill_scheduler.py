"""通用 skill 定时调度器。

`config.yaml::agent.scheduled_tasks` 声明的任务，进程启动时挂上 asyncio loop 跑：

- 每 interval_seconds 触发一次
- 跑 `skills/<skill>/<script>` 脚本（async subprocess），按扩展名 auto resolve 解释器
- timeout 包裹防 hang
- cancel 时优雅退出（不丢消息）

框架完全**不感知**任务的业务语义——只知道"按周期跑某个 skill 内脚本"。任何业务（token
刷新、定时同步、缓存预热…）都通过 config.yaml 声明即可，不需要改框架代码。

为什么不复用 SKILL.md::bin：定时任务通常是后台运维操作，不应该让 LLM 通过 `run_command`
调用——脚本走 `script` 路径而非 bin 命令名，LLM 看不到这条能力。详见
`agent/config.py::ScheduledTaskConfig` 的设计取舍说明。
"""
from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ScheduledTask:
    """单个定时任务的运行时形态。从 ScheduledTaskConfig 转换而来——分离 pydantic
    模型和 runtime dataclass 让 scheduler 模块零依赖 config.py，便于独立测试。"""
    skill: str
    script: str
    interval_seconds: int = 900
    on_startup: bool = False
    timeout_seconds: int = 120


def _resolve_interpreter(script: Path) -> list[str]:
    """按扩展名挑解释器；绝对路径喂给子进程避免 cwd 叠加错乱。"""
    abs_path = str(script.resolve())
    suffix = script.suffix.lower()
    if suffix == ".js":
        return ["node", abs_path]
    if suffix == ".py":
        return [sys.executable, abs_path]
    if suffix in (".sh", ".bash"):
        return ["bash", abs_path]
    return [abs_path]


async def _run_task_once(task: ScheduledTask, skills_dir: Path) -> None:
    """单次执行一个 task——失败只记 warning，不抛（不影响 loop 继续）。"""
    skill_dir = (skills_dir / task.skill).resolve()
    script_path = (skill_dir / task.script).resolve()

    # 路径越界保护：脚本必须在 skill_dir 下，防 config 写 "../../etc/passwd" 之类
    try:
        script_path.relative_to(skill_dir)
    except ValueError:
        logger.error("scheduled[%s/%s]: script 路径越界 %s", task.skill, task.script, script_path)
        return
    if not script_path.exists():
        logger.warning("scheduled[%s/%s]: 脚本不存在 %s", task.skill, task.script, script_path)
        return

    argv = _resolve_interpreter(script_path)
    logger.info("scheduled[%s/%s]: 启动", task.skill, task.script)

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(skill_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as e:
        logger.error("scheduled[%s/%s]: 解释器找不到（node/python 未安装？）: %s",
                     task.skill, task.script, e)
        return
    except OSError as e:
        logger.error("scheduled[%s/%s]: spawn 失败: %s", task.skill, task.script, e)
        return

    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=task.timeout_seconds)
    except asyncio.TimeoutError:
        proc.kill()
        logger.warning("scheduled[%s/%s]: 超时 %ds——已 kill", task.skill, task.script, task.timeout_seconds)
        return
    except asyncio.CancelledError:
        # 退出前清理子进程——避免遗留 zombie
        proc.kill()
        raise

    if proc.returncode == 0:
        logger.info("scheduled[%s/%s]: 成功", task.skill, task.script)
    else:
        err = stderr.decode("utf-8", errors="replace").strip()
        # 截断 stderr 防日志爆炸
        logger.warning("scheduled[%s/%s]: 失败 exit=%d stderr=%s",
                       task.skill, task.script, proc.returncode, err[:500])


async def _task_loop(task: ScheduledTask, skills_dir: Path) -> None:
    """单个 task 的 async loop——按 interval 重复跑，cancel 时退出。

    on_startup=True 时立即跑一次；on_startup=False 时等 interval 后才跑首次。
    """
    if task.on_startup:
        try:
            await _run_task_once(task, skills_dir)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("scheduled[%s/%s]: on_startup 执行意外异常 %s",
                         task.skill, task.script, e, exc_info=True)

    while True:
        try:
            await asyncio.sleep(task.interval_seconds)
        except asyncio.CancelledError:
            logger.info("scheduled[%s/%s]: 收到 cancel，退出循环", task.skill, task.script)
            raise
        try:
            await _run_task_once(task, skills_dir)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            # 单次执行内的意外异常不能让 loop 死——下次 interval 后继续尝试
            logger.error("scheduled[%s/%s]: 循环内意外异常 %s",
                         task.skill, task.script, e, exc_info=True)


async def start_scheduler(
    tasks: list[ScheduledTask],
    skills_dir: Path,
) -> list[asyncio.Task]:
    """启动所有 task 的后台 loop。

    Returns asyncio.Task 列表——lifespan shutdown 时调 stop_scheduler 收尾。
    空列表 = 无任务可调度，仍正常返回（caller 也无须 if 判空）。
    """
    asyncio_tasks: list[asyncio.Task] = []
    for t in tasks:
        loop_task = asyncio.create_task(
            _task_loop(t, skills_dir),
            name=f"scheduled-{t.skill}-{t.script}",
        )
        asyncio_tasks.append(loop_task)
    if asyncio_tasks:
        logger.info("scheduler 启动 %d 个定时任务", len(asyncio_tasks))
    return asyncio_tasks


async def stop_scheduler(asyncio_tasks: list[asyncio.Task]) -> None:
    """优雅退出——cancel 全部 task + 等他们退出（最多每个 5s）。"""
    for t in asyncio_tasks:
        if not t.done():
            t.cancel()
    for t in asyncio_tasks:
        try:
            await asyncio.wait_for(t, timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        except Exception as e:
            logger.warning("scheduler stop: task 退出异常 %s", e)
