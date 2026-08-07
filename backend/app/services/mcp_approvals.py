"""MCP tool-call approvals (``ask_in_stept`` mode of the per-agent MCP channel).

An external LLM has no run to pause, so the gate is a lookup keyed by
``(api_key, agent, tool, params_hash)``: the first call parks a pending row and
returns ``APPROVAL_REQUIRED``; after a teammate decides in Stept, the client
retries the *same* call and the retry finds the decision. A decision governs
until ``expires_at`` (old-repo parity: approved rows are re-usable, not
one-shot), after which the row is dead and a fresh call mints a new approval.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.core.errors import ConflictError, NotFoundError
from app.models.mcp_approval import McpApprovalStatus, McpToolApproval

APPROVAL_TTL_HOURS = 24
LIST_LIMIT = 200


def params_hash(params: dict[str, Any] | None) -> str:
    """Stable hash of the tool arguments for approval lookup.

    Hashes EVERY argument. The old repo excluded underscore-prefixed keys as
    "internal context", which is true on a surface where the server injects
    them — but here the arguments arrive verbatim from the MCP client and go
    verbatim to the executor, which happily substitutes `{_x}` into an action's
    URL or body. Excluding them let a caller reuse a human's approval for a
    materially different call. What is approved must be what runs.
    """
    canonical = json.dumps(params or {}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def get_or_create(
    session: AsyncSession,
    workspace_id: str,
    *,
    api_key_id: str,
    agent_id: str,
    tool_key: str,
    params: dict[str, Any] | None,
) -> McpToolApproval:
    """Return the row governing this exact call: a live decision, the pending
    row already waiting on it, or a freshly minted pending row.

    Expired *pending* rows are flipped to ``expired`` on the way through;
    expired decisions simply stop counting (history is not rewritten).
    """
    now = utcnow()
    digest = params_hash(params)
    rows = (
        (
            await session.execute(
                select(McpToolApproval)
                .where(
                    McpToolApproval.workspace_id == workspace_id,
                    McpToolApproval.api_key_id == api_key_id,
                    McpToolApproval.agent_id == agent_id,
                    McpToolApproval.tool_key == tool_key,
                    McpToolApproval.params_hash == digest,
                )
                .order_by(McpToolApproval.requested_at.desc())
            )
        )
        .scalars()
        .all()
    )
    active: McpToolApproval | None = None
    for row in rows:
        if row.expires_at <= now:
            if row.status == McpApprovalStatus.PENDING:
                row.status = McpApprovalStatus.EXPIRED
                row.decided_at = now
            continue
        if active is None and row.status in (
            McpApprovalStatus.PENDING,
            McpApprovalStatus.APPROVED,
            McpApprovalStatus.DENIED,
        ):
            active = row
    if active is not None:
        await session.flush()
        return active

    approval = McpToolApproval(
        workspace_id=workspace_id,
        api_key_id=api_key_id,
        agent_id=agent_id,
        tool_key=tool_key,
        # Store what will actually run, so the approver reviews the real call.
        tool_input=dict(params or {}),
        params_hash=digest,
        status=McpApprovalStatus.PENDING,
        requested_at=now,
        expires_at=now + timedelta(hours=APPROVAL_TTL_HOURS),
    )
    session.add(approval)
    await session.flush()
    return approval


async def expire_overdue(session: AsyncSession, workspace_id: str) -> int:
    """Lazily flip overdue pending approvals to expired. Returns the count."""
    now = utcnow()
    overdue = (
        (
            await session.execute(
                select(McpToolApproval).where(
                    McpToolApproval.workspace_id == workspace_id,
                    McpToolApproval.status == McpApprovalStatus.PENDING,
                    McpToolApproval.expires_at <= now,
                )
            )
        )
        .scalars()
        .all()
    )
    for approval in overdue:
        approval.status = McpApprovalStatus.EXPIRED
        approval.decided_at = now
    if overdue:
        await session.flush()
    return len(overdue)


async def list_approvals(
    session: AsyncSession, workspace_id: str, *, status: str = "pending"
) -> list[McpToolApproval]:
    """Newest-first listing (expiry-flipped first); ``status="all"`` disables the filter."""
    await expire_overdue(session, workspace_id)
    query = select(McpToolApproval).where(McpToolApproval.workspace_id == workspace_id)
    if status and status != "all":
        query = query.where(McpToolApproval.status == status)
    rows = (
        (
            await session.execute(
                query.order_by(McpToolApproval.requested_at.desc()).limit(LIST_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


async def decide(
    session: AsyncSession,
    workspace_id: str,
    approval_id: str,
    *,
    decision: str,
    decided_by: str | None,
) -> McpToolApproval:
    """Record a human decision. 404 cross-workspace, 409 already resolved/expired."""
    approval = await session.get(McpToolApproval, approval_id)
    if approval is None or approval.workspace_id != workspace_id:
        raise NotFoundError("Approval not found")
    if approval.status != McpApprovalStatus.PENDING:
        raise ConflictError(f"Approval already {approval.status}")
    now = utcnow()
    if approval.expires_at <= now:
        approval.status = McpApprovalStatus.EXPIRED
        approval.decided_at = now
        await session.flush()
        raise ConflictError("Approval expired")
    approval.status = (
        McpApprovalStatus.APPROVED if decision == "approve" else McpApprovalStatus.DENIED
    )
    approval.decided_by = decided_by
    approval.decided_at = now
    await session.flush()
    return approval
