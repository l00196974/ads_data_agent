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


def main() -> None:
    root = _bundle_root()
    os.chdir(root)
    _setup_runtime_path(root)

    # 此时 PATH / cwd 已就绪，再 import 业务模块（main.py 会读 config.yaml + .env）。
    # 直接 import app 对象（不是 "main:app" 字符串）—— PyInstaller 才能顺着 import 链
    # 把 main.py + 它依赖的所有模块打进 PYZ。
    import uvicorn
    from main import app

    host = os.getenv("ADS_AGENT_HOST", "127.0.0.1")
    port = int(os.getenv("ADS_AGENT_PORT", "8000"))
    _open_browser_when_ready(f"http://{host}:{port}/")

    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
