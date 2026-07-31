"""Widget websocket (visitor side) — implemented in Wave 2 with the widget API.

Auth: widget contact JWT. Rooms: conv:{conversation_id} per open conversation.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
