"""App-side message feedback endpoints: happy path, upsert, authz, 404s."""

from __future__ import annotations

from app.core.db import uuid7
from tests.analytics.conftest import seed_conversation_message
from tests.conftest import bearer, signup


def _url(base: str, conversation_id: str, message_id: str) -> str:
    return f"{base}/conversations/{conversation_id}/messages/{message_id}/feedback"


async def test_submit_feedback_and_upsert(client, workspace_ctx):
    conversation_id, message_id = await seed_conversation_message(workspace_ctx.id)
    url = _url(workspace_ctx.base, conversation_id, message_id)

    response = await client.post(
        url, json={"rating": "up", "comment": "great answer"}, headers=workspace_ctx.owner_headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rating"] == "up"
    assert body["comment"] == "great answer"
    assert body["actor_type"] == "user"
    assert body["message_id"] == message_id
    assert body["conversation_id"] == conversation_id

    # same user re-rates → same row updated, no duplicate
    again = await client.post(url, json={"rating": "down"}, headers=workspace_ctx.owner_headers)
    assert again.status_code == 200, again.text
    assert again.json()["id"] == body["id"]
    assert again.json()["rating"] == "down"

    listing = await client.get(url, headers=workspace_ctx.owner_headers)
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) == 1
    assert rows[0]["rating"] == "down"


async def test_feedback_invalid_rating_422(client, workspace_ctx):
    conversation_id, message_id = await seed_conversation_message(workspace_ctx.id)
    response = await client.post(
        _url(workspace_ctx.base, conversation_id, message_id),
        json={"rating": "meh"},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 422


async def test_feedback_message_not_in_conversation_404(client, workspace_ctx):
    conversation_id, _ = await seed_conversation_message(workspace_ctx.id, number=1)
    _, other_message_id = await seed_conversation_message(workspace_ctx.id, number=2)
    response = await client.post(
        _url(workspace_ctx.base, conversation_id, other_message_id),
        json={"rating": "up"},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 404


async def test_feedback_unknown_conversation_404(client, workspace_ctx):
    response = await client.post(
        _url(workspace_ctx.base, uuid7(), uuid7()),
        json={"rating": "up"},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 404


async def test_feedback_cross_workspace_404(client, workspace_ctx):
    conversation_id, message_id = await seed_conversation_message(workspace_ctx.id)
    rival_auth = await signup(client, "rival-feedback@example.com")
    rival_ws = (
        await client.post(
            "/api/v1/workspaces", json={"name": "Rival Co"}, headers=bearer(rival_auth)
        )
    ).json()
    response = await client.post(
        _url(f"/api/v1/w/{rival_ws['id']}", conversation_id, message_id),
        json={"rating": "up"},
        headers=bearer(rival_auth),
    )
    assert response.status_code == 404


async def test_feedback_viewer_403_but_can_read(client, workspace_ctx):
    conversation_id, message_id = await seed_conversation_message(workspace_ctx.id)
    viewer_headers = await workspace_ctx.add_member("viewer-fb@example.com", role="viewer")
    url = _url(workspace_ctx.base, conversation_id, message_id)

    denied = await client.post(url, json={"rating": "up"}, headers=viewer_headers)
    assert denied.status_code == 403  # no conversations:write

    allowed = await client.get(url, headers=viewer_headers)
    assert allowed.status_code == 200  # conversations:read is enough to see thumbs state
    assert allowed.json() == []
