"""Tests for the ExternallyConfirmable protocol + ChannelRegistry filter.

Verifies that the registry actually gates on the capability protocol:
WebSSEChannel (which has resolve_confirm) gets registered; CLIChannel
(same-stack stdin block, no resolve_confirm) is silently skipped.
"""
import asyncio

from api.channel import ExternallyConfirmable, WebSSEChannel
from api.channel.cli import CLIChannel
from api.channel.registry import ChannelRegistry


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
    reg = ChannelRegistry()
    reg.register("s1", ch)
    assert reg.get("s1") is ch


def test_registry_silently_skips_same_stack_channel():
    """CLIChannel doesn't need cross-request lookup — register is a no-op,
    not an error. This lets callers register without checking channel kind."""
    ch = CLIChannel("u", "s2")
    reg = ChannelRegistry()
    reg.register("s2", ch)  # should not raise
    assert reg.get("s2") is None


def test_registry_unregister_idempotent():
    reg = ChannelRegistry()
    reg.unregister("never_registered")  # must not raise
    queue = asyncio.Queue()
    ch = WebSSEChannel("u", "s3", queue)
    reg.register("s3", ch)
    reg.unregister("s3")
    assert reg.get("s3") is None
    reg.unregister("s3")  # second call also no-op
