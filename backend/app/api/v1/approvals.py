"""Human-in-the-loop approval endpoints (ai:approve).

Listing lazily expires overdue pending approvals (resuming their runs as
rejected); deciding records the decision and re-enqueues the run to resume.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import engine
from app.core.deps import Db, Principal, require_perm
from app.core.errors import NotFoundError
from app.core.permissions import Perm
from app.models.agent import Agent
from app.models.agent_run import ApprovalRequest
from app.schemas.agents import ApprovalDecideRequest, ApprovalOut

router = APIRouter()

Approver = Annotated[Principal, Depends(require_perm(Perm.AI_APPROVE))]


async def _approval_out(session: AsyncSession, approval: ApprovalRequest) -> ApprovalOut:
    agent = await session.get(Agent, approval.agent_id)
    return ApprovalOut(
        id=approval.id,
        run_id=approval.run_id,
        conversation_id=approval.conversation_id,
        agent_id=approval.agent_id,
        agent_name=agent.name if agent is not None else None,
        tool_key=approval.tool_key,
        tool_input=approval.tool_input,
        status=approval.status,
        requested_at=approval.requested_at,
        expires_at=approval.expires_at,
        decided_by=approval.decided_by,
        decided_at=approval.decided_at,
        note=approval.note,
        created_at=approval.created_at,
    )


@router.get("/ai/approvals", response_model=list[ApprovalOut])
async def list_approvals(
    principal: Approver,
    session: Db,
    status: str = Query(default="pending"),
) -> Any:
    # Lazily expire overdue pending approvals (and resume their runs as rejected).
    await engine.expire_overdue(session, principal.workspace.id)
    filters = [ApprovalRequest.workspace_id == principal.workspace.id]
    if status:
        filters.append(ApprovalRequest.status == status)
    rows = (
        (
            await session.execute(
                select(ApprovalRequest)
                .where(*filters)
                .order_by(ApprovalRequest.requested_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [await _approval_out(session, approval) for approval in rows]


@router.post("/ai/approvals/{approval_id}/decide", response_model=ApprovalOut)
async def decide_approval(
    approval_id: str, body: ApprovalDecideRequest, principal: Approver, session: Db
) -> Any:
    approval = await session.get(ApprovalRequest, approval_id)
    if approval is None or approval.workspace_id != principal.workspace.id:
        raise NotFoundError("Approval request not found")
    approval = await engine.decide_approval(
        session,
        approval,
        approved=body.approved,
        decided_by=principal.actor_id,
        note=body.note,
    )
    return await _approval_out(session, approval)
