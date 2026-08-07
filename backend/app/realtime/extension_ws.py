"""Extension remote-drive WebSocket gateway: ``/ws/extension``.

``/ws/extension?token=<extension-token>&device_id=<id>&name=<label>`` — the
Chrome extension's outbound socket (protocol in docs/MCP-CONTRACTS.md). Auth
happens BEFORE accept, mirroring the other realtime routes: the 30-day
``extension`` token (claims ws+sub) must decode (else close 4401) and the
subject must still be a member of the workspace (else 4403) — a revoked
member's long-lived token is refused on every reconnect.

The socket registers in ``app.services.remote_drive.gateway``, which owns it
for as long as it lives: the API runs several uvicorn workers, so a socket is
reachable only from the process that accepted it (the gateway routes ops to
that process over pub/sub). A reconnect with the same ``device_id`` supersedes
the old socket (server close code 4000 "superseded") even when it lands on a
different worker; the superseded socket's late disconnect is identity-checked
in the gateway so it never tears down the replacement.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core import security
from app.core.db import get_session_factory
from app.core.logging import log
from app.core.permissions import Perm, resolve_permissions
from app.models.workspace import Membership
from app.services.remote_drive import gateway

logger = log("ws.extension")

router = APIRouter()


@router.websocket("/ws/extension")
async def extension_websocket(
    websocket: WebSocket, token: str, device_id: str, name: str = "Chrome"
) -> None:
    # Authenticate before accepting the socket.
    try:
        payload = security.decode_token(token, "extension")
    except Exception:
        await websocket.close(code=4401)
        return
    workspace_id = payload.get("ws")
    user_id = payload.get("sub")
    if not isinstance(workspace_id, str) or not isinstance(user_id, str):
        await websocket.close(code=4401)
        return

    async with get_session_factory()() as session:
        membership = (
            await session.execute(
                select(Membership).where(
                    Membership.workspace_id == workspace_id, Membership.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        # Re-check the permission the token was minted under, not just
        # membership: /tours/extension-token requires tours:manage, so a member
        # demoted to viewer must lose the browser their 30-day token still
        # opens, on their next reconnect.
        if membership is not None:
            custom = (
                list(membership.custom_role.permissions)
                if membership.role == "custom" and membership.custom_role is not None
                else None
            )
            permitted = Perm.TOURS_MANAGE in resolve_permissions(membership.role, custom)
        else:
            permitted = False
    if not permitted:
        await websocket.close(code=4403)
        return

    await websocket.accept()

    # The extension picks its own device_id, so the slot is namespaced by the
    # authenticated user. Without this any member could take over a colleague's
    # slot just by reusing their device_id: the victim's socket is closed as
    # "superseded" and subsequent drive ops are delivered to the attacker's
    # browser instead.
    slot_id = f"{user_id}:{device_id}"

    async def send(message: dict[str, Any]) -> None:
        await websocket.send_json(message)

    async def close(code: int, reason: str) -> None:
        await websocket.close(code=code, reason=reason)

    peer = await gateway.register(workspace_id, slot_id, user_id, name, send, close=close)
    try:
        while True:
            message: dict[str, Any] = await websocket.receive_json()
            if isinstance(message, dict):
                await gateway.handle_message(workspace_id, slot_id, message)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("extension websocket error (device %s)", device_id)
    finally:
        # Identity-checked in the gateway: after a supersede the registry
        # already points at the replacement, so this old socket's teardown
        # (which fires AFTER the new one registered) is a no-op.
        gateway.unregister(peer)
