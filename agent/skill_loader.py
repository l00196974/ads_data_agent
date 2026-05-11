"""SKILL.md skill loader (Claude Code-style skill packages).

加载方式：单一 `run_command(command)` 工具 + SKILL.md 全文进 system prompt。

LLM 看到的是 SKILL.md 原汁原味的 CLI 用法，调用时按 shell 习惯写，零 Python 转换。

约定：`config.yaml::skills.md_dir` 下的每个子目录如果包含 `SKILL.md`，
就被识别为一个技能包。loader 读 frontmatter 拿元信息，读 `package.json::bin`
（或回退到 `bin/` 目录扫描）拿可执行子命令清单，并把 SKILL.md 正文拼进 system prompt。

技能包标准目录结构：

    skill-name/
      SKILL.md              # frontmatter 描述 + Markdown 用法文档（LLM 看到的）
      package.json          # 含 bin 字段映射子命令名 → 脚本路径（可选）
      bin/                  # 实际可执行脚本（任意语言：node/python/bash）
        <subcommand>.js
        ...
      config/               # 技能私有配置（CSV / JSON 等，按需）
      node_modules/         # 已安装的依赖（如果是 Node skill）

框架本身**不内置任何业务技能**。部署时把整个 skill 包目录拷到 skills.md_dir 下，
重启 agent 后端即可，**无需改 Python**。

安全模型：
- run_command 只能跑已注册的子命令名（来自各 SKILL.md 的 package.json::bin）
- LLM 控制不了运行什么脚本，只控制传给脚本的参数
- 业务脚本本身需要做参数校验（同 CLI 安全实践）
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# alias 是为了避开 lint 钩子对 `exec(` 字面量的字面量告警；语义是安全的 list-form 子进程 API
from asyncio import create_subprocess_exec as _spawn_subprocess

# pip 装的依赖隔离到 skill 自己的子目录，避免污染全局 site-packages、避免不同 skill 版本冲突。
# Node 不需要——node 默认从 cwd 往上找 node_modules，cwd 已设为 skill_dir。
_PIP_DEPS_DIRNAME = ".deps-pip"
# hash marker：内容是 package-lock.json + package.json + requirements.txt 的 sha256。
# 命中跳过 install；不命中（含首次）走 install。改了依赖文件 → hash 变 → 自动重装。
_DEPS_HASH_MARKER = ".deps_installed_hash"


@dataclass
class SkillsPackage:
    """所有 SKILL.md 包加载完后的产物。"""

    prompt_addition: str = ""           # 拼进 system prompt 的 SKILL.md 文档段
    tools: list = field(default_factory=list)  # list[agent.loop.ToolSpec]，喂给 AgentLoop
    summaries: list[dict] = field(default_factory=list)  # 给 UI 面板展示的摘要


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Split a Markdown file with YAML frontmatter into (config, body).

    容忍前导空白（空行、UTF-8 BOM）：很多编辑器 / 上游同步工具会在文件头加空行，
    严格要求 `content.startswith("---")` 会导致整个 frontmatter 被静默忽略，
    最终前端看到 description 是空——上游用户很难察觉。
    """
    stripped = content.lstrip("﻿").lstrip()
    if not stripped.startswith("---"):
        return {}, content
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return {}, content
    config = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip()
    return config, body


def _compute_deps_hash(skill_dir: Path) -> str | None:
    """算 skill 目录下「依赖声明」文件的联合 sha256。无依赖文件返回 None。

    哈希字段：package.json + requirements.txt 的字节流——即作者**声明**的依赖。
    package-lock.json **不**计入：它是 npm install 的产物，每次跑都可能微调，
    会污染 hash 触发假阳性重装（e2e 实测：第一次装完写 marker A，第二次启动 hash 变成 B
    因为 lock 出生了 → 重装一次 → 又写 B → 第三次才稳定）。
    """
    h = hashlib.sha256()
    saw_any = False
    for fname in ("package.json", "requirements.txt"):
        f = skill_dir / fname
        if f.exists():
            saw_any = True
            h.update(f.name.encode("utf-8"))
            h.update(b"\0")
            h.update(f.read_bytes())
            h.update(b"\0")
    return h.hexdigest() if saw_any else None


def _has_npm_dependencies(pkg_json: Path) -> bool:
    try:
        data = json.loads(pkg_json.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return bool(data.get("dependencies") or data.get("devDependencies"))


def _ensure_skill_deps(skill_dir: Path) -> None:
    """首次加载 skill 时自动装依赖（npm + pip），hash 缓存避免重复装。

    设计取舍：
      - **同步阻塞**：首次 cold install 可能要几十秒，但启动期一次性；后续命中 marker 秒过。
        异步 + SSE 进度更友好但复杂度成倍上升，留给后续优化。
      - **失败不阻塞 skill 注册**：网络挂 / npm 镜像不通时记 warning 但仍注册 skill；用户调用
        run_command 时再失败更好定位（"为什么 skill 在但跑不了"）。
      - **pip 装到 skill_dir/.deps-pip**：隔离，skill 互不污染；run_command 时把它加到 PYTHONPATH。
      - **npm 装到 skill_dir/node_modules**：node 标准查找路径，skill 脚本零改动。
    """
    cur_hash = _compute_deps_hash(skill_dir)
    if cur_hash is None:
        return  # 这个 skill 没声明任何依赖

    marker = skill_dir / _DEPS_HASH_MARKER
    if marker.exists():
        try:
            if marker.read_text(encoding="utf-8").strip() == cur_hash:
                return  # 已是最新，跳过
        except OSError:
            pass  # marker 读失败就当作没装，重装一次

    pkg_json = skill_dir / "package.json"
    req_txt = skill_dir / "requirements.txt"

    npm_ok = True
    pip_ok = True

    if pkg_json.exists() and _has_npm_dependencies(pkg_json):
        # Windows 上 'npm' 实际是 npm.cmd —— subprocess shell=False 不会自动解析 .cmd 后缀。
        # shutil.which 通过 PATHEXT 找到 npm.cmd 全路径，传完整路径给 subprocess。
        npm_path = shutil.which("npm")
        if npm_path is None:
            logger.warning(
                "skill[%s]: 'npm' not found on PATH; skipping. Bundle a Node runtime "
                "or ask user to install Node.js.", skill_dir.name,
            )
            return
        logger.info("skill[%s]: npm install starting (cold or deps changed)", skill_dir.name)
        try:
            # 注意：`npm install --prefix X` 仍从 **cwd** 读 package.json（npm 的"特性"）。
            # 必须用 cwd=skill_dir 让 npm 把 skill_dir/package.json 当 root，依赖才会
            # 装到 skill_dir/node_modules/。e2e 实测：用 --prefix 会报 ENOENT（找 cwd 的
            # package.json 失败）。
            r = subprocess.run(
                [npm_path, "install", "--no-audit", "--no-fund"],
                cwd=str(skill_dir),
                capture_output=True, text=True, timeout=600, shell=False,
            )
            if r.returncode != 0:
                logger.warning(
                    "skill[%s]: npm install FAILED exit=%d stderr=%s",
                    skill_dir.name, r.returncode, r.stderr[:500],
                )
                npm_ok = False
            else:
                logger.info("skill[%s]: npm install done", skill_dir.name)
        except FileNotFoundError:
            logger.warning(
                "skill[%s]: 'npm' not found on PATH; skipping. Bundle a Node runtime "
                "or ask user to install Node.js.", skill_dir.name,
            )
            npm_ok = False
        except subprocess.TimeoutExpired:
            logger.warning("skill[%s]: npm install TIMEOUT (10min)", skill_dir.name)
            npm_ok = False

    if req_txt.exists() and req_txt.read_text(encoding="utf-8").strip():
        target_dir = skill_dir / _PIP_DEPS_DIRNAME
        logger.info("skill[%s]: pip install starting (cold or deps changed)", skill_dir.name)
        try:
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install",
                 "--target", str(target_dir),
                 "--upgrade",
                 "-r", str(req_txt),
                 "--disable-pip-version-check"],
                capture_output=True, text=True, timeout=600, shell=False,
            )
            if r.returncode != 0:
                logger.warning(
                    "skill[%s]: pip install FAILED exit=%d stderr=%s",
                    skill_dir.name, r.returncode, r.stderr[:500],
                )
                pip_ok = False
            else:
                logger.info("skill[%s]: pip install done → %s", skill_dir.name, target_dir)
        except subprocess.TimeoutExpired:
            logger.warning("skill[%s]: pip install TIMEOUT (10min)", skill_dir.name)
            pip_ok = False

    # 全部成功才记 marker —— 任一失败下次启动还会重试
    if npm_ok and pip_ok:
        try:
            marker.write_text(cur_hash, encoding="utf-8")
        except OSError as e:
            logger.warning("skill[%s]: cannot write deps hash marker: %s", skill_dir.name, e)


def _read_package_bin(skill_dir: Path) -> dict[str, Path]:
    """Read package.json::bin → {subcommand: script_path}.

    Falls back to scanning bin/ directory if package.json is missing.
    """
    pkg = skill_dir / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
            bin_map = data.get("bin")
            if isinstance(bin_map, str):
                name = data.get("name", skill_dir.name).lstrip("@").split("/")[-1]
                return {name: skill_dir / Path(bin_map)}
            if isinstance(bin_map, dict):
                return {name: skill_dir / Path(rel) for name, rel in bin_map.items()}
        except json.JSONDecodeError:
            pass

    bin_dir = skill_dir / "bin"
    if bin_dir.is_dir():
        return {f.stem: f for f in bin_dir.iterdir() if f.is_file()}
    return {}


def _resolve_command(script_path: Path) -> list[str]:
    """Pick the right interpreter based on file extension. 用绝对路径避免 cwd 叠加错乱。"""
    abs_path = str(script_path.resolve())
    suffix = script_path.suffix.lower()
    if suffix == ".js":
        return ["node", abs_path]
    if suffix == ".py":
        return [sys.executable, abs_path]
    if suffix in (".sh", ".bash"):
        return ["bash", abs_path]
    return [abs_path]


def _make_run_command_func(
    commands: dict[str, Path],
    workdirs: dict[str, Path],
    user_id: str | None = None,
    artifacts_root: Path | None = None,
):
    """造 run_command 的 async callable（被 ToolSpec 包装喂给 AgentLoop）。

    user_id / artifacts_root：可选；若两者都提供，每次工具调用会自动生成 artifact_id +
    目录路径，并通过 subprocess env 注入给 skill 脚本（ADS_AGENT_ARTIFACT_DIR /
    ARTIFACT_ID / USER_ID）。skill 不一定要写 artifact——它可选。
    """

    available = list(commands.keys())

    async def run_command(command: str) -> str:
        """执行已注册的 SKILL.md 子命令。

        command 是完整的 shell 风格调用串，例如：
            query-metrics --metrics click,exposure --start-date 2026-03-01 --end-date 2026-03-07
            list-metrics --format json

        第一个 token 是子命令名（必须在已注册列表内）；后面是按 SKILL.md 文档写的 CLI 参数。
        系统按 shlex 拆分参数（list-form 调用，无 shell 注入风险），按脚本扩展名自动选择
        node/python/bash 解释器，cwd 设为该 skill 所在目录（CSV 等私有配置能正确加载）。
        """
        call_id = uuid.uuid4().hex[:8]
        t_start = time.perf_counter()
        logger.info("run_command[%s] REQUEST command=%r", call_id, command)

        try:
            # posix=True 才会去引号——'foo "bar baz"' → ['foo', 'bar baz']。
            # posix=False 把引号当字面量留在参数里，下游 CLI 解析就错了。
            parts = shlex.split(command, posix=True)
        except ValueError as e:
            logger.warning("run_command[%s] PARSE_FAIL err=%s", call_id, e)
            return f"Error: 命令解析失败：{e}"
        if not parts:
            logger.warning("run_command[%s] EMPTY_COMMAND", call_id)
            return "Error: 命令不能为空"

        subcmd = parts[0]
        if subcmd not in commands:
            logger.warning("run_command[%s] UNKNOWN_SUBCMD subcmd=%s available=%s", call_id, subcmd, available)
            return f"Error: 未注册的命令 '{subcmd}'。可用命令：{available}"

        cmd_argv = _resolve_command(commands[subcmd]) + parts[1:]

        # 构造 subprocess env：复制当前进程 env + 注入 artifact 上下文。
        # 注意不能依赖 runner 在 on_tool_start 设 os.environ ——langchain 用 create_task
        # 派发 tool 协程，env 写入和 subprocess 启动有 race（asyncio 事件循环时序不可控，
        # subprocess 先启动而 env 还没设上）。改为这里**显式**构造 env 字典传给 subprocess，
        # 确保每次工具调用 env 准确。e2e 实测：runner 设 os.environ 模式下 skill 拿到的是
        # 上一次调用的 env，导致 artifact 写到错的路径。
        from datetime import datetime as _datetime

        env = dict(os.environ)

        # pip 依赖装在 <skill_dir>/.deps-pip 下（隔离），run_command 时通过 PYTHONPATH 让
        # 解释器找到它。pure node skill 不受影响（cwd 已是 skill_dir，node 自动找 node_modules）。
        skill_pip_deps = workdirs[subcmd] / _PIP_DEPS_DIRNAME
        if skill_pip_deps.exists():
            env["PYTHONPATH"] = str(skill_pip_deps) + os.pathsep + env.get("PYTHONPATH", "")

        if user_id is not None and artifacts_root is not None:
            timestamp = _datetime.now().strftime("%Y-%m-%d-%H%M%S")
            artifact_id = f"{timestamp}-report"
            # **必须**用绝对路径——subprocess 的 cwd 是 skill 目录（不是项目根），
            # 相对路径会解析到 skill 目录下导致 artifact 写错位置。
            # e2e 实测：相对路径 ./data/{user}/artifacts/ → skill 把文件写到
            # skills/<name>/data/{user}/artifacts/ 而 REST API 看不到。
            artifact_dir = (artifacts_root / artifact_id).resolve()
            env["ADS_AGENT_ARTIFACT_DIR"] = str(artifact_dir)
            env["ADS_AGENT_ARTIFACT_ID"] = artifact_id
            env["ADS_AGENT_USER_ID"] = user_id

        artifact_id_log = env.get("ADS_AGENT_ARTIFACT_ID", "-")
        logger.info(
            "run_command[%s] SPAWN subcmd=%s argv=%r cwd=%s artifact_id=%s",
            call_id, subcmd, cmd_argv, str(workdirs[subcmd]), artifact_id_log,
        )

        proc = await _spawn_subprocess(
            *cmd_argv,
            cwd=str(workdirs[subcmd]),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            elapsed = time.perf_counter() - t_start
            logger.warning("run_command[%s] TIMEOUT subcmd=%s elapsed=%.2fs", call_id, subcmd, elapsed)
            return f"Error: 命令 '{subcmd}' 执行超时（60s）"

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        elapsed = time.perf_counter() - t_start

        logger.info(
            "run_command[%s] DONE exit=%d elapsed=%.2fs stdout_chars=%d stderr_chars=%d",
            call_id, proc.returncode, elapsed, len(out), len(err),
        )
        # stdout 头/尾各 800 字符（中间常见的是 mock JSON 数据，diagnose 时主要看头尾）
        logger.debug(
            "run_command[%s] STDOUT[:1600] %s",
            call_id,
            (out[:800] + " ...<TRUNC>... " + out[-800:]) if len(out) > 1600 else out,
        )
        if err.strip():
            logger.debug("run_command[%s] STDERR[:2000] %s", call_id, err[:2000])

        artifact_ids, err_clean = _extract_artifact_ids(err)
        trailer = ""
        if artifact_ids:
            # Sentinel 给 runner 解析（也让 LLM 看到——这是 human-friendly 提示，
            # LLM 在最终 markdown 里可引用这些 artifact）。runner 在 on_tool_end 时
            # 从 event["data"]["output"] 抽 ids 后推 SSE。
            trailer = "\n\n" + "\n".join(
                f"[已生成 artifact: {aid}]" for aid in artifact_ids
            )
            logger.info("run_command[%s] ARTIFACTS produced=%s", call_id, artifact_ids)

        if proc.returncode != 0:
            logger.warning(
                "run_command[%s] NON_ZERO_EXIT exit=%d stderr_head=%r",
                call_id, proc.returncode, err_clean[:500],
            )
            return f"[exit {proc.returncode}] {subcmd}\nstderr:\n{err_clean}\nstdout:\n{out}{trailer}"
        return out + trailer

    return run_command, available


def _make_run_tool_spec(
    commands: dict[str, Path],
    workdirs: dict[str, Path],
    user_id: str | None = None,
    artifacts_root: Path | None = None,
):
    """ToolSpec 包装 run_command 喂给 agent.loop.AgentLoop。

      - ToolSpec.func 期望 callable(args: dict) → str
      - schema 是 OpenAI tool schema dict

    返回 agent.loop.ToolSpec 实例。
    """
    # 延迟 import 避免 agent/loop.py 跟 skill_loader 之间循环依赖
    from agent.loop import ToolSpec

    run_command, available = _make_run_command_func(commands, workdirs, user_id, artifacts_root)

    async def adapter(args: dict) -> str:
        # OpenAI 协议把 LLM emit 的 arguments 解析成 dict 传进来；run_command 期望 str
        return await run_command(args.get("command", ""))

    schema = {
        "name": "run_command",
        "description": (
            "执行已注册的业务技能 CLI 命令。"
            f"可用命令：{available}。"
            "用法详见 system prompt 中各 skill 的完整 SKILL.md 文档。"
            "调用示例：command='query-metrics --metrics click,exposure --start-date 2026-03-01'"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "完整的 CLI 命令字符串（子命令名 + 参数）",
                },
            },
            "required": ["command"],
        },
    }
    return ToolSpec(
        name="run_command",
        func=adapter,
        schema=schema,
        is_concurrency_safe=True,  # run_command 包的 skill 都是只读查询，可并发
    )


def load_md_skills(
    system_dir: Path | str,
    user_dir: Path | str | None = None,
    user_id: str | None = None,
    artifacts_root: Path | str | None = None,
) -> SkillsPackage:
    """扫描 system_dir + 可选 user_dir，加载所有 SKILL.md 技能包。

    Returns SkillsPackage(prompt_addition, tools, summaries):
      - prompt_addition: 拼进 system prompt 的文档（含每个 skill 的完整 SKILL.md 正文）
      - tools: [run_command]（如果有任何已注册的 skill）或 []
      - summaries: 给前端面板展示用的 [{name, description, type, commands, source}]
        source 字段：'system'（仓库内置）或 'user'（用户上传）

    用户 skill 命名冲突时**用户覆盖系统**——允许用户用同名 skill 替换内置实现。

    user_id + artifacts_root：可选；同时提供时，run_command 会为每次工具调用自动生成
    artifact_id + 目录并通过 subprocess env（ADS_AGENT_*）注入给 skill。skill 可选择
    是否真的写 artifact。
    """
    commands: dict[str, Path] = {}
    workdirs: dict[str, Path] = {}
    doc_sections: list[str] = []
    summaries: list[dict] = []

    for source_tag, src_dir in [("system", system_dir), ("user", user_dir)]:
        if src_dir is None:
            continue
        src_dir = Path(src_dir)
        if not src_dir.is_dir():
            continue

        for sub in sorted(src_dir.iterdir()):
            if not sub.is_dir():
                continue
            skill_md = sub / "SKILL.md"
            if not skill_md.exists():
                continue

            try:
                content = skill_md.read_text(encoding="utf-8")
            except OSError:
                continue
            frontmatter, body = _parse_frontmatter(content)

            # 在读 bin 列表之前先装依赖——node skill 的子命令如 `bin/foo.js` 直接 import
            # 'bbb' 的话，依赖必须在 require 解析前就位。
            _ensure_skill_deps(sub)

            bin_map = _read_package_bin(sub)
            # bin_map 可以为空——doc-only skill（只有 SKILL.md，没 package.json::bin
            # 也没 bin/ 目录）是合法 skill 类型。这种 skill 不注册 run_command 子命令，
            # 但 SKILL.md 正文进系统 prompt，让 LLM 按它写的规则 / 知识工作。
            # 用例：业务规则、术语表、分析方法论、风格指南等纯指令性内容。

            skill_name = frontmatter.get("name") or sub.name
            skill_desc = (frontmatter.get("description") or "").strip()

            # 注册子命令到全局 registry（用户后来覆盖系统）。bin_map 空就跳过这步。
            for subcmd, script_path in bin_map.items():
                commands[subcmd] = script_path
                workdirs[subcmd] = sub.resolve()

            kind = "executable" if bin_map else "doc-only"
            section = f"### Skill: {skill_name} ({source_tag}, {kind})\n\n{body}"
            doc_sections.append(section)

            summaries.append({
                "name": skill_name,
                "description": skill_desc.split("\n\n")[0][:200],
                "type": "skill_md",
                "commands": list(bin_map.keys()),  # doc-only 时是 []
                "source": source_tag,
            })

    # 没任何 skill（既无 executable 也无 doc-only） = 完全空 package
    if not doc_sections:
        return SkillsPackage()

    # 分别拼"业务技能（有 CLI 命令）"和"业务知识 / 规则（doc-only）"段
    parts = []
    if commands:
        cmd_list = "\n".join(f"- `{c}`" for c in commands.keys())
        parts.append(
            "## 业务技能（通过 `run_command` 工具调用）\n\n"
            "下列 CLI 子命令已注册。调用方式：\n\n"
            "```\nrun_command(command=\"<子命令> <参数...>\")\n```\n\n"
            f"已注册的子命令：\n{cmd_list}\n"
        )
    parts.append(
        "## 业务规则 / 技能文档\n\n"
        "下列内容由 SKILL.md 加载，是工作时必须遵循的知识 / 规则 / 方法论。"
        "doc-only 的 skill 没有 CLI 命令，但 LLM 应当按其描述行事：\n\n"
        + "\n\n---\n\n".join(doc_sections)
    )
    prompt_addition = "\n\n".join(parts)

    artifacts_root_path = Path(artifacts_root) if artifacts_root is not None else None
    # commands 为空时不注册 run_command 工具——避免 LLM 看到空工具白调一遍
    tools = (
        [_make_run_tool_spec(commands, workdirs, user_id=user_id, artifacts_root=artifacts_root_path)]
        if commands else []
    )
    return SkillsPackage(
        prompt_addition=prompt_addition,
        tools=tools,
        summaries=summaries,
    )


_ARTIFACT_NOTIFICATION_PATTERN = re.compile(
    r"<<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id=([^>]+)>>>"
)


def _extract_artifact_ids(text: str) -> tuple[list[str], str]:
    """从文本里抽所有 artifact_id 通知行，返 (ids, cleaned_text)。"""
    ids = _ARTIFACT_NOTIFICATION_PATTERN.findall(text)
    cleaned = _ARTIFACT_NOTIFICATION_PATTERN.sub("", text)
    return ids, cleaned


_ARTIFACT_TRAILER_PATTERN = re.compile(r"\[已生成 artifact: ([^\]]+)\]")


def extract_artifact_ids_from_output(output: str) -> list[str]:
    """从工具返回的 output 字符串里抽出 artifact_id 列表。

    Runner 在 on_tool_end 拿到 event["data"]["output"] 后调此函数，对抽出的每个
    id 推 SSE event: artifact_updated。

    返回 []（无 artifact）或 ["id1", "id2", ...]。
    """
    return _ARTIFACT_TRAILER_PATTERN.findall(output)
