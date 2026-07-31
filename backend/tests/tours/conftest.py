"""Tours-domain test fixtures and helpers.

HTTP tests build on the root `workspace_ctx`; the `seed_ctx` fixture provides a
service-layer workspace (via `db_only`) for seed/stats unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import jwt
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session_factory, utcnow
from app.core.security import decode_token
from app.models.tour import TourEvent
from app.models.user import User
from app.models.workspace import Membership, Workspace
from app.seed import SeedContext

# A two-step and a three-step deck reused across tests.
TWO_STEPS = [
    {"selector": '[data-tour="inbox"]', "title": "Inbox", "body": "b", "placement": "right"},
    {"selector": '[data-tour="ai"]', "title": "AI", "body": "b", "placement": "auto"},
]
THREE_STEPS = [
    {"selector": '[data-tour="inbox"]', "title": "Inbox", "body": "b", "placement": "right"},
    {
        "selector": '[data-tour="knowledge"]',
        "title": "Knowledge",
        "body": "b",
        "placement": "right",
    },
    {"selector": '[data-tour="ai"]', "title": "AI", "body": "b", "placement": "right"},
]


# --- HTTP helpers -----------------------------------------------------------


async def widget_key_for(client, ctx) -> str:
    resp = await client.get(f"{ctx.base}/inboxes", headers=ctx.owner_headers)
    assert resp.status_code == 200, resp.text
    widgets = [i for i in resp.json() if i["channel_type"] == "widget"]
    assert widgets, "no default widget inbox"
    return widgets[0]["widget_key"]


async def create_tour(client, ctx, *, headers=None, **overrides) -> dict:
    payload: dict = {"name": "Test tour", "steps": TWO_STEPS}
    payload.update(overrides)
    resp = await client.post(
        f"{ctx.base}/tours", json=payload, headers=headers or ctx.owner_headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def publish_tour(client, ctx, tour_id: str) -> dict:
    resp = await client.post(f"{ctx.base}/tours/{tour_id}/publish", headers=ctx.owner_headers)
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


def user_id_from_headers(headers: dict[str, str]) -> str:
    token = headers["Authorization"].split(" ", 1)[1]
    return decode_token(token, "access")["sub"]


def expired_recorder_token(workspace_id: str, user_id: str) -> str:
    now = utcnow()
    payload = {
        "ws": workspace_id,
        "sub": user_id,
        "iss": "stept",
        "typ": "recorder",
        "iat": int((now - timedelta(days=8)).timestamp()),
        "exp": int((now - timedelta(days=1)).timestamp()),
    }
    return jwt.encode(payload, get_settings().secret_key, algorithm="HS256")


async def insert_events(workspace_id: str, tour_id: str, plays: list[tuple]) -> None:
    """Insert (contact_id, event, step_index) telemetry through a committed session
    so a separate API request transaction can read it."""
    async with get_session_factory()() as session:
        for contact_id, event, step_index in plays:
            session.add(
                TourEvent(
                    workspace_id=workspace_id,
                    tour_id=tour_id,
                    contact_id=contact_id,
                    event=event,
                    step_index=step_index,
                )
            )
        await session.commit()


# --- service-layer fixture --------------------------------------------------


@dataclass
class SeedEnv:
    session: AsyncSession
    ctx: SeedContext


@pytest.fixture
async def seed_ctx(db_only) -> SeedEnv:
    owner = User(email="tour-owner@example.com", name="Tour Owner", password_hash="x")
    agent = User(email="tour-agent@example.com", name="Tour Agent", password_hash="x")
    workspace = Workspace(name="Tour WS", slug="tour-ws")
    db_only.add_all([owner, agent, workspace])
    await db_only.flush()
    db_only.add_all(
        [
            Membership(workspace_id=workspace.id, user_id=owner.id, role="owner"),
            Membership(workspace_id=workspace.id, user_id=agent.id, role="agent"),
        ]
    )
    await db_only.flush()
    ctx = SeedContext(workspace=workspace, owner=owner, agent=agent)
    return SeedEnv(session=db_only, ctx=ctx)
