"""launcher._resolve_host_port 优先级测试：env > config.yaml > 默认值。

打包出去的 exe 端口配置经常被踩坑——这组测试锁定行为，避免回退到老的"只看 env"。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


@pytest.fixture
def launcher_mod():
    """直接从源文件 load launcher 模块——build/ 不是 Python 包，没 __init__.py。"""
    src = Path(__file__).resolve().parents[3] / "build" / "launcher.py"
    spec = importlib.util.spec_from_file_location("_launcher_under_test", src)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_launcher_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_defaults_when_no_env_no_config(launcher_mod, tmp_path, monkeypatch):
    """无 env + 无 config.yaml → 127.0.0.1:8000"""
    monkeypatch.delenv("ADS_AGENT_HOST", raising=False)
    monkeypatch.delenv("ADS_AGENT_PORT", raising=False)
    host, browser_host, port = launcher_mod._resolve_host_port(tmp_path)
    assert host == "127.0.0.1"
    assert browser_host == "127.0.0.1"
    assert port == 8000


def test_config_yaml_takes_effect(launcher_mod, tmp_path, monkeypatch):
    """无 env，有 config.yaml → 走 yaml 值（这次修的核心 case）"""
    monkeypatch.delenv("ADS_AGENT_HOST", raising=False)
    monkeypatch.delenv("ADS_AGENT_PORT", raising=False)
    (tmp_path / "config.yaml").write_text(
        "server:\n  host: 0.0.0.0\n  port: 8001\n",
        encoding="utf-8",
    )
    host, browser_host, port = launcher_mod._resolve_host_port(tmp_path)
    assert host == "0.0.0.0"
    assert browser_host == "127.0.0.1"  # 0.0.0.0 浏览器打不开，自动改 127
    assert port == 8001


def test_env_overrides_config(launcher_mod, tmp_path, monkeypatch):
    """env 同时设了，env 赢——优先级最高"""
    monkeypatch.setenv("ADS_AGENT_PORT", "9000")
    monkeypatch.delenv("ADS_AGENT_HOST", raising=False)
    (tmp_path / "config.yaml").write_text(
        "server:\n  port: 8001\n",
        encoding="utf-8",
    )
    host, browser_host, port = launcher_mod._resolve_host_port(tmp_path)
    assert port == 9000


def test_broken_yaml_falls_back_silently(launcher_mod, tmp_path, monkeypatch):
    """yaml 语法坏了别让进程崩——回落到默认"""
    monkeypatch.delenv("ADS_AGENT_HOST", raising=False)
    monkeypatch.delenv("ADS_AGENT_PORT", raising=False)
    (tmp_path / "config.yaml").write_text(
        "server:\n  port: [this is not valid yaml int}\n",
        encoding="utf-8",
    )
    host, browser_host, port = launcher_mod._resolve_host_port(tmp_path)
    assert port == 8000  # 不爆，回默认


def test_partial_yaml_only_port(launcher_mod, tmp_path, monkeypatch):
    """yaml 只设了 port，host 还是默认"""
    monkeypatch.delenv("ADS_AGENT_HOST", raising=False)
    monkeypatch.delenv("ADS_AGENT_PORT", raising=False)
    (tmp_path / "config.yaml").write_text(
        "server:\n  port: 7777\n",
        encoding="utf-8",
    )
    host, browser_host, port = launcher_mod._resolve_host_port(tmp_path)
    assert host == "127.0.0.1"
    assert port == 7777
