"""tool_outputs TTL 清理契约测试。

锁住：
  1. ttl_days <= 0 直接 no-op（开发场景关闭）
  2. 超龄文件被删，未超龄保留
  3. 跨 user / 跨 conv 都能扫到
  4. 空目录被 rmdir
  5. data_dir 不存在时不崩
"""
import os
import time
from pathlib import Path

from agent.tool_outputs_cleanup import cleanup_expired


def _touch(path: Path, age_days: float) -> None:
    """造一个 mtime 为 age_days 天前的文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x")
    if age_days > 0:
        ts = time.time() - age_days * 86400
        os.utime(path, (ts, ts))


def test_ttl_zero_skips_cleanup(tmp_path):
    """ttl_days=0 表示关闭——即使文件超龄也不动。"""
    f = tmp_path / "alice" / "tool_outputs" / "conv1" / "old.json"
    _touch(f, age_days=100)
    stats = cleanup_expired(tmp_path, ttl_days=0)
    assert stats == {"files_deleted": 0, "dirs_removed": 0, "errors": 0}
    assert f.exists()


def test_expired_files_deleted_fresh_kept(tmp_path):
    expired = tmp_path / "alice" / "tool_outputs" / "conv1" / "old.json"
    fresh = tmp_path / "alice" / "tool_outputs" / "conv1" / "new.json"
    _touch(expired, age_days=45)
    _touch(fresh, age_days=2)

    stats = cleanup_expired(tmp_path, ttl_days=30)
    assert stats["files_deleted"] == 1
    assert not expired.exists()
    assert fresh.exists()


def test_cross_user_cross_conv(tmp_path):
    """每个 user/tool_outputs/{conv}/ 都被扫到。"""
    paths = [
        tmp_path / "alice" / "tool_outputs" / "c1" / "a.json",
        tmp_path / "alice" / "tool_outputs" / "c2" / "b.json",
        tmp_path / "bob" / "tool_outputs" / "c3" / "c.json",
    ]
    for p in paths:
        _touch(p, age_days=60)
    stats = cleanup_expired(tmp_path, ttl_days=30)
    assert stats["files_deleted"] == 3
    for p in paths:
        assert not p.exists()


def test_empty_conv_dirs_removed(tmp_path):
    """文件全部清掉后，空 conv 目录也被 rmdir（避免大量空目录残留）。"""
    f = tmp_path / "alice" / "tool_outputs" / "conv1" / "old.json"
    _touch(f, age_days=60)
    cleanup_expired(tmp_path, ttl_days=30)
    # conv1 目录被删
    assert not (tmp_path / "alice" / "tool_outputs" / "conv1").exists()


def test_non_empty_dirs_preserved(tmp_path):
    """未清空的目录保留——只删空目录。"""
    expired = tmp_path / "alice" / "tool_outputs" / "conv1" / "old.json"
    fresh = tmp_path / "alice" / "tool_outputs" / "conv1" / "new.json"
    _touch(expired, age_days=60)
    _touch(fresh, age_days=2)
    cleanup_expired(tmp_path, ttl_days=30)
    # conv1 目录因 fresh.json 还在，所以保留
    assert (tmp_path / "alice" / "tool_outputs" / "conv1").exists()


def test_other_files_under_user_dir_untouched(tmp_path):
    """user 目录下其它文件 / 子目录（如 skills/、artifacts/、agents.md）不被波及。"""
    skills_file = tmp_path / "alice" / "skills" / "my-skill" / "SKILL.md"
    skills_file.parent.mkdir(parents=True, exist_ok=True)
    skills_file.write_text("# my skill")
    # 让它看起来很老
    old_ts = time.time() - 365 * 86400
    os.utime(skills_file, (old_ts, old_ts))

    cleanup_expired(tmp_path, ttl_days=30)
    assert skills_file.exists(), "tool_outputs 之外的文件不能被清"


def test_missing_data_dir_safe(tmp_path):
    """data_dir 不存在时不崩。"""
    stats = cleanup_expired(tmp_path / "nope", ttl_days=30)
    assert stats == {"files_deleted": 0, "dirs_removed": 0, "errors": 0}
