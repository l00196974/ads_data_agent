"""auto_approve 全局白名单：HitL 决策核心数据契约。

锁住三件事：
  1. add → contains 立即生效（in-memory cache）
  2. add → 持久化到 yaml（重新 init 后还在）
  3. remove 后 contains 返回 False
"""
import yaml

from agent import auto_approve


def test_init_creates_empty_when_no_file(tmp_path):
    auto_approve.init(tmp_path)
    assert auto_approve.list_all() == []


def test_add_then_contains(tmp_path):
    auto_approve.init(tmp_path)
    auto_approve.add("create-audience")
    assert auto_approve.contains("create-audience")
    assert not auto_approve.contains("other-tool")


def test_add_persists_to_disk(tmp_path):
    auto_approve.init(tmp_path)
    auto_approve.add("write-report-artifact")
    # 重新 init 模拟进程重启
    auto_approve.init(tmp_path)
    assert auto_approve.contains("write-report-artifact")


def test_add_idempotent(tmp_path):
    auto_approve.init(tmp_path)
    auto_approve.add("x")
    auto_approve.add("x")
    auto_approve.add("x")
    assert auto_approve.list_all() == ["x"]


def test_remove(tmp_path):
    auto_approve.init(tmp_path)
    auto_approve.add("a")
    auto_approve.add("b")
    auto_approve.remove("a")
    assert auto_approve.list_all() == ["b"]
    # 持久化也生效
    auto_approve.init(tmp_path)
    assert not auto_approve.contains("a")
    assert auto_approve.contains("b")


def test_contains_safe_when_not_initialized(tmp_path):
    """未 init 时 contains 必须返回 False，不能 raise——runner 决策路径上调，
    异常会让整个 agent run 崩溃。"""
    # 强制清掉 cache 模拟"未 init"
    auto_approve._cache = None
    assert auto_approve.contains("anything") is False


def test_contains_empty_string_returns_false(tmp_path):
    """tool_name 拿不到（空串）时不能误命中——runner 走"未知工具"安全路径。"""
    auto_approve.init(tmp_path)
    auto_approve.add("real-tool")
    assert not auto_approve.contains("")


def test_yaml_format_is_human_readable(tmp_path):
    """白名单文件人类可读且可手编（运维场景：手动加白名单不依赖前端）。"""
    auto_approve.init(tmp_path)
    auto_approve.add("create-audience")
    auto_approve.add("write-report-artifact")
    raw = (tmp_path / "auto_approve.yaml").read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    assert sorted(parsed) == ["create-audience", "write-report-artifact"]
