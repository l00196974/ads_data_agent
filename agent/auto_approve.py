"""HitL "免确认"全局白名单。

用户在确认弹窗里勾选"以后不再确认 [工具名]"会把该工具名加进白名单，
后续所有请求该工具（且属于 medium_risk 类）直接放行，不再弹窗。

high_risk 类工具即使在白名单里也会被强制弹窗——runner 决策时硬挡，
保证高危操作永远过人手。

存储：`{data_dir}/auto_approve.yaml`，简单的 yaml list。

设计取舍：
  1. **全局共享**：任何用户勾选 = 所有用户生效。简单，符合"工具风险与人无关"
     的直觉（一个动作要么对系统安全要么不安全）。Future 升级到 per-user
     时只需把存储路径改成 `data/{user_id}/preferences.yaml` + 给 contains/add
     函数加 user_id 参数，runner 决策时传当前 user_id 进来即可。
  2. **永久生效**：勾过就一直在，除非用户主动从设置面板移除。不做过期，
     避免用户惊讶"明明记住过为啥又问我"。
  3. **进程内缓存 + 文件持久化**：避免每次决策读盘。写时持锁串行化，
     读时无锁。多进程部署需要换共享存储（redis / db）—— 现在单进程足够。
"""
from __future__ import annotations

import logging
from pathlib import Path
from threading import Lock

import yaml


logger = logging.getLogger(__name__)

_lock = Lock()
_cache: set[str] | None = None
_data_dir: Path | None = None


def init(data_dir: str | Path) -> None:
    """启动时调一次，从磁盘加载已有白名单到内存。
    幂等：重复调用 = 重新加载（用于测试）。
    """
    global _data_dir, _cache
    _data_dir = Path(data_dir)
    _data_dir.mkdir(parents=True, exist_ok=True)
    _cache = _load_from_disk()
    logger.info("auto_approve init: loaded %d entries from %s", len(_cache), _file())


def _file() -> Path:
    if _data_dir is None:
        raise RuntimeError("auto_approve not initialized; call init(data_dir) first")
    return _data_dir / "auto_approve.yaml"


def _load_from_disk() -> set[str]:
    f = _file()
    if not f.exists():
        return set()
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or []
        if not isinstance(data, list):
            logger.warning("auto_approve.yaml malformed (not a list), ignoring")
            return set()
        return set(str(x) for x in data)
    except Exception as e:
        logger.warning("auto_approve.yaml load failed: %s", e)
        return set()


def _save_to_disk(s: set[str]) -> None:
    _file().write_text(
        yaml.safe_dump(sorted(s), allow_unicode=True),
        encoding="utf-8",
    )


def contains(tool_name: str) -> bool:
    """白名单是否包含此工具名。未初始化时安全返回 False。"""
    if _cache is None or not tool_name:
        return False
    return tool_name in _cache


def add(tool_name: str) -> None:
    """加入白名单 + 持久化到磁盘。线程安全；幂等（已在 = no-op）。"""
    global _cache
    if not tool_name:
        return
    with _lock:
        if _cache is None:
            logger.warning("auto_approve.add called before init; ignored")
            return
        if tool_name not in _cache:
            _cache.add(tool_name)
            _save_to_disk(_cache)
            logger.info("auto_approve added: %s (total=%d)", tool_name, len(_cache))


def remove(tool_name: str) -> None:
    """从白名单移除 + 持久化。用户在设置页"撤销免确认"时调用。"""
    global _cache
    with _lock:
        if _cache is None or tool_name not in _cache:
            return
        _cache.discard(tool_name)
        _save_to_disk(_cache)
        logger.info("auto_approve removed: %s (total=%d)", tool_name, len(_cache))


def list_all() -> list[str]:
    """返回全部白名单（排序后），用于设置页展示。"""
    return sorted(_cache or set())
