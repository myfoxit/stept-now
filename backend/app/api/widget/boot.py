"""Widget public API: ``POST /api/widget/boot`` — the widget handshake.

Resolves the workspace/inbox from the public widget key, verifies optional
Intercom-style identity HMAC, find-or-creates the contact + per-inbox identity,
and returns a signed contact token plus the visitor's recent conversations and
workspace/inbox display config.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select

from app.api.widget.conversations import ConversationSummary, conversation_summaries
from app.api.widget.deps import resolve_inbox
from app.core.db import utcnow, uuid7
from app.core.deps import Db
from app.core.errors import BlockedContactError, ForbiddenError
from app.core.i18n import normalize_locale, workspace_locale
from app.core.ratelimit import RateLimit
from app.core.security import create_widget_token, verify_identity_hash
from app.models.agent import Agent
from app.models.article import Article
from app.models.contact import Contact
from app.models.inbox import ContactInbox, Inbox
from app.models.workspace import Workspace
from app.services import contacts as contacts_service

#: Valid `tour_autostart_policy` values ("ask" is the default: answer in text,
#: attach an offer card; only an explicit "show me / play it" starts a tour).
_TOUR_POLICIES = frozenset({"ask", "auto", "never"})

router = APIRouter()


class BootIdentity(BaseModel):
    external_id: str
    email: str | None = None
    name: str | None = None
    hash: str


class BootRequest(BaseModel):
    widget_key: str
    visitor_id: str | None = None
    identity: BootIdentity | None = None
    #: The visitor's browser language (`navigator.language`). Weakest personal
    #: locale signal: stamped on brand-new contacts only, overwritten by what
    #: they actually write (`learn_contact_locale`).
    locale: str | None = None


class BootContactOut(BaseModel):
    id: str
    name: str
    email: str | None = None
    #: Language this contact writes in, learned from previous messages. The
    #: widget prefers it over the browser's Accept-Language, so a returning
    #: visitor gets their language back before they type anything.
    locale: str | None = None


class BootWorkspaceOut(BaseModel):
    name: str
    logo_url: str | None = None


class BootResponse(BaseModel):
    token: str
    visitor_id: str
    contact: BootContactOut
    workspace: BootWorkspaceOut
    config: dict[str, Any]
    conversations: list[ConversationSummary]
    help_center_enabled: bool


class RequireIdentityResponse(BaseModel):
    require_identity: bool = True


async def _help_center_enabled(session: Db, workspace_id: str) -> bool:
    row = (
        await session.execute(
            select(Article.id)
            .where(Article.workspace_id == workspace_id, Article.status == "published")
            .limit(1)
        )
    ).first()
    return row is not None


async def _display_config(session: Db, workspace: Workspace, inbox: Inbox) -> dict[str, Any]:
    """Branding/behavior keys the messenger needs, resolved server-side.

    All four existed server-side and never reached the UI (the bound agent's
    name — "Northplane Guide" on doktrace — being the sore one). Settings live
    in the widget inbox config JSON; fallbacks are computed here so the widget
    never re-implements them.
    """
    config = inbox.config or {}
    brand = str(config.get("brand_display_name") or "").strip() or workspace.name
    agent_name: str | None = None
    agent_id = config.get("ai_agent_id")
    if agent_id:
        agent = await session.get(Agent, agent_id)
        if agent is not None and agent.workspace_id == workspace.id:
            agent_name = agent.name
    raw_policy = config.get("tour_autostart_policy")
    policy = raw_policy if isinstance(raw_policy, str) and raw_policy in _TOUR_POLICIES else "ask"
    return {
        "brand_display_name": brand,
        "agent_display_name": agent_name,
        "ai_disclosure": bool(config.get("ai_disclosure", True)),
        "tour_autostart_policy": policy,
    }


@router.post(
    "/boot",
    response_model=None,
    # Unauthenticated and row-creating (Contact + ContactInbox per new
    # visitor), so it needs a ceiling of its own.
    dependencies=[Depends(RateLimit("widget_boot", times=30, seconds=60))],
)
async def boot(body: BootRequest, session: Db) -> BootResponse | RequireIdentityResponse:
    inbox = await resolve_inbox(session, body.widget_key)
    workspace = await session.get(Workspace, inbox.workspace_id)
    assert workspace is not None  # FK-guaranteed

    verified = False
    if body.identity is not None:
        identity_secret = workspace.settings.get("identity_secret")
        if not identity_secret or not verify_identity_hash(
            identity_secret, body.identity.external_id, body.identity.hash
        ):
            raise ForbiddenError("Identity verification failed")
        verified = True
    elif inbox.config.get("require_identity"):
        return RequireIdentityResponse()

    # The per-inbox source id: external id for identified users, else the
    # persisted visitor id, else a fresh one the widget will store.
    if body.identity is not None:
        source_id = body.identity.external_id
    else:
        source_id = body.visitor_id or uuid7()

    contact_inbox = (
        await session.execute(
            select(ContactInbox).where(
                ContactInbox.inbox_id == inbox.id, ContactInbox.source_id == source_id
            )
        )
    ).scalar_one_or_none()

    contact = await _resolve_contact(session, workspace, body.identity, contact_inbox)
    await _ensure_contact_inbox(
        session, workspace.id, inbox, contact, source_id, contact_inbox, verified
    )

    now = utcnow()
    contact.last_seen_at = now
    if contact.first_seen_at is None:
        contact.first_seen_at = now
    if contact.locale is None:
        # The browser's language is the weakest *personal* locale signal —
        # it only fills the vacuum for brand-new visitors. The first real
        # message they write overrides it via `learn_contact_locale`, and it
        # must never be left empty: an empty locale plus an undetectable
        # first message ("Yes, show me.") leaves the agent guessing from its
        # persona's language instead of the visitor's.
        contact.locale = normalize_locale(body.locale)
    await session.flush()

    return BootResponse(
        token=create_widget_token(workspace.id, contact.id),
        visitor_id=body.visitor_id or source_id,
        contact=BootContactOut(
            id=contact.id, name=contact.name, email=contact.email, locale=contact.locale
        ),
        workspace=BootWorkspaceOut(name=workspace.name, logo_url=workspace.logo_url),
        # The workspace default is the widget's weakest locale signal — used
        # only when we know nothing about this particular visitor.
        config={
            **inbox.config,
            "default_locale": workspace_locale(workspace.settings),
            **await _display_config(session, workspace, inbox),
        },
        conversations=await conversation_summaries(
            session, workspace.id, inbox.id, contact.id, limit=10
        ),
        help_center_enabled=await _help_center_enabled(session, workspace.id),
    )


async def _resolve_contact(
    session: Db,
    workspace: Workspace,
    identity: BootIdentity | None,
    contact_inbox: ContactInbox | None,
) -> Contact:
    if identity is not None:
        contact, _created = await contacts_service.find_or_create(
            session,
            workspace.id,
            external_id=identity.external_id,
            email=identity.email,
            name=identity.name,
            verified=True,
        )
        # A blocked visitor gets no widget session at all — checking here keeps
        # the block ahead of conversation creation and message posting alike.
        if contact.blocked:
            raise BlockedContactError("This contact is blocked")
        return contact
    if contact_inbox is not None:
        existing = await session.get(Contact, contact_inbox.contact_id)
        if existing is not None:
            if existing.blocked:
                raise BlockedContactError("This contact is blocked")
            return existing
    # Brand-new anonymous visitor — mint a bare contact (no shared identity keys,
    # so this never collapses distinct visitors together).
    contact, _created = await contacts_service.find_or_create(session, workspace.id)
    return contact


async def _ensure_contact_inbox(
    session: Db,
    workspace_id: str,
    inbox: Inbox,
    contact: Contact,
    source_id: str,
    contact_inbox: ContactInbox | None,
    verified: bool,
) -> ContactInbox:
    if contact_inbox is None:
        contact_inbox = ContactInbox(
            workspace_id=workspace_id,
            contact_id=contact.id,
            inbox_id=inbox.id,
            source_id=source_id,
            hmac_verified=verified,
        )
        session.add(contact_inbox)
        await session.flush()
    else:
        contact_inbox.contact_id = contact.id
        if verified:
            contact_inbox.hmac_verified = True
    return contact_inbox
