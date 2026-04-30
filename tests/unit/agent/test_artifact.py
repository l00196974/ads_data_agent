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


def test_load_artifact_from_disk(tmp_path: Path):
    art = Artifact.create(user_id="alice", title="t", slug="x", artifacts_root=tmp_path)
    loaded = Artifact.load(tmp_path, art.artifact_id)
    assert loaded.artifact_id == art.artifact_id
    assert loaded.user_id == "alice"
    assert loaded.title == "t"


def test_load_missing_artifact_raises(tmp_path: Path):
    import pytest

    with pytest.raises(FileNotFoundError):
        Artifact.load(tmp_path, "2026-04-30-000000-nonexistent")


def test_list_artifacts_sorted_by_created_at_desc(tmp_path: Path):
    import time

    a1 = Artifact.create(user_id="u", title="first", slug="a", artifacts_root=tmp_path)
    time.sleep(1.1)
    a2 = Artifact.create(user_id="u", title="second", slug="b", artifacts_root=tmp_path)
    time.sleep(1.1)
    a3 = Artifact.create(user_id="u", title="third", slug="c", artifacts_root=tmp_path)

    summaries = Artifact.list_summaries(tmp_path)
    ids = [s["artifact_id"] for s in summaries]
    assert ids == [a3.artifact_id, a2.artifact_id, a1.artifact_id]


def test_list_artifacts_skips_dirs_without_manifest(tmp_path: Path):
    Artifact.create(user_id="u", title="t", slug="a", artifacts_root=tmp_path)
    (tmp_path / "2026-01-01-000000-broken").mkdir()
    summaries = Artifact.list_summaries(tmp_path)
    assert len(summaries) == 1


def test_tree_lists_files_and_dirs(tmp_path: Path):
    art = Artifact.create(user_id="u", title="t", slug="x", artifacts_root=tmp_path)
    (art.dir / "INDEX.md").write_text("# index", encoding="utf-8")
    (art.dir / "data").mkdir()
    (art.dir / "data" / "raw.json").write_text("{}", encoding="utf-8")

    tree = art.tree()
    paths = {node["path"] for node in _flatten(tree)}
    assert "manifest.yaml" in paths
    assert "INDEX.md" in paths
    assert "data" in paths
    assert "data/raw.json" in paths


def _flatten(tree: list) -> list:
    out = []
    for node in tree:
        out.append(node)
        if node["type"] == "dir":
            out.extend(_flatten(node.get("children", [])))
    return out
