"""AI agents, custom actions, sandbox test, and reply copilot.

ai:read for agent GETs, ai:manage for agent/action mutations + the sandbox;
copilot is conversations:write. Custom-action header VALUES are write-only —
responses expose only header names.

The side-effect import of ``app.agents.engine`` (loaded by the router registry at
app build) registers the engine's @on event triggers and its queue task.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import copilot, engine, writer
from app.agents import engine as _agent_engine  # noqa: F401 — registers @on triggers + task
from app.agents.tools import execute_custom_action
from app.core.deps import Db, Principal, require_perm
from app.core.errors import NotFoundError
from app.core.events import Actor
from app.core.permissions import Perm
from app.core.security import encrypt_secret
from app.models.agent import Agent, CustomAction
from app.models.agent_run import AgentRun, AgentStep, ApprovalRequest
from app.models.conversation import Conversation
from app.schemas.agents import (
    ActionTestRequest,
    ActionTestResult,
    AgentCreate,
    AgentOut,
    AgentTestRequest,
    AgentTestResult,
    AgentUpdate,
    Citation,
    CopilotRequest,
    CopilotResult,
    CustomActionCreate,
    CustomActionOut,
    CustomActionUpdate,
    WriteRequest,
    WriteResult,
)
from app.schemas.common import Msg
from app.services import audit

router = APIRouter()

Reader = Annotated[Principal, Depends(require_perm(Perm.AI_READ))]
Manager = Annotated[Principal, Depends(require_perm(Perm.AI_MANAGE))]
Writer = Annotated[Principal, Depends(require_perm(Perm.CONVERSATIONS_WRITE))]
Author = Annotated[Principal, Depends(require_perm(Perm.KNOWLEDGE_WRITE))]


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


def _agent_out(agent: Agent) -> AgentOut:
    return AgentOut(
        id=agent.id,
        name=agent.name,
        description=agent.description,
        avatar_emoji=agent.avatar_emoji,
        status=agent.status,
        model_ref=agent.model_ref,
        system_prompt=agent.system_prompt,
        temperature=agent.temperature,
        settings=agent.settings,
        tools=agent.tools,
        created_at=agent.created_at,
        updated_at=agent.updated_at,
    )


def _action_out(action: CustomAction) -> CustomActionOut:
    return CustomActionOut(
        id=action.id,
        name=action.name,
        description=action.description,
        method=action.method,
        url=action.url,
        header_names=sorted((action.headers or {}).keys()),
        body_template=action.body_template,
        params_schema=action.params_schema,
        timeout_s=action.timeout_s,
        created_at=action.created_at,
        updated_at=action.updated_at,
    )


async def _get_agent(session: AsyncSession, workspace_id: str, agent_id: str) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.workspace_id != workspace_id:
        raise NotFoundError("Agent not found")
    return agent


async def _get_action(session: AsyncSession, workspace_id: str, action_id: str) -> CustomAction:
    action = await session.get(CustomAction, action_id)
    if action is None or action.workspace_id != workspace_id:
        raise NotFoundError("Custom action not found")
    return action


# ---------------------------------------------------------------------------
# agents CRUD
# ---------------------------------------------------------------------------


@router.get("/ai/agents", response_model=list[AgentOut])
async def list_agents(principal: Reader, session: Db) -> Any:
    rows = (
        (
            await session.execute(
                select(Agent)
                .where(Agent.workspace_id == principal.workspace.id)
                .order_by(Agent.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [_agent_out(agent) for agent in rows]


@router.post("/ai/agents", response_model=AgentOut, status_code=201)
async def create_agent(body: AgentCreate, principal: Manager, session: Db) -> Any:
    agent = Agent(
        workspace_id=principal.workspace.id,
        name=body.name.strip(),
        description=body.description,
        avatar_emoji=body.avatar_emoji,
        status=body.status,
        model_ref=body.model_ref,
        system_prompt=body.system_prompt,
        temperature=body.temperature,
        settings=body.settings.model_dump(),
        tools=[tool.model_dump() for tool in body.tools],
    )
    session.add(agent)
    await session.flush()
    await audit.record(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        action="agent.create",
        target_type="agent",
        target_id=agent.id,
        meta={"name": agent.name},
    )
    return _agent_out(agent)


@router.get("/ai/agents/{agent_id}", response_model=AgentOut)
async def get_agent(agent_id: str, principal: Reader, session: Db) -> Any:
    return _agent_out(await _get_agent(session, principal.workspace.id, agent_id))


@router.patch("/ai/agents/{agent_id}", response_model=AgentOut)
async def update_agent(agent_id: str, body: AgentUpdate, principal: Manager, session: Db) -> Any:
    agent = await _get_agent(session, principal.workspace.id, agent_id)
    fields = body.model_fields_set
    if "name" in fields and body.name is not None:
        agent.name = body.name.strip()
    if "description" in fields:
        agent.description = body.description
    if "avatar_emoji" in fields:
        agent.avatar_emoji = body.avatar_emoji
    if "status" in fields and body.status is not None:
        agent.status = body.status
    if "model_ref" in fields:
        agent.model_ref = body.model_ref
    if "system_prompt" in fields and body.system_prompt is not None:
        agent.system_prompt = body.system_prompt
    if "temperature" in fields:
        agent.temperature = body.temperature
    if "settings" in fields and body.settings is not None:
        agent.settings = body.settings.model_dump()
    if "tools" in fields and body.tools is not None:
        agent.tools = [tool.model_dump() for tool in body.tools]
    await session.flush()
    await audit.record(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        action="agent.update",
        target_type="agent",
        target_id=agent.id,
        meta={"name": agent.name},
    )
    return _agent_out(agent)


@router.delete("/ai/agents/{agent_id}", response_model=Msg)
async def delete_agent(agent_id: str, principal: Manager, session: Db) -> Any:
    agent = await _get_agent(session, principal.workspace.id, agent_id)
    run_ids = select(AgentRun.id).where(AgentRun.agent_id == agent.id)
    # Explicit cleanup — SQLite does not enforce ON DELETE CASCADE.
    await session.execute(delete(AgentStep).where(AgentStep.run_id.in_(run_ids)))
    await session.execute(delete(ApprovalRequest).where(ApprovalRequest.run_id.in_(run_ids)))
    await session.execute(delete(AgentRun).where(AgentRun.agent_id == agent.id))
    await session.delete(agent)
    await session.flush()
    await audit.record(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        action="agent.delete",
        target_type="agent",
        target_id=agent_id,
        meta={"name": agent.name},
    )
    return Msg(message="Agent deleted")


# ---------------------------------------------------------------------------
# sandbox test
# ---------------------------------------------------------------------------


@router.post("/ai/agents/{agent_id}/test", response_model=AgentTestResult)
async def test_agent(agent_id: str, body: AgentTestRequest, principal: Manager, session: Db) -> Any:
    agent = await _get_agent(session, principal.workspace.id, agent_id)
    history: list[tuple[str, str]] = [(str(item.role), item.content) for item in body.history]
    result = await engine.run_sandbox(session, agent, message=body.message, history=history)
    return AgentTestResult(
        reply=result.reply,
        status=result.status,
        steps=[_step_out(step) for step in result.steps],
        citations=[Citation(**c) for c in result.citations],
    )


def _step_out(step: AgentStep) -> Any:
    from app.schemas.agents import AgentStepOut

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


# ---------------------------------------------------------------------------
# custom actions
# ---------------------------------------------------------------------------


@router.get("/ai/actions", response_model=list[CustomActionOut])
async def list_actions(principal: Manager, session: Db) -> Any:
    rows = (
        (
            await session.execute(
                select(CustomAction)
                .where(CustomAction.workspace_id == principal.workspace.id)
                .order_by(CustomAction.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [_action_out(action) for action in rows]


@router.post("/ai/actions", response_model=CustomActionOut, status_code=201)
async def create_action(body: CustomActionCreate, principal: Manager, session: Db) -> Any:
    action = CustomAction(
        workspace_id=principal.workspace.id,
        name=body.name,
        description=body.description,
        method=body.method.upper(),
        url=body.url,
        headers={key: encrypt_secret(value) for key, value in body.headers.items()},
        body_template=body.body_template,
        params_schema=body.params_schema,
        timeout_s=body.timeout_s,
    )
    session.add(action)
    await session.flush()
    await audit.record(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        action="agent_action.create",
        target_type="custom_action",
        target_id=action.id,
        meta={"name": action.name},
    )
    return _action_out(action)


@router.patch("/ai/actions/{action_id}", response_model=CustomActionOut)
async def update_action(
    action_id: str, body: CustomActionUpdate, principal: Manager, session: Db
) -> Any:
    action = await _get_action(session, principal.workspace.id, action_id)
    fields = body.model_fields_set
    if "name" in fields and body.name is not None:
        action.name = body.name
    if "description" in fields and body.description is not None:
        action.description = body.description
    if "method" in fields and body.method is not None:
        action.method = body.method.upper()
    if "url" in fields and body.url is not None:
        action.url = body.url
    if "headers" in fields and body.headers is not None:
        action.headers = {key: encrypt_secret(value) for key, value in body.headers.items()}
    if "body_template" in fields:
        action.body_template = body.body_template
    if "params_schema" in fields and body.params_schema is not None:
        action.params_schema = body.params_schema
    if "timeout_s" in fields and body.timeout_s is not None:
        action.timeout_s = body.timeout_s
    await session.flush()
    await audit.record(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        action="agent_action.update",
        target_type="custom_action",
        target_id=action.id,
        meta={"name": action.name},
    )
    return _action_out(action)


@router.delete("/ai/actions/{action_id}", response_model=Msg)
async def delete_action(action_id: str, principal: Manager, session: Db) -> Any:
    action = await _get_action(session, principal.workspace.id, action_id)
    name = action.name
    await session.delete(action)
    await session.flush()
    await audit.record(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        action="agent_action.delete",
        target_type="custom_action",
        target_id=action_id,
        meta={"name": name},
    )
    return Msg(message="Custom action deleted")


@router.post("/ai/actions/{action_id}/test", response_model=ActionTestResult)
async def test_action(
    action_id: str, body: ActionTestRequest, principal: Manager, session: Db
) -> Any:
    action = await _get_action(session, principal.workspace.id, action_id)
    result = await execute_custom_action(action, body.params)
    return ActionTestResult(
        ok=result.ok, status=result.status, body=result.body, error=result.error
    )


# ---------------------------------------------------------------------------
# copilot
# ---------------------------------------------------------------------------


@router.post("/ai/copilot/suggest", response_model=CopilotResult)
async def copilot_suggest(body: CopilotRequest, principal: Writer, session: Db) -> Any:
    conversation = await session.get(Conversation, body.conversation_id)
    if conversation is None or conversation.workspace_id != principal.workspace.id:
        raise NotFoundError("Conversation not found")
    suggestion = await copilot.suggest_reply(session, conversation, principal.label)
    return CopilotResult(
        content=suggestion["content"],
        citations=[Citation(**c) for c in suggestion["citations"]],
    )


@router.post("/ai/write", response_model=WriteResult)
async def ai_write(body: WriteRequest, principal: Author, session: Db) -> Any:
    """Inline AI for the editor: draft, rewrite, translate, outline.

    `knowledge:write` rather than an AI permission — this is an authoring tool for
    people who already edit articles and knowledge documents, and it writes
    nothing on its own.
    """
    result = await writer.write(
        session,
        principal.workspace.id,
        command=body.command,
        prompt=body.prompt,
        context=body.context,
        language=body.language,
        ground=body.ground,
    )
    return WriteResult(
        content=result.content,
        citations=[Citation(**citation) for citation in result.citations],  # type: ignore[arg-type]
    )
