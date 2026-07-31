"""Global search: cross-entity results, permission filtering, and authz."""

from __future__ import annotations

from app.core.db import get_session_factory, utcnow
from app.core.security import create_access_token
from app.models.article import Article
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.models.knowledge import Chunk, Document, KnowledgeSource
from app.models.user import User
from app.models.workspace import CustomRole, Membership, Workspace
from tests.conftest import bearer, signup
from tests.reports.conftest import make_inbox


async def _build_search_data(workspace_id: str) -> None:
    """Seed one entity per section that matches the query 'refund'."""
    async with get_session_factory()() as session:
        ws = await session.get(Workspace, workspace_id)
        assert ws is not None
        inbox = await make_inbox(session, ws, channel_type="widget")

        contact = Contact(workspace_id=workspace_id, name="Refund Rachel", email="rachel@acme.com")
        session.add(contact)
        await session.flush()

        session.add(
            Conversation(
                workspace_id=workspace_id,
                number=1,
                inbox_id=inbox.id,
                contact_id=contact.id,
                subject="Refund request",
                last_activity_at=utcnow(),
            )
        )
        session.add(
            Article(
                workspace_id=workspace_id,
                title="Refund policy",
                slug="refund-policy",
                body="How to request a refund.",
                status="published",
            )
        )

        source = KnowledgeSource(workspace_id=workspace_id, type="text", name="Docs")
        session.add(source)
        await session.flush()
        document = Document(
            workspace_id=workspace_id, source_id=source.id, title="Refund policy", status="indexed"
        )
        session.add(document)
        await session.flush()
        session.add(
            Chunk(
                workspace_id=workspace_id,
                document_id=document.id,
                ord=0,
                content="Our refund policy allows a refund within 30 days of purchase.",
                embedding=None,
                meta={"title": "Refund policy"},
            )
        )
        await session.commit()


async def test_search_across_entities(client, workspace_ctx):
    await _build_search_data(workspace_ctx.id)

    resp = await client.get(
        f"{workspace_ctx.base}/search?q=refund", headers=workspace_ctx.owner_headers
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert len(body["conversations"]) == 1
    assert body["conversations"][0]["subject"] == "Refund request"
    assert body["conversations"][0]["contact_name"] == "Refund Rachel"

    assert len(body["contacts"]) == 1
    assert body["contacts"][0]["name"] == "Refund Rachel"

    assert len(body["articles"]) == 1
    assert body["articles"][0]["slug"] == "refund-policy"

    assert len(body["documents"]) == 1
    doc = body["documents"][0]
    assert doc["title"] == "Refund policy"
    assert "refund" in doc["snippet"].lower()
    assert doc["score"] > 0


async def test_search_by_conversation_number(client, workspace_ctx):
    await _build_search_data(workspace_ctx.id)
    resp = await client.get(f"{workspace_ctx.base}/search?q=1", headers=workspace_ctx.owner_headers)
    assert resp.status_code == 200
    assert 1 in [c["number"] for c in resp.json()["conversations"]]


async def test_search_empty_query_returns_empty_sections(client, workspace_ctx):
    await _build_search_data(workspace_ctx.id)
    resp = await client.get(f"{workspace_ctx.base}/search?q=", headers=workspace_ctx.owner_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["conversations"] == []
    assert body["contacts"] == []
    assert body["articles"] == []
    assert body["documents"] == []


async def test_search_permission_filtering(client, workspace_ctx):
    await _build_search_data(workspace_ctx.id)

    # A custom-role member who can read only conversations.
    await signup(client, "limited@example.com", name="Limited")
    async with get_session_factory()() as session:
        user = (
            await session.execute(
                User.__table__.select().where(User.email == "limited@example.com")
            )
        ).first()
        assert user is not None
        role = CustomRole(
            workspace_id=workspace_ctx.id, name="convo-only", permissions=["conversations:read"]
        )
        session.add(role)
        await session.flush()
        session.add(
            Membership(
                workspace_id=workspace_ctx.id,
                user_id=user.id,
                role="custom",
                custom_role_id=role.id,
            )
        )
        await session.commit()
        token = create_access_token(user.id)

    resp = await client.get(
        f"{workspace_ctx.base}/search?q=refund", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "conversations" in body
    assert len(body["conversations"]) == 1
    # sections the caller cannot read are omitted entirely
    assert "contacts" not in body
    assert "articles" not in body
    assert "documents" not in body


async def test_search_authz_cross_workspace(client, workspace_ctx):
    intruder = await signup(client, "intruder-search@example.com")
    await client.post("/api/v1/workspaces", json={"name": "Theirs"}, headers=bearer(intruder))
    resp = await client.get(f"{workspace_ctx.base}/search?q=refund", headers=bearer(intruder))
    assert resp.status_code == 403


async def test_search_workspace_isolation(client, workspace_ctx):
    await _build_search_data(workspace_ctx.id)

    other = await client.post(
        "/api/v1/workspaces", json={"name": "Second"}, headers=workspace_ctx.owner_headers
    )
    other_id = other.json()["id"]
    resp = await client.get(
        f"/api/v1/w/{other_id}/search?q=refund", headers=workspace_ctx.owner_headers
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["conversations"] == []
    assert body["articles"] == []
    assert body["documents"] == []
