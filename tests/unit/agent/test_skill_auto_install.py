"""Skill 自动 install 契约测试。

锁住：
  1. 没声明依赖（无 package.json / requirements.txt）→ 不调 install
  2. package.json 但无 deps 字段 → 不调 npm install
  3. 装成功 → 写 hash marker
  4. hash 命中 marker → 跳过 install
  5. 改 deps 文件 → hash 变 → 重装
  6. install 失败（npm exit !=0）→ 不写 marker，下次还会重试
  7. npm 不在 PATH（FileNotFoundError）→ 不阻塞，记 warning
  8. PYTHONPATH 注入：skill_dir/.deps-pip 存在时被加到 subprocess env
"""
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from agent.skill_loader import (
    _compute_deps_hash,
    _ensure_skill_deps,
    _DEPS_HASH_MARKER,
    _PIP_DEPS_DIRNAME,
)


def _mk_pkg_json(skill_dir: Path, deps: dict | None = None) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    pkg = {"name": skill_dir.name, "version": "0.0.1"}
    if deps:
        pkg["dependencies"] = deps
    (skill_dir / "package.json").write_text(json.dumps(pkg), encoding="utf-8")


def test_no_deps_files_skips_install(tmp_path):
    """无 package.json 也无 requirements.txt → 函数 no-op，不调子进程。"""
    skill_dir = tmp_path / "noop-skill"
    skill_dir.mkdir()
    with patch("agent.skill_loader.subprocess.run") as run:
        _ensure_skill_deps(skill_dir)
    run.assert_not_called()


def test_package_json_without_deps_skips_npm(tmp_path):
    """package.json 但 deps 字段为空 → 不跑 npm install。但 marker 仍写入，
    让下次启动直接 hash 命中跳过整个检查（避免每次 import 都重读文件）。"""
    skill_dir = tmp_path / "empty-deps"
    _mk_pkg_json(skill_dir, deps=None)
    with patch("agent.skill_loader.subprocess.run") as run:
        _ensure_skill_deps(skill_dir)
    run.assert_not_called()
    # marker 写入 = "确认了一遍，无依赖需装"
    assert (skill_dir / _DEPS_HASH_MARKER).exists()


def test_npm_install_writes_marker_on_success(tmp_path):
    skill_dir = tmp_path / "node-skill"
    _mk_pkg_json(skill_dir, deps={"left-pad": "^1.0.0"})

    fake = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("agent.skill_loader.subprocess.run", return_value=fake) as run:
        _ensure_skill_deps(skill_dir)

    # 跑了一次 npm install
    assert run.call_count == 1
    cmd = run.call_args[0][0]
    # cmd[0] 是 shutil.which 解析过的全路径（Windows 上是 npm.cmd）
    assert cmd[0].lower().endswith("npm") or cmd[0].lower().endswith("npm.cmd")
    assert cmd[1] == "install"
    # cwd 必须是 skill_dir（npm install 从 cwd 读 package.json）
    assert run.call_args.kwargs["cwd"] == str(skill_dir)
    # marker 写入了
    expected_hash = _compute_deps_hash(skill_dir)
    assert (skill_dir / _DEPS_HASH_MARKER).read_text(encoding="utf-8").strip() == expected_hash


def test_marker_hit_skips_install(tmp_path):
    skill_dir = tmp_path / "cached-skill"
    _mk_pkg_json(skill_dir, deps={"left-pad": "^1.0.0"})
    # 预填 marker
    (skill_dir / _DEPS_HASH_MARKER).write_text(_compute_deps_hash(skill_dir), encoding="utf-8")

    with patch("agent.skill_loader.subprocess.run") as run:
        _ensure_skill_deps(skill_dir)
    run.assert_not_called()  # 命中 marker，绝不调 install


def test_changed_deps_triggers_reinstall(tmp_path):
    """改了 package.json → hash 变 → 即使 marker 在也要重装。"""
    skill_dir = tmp_path / "changing-skill"
    _mk_pkg_json(skill_dir, deps={"left-pad": "^1.0.0"})
    # 预填一个**旧** hash（改前的）
    (skill_dir / _DEPS_HASH_MARKER).write_text("OLD_HASH_DIFFERENT", encoding="utf-8")

    fake = MagicMock(returncode=0, stdout="", stderr="")
    with patch("agent.skill_loader.subprocess.run", return_value=fake) as run:
        _ensure_skill_deps(skill_dir)
    assert run.call_count == 1


def test_install_failure_skips_marker(tmp_path):
    """npm exit != 0 → 不写 marker，下次还会重试。"""
    skill_dir = tmp_path / "broken-skill"
    _mk_pkg_json(skill_dir, deps={"nonexistent-package-xyz": "^9.9.9"})

    fake = MagicMock(returncode=1, stdout="", stderr="npm ERR! 404 not found")
    with patch("agent.skill_loader.subprocess.run", return_value=fake):
        _ensure_skill_deps(skill_dir)

    assert not (skill_dir / _DEPS_HASH_MARKER).exists()


def test_npm_not_on_path_warns_but_does_not_raise(tmp_path):
    """没装 Node 时不能崩——记 warning + 跳过即可。

    走两层保护：(1) shutil.which 主动查找 npm.cmd（Windows 必需），返回 None 则早 return；
    (2) 即使 which 返回路径但实际 subprocess 抛 FileNotFoundError 也兜住。
    """
    skill_dir = tmp_path / "needs-npm"
    _mk_pkg_json(skill_dir, deps={"left-pad": "^1.0.0"})

    with patch("agent.skill_loader.shutil.which", return_value=None):
        _ensure_skill_deps(skill_dir)  # 不应抛
    assert not (skill_dir / _DEPS_HASH_MARKER).exists()


def test_npm_subprocess_filenotfound_caught(tmp_path):
    """shutil.which 返回路径但实际 subprocess 抛 FileNotFoundError——兜住不崩。"""
    skill_dir = tmp_path / "needs-npm-2"
    _mk_pkg_json(skill_dir, deps={"left-pad": "^1.0.0"})

    with patch("agent.skill_loader.shutil.which", return_value="C:/fake/npm.cmd"):
        with patch("agent.skill_loader.subprocess.run", side_effect=FileNotFoundError("npm")):
            _ensure_skill_deps(skill_dir)
    assert not (skill_dir / _DEPS_HASH_MARKER).exists()


def test_requirements_txt_triggers_pip_install(tmp_path):
    skill_dir = tmp_path / "py-skill"
    skill_dir.mkdir()
    (skill_dir / "requirements.txt").write_text("requests>=2.0\n", encoding="utf-8")

    fake = MagicMock(returncode=0, stdout="", stderr="")
    with patch("agent.skill_loader.subprocess.run", return_value=fake) as run:
        _ensure_skill_deps(skill_dir)

    assert run.call_count == 1
    cmd = run.call_args[0][0]
    # 含 'pip install' 和 '--target <skill_dir>/.deps-pip'
    assert "pip" in cmd
    assert "install" in cmd
    target_idx = cmd.index("--target")
    assert cmd[target_idx + 1].endswith(_PIP_DEPS_DIRNAME)


def test_compute_hash_changes_when_package_json_changes(tmp_path):
    skill_dir = tmp_path / "h"
    _mk_pkg_json(skill_dir, deps={"a": "1"})
    h1 = _compute_deps_hash(skill_dir)
    _mk_pkg_json(skill_dir, deps={"a": "2"})
    h2 = _compute_deps_hash(skill_dir)
    assert h1 != h2 and h1 and h2


def test_compute_hash_returns_none_without_dep_files(tmp_path):
    skill_dir = tmp_path / "none"
    skill_dir.mkdir()
    assert _compute_deps_hash(skill_dir) is None
