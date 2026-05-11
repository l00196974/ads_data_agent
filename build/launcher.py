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


def _open_browser_when_ready(
    host: str,
    port: int,
    url: str,
    *,
    max_wait: float = 60.0,
    poll_interval: float = 0.3,
) -> None:
    """轮询 TCP 端口直到可连再开浏览器——避免冷启动 warmup 期开浏览器拿到
    'connection refused'。

    Why not fixed sleep：之前是 time.sleep(1.5)，假定 1.5s 内 uvicorn 起来。
    但首次启动还有 tiktoken BPE load + skill_loader（含 npm install）+
    ThreadStore SQLite WAL 初始化，可能 5-30s。固定 sleep 太短开早了、太长
    用户等急。

    Why max_wait=60：60s 仍连不上几乎一定是 uvicorn 自己崩了，让用户去看
    终端日志比无脑等强。

    ADS_AGENT_NO_BROWSER=1：完全关掉自动开浏览器。用例：默认浏览器（Chrome）
    本身有问题（如 chrome_elf.dll 损坏）导致 ShellExecute 弹错误框；用户想
    用别的浏览器手动访问。
    """
    no_browser = os.getenv("ADS_AGENT_NO_BROWSER", "").lower() in ("1", "true", "yes")
    # 醒目地把 URL 印到终端，无论是否自动开浏览器
    bar = "=" * 60
    print(f"\n{bar}\n  🚀 ads-agent 启动中... 浏览器地址:\n     {url}\n{bar}\n", flush=True)

    if no_browser:
        print("[info] ADS_AGENT_NO_BROWSER=1，跳过自动打开浏览器（请手动复制上面 URL）", flush=True)
        return

    import socket as _sock

    def _go():
        deadline = time.time() + max_wait
        # localhost 0.0.0.0 实际监听 — 探测时统一用 127.0.0.1
        probe_host = "127.0.0.1" if host in ("0.0.0.0", "") else host
        while time.time() < deadline:
            try:
                with _sock.create_connection((probe_host, port), timeout=0.5):
                    break  # 连上了，跳出循环
            except (OSError, ConnectionRefusedError):
                time.sleep(poll_interval)
        else:
            # while 跑完没 break = 超时，悄悄放弃开浏览器（用户能从终端日志看到状态）
            return
        try:
            webbrowser.open(url)
        except Exception as e:
            # webbrowser.open 通常不抛——chrome_elf.dll 损坏等系统层错误是
            # Chrome 进程自己弹框，我们这边接不到。但 Python 层异常仍兜底。
            print(f"[warn] 自动打开浏览器失败：{e}。请手动访问上面的 URL。", flush=True)

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
    _open_browser_when_ready(host, port, f"http://{browser_host}:{port}/")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
