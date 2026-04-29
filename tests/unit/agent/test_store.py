"""Tests for store backend selector + lifecycle."""
import asyncio
from unittest.mock import patch

import pytest

from agent.config import AppConfig, PersistenceConfig


def _reset_store_module():
    """每个 test 前清理 store.py 的模块级 singleton——否则跨 test 会泄漏状态。"""
    import agent.store as s
    s._store = None
    s._exit_stack = None


def test_get_store_lazy_inits_to_in_memory_for_sync_callers():
    """同步路径（单元测试 / 脚本）调 get_store 不需要先 await init_store——
    应当回退到 InMemoryStore 让代码能跑。"""
    _reset_store_module()
    from langgraph.store.memory import InMemoryStore
    from agent.store import get_store
    store = get_store()
    assert isinstance(store, InMemoryStore)


def test_init_store_memory_backend_returns_in_memory():
    _reset_store_module()
    from langgraph.store.memory import InMemoryStore
    from agent.store import init_store

    cfg = AppConfig(persistence=PersistenceConfig(store_backend="memory"))
    with patch("agent.store.load_config", return_value=cfg):
        store = asyncio.run(init_store())
    assert isinstance(store, InMemoryStore)


def test_init_store_unknown_backend_raises():
    _reset_store_module()
    from agent.store import init_store

    cfg = AppConfig(persistence=PersistenceConfig(store_backend="cassandra"))
    with patch("agent.store.load_config", return_value=cfg):
        with pytest.raises(ValueError, match="Unknown persistence.store_backend"):
            asyncio.run(init_store())


def test_init_store_mysql_backend_is_stub():
    """MySQL adapter 还没实现——必须明确 NotImplementedError，
    避免误以为可用导致生产事故。"""
    _reset_store_module()
    from agent.store import init_store

    cfg = AppConfig(persistence=PersistenceConfig(store_backend="mysql"))
    with patch("agent.store.load_config", return_value=cfg):
        with pytest.raises(NotImplementedError):
            asyncio.run(init_store())


def test_init_store_idempotent():
    """多次 init 只创建一次实例。"""
    _reset_store_module()
    from agent.store import init_store

    cfg = AppConfig(persistence=PersistenceConfig(store_backend="memory"))
    with patch("agent.store.load_config", return_value=cfg):
        s1 = asyncio.run(init_store())
        s2 = asyncio.run(init_store())
    assert s1 is s2


def test_persistence_config_defaults_to_sqlite():
    """生产默认是 sqlite——内存 store 进程重启即丢，多副本失同步。"""
    cfg = PersistenceConfig()
    assert cfg.store_backend == "sqlite"
