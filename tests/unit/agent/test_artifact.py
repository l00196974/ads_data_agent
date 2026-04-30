"""Tests for the Artifact model."""
from pathlib import Path

import yaml

from agent.artifact import Artifact


def test_create_artifact_creates_dir_and_writes_manifest(tmp_path: Path):
    art = Artifact.create(
        user_id="alice",
        title="KA 客户 CTR 异常分析",
        slug="ka-ctr-anomaly",
        artifacts_root=tmp_path,
        related_skills=["query-metrics", "detect-anomaly"],
    )
    assert art.dir.exists()
    assert art.dir.is_dir()
    assert art.dir.parent == tmp_path

    manifest_path = art.dir / "manifest.yaml"
    assert manifest_path.exists()
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert manifest["user_id"] == "alice"
    assert manifest["title"] == "KA 客户 CTR 异常分析"
    assert manifest["artifact_id"] == art.artifact_id
    assert manifest["related_skills"] == ["query-metrics", "detect-anomaly"]
    assert "-ka-ctr-anomaly" in art.artifact_id


def test_artifact_id_is_sortable_by_time(tmp_path: Path):
    """同一 slug 但不同时间生成的 id 字典序与时间序一致（前缀是时间戳）。"""
    import time

    art1 = Artifact.create(user_id="u", title="t", slug="x", artifacts_root=tmp_path)
    time.sleep(1.1)
    art2 = Artifact.create(user_id="u", title="t", slug="x", artifacts_root=tmp_path)
    assert art1.artifact_id < art2.artifact_id


def test_invalid_slug_patterns(tmp_path: Path):
    """slug 校验拒绝非法模式。"""
    import pytest

    invalid_cases = [
        "-leading",         # 开头连字符
        "trailing-",        # 结尾连字符
        "double--dash",     # 连续连字符
        "UPPERCASE",        # 大写
        "special_char",     # 下划线
        "",                 # 空字符串
    ]
    for slug in invalid_cases:
        with pytest.raises(ValueError, match="非法 slug"):
            Artifact.create(
                user_id="u", title="t", slug=slug, artifacts_root=tmp_path
            )
