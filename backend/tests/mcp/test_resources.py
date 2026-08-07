"""stept:// resource templates (markdown views over articles/documents/tours)."""

from __future__ import annotations

from tests.mcp.conftest import make_api_key, read_resource, rpc, seed_document
from tests.mcp.test_tour_tools import create_tour


async def test_templates_listed_and_auth_required(client, workspace_ctx):
    body = await rpc(client, "resources/templates/list")
    templates = {t["uriTemplate"] for t in body["result"]["resourceTemplates"]}
    assert {
        "stept://articles/{article_id}",
        "stept://documents/{document_id}",
        "stept://tours/{tour_id}",
    } <= templates

    text = await read_resource(client, "stept://documents/any")
    assert text == "Error: Authentication required. Provide a valid API key."


async def test_article_document_and_tour_resources(client, workspace_ctx):
    _, document_id = await seed_document(client, workspace_ctx)

    article = await client.post(
        f"{workspace_ctx.base}/articles",
        json={"title": "Exporting data", "body": "Use the export button."},
        headers=workspace_ctx.owner_headers,
    )
    article_id = article.json()["id"]
    published = await client.post(
        f"{workspace_ctx.base}/articles/{article_id}/publish", headers=workspace_ctx.owner_headers
    )
    assert published.status_code == 200

    tour_id = await create_tour(client, workspace_ctx, name="Resource tour")

    key = (await make_api_key(client, workspace_ctx))["key"]

    article_md = await read_resource(client, f"stept://articles/{article_id}", key=key)
    assert article_md.startswith("# Exporting data")
    assert "Use the export button." in article_md

    document_md = await read_resource(client, f"stept://documents/{document_id}", key=key)
    assert document_md.startswith("# Refund policy")
    assert "5 business days" in document_md

    tour_md = await read_resource(client, f"stept://tours/{tour_id}", key=key)
    assert tour_md.startswith("# Resource tour")
    assert "## Step 1: Inbox" in tour_md
    assert '[data-tour="inbox"]' in tour_md
