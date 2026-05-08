"""skill_loader 的 stderr artifact 通知解析。

设计：
- skill 通过 stderr 输出 <<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id=...>>> 通知
- run_command 抽 ids 后用 human-friendly sentinel "[已生成 artifact: <id>]" 加到 output 末尾
- runner 在 on_tool_end 用 extract_artifact_ids_from_output 抽 ids 推 SSE

为什么不用 ContextVar：langchain tool.arun() 用 create_task(coro, context=copied_ctx)
跨 task 边界，子 task 对 ContextVar 的写入对父 task 不可见。output sentinel 通过
工具返回值传递，langgraph 的 on_tool_end event["data"]["output"] 始终能拿到。
"""
import asyncio
import os
from pathlib import Path

import pytest


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    """造一个测试 skill：打印 ADS_AGENT_* env 并向 stderr 输出 artifact 通知行。"""
    pkg = tmp_path / "echo-env-skill"
    pkg.mkdir()
    (pkg / "SKILL.md").write_text(
        """---
name: echo-env
description: 测试 skill
---
""",
        encoding="utf-8",
    )
    (pkg / "package.json").write_text(
        '{"name":"echo-env","bin":{"echo-env":"bin/echo-env.py"}}',
        encoding="utf-8",
    )
    bin_dir = pkg / "bin"
    bin_dir.mkdir()
    script = bin_dir / "echo-env.py"
    script.write_text(
        """import os, sys
artifact_dir = os.environ.get("ADS_AGENT_ARTIFACT_DIR", "")
artifact_id = os.environ.get("ADS_AGENT_ARTIFACT_ID", "")
print(f"DIR={artifact_dir}")
print(f"ID={artifact_id}")
sys.stderr.write(f"<<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id={artifact_id}>>>\\n")
""",
        encoding="utf-8",
    )
    return tmp_path


def test_run_command_appends_artifact_trailer_to_output(skills_root):
    """skill 写 stderr 通知后，run_command 应把 ids 用 sentinel 加到 stdout 末尾。"""
    from agent.skill_loader import (
        load_md_skills,
        extract_artifact_ids_from_output,
    )

    pkg = load_md_skills(skills_root, user_dir=None)
    assert pkg.tools, "expected one run_command tool"
    tool = pkg.tools[0]

    os.environ["ADS_AGENT_ARTIFACT_ID"] = "2026-04-30-120000-test"
    os.environ["ADS_AGENT_ARTIFACT_DIR"] = "/tmp/x"
    try:
        output = asyncio.run(tool.func({"command": "echo-env"}))
    finally:
        del os.environ["ADS_AGENT_ARTIFACT_ID"]
        del os.environ["ADS_AGENT_ARTIFACT_DIR"]

    assert "DIR=/tmp/x" in output
    assert "ID=2026-04-30-120000-test" in output
    # ADS_AGENT 内部标识行不应回到给 LLM 的工具返回里
    assert "<<<ADS_AGENT:" not in output
    # human-friendly sentinel 应附加到末尾
    assert "[已生成 artifact: 2026-04-30-120000-test]" in output

    # runner 用的 helper 能从 output 抽 ids
    ids = extract_artifact_ids_from_output(output)
    assert ids == ["2026-04-30-120000-test"]


def test_extract_artifact_ids_pattern():
    """内部函数：从 stderr 文本抽 ids + clean。"""
    from agent.skill_loader import _extract_artifact_ids

    ids, clean = _extract_artifact_ids(
        "some output\n<<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id=2026-04-30-120000-x>>>\nmore"
    )
    assert ids == ["2026-04-30-120000-x"]
    assert "<<<ADS_AGENT:" not in clean
    assert "some output" in clean


def test_extract_artifact_ids_handles_multiple():
    from agent.skill_loader import _extract_artifact_ids

    text = (
        "<<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id=a>>>\n"
        "<<<ADS_AGENT:ARTIFACT_UPDATED:artifact_id=b>>>\n"
    )
    ids, clean = _extract_artifact_ids(text)
    assert ids == ["a", "b"]
    assert clean.strip() == ""


def test_extract_artifact_ids_no_match():
    from agent.skill_loader import _extract_artifact_ids

    ids, clean = _extract_artifact_ids("just plain text")
    assert ids == []
    assert clean == "just plain text"


def test_extract_artifact_ids_from_output_empty():
    """无 artifact 时 output 不变，helper 返 []。"""
    from agent.skill_loader import extract_artifact_ids_from_output

    assert extract_artifact_ids_from_output("just plain output") == []


def test_extract_artifact_ids_from_output_multiple():
    """多 artifact 时全部抽出。"""
    from agent.skill_loader import extract_artifact_ids_from_output

    output = "result\n\n[已生成 artifact: a]\n[已生成 artifact: b]"
    assert extract_artifact_ids_from_output(output) == ["a", "b"]
