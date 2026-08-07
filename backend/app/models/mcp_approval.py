"""Approval requests for write tools called over the per-agent MCP channel.

Separate from ``ApprovalRequest`` on purpose: that one is a paused step inside
an agent *run* (FK agent_runs). An MCP tool call has no run — the external LLM
retries the call after a human approves here, matching the key's
``approval_mode = "ask_in_stept"``. The (api_key, tool, params_hash) triple lets
the retry find its earlier decision instead of minting approvals in a loop.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON, UTCDateTime
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class McpApprovalStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class McpToolApproval(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "mcp_tool_approvals"
    __table_args__ = (
        Index("ix_mcp_tool_approvals_lookup", "api_key_id", "tool_key", "params_hash", "status"),
        Index("ix_mcp_tool_approvals_ws_status", "workspace_id", "status"),
    )

    id: Mapped[str] = pk()
    api_key_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("api_keys.id", ondelete="CASCADE"), index=True, nullable=False
    )
    agent_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("agents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    tool_key: Mapped[str] = mapped_column(String(120), nullable=False)
    tool_input: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # sha256 of the canonical params JSON (sorted keys, "_"-prefixed keys stripped).
    params_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(12), default=McpApprovalStatus.PENDING, nullable=False
    )
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    decided_by: Mapped[str | None] = mapped_column(GUID)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
