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


def run_pyinstaller() -> None:
    log("running PyInstaller")
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
        "--collect-all", "deepagents",
        "--collect-all", "langgraph",
        "--collect-all", "langchain",
        "--collect-all", "langchain_openai",
        "--collect-all", "langchain_core",
        "--collect-all", "tiktoken",
        "--collect-all", "tiktoken_ext",
        "--collect-all", "fastapi",
        "--collect-all", "starlette",
        "--collect-all", "pydantic",
        "--collect-all", "uvicorn",
        # 业务源代码（agent / api / main.py）已经被 launcher.py 直接 import，
        # PyInstaller 会顺 import 链打包。prompts/ 是运行时按文件路径读的资源，
        # 必须 add-data 显式带进去。
        "--add-data", f"{ROOT / 'prompts'};prompts",
        # console=True：保留 stdout 窗口，便于调试；正式发布时可换 windowed
        "--console",
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
    """拷源代码 skills/ 到 bundle，但**排除** node_modules / .deps-pip / .deps_installed_hash。

    用户首次启动 launcher 时，skill_loader 检测到 hash marker 缺失会自动装依赖
    （走 bundle 自带的 Node + Python，零外部依赖）。带 node_modules 进 bundle 会让
    cold install 不被触发，无法验证 auto-install 路径，且 node_modules 通常 50MB+ 浪费空间。
    """
    src_root = ROOT / "skills"
    if not src_root.exists():
        log("no skills/ dir, skipping")
        return
    dst_root = BUNDLE / "skills"
    if dst_root.exists():
        shutil.rmtree(dst_root)

    def ignore(_d: str, names: list[str]) -> list[str]:
        # 不带任何 install 产物 / 缓存进 bundle
        skip = {"node_modules", ".deps-pip", ".deps_installed_hash",
                "__pycache__", ".pytest_cache", "package-lock.json"}
        return [n for n in names if n in skip]

    shutil.copytree(src_root, dst_root, ignore=ignore)
    log(f"skills copied (without lock/install artifacts): {dst_root}")


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
        "1. 复制 .env.example 为 .env，填入 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL\n"
        "2. 双击 ads-agent.exe 启动\n"
        "3. 浏览器自动打开 http://127.0.0.1:8000\n\n"
        "修改端口（任选其一，优先级从高到低）：\n"
        "  a. 环境变量 ADS_AGENT_PORT=xxxx（或写到 .env 里）\n"
        "  b. 编辑 config.yaml::server.port: xxxx\n"
        "host 同理：ADS_AGENT_HOST / config.yaml::server.host\n\n"
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
    args = parser.parse_args()

    DIST.mkdir(exist_ok=True)
    CACHE.mkdir(exist_ok=True, parents=True)

    if not args.skip_pyinstaller:
        run_pyinstaller()
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
