"""search_knowledge / ask_knowledge_base / articles / documents / create_document."""

from __future__ import annotations

from sqlalchemy import select

from tests.conftest import drain_tasks
from tests.mcp.conftest import call_tool, make_api_key, seed_document


async def test_search_and_get_document_round_trip(client, workspace_ctx):
    source_id, document_id = await seed_document(client, workspace_ctx)
    key = (await make_api_key(client, workspace_ctx))["key"]

    results = await call_tool(client, "search_knowledge", {"query": "refund policy"}, key=key)
    assert results, "seeded document should be found"
    top = results[0]
    assert top["document_id"] == document_id
    assert top["source_id"] == source_id
    assert top["title"] == "Refund policy"
    assert "refund" in top["snippet"].lower()
    assert top["score"] > 0

    document = await call_tool(client, "get_document", {"document_id": document_id}, key=key)
    assert document["id"] == document_id
    assert document["source"] == "Docs"
    assert "5 business days" in document["content_markdown"]
    assert isinstance(document["updated_at"], str)


async def test_get_document_not_found(client, workspace_ctx):
    key = (await make_api_key(client, workspace_ctx))["key"]
    payload = await call_tool(
        client, "get_document", {"document_id": "00000000-0000-0000-0000-000000000000"}, key=key
    )
    assert payload == {"error": "Document not found"}


async def test_ask_knowledge_base_deterministic_with_mock_provider(client, workspace_ctx):
    _, document_id = await seed_document(client, workspace_ctx)
    key = (await make_api_key(client, workspace_ctx))["key"]

    first = await call_tool(
        client, "ask_knowledge_base", {"question": "How long do refunds take?"}, key=key
    )
    assert first["answer"]
    assert first["chunks_used"] >= 1
    assert 0 < first["confidence"] <= 1
    assert first["took_ms"] >= 0
    assert first["citations"], "expected at least one citation"
    citation = first["citations"][0]
    assert citation["n"] == 1
    assert citation["title"] == "Refund policy"
    assert citation["document_id"] == document_id

    second = await call_tool(
        client, "ask_knowledge_base", {"question": "How long do refunds take?"}, key=key
    )
    assert second["answer"] == first["answer"]
    assert second["citations"] == first["citations"]
    assert second["confidence"] == first["confidence"]


async def test_search_articles_published_only_and_get_article(client, workspace_ctx):
    published = await client.post(
        f"{workspace_ctx.base}/articles",
        json={"title": "Connecting Slack", "body": "Open settings and connect Slack."},
        headers=workspace_ctx.owner_headers,
    )
    assert published.status_code == 201, published.text
    article_id = published.json()["id"]
    publish = await client.post(
        f"{workspace_ctx.base}/articles/{article_id}/publish", headers=workspace_ctx.owner_headers
    )
    assert publish.status_code == 200, publish.text
    draft = await client.post(
        f"{workspace_ctx.base}/articles",
        json={"title": "Slack draft", "body": "Unpublished Slack notes."},
        headers=workspace_ctx.owner_headers,
    )
    assert draft.status_code == 201

    key = (await make_api_key(client, workspace_ctx))["key"]
    results = await call_tool(client, "search_articles", {"query": "Slack"}, key=key)
    assert [r["id"] for r in results] == [article_id]  # the draft is invisible
    assert results[0]["url"] and results[0]["url"].endswith("/articles/connecting-slack")

    article = await call_tool(client, "get_article", {"article_id": article_id}, key=key)
    assert article["title"] == "Connecting Slack"
    assert article["body_markdown"] == "Open settings and connect Slack."
    assert article["url"] == results[0]["url"]

    missing = await call_tool(client, "get_article", {"article_id": "nope"}, key=key)
    assert missing == {"error": "Article not found"}


async def test_create_document_ingests_and_audits(client, workspace_ctx):
    key = (await make_api_key(client, workspace_ctx, scopes=["read", "write"]))["key"]
    created = await call_tool(
        client,
        "create_document",
        {"title": "Escalation playbook", "content_markdown": "Escalate P1 incidents to on-call."},
        key=key,
    )
    assert created["status"] == "pending"
    await drain_tasks()

    document = await call_tool(client, "get_document", {"document_id": created["id"]}, key=key)
    assert "Escalate P1 incidents" in document["content_markdown"]
    assert document["source"] == "MCP documents"

    found = await call_tool(client, "search_knowledge", {"query": "escalation playbook"}, key=key)
    assert created["id"] in {r["document_id"] for r in found}

    from app.core.db import get_session_factory
    from app.models.audit import AuditLog

    async with get_session_factory()() as session:
        entry = (
            await session.execute(
                select(AuditLog).where(
                    AuditLog.workspace_id == workspace_ctx.id,
                    AuditLog.action == "knowledge.document.create",
                    AuditLog.target_id == created["id"],
                )
            )
        ).scalar_one()
        assert entry.actor_type == "api_key"
        assert entry.meta["via"] == "mcp"


async def test_create_document_unknown_source(client, workspace_ctx):
    key = (await make_api_key(client, workspace_ctx, scopes=["write"]))["key"]
    payload = await call_tool(
        client,
        "create_document",
        {
            "title": "T",
            "content_markdown": "b",
            "source_id": "00000000-0000-0000-0000-000000000000",
        },
        key=key,
    )
    assert payload == {"error": "Source not found"}
