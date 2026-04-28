"""Mock service supervisor for SKILL.md packages.

约定：技能包目录下如果有 `mock/mock-server.js`，agent 启动时自动 spawn
（用 node 跑），关闭时 terminate。让"开发态调用真实 skill"无需手动起 mock。

只识别 `mock/mock-server.js` 这一个文件名以保持简单。如果未来 skill 用 Python
或其他语言写 mock，可以扩展为读 `package.json::scripts.mock` 或 SKILL.md
frontmatter 里的 services 字段。

不暴露 mock 端口给 LLM——LLM 从来不知道有 mock 这件事，
它只是按 SKILL.md 调 CLI，CLI 自己读 config.json 里的 METRICS_API_URL。
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from asyncio import create_subprocess_exec as _spawn_subprocess

logger = logging.getLogger(__name__)

_procs: list[asyncio.subprocess.Process] = []


async def start_skill_mocks(*skills_dirs: Path | str) -> None:
    """Walk each skills dir, spawn `mock/mock-server.js` for every skill that has one."""
    for sd in skills_dirs:
        sd = Path(sd) if sd else None
        if not sd or not sd.is_dir():
            continue
        for sub in sorted(sd.iterdir()):
            if not sub.is_dir():
                continue
            mock_js = sub / "mock" / "mock-server.js"
            if not mock_js.is_file():
                continue
            try:
                # 长时间运行进程**不能**用 PIPE 收集——没人 drain 时 OS 管道
                # 缓冲区一满（通常 64KB）write() 阻塞，mock server 整个挂掉。
                # 直接继承父进程 stdio：日志打到后端终端，dev 调试方便。
                proc = await _spawn_subprocess(
                    "node",
                    str(mock_js.resolve()),
                    cwd=str(sub.resolve()),
                )
            except FileNotFoundError:
                logger.warning("node 不在 PATH，跳过 %s 的 mock", sub.name)
                continue
            _procs.append(proc)
            logger.info("[mock] spawned %s for skill '%s' (pid=%d)", mock_js.name, sub.name, proc.pid)
            print(f"[mock] {sub.name}: pid={proc.pid} ({mock_js})")


async def stop_skill_mocks() -> None:
    """Terminate all spawned mock processes. Force-kill if they don't exit in 5s."""
    for proc in _procs:
        if proc.returncode is not None:
            continue
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    _procs.clear()
