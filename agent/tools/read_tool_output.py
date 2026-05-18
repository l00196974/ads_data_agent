"""
read_tool_output — 读取被 ToolOutputTruncation 截断的完整工具输出。

只能读取 tool_outputs/ 目录下的文件，支持 offset/limit 分片读取。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.loop import ToolSpec


# 默认行数上限——LLM 不传 limit 时也兜底。
# 设计取舍：默认全部返回 → 大文件被 ToolOutputTruncation 再次截断卸盘 → LLM 读到的
# 还是截断版 → 形成"读截断再被截断"的死循环。给默认 limit 把单次返回压在常规阈值
# (tool_output_max_bytes=5000) 之下；LLM 用 offset 翻页拿后续内容。
_DEFAULT_LIMIT = 200


def _read_file_sliced(full: Path, offset: int, limit: int | None) -> tuple[str, int]:
    """同步读 + 切片——独立函数让 asyncio.to_thread 包装更干净。

    .json 文件做 pretty-print 后再按行切（紧凑 JSON 是单行，按行切等于无效）。
    其它文件按原文 splitlines。

    返回 (selected_text, total_lines)。
    """
    text = full.read_text(encoding="utf-8")

    # 紧凑 JSON 切行无意义——先 pretty 化让 offset/limit 真正生效。
    # 失败兜底走原文，避免非合法 JSON 文件（如部分写入、自定义格式）整体读不出
    if full.suffix.lower() == ".json":
        try:
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, ValueError):
            pass  # 非合法 JSON——保留原文按行切

    lines = text.splitlines(keepends=True)
    total = len(lines)
    start = max(0, offset)
    end = start + limit if limit is not None else total
    return "".join(lines[start:end]), total


def make_read_tool_output_tool(data_dir: str | Path, user_id: str) -> "ToolSpec":
    """创建 read_tool_output 内置工具，与 run_command 同级注册到 AgentLoop。

    Args:
        data_dir: 数据根目录（如 cfg.persistence.data_dir）
        user_id: 当前用户 ID

    Returns:
        ToolSpec 实例，可直接追加到 tools 列表
    """
    from agent.loop import ToolSpec

    root = (Path(data_dir).resolve() / user_id / "tool_outputs").resolve()

    async def read_tool_output(args: dict) -> str:
        """读取被截断的工具输出文件。

        Args:
            args: 工具调用参数 dict，包含:
                - paths: list[str] — 相对路径列表，必须以 tool_outputs/ 开头
                - offset: int (可选) — 跳过前 N 行
                - limit: int (可选) — 最多返回多少行；默认 200（防被再次截断）

        Returns:
            拼接后的文本内容，每个文件之间用分隔线隔开
        """
        paths: list[str] = args.get("paths", [])
        offset: int = args.get("offset", 0)
        # 默认 limit 防"读截断又被截断"死循环——LLM 不传时按 200 行返回
        limit = args.get("limit")
        if limit is None:
            limit = _DEFAULT_LIMIT

        output: list[str] = []
        for path in paths:
            # 路径校验：必须 tool_outputs/ 开头 + resolve 后仍在 root 内（防遍历）
            try:
                relative = Path(path).relative_to("tool_outputs")
                full = (root / relative).resolve()
                full.relative_to(root)
            except ValueError:
                output.append(f"## Error: 路径必须以 tool_outputs/ 开头且不越界: {path}")
                continue

            # 文件读 + 切片——用 to_thread 避免 read_text 阻塞 event loop
            # （tool_outputs 卸盘文件常 50-500KB，多用户场景同步 IO 会拖累整服务）
            try:
                content, total_lines = await asyncio.to_thread(
                    _read_file_sliced, full, offset, limit,
                )
            except FileNotFoundError:
                output.append(f"## Error: 文件未找到: {path}")
                continue
            except Exception as e:
                output.append(f"## Error: 读取失败 {path}: {e}")
                continue

            start = max(0, offset)
            end = start + limit
            shown_to = min(end - 1, total_lines - 1) if total_lines else 0
            output.append(f"## {path}  ({total_lines} 行, 显示 {start}-{shown_to})")
            output.append(content if content else "（空内容）")

        return "\n".join(output)

    schema = {
        "name": "read_tool_output",
        "description": "读取被截断的完整工具输出。当你看到「saved to tool_outputs/xxx.json」时，用此工具查看完整内容。支持 offset + limit 分片读取大文件（默认 limit=200 行，传 null 也会被替换为 200，避免重复触发截断）。只支持读取 tool_outputs/ 目录下的文件。",
        "parameters": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "文件路径列表，如 [\"tool_outputs/abc123/call_def456.json\"], [\"tool_outputs/abc123/call_def456.json\", \"tool_outputs/abc123/call_def789.json\"]",
                },
                "offset": {
                    "type": "number",
                    "description": "跳过前 N 行（0 表示从开头读）。用于分批读取大文件：先 offset=0 读前 200 行，不够再 offset=200 limit=200 继续。",
                },
                "limit": {
                    "type": "number",
                    "description": "最多返回多少行。默认/null 都按 200 行处理（避免单次返回过大被再次截断）。配合 offset 分片读取大文件。",
                },
            },
            "required": ["paths"],
        },
    }
    return ToolSpec(name="read_tool_output",
                    func=read_tool_output,
                    schema=schema,
                    is_concurrency_safe=True)
