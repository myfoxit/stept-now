"""Dashboard websocket: /ws/app?token=<access-jwt>&workspace_id=<id>

Server → client messages: {"type": "...", "data": {...}} — message.created,
conversation.updated, typing, presence.changed, notification.created,
approval.pending, agent_run.updated. Client → server: typing + presence pings.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core import security
from app.core.db import get_session_factory
from app.core.logging import log
from app.models.workspace import Membership
from app.realtime.manager import (
    broadcast,
    conversation_topic,
    manager,
    user_topic,
    workspace_topic,
)

logger = log("ws.app")

router = APIRouter()

# Presence: member_id -> last ping monotonic time; pruned lazily on reads.
_presence: dict[str, dict[str, float]] = {}
PRESENCE_TTL = 70.0


def online_members(workspace_id: str) -> list[str]:
    now = time.monotonic()
    room = _presence.get(workspace_id, {})
    stale = [uid for uid, ts in room.items() if now - ts > PRESENCE_TTL]
    for uid in stale:
        room.pop(uid, None)
    return sorted(room)


async def _mark_online(workspace_id: str, user_id: str) -> None:
    room = _presence.setdefault(workspace_id, {})
    was_online = user_id in room and (time.monotonic() - room[user_id]) <= PRESENCE_TTL
    room[user_id] = time.monotonic()
    if not was_online:
        await broadcast(
            workspace_topic(workspace_id),
            "presence.changed",
            {"user_id": user_id, "online": True},
        )


async def _mark_offline(workspace_id: str, user_id: str) -> None:
    room = _presence.get(workspace_id, {})
    room.pop(user_id, None)
    await broadcast(
        workspace_topic(workspace_id),
        "presence.changed",
        {"user_id": user_id, "online": False},
    )


@router.websocket("/ws/app")
async def app_websocket(websocket: WebSocket, token: str, workspace_id: str) -> None:
    # Authenticate before accepting.
    try:
        payload = security.decode_token(token, "access")
    except Exception:
        await websocket.close(code=4401)
        return
    user_id = payload["sub"]
    async with get_session_factory()() as session:
        membership = (
            await session.execute(
                select(Membership.id).where(
                    Membership.workspace_id == workspace_id, Membership.user_id == user_id
                )
            )
        ).scalar_one_or_none()
    if membership is None:
        await websocket.close(code=4403)
        return

    await websocket.accept()
    ws_topic = workspace_topic(workspace_id)
    u_topic = user_topic(workspace_id, user_id)
    await manager.join(ws_topic, websocket)
    await manager.join(u_topic, websocket)
    await _mark_online(workspace_id, user_id)
    await websocket.send_json(
        {"type": "presence.state", "data": {"online_user_ids": online_members(workspace_id)}}
    )

    try:
        while True:
            message: dict[str, Any] = await websocket.receive_json()
            kind = message.get("type")
            if kind == "ping":
                await _mark_online(workspace_id, user_id)
                await websocket.send_json({"type": "pong", "data": {}})
            elif kind == "typing":
                conversation_id = str(message.get("conversation_id", ""))
                if conversation_id:
                    payload_out = {
                        "conversation_id": conversation_id,
                        "user_id": user_id,
                        "is_typing": bool(message.get("is_typing", True)),
                        "source": "member",
                    }
                    await broadcast(conversation_topic(conversation_id), "typing", payload_out)
                    await broadcast(ws_topic, "typing", payload_out)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("app websocket error (user %s)", user_id)
    finally:
        await manager.leave(ws_topic, websocket)
        await manager.leave(u_topic, websocket)
        await _mark_offline(workspace_id, user_id)
