"""Agent execution models: runs, per-step traces, and approval gates.

The design ports the Claude Agent SDK's *defer + resume* state machine to a
DB-backed form (docs/research/claude-agent-sdk.md):

- `AgentRun` is one execution over a conversation. When a `require_approval` tool
  is hit the loop persists `pending_tool_call` + `messages_snapshot`, flips to
  `awaiting_approval`, and RETURNS — nothing is held in memory, so the gate
  survives process restarts. On decision the run is re-enqueued and resumed from
  the snapshot.
- `AgentStep` is the append-only trace (one row per llm_call / tool_call /
  tool_result / approval_request / approval_decision / final_reply / guardrail /
  error / handoff).
- `ApprovalRequest` is the human-in-the-loop gate row.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON, UTCDateTime, utcnow
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class AgentRunStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    HANDED_OFF = "handed_off"
    CANCELED = "canceled"


class AgentStepKind(enum.StrEnum):
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    APPROVAL_REQUEST = "approval_request"
    APPROVAL_DECISION = "approval_decision"
    FINAL_REPLY = "final_reply"
    GUARDRAIL = "guardrail"
    ERROR = "error"
    HANDOFF = "handoff"


class ApprovalStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class AgentRun(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_ws_status", "workspace_id", "status"),
        Index("ix_agent_runs_conversation", "conversation_id"),
    )

    id: Mapped[str] = pk()
    # Plain GUIDs — conversations/messages belong to another domain (no FK by contract).
    conversation_id: Mapped[str] = mapped_column(GUID, nullable=False)
    agent_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("agents.id", ondelete="CASCADE"), index=True, nullable=False
    )
    trigger_message_id: Mapped[str | None] = mapped_column(GUID)
    status: Mapped[str] = mapped_column(String(20), default=AgentRunStatus.QUEUED, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Worker lease — a reaper can reclaim runs whose lease expired mid-flight.
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    # Serialized provider ChatMessage list for pause/resume (null when not paused).
    messages_snapshot: Mapped[list[Any] | None] = mapped_column(PortableJSON)
    # {id, name, input, approval_request_id} — the deferred tool call awaiting sign-off.
    pending_tool_call: Mapped[dict[str, Any] | None] = mapped_column(PortableJSON)
    # Last search_knowledge results, for mapping [n] citation markers in the reply.
    citations: Mapped[list[Any]] = mapped_column(PortableJSON, default=list, nullable=False)
    reply_message_id: Mapped[str | None] = mapped_column(GUID)


class AgentStep(WorkspaceScopedMixin, Base):
    __tablename__ = "agent_steps"
    __table_args__ = (Index("ix_agent_steps_run_ord", "run_id", "ord"),)

    id: Mapped[str] = pk()
    run_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    ord: Mapped[int] = mapped_column(Integer, nullable=False)  # dense order within the run
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # AgentStepKind values
    name: Mapped[str | None] = mapped_column(String(120))  # tool name / model key
    input: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, nullable=False)


class ApprovalRequest(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (Index("ix_approval_requests_ws_status", "workspace_id", "status"),)

    id: Mapped[str] = pk()
    run_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True, nullable=False
    )
    conversation_id: Mapped[str] = mapped_column(GUID, nullable=False)
    agent_id: Mapped[str] = mapped_column(GUID, nullable=False)
    tool_key: Mapped[str] = mapped_column(String(120), nullable=False)
    tool_input: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(12), default=ApprovalStatus.PENDING, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    decided_by: Mapped[str | None] = mapped_column(GUID)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    note: Mapped[str | None] = mapped_column(Text)
