"""Cross-request channel lookup for HitL confirm.

Only channels that satisfy ``ExternallyConfirmable`` need to be registered —
the registry exists specifically to let an out-of-band signal source (e.g.
the ``POST /confirm`` HTTP endpoint) reach the right channel instance.
Same-stack channels (CLI) don't need this — their confirm signal arrives
in the same coroutine via direct IO, no reverse lookup needed.

Callers can ``register()`` any ``BaseChannel`` without checking — the
registry silently drops channels that don't satisfy the protocol, so
callers never need to know which channel kinds are externally confirmable.

Multi-worker note
-----------------
``InMemoryChannelRegistry`` is a per-worker process-local dict. With
multiple uvicorn workers behind a load balancer, a confirm POST may land
on a worker that never registered the session — in that case ``get()``
returns None and the confirm endpoint replies "not_found".

For a true multi-worker deployment, plug in ``RedisChannelRegistry``
(see stub below for the design) which keeps a worker-local channel dict
**and** uses Redis pub/sub to forward confirm signals to whichever worker
actually owns the channel instance. Channel objects can't be serialized
across processes (they hold asyncio Events / Queues / ws connections),
so Redis stores only routing metadata — never the channel itself.
"""
from abc import ABC, abstractmethod
from typing import Optional

from .base import BaseChannel, ExternallyConfirmable


class ChannelRegistry(ABC):
    """Abstract registry. Implementations differ by deployment topology
    (single-worker = in-memory dict; multi-worker = local dict + cross-worker
    routing via Redis pub/sub or similar)."""

    @abstractmethod
    def register(self, session_id: str, channel: BaseChannel) -> None:
        """Register a channel. Must silently skip non-``ExternallyConfirmable``
        channels — callers shouldn't need to know which kinds need lookup."""

    @abstractmethod
    def get(self, session_id: str) -> Optional[ExternallyConfirmable]:
        """Return the channel for this session if owned locally, else None."""

    @abstractmethod
    def unregister(self, session_id: str) -> None:
        """Idempotent — never raises for unknown session_id."""


class InMemoryChannelRegistry(ChannelRegistry):
    """Single-worker default. Process-local dict, zero external dependencies."""

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


class RedisChannelRegistry(ChannelRegistry):  # pragma: no cover - design stub
    """Multi-worker registry stub. **Not implemented** — drop in when actually
    deploying multiple workers behind a non-sticky load balancer.

    Design sketch:

    1. Each worker keeps its own ``InMemoryChannelRegistry``-style local dict
       so ``get()`` for a locally-owned session is a fast in-process lookup.
    2. On ``register(session_id, channel)`` also write
       ``SET ads:channel:{session_id} = {worker_id}`` to Redis with TTL.
    3. Each worker subscribes to a Redis pub/sub channel
       ``ads:channel:resolve:{worker_id}`` on startup.
    4. When the confirm endpoint runs on a worker that doesn't own the
       session, instead of returning ``not_found`` it looks up the owner via
       Redis ``GET`` and ``PUBLISH``es to the owner's resolve channel.
       The owner's subscriber then calls the local channel's
       ``resolve_confirm(approve)``.
    5. ``unregister`` deletes the Redis key + invalidates any pending
       in-flight publishes.

    Channels themselves are NEVER serialized across workers — they hold
    ``asyncio.Event`` / ``asyncio.Queue`` / WebSocket refs that only make
    sense in their owning event loop.
    """

    def __init__(self, redis_url: str, worker_id: str):
        raise NotImplementedError(
            "RedisChannelRegistry is a design stub. Implement when deploying "
            "with >1 worker. See class docstring for the protocol."
        )

    def register(self, session_id: str, channel: BaseChannel) -> None:
        raise NotImplementedError

    def get(self, session_id: str) -> Optional[ExternallyConfirmable]:
        raise NotImplementedError

    def unregister(self, session_id: str) -> None:
        raise NotImplementedError


def make_channel_registry(registry_type: str = "memory", **kwargs) -> ChannelRegistry:
    """Factory: build the configured registry implementation.

    registry_type:
      - "memory" (default): in-process dict, single-worker only
      - "redis":  cross-worker via Redis pub/sub (stub, not implemented)
    """
    if registry_type == "memory":
        return InMemoryChannelRegistry()
    if registry_type == "redis":
        return RedisChannelRegistry(**kwargs)
    raise ValueError(f"Unknown channel_registry type: {registry_type!r}")


# Module-level singleton — single-worker default. For multi-worker deployments
# replace this with `make_channel_registry("redis", ...)` and ensure all import
# sites use the same instance.
channel_registry: ChannelRegistry = InMemoryChannelRegistry()
