"""Tests for the ExternallyConfirmable protocol + ChannelRegistry filter.

Verifies that the registry actually gates on the capability protocol:
WebSSEChannel (which has resolve_confirm) gets registered; CLIChannel
(same-stack stdin block, no resolve_confirm) is silently skipped.
"""
import asyncio

from api.channel import ExternallyConfirmable, WebSSEChannel
from api.channel.cli import CLIChannel
from api.channel.registry import (
    ChannelRegistry,
    InMemoryChannelRegistry,
    RedisChannelRegistry,
    make_channel_registry,
)


def test_websse_satisfies_externally_confirmable():
    queue = asyncio.Queue()
    ch = WebSSEChannel("u", "s", queue)
    assert isinstance(ch, ExternallyConfirmable)


def test_cli_does_not_satisfy_externally_confirmable():
    ch = CLIChannel("u", "s")
    assert not isinstance(ch, ExternallyConfirmable)


def test_registry_accepts_externally_confirmable_channel():
    queue = asyncio.Queue()
    ch = WebSSEChannel("u", "s1", queue)
    reg = InMemoryChannelRegistry()
    reg.register("s1", ch)
    assert reg.get("s1") is ch


def test_registry_silently_skips_same_stack_channel():
    """CLIChannel doesn't need cross-request lookup — register is a no-op,
    not an error. This lets callers register without checking channel kind."""
    ch = CLIChannel("u", "s2")
    reg = InMemoryChannelRegistry()
    reg.register("s2", ch)  # should not raise
    assert reg.get("s2") is None


def test_registry_unregister_idempotent():
    reg = InMemoryChannelRegistry()
    reg.unregister("never_registered")  # must not raise
    queue = asyncio.Queue()
    ch = WebSSEChannel("u", "s3", queue)
    reg.register("s3", ch)
    reg.unregister("s3")
    assert reg.get("s3") is None
    reg.unregister("s3")  # second call also no-op


def test_factory_returns_inmemory_by_default():
    reg = make_channel_registry()
    assert isinstance(reg, InMemoryChannelRegistry)


def test_factory_rejects_unknown_type():
    import pytest
    with pytest.raises(ValueError, match="Unknown channel_registry"):
        make_channel_registry("etcd")


def test_redis_registry_is_stub():
    """Until Redis adapter is implemented, instantiation must explicitly fail
    rather than silently no-op (which would let multi-worker bugs slip)."""
    import pytest
    with pytest.raises(NotImplementedError):
        RedisChannelRegistry(redis_url="redis://x", worker_id="w1")


def test_inmemory_registry_implements_abc():
    """ABC contract guard: any future registry impl must satisfy this."""
    assert issubclass(InMemoryChannelRegistry, ChannelRegistry)
    assert issubclass(RedisChannelRegistry, ChannelRegistry)
