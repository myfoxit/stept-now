"""Macro-suite helpers (API-level: committed rows other sessions can see)."""

from __future__ import annotations

import httpx

from app.core.db import get_session_factory
from app.models.contact import Contact
from app.models.tag import Tag


async def create_contact_via_db(workspace_id: str, *, name: str = "Nina Doe") -> str:
    async with get_session_factory()() as session:
        contact = Contact(workspace_id=workspace_id, name=name)
        session.add(contact)
        await session.commit()
        return contact.id


async def create_tag_via_db(workspace_id: str, *, name: str = "vip") -> str:
    async with get_session_factory()() as session:
        tag = Tag(workspace_id=workspace_id, name=name)
        session.add(tag)
        await session.commit()
        return tag.id


async def create_inbox_via_api(client: httpx.AsyncClient, ctx) -> dict:
    response = await client.post(
        f"{ctx.base}/inboxes",
        json={"name": "widget inbox", "channel_type": "widget", "config": {}},
        headers=ctx.owner_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_conversation_via_api(
    client: httpx.AsyncClient, ctx, *, inbox_id: str, contact_id: str
) -> dict:
    response = await client.post(
        f"{ctx.base}/conversations",
        json={"contact_id": contact_id, "inbox_id": inbox_id, "content": "Hello!"},
        headers=ctx.owner_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def create_macro(
    client: httpx.AsyncClient,
    ctx,
    headers: dict[str, str],
    *,
    name: str = "Wrap up",
    actions: list[dict] | None = None,
    visibility: str = "personal",
) -> dict:
    response = await client.post(
        f"{ctx.base}/macros",
        json={
            "name": name,
            "actions": actions or [{"type": "set_priority", "params": {"priority": "high"}}],
            "visibility": visibility,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()
