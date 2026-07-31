"""Survey test fixtures and helpers.

HTTP tests build on the root `workspace_ctx`; `survey_env` provides a
service-layer workspace (via `db_only`) for delivery/results unit tests.
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

NPS_ID = "q-nps"
TEXT_ID = "q-text"
RATING_ID = "q-rating"
SELECT_ID = "q-select"

NPS_AND_TEXT: list[dict[str, Any]] = [
    {"id": NPS_ID, "type": "nps", "question": "How likely are you to recommend us?"},
    {"id": TEXT_ID, "type": "text", "question": "Why?", "required": False},
]

FULL_DECK: list[dict[str, Any]] = [
    *NPS_AND_TEXT,
    {"id": RATING_ID, "type": "rating", "question": "Rate the docs"},
    {
        "id": SELECT_ID,
        "type": "select",
        "question": "How did you hear about us?",
        "options": ["Search", "A friend", "Conference"],
    },
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


async def create_survey(client, ctx, *, headers=None, **overrides) -> dict:
    payload: dict[str, Any] = {"name": "Test survey", "questions": NPS_AND_TEXT}
    payload.update(overrides)
    resp = await client.post(
        f"{ctx.base}/surveys", json=payload, headers=headers or ctx.owner_headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def publish_survey(client, ctx, survey_id: str) -> dict:
    resp = await client.post(f"{ctx.base}/surveys/{survey_id}/publish", headers=ctx.owner_headers)
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
class SurveyEnv:
    session: AsyncSession
    ctx: SeedContext

    @property
    def workspace_id(self) -> str:
        return self.ctx.workspace.id


@pytest.fixture
async def survey_env(db_only) -> SurveyEnv:
    owner = User(email="survey-owner@example.com", name="SV Owner", password_hash="x")
    agent = User(email="survey-agent@example.com", name="SV Agent", password_hash="x")
    workspace = Workspace(name="Survey WS", slug="survey-ws")
    db_only.add_all([owner, agent, workspace])
    await db_only.flush()
    db_only.add_all(
        [
            Membership(workspace_id=workspace.id, user_id=owner.id, role="owner"),
            Membership(workspace_id=workspace.id, user_id=agent.id, role="agent"),
        ]
    )
    await db_only.flush()
    return SurveyEnv(
        session=db_only, ctx=SeedContext(workspace=workspace, owner=owner, agent=agent)
    )


async def make_contact(
    session: AsyncSession, workspace_id: str, *, name: str = "Ada", attributes=None
) -> Contact:
    contact = Contact(workspace_id=workspace_id, name=name, attributes=attributes or {})
    session.add(contact)
    await session.flush()
    return contact
