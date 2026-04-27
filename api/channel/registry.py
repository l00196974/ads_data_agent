from typing import Optional

from .base import BaseChannel


class ChannelRegistry:
    def __init__(self):
        self._channels: dict[str, BaseChannel] = {}

    def register(self, session_id: str, channel: BaseChannel) -> None:
        self._channels[session_id] = channel

    def get(self, session_id: str) -> Optional[BaseChannel]:
        return self._channels.get(session_id)

    def unregister(self, session_id: str) -> None:
        self._channels.pop(session_id, None)


channel_registry = ChannelRegistry()
