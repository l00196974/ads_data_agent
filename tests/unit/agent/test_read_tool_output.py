"""read_tool_output 工具测试——覆盖刚清洗的 4 个隐患：

1. 同步 IO 改异步（asyncio.to_thread）——不再阻塞 event loop
2. 默认 limit 兜底——防"读截断又被截断"死循环
3. JSON 文件 pretty-print 后切行——单行紧凑 JSON 也能 offset/limit
4. 路径校验（不在 tool_outputs/ 开头 / 越界 / 不存在 三类合并）
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.tools.read_tool_output import make_read_tool_output_tool


@pytest.fixture
def tool_factory(tmp_path: Path):
    """造一个临时 data_dir 并准备好 read_tool_output_tool。"""
    user_id = "testuser"
    tool = make_read_tool_output_tool(data_dir=tmp_path, user_id=user_id)
    root = tmp_path / user_id / "tool_outputs"
    root.mkdir(parents=True)
    return tool, root


# ============================================================
# 基本读取 + 切片
# ============================================================


@pytest.mark.asyncio
async def test_read_normal_text_file(tool_factory):
    tool, root = tool_factory
    f = root / "call_x.txt"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")

    out = await tool.func({"paths": ["tool_outputs/call_x.txt"]})
    assert "line1" in out and "line2" in out and "line3" in out
    assert "3 行" in out


@pytest.mark.asyncio
async def test_offset_limit_slices_lines(tool_factory):
    tool, root = tool_factory
    f = root / "many.txt"
    f.write_text("".join(f"L{i}\n" for i in range(20)), encoding="utf-8")

    out = await tool.func({"paths": ["tool_outputs/many.txt"], "offset": 5, "limit": 3})
    assert "L5" in out and "L6" in out and "L7" in out
    assert "L4" not in out  # offset=5 跳过前 5 行
    assert "L8" not in out  # limit=3 后停


# ============================================================
# JSON pretty-print 切行（隐患 #3）
# ============================================================


@pytest.mark.asyncio
async def test_single_line_compact_json_gets_pretty_printed(tool_factory):
    """紧凑单行 JSON 不 pretty-print 的话整个文件只有 1 行，offset/limit 失效。
    清洗后应该被 json.dumps(indent=2) 重新格式化为多行才能切。"""
    tool, root = tool_factory
    f = root / "compact.json"
    # 单行紧凑 JSON——splitlines 后只有 1 行
    payload = {"items": [{"id": i, "name": f"item_{i}"} for i in range(10)]}
    f.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    assert f.read_text().count("\n") == 0  # 确认原文是真的单行紧凑

    out = await tool.func({"paths": ["tool_outputs/compact.json"], "limit": 5})
    # pretty-print 后总行数应该 > 1，limit=5 能切到内容
    assert "item_0" in out or "id" in out
    # 行数显示在 header 里——应该 > 1（pretty 后多行）
    assert "1 行" not in out, "紧凑 JSON 应该被 pretty-print，不应只显示 1 行"


@pytest.mark.asyncio
async def test_malformed_json_falls_back_to_original_text(tool_factory):
    """非合法 JSON 文件不能因为 pretty-print 失败而读不出——fallback 走原文。"""
    tool, root = tool_factory
    f = root / "broken.json"
    f.write_text("{this is not json\nbut should still read", encoding="utf-8")

    out = await tool.func({"paths": ["tool_outputs/broken.json"]})
    assert "this is not json" in out
    assert "Error" not in out  # 读取本身不应报错


# ============================================================
# 默认 limit 兜底（隐患 #2）
# ============================================================


@pytest.mark.asyncio
async def test_default_limit_applies_when_omitted(tool_factory):
    """不传 limit 时应当应用默认 200——而非全文返回（防再次触发截断）。"""
    tool, root = tool_factory
    f = root / "big.txt"
    f.write_text("".join(f"L{i}\n" for i in range(500)), encoding="utf-8")

    out = await tool.func({"paths": ["tool_outputs/big.txt"]})
    # 默认 limit=200，应该看到 L0 但看不到 L300（远在 200 之后）
    assert "L0\n" in out
    assert "L199\n" in out
    assert "L300" not in out


@pytest.mark.asyncio
async def test_explicit_null_limit_also_falls_back_to_default(tool_factory):
    """显式传 limit=null/None 时也应当走默认值（args.get 拿到 None 时走兜底）。"""
    tool, root = tool_factory
    f = root / "big.txt"
    f.write_text("".join(f"L{i}\n" for i in range(500)), encoding="utf-8")

    out = await tool.func({"paths": ["tool_outputs/big.txt"], "limit": None})
    assert "L199\n" in out
    assert "L300" not in out


# ============================================================
# 路径校验
# ============================================================


@pytest.mark.asyncio
async def test_path_must_start_with_tool_outputs(tool_factory):
    tool, _ = tool_factory
    out = await tool.func({"paths": ["random/file.json"]})
    assert "Error" in out and "tool_outputs/" in out


@pytest.mark.asyncio
async def test_path_traversal_blocked(tool_factory):
    """tool_outputs/../../etc/passwd 之类企图越界——resolve 后必须落在 root 内。"""
    tool, _ = tool_factory
    out = await tool.func({"paths": ["tool_outputs/../../../etc/passwd"]})
    assert "Error" in out


@pytest.mark.asyncio
async def test_nonexistent_file_returns_error_not_raise(tool_factory):
    tool, _ = tool_factory
    out = await tool.func({"paths": ["tool_outputs/never_existed.json"]})
    assert "Error" in out and "未找到" in out


# ============================================================
# 多文件批量
# ============================================================


@pytest.mark.asyncio
async def test_multiple_paths_returned_separately(tool_factory):
    tool, root = tool_factory
    (root / "a.txt").write_text("A content\n", encoding="utf-8")
    (root / "b.txt").write_text("B content\n", encoding="utf-8")

    out = await tool.func({"paths": ["tool_outputs/a.txt", "tool_outputs/b.txt"]})
    assert "A content" in out
    assert "B content" in out
    # 每个文件应该有自己的 header
    assert out.count("## tool_outputs/") == 2
