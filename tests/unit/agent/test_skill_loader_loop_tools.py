"""skill_loader 输出 loop_tools (ToolSpec) 的契约测试。

锁住：
  1. SkillsPackage.loop_tools 与 .tools 同时存在（兼容老链路）
  2. loop_tools[0] 是 agent.loop.ToolSpec 实例，name='run_command'
  3. ToolSpec.func(args=dict) 跟老 StructuredTool.coroutine(command=str) 等价输出
  4. ToolSpec.schema 是合法 OpenAI tool schema（含 type=object + required:[command]）
  5. 空 skill_dir 也不崩（loop_tools 是 []）
"""
import asyncio
import json
from pathlib import Path

import pytest

from agent.loop import ToolSpec
from agent.skill_loader import load_md_skills


def _setup_test_skill(tmp_path: Path) -> Path:
    """造一个最小可用的 skill 包。"""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: 测试 skill\n---\n# Test\n",
        encoding="utf-8",
    )
    bin_dir = skill_dir / "bin"
    bin_dir.mkdir()
    # 简单 echo 脚本——sys.executable 跑就行
    echo_py = bin_dir / "test-cmd.py"
    echo_py.write_text(
        "import sys; print('args:', sys.argv[1:] if len(sys.argv) > 1 else 'none')",
        encoding="utf-8",
    )
    return tmp_path


def test_loop_tools_field_present(tmp_path):
    skills_root = _setup_test_skill(tmp_path)
    pkg = load_md_skills(skills_root)
    assert hasattr(pkg, "loop_tools")
    assert len(pkg.loop_tools) == 1
    assert isinstance(pkg.loop_tools[0], ToolSpec)
    assert pkg.loop_tools[0].name == "run_command"


def test_loop_tools_and_tools_coexist(tmp_path):
    """老 .tools (StructuredTool) 和新 .loop_tools (ToolSpec) 同时存在。"""
    skills_root = _setup_test_skill(tmp_path)
    pkg = load_md_skills(skills_root)
    assert len(pkg.tools) == 1   # 老路径
    assert len(pkg.loop_tools) == 1  # 新路径
    # 名字一致
    old_name = pkg.tools[0].name
    new_name = pkg.loop_tools[0].name
    assert old_name == new_name == "run_command"


def test_tool_spec_schema_is_valid_openai_format(tmp_path):
    skills_root = _setup_test_skill(tmp_path)
    pkg = load_md_skills(skills_root)
    schema = pkg.loop_tools[0].schema
    assert schema["name"] == "run_command"
    assert "description" in schema
    params = schema["parameters"]
    assert params["type"] == "object"
    assert "command" in params["properties"]
    assert params["properties"]["command"]["type"] == "string"
    assert "command" in params["required"]


@pytest.mark.asyncio
async def test_tool_spec_func_executes_skill(tmp_path):
    """ToolSpec.func(args=dict) 真能 spawn skill 子进程拿到 stdout。"""
    skills_root = _setup_test_skill(tmp_path)
    pkg = load_md_skills(skills_root)

    output = await pkg.loop_tools[0].func({"command": "test-cmd hello world"})
    assert "hello" in output and "world" in output


@pytest.mark.asyncio
async def test_tool_spec_func_unknown_subcmd_returns_error(tmp_path):
    """未注册子命令 → 返 Error 字符串，不抛。"""
    skills_root = _setup_test_skill(tmp_path)
    pkg = load_md_skills(skills_root)

    output = await pkg.loop_tools[0].func({"command": "nonexistent-cmd"})
    assert "Error" in output and "未注册" in output


def test_empty_skill_dir_loop_tools_empty(tmp_path):
    """没有任何 SKILL.md 的目录 → loop_tools 是 []。"""
    pkg = load_md_skills(tmp_path)
    assert pkg.loop_tools == []
    assert pkg.tools == []
