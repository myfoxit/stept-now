"""Widget-suite fixtures: a committed workspace + widget inbox, plus helpers to
boot and act as a visitor over HTTP, and to inject agent-side messages."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest

from app.core.db import get_session_factory, uuid7
from app.core.events import Actor
from app.core.security import compute_identity_hash
from app.models.conversation import Conversation
from app.models.inbox import Inbox
from app.models.workspace import Workspace
from app.services import conversations as conversations_service

IDENTITY_SECRET = "widget-identity-secret"


@dataclass
class WidgetSetup:
    workspace_id: str
    workspace_slug: str
    inbox_id: str
    widget_key: str
    identity_secret: str = IDENTITY_SECRET


async def create_widget_setup(
    *, require_identity: bool = False, identity_secret: str = IDENTITY_SECRET
) -> WidgetSetup:
    async with get_session_factory()() as session:
        workspace = Workspace(
            name="Acme Support",
            slug=f"acme-{uuid7()}",
            settings={"identity_secret": identity_secret},
        )
        session.add(workspace)
        await session.flush()
        inbox = Inbox(
            workspace_id=workspace.id,
            name="Website widget",
            channel_type="widget",
            config={"greeting": "Hi there", "require_identity": require_identity},
            widget_key=f"wk_{uuid7().replace('-', '')[:20]}",
        )
        session.add(inbox)
        await session.commit()
        return WidgetSetup(
            workspace_id=workspace.id,
            workspace_slug=workspace.slug,
            inbox_id=inbox.id,
            widget_key=inbox.widget_key,
            identity_secret=identity_secret,
        )


@pytest.fixture
async def widget(client: httpx.AsyncClient) -> WidgetSetup:  # noqa: ARG001 — schema via lifespan
    return await create_widget_setup()


def identity_payload(external_id: str, *, secret: str = IDENTITY_SECRET, **extra: Any) -> dict:
    return {
        "external_id": external_id,
        "hash": compute_identity_hash(secret, external_id),
        **extra,
    }


async def boot(
    client: httpx.AsyncClient,
    widget_key: str,
    *,
    visitor_id: str | None = None,
    identity: dict | None = None,
) -> httpx.Response:
    body: dict[str, Any] = {"widget_key": widget_key}
    if visitor_id is not None:
        body["visitor_id"] = visitor_id
    if identity is not None:
        body["identity"] = identity
    return await client.post("/api/widget/boot", json=body)


def auth_headers(token: str) -> dict[str, str]:
    return {"X-Widget-Token": token}


async def agent_reply(conversation_id: str, content: str, *, visibility: str = "public") -> None:
    """Inject an agent-side outbound message straight through the service layer."""
    async with get_session_factory()() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        await conversations_service.add_message(
            session,
            conversation,
            direction="out",
            author_type="user",
            author_id=None,
            author_name="Sam Support",
            content=content,
            visibility=visibility,
            actor=Actor(type="user", id=None, label="Sam Support"),
            deliver=False,
        )
        await session.commit()


async def add_activity(conversation_id: str, content: str) -> None:
    async with get_session_factory()() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        await conversations_service.add_message(
            session,
            conversation,
            direction="out",
            author_type="system",
            author_id=None,
            author_name="System",
            content=content,
            visibility="activity",
            actor=Actor.system(),
            deliver=False,
        )
        await session.commit()
