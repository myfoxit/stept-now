"""Widget websocket (visitor side): ``/ws/widget?token=<widget-jwt>``.

Auth: the signed widget contact token. The socket joins the ``conv:{id}`` topic
for each of the contact's conversations and re-joins on a ``subscribe`` message
after the widget starts a new one. Message/typing/conversation.updated events
are already broadcast onto those topics by the conversations service.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core import security
from app.core.db import get_session_factory
from app.core.logging import log
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.realtime.manager import broadcast, conversation_topic, manager

logger = log("ws.widget")

router = APIRouter()


async def _contact_conversation_ids(workspace_id: str, contact_id: str) -> list[str]:
    async with get_session_factory()() as session:
        return list(
            (
                await session.execute(
                    select(Conversation.id).where(
                        Conversation.workspace_id == workspace_id,
                        Conversation.contact_id == contact_id,
                    )
                )
            ).scalars()
        )


async def _owns_conversation(workspace_id: str, contact_id: str, conversation_id: str) -> bool:
    async with get_session_factory()() as session:
        conversation = await session.get(Conversation, conversation_id)
        return (
            conversation is not None
            and conversation.workspace_id == workspace_id
            and conversation.contact_id == contact_id
        )


@router.websocket("/ws/widget")
async def widget_websocket(websocket: WebSocket, token: str) -> None:
    # Authenticate before accepting the socket.
    try:
        payload = security.decode_token(token, "widget")
    except Exception:
        await websocket.close(code=4401)
        return
    workspace_id = payload.get("ws")
    contact_id = payload.get("sub")
    if not isinstance(workspace_id, str) or not isinstance(contact_id, str):
        await websocket.close(code=4401)
        return

    async with get_session_factory()() as session:
        contact = await session.get(Contact, contact_id)
        if contact is None or contact.workspace_id != workspace_id:
            await websocket.close(code=4403)
            return

    await websocket.accept()
    joined: set[str] = set()

    async def subscribe(conversation_id: str) -> None:
        topic = conversation_topic(conversation_id)
        if topic not in joined:
            joined.add(topic)
            await manager.join(topic, websocket)

    for conversation_id in await _contact_conversation_ids(workspace_id, contact_id):
        await subscribe(conversation_id)

    try:
        while True:
            message: dict[str, Any] = await websocket.receive_json()
            kind = message.get("type")
            if kind == "ping":
                await websocket.send_json({"type": "pong", "data": {}})
            elif kind == "subscribe":
                conversation_id = str(message.get("conversation_id", ""))
                if conversation_id and await _owns_conversation(
                    workspace_id, contact_id, conversation_id
                ):
                    await subscribe(conversation_id)
            elif kind == "typing":
                conversation_id = str(message.get("conversation_id", ""))
                if conversation_id and conversation_topic(conversation_id) in joined:
                    await broadcast(
                        conversation_topic(conversation_id),
                        "typing",
                        {
                            "conversation_id": conversation_id,
                            "is_typing": bool(message.get("is_typing", True)),
                            "source": "contact",
                        },
                    )
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("widget websocket error (contact %s)", contact_id)
    finally:
        for topic in joined:
            await manager.leave(topic, websocket)
