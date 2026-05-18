"""
Wisedata Token 后台自动刷新

在 FastAPI lifespan 中启动一个 asyncio 后台任务，
每隔 CHECK_INTERVAL 秒检查一次 .wisedata-token-cache.json，
如果 token 将在 REFRESH_BEFORE_EXPIRY_SEC 内过期，则自动执行 Node.js 刷新脚本。

这个方案利用"在过期前刷新"的特点——服务器 session cookie 仍有效时，
浏览器自动导航到 ads-data 页面提取新 token，无需短信验证码 MFA。

路径约定（相对于 skills/metric-data-extractor/）：
  - lib/refresh-wisedata-token.js  — 刷新脚本
  - .wisedata-token-cache.json     — token 缓存文件
"""

import asyncio
import json
import logging
import time
from pathlib import Path

log = logging.getLogger("agent.wisedata_token_refresher")

# 每隔多久检查一次（秒）
CHECK_INTERVAL = 900  # 15 分钟

# token 剩余有效期少于此值时触发刷新（秒）
REFRESH_BEFORE_EXPIRY_SEC = 900  # 15 分钟

# Node.js 刷新脚本路径（相对于 skills/metric-data-extractor/）
REFRESH_SCRIPT_REL = Path("lib") / "refresh-wisedata-token.js"

# token 缓存文件路径（相对于 skills/metric-data-extractor/）
TOKEN_CACHE_REL = Path(".wisedata-token-cache.json")

# 技能目录名
SKILL_DIR_NAME = "metric-data-extractor"


def _get_skill_dir(project_root: Path) -> Path:
    """返回 skills/metric-data-extractor 的绝对路径。"""
    return project_root / "skills" / SKILL_DIR_NAME


def _read_token_cache(skill_dir: Path) -> dict | None:
    """读取 token 缓存，返回 {expiresAt, ...} 或 None。"""
    path = skill_dir / TOKEN_CACHE_REL
    if not path.exists():
        log.debug("Token cache file not found: %s", path)
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to read token cache: %s", e)
        return None


async def _run_refresh_script(skill_dir: Path) -> bool:
    """执行 Node.js 刷新脚本，返回是否成功。"""
    script_path = skill_dir / REFRESH_SCRIPT_REL
    if not script_path.exists():
        log.error("Refresh script not found: %s", script_path)
        return False

    log.info("Running token refresh script: %s", script_path)

    try:
        proc = await asyncio.create_subprocess_exec(
            "node",
            str(script_path),
            cwd=str(skill_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        except asyncio.TimeoutError:
            proc.kill()
            log.error("Token refresh script timed out (120s)")
            return False

        if proc.returncode == 0:
            log.info("Token refresh succeeded")
            return True
        else:
            stderr_text = stderr.decode("utf-8", errors="replace").strip()
            log.warning("Token refresh failed (exit=%d): %s", proc.returncode, stderr_text)
            return False

    except FileNotFoundError:
        log.error("node command not found — is Node.js installed and on PATH?")
        return False
    except OSError as e:
        log.error("Failed to run refresh script: %s", e)
        return False


async def token_refresh_loop(skill_dir: Path):
    """后台循环：定期检查 token 有效期，必要时触发刷新。"""
    log.info(
        "Wisedata token auto-refresh started: check_interval=%ds, "
        "refresh_before_expiry=%ds",
        CHECK_INTERVAL, REFRESH_BEFORE_EXPIRY_SEC,
    )

    while True:
        try:
            await _check_and_refresh(skill_dir)
        except asyncio.CancelledError:
            log.info("Token refresh loop cancelled")
            break
        except Exception as e:
            log.error("Unexpected error in refresh loop: %s", e, exc_info=True)

        await asyncio.sleep(CHECK_INTERVAL)


async def _check_and_refresh(skill_dir: Path):
    """单次检查逻辑：读取缓存，判断是否需要刷新。"""
    cache = _read_token_cache(skill_dir)

    if cache is None:
        # 未生成过缓存 — 由首次查询触发 browserLogin，后台不自动触发
        log.info("No token cache yet — will be created on first query")
        return

    expires_at = cache.get("expiresAt", 0)
    now_ms = time.time() * 1000
    remaining_ms = expires_at - now_ms

    if remaining_ms <= 0:
        log.info("Token already expired %d ms ago — skip (requires MFA re-login)", -int(remaining_ms / 1000))
        return
    elif remaining_ms < REFRESH_BEFORE_EXPIRY_SEC * 1000:
        log.info(
            "Token expires in %d seconds — refreshing proactively",
            int(remaining_ms / 1000),
        )
        await _run_refresh_script(skill_dir)
    else:
        log.debug(
            "Token still valid for %d seconds — no refresh needed",
            int(remaining_ms / 1000),
        )


async def start_refresher(project_root: Path) -> asyncio.Task:
    """启动 token 刷新后台任务（由 main.py lifespan 调用）。

    Args:
        project_root: 项目根目录（main.py 所在目录），用于定位 skills/ 目录。

    Returns:
        asyncio.Task: 后台任务句柄，shutdown 时 await cancel 它。
    """
    skill_dir = _get_skill_dir(project_root)
    task = asyncio.create_task(token_refresh_loop(skill_dir))
    return task
