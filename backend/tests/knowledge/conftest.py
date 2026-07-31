"""Shared fixtures/helpers for knowledge & RAG tests."""

from __future__ import annotations

import pytest
from sqlalchemy import select


@pytest.fixture
async def seeded_ctx(workspace_ctx):
    """workspace_ctx with the demo product docs + published articles seeded in."""
    from app.core.db import get_session_factory
    from app.models.user import User
    from app.models.workspace import Workspace
    from app.rag.seed import seed
    from app.seed import SeedContext

    async with get_session_factory()() as session:
        workspace = await session.get(Workspace, workspace_ctx.id)
        owner = (
            await session.execute(select(User).where(User.email == "owner@example.com"))
        ).scalar_one()
        await seed(session, SeedContext(workspace=workspace, owner=owner, agent=owner))
        await session.commit()
    return workspace_ctx


async def create_source(client, ctx, *, type="text", name="Docs", config=None, headers=None):
    response = await client.post(
        f"{ctx.base}/knowledge/sources",
        json={"type": type, "name": name, "config": config or {}},
        headers=headers or ctx.owner_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def paste_text(client, ctx, source_id, *, title, content, headers=None):
    response = await client.post(
        f"{ctx.base}/knowledge/sources/{source_id}/documents",
        json={"title": title, "content": content},
        headers=headers or ctx.owner_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def search(client, ctx, query, *, k=8, source_ids=None, headers=None):
    body = {"query": query, "k": k}
    if source_ids is not None:
        body["source_ids"] = source_ids
    response = await client.post(
        f"{ctx.base}/knowledge/search", json=body, headers=headers or ctx.owner_headers
    )
    assert response.status_code == 200, response.text
    return response.json()
