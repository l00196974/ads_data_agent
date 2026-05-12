"""build.py — 一键打包脚本。

流程：
  1. (跳过) 前端 vite build —— 由用户先手动跑（避免每次打包都重装 node_modules）
  2. PyInstaller --onedir 把 launcher.py + 所有 Python 依赖打成 dist/ads-agent/
  3. 把 frontend/dist/ 拷到 dist/ads-agent/frontend/
  4. 下载 + 解压 Node portable 到 dist/ads-agent/runtime/node/
  5. 下载 + 解压 Python embeddable 到 dist/ads-agent/runtime/python/
  6. 拷 vendor/tiktoken_cache 到 bundle（离线环境 tiktoken 必需）
  7. 拷 config.yaml + .env.example 到 bundle 根
  8. 写 README-INSTALL.txt 给最终用户
  9. **清敏感（.env / data/）+ 关键资产校验 + zip → dist/ads-agent-<时间戳>.zip**
     直接拿这个 zip 发给最终用户

用法：
  python build/build.py                    # 全量打包 + 出分发 zip
  python build/build.py --skip-runtimes    # 跳过 Node/Python 下载（已下载过）
  python build/build.py --skip-pyinstaller # 只更新 runtimes 不重打 PyInstaller
  python build/build.py --skip-package     # dev 迭代时跳过 zip（省 1-2min 压缩）

下载的 archive 缓存在 build/cache/ 下，不会重复下载。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = ROOT / "build"
CACHE = BUILD_DIR / "cache"
DIST = ROOT / "dist"
BUNDLE = DIST / "ads-agent"

# 锁定版本（保证 reproducible build；想升级改这里）
NODE_VERSION = "v22.11.0"
NODE_URL = f"https://nodejs.org/dist/{NODE_VERSION}/node-{NODE_VERSION}-win-x64.zip"
NODE_DIRNAME = f"node-{NODE_VERSION}-win-x64"

PYTHON_VERSION = "3.13.1"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip"


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def download_if_missing(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        log(f"cached: {dest.name}")
        return dest
    log(f"downloading {url}")
    with urllib.request.urlopen(url) as r, open(dest, "wb") as f:
        shutil.copyfileobj(r, f)
    log(f"saved: {dest.name} ({dest.stat().st_size // 1_000_000} MB)")
    return dest


def unzip_to(zip_path: Path, target: Path, strip_root: str | None = None) -> None:
    """解压 zip 到 target；strip_root 给定时去掉 zip 内的顶层目录前缀。"""
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        for member in z.infolist():
            name = member.filename
            if strip_root:
                if name == strip_root or name == strip_root + "/":
                    continue
                if name.startswith(strip_root + "/"):
                    name = name[len(strip_root) + 1:]
                else:
                    continue
            if not name:
                continue
            out = target / name
            if member.is_dir():
                out.mkdir(parents=True, exist_ok=True)
            else:
                out.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)


def run_pyinstaller(windowed: bool = False) -> None:
    log(f"running PyInstaller (mode={'windowed' if windowed else 'console'})")
    pyi = ROOT / ".venv" / "Scripts" / "pyinstaller.exe"
    if not pyi.exists():
        raise FileNotFoundError(f"pyinstaller not found at {pyi} — run pip install pyinstaller")

    # 清旧 build/dist
    for p in [DIST / "ads-agent", ROOT / "build" / "_pyi-build", ROOT / "build" / "_pyi-spec"]:
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)

    cmd = [
        str(pyi),
        "--noconfirm",
        "--onedir",
        "--name", "ads-agent",
        "--paths", str(ROOT),
        # PyInstaller 自动收集 import 链，但有些包用 importlib.metadata 找资源，要 collect-all
        "--collect-all", "tiktoken",
        "--collect-all", "webview",       # PyWebView 桌面窗口 + 平台后端（Windows: Edge WebView2）
        "--collect-all", "clr_loader",    # pythonnet 依赖 —— Windows 上 webview 走 .NET CLR
        "--collect-all", "tiktoken_ext",
        "--collect-all", "fastapi",
        "--collect-all", "starlette",
        "--collect-all", "pydantic",
        "--collect-all", "uvicorn",
        # 业务源代码（agent / api / main.py）已经被 launcher.py 直接 import，
        # PyInstaller 会顺 import 链打包。prompts/ 是运行时按文件路径读的资源，
        # 必须 add-data 显式带进去。
        "--add-data", f"{ROOT / 'prompts'};prompts",
        # --console: 双击 exe 时会弹黑色控制台窗口（调试模式，看 uvicorn 日志方便）
        # --windowed: 不弹黑窗，看起来是干净的 desktop app（生产模式）
        #             崩了找 data/logs/backend-*.log 看
        "--windowed" if windowed else "--console",
        "--distpath", str(DIST),
        "--workpath", str(BUILD_DIR / "_pyi-build"),
        "--specpath", str(BUILD_DIR / "_pyi-spec"),
        str(BUILD_DIR / "launcher.py"),
    ]
    subprocess.check_call(cmd)
    log("PyInstaller done")


def copy_frontend() -> None:
    src = ROOT / "frontend" / "dist"
    if not src.exists():
        # 早失败强约束——bundle 没前端等于双击启动后浏览器全 404，用户排查痛苦
        raise FileNotFoundError(
            f"前端构建产物缺失：{src}\n"
            "请先在 frontend/ 下跑 `npm run build`，或者用 --skip-frontend 显式跳过前端"
        )
    dst = BUNDLE / "frontend" / "dist"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    log(f"frontend copied: {dst}")


def setup_node_runtime() -> None:
    target = BUNDLE / "runtime" / "node"
    # 幂等：bundle 里已有 node.exe 就跳过——PyInstaller 每次重建 dist/ads-agent/，
    # 但同一次 build 里多个步骤可能都触发到这函数，重复解压浪费时间
    if (target / "node.exe").exists():
        log(f"node runtime 已就位，跳过解压: {target}")
        return
    archive = download_if_missing(NODE_URL, CACHE / Path(NODE_URL).name)
    if target.exists():
        shutil.rmtree(target)
    log(f"extracting node → {target}")
    unzip_to(archive, target, strip_root=NODE_DIRNAME)


def setup_python_runtime() -> None:
    target = BUNDLE / "runtime" / "python"
    if (target / "python.exe").exists():
        log(f"python runtime 已就位，跳过解压: {target}")
        return
    archive = download_if_missing(PYTHON_URL, CACHE / Path(PYTHON_URL).name)
    if target.exists():
        shutil.rmtree(target)
    log(f"extracting python → {target}")
    unzip_to(archive, target, strip_root=None)
    # python embeddable 默认禁用 site —— 想用 pip 必须手动启用。
    # 找 pythonXY._pth 文件，注释掉 #import site 那行
    for pth in target.glob("python*._pth"):
        text = pth.read_text(encoding="utf-8")
        new_text = text.replace("#import site", "import site")
        if new_text != text:
            pth.write_text(new_text, encoding="utf-8")
            log(f"enabled site in {pth.name}")
    # bootstrap pip
    get_pip = CACHE / "get-pip.py"
    if not get_pip.exists():
        log("downloading get-pip.py")
        urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", get_pip)
    py_exe = target / "python.exe"
    log("bootstrap pip into bundled python")
    subprocess.check_call([str(py_exe), str(get_pip), "--no-warn-script-location"])


def copy_skills() -> None:
    """拷源代码 skills/ 到 bundle，**保留** node_modules / .deps-pip / .deps_installed_hash。

    以前故意排除这些产物想让接收方"首次启动按需自动装"，但实际效果是接收方一启动
    就连 registry.npmjs.org / pypi.org 拉包（命中企业防火墙 443 告警 + 离线环境
    直接挂）。改成"打包时把 hash marker + install 产物一起 ship"，运行期
    `_ensure_skill_deps` 看 hash 匹配直接跳过 install，零外网连接。

    代价是 bundle 大几十 MB（node_modules 通常 30-50MB/skill）；收益是接收方
    真正离线启动 + 没首次启动外网告警。

    前提：打包机本地已经跑过 skill 一次（node_modules 已就位），否则 bundle 出
    去依然空 deps，接收方还是要联网。CI / 自动化打包脚本应该先跑一遍
    `npm install` 把源码 skills 装好再 build。
    """
    src_root = ROOT / "skills"
    if not src_root.exists():
        log("no skills/ dir, skipping")
        return
    dst_root = BUNDLE / "skills"
    if dst_root.exists():
        shutil.rmtree(dst_root)

    def ignore(_d: str, names: list[str]) -> list[str]:
        # 排除：纯缓存（__pycache__）、测试产物（.pytest_cache）；
        # 保留：node_modules / .deps-pip / .deps_installed_hash（这些是 deps 已装的证据）
        # package-lock.json 也保留——npm install 时 lock 在能加快解析
        skip = {"__pycache__", ".pytest_cache"}
        return [n for n in names if n in skip]

    shutil.copytree(src_root, dst_root, ignore=ignore)

    # 给用户一个"deps 已就位"的可视化反馈
    has_deps = 0
    for sub in dst_root.iterdir():
        if (sub / ".deps_installed_hash").exists():
            has_deps += 1
    log(f"skills copied (with install artifacts): {dst_root} ({has_deps} 个 skill 含 hash marker)")


def copy_tiktoken_cache() -> None:
    """拷 vendor/tiktoken_cache/ 到 bundle 根——内网 / 离线环境 tiktoken 不
    用再从 openaipublic.blob.core.windows.net 下 BPE 文件。launcher.py 启动
    时把 TIKTOKEN_CACHE_DIR 指向这里。
    """
    src = ROOT / "vendor" / "tiktoken_cache"
    if not src.exists():
        log("WARN: vendor/tiktoken_cache 不存在——离线环境 tiktoken 会尝试联网下载")
        return
    dst = BUNDLE / "vendor" / "tiktoken_cache"
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)
    log(f"tiktoken BPE cache copied: {dst}")


def package_for_distribution() -> None:
    """打包 bundle 成 zip，给最终用户直接下发。

    设计：**不修改 BUNDLE 本身**——本地反复 build + 测试 exe 时，用户往往
    把自己的 .env 放进 dist/ads-agent/ 跑测试。如果 package 步骤删了 .env，
    下次再 build 就要重新拷一次。改成"zip 时按名跳过敏感目录"：BUNDLE 始终
    保留用户的本地测试文件，zip 里始终干净。

    分 2 步：
    1. **关键资产校验**——bundle 里少了 frontend/dist / tiktoken_cache /
       runtime/node 等任意一个，接收方启动都会"看起来正常但浏览器 404 /
       离线报错 / skill 跑不起来"。早 fail 比晚踩坑好。
    2. **zip 压缩，按名跳过敏感顶层路径**：`.env`（含密钥）+ `data/`（会话
       历史）不入 zip。bundle 目录里这俩保留不变。
    """
    import zipfile
    from datetime import datetime

    # 1. 关键资产校验
    required = {
        "ads-agent.exe": "启动入口",
        "frontend/dist/index.html": "前端构建产物（缺会浏览器 404）",
        "vendor/tiktoken_cache": "tiktoken BPE 缓存（缺离线环境启动报 openaipublic 网络错）",
        "runtime/node": "Node 运行时（skill 跑 npm 用）",
        "runtime/python": "Python 运行时（skill 跑 python 用）",
        "config.yaml": "运行时配置",
        ".env.example": "给接收方的模板",
        "README-INSTALL.txt": "接收方安装说明",
    }
    missing = [
        f"  - {path}  ({label})"
        for path, label in required.items()
        if not (BUNDLE / path).exists()
    ]
    if missing:
        raise RuntimeError(
            "bundle 资产校验失败——接收方会踩坑：\n"
            + "\n".join(missing)
            + "\n显式跳过对应 build 步骤的话，用 --skip-package 一并跳过打包"
        )

    # 2. 压缩——按顶层名跳过敏感路径
    excluded_top = {".env", "data"}
    stamp = datetime.now().strftime("%Y-%m-%d-%H%M")
    zip_path = DIST / f"ads-agent-{stamp}.zip"
    if zip_path.exists():
        zip_path.unlink()
    log(f"compressing → {zip_path.name}（这步要 1-2 分钟，跳过 {excluded_top}）")

    skipped_paths: list[str] = []
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in BUNDLE.rglob("*"):
            if not f.is_file():
                continue
            rel_in_bundle = f.relative_to(BUNDLE)
            top = rel_in_bundle.parts[0]
            if top in excluded_top:
                skipped_paths.append(str(rel_in_bundle))
                continue
            # arcname 含一层 ads-agent/ 前缀，解压后是 ads-agent/ 子目录而非散文件
            zf.write(f, arcname=f.relative_to(DIST))

    if skipped_paths:
        log(f"  跳过 {len(skipped_paths)} 个本地文件（未入 zip）：{skipped_paths[:3]}{'...' if len(skipped_paths) > 3 else ''}")
    size_mb = zip_path.stat().st_size // 1_000_000
    log(f"distribution package ready: {zip_path} ({size_mb} MB)")


def copy_static_files() -> None:
    for fname in ["config.yaml", ".env.example"]:
        src = ROOT / fname
        if not src.exists():
            log(f"missing: {fname}")
            continue
        shutil.copy2(src, BUNDLE / fname)
    # 写 README
    (BUNDLE / "README-INSTALL.txt").write_text(
        "华为广告数据助手 - 离线安装包\n"
        "=" * 40 + "\n\n"
        "⚠️ 重要：必须先把 zip 解压到本地目录再运行！\n"
        "   直接在 zip 里双击 ads-agent.exe 会报 'python313.dll 找不到'——\n"
        "   Windows 只单独解压了 exe，旁边的 _internal/ runtime/ 没出来。\n\n"
        "操作步骤：\n\n"
        "0. **先解压**：右键 zip → 解压全部 → 选目标目录（如 D:\\ads-agent\\）\n"
        "1. 进入解压后的目录\n"
        "2. 复制 .env.example 为 .env，填入 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL\n"
        "3. 双击 ads-agent.exe 启动\n"
        "4. 自动弹出应用窗口（用 Edge WebView2 内核，不依赖 Chrome）\n\n"
        "启动模式三选一（默认走 desktop 窗口）：\n"
        "  - 默认                   : 弹应用窗口（推荐）\n"
        "  - ADS_AGENT_NO_WEBVIEW=1 : 改用系统默认浏览器打开\n"
        "  - ADS_AGENT_NO_BROWSER=1 : 纯后端，手动用浏览器访问终端打印的 URL\n\n"
        "修改端口（任选其一，优先级从高到低）：\n"
        "  a. 环境变量 ADS_AGENT_PORT=xxxx（或写到 .env 里）\n"
        "  b. 编辑 config.yaml::server.port: xxxx\n"
        "host 同理：ADS_AGENT_HOST / config.yaml::server.host\n\n"
        "WebView2 没装（少数老 Win10）：\n"
        "  访问 https://developer.microsoft.com/microsoft-edge/webview2/\n"
        "  下载 Evergreen Bootstrapper 安装一次即可（~5MB）。\n"
        "  没装时本程序会自动回落到系统默认浏览器模式。\n\n"
        "数据目录：./data/（首次运行自动创建）\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-pyinstaller", action="store_true")
    parser.add_argument("--skip-runtimes", action="store_true")
    parser.add_argument("--skip-frontend", action="store_true")
    parser.add_argument(
        "--skip-package", action="store_true",
        help="不生成最终的 .zip 分发包（dev 迭代时用，省 1-2 分钟压缩）",
    )
    parser.add_argument(
        "--windowed", action="store_true",
        help="生产模式：双击 exe 不弹黑色控制台，干净的 desktop app 形态（"
             "崩了从 data/logs/backend-*.log 看日志）",
    )
    args = parser.parse_args()

    DIST.mkdir(exist_ok=True)
    CACHE.mkdir(exist_ok=True, parents=True)

    if not args.skip_pyinstaller:
        run_pyinstaller(windowed=args.windowed)
    else:
        log("skip PyInstaller")

    if not args.skip_frontend:
        copy_frontend()

    if not args.skip_runtimes:
        setup_node_runtime()
        setup_python_runtime()
    else:
        # --skip-runtimes 不能真盲跳——PyInstaller 每次 wipe dist/ads-agent/，
        # bundle 可能里面根本没 runtime；那就自动恢复别让用户撞 package 校验失败
        node_missing = not (BUNDLE / "runtime" / "node" / "node.exe").exists()
        py_missing = not (BUNDLE / "runtime" / "python" / "python.exe").exists()
        if node_missing or py_missing:
            log("--skip-runtimes 指定但 runtime 缺失（PyInstaller 重建过 bundle）——自动恢复")
            if node_missing:
                setup_node_runtime()
            if py_missing:
                setup_python_runtime()
        else:
            log("skip runtimes（已就位）")

    copy_skills()
    copy_tiktoken_cache()
    copy_static_files()
    log(f"bundle ready: {BUNDLE}")
    log(f"size: {sum(f.stat().st_size for f in BUNDLE.rglob('*') if f.is_file()) // 1_000_000} MB")

    if not args.skip_package:
        package_for_distribution()
    else:
        log("skip package (用 zip + 校验)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
