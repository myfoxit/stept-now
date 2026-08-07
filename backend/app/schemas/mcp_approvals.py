"""Schemas for MCP tool-call approvals (per-agent MCP channel, ask_in_stept mode)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


class McpApprovalOut(BaseModel):
    id: str
    agent_id: str
    agent_name: str | None = None
    tool_key: str
    tool_input: dict[str, Any]
    status: str
    requested_at: datetime
    expires_at: datetime
    decided_by: str | None
    decided_at: datetime | None
    created_at: datetime


class McpApprovalDecideRequest(BaseModel):
    decision: Literal["approve", "deny"]
