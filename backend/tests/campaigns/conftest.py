"""Campaign-suite helpers: committed rows (separate sessions see them) + a
widget workspace/visitor setup mirroring tests/widget/conftest.py."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.db import get_session_factory, uuid7
from app.models.campaign import Campaign
from app.models.contact import Contact
from app.models.inbox import ContactInbox, Inbox
from app.models.segment import Segment
from app.models.workspace import Workspace


async def create_contact_via_db(
    workspace_id: str,
    *,
    name: str = "Nina Doe",
    email: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> str:
    async with get_session_factory()() as session:
        contact = Contact(
            workspace_id=workspace_id, name=name, email=email, attributes=attributes or {}
        )
        session.add(contact)
        await session.commit()
        return contact.id


async def create_segment_via_db(
    workspace_id: str, *, name: str = "Segment", filters: list[dict[str, Any]]
) -> str:
    async with get_session_factory()() as session:
        segment = Segment(workspace_id=workspace_id, name=name, filters=filters)
        session.add(segment)
        await session.commit()
        return segment.id


async def create_contact_inbox_via_db(
    workspace_id: str, contact_id: str, inbox_id: str, *, source_id: str
) -> str:
    async with get_session_factory()() as session:
        contact_inbox = ContactInbox(
            workspace_id=workspace_id,
            contact_id=contact_id,
            inbox_id=inbox_id,
            source_id=source_id,
        )
        session.add(contact_inbox)
        await session.commit()
        return contact_inbox.id


async def create_inbox_via_api(
    client: httpx.AsyncClient, ctx, *, channel_type: str = "email"
) -> dict:
    response = await client.post(
        f"{ctx.base}/inboxes",
        json={"name": f"{channel_type} inbox", "channel_type": channel_type, "config": {}},
        headers=ctx.owner_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_inbox_via_db(workspace_id: str, *, channel_type: str) -> str:
    """Direct insert for channel types the inbox API literal doesn't offer yet
    (sms/whatsapp arrive with the channels wave)."""
    async with get_session_factory()() as session:
        inbox = Inbox(
            workspace_id=workspace_id,
            name=f"{channel_type} inbox",
            channel_type=channel_type,
            config={},
        )
        session.add(inbox)
        await session.commit()
        return inbox.id


async def create_campaign_via_api(
    client: httpx.AsyncClient, ctx, *, inbox_id: str, campaign_type: str = "one_off", **overrides
) -> dict:
    payload = {
        "title": "Spring launch",
        "message": "Hello {{contact.name}}!",
        "campaign_type": campaign_type,
        "inbox_id": inbox_id,
        **overrides,
    }
    response = await client.post(f"{ctx.base}/campaigns", json=payload, headers=ctx.owner_headers)
    assert response.status_code == 201, response.text
    return response.json()


# --- widget-side setup ------------------------------------------------------


@dataclass
class WidgetCampaignSetup:
    workspace_id: str
    workspace_name: str
    inbox_id: str
    widget_key: str


async def create_widget_setup(name: str = "Acme Support") -> WidgetCampaignSetup:
    async with get_session_factory()() as session:
        workspace = Workspace(name=name, slug=f"acme-{uuid7()}", settings={})
        session.add(workspace)
        await session.flush()
        inbox = Inbox(
            workspace_id=workspace.id,
            name="Website widget",
            channel_type="widget",
            config={},
            widget_key=f"wk_{uuid7().replace('-', '')[:20]}",
        )
        session.add(inbox)
        await session.commit()
        assert inbox.widget_key is not None
        return WidgetCampaignSetup(
            workspace_id=workspace.id,
            workspace_name=workspace.name,
            inbox_id=inbox.id,
            widget_key=inbox.widget_key,
        )


async def create_campaign_via_db(
    workspace_id: str,
    inbox_id: str,
    *,
    title: str = "Welcome tour",
    message: str = "Welcome {{contact.name}}!",
    campaign_type: str = "ongoing",
    status: str = "active",
    enabled: bool = True,
    sender_user_id: str | None = None,
    trigger_rules: dict[str, Any] | None = None,
    audience: dict[str, Any] | None = None,
) -> str:
    async with get_session_factory()() as session:
        campaign = Campaign(
            workspace_id=workspace_id,
            inbox_id=inbox_id,
            title=title,
            message=message,
            campaign_type=campaign_type,
            status=status,
            enabled=enabled,
            sender_user_id=sender_user_id,
            trigger_rules=(
                trigger_rules
                if trigger_rules is not None
                else {"url_pattern": "*", "time_on_page_seconds": 5}
            ),
            audience=audience or {"type": "all"},
        )
        session.add(campaign)
        await session.commit()
        return campaign.id


async def boot_visitor(client: httpx.AsyncClient, widget_key: str, visitor_id: str) -> str:
    response = await client.post(
        "/api/widget/boot", json={"widget_key": widget_key, "visitor_id": visitor_id}
    )
    assert response.status_code == 200, response.text
    return response.json()["token"]


def widget_headers(token: str) -> dict[str, str]:
    return {"X-Widget-Token": token}
