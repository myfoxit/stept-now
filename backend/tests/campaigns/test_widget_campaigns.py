"""Widget campaigns: public listing per widget key + Chatwoot-style trigger."""

from __future__ import annotations

from sqlalchemy import select

from app.core.db import get_session_factory
from app.models.campaign import Campaign
from app.models.message import Message
from tests.campaigns.conftest import (
    boot_visitor,
    create_campaign_via_db,
    create_widget_setup,
    widget_headers,
)


async def _campaign_row(campaign_id: str) -> Campaign:
    async with get_session_factory()() as session:
        campaign = await session.get(Campaign, campaign_id)
        assert campaign is not None
        return campaign


async def test_get_lists_only_own_inbox_enabled_active_ongoing(client):
    setup = await create_widget_setup()
    other = await create_widget_setup(name="Other Corp")
    visible = await create_campaign_via_db(setup.workspace_id, setup.inbox_id, title="Visible")
    await create_campaign_via_db(setup.workspace_id, setup.inbox_id, status="draft")
    await create_campaign_via_db(setup.workspace_id, setup.inbox_id, enabled=False)
    await create_campaign_via_db(other.workspace_id, other.inbox_id, title="Foreign")

    response = await client.get(f"/api/widget/campaigns?widget_key={setup.widget_key}")
    assert response.status_code == 200, response.text
    items = response.json()
    assert [item["id"] for item in items] == [visible]
    assert items[0]["message"] == "Welcome {{contact.name}}!"
    assert items[0]["trigger_rules"] == {"url_pattern": "*", "time_on_page_seconds": 5}
    assert items[0]["sender_name"] == "Acme Support"  # workspace-name fallback

    unknown = await client.get("/api/widget/campaigns?widget_key=wk_nope")
    assert unknown.status_code == 404


async def test_trigger_creates_conversation_once_then_skips(client):
    setup = await create_widget_setup()
    campaign_id = await create_campaign_via_db(setup.workspace_id, setup.inbox_id)
    token = await boot_visitor(client, setup.widget_key, "visitor-1")

    first = await client.post(
        f"/api/widget/campaigns/{campaign_id}/trigger", headers=widget_headers(token)
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["skipped"] is False
    conversation_id = body["conversation_id"]
    assert conversation_id

    async with get_session_factory()() as session:
        message = (
            await session.execute(select(Message).where(Message.conversation_id == conversation_id))
        ).scalar_one()
        assert message.direction == "out"
        assert message.content == "Welcome there!"  # anonymous visitor → fallback name
        assert message.meta == {"campaign_id": campaign_id}
    assert (await _campaign_row(campaign_id)).sent_count == 1

    # Same visitor again: the contact_inbox already has a conversation → skipped.
    second = await client.post(
        f"/api/widget/campaigns/{campaign_id}/trigger", headers=widget_headers(token)
    )
    assert second.status_code == 200
    assert second.json() == {"skipped": True, "conversation_id": None}
    assert (await _campaign_row(campaign_id)).sent_count == 1


async def test_trigger_skips_visitors_with_existing_conversation(client):
    setup = await create_widget_setup()
    campaign_id = await create_campaign_via_db(setup.workspace_id, setup.inbox_id)
    token = await boot_visitor(client, setup.widget_key, "visitor-2")

    started = await client.post(
        "/api/widget/conversations", json={"message": "hi!"}, headers=widget_headers(token)
    )
    assert started.status_code == 201

    response = await client.post(
        f"/api/widget/campaigns/{campaign_id}/trigger", headers=widget_headers(token)
    )
    assert response.status_code == 200
    assert response.json()["skipped"] is True  # proactive messages target fresh visitors only
    assert (await _campaign_row(campaign_id)).sent_count == 0


async def test_trigger_requires_widget_token(client):
    setup = await create_widget_setup()
    campaign_id = await create_campaign_via_db(setup.workspace_id, setup.inbox_id)
    response = await client.post(f"/api/widget/campaigns/{campaign_id}/trigger")
    assert response.status_code == 401


async def test_trigger_cross_workspace_campaign_404(client):
    setup = await create_widget_setup()
    other = await create_widget_setup(name="Other Corp")
    foreign_campaign = await create_campaign_via_db(other.workspace_id, other.inbox_id)
    token = await boot_visitor(client, setup.widget_key, "visitor-3")

    response = await client.post(
        f"/api/widget/campaigns/{foreign_campaign}/trigger", headers=widget_headers(token)
    )
    assert response.status_code == 404


async def test_trigger_inactive_campaign_409(client):
    setup = await create_widget_setup()
    draft = await create_campaign_via_db(setup.workspace_id, setup.inbox_id, status="draft")
    token = await boot_visitor(client, setup.widget_key, "visitor-4")

    response = await client.post(
        f"/api/widget/campaigns/{draft}/trigger", headers=widget_headers(token)
    )
    assert response.status_code == 409
