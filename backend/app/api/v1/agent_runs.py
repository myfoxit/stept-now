"""Agent run trace endpoints (ai:read): a filterable run list + full step trace."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from app.core.deps import Db, Principal, require_perm
from app.core.errors import NotFoundError
from app.core.pagination import OffsetPage
from app.core.permissions import Perm
from app.models.agent import Agent
from app.models.agent_run import AgentRun, AgentStep
from app.schemas.agents import AgentRunDetail, AgentRunOut, AgentStepOut

router = APIRouter()

Reader = Annotated[Principal, Depends(require_perm(Perm.AI_READ))]


def _run_out(run: AgentRun, agent_name: str | None) -> AgentRunOut:
    return AgentRunOut(
        id=run.id,
        conversation_id=run.conversation_id,
        agent_id=run.agent_id,
        agent_name=agent_name,
        trigger_message_id=run.trigger_message_id,
        status=run.status,
        error=run.error,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        started_at=run.started_at,
        finished_at=run.finished_at,
        reply_message_id=run.reply_message_id,
        created_at=run.created_at,
    )


def _step_out(step: AgentStep) -> AgentStepOut:
    return AgentStepOut(
        id=step.id,
        ord=step.ord,
        kind=step.kind,
        name=step.name,
        input=step.input,
        output=step.output,
        latency_ms=step.latency_ms,
        input_tokens=step.input_tokens,
        output_tokens=step.output_tokens,
        created_at=step.created_at,
    )


@router.get("/ai/runs", response_model=OffsetPage[AgentRunOut])
async def list_runs(
    principal: Reader,
    session: Db,
    agent_id: str | None = None,
    conversation_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> Any:
    filters = [AgentRun.workspace_id == principal.workspace.id]
    if agent_id:
        filters.append(AgentRun.agent_id == agent_id)
    if conversation_id:
        filters.append(AgentRun.conversation_id == conversation_id)
    if status:
        filters.append(AgentRun.status == status)

    total = (
        await session.execute(select(func.count()).select_from(AgentRun).where(*filters))
    ).scalar_one()
    rows = (
        await session.execute(
            select(AgentRun, Agent.name)
            .join(Agent, Agent.id == AgentRun.agent_id)
            .where(*filters)
            .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    items = [_run_out(run, agent_name) for run, agent_name in rows]
    return OffsetPage[AgentRunOut](items=items, total=total, limit=limit, offset=offset)


@router.get("/ai/runs/{run_id}", response_model=AgentRunDetail)
async def get_run(run_id: str, principal: Reader, session: Db) -> Any:
    run = await session.get(AgentRun, run_id)
    if run is None or run.workspace_id != principal.workspace.id:
        raise NotFoundError("Agent run not found")
    agent = await session.get(Agent, run.agent_id)
    steps = (
        (
            await session.execute(
                select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.ord)
            )
        )
        .scalars()
        .all()
    )
    return AgentRunDetail(
        run=_run_out(run, agent.name if agent is not None else None),
        steps=[_step_out(step) for step in steps],
    )
