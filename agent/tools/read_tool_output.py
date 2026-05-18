"""
read_tool_output — 读取被 ToolOutputTruncation 截断的完整工具输出。

只能读取 tool_outputs/ 目录下的文件，支持 offset/limit 分片读取。
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.loop import ToolSpec


def make_read_tool_output_tool(data_dir: str | Path, user_id: str) -> "ToolSpec":
    """创建 read_tool_output 内置工具，与 run_command 同级注册到 AgentLoop。

    Args:
        data_dir: 数据根目录（如 cfg.persistence.data_dir）
        user_id: 当前用户 ID

    Returns:
        ToolSpec 实例，可直接追加到 tools 列表
    """
    from agent.loop import ToolSpec

    root = Path(data_dir).resolve() / user_id / "tool_outputs"

    async def read_tool_output(args: dict) -> str:
        """读取被截断的工具输出文件。

        Args:
            args: 工具调用参数 dict，包含:
                - paths: list[str] — 相对路径列表，必须以 tool_outputs/ 开头
                - offset: int (可选) — 跳过前 N 行
                - limit: int (可选) — 最多返回多少行

        Returns:
            拼接后的文本内容，每个文件之间用分隔线隔开
        """
        paths: list[str] = args.get("paths", [])
        offset: int = args.get("offset", 0)
        limit: int | None = args.get("limit")

        output: list[str] = []
        for path in paths:
            p = Path(path)

            # 安全校验 1：只允许 tool_outputs/ 开头的路径
            try:
                relative = p.relative_to("tool_outputs")
            except ValueError:
                output.append(f"## Error: 路径必须以 tool_outputs/ 开头: {path}")
                continue

            # 安全校验 2：解析后仍在 root 目录内（防目录遍历）
            full = (root / relative).resolve()
            try:
                full.relative_to(root.resolve())
            except ValueError:
                output.append(f"## Error: 路径越界: {path}")
                continue

            # 读取文件
            try:
                text = full.read_text(encoding="utf-8")
                lines = text.splitlines(keepends=True)
                total_lines = len(lines)

                # 应用 offset / limit
                start = max(0, offset)
                end = start + limit if limit is not None else total_lines
                selected = lines[start:end]

                content = "".join(selected)
                summary = (f"## {path}  ({total_lines} 行"
                           f"{', 显示 ' + str(start) + '-' + str(end - 1) if limit else ''})")
                output.append(summary)
                if content:
                    output.append(content)
                else:
                    output.append("（空内容）")
            except FileNotFoundError:
                output.append(f"## Error: 文件未找到: {path}")
            except Exception as e:
                output.append(f"## Error: 读取失败 {path}: {e}")

        return "\n".join(output)

    schema = {
        "name": "read_tool_output",
        "description": "读取被截断的完整工具输出。当你看到「saved to tool_outputs/xxx.json」时，用此工具查看完整内容。支持 offset + limit 分片读取大文件。只支持读取 tool_outputs/ 目录下的文件。",
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
                    "description": "跳过前 N 行（0 表示从开头读）。用于分批读取大文件：先 offset=0 limit=50 读前 50 行，不够再 offset=50 limit=50 继续。",
                },
                "limit": {
                    "type": "number",
                    "description": "最多返回多少行。不传或传 null 表示返回全部行。通常配合 offset 分片使用。",
                },
            },
            "required": ["paths"],
        },
    }
    return ToolSpec(name="read_tool_output",
                    func=read_tool_output,
                    schema=schema,
                    is_concurrency_safe=True)
