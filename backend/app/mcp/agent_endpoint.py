"""Per-agent MCP channel: hand-rolled JSON-RPC at POST /mcp/agents/{agent_id}.

FILLED BY WAVE AGENT BE-C — semantics in docs/MCP-CONTRACTS.md (exposure rules,
approval modes, error codes). Keep ``router`` module-level: app/main.py includes
it BEFORE the /mcp mount so these routes win over the mounted server.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
