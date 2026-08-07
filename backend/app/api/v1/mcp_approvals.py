"""Workspace MCP approvals REST: list pending write-tool calls, approve/deny.

FILLED BY WAVE AGENT BE-C — see docs/MCP-CONTRACTS.md (perm AI_APPROVE, 409 on
already-resolved, expiry flip on read).
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()
