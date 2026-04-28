"""Cross-request channel lookup for HitL confirm.

Only channels that satisfy ``ExternallyConfirmable`` need to be registered —
the registry exists specifically to let an out-of-band signal source (e.g.
the ``POST /confirm`` HTTP endpoint) reach the right channel instance.
Same-stack channels (CLI) don't need this — their confirm signal arrives
in the same coroutine via direct IO, no reverse lookup needed.

Callers can ``register()`` any ``BaseChannel`` without checking — the
registry silently drops channels that don't satisfy the protocol, so
callers never need to know which channel kinds are externally confirmable.
"""
from typing import Optional

from .base import BaseChannel, ExternallyConfirmable


class ChannelRegistry:
    def __init__(self):
        self._channels: dict[str, ExternallyConfirmable] = {}

    def register(self, session_id: str, channel: BaseChannel) -> None:
        # Silently skip channels whose confirm is same-stack (e.g. CLIChannel) —
        # they have nothing meaningful to look up.
        if isinstance(channel, ExternallyConfirmable):
            self._channels[session_id] = channel

    def get(self, session_id: str) -> Optional[ExternallyConfirmable]:
        return self._channels.get(session_id)

    def unregister(self, session_id: str) -> None:
        self._channels.pop(session_id, None)


channel_registry = ChannelRegistry()
