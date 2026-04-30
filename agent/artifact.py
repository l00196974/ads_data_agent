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


_SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


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
            raise ValueError(f"非法 slug: {slug!r}（要求 ^[a-z0-9]+(?:-[a-z0-9]+)*$）")

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

    @classmethod
    def load(cls, artifacts_root: Path, artifact_id: str) -> Artifact:
        """从磁盘加载已存在的 artifact。"""
        artifact_dir = artifacts_root / artifact_id
        manifest_path = artifact_dir / "manifest.yaml"
        if not manifest_path.exists():
            raise FileNotFoundError(f"artifact 不存在或未完成（缺 manifest.yaml）：{artifact_id}")
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        return cls(
            artifact_id=manifest["artifact_id"],
            user_id=manifest["user_id"],
            title=manifest.get("title", ""),
            dir=artifact_dir,
        )

    @staticmethod
    def list_summaries(artifacts_root: Path) -> list[dict]:
        """列出 artifacts_root 下所有有效 artifact 的 manifest 摘要，按 created_at 倒序。"""
        if not artifacts_root.is_dir():
            return []
        summaries: list[dict] = []
        for entry in artifacts_root.iterdir():
            if not entry.is_dir():
                continue
            manifest_path = entry / "manifest.yaml"
            if not manifest_path.exists():
                continue
            try:
                manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            except (yaml.YAMLError, OSError):
                continue
            file_count = sum(1 for p in entry.rglob("*") if p.is_file())
            summaries.append({
                "artifact_id": manifest.get("artifact_id", entry.name),
                "title": manifest.get("title", ""),
                "created_at": manifest.get("created_at", ""),
                "artifact_type": manifest.get("artifact_type", "mixed"),
                "description": manifest.get("description", ""),
                "file_count": file_count,
            })
        summaries.sort(key=lambda s: s["created_at"], reverse=True)
        return summaries

    def manifest(self) -> dict:
        """读取并返回 manifest dict。"""
        return yaml.safe_load((self.dir / "manifest.yaml").read_text(encoding="utf-8"))

    def tree(self) -> list[dict]:
        """返回目录树（递归）：[{path, type, size?, children?}, ...]"""
        return _build_tree(self.dir, prefix="")


def _build_tree(root: Path, prefix: str) -> list[dict]:
    nodes: list[dict] = []
    for entry in sorted(root.iterdir()):
        rel_path = f"{prefix}{entry.name}" if not prefix else f"{prefix}/{entry.name}"
        if entry.is_dir():
            nodes.append({
                "path": rel_path,
                "type": "dir",
                "children": _build_tree(entry, rel_path),
            })
        else:
            nodes.append({
                "path": rel_path,
                "type": "file",
                "size": entry.stat().st_size,
            })
    return nodes
