"""_open_browser_when_ready 轮询行为锁定。

老实现固定 time.sleep(1.5) 然后开浏览器——冷启动 warmup 期开早了拿到
connection refused。改成 TCP 探测后再开。

测试用 mock 替掉真实 TCP——避免 daemon 线程 / 端口复用引起的 flaky。
"""
from __future__ import annotations

import importlib.util
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def launcher_mod():
    src = Path(__file__).resolve().parents[3] / "build" / "launcher.py"
    spec = importlib.util.spec_from_file_location("_launcher_under_test_browser", src)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_launcher_under_test_browser"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_waits_until_port_listening_then_opens(launcher_mod):
    """前 N 次 connect 抛 ConnectionRefused，第 N+1 次成功——验证轮询 + 等就绪。"""
    open_called = threading.Event()
    connect_attempts = []
    success_after = 5  # 模拟 listener 在第 5 次探测时才起

    def fake_create_connection(addr, timeout):
        connect_attempts.append(addr)
        if len(connect_attempts) < success_after:
            raise ConnectionRefusedError("not yet")
        # 返回一个 context manager 兼容的假 socket
        class _FakeSock:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *a): pass
            def close(self_inner): pass
        return _FakeSock()

    with patch.object(launcher_mod.webbrowser, "open", side_effect=lambda u: open_called.set()):
        # 注意：launcher._open_browser_when_ready 是 `import socket as _sock` 在函数体内
        # 用 patch("socket.create_connection")（其顶级模块）来拦截
        with patch("socket.create_connection", side_effect=fake_create_connection):
            launcher_mod._open_browser_when_ready(
                "127.0.0.1", 12345, "http://127.0.0.1:12345/",
                max_wait=5.0, poll_interval=0.01,
            )
            # 给探测线程时间跑完 5 次重试
            assert open_called.wait(timeout=2.0), "listener 起来后浏览器没开"

    assert len(connect_attempts) >= success_after, f"只探测了 {len(connect_attempts)} 次"
    # 所有探测都打到正确的 host:port
    assert all(a == ("127.0.0.1", 12345) for a in connect_attempts)


def test_gives_up_after_max_wait(launcher_mod):
    """端口一直 refused，max_wait 后悄悄放弃——不开浏览器、不抛异常。"""
    open_called = threading.Event()

    def always_refused(*a, **kw):
        raise ConnectionRefusedError("never ready")

    with patch.object(launcher_mod.webbrowser, "open", side_effect=lambda u: open_called.set()):
        with patch("socket.create_connection", side_effect=always_refused):
            launcher_mod._open_browser_when_ready(
                "127.0.0.1", 12346, "http://127.0.0.1:12346/",
                max_wait=0.3, poll_interval=0.05,
            )
            # 等比 max_wait 长一点确保线程退出
            time.sleep(0.8)
            assert not open_called.is_set(), "max_wait 超时还开了浏览器——会拿 connection refused"


def test_zero_zero_zero_zero_host_probes_127(launcher_mod):
    """uvicorn 监听 0.0.0.0 时探测要用 127.0.0.1（不能 connect 0.0.0.0）。"""
    open_called = threading.Event()
    probed_hosts = []

    def fake_create_connection(addr, timeout):
        probed_hosts.append(addr[0])
        class _FakeSock:
            def __enter__(self_inner): return self_inner
            def __exit__(self_inner, *a): pass
        return _FakeSock()

    with patch.object(launcher_mod.webbrowser, "open", side_effect=lambda u: open_called.set()):
        with patch("socket.create_connection", side_effect=fake_create_connection):
            launcher_mod._open_browser_when_ready(
                "0.0.0.0", 12347, "http://127.0.0.1:12347/",
                max_wait=2.0, poll_interval=0.05,
            )
            assert open_called.wait(timeout=2.0)

    assert probed_hosts and probed_hosts[0] == "127.0.0.1", \
        f"host=0.0.0.0 应回落探测 127.0.0.1，实际：{probed_hosts}"
