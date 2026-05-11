"""launcher.py — PyInstaller 打包入口。

职责：
  1. 把 bundle 自带的 runtime/node/、runtime/python/ 路径加到 PATH 最前面，
     这样 subprocess.Popen 启动 SKILL.md::package.json::bin 脚本时能找到自带的解释器。
  2. 切到 bundle 目录（_MEIPASS 在 onedir 模式下是 bundle 根，跟 .exe 同级）。
  3. 起 uvicorn，挂 main:app。
  4. 自动开浏览器到 localhost:8000。

为什么要单独搞个 launcher.py 而不是直接打 main.py：
  - main.py 在 import 阶段就读 .env / config.yaml，路径和 PATH 没准备好就崩；
  - launcher 先做 setup 再 import，把启动顺序锁死。
"""
from __future__ import annotations

import os
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _bundle_root() -> Path:
    """PyInstaller onedir：launcher.exe 同级目录。开发态：项目根。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def _setup_runtime_path(root: Path) -> None:
    """把 bundle 自带的 runtime 加到 PATH 最前面。

    Node：runtime/node/ 下直接是 node.exe / npm.cmd（解压自 nodejs.org dist zip 后的根目录）。
    Python：runtime/python/ 下是 python.exe（解压自 python embeddable）。
    """
    paths_to_prepend = []
    node_dir = root / "runtime" / "node"
    py_dir = root / "runtime" / "python"
    if node_dir.exists():
        paths_to_prepend.append(str(node_dir))
        # npm 在 npm.cmd 同级，无需额外路径
    if py_dir.exists():
        paths_to_prepend.append(str(py_dir))
        # pip 走 python -m pip，不需要 Scripts/
    if paths_to_prepend:
        os.environ["PATH"] = os.pathsep.join(paths_to_prepend + [os.environ.get("PATH", "")])


def _open_browser_when_ready(url: str, delay: float = 1.5) -> None:
    """uvicorn 启动需要 1-2s，等一下再开浏览器避免 'connection refused'。"""
    def _go():
        time.sleep(delay)
        try:
            webbrowser.open(url)
        except Exception:
            pass
    threading.Thread(target=_go, daemon=True).start()


def _resolve_host_port(root: Path) -> tuple[str, int]:
    """端口/host 解析优先级：环境变量 > config.yaml > hardcoded 默认。

    `config.yaml::server.{host,port}` 之前是装饰字段——launcher 只看 ENV——这里
    补上回落读取，让用户在 bundle 根目录改 yaml 也能生效（不用每次都开 .env）。
    """
    # 默认值
    default_host = "127.0.0.1"
    default_port = 8000

    # config.yaml 回落（如果存在且可解析）
    cfg_host: str | None = None
    cfg_port: int | None = None
    cfg_path = root / "config.yaml"
    if cfg_path.exists():
        try:
            import yaml  # PyInstaller 已经 collect-all pydantic 间接拉进来
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
            server = raw.get("server") or {}
            cfg_host = server.get("host")
            cfg_port = server.get("port")
        except Exception:
            # yaml 坏了别让进程崩——退回 ENV / 默认即可
            pass

    host = os.getenv("ADS_AGENT_HOST") or cfg_host or default_host
    # host=0.0.0.0 浏览器打不开，自动改成 127.0.0.1 用于 _open_browser
    if host == "0.0.0.0":
        browser_host = "127.0.0.1"
    else:
        browser_host = host

    port_env = os.getenv("ADS_AGENT_PORT")
    if port_env:
        port = int(port_env)
    elif cfg_port:
        port = int(cfg_port)
    else:
        port = default_port

    return host, browser_host, port


def _setup_tiktoken_cache(root: Path) -> None:
    """指向 bundle 自带的 BPE 缓存。

    main.py 也 setdefault 了，但它走 `Path(__file__).parent` —— 在 PyInstaller
    onedir 模式下 __file__ 落到 _internal/ 里，vendor/ 不在那儿，路径会错。这里
    先于 main 的 setdefault 把环境变量定死，main.py 的 setdefault 就成了 noop。
    """
    cache_dir = root / "vendor" / "tiktoken_cache"
    if cache_dir.exists():
        os.environ["TIKTOKEN_CACHE_DIR"] = str(cache_dir)


def main() -> None:
    root = _bundle_root()
    os.chdir(root)
    _setup_runtime_path(root)
    _setup_tiktoken_cache(root)

    # 此时 PATH / cwd / TIKTOKEN_CACHE_DIR 都就绪，再 import 业务模块（main.py
    # 会读 config.yaml + .env，且首次 import 链可能触发 tiktoken load）。
    # 直接 import app 对象（不是 "main:app" 字符串）—— PyInstaller 才能顺着 import 链
    # 把 main.py + 它依赖的所有模块打进 PYZ。
    import uvicorn
    from main import app

    host, browser_host, port = _resolve_host_port(root)
    _open_browser_when_ready(f"http://{browser_host}:{port}/")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
