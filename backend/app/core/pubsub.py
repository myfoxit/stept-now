"""Pub/sub for realtime fan-out.

Topics: `ws:{workspace_id}` (dashboard), `ws:{workspace_id}:user:{user_id}`
(per-member), `conv:{conversation_id}` (widget visitors). In-memory works for a
single process (dev/tests); Redis makes it multi-process.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any, Protocol

from app.core.config import get_settings
from app.core.logging import log

logger = log("pubsub")


class Subscription(Protocol):
    def __aiter__(self) -> AsyncIterator[dict[str, Any]]: ...
    async def close(self) -> None: ...


class PubSub(Protocol):
    async def publish(self, topic: str, message: dict[str, Any]) -> None: ...
    async def subscribe(self, topic: str) -> Subscription: ...
    async def close(self) -> None: ...


class _MemorySubscription:
    def __init__(self, hub: InMemoryPubSub, topic: str):
        self._hub = hub
        self._topic = topic
        self.queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1000)

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            yield await self.queue.get()

    async def close(self) -> None:
        self._hub._drop(self._topic, self)


class InMemoryPubSub:
    def __init__(self) -> None:
        self._topics: dict[str, set[_MemorySubscription]] = {}

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        for sub in list(self._topics.get(topic, ())):
            with contextlib.suppress(asyncio.QueueFull):
                sub.queue.put_nowait(message)

    async def subscribe(self, topic: str) -> _MemorySubscription:
        sub = _MemorySubscription(self, topic)
        self._topics.setdefault(topic, set()).add(sub)
        return sub

    def _drop(self, topic: str, sub: _MemorySubscription) -> None:
        subs = self._topics.get(topic)
        if subs is not None:
            subs.discard(sub)
            if not subs:
                self._topics.pop(topic, None)

    async def close(self) -> None:
        self._topics.clear()


class _RedisSubscription:
    def __init__(self, pubsub: Any, topic: str):
        self._pubsub = pubsub
        self._topic = topic

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[dict[str, Any]]:
        async for raw in self._pubsub.listen():
            if raw.get("type") != "message":
                continue
            try:
                yield json.loads(raw["data"])
            except (TypeError, ValueError):
                logger.warning("dropping malformed pubsub payload on %s", self._topic)

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._pubsub.unsubscribe(self._topic)
            await self._pubsub.aclose()


class RedisPubSub:
    def __init__(self, url: str):
        import redis.asyncio as aioredis

        self._redis = aioredis.from_url(url, decode_responses=True)

    async def publish(self, topic: str, message: dict[str, Any]) -> None:
        await self._redis.publish(topic, json.dumps(message, default=str))

    async def subscribe(self, topic: str) -> _RedisSubscription:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(topic)
        return _RedisSubscription(pubsub, topic)

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._redis.aclose()


_pubsub: PubSub | None = None


def get_pubsub() -> PubSub:
    global _pubsub
    if _pubsub is None:
        settings = get_settings()
        _pubsub = RedisPubSub(settings.redis_url) if settings.redis_url else InMemoryPubSub()
    return _pubsub


async def reset_pubsub() -> None:
    """Test helper."""
    global _pubsub
    if _pubsub is not None:
        await _pubsub.close()
    _pubsub = None
