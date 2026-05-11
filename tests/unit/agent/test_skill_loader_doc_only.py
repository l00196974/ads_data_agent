"""doc-only skill 测试——只有 SKILL.md、没 package.json/bin/ 的 skill 也应当被识别。

用例：业务规则、术语表、风格指南等纯指令性内容——不需要可执行子命令，但 SKILL.md
内容要进 system prompt 让 LLM 跟着规则走。
"""
from pathlib import Path

from agent.skill_loader import load_md_skills


def test_doc_only_skill_registered_with_empty_commands(tmp_path: Path):
    """只有 SKILL.md 的 skill 出现在 summaries 里、commands 为空。"""
    user_dir = tmp_path / "user_skills"
    user_dir.mkdir()
    skill = user_dir / "ad-style-guide"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: ad-style-guide\ndescription: 广告文案风格规范\n---\n\n"
        "## 风格要求\n\n标题不超 15 字、不用感叹号、不堆砌形容词。\n",
        encoding="utf-8",
    )

    pkg = load_md_skills(system_dir=tmp_path / "nonexistent", user_dir=user_dir)

    assert len(pkg.summaries) == 1
    s = pkg.summaries[0]
    assert s["name"] == "ad-style-guide"
    assert s["commands"] == []          # doc-only 没命令
    assert s["source"] == "user"
    assert s["type"] == "skill_md"


def test_doc_only_skill_body_in_prompt_addition(tmp_path: Path):
    """SKILL.md 正文应当进 prompt_addition（让 LLM 看到规则）。"""
    user_dir = tmp_path / "user_skills"
    user_dir.mkdir()
    skill = user_dir / "naming-convention"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: naming-convention\ndescription: 投放命名规则\n---\n\n"
        "活动命名：`<品牌>-<产品>-<日期>-<媒体>`。例：huawei-mate60-20260501-wechat\n",
        encoding="utf-8",
    )

    pkg = load_md_skills(system_dir=tmp_path / "nonexistent", user_dir=user_dir)

    assert "naming-convention" in pkg.prompt_addition
    assert "huawei-mate60-20260501-wechat" in pkg.prompt_addition
    # doc-only 应被标记
    assert "doc-only" in pkg.prompt_addition


def test_doc_only_skill_no_run_command_tool(tmp_path: Path):
    """只有 doc-only skill 时，不注册 run_command 工具——避免 LLM 看到空工具白调。"""
    user_dir = tmp_path / "user_skills"
    user_dir.mkdir()
    skill = user_dir / "doc-only"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: doc-only\ndescription: 纯文档\n---\n规则一二三",
        encoding="utf-8",
    )

    pkg = load_md_skills(system_dir=tmp_path / "nonexistent", user_dir=user_dir)

    assert pkg.tools == []


def test_mixed_executable_and_doc_only(tmp_path: Path):
    """既有 executable skill 又有 doc-only skill 时，两段 prompt 都有，commands 只来自前者。"""
    user_dir = tmp_path / "user_skills"
    user_dir.mkdir()

    # Skill A: executable
    sk_a = user_dir / "sk-exec"
    sk_a.mkdir()
    (sk_a / "SKILL.md").write_text(
        "---\nname: sk-exec\ndescription: 可执行\n---\n用法说明",
        encoding="utf-8",
    )
    (sk_a / "bin").mkdir()
    (sk_a / "bin" / "do-thing.sh").write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    (sk_a / "package.json").write_text(
        '{"name":"sk-exec","bin":{"do-thing":"bin/do-thing.sh"}}',
        encoding="utf-8",
    )

    # Skill B: doc-only
    sk_b = user_dir / "sk-doc"
    sk_b.mkdir()
    (sk_b / "SKILL.md").write_text(
        "---\nname: sk-doc\ndescription: 纯规则\n---\n规则正文",
        encoding="utf-8",
    )

    pkg = load_md_skills(system_dir=tmp_path / "nonexistent", user_dir=user_dir)

    assert len(pkg.summaries) == 2
    names = {s["name"]: s for s in pkg.summaries}
    assert names["sk-exec"]["commands"] == ["do-thing"]
    assert names["sk-doc"]["commands"] == []

    # 两个 skill 的描述都进 prompt_addition
    assert "sk-exec" in pkg.prompt_addition
    assert "sk-doc" in pkg.prompt_addition
    # executable 段的 run_command 指引在
    assert "run_command" in pkg.prompt_addition
    # 业务规则 / 技能文档段也在
    assert "业务规则" in pkg.prompt_addition

    # 有 executable skill 时应注册 run_command 工具
    assert len(pkg.tools) == 1


def test_doc_only_skill_with_references_dir(tmp_path: Path):
    """skill 目录里有 references/ 等辅助子目录不应破坏识别——加载器只看 SKILL.md。"""
    user_dir = tmp_path / "user_skills"
    user_dir.mkdir()
    skill = user_dir / "deep-skill"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: deep-skill\ndescription: 含 references 子目录\n---\n参考 references/.",
        encoding="utf-8",
    )
    # 加 references/ + 其它辅助内容
    refs = skill / "references"
    refs.mkdir()
    (refs / "appendix.md").write_text("附录文档", encoding="utf-8")
    (refs / "examples.json").write_text('{"foo": "bar"}', encoding="utf-8")

    pkg = load_md_skills(system_dir=tmp_path / "nonexistent", user_dir=user_dir)

    assert len(pkg.summaries) == 1
    assert pkg.summaries[0]["name"] == "deep-skill"
