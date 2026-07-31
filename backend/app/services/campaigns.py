"""Service layer for campaigns.

Chatwoot semantics (docs/research/chatwoot-gaps.md §2):
- The inbox channel forces the type: widget → "ongoing", email/sms/whatsapp →
  "one_off" (scheduled_at defaults to now for one_off).
- One-off dispatch runs on the `campaign_dispatch` scheduler job: due campaigns
  flip to "processing" (concurrency guard), the audience resolves to contacts
  (all / segment / tag), one message goes out per contact, then "completed".
  Email sends go straight through `send_email` (no conversation); sms/whatsapp
  require an existing ContactInbox (no implicit opt-in) and create a
  conversation + outbound message delivered by the channel senders.
- Ongoing widget campaigns are listed publicly per widget key; the widget
  evaluates trigger_rules client-side and calls trigger, which only creates a
  conversation for fresh visitors (no prior conversation on the contact_inbox,
  never twice per campaign per contact).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import session_scope, utcnow
from app.core.errors import ConflictError, NotFoundError, ValidationFailure
from app.core.events import Actor, Event, EventNames, emit
from app.core.scheduler import scheduled
from app.models.campaign import Campaign, CampaignStatus, CampaignType
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.inbox import ChannelType, ContactInbox, Inbox
from app.models.message import AuthorType, MessageDirection
from app.models.segment import Segment
from app.models.tag import ContactTag
from app.models.user import User
from app.models.workspace import Workspace
from app.services import audit
from app.services import conversations as conversations_service
from app.services import email as email_service
from app.services import inboxes as inboxes_service
from app.services import segments as segments_service

ONGOING_CHANNEL_TYPES = frozenset({ChannelType.WIDGET.value})
ONE_OFF_CHANNEL_TYPES = frozenset(
    {ChannelType.EMAIL.value, ChannelType.SMS.value, ChannelType.WHATSAPP.value}
)
AUDIENCE_TYPES = frozenset({"all", "segment", "tag"})

FALLBACK_SENDER_NAME = "Campaign"


def render_message(template: str, contact: Contact | None) -> str:
    """Substitute {{contact.name}} with the contact's name (fallback "there")."""
    name = (contact.name or "").strip() if contact is not None else ""
    return template.replace("{{contact.name}}", name or "there")


def _validate_audience(audience: dict[str, Any]) -> dict[str, Any]:
    audience_type = audience.get("type", "all")
    if audience_type not in AUDIENCE_TYPES:
        raise ValidationFailure(f"Unknown audience type: {audience_type}")
    if audience_type == "segment" and not audience.get("segment_id"):
        raise ValidationFailure("Segment audiences need a segment_id")
    if audience_type == "tag" and not audience.get("tag_id"):
        raise ValidationFailure("Tag audiences need a tag_id")
    return audience


def _validate_inbox_for_type(campaign_type: str, inbox: Inbox) -> None:
    if campaign_type == CampaignType.ONGOING.value:
        if inbox.channel_type not in ONGOING_CHANNEL_TYPES:
            raise ValidationFailure("Ongoing campaigns require a widget inbox")
    elif inbox.channel_type not in ONE_OFF_CHANNEL_TYPES:
        raise ValidationFailure("One-off campaigns require an email, sms or whatsapp inbox")


# ---------------------------------------------------------------------------
# CRUD + lifecycle
# ---------------------------------------------------------------------------


async def get_campaign(session: AsyncSession, workspace_id: str, campaign_id: str) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None or campaign.workspace_id != workspace_id:
        raise NotFoundError("Campaign not found")
    return campaign


async def list_campaigns(session: AsyncSession, workspace_id: str) -> list[Campaign]:
    result = await session.execute(
        select(Campaign)
        .where(Campaign.workspace_id == workspace_id)
        .order_by(Campaign.created_at.desc(), Campaign.id.desc())
    )
    return list(result.scalars())


async def create_campaign(
    session: AsyncSession,
    workspace_id: str,
    *,
    actor: Actor,
    title: str,
    message: str,
    campaign_type: str,
    inbox_id: str,
    sender_user_id: str | None = None,
    audience: dict[str, Any] | None = None,
    trigger_rules: dict[str, Any] | None = None,
    scheduled_at: datetime | None = None,
    enabled: bool = True,
) -> Campaign:
    if campaign_type not in CampaignType:
        raise ValidationFailure(f"Unknown campaign type: {campaign_type}")
    if not message.strip():
        raise ValidationFailure("Campaign message must not be empty")
    inbox = await inboxes_service.get_inbox(session, workspace_id, inbox_id)
    _validate_inbox_for_type(campaign_type, inbox)
    if campaign_type == CampaignType.ONE_OFF.value:
        scheduled_at = scheduled_at or utcnow()
    else:
        scheduled_at = None  # ongoing campaigns are never scheduled
    campaign = Campaign(
        workspace_id=workspace_id,
        title=title.strip(),
        message=message,
        campaign_type=campaign_type,
        inbox_id=inbox.id,
        sender_user_id=sender_user_id,
        audience=_validate_audience(audience or {"type": "all"}),
        trigger_rules=trigger_rules or {},
        scheduled_at=scheduled_at,
        enabled=enabled,
    )
    session.add(campaign)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="campaign.create",
        target_type="campaign",
        target_id=campaign.id,
        meta={"title": campaign.title, "campaign_type": campaign.campaign_type},
    )
    return campaign


async def update_campaign(
    session: AsyncSession,
    workspace_id: str,
    campaign_id: str,
    *,
    actor: Actor,
    changes: dict[str, Any],
) -> Campaign:
    """Apply the provided fields (from `model_dump(exclude_unset=True)`)."""
    campaign = await get_campaign(session, workspace_id, campaign_id)
    if campaign.status == CampaignStatus.PROCESSING.value:
        raise ConflictError("Campaign is being dispatched and cannot be edited")
    if "message" in changes and not str(changes["message"] or "").strip():
        raise ValidationFailure("Campaign message must not be empty")
    if "audience" in changes and changes["audience"] is not None:
        changes["audience"] = _validate_audience(changes["audience"])
    for field in (
        "title",
        "message",
        "sender_user_id",
        "audience",
        "trigger_rules",
        "scheduled_at",
        "enabled",
    ):
        if field in changes:
            value = changes[field]
            setattr(campaign, field, value.strip() if field == "title" and value else value)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="campaign.update",
        target_type="campaign",
        target_id=campaign.id,
        meta={"title": campaign.title},
    )
    return campaign


async def delete_campaign(
    session: AsyncSession, workspace_id: str, campaign_id: str, *, actor: Actor
) -> None:
    campaign = await get_campaign(session, workspace_id, campaign_id)
    title = campaign.title
    await session.delete(campaign)
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="campaign.delete",
        target_type="campaign",
        target_id=campaign_id,
        meta={"title": title},
    )


async def activate_campaign(
    session: AsyncSession, workspace_id: str, campaign_id: str, *, actor: Actor
) -> Campaign:
    campaign = await get_campaign(session, workspace_id, campaign_id)
    if campaign.status != CampaignStatus.DRAFT.value:
        raise ConflictError(f"Only draft campaigns can be activated (status: {campaign.status})")
    if campaign.campaign_type == CampaignType.ONE_OFF.value:
        if campaign.scheduled_at is None:
            raise ValidationFailure("One-off campaigns need a scheduled_at to activate")
    else:
        rules = campaign.trigger_rules or {}
        url_pattern = rules.get("url_pattern")
        time_on_page = rules.get("time_on_page_seconds")
        has_url = isinstance(url_pattern, str) and bool(url_pattern.strip())
        has_time = isinstance(time_on_page, int | float) and time_on_page >= 0
        if not (has_url or has_time):
            raise ValidationFailure(
                "Ongoing campaigns need trigger_rules with url_pattern or "
                "time_on_page_seconds to activate"
            )
    campaign.status = CampaignStatus.ACTIVE
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="campaign.activate",
        target_type="campaign",
        target_id=campaign.id,
        meta={"title": campaign.title},
    )
    return campaign


async def pause_campaign(
    session: AsyncSession, workspace_id: str, campaign_id: str, *, actor: Actor
) -> Campaign:
    campaign = await get_campaign(session, workspace_id, campaign_id)
    if campaign.status != CampaignStatus.ACTIVE.value:
        raise ConflictError(f"Only active campaigns can be paused (status: {campaign.status})")
    campaign.status = CampaignStatus.DRAFT
    await session.flush()
    await audit.record(
        session,
        workspace_id,
        actor=actor,
        action="campaign.pause",
        target_type="campaign",
        target_id=campaign.id,
        meta={"title": campaign.title},
    )
    return campaign


# ---------------------------------------------------------------------------
# audience + sender resolution
# ---------------------------------------------------------------------------


async def _resolve_audience(session: AsyncSession, campaign: Campaign) -> list[Contact]:
    audience = campaign.audience or {"type": "all"}
    audience_type = audience.get("type", "all")
    if audience_type == "segment":
        segment = await session.get(Segment, str(audience.get("segment_id")))
        if segment is None or segment.workspace_id != campaign.workspace_id:
            return []
        return await segments_service.apply_filters(
            session, campaign.workspace_id, list(segment.filters)
        )
    if audience_type == "tag":
        result = await session.execute(
            select(Contact)
            .join(ContactTag, ContactTag.contact_id == Contact.id)
            .where(
                Contact.workspace_id == campaign.workspace_id,
                ContactTag.tag_id == str(audience.get("tag_id")),
            )
            .order_by(Contact.created_at, Contact.id)
        )
        return list(result.scalars())
    result = await session.execute(
        select(Contact)
        .where(Contact.workspace_id == campaign.workspace_id)
        .order_by(Contact.created_at, Contact.id)
    )
    return list(result.scalars())


async def _resolve_sender(session: AsyncSession, campaign: Campaign) -> tuple[str | None, str]:
    """(author user id | None, display name). Falls back to the system author
    named "Campaign" when no sender member is set/resolvable."""
    if campaign.sender_user_id:
        user = await session.get(User, campaign.sender_user_id)
        if user is not None:
            return user.id, user.name
    return None, FALLBACK_SENDER_NAME


# ---------------------------------------------------------------------------
# one-off dispatch (scheduler job)
# ---------------------------------------------------------------------------


async def dispatch_due_campaigns(now: datetime | None = None) -> int:
    """Send every due one-off campaign; returns the number of campaigns
    processed. Runs in its own unit of work (scheduler entry point)."""
    now = now or utcnow()
    processed = 0
    async with session_scope() as session:
        due = (
            (
                await session.execute(
                    select(Campaign)
                    .where(
                        Campaign.campaign_type == CampaignType.ONE_OFF.value,
                        Campaign.status == CampaignStatus.ACTIVE.value,
                        Campaign.enabled.is_(True),
                        Campaign.scheduled_at.is_not(None),
                        Campaign.scheduled_at <= now,
                    )
                    .order_by(Campaign.scheduled_at, Campaign.id)
                )
            )
            .scalars()
            .all()
        )
        for campaign in due:
            campaign.status = CampaignStatus.PROCESSING
            await session.flush()  # cheap concurrency guard
            await _dispatch_one_off(session, campaign)
            processed += 1
    return processed


@scheduled("campaign_dispatch", every_seconds=30)
async def _campaign_dispatch_job() -> None:
    await dispatch_due_campaigns()


async def _dispatch_one_off(session: AsyncSession, campaign: Campaign) -> None:
    inbox = await session.get(Inbox, campaign.inbox_id)
    sent = 0
    skipped = 0
    if inbox is not None:
        author_id, author_name = await _resolve_sender(session, campaign)
        actor = (
            Actor(type="user", id=author_id, label=author_name)
            if author_id is not None
            else Actor.system()
        )
        for contact in await _resolve_audience(session, campaign):
            if inbox.channel_type == ChannelType.EMAIL.value:
                if not contact.email:
                    skipped += 1
                    continue
                ok = await email_service.send_email(
                    to=contact.email,
                    subject=campaign.title,
                    html=render_message(campaign.message, contact),
                )
                if ok:
                    sent += 1
            else:  # sms / whatsapp: existing channel identity required (no implicit opt-in)
                contact_inbox = (
                    (
                        await session.execute(
                            select(ContactInbox).where(
                                ContactInbox.inbox_id == inbox.id,
                                ContactInbox.contact_id == contact.id,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if contact_inbox is None:
                    skipped += 1
                    continue
                await _send_campaign_conversation(
                    session,
                    campaign,
                    inbox=inbox,
                    contact=contact,
                    contact_inbox=contact_inbox,
                    author_id=author_id,
                    author_name=author_name,
                    actor=actor,
                )
                sent += 1
    campaign.sent_count += sent
    campaign.status = CampaignStatus.COMPLETED
    await session.flush()
    await audit.record(
        session,
        campaign.workspace_id,
        actor=Actor.system(),
        action="campaign.dispatch",
        target_type="campaign",
        target_id=campaign.id,
        meta={"sent": sent, "skipped": skipped},
    )
    await emit(
        session,
        Event(
            name=EventNames.CAMPAIGN_SENT,
            workspace_id=campaign.workspace_id,
            payload={"campaign_id": campaign.id, "sent_count": campaign.sent_count},
        ),
    )


async def _send_campaign_conversation(
    session: AsyncSession,
    campaign: Campaign,
    *,
    inbox: Inbox,
    contact: Contact,
    contact_inbox: ContactInbox,
    author_id: str | None,
    author_name: str,
    actor: Actor,
) -> Conversation:
    """Create the campaign conversation + outbound message (channel delivery is
    handled by the registered senders via the normal add_message path)."""
    conversation = await conversations_service.create_conversation(
        session,
        inbox=inbox,
        contact=contact,
        contact_inbox=contact_inbox,
        attributes={"campaign_id": campaign.id},
        actor=actor,
    )
    await conversations_service.add_message(
        session,
        conversation,
        direction=MessageDirection.OUT.value,
        author_type=AuthorType.USER.value if author_id is not None else AuthorType.SYSTEM.value,
        author_id=author_id,
        author_name=author_name,
        content=render_message(campaign.message, contact),
        meta={"campaign_id": campaign.id},
        actor=actor,
    )
    return conversation


# ---------------------------------------------------------------------------
# ongoing widget delivery
# ---------------------------------------------------------------------------


async def list_widget_campaigns(session: AsyncSession, inbox: Inbox) -> list[dict[str, Any]]:
    """Enabled, active, ongoing campaigns for one widget inbox (public shape)."""
    campaigns = (
        (
            await session.execute(
                select(Campaign)
                .where(
                    Campaign.workspace_id == inbox.workspace_id,
                    Campaign.inbox_id == inbox.id,
                    Campaign.campaign_type == CampaignType.ONGOING.value,
                    Campaign.status == CampaignStatus.ACTIVE.value,
                    Campaign.enabled.is_(True),
                )
                .order_by(Campaign.created_at, Campaign.id)
            )
        )
        .scalars()
        .all()
    )
    workspace = await session.get(Workspace, inbox.workspace_id)
    workspace_name = workspace.name if workspace is not None else FALLBACK_SENDER_NAME
    items: list[dict[str, Any]] = []
    for campaign in campaigns:
        sender_name = workspace_name
        if campaign.sender_user_id:
            user = await session.get(User, campaign.sender_user_id)
            if user is not None:
                sender_name = user.name
        items.append(
            {
                "id": campaign.id,
                "message": campaign.message,
                "trigger_rules": dict(campaign.trigger_rules or {}),
                "sender_name": sender_name,
            }
        )
    return items


async def trigger_ongoing(
    session: AsyncSession,
    campaign: Campaign,
    *,
    contact: Contact,
    contact_inbox: ContactInbox,
    inbox: Inbox,
) -> Conversation | None:
    """Chatwoot fresh-visitor rule: proactive messages only target visitors with
    no conversation yet on this contact_inbox, and never fire twice per campaign
    for the same contact. Returns None when skipped."""
    existing = (
        await session.execute(
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.contact_inbox_id == contact_inbox.id)
        )
    ).scalar_one()
    if existing:
        return None
    prior = (
        await session.execute(
            select(Conversation).where(
                Conversation.workspace_id == campaign.workspace_id,
                Conversation.contact_id == contact.id,
            )
        )
    ).scalars()
    for conversation in prior:
        if (conversation.attributes or {}).get("campaign_id") == campaign.id:
            return None
    author_id, author_name = await _resolve_sender(session, campaign)
    actor = (
        Actor(type="user", id=author_id, label=author_name)
        if author_id is not None
        else Actor.system()
    )
    conversation = await _send_campaign_conversation(
        session,
        campaign,
        inbox=inbox,
        contact=contact,
        contact_inbox=contact_inbox,
        author_id=author_id,
        author_name=author_name,
        actor=actor,
    )
    campaign.sent_count += 1
    await session.flush()
    await emit(
        session,
        Event(
            name=EventNames.CAMPAIGN_SENT,
            workspace_id=campaign.workspace_id,
            payload={"campaign_id": campaign.id, "conversation_id": conversation.id},
        ),
    )
    return conversation
