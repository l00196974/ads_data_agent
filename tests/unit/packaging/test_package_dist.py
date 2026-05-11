"""build.package_for_distribution 单测——清理 + 校验 + zip 三个职责。

不真跑 PyInstaller / npm / 下载——构造一个最小 fake bundle 跑 packaging 即可。
"""
from __future__ import annotations

import importlib.util
import sys
import zipfile
from pathlib import Path

import pytest


@pytest.fixture
def build_mod(tmp_path, monkeypatch):
    """加载 build/build.py 并把 BUNDLE / DIST 重定向到 tmp。"""
    src = Path(__file__).resolve().parents[3] / "build" / "build.py"
    spec = importlib.util.spec_from_file_location("_build_under_test", src)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_build_under_test"] = mod
    spec.loader.exec_module(mod)

    fake_dist = tmp_path / "dist"
    fake_dist.mkdir()
    fake_bundle = fake_dist / "ads-agent"
    fake_bundle.mkdir()

    monkeypatch.setattr(mod, "DIST", fake_dist)
    monkeypatch.setattr(mod, "BUNDLE", fake_bundle)
    return mod, fake_dist, fake_bundle


def _make_minimal_bundle(bundle: Path) -> None:
    """造一个通过 package_for_distribution 校验所需的最小目录结构。"""
    (bundle / "ads-agent.exe").write_bytes(b"fake exe")
    (bundle / "frontend" / "dist").mkdir(parents=True)
    (bundle / "frontend" / "dist" / "index.html").write_text("<html></html>")
    (bundle / "vendor" / "tiktoken_cache").mkdir(parents=True)
    (bundle / "vendor" / "tiktoken_cache" / "fake_bpe").write_bytes(b"x")
    (bundle / "runtime" / "node").mkdir(parents=True)
    (bundle / "runtime" / "node" / "node.exe").write_bytes(b"fake")
    (bundle / "runtime" / "python").mkdir(parents=True)
    (bundle / "runtime" / "python" / "python.exe").write_bytes(b"fake")
    (bundle / "config.yaml").write_text("server:\n  port: 8000\n")
    (bundle / ".env.example").write_text("LLM_API_KEY=\n")
    (bundle / "README-INSTALL.txt").write_text("install...\n")


def test_preserves_bundle_but_excludes_env_and_data_from_zip(build_mod):
    """.env / data/ 必须不入 zip，但 BUNDLE 本地保留（用户的测试现场不受打扰）。"""
    mod, dist, bundle = build_mod
    _make_minimal_bundle(bundle)
    # 故意往里塞敏感文件 —— 模拟用户本地拷 .env 测试 exe 后又跑 build
    (bundle / ".env").write_text("LLM_API_KEY=secret-key-xyz")
    (bundle / "data").mkdir()
    (bundle / "data" / "checkpoints.db").write_bytes(b"sqlite")

    mod.package_for_distribution()

    # 1. BUNDLE 里的 .env / data/ **不该被删**——这是这次修复的核心保证
    assert (bundle / ".env").exists(), "本地 .env 被误删（破坏用户测试现场）"
    assert (bundle / "data").exists(), "本地 data/ 被误删"
    assert (bundle / ".env").read_text() == "LLM_API_KEY=secret-key-xyz", "本地 .env 内容应保留"

    # 2. zip 里**不应有** .env / data/
    zips = list(dist.glob("ads-agent-*.zip"))
    assert len(zips) == 1
    with zipfile.ZipFile(zips[0]) as zf:
        names = zf.namelist()
    assert "ads-agent/.env" not in names, "敏感 .env 进了 zip"
    assert not any(n.startswith("ads-agent/data/") for n in names), "运行时 data/ 进了 zip"
    # .env.example 必须保留——是给接收方的模板
    assert "ads-agent/.env.example" in names
    # 业务文件还在 zip 里
    assert "ads-agent/ads-agent.exe" in names
    assert "ads-agent/frontend/dist/index.html" in names


def test_raises_when_required_asset_missing(build_mod):
    """少了 frontend/dist 应当 raise——避免出无前端的废 bundle。"""
    mod, dist, bundle = build_mod
    _make_minimal_bundle(bundle)
    # 故意删 frontend
    import shutil
    shutil.rmtree(bundle / "frontend")

    with pytest.raises(RuntimeError) as exc_info:
        mod.package_for_distribution()
    assert "frontend/dist/index.html" in str(exc_info.value)


def test_creates_timestamped_zip_at_dist_root(build_mod):
    """zip 落在 dist/，含 ads-agent/ 子目录前缀（解压后是子目录而非散文件）。"""
    mod, dist, bundle = build_mod
    _make_minimal_bundle(bundle)

    mod.package_for_distribution()

    zips = list(dist.glob("ads-agent-*.zip"))
    assert len(zips) == 1, f"应只产 1 个 zip，实际：{zips}"
    zip_path = zips[0]
    assert zip_path.stem.startswith("ads-agent-")

    # 解压验证含 ads-agent/ 前缀
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert any(n.startswith("ads-agent/") for n in names), "zip 缺 ads-agent/ 子目录前缀"
    assert "ads-agent/ads-agent.exe" in names
    assert "ads-agent/frontend/dist/index.html" in names


def test_validates_all_required_paths(build_mod):
    """逐项删，每项都应触发对应错误信息。"""
    import shutil
    mod, dist, bundle = build_mod

    # 校验列表里每一项缺失都要被检测到
    critical_paths = [
        "frontend/dist/index.html",
        "vendor/tiktoken_cache",
        "runtime/node",
        "runtime/python",
    ]
    for missing_path in critical_paths:
        # 每次重建完整 bundle 再删一项
        if bundle.exists():
            shutil.rmtree(bundle)
        bundle.mkdir()
        _make_minimal_bundle(bundle)
        target = bundle / missing_path
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()

        with pytest.raises(RuntimeError) as exc_info:
            mod.package_for_distribution()
        assert missing_path in str(exc_info.value), \
            f"删除 {missing_path} 后报错信息没提到它：{exc_info.value}"
