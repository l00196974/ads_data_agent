"""ToolOutputTruncationMiddleware 卸盘文件的 TTL 清理。

为什么需要：
  middleware 把超大 ToolMessage content 卸到磁盘后，messages 里只留摘要 + 文件
  指针。一旦 SummarizationMiddleware 触发把老 messages 替换成 summary string，
  那些指针就消失了——文件**孤立**仍占盘。永久删除 conversation 时虽然会 rmtree
  对应 conv 子目录，但软删 / 切换会话不会，长期累积失控。

策略（启动时一次性清理）：
  - 默认 30 天 TTL（config.yaml::agent.long_context.tool_output_ttl_days）
  - 0 = 不清理（关闭功能）
  - 扫所有 user/tool_outputs/ 下的文件，mtime 超龄即删
  - 顺手 rmdir 空 conv 子目录（避免大量空目录残留）
  - 失败容忍：单个文件删不掉不阻塞批处理，只 log
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def cleanup_expired(data_dir: str | Path, ttl_days: int) -> dict[str, int]:
    """删除 data_dir/*/tool_outputs/**/*.json 中 mtime 超过 ttl_days 的文件。

    返回 {files_deleted, dirs_removed, errors} 统计。ttl_days <= 0 直接 no-op。
    """
    stats = {"files_deleted": 0, "dirs_removed": 0, "errors": 0}
    if ttl_days <= 0:
        return stats

    data_path = Path(data_dir)
    if not data_path.exists():
        return stats

    cutoff = time.time() - ttl_days * 86400
    # 扫每个 user 目录下的 tool_outputs/
    for user_dir in data_path.iterdir():
        if not user_dir.is_dir():
            continue
        outputs_dir = user_dir / "tool_outputs"
        if not outputs_dir.exists():
            continue

        # 删超龄文件——递归扫所有子目录（含按 conv_id 分组的旧 / 新结构）
        for f in outputs_dir.rglob("*"):
            if not f.is_file():
                continue
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    stats["files_deleted"] += 1
            except OSError as e:
                logger.warning("cleanup: cannot delete %s: %s", f, e)
                stats["errors"] += 1

        # 顺手 rmdir 空目录（叶子优先：先 rglob 再过滤目录，反向排序保证从最深层删）
        for d in sorted(outputs_dir.rglob("*"), key=lambda p: -len(p.parts)):
            if not d.is_dir():
                continue
            try:
                d.rmdir()  # 仅当目录为空时成功
                stats["dirs_removed"] += 1
            except OSError:
                pass  # 非空目录直接跳过——预期行为

    if stats["files_deleted"] or stats["dirs_removed"]:
        logger.info(
            "tool_outputs cleanup: deleted %d files, removed %d empty dirs (ttl=%dd)",
            stats["files_deleted"], stats["dirs_removed"], ttl_days,
        )
    return stats
