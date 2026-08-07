"""Extension remote-drive WebSocket gateway: /ws/extension.

FILLED BY WAVE AGENT BE-B — protocol in docs/MCP-CONTRACTS.md (auth via the
30-day extension token, per-workspace device registry, ctrl_id-correlated
request/response, supersede-on-reconnect close code 4000). Keep ``router``
module-level: app/main.py includes it next to the other realtime routers.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
