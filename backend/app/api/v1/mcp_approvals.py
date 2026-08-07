"""Workspace MCP approvals REST: list pending write-tool calls, approve/deny.

These are approvals for tool calls arriving over the per-agent MCP channel
(``approval_mode = "ask_in_stept"``). Unlike ``/ai/approvals`` there is no run
to resume — the external LLM retries the same call and finds the decision.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import Db, Principal, require_perm
from app.core.events import Actor
from app.core.permissions import Perm
from app.models.agent import Agent
from app.models.mcp_approval import McpToolApproval
from app.schemas.mcp_approvals import McpApprovalDecideRequest, McpApprovalOut
from app.services import audit
from app.services import mcp_approvals as approvals_service

router = APIRouter()

Approver = Annotated[Principal, Depends(require_perm(Perm.AI_APPROVE))]


def _out(approval: McpToolApproval, agent_name: str | None) -> McpApprovalOut:
    return McpApprovalOut(
        id=approval.id,
        agent_id=approval.agent_id,
        agent_name=agent_name,
        tool_key=approval.tool_key,
        tool_input=approval.tool_input,
        status=approval.status,
        requested_at=approval.requested_at,
        expires_at=approval.expires_at,
        decided_by=approval.decided_by,
        decided_at=approval.decided_at,
        created_at=approval.created_at,
    )


async def _agent_names(session: AsyncSession, agent_ids: set[str]) -> dict[str, str]:
    if not agent_ids:
        return {}
    rows = (
        await session.execute(select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids)))
    ).all()
    return {agent_id: name for agent_id, name in rows}


@router.get("/mcp-approvals", response_model=list[McpApprovalOut])
async def list_mcp_approvals(
    principal: Approver,
    session: Db,
    status: str = Query(default="pending"),
) -> Any:
    """Newest-first approvals for this workspace; ``status=all`` for history.

    Overdue pending rows are lazily flipped to ``expired`` on the way through.
    """
    rows = await approvals_service.list_approvals(session, principal.workspace.id, status=status)
    names = await _agent_names(session, {row.agent_id for row in rows})
    return [_out(row, names.get(row.agent_id)) for row in rows]


@router.post("/mcp-approvals/{approval_id}/decide", response_model=McpApprovalOut)
async def decide_mcp_approval(
    approval_id: str, body: McpApprovalDecideRequest, principal: Approver, session: Db
) -> Any:
    """Approve or deny; the external LLM's retry with the same params finds it."""
    approval = await approvals_service.decide(
        session,
        principal.workspace.id,
        approval_id,
        decision=body.decision,
        decided_by=principal.actor_id,
    )
    await audit.record(
        session,
        principal.workspace.id,
        actor=Actor(type=principal.kind, id=principal.actor_id, label=principal.label),
        action="mcp_approval.decide",
        target_type="mcp_approval",
        target_id=approval.id,
        meta={"tool": approval.tool_key, "agent_id": approval.agent_id, "status": approval.status},
    )
    agent = await session.get(Agent, approval.agent_id)
    return _out(approval, agent.name if agent is not None else None)
