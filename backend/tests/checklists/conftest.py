"""Checklist test fixtures and helpers.

HTTP tests build on the root `workspace_ctx`; `dap_env` provides a service-layer
workspace (via `db_only`) for delivery/progress unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.contact import Contact
from app.models.user import User
from app.models.workspace import Membership, Workspace
from app.seed import SeedContext

# A three-item deck reused across tests.
THREE_ITEMS: list[dict[str, Any]] = [
    {"title": "Take the tour", "body": "Two minutes, tops."},
    {
        "title": "Connect an inbox",
        "completion": {"type": "url_visited", "url_pattern": "*/settings*"},
    },
    {"title": "Invite a teammate"},
]


# --- HTTP helpers -----------------------------------------------------------


async def widget_key_for(client, ctx) -> str:
    resp = await client.get(f"{ctx.base}/inboxes", headers=ctx.owner_headers)
    assert resp.status_code == 200, resp.text
    widgets = [i for i in resp.json() if i["channel_type"] == "widget"]
    assert widgets, "no default widget inbox"
    return widgets[0]["widget_key"]


async def widget_inbox_id(client, ctx) -> str:
    resp = await client.get(f"{ctx.base}/inboxes", headers=ctx.owner_headers)
    return next(i["id"] for i in resp.json() if i["channel_type"] == "widget")


async def create_checklist(client, ctx, *, headers=None, **overrides) -> dict:
    payload: dict[str, Any] = {"name": "Test checklist", "items": THREE_ITEMS}
    payload.update(overrides)
    resp = await client.post(
        f"{ctx.base}/checklists", json=payload, headers=headers or ctx.owner_headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def publish_checklist(client, ctx, checklist_id: str) -> dict:
    resp = await client.post(
        f"{ctx.base}/checklists/{checklist_id}/publish", headers=ctx.owner_headers
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def create_contact(client, ctx, *, name="Ada", attributes=None) -> dict:
    resp = await client.post(
        f"{ctx.base}/contacts",
        json={"name": name, "attributes": attributes or {}},
        headers=ctx.owner_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- service-layer fixture --------------------------------------------------


@dataclass
class DapEnv:
    session: AsyncSession
    ctx: SeedContext

    @property
    def workspace_id(self) -> str:
        return self.ctx.workspace.id


@pytest.fixture
async def dap_env(db_only) -> DapEnv:
    owner = User(email="checklist-owner@example.com", name="CL Owner", password_hash="x")
    agent = User(email="checklist-agent@example.com", name="CL Agent", password_hash="x")
    workspace = Workspace(name="Checklist WS", slug="checklist-ws")
    db_only.add_all([owner, agent, workspace])
    await db_only.flush()
    db_only.add_all(
        [
            Membership(workspace_id=workspace.id, user_id=owner.id, role="owner"),
            Membership(workspace_id=workspace.id, user_id=agent.id, role="agent"),
        ]
    )
    await db_only.flush()
    return DapEnv(session=db_only, ctx=SeedContext(workspace=workspace, owner=owner, agent=agent))


async def make_contact(
    session: AsyncSession, workspace_id: str, *, name: str = "Ada", attributes=None
) -> Contact:
    contact = Contact(workspace_id=workspace_id, name=name, attributes=attributes or {})
    session.add(contact)
    await session.flush()
    return contact
