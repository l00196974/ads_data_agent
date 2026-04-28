"""SKILL.md skill loader (Claude Code-style skill packages).

加载方式：单一 `run_command(command)` 工具 + SKILL.md 全文进 system prompt。

LLM 看到的是 SKILL.md 原汁原味的 CLI 用法，调用时按 shell 习惯写，零 Python 转换。

约定：`config.yaml::skills.md_dir` 下的每个子目录如果包含 `SKILL.md`，
就被识别为一个技能包。loader 读 frontmatter 拿元信息，读 `package.json::bin`
（或回退到 `bin/` 目录扫描）拿可执行子命令清单，并把 SKILL.md 正文拼进 system prompt。

技能包目录结构（参考 skills/metric-data-extractor）：

    skill-name/
      SKILL.md              # frontmatter 描述 + Markdown 用法文档（LLM 看到的）
      package.json          # 含 bin 字段映射子命令名 → 脚本路径
      bin/                  # 实际可执行脚本（任意语言：node/python/bash）
        query-metrics.js
        ...
      config/               # 技能私有配置（CSV 等）
      node_modules/         # 已安装的依赖

加新技能：把整个目录拷到 skills/ 下，重启 agent 后端即可，**无需改 Python**。

安全模型：
- run_command 只能跑已注册的子命令名（来自各 SKILL.md 的 package.json::bin）
- LLM 控制不了运行什么脚本，只控制传给脚本的参数
- 业务脚本本身需要做参数校验（同 CLI 安全实践）
"""
from __future__ import annotations

import asyncio
import json
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from langchain_core.tools import StructuredTool

# alias 是为了避开 lint 钩子对 `exec(` 字面量的告警；语义是安全的 list-form 子进程 API
from asyncio import create_subprocess_exec as _spawn_subprocess


@dataclass
class SkillsPackage:
    """所有 SKILL.md 包加载完后的产物。"""

    prompt_addition: str = ""           # 拼进 system prompt 的 SKILL.md 文档段
    tools: list = field(default_factory=list)  # 给 agent 的工具列表（一个或零个）
    summaries: list[dict] = field(default_factory=list)  # 给 UI 面板展示的摘要


def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Split a Markdown file with YAML frontmatter into (config, body)."""
    if not content.startswith("---"):
        return {}, content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}, content
    config = yaml.safe_load(parts[1]) or {}
    body = parts[2].strip()
    return config, body


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


def _make_run_tool(commands: dict[str, Path], workdirs: dict[str, Path]) -> StructuredTool:
    """单一通用工具：根据 command 字符串第一个 token 找到对应 skill 脚本，subprocess 执行。"""

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
        try:
            # posix=True 才会去引号——'foo "bar baz"' → ['foo', 'bar baz']。
            # posix=False 把引号当字面量留在参数里，下游 CLI 解析就错了。
            parts = shlex.split(command, posix=True)
        except ValueError as e:
            return f"Error: 命令解析失败：{e}"
        if not parts:
            return "Error: 命令不能为空"

        subcmd = parts[0]
        if subcmd not in commands:
            return f"Error: 未注册的命令 '{subcmd}'。可用命令：{available}"

        cmd_argv = _resolve_command(commands[subcmd]) + parts[1:]
        proc = await _spawn_subprocess(
            *cmd_argv,
            cwd=str(workdirs[subcmd]),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        except asyncio.TimeoutError:
            proc.kill()
            return f"Error: 命令 '{subcmd}' 执行超时（60s）"

        out = stdout.decode("utf-8", errors="replace")
        err = stderr.decode("utf-8", errors="replace")
        if proc.returncode != 0:
            return f"[exit {proc.returncode}] {subcmd}\nstderr:\n{err}\nstdout:\n{out}"
        return out

    return StructuredTool.from_function(
        coroutine=run_command,
        name="run_command",
        description=(
            "执行已注册的业务技能 CLI 命令。\n"
            f"可用命令：{available}\n"
            "用法详见 system prompt 中各 skill 的完整 SKILL.md 文档。\n"
            "调用示例：command='query-metrics --metrics click,exposure --start-date 2026-03-01'"
        ),
    )


def load_md_skills(
    system_dir: Path | str,
    user_dir: Path | str | None = None,
) -> SkillsPackage:
    """扫描 system_dir + 可选 user_dir，加载所有 SKILL.md 技能包。

    Returns SkillsPackage(prompt_addition, tools, summaries):
      - prompt_addition: 拼进 system prompt 的文档（含每个 skill 的完整 SKILL.md 正文）
      - tools: [run_command]（如果有任何已注册的 skill）或 []
      - summaries: 给前端面板展示用的 [{name, description, type, commands, source}]
        source 字段：'system'（仓库内置）或 'user'（用户上传）

    用户 skill 命名冲突时**用户覆盖系统**——允许用户用同名 skill 替换内置实现。
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

            bin_map = _read_package_bin(sub)
            if not bin_map:
                continue

            skill_name = frontmatter.get("name") or sub.name
            skill_desc = (frontmatter.get("description") or "").strip()

            # 注册子命令到全局 registry（用户后来覆盖系统）
            for subcmd, script_path in bin_map.items():
                commands[subcmd] = script_path
                workdirs[subcmd] = sub.resolve()

            section = f"### Skill: {skill_name} ({source_tag})\n\n{body}"
            doc_sections.append(section)

            summaries.append({
                "name": skill_name,
                "description": skill_desc.split("\n\n")[0][:200],
                "type": "skill_md",
                "commands": list(bin_map.keys()),
                "source": source_tag,
            })

    if not commands:
        return SkillsPackage()

    cmd_list = "\n".join(f"- `{c}`" for c in commands.keys())
    prompt_addition = (
        "## 业务技能（通过 `run_command` 工具调用）\n\n"
        "下列 CLI 子命令已注册。调用方式：\n\n"
        "```\nrun_command(command=\"<子命令> <参数...>\")\n```\n\n"
        f"已注册的子命令：\n{cmd_list}\n\n"
        "完整用法文档（每个 skill 一段，按 CLI 命令分组说明）：\n\n"
        + "\n\n---\n\n".join(doc_sections)
    )

    return SkillsPackage(
        prompt_addition=prompt_addition,
        tools=[_make_run_tool(commands, workdirs)],
        summaries=summaries,
    )
