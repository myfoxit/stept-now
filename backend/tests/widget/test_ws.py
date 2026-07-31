"""Widget websocket auth + typing forwarding (driven through a fake socket)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
from fastapi import WebSocketDisconnect

from app.core.pubsub import get_pubsub
from app.realtime.manager import conversation_topic
from app.realtime.widget_ws import widget_websocket
from tests.widget.conftest import WidgetSetup, auth_headers, boot


class FakeWebSocket:
    """Minimal WebSocket double: scripts inbound frames, records accept/close/send."""

    def __init__(self, incoming: list[dict[str, Any]]):
        self._incoming = list(incoming)
        self.accepted = False
        self.close_code: int | None = None
        self.sent: list[dict[str, Any]] = []

    async def accept(self) -> None:
        self.accepted = True

    async def close(self, code: int = 1000) -> None:
        self.close_code = code

    async def send_json(self, data: dict[str, Any]) -> None:
        self.sent.append(data)

    async def receive_json(self) -> dict[str, Any]:
        if self._incoming:
            return self._incoming.pop(0)
        raise WebSocketDisconnect(code=1000)


async def test_ws_bad_token_closes_4401():
    ws = FakeWebSocket([])
    await widget_websocket(ws, token="not-a-token")  # type: ignore[arg-type]
    assert ws.accepted is False
    assert ws.close_code == 4401


async def test_ws_access_token_rejected():
    from app.core.security import create_access_token

    ws = FakeWebSocket([])
    await widget_websocket(ws, token=create_access_token("some-user"))  # type: ignore[arg-type]
    assert ws.close_code == 4401  # wrong token type


async def test_ws_valid_token_accepts_and_forwards_typing(
    client: httpx.AsyncClient, widget: WidgetSetup
):
    token = (await boot(client, widget.widget_key, visitor_id="v1")).json()["token"]
    created = await client.post(
        "/api/widget/conversations", json={"message": "hi"}, headers=auth_headers(token)
    )
    conversation_id = created.json()["id"]

    subscription = await get_pubsub().subscribe(conversation_topic(conversation_id))
    ws = FakeWebSocket([{"type": "typing", "conversation_id": conversation_id, "is_typing": True}])
    await widget_websocket(ws, token=token)  # type: ignore[arg-type]

    assert ws.accepted is True
    envelope = await asyncio.wait_for(subscription.queue.get(), 2.0)
    assert envelope["type"] == "typing"
    assert envelope["data"]["conversation_id"] == conversation_id
    assert envelope["data"]["source"] == "contact"
    await subscription.close()
