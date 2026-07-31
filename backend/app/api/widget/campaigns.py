"""Widget public campaigns API.

GET lists the ongoing campaigns for one widget inbox (resolved from the public
`widget_key` query param, like tours) — no token required; the widget evaluates
`trigger_rules` (url_pattern / time_on_page_seconds) client-side. POST trigger
requires the signed visitor token (`widget_auth`) and creates the proactive
conversation with Chatwoot's fresh-visitor semantics (skip when the visitor
already has any conversation, or the campaign already fired for the contact).
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.widget.deps import WidgetAuth
from app.core.deps import Db
from app.core.errors import ConflictError, NotFoundError
from app.models.campaign import Campaign, CampaignStatus, CampaignType
from app.models.inbox import Inbox
from app.schemas.campaigns import WidgetCampaignOut, WidgetCampaignTriggerOut
from app.services import campaigns as campaigns_service

router = APIRouter()


async def _resolve(session: Db, widget_key: str) -> Inbox:
    """Resolve the widget inbox from its public embed key (mirrors tours)."""
    inbox = (
        await session.execute(select(Inbox).where(Inbox.widget_key == widget_key))
    ).scalar_one_or_none()
    if inbox is None:
        raise NotFoundError("Unknown widget key")
    return inbox


@router.get("/campaigns", response_model=list[WidgetCampaignOut])
async def list_widget_campaigns(session: Db, widget_key: str) -> list[WidgetCampaignOut]:
    inbox = await _resolve(session, widget_key)
    items = await campaigns_service.list_widget_campaigns(session, inbox)
    return [WidgetCampaignOut.model_validate(item) for item in items]


@router.post("/campaigns/{campaign_id}/trigger", response_model=WidgetCampaignTriggerOut)
async def trigger_campaign(
    campaign_id: str, principal: WidgetAuth, session: Db
) -> WidgetCampaignTriggerOut:
    campaign = await session.get(Campaign, campaign_id)
    if (
        campaign is None
        or campaign.workspace_id != principal.workspace.id
        or campaign.inbox_id != principal.inbox.id
    ):
        raise NotFoundError("Campaign not found")
    if (
        not campaign.enabled
        or campaign.status != CampaignStatus.ACTIVE.value
        or campaign.campaign_type != CampaignType.ONGOING.value
    ):
        raise ConflictError("Campaign is not active")
    conversation = await campaigns_service.trigger_ongoing(
        session,
        campaign,
        contact=principal.contact,
        contact_inbox=principal.contact_inbox,
        inbox=principal.inbox,
    )
    if conversation is None:
        return WidgetCampaignTriggerOut(skipped=True)
    return WidgetCampaignTriggerOut(conversation_id=conversation.id)
