"""skill_loader 的 stderr artifact 通知解析。"""
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


def test_run_command_extracts_artifact_id_from_stderr(skills_root):
    from agent.skill_loader import (
        load_md_skills,
        consume_pending_artifact_ids,
    )

    pkg = load_md_skills(skills_root, user_dir=None)
    assert pkg.tools, "expected one run_command tool"
    tool = pkg.tools[0]

    os.environ["ADS_AGENT_ARTIFACT_ID"] = "2026-04-30-120000-test"
    os.environ["ADS_AGENT_ARTIFACT_DIR"] = "/tmp/x"
    try:
        async def _run():
            consume_pending_artifact_ids()  # 清残留（同一 context 内）
            output = await tool.coroutine(command="echo-env")
            ids = consume_pending_artifact_ids()
            return output, ids

        output, ids = asyncio.run(_run())
    finally:
        del os.environ["ADS_AGENT_ARTIFACT_ID"]
        del os.environ["ADS_AGENT_ARTIFACT_DIR"]

    assert "DIR=/tmp/x" in output
    assert "ID=2026-04-30-120000-test" in output
    assert ids == ["2026-04-30-120000-test"]


def test_extract_artifact_ids_pattern():
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


def test_consume_drains_pending(skills_root):
    """consume 一次后 contextvar 应该清空。"""
    from agent.skill_loader import consume_pending_artifact_ids, _pending_artifact_ids

    _pending_artifact_ids.set(["x", "y"])
    assert consume_pending_artifact_ids() == ["x", "y"]
    assert consume_pending_artifact_ids() == []
