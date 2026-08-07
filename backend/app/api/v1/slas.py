"""SLA policies API: policy CRUD + per-conversation apply/remove/state.

Reading is open to anyone who can read conversations; managing policies needs
automations:manage; applying/removing a conversation's SLA needs
conversations:manage.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.billing import require_plan_feature
from app.core.deps import Db, Member, Principal, require_perm
from app.core.events import Actor
from app.core.permissions import Perm
from app.models.sla import SlaPolicy
from app.schemas.common import Msg
from app.schemas.slas import (
    ConversationSlaOut,
    SlaApplyRequest,
    SlaEventOut,
    SlaPolicyCreate,
    SlaPolicyOut,
    SlaPolicyUpdate,
)
from app.services import conversations as conversations_service
from app.services import slas as slas_service
from app.services.billing import Feature

router = APIRouter()


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


@router.get(
    "/slas",
    response_model=list[SlaPolicyOut],
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def list_sla_policies(principal: Member, session: Db) -> list[SlaPolicyOut]:
    policies = await slas_service.list_policies(session, principal.workspace.id)
    return [SlaPolicyOut.model_validate(policy) for policy in policies]


@router.post(
    "/slas",
    response_model=SlaPolicyOut,
    status_code=201,
    dependencies=[
        Depends(require_perm(Perm.AUTOMATIONS_MANAGE)),
        Depends(require_plan_feature(Feature.SLA_MANAGEMENT)),
    ],
)
async def create_sla_policy(body: SlaPolicyCreate, principal: Member, session: Db) -> SlaPolicyOut:
    policy = await slas_service.create_policy(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        name=body.name,
        description=body.description,
        first_response_minutes=body.first_response_minutes,
        next_response_minutes=body.next_response_minutes,
        resolution_minutes=body.resolution_minutes,
        only_during_business_hours=body.only_during_business_hours,
    )
    return SlaPolicyOut.model_validate(policy)


@router.patch(
    "/slas/{policy_id}",
    response_model=SlaPolicyOut,
    dependencies=[
        Depends(require_perm(Perm.AUTOMATIONS_MANAGE)),
        Depends(require_plan_feature(Feature.SLA_MANAGEMENT)),
    ],
)
async def update_sla_policy(
    policy_id: str, body: SlaPolicyUpdate, principal: Member, session: Db
) -> SlaPolicyOut:
    policy = await slas_service.update_policy(
        session,
        principal.workspace.id,
        policy_id,
        actor=_actor(principal),
        changes=body.model_dump(exclude_unset=True),
    )
    return SlaPolicyOut.model_validate(policy)


@router.delete(
    "/slas/{policy_id}",
    response_model=Msg,
    dependencies=[
        Depends(require_perm(Perm.AUTOMATIONS_MANAGE)),
        Depends(require_plan_feature(Feature.SLA_MANAGEMENT)),
    ],
)
async def delete_sla_policy(policy_id: str, principal: Member, session: Db) -> Msg:
    await slas_service.delete_policy(
        session, principal.workspace.id, policy_id, actor=_actor(principal)
    )
    return Msg(message="SLA policy deleted")


# ---------------------------------------------------------------------------
# per-conversation SLA
# ---------------------------------------------------------------------------


async def _conversation_sla(
    session: AsyncSession, workspace_id: str, conversation_id: str
) -> ConversationSlaOut:
    applied = await slas_service.get_applied(session, workspace_id, conversation_id)
    if applied is None:
        return ConversationSlaOut()
    policy = await session.get(SlaPolicy, applied.sla_policy_id)
    events = await slas_service.list_events(session, applied.id)
    return ConversationSlaOut(
        policy=SlaPolicyOut.model_validate(policy) if policy is not None else None,
        status=applied.status,
        events=[SlaEventOut.model_validate(event) for event in events],
    )


@router.get(
    "/conversations/{conversation_id}/sla",
    response_model=ConversationSlaOut,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def get_conversation_sla(
    conversation_id: str, principal: Member, session: Db
) -> ConversationSlaOut:
    conversation = await conversations_service.get_conversation(
        session, principal.workspace.id, conversation_id
    )
    return await _conversation_sla(session, principal.workspace.id, conversation.id)


@router.put(
    "/conversations/{conversation_id}/sla",
    response_model=ConversationSlaOut,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_MANAGE))],
)
async def put_conversation_sla(
    conversation_id: str, body: SlaApplyRequest, principal: Member, session: Db
) -> ConversationSlaOut:
    """Apply (or replace) the conversation's SLA policy; null removes it."""
    conversation = await conversations_service.get_conversation(
        session, principal.workspace.id, conversation_id
    )
    if body.sla_policy_id is None:
        await slas_service.remove_sla(
            session, principal.workspace.id, conversation, actor=_actor(principal)
        )
    else:
        await slas_service.apply_sla(
            session,
            principal.workspace.id,
            conversation,
            body.sla_policy_id,
            actor=_actor(principal),
        )
    return await _conversation_sla(session, principal.workspace.id, conversation.id)
