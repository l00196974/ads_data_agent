# tests/test_user_space.py
import pytest
from pathlib import Path
from agent.user_space import UserSpace


def test_user_dirs_created(tmp_path):
    us = UserSpace("user_001", data_dir=str(tmp_path))
    assert us.data_dir.exists()
    assert us.skills_dir.exists()
    assert us.memory_dir.exists()


def test_agents_md_merged(tmp_path):
    us = UserSpace("user_001", data_dir=str(tmp_path))
    # 写入用户自定义 agents.md
    us.agents_md_path.write_text("# 用户自定义指令\n- 始终用中文回答")
    content = us.get_agents_md(system_md_path="config/system_agents.md")
    assert "华为广告数据分析助手" in content   # 来自系统模板
    assert "始终用中文回答" in content          # 来自用户自定义


def test_different_users_isolated(tmp_path):
    us1 = UserSpace("user_001", data_dir=str(tmp_path))
    us2 = UserSpace("user_002", data_dir=str(tmp_path))
    assert us1.data_dir != us2.data_dir
