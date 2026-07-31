"""WebSocket room manager bridged onto pub/sub.

Every broadcast goes through pub/sub (even in-memory) so behavior is identical
in single- and multi-process deployments. One bridge task per (process, topic)
fans messages out to local sockets.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from fastapi import WebSocket

from app.core.logging import log
from app.core.pubsub import get_pubsub

logger = log("realtime")


class ConnectionManager:
    def __init__(self) -> None:
        self._rooms: dict[str, set[WebSocket]] = {}
        self._bridges: dict[str, asyncio.Task[None]] = {}

    async def join(self, topic: str, websocket: WebSocket) -> None:
        self._rooms.setdefault(topic, set()).add(websocket)
        if topic not in self._bridges:
            self._bridges[topic] = asyncio.create_task(
                self._bridge(topic), name=f"ws-bridge:{topic}"
            )

    async def leave(self, topic: str, websocket: WebSocket) -> None:
        sockets = self._rooms.get(topic)
        if sockets is None:
            return
        sockets.discard(websocket)
        if not sockets:
            self._rooms.pop(topic, None)
            bridge = self._bridges.pop(topic, None)
            if bridge is not None:
                bridge.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await bridge

    async def _bridge(self, topic: str) -> None:
        subscription = await get_pubsub().subscribe(topic)
        try:
            async for message in subscription:
                await self._deliver(topic, message)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("bridge for %s died", topic)
        finally:
            await subscription.close()

    async def _deliver(self, topic: str, message: dict[str, Any]) -> None:
        for websocket in list(self._rooms.get(topic, ())):
            try:
                await websocket.send_json(message)
            except Exception:
                await self.leave(topic, websocket)

    async def shutdown(self) -> None:
        for bridge in self._bridges.values():
            bridge.cancel()
        for bridge in list(self._bridges.values()):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await bridge
        self._bridges.clear()
        self._rooms.clear()


manager = ConnectionManager()


async def broadcast(topic: str, type: str, data: dict[str, Any]) -> None:
    """Publish a typed realtime message to a topic (crosses processes)."""
    await get_pubsub().publish(topic, {"type": type, "data": data})


def workspace_topic(workspace_id: str) -> str:
    return f"ws:{workspace_id}"


def user_topic(workspace_id: str, user_id: str) -> str:
    return f"ws:{workspace_id}:user:{user_id}"


def conversation_topic(conversation_id: str) -> str:
    return f"conv:{conversation_id}"
