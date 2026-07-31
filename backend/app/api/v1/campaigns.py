"""Campaigns API: CRUD + activate/pause for proactive outbound campaigns.

Permission model mirrors automations: reading needs automations:read, every
mutation needs automations:manage. Edits are blocked while a one-off campaign
is being dispatched (status "processing").
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import Db, Member, Principal, require_perm
from app.core.events import Actor
from app.core.permissions import Perm
from app.schemas.campaigns import CampaignCreate, CampaignOut, CampaignUpdate
from app.schemas.common import Msg
from app.services import campaigns as campaigns_service

router = APIRouter()


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


@router.get(
    "/campaigns",
    response_model=list[CampaignOut],
    dependencies=[Depends(require_perm(Perm.AUTOMATIONS_READ))],
)
async def list_campaigns(principal: Member, session: Db) -> list[CampaignOut]:
    campaigns = await campaigns_service.list_campaigns(session, principal.workspace.id)
    return [CampaignOut.model_validate(campaign) for campaign in campaigns]


@router.post(
    "/campaigns",
    response_model=CampaignOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.AUTOMATIONS_MANAGE))],
)
async def create_campaign(body: CampaignCreate, principal: Member, session: Db) -> CampaignOut:
    campaign = await campaigns_service.create_campaign(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        title=body.title,
        message=body.message,
        campaign_type=body.campaign_type,
        inbox_id=body.inbox_id,
        sender_user_id=body.sender_user_id,
        audience=body.audience,
        trigger_rules=body.trigger_rules,
        scheduled_at=body.scheduled_at,
        enabled=body.enabled,
    )
    return CampaignOut.model_validate(campaign)


@router.get(
    "/campaigns/{campaign_id}",
    response_model=CampaignOut,
    dependencies=[Depends(require_perm(Perm.AUTOMATIONS_READ))],
)
async def get_campaign(campaign_id: str, principal: Member, session: Db) -> CampaignOut:
    campaign = await campaigns_service.get_campaign(session, principal.workspace.id, campaign_id)
    return CampaignOut.model_validate(campaign)


@router.patch(
    "/campaigns/{campaign_id}",
    response_model=CampaignOut,
    dependencies=[Depends(require_perm(Perm.AUTOMATIONS_MANAGE))],
)
async def update_campaign(
    campaign_id: str, body: CampaignUpdate, principal: Member, session: Db
) -> CampaignOut:
    campaign = await campaigns_service.update_campaign(
        session,
        principal.workspace.id,
        campaign_id,
        actor=_actor(principal),
        changes=body.model_dump(exclude_unset=True),
    )
    return CampaignOut.model_validate(campaign)


@router.delete(
    "/campaigns/{campaign_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.AUTOMATIONS_MANAGE))],
)
async def delete_campaign(campaign_id: str, principal: Member, session: Db) -> Msg:
    await campaigns_service.delete_campaign(
        session, principal.workspace.id, campaign_id, actor=_actor(principal)
    )
    return Msg(message="Campaign deleted")


@router.post(
    "/campaigns/{campaign_id}/activate",
    response_model=CampaignOut,
    dependencies=[Depends(require_perm(Perm.AUTOMATIONS_MANAGE))],
)
async def activate_campaign(campaign_id: str, principal: Member, session: Db) -> CampaignOut:
    campaign = await campaigns_service.activate_campaign(
        session, principal.workspace.id, campaign_id, actor=_actor(principal)
    )
    return CampaignOut.model_validate(campaign)


@router.post(
    "/campaigns/{campaign_id}/pause",
    response_model=CampaignOut,
    dependencies=[Depends(require_perm(Perm.AUTOMATIONS_MANAGE))],
)
async def pause_campaign(campaign_id: str, principal: Member, session: Db) -> CampaignOut:
    campaign = await campaigns_service.pause_campaign(
        session, principal.workspace.id, campaign_id, actor=_actor(principal)
    )
    return CampaignOut.model_validate(campaign)
