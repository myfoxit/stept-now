"""Inbox CRUD, default widget inbox, embed snippet, encrypted secrets, authz."""

from __future__ import annotations

from sqlalchemy import select

from app.core.security import decrypt_secret
from app.models.inbox import Inbox
from tests.conftest import get_session_factory
from tests.conversations.conftest import default_inbox


async def test_default_widget_inbox_created_with_workspace(client, workspace_ctx):
    inbox = await default_inbox(client, workspace_ctx)
    assert inbox["name"] == "Website widget"
    assert inbox["enabled"] is True
    assert inbox["widget_key"].startswith("wk_")
    assert inbox["config"]["greeting"] == "Hi! How can we help?"
    # Off by default: inbound conversations belong in Unassigned until a human
    # picks them up. Turning it on pre-claims every conversation, which in a
    # single-member workspace leaves the triage queue permanently empty.
    assert inbox["config"]["auto_assign"] is False
    assert inbox["has_secrets"] is False


async def test_widget_embed_snippet(client, workspace_ctx):
    inbox = await default_inbox(client, workspace_ctx)
    detail = await client.get(
        f"{workspace_ctx.base}/inboxes/{inbox['id']}", headers=workspace_ctx.owner_headers
    )
    snippet = detail.json()["embed_snippet"]
    assert f'window.SteptSettings={{workspaceKey:"{inbox["widget_key"]}"}}' in snippet
    assert "http://localhost:8600/widget-assets/loader.js" in snippet
    assert snippet.count("<script") == 2
    # The inline script must also install the queue stub the loader drains
    # (loader-core.ts installStept) so pre-load Stept(...) calls never throw.
    assert snippet == (
        f'<script>window.SteptSettings={{workspaceKey:"{inbox["widget_key"]}"}};'
        "window.Stept=window.Stept||"
        "function(){(window.Stept.q=window.Stept.q||[]).push(arguments)};</script>\n"
        '<script src="http://localhost:8600/widget-assets/loader.js" async></script>'
    )


async def test_create_inbox_with_secrets_encrypted_at_rest(client, workspace_ctx):
    response = await client.post(
        f"{workspace_ctx.base}/inboxes",
        json={
            "name": "Support mail",
            "channel_type": "email",
            "config": {"address": "support@acme.dev"},
            "secrets": {"smtp_password": "hunter2"},
        },
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["has_secrets"] is True
    assert body["widget_key"] is None
    assert body["embed_snippet"] is None
    assert "secrets" not in body  # never returned
    assert "hunter2" not in response.text

    async with get_session_factory()() as session:
        stored = (await session.execute(select(Inbox).where(Inbox.id == body["id"]))).scalar_one()
    assert stored.secrets_encrypted is not None
    assert "hunter2" not in stored.secrets_encrypted  # Fernet ciphertext
    assert '"smtp_password": "hunter2"' in decrypt_secret(stored.secrets_encrypted)


async def test_patch_rotates_secrets_and_updates_config(client, workspace_ctx):
    created = await client.post(
        f"{workspace_ctx.base}/inboxes",
        json={
            "name": "Bot",
            "channel_type": "telegram",
            "secrets": {"bot_token": "old-token"},
        },
        headers=workspace_ctx.owner_headers,
    )
    inbox_id = created.json()["id"]

    patched = await client.patch(
        f"{workspace_ctx.base}/inboxes/{inbox_id}",
        json={
            "secrets": {"bot_token": "new-token"},
            "config": {"greeting": "yo"},
            "enabled": False,
        },
        headers=workspace_ctx.owner_headers,
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False
    assert patched.json()["config"] == {"greeting": "yo"}

    async with get_session_factory()() as session:
        stored = (await session.execute(select(Inbox).where(Inbox.id == inbox_id))).scalar_one()
    assert "new-token" in decrypt_secret(stored.secrets_encrypted)

    cleared = await client.patch(
        f"{workspace_ctx.base}/inboxes/{inbox_id}",
        json={"secrets": {}},
        headers=workspace_ctx.owner_headers,
    )
    assert cleared.json()["has_secrets"] is False


async def test_inbox_authz_and_isolation(client, workspace_ctx):
    agent_headers = await workspace_ctx.add_member("agent@example.com", role="agent")

    # Agents can read inboxes but lack channels:manage.
    listing = await client.get(f"{workspace_ctx.base}/inboxes", headers=agent_headers)
    assert listing.status_code == 200
    denied = await client.post(
        f"{workspace_ctx.base}/inboxes",
        json={"name": "Nope", "channel_type": "api"},
        headers=agent_headers,
    )
    assert denied.status_code == 403
    inbox = await default_inbox(client, workspace_ctx)
    denied_patch = await client.patch(
        f"{workspace_ctx.base}/inboxes/{inbox['id']}",
        json={"name": "Hacked"},
        headers=agent_headers,
    )
    assert denied_patch.status_code == 403

    missing = await client.get(
        f"{workspace_ctx.base}/inboxes/00000000-0000-7000-8000-000000000000",
        headers=workspace_ctx.owner_headers,
    )
    assert missing.status_code == 404


async def test_delete_inbox(client, workspace_ctx):
    created = await client.post(
        f"{workspace_ctx.base}/inboxes",
        json={"name": "Temp", "channel_type": "api"},
        headers=workspace_ctx.owner_headers,
    )
    inbox_id = created.json()["id"]
    deleted = await client.delete(
        f"{workspace_ctx.base}/inboxes/{inbox_id}", headers=workspace_ctx.owner_headers
    )
    assert deleted.status_code == 200
    gone = await client.get(
        f"{workspace_ctx.base}/inboxes/{inbox_id}", headers=workspace_ctx.owner_headers
    )
    assert gone.status_code == 404


async def test_unknown_channel_type_rejected(client, workspace_ctx):
    response = await client.post(
        f"{workspace_ctx.base}/inboxes",
        json={"name": "Fax", "channel_type": "fax"},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 422
