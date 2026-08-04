"""Automation-domain fixtures/helpers.

Importing the engine here registers its @on(...) subscribers for service-level
(db_only) tests that fire events without building the app.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import app.automation.engine  # noqa: F401 — registers rule + webhook subscribers
from app.core.db import get_session_factory, uuid7
from app.core.events import Actor
from app.models.automation import AutomationRule
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.inbox import Inbox
from app.models.user import User
from app.models.webhook import Webhook, WebhookDelivery
from app.models.workspace import Membership, Workspace

SYSTEM = Actor.system()


async def make_workspace(session: AsyncSession, name: str = "Auto WS") -> Workspace:
    workspace = Workspace(name=name, slug=f"ws-{uuid7()}", settings={})
    session.add(workspace)
    await session.flush()
    return workspace


async def make_inbox(
    session: AsyncSession,
    workspace: Workspace,
    *,
    channel_type: str = "widget",
    config: dict[str, Any] | None = None,
    name: str = "Test inbox",
) -> Inbox:
    inbox = Inbox(
        workspace_id=workspace.id,
        name=name,
        channel_type=channel_type,
        config=config or {},
        widget_key=f"wk_{uuid7()}" if channel_type == "widget" else None,
    )
    session.add(inbox)
    await session.flush()
    return inbox


async def make_contact(
    session: AsyncSession,
    workspace: Workspace,
    *,
    name: str = "Nina Doe",
    email: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> Contact:
    contact = Contact(
        workspace_id=workspace.id, name=name, email=email, attributes=attributes or {}
    )
    session.add(contact)
    await session.flush()
    return contact


async def make_user(session: AsyncSession, name: str, email: str | None = None) -> User:
    user = User(email=email or f"user-{uuid7()}@example.com", name=name, password_hash="x")
    session.add(user)
    await session.flush()
    return user


async def make_member(
    session: AsyncSession, workspace: Workspace, user: User, role: str = "agent"
) -> Membership:
    membership = Membership(workspace_id=workspace.id, user_id=user.id, role=role)
    session.add(membership)
    await session.flush()
    return membership


async def make_rule(
    session: AsyncSession,
    workspace: Workspace,
    *,
    event: str,
    actions: list[dict[str, Any]],
    conditions: list[dict[str, Any]] | None = None,
    enabled: bool = True,
    ord: int = 0,
    name: str | None = None,
) -> AutomationRule:
    rule = AutomationRule(
        workspace_id=workspace.id,
        name=name or f"rule-{uuid7()[:8]}",
        event=event,
        conditions=conditions or [],
        actions=actions,
        enabled=enabled,
        ord=ord,
    )
    session.add(rule)
    await session.flush()
    return rule


async def make_webhook(
    session: AsyncSession,
    workspace: Workspace,
    *,
    url: str = "https://hooks.example.com/x",
    events: list[str] | None = None,
    enabled: bool = True,
    secret: str = "whsec_test_secret",
) -> Webhook:
    webhook = Webhook(
        workspace_id=workspace.id,
        url=url,
        secret=secret,
        events=events if events is not None else ["*"],
        enabled=enabled,
    )
    session.add(webhook)
    await session.flush()
    return webhook


async def make_delivery(
    session: AsyncSession,
    workspace: Workspace,
    webhook: Webhook,
    *,
    event_name: str = "webhook.test",
    payload: dict[str, Any] | None = None,
) -> WebhookDelivery:
    delivery = WebhookDelivery(
        workspace_id=workspace.id,
        webhook_id=webhook.id,
        event_name=event_name,
        payload=payload
        or {
            "event": event_name,
            "workspace_id": workspace.id,
            "payload": {"hello": "world"},
            "timestamp": "2026-01-01T00:00:00+00:00",
        },
        status="pending",
    )
    session.add(delivery)
    await session.flush()
    return delivery


async def insert_conversation(
    session: AsyncSession,
    workspace: Workspace,
    inbox: Inbox,
    contact: Contact,
    *,
    number: int = 1,
) -> Conversation:
    """Insert a Conversation row directly (no conversation.created event)."""
    conversation = Conversation(
        workspace_id=workspace.id,
        number=number,
        inbox_id=inbox.id,
        contact_id=contact.id,
        subject="Test conversation",
    )
    session.add(conversation)
    await session.flush()
    return conversation


@dataclass
class AutoCtx:
    session: AsyncSession
    workspace: Workspace
    inbox: Inbox
    contact: Contact
    user: User


@pytest.fixture
async def auto(db_only: AsyncSession) -> AutoCtx:
    workspace = await make_workspace(db_only)
    inbox = await make_inbox(db_only, workspace)
    contact = await make_contact(db_only, workspace, email="nina@example.com")
    user = await make_user(db_only, "Sam Support")
    await make_member(db_only, workspace, user, role="agent")
    return AutoCtx(session=db_only, workspace=workspace, inbox=inbox, contact=contact, user=user)


# --- API-side helpers -------------------------------------------------------


async def create_contact_via_db(
    workspace_id: str,
    *,
    name: str = "Nina Doe",
    email: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> str:
    """Insert + commit a contact so API requests (separate sessions) can see it."""
    async with get_session_factory()() as session:
        contact = Contact(
            workspace_id=workspace_id, name=name, email=email, attributes=attributes or {}
        )
        session.add(contact)
        await session.commit()
        return contact.id


async def create_inbox_via_api(client, ctx, *, channel_type: str = "widget") -> dict:
    response = await client.post(
        f"{ctx.base}/inboxes",
        json={"name": f"{channel_type} inbox", "channel_type": channel_type, "config": {}},
        headers=ctx.owner_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()
