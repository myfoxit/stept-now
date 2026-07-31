"""Widget help center: collections tree (empty query), search, article detail."""

from __future__ import annotations

import httpx

from app.core.db import get_session_factory
from app.core.events import Actor
from app.services import articles as articles_service
from tests.widget.conftest import WidgetSetup, auth_headers, boot


async def publish_article(
    workspace_id: str, *, title: str, body: str, collection_name: str | None = None
) -> str:
    async with get_session_factory()() as session:
        actor = Actor.system()
        collection_id = None
        if collection_name:
            collection = await articles_service.create_collection(
                session, workspace_id, actor=actor, name=collection_name
            )
            collection_id = collection.id
        article = await articles_service.create_article(
            session, workspace_id, actor=actor, title=title, body=body, collection_id=collection_id
        )
        await articles_service.publish_article(session, workspace_id, article.id, actor=actor)
        await session.commit()
        return article.slug


async def _token(client: httpx.AsyncClient, widget: WidgetSetup) -> str:
    return (await boot(client, widget.widget_key, visitor_id="v1")).json()["token"]


async def test_articles_empty_query_returns_collections(
    client: httpx.AsyncClient, widget: WidgetSetup
):
    await publish_article(
        widget.workspace_id,
        title="How to request a refund",
        body="You can request a refund from your billing settings within 30 days.",
        collection_name="Billing",
    )
    token = await _token(client, widget)
    response = await client.get("/api/widget/articles", headers=auth_headers(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["results"] == []
    collection_names = [c["name"] for c in body["collections"]]
    assert "Billing" in collection_names
    billing = next(c for c in body["collections"] if c["name"] == "Billing")
    assert any(a["title"] == "How to request a refund" for a in billing["articles"])


async def test_articles_search_returns_matches(client: httpx.AsyncClient, widget: WidgetSetup):
    slug = await publish_article(
        widget.workspace_id,
        title="Refund policy",
        body="Refunds are processed within five business days of the request.",
        collection_name="Billing",
    )
    token = await _token(client, widget)
    response = await client.get(
        "/api/widget/articles", params={"query": "refund"}, headers=auth_headers(token)
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["collections"] == []
    slugs = [r["slug"] for r in body["results"]]
    assert slug in slugs
    result = next(r for r in body["results"] if r["slug"] == slug)
    assert result["title"] == "Refund policy"
    assert result["snippet"]


async def test_article_detail_by_slug(client: httpx.AsyncClient, widget: WidgetSetup):
    slug = await publish_article(
        widget.workspace_id,
        title="Getting started",
        body="Welcome to the product. Here is how to begin.",
        collection_name="Basics",
    )
    token = await _token(client, widget)
    response = await client.get(f"/api/widget/articles/{slug}", headers=auth_headers(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "Getting started"
    assert "Welcome to the product" in body["body"]
    assert body["collection"]["name"] == "Basics"


async def test_article_unknown_slug_404(client: httpx.AsyncClient, widget: WidgetSetup):
    token = await _token(client, widget)
    response = await client.get("/api/widget/articles/nope", headers=auth_headers(token))
    assert response.status_code == 404


async def test_articles_requires_auth(client: httpx.AsyncClient, widget: WidgetSetup):
    response = await client.get("/api/widget/articles")
    assert response.status_code == 401


async def test_boot_help_center_enabled_after_publish(
    client: httpx.AsyncClient, widget: WidgetSetup
):
    await publish_article(
        widget.workspace_id, title="FAQ", body="Frequently asked questions and answers."
    )
    response = await boot(client, widget.widget_key, visitor_id="v2")
    assert response.json()["help_center_enabled"] is True
