"""Artifact 模型：用户工作区里的一份分析产物（一个目录 + manifest）。

设计前提见 docs/superpowers/specs/2026-04-30-artifact-system-design.md。

核心约定：
- artifact_id 格式：{YYYY-MM-DD-HHmmss}-{slug}（时间戳保证唯一 + 可排序）
- 目录结构自由，仅 manifest.yaml 是契约文件
- immutable：写完不变，没有"更新 artifact"的 API（同 id 后写覆盖前写）
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml


_SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass
class Artifact:
    """一份 artifact 的内存视图，绑定到磁盘上的目录。"""

    artifact_id: str
    user_id: str
    title: str
    dir: Path

    @classmethod
    def create(
        cls,
        user_id: str,
        title: str,
        slug: str,
        artifacts_root: Path,
        artifact_type: str = "report",
        description: str = "",
        entry_files: Optional[list[str]] = None,
        related_skills: Optional[list[str]] = None,
        related_conversation_id: Optional[str] = None,
    ) -> Artifact:
        """创建一个新 artifact 目录 + 写入初始 manifest.yaml。

        slug 由调用方传入，要求小写字母数字 + 连字符。
        """
        if not _SLUG_PATTERN.match(slug):
            raise ValueError(f"非法 slug: {slug!r}（要求 ^[a-z0-9][a-z0-9-]*$）")

        now = datetime.now()
        timestamp = now.strftime("%Y-%m-%d-%H%M%S")
        artifact_id = f"{timestamp}-{slug}"

        artifacts_root.mkdir(parents=True, exist_ok=True)
        artifact_dir = artifacts_root / artifact_id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            "artifact_id": artifact_id,
            "title": title,
            "created_at": now.isoformat(timespec="seconds"),
            "user_id": user_id,
            "artifact_type": artifact_type,
            "description": description,
            "entry_files": entry_files or [],
            "related_skills": related_skills or [],
        }
        if related_conversation_id:
            manifest["related_conversation_id"] = related_conversation_id

        (artifact_dir / "manifest.yaml").write_text(
            yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return cls(
            artifact_id=artifact_id,
            user_id=user_id,
            title=title,
            dir=artifact_dir,
        )
