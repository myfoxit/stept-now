"""Shared fixtures/helpers for knowledge & RAG tests."""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from tests.conftest import drain_tasks

SITEMAP_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"


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


async def sync_now(client, ctx, source_id, *, headers=None):
    """Trigger a sync and wait for the background task to finish."""
    response = await client.post(
        f"{ctx.base}/knowledge/sources/{source_id}/sync", headers=headers or ctx.owner_headers
    )
    assert response.status_code == 200, response.text
    await drain_tasks()


async def get_source_json(client, ctx, source_id, *, headers=None):
    response = await client.get(
        f"{ctx.base}/knowledge/sources/{source_id}", headers=headers or ctx.owner_headers
    )
    assert response.status_code == 200, response.text
    return response.json()


async def list_docs(client, ctx, source_id, *, headers=None):
    response = await client.get(
        f"{ctx.base}/knowledge/documents?source_id={source_id}&limit=50",
        headers=headers or ctx.owner_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def get_doc(client, ctx, document_id, *, headers=None):
    response = await client.get(
        f"{ctx.base}/knowledge/documents/{document_id}", headers=headers or ctx.owner_headers
    )
    assert response.status_code == 200, response.text
    return response.json()


def html_page(title, body, links=()):
    anchors = "".join(f'<a href="{href}">{href}</a>' for href in links)
    content = f"<html><head><title>{title}</title></head><body><p>{body}</p>{anchors}</body></html>"
    return httpx.Response(200, content=content.encode(), headers={"content-type": "text/html"})


def robots_response(body):
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/plain"})


def sitemap_response(entries):
    """entries: [url, ...] or [(url, lastmod|None), ...] → a <urlset> response."""
    locs = ""
    for entry in entries:
        url, lastmod = entry if isinstance(entry, tuple) else (entry, None)
        stamp = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        locs += f"<url><loc>{url}</loc>{stamp}</url>"
    xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="{SITEMAP_NS}">{locs}</urlset>'
    return httpx.Response(200, content=xml.encode(), headers={"content-type": "application/xml"})


async def search(client, ctx, query, *, k=8, source_ids=None, headers=None):
    body = {"query": query, "k": k}
    if source_ids is not None:
        body["source_ids"] = source_ids
    response = await client.post(
        f"{ctx.base}/knowledge/search", json=body, headers=headers or ctx.owner_headers
    )
    assert response.status_code == 200, response.text
    return response.json()
