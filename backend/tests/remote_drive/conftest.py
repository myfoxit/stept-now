"""Remote-drive test kit: gateway reset, fake extension sockets, an auto-ack
peer, and the MCP auth seam (controls who the browser tools see as caller)."""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import WebSocketDisconnect

from app.core.permissions import Perm, scopes_to_permissions
from app.services.remote_drive import Peer, RemoteDriveGateway, gateway

DEFAULT_SNAPSHOT: dict[str, Any] = {
    "url": "https://app.example/dashboard",
    "elements": "[0]<button Save>\n[1]<a Docs>",
    "count": 2,
}


@pytest.fixture(autouse=True)
async def _reset_gateway():
    """The gateway is a module singleton with pub/sub wiring — and every test
    gets a fresh event loop plus a fresh pubsub hub, so its subscriptions and
    bridge tasks have to be torn down (and lazily rebuilt) per test."""
    await gateway.aclose()
    yield
    await gateway.aclose()


@pytest.fixture
async def worker():
    """Factory for extra gateway instances — one per simulated API worker.

    They share the process-global in-memory pub/sub hub, which is exactly what
    separate uvicorn workers share through Redis in production.
    """
    workers: list[RemoteDriveGateway] = []

    def make() -> RemoteDriveGateway:
        instance = RemoteDriveGateway()
        workers.append(instance)
        return instance

    yield make
    for instance in workers:
        await instance.aclose()


async def eventually(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """Poll until ``predicate()`` holds (tiny sleeps, no fixed waits)."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.001)


# ---------------------------------------------------------------------------
# fake websocket (drives the endpoint function directly, widget-test style,
# but queue-fed so the test can talk to a LIVE connection from outside)
# ---------------------------------------------------------------------------


class _Disconnect:
    def __init__(self, code: int) -> None:
        self.code = code


class FakeExtensionSocket:
    """WebSocket double: records accept/close/send, queue-feeds receive_json."""

    def __init__(self) -> None:
        self._incoming: asyncio.Queue[Any] = asyncio.Queue()
        self.accepted = False
        self.close_code: int | None = None
        self.close_reason: str | None = None
        self.sent: list[dict[str, Any]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000, reason: str | None = None) -> None:
        self.close_code = code
        self.close_reason = reason
        # A server-side close ends the receive loop, like a real socket.
        self._incoming.put_nowait(_Disconnect(code))

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    async def receive_json(self) -> Any:
        item = await self._incoming.get()
        if isinstance(item, _Disconnect):
            raise WebSocketDisconnect(code=item.code)
        return item

    async def push(self, message: Any) -> None:
        """Deliver an inbound frame to the connection."""
        await self._incoming.put(message)

    def drop(self, code: int = 1000) -> None:
        """Client-side disconnect."""
        self._incoming.put_nowait(_Disconnect(code))


# ---------------------------------------------------------------------------
# auto-ack peer: registers on the real gateway and answers every control
# message inline, like a (very fast) live extension
# ---------------------------------------------------------------------------


async def register_auto_peer(
    workspace_id: str,
    device_id: str = "dev-1",
    *,
    user_id: str = "user-1",
    name: str = "Chrome",
    data: dict[str, Any] | None = None,
    record_stop_ack: dict[str, Any] | None = None,
    run_result: dict[str, Any] | None = None,
    on: RemoteDriveGateway | None = None,
) -> tuple[Peer, list[dict[str, Any]]]:
    """Register an auto-acking browser on ``on`` (default: the singleton)."""
    target = on if on is not None else gateway
    sent: list[dict[str, Any]] = []

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)
        kind = message.get("type")
        if kind == "pong":
            return
        ctrl_id = message["ctrl_id"]
        ack: dict[str, Any]
        if kind == "exec-op":
            snapshot = data if data is not None else DEFAULT_SNAPSHOT
            ack = {"type": "exec-result", "ctrl_id": ctrl_id, "ok": True, "data": snapshot}
        elif kind == "record-start":
            ack = {"type": "record-ack", "ctrl_id": ctrl_id, "ok": True, "recording": True}
        elif kind == "record-stop":
            fields = record_stop_ack or {"tour_id": "tour-1", "event_count": 5}
            ack = {"type": "record-ack", "ctrl_id": ctrl_id, "ok": True, **fields}
        elif kind == "run-tour":
            status = run_result or {"status": "completed"}
            ack = {"type": "run-result", "ctrl_id": ctrl_id, **status}
        else:
            return
        await target.handle_message(workspace_id, device_id, ack)

    peer = await target.register(workspace_id, device_id, user_id, name, send)
    return peer, sent


# ---------------------------------------------------------------------------
# MCP auth seam — patches app.mcp.auth so direct tool calls run without HTTP
# ---------------------------------------------------------------------------


@dataclass
class FakeResolvedKey:
    """Stand-in for ``app.mcp.auth.ResolvedMcpKey`` (same attribute contract)."""

    api_key: Any
    workspace_id: str
    permissions: frozenset[Perm]


@pytest.fixture
def mcp_caller(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Control who the browser tools resolve as the MCP caller.

    ``mcp_caller("ws-1", scopes=["write"])`` installs a resolved key with the
    real scope→permission mapping; ``mcp_caller(None)`` makes resolution fail.
    The real ``authorization_error`` / ``permission_error`` payloads stay in use.
    """

    def set_caller(workspace_id: str | None, scopes: list[str] | None = None) -> None:
        from app.mcp import auth as auth_module

        resolved = (
            None
            if workspace_id is None
            else FakeResolvedKey(
                api_key=None,
                workspace_id=workspace_id,
                permissions=scopes_to_permissions(scopes or ["read"]),
            )
        )

        @contextlib.asynccontextmanager
        async def open_session():
            yield None

        async def resolve_request_key(session: Any) -> FakeResolvedKey | None:
            return resolved

        monkeypatch.setattr(auth_module, "open_session", open_session, raising=False)
        monkeypatch.setattr(auth_module, "resolve_request_key", resolve_request_key, raising=False)

    return set_caller
