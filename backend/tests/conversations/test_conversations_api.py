"""Conversations API: outbound start, detail, list filters + cursor, messages,
tags, read state, counts, authz."""

from __future__ import annotations

import pytest

from app.core.db import get_session_factory, uuid7
from app.core.events import Actor
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.services import conversations as convs
from tests.conftest import bearer, signup
from tests.conversations.conftest import (
    create_contact_via_db,
    create_inbox_via_api,
    start_conversation,
)


async def _add_inbound(conversation_id: str, content: str = "customer message") -> None:
    """Inbound messages arrive via channels — simulated at the service layer."""
    async with get_session_factory()() as session:
        conversation = await session.get(Conversation, conversation_id)
        assert conversation is not None
        contact = await session.get(Contact, conversation.contact_id)
        assert contact is not None
        await convs.add_message(
            session,
            conversation,
            direction="in",
            author_type="contact",
            author_id=contact.id,
            author_name=contact.name,
            content=content,
            actor=Actor(type="contact", id=contact.id),
        )
        await session.commit()


async def _make_tag_id(workspace_id: str) -> str:
    """A real Tag row when the directory agent's model exists, else a bare id."""
    try:
        from app.models.tag import Tag
    except ImportError:
        return uuid7()
    try:
        async with get_session_factory()() as session:
            tag = Tag(workspace_id=workspace_id, name=f"tag-{uuid7()[:8]}", color="#22c55e")
            session.add(tag)
            await session.commit()
            return tag.id
    except Exception:  # pragma: no cover — partially-built Tag model mid-wave
        pytest.skip("Tag model exists but is not constructible yet")


async def test_outbound_start_creates_thread(client, workspace_ctx):
    contact_id = await create_contact_via_db(
        workspace_ctx.id, name="Maya Chen", email="maya@acme.io"
    )
    inbox = await create_inbox_via_api(client, workspace_ctx)
    conversation = await start_conversation(
        client,
        workspace_ctx,
        contact_id=contact_id,
        inbox_id=inbox["id"],
        content="Hi Maya — following up on your trial.",
        subject="Trial follow-up",
    )
    assert conversation["number"] == 1
    assert conversation["status"] == "open"
    assert conversation["subject"] == "Trial follow-up"
    assert conversation["first_reply_at"] is not None  # outbound user message
    assert conversation["waiting_since"] is None
    assert conversation["contact"]["email"] == "maya@acme.io"

    messages = await client.get(
        f"{workspace_ctx.base}/conversations/{conversation['id']}/messages",
        headers=workspace_ctx.owner_headers,
    )
    items = messages.json()["items"]
    assert len(items) == 1
    assert items[0]["direction"] == "out"
    assert items[0]["author_type"] == "user"
    assert items[0]["delivery_status"] == "sent"  # api channel delivers in-app


async def test_detail_includes_contact_attributes(client, workspace_ctx):
    contact_id = await create_contact_via_db(
        workspace_ctx.id,
        name="Priya Patel",
        email="priya@globex.com",
        attributes={"plan": "enterprise", "company": "Globex"},
    )
    inbox = await create_inbox_via_api(client, workspace_ctx)
    conversation = await start_conversation(
        client, workspace_ctx, contact_id=contact_id, inbox_id=inbox["id"]
    )
    detail = await client.get(
        f"{workspace_ctx.base}/conversations/{conversation['id']}",
        headers=workspace_ctx.owner_headers,
    )
    body = detail.json()
    assert body["contact"]["attributes"] == {"plan": "enterprise", "company": "Globex"}
    assert body["inbox"]["channel_type"] == "api"
    assert body["tag_ids"] == []


async def test_missing_contact_or_cross_workspace_conversation_404(client, workspace_ctx):
    inbox = await create_inbox_via_api(client, workspace_ctx)
    ghost = await client.post(
        f"{workspace_ctx.base}/conversations",
        json={"contact_id": uuid7(), "inbox_id": inbox["id"], "content": "hi"},
        headers=workspace_ctx.owner_headers,
    )
    assert ghost.status_code == 404

    # A conversation from another workspace is invisible (404, not 403 leak).
    contact_id = await create_contact_via_db(workspace_ctx.id)
    conversation = await start_conversation(
        client, workspace_ctx, contact_id=contact_id, inbox_id=inbox["id"]
    )
    other_auth = await signup(client, "other-owner@example.com")
    other_ws = await client.post(
        "/api/v1/workspaces", json={"name": "Other Corp"}, headers=bearer(other_auth)
    )
    other_base = f"/api/v1/w/{other_ws.json()['id']}"
    stolen = await client.get(
        f"{other_base}/conversations/{conversation['id']}", headers=bearer(other_auth)
    )
    assert stolen.status_code == 404

    outsider = await client.get(f"{workspace_ctx.base}/conversations", headers=bearer(other_auth))
    assert outsider.status_code == 403


async def test_list_filters(client, workspace_ctx):
    alice = await create_contact_via_db(workspace_ctx.id, name="Alice", email="alice@wonder.land")
    bob = await create_contact_via_db(workspace_ctx.id, name="Bob", email="bob@builder.dev")
    inbox_a = await create_inbox_via_api(client, workspace_ctx, name="A")
    inbox_b = await create_inbox_via_api(client, workspace_ctx, name="B")
    headers = workspace_ctx.owner_headers
    me = (await client.get("/api/v1/me", headers=headers)).json()["user"]["id"]

    c1 = await start_conversation(
        client,
        workspace_ctx,
        contact_id=alice,
        inbox_id=inbox_a["id"],
        subject="Billing double charge",
    )
    c2 = await start_conversation(
        client, workspace_ctx, contact_id=bob, inbox_id=inbox_a["id"], subject="Widget install"
    )
    c3 = await start_conversation(
        client, workspace_ctx, contact_id=alice, inbox_id=inbox_b["id"], subject="Feature idea"
    )
    base = f"{workspace_ctx.base}/conversations"
    await client.patch(
        f"{base}/{c1['id']}",
        json={"assignee_user_id": me, "priority": "urgent"},
        headers=headers,
    )
    await client.patch(f"{base}/{c3['id']}", json={"status": "resolved"}, headers=headers)

    async def ids(url: str) -> set[str]:
        response = await client.get(url, headers=headers)
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["items"]}

    assert await ids(f"{base}?status=open") == {c1["id"], c2["id"]}
    assert await ids(f"{base}?status=open&status=resolved") == {c1["id"], c2["id"], c3["id"]}
    assert await ids(f"{base}?inbox_id={inbox_b['id']}") == {c3["id"]}
    assert await ids(f"{base}?assignee=me") == {c1["id"]}
    assert await ids(f"{base}?assignee=unassigned&status=open") == {c2["id"]}
    assert await ids(f"{base}?priority=urgent") == {c1["id"]}
    assert await ids(f"{base}?contact_id={alice}") == {c1["id"], c3["id"]}
    assert await ids(f"{base}?q=wonder.land") == {c1["id"], c3["id"]}  # contact email
    assert await ids(f"{base}?q=Billing") == {c1["id"]}  # subject
    assert await ids(f"{base}?q=alice") == {c1["id"], c3["id"]}  # contact name/email

    bad = await client.get(f"{base}?status=nope", headers=headers)
    assert bad.status_code == 422


async def test_list_item_shape_preview_and_unread(client, workspace_ctx):
    contact_id = await create_contact_via_db(workspace_ctx.id, name="Lars", email="l@n.dev")
    inbox = await create_inbox_via_api(client, workspace_ctx)
    conversation = await start_conversation(
        client, workspace_ctx, contact_id=contact_id, inbox_id=inbox["id"]
    )
    long_text = "A" * 200
    await _add_inbound(conversation["id"], long_text)

    # A private note afterwards must not become the preview.
    await client.post(
        f"{workspace_ctx.base}/conversations/{conversation['id']}/messages",
        json={"content": "internal-only note", "visibility": "note"},
        headers=workspace_ctx.owner_headers,
    )

    listing = await client.get(
        f"{workspace_ctx.base}/conversations", headers=workspace_ctx.owner_headers
    )
    item = next(i for i in listing.json()["items"] if i["id"] == conversation["id"])
    assert item["number"] == conversation["number"]
    assert item["contact"]["name"] == "Lars"
    assert item["inbox"]["channel_type"] == "api"
    assert item["last_message_preview"] == "A" * 140  # truncated, note excluded
    assert item["unread"] is True
    assert item["waiting_since"] is not None

    read = await client.post(
        f"{workspace_ctx.base}/conversations/{conversation['id']}/read",
        headers=workspace_ctx.owner_headers,
    )
    assert read.status_code == 200
    listing = await client.get(
        f"{workspace_ctx.base}/conversations", headers=workspace_ctx.owner_headers
    )
    item = next(i for i in listing.json()["items"] if i["id"] == conversation["id"])
    assert item["unread"] is False


async def test_list_cursor_pagination(client, workspace_ctx):
    contact_id = await create_contact_via_db(workspace_ctx.id)
    inbox = await create_inbox_via_api(client, workspace_ctx)
    created = [
        (
            await start_conversation(
                client, workspace_ctx, contact_id=contact_id, inbox_id=inbox["id"], subject=f"#{i}"
            )
        )["id"]
        for i in range(7)
    ]
    base = f"{workspace_ctx.base}/conversations?limit=3"
    seen: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        url = base + (f"&cursor={cursor}" if cursor else "")
        page = (await client.get(url, headers=workspace_ctx.owner_headers)).json()
        seen.extend(item["id"] for item in page["items"])
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages == 3  # 3 + 3 + 1
    assert len(seen) == 7
    assert len(set(seen)) == 7  # no duplicates, no gaps
    assert seen == list(reversed(created))  # newest activity first


async def test_messages_pagination_desc_pages_asc_within(client, workspace_ctx):
    contact_id = await create_contact_via_db(workspace_ctx.id)
    inbox = await create_inbox_via_api(client, workspace_ctx)
    conversation = await start_conversation(
        client, workspace_ctx, contact_id=contact_id, inbox_id=inbox["id"], content="m0"
    )
    for i in range(1, 7):
        response = await client.post(
            f"{workspace_ctx.base}/conversations/{conversation['id']}/messages",
            json={"content": f"m{i}"},
            headers=workspace_ctx.owner_headers,
        )
        assert response.status_code == 201, response.text

    url = f"{workspace_ctx.base}/conversations/{conversation['id']}/messages?limit=3"
    first_page = (await client.get(url, headers=workspace_ctx.owner_headers)).json()
    assert [m["content"] for m in first_page["items"]] == ["m4", "m5", "m6"]  # asc within page

    second_page = (
        await client.get(
            f"{url}&cursor={first_page['next_cursor']}", headers=workspace_ctx.owner_headers
        )
    ).json()
    assert [m["content"] for m in second_page["items"]] == ["m1", "m2", "m3"]

    third_page = (
        await client.get(
            f"{url}&cursor={second_page['next_cursor']}", headers=workspace_ctx.owner_headers
        )
    ).json()
    assert [m["content"] for m in third_page["items"]] == ["m0"]
    assert third_page["next_cursor"] is None


async def test_post_message_with_attachments(client, workspace_ctx):
    contact_id = await create_contact_via_db(workspace_ctx.id)
    inbox = await create_inbox_via_api(client, workspace_ctx)
    conversation = await start_conversation(
        client, workspace_ctx, contact_id=contact_id, inbox_id=inbox["id"]
    )
    attachment = {
        "key": "uploads/2026/screenshot.png",
        "name": "screenshot.png",
        "size": 48213,
        "content_type": "image/png",
    }
    response = await client.post(
        f"{workspace_ctx.base}/conversations/{conversation['id']}/messages",
        json={"content": "See attached screenshot", "attachments": [attachment]},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 201
    assert response.json()["attachments"] == [attachment]


async def test_tags_add_remove_and_filter(client, workspace_ctx):
    tag_id = await _make_tag_id(workspace_ctx.id)
    contact_id = await create_contact_via_db(workspace_ctx.id)
    inbox = await create_inbox_via_api(client, workspace_ctx)
    tagged = await start_conversation(
        client, workspace_ctx, contact_id=contact_id, inbox_id=inbox["id"]
    )
    await start_conversation(client, workspace_ctx, contact_id=contact_id, inbox_id=inbox["id"])

    added = await client.post(
        f"{workspace_ctx.base}/conversations/{tagged['id']}/tags",
        json={"tag_id": tag_id},
        headers=workspace_ctx.owner_headers,
    )
    assert added.status_code == 200, added.text
    assert added.json()["tag_ids"] == [tag_id]

    # idempotent re-add
    again = await client.post(
        f"{workspace_ctx.base}/conversations/{tagged['id']}/tags",
        json={"tag_id": tag_id},
        headers=workspace_ctx.owner_headers,
    )
    assert again.json()["tag_ids"] == [tag_id]

    filtered = await client.get(
        f"{workspace_ctx.base}/conversations?tag_id={tag_id}",
        headers=workspace_ctx.owner_headers,
    )
    items = filtered.json()["items"]
    assert [i["id"] for i in items] == [tagged["id"]]
    assert items[0]["tag_ids"] == [tag_id]

    removed = await client.delete(
        f"{workspace_ctx.base}/conversations/{tagged['id']}/tags/{tag_id}",
        headers=workspace_ctx.owner_headers,
    )
    assert removed.json()["tag_ids"] == []


async def test_counts_endpoint(client, workspace_ctx):
    contact_id = await create_contact_via_db(workspace_ctx.id)
    inbox = await create_inbox_via_api(client, workspace_ctx)
    headers = workspace_ctx.owner_headers
    me = (await client.get("/api/v1/me", headers=headers)).json()["user"]["id"]
    base = f"{workspace_ctx.base}/conversations"

    ids = [
        (
            await start_conversation(
                client, workspace_ctx, contact_id=contact_id, inbox_id=inbox["id"]
            )
        )["id"]
        for _ in range(5)
    ]
    await client.patch(f"{base}/{ids[0]}", json={"assignee_user_id": me}, headers=headers)
    await client.patch(f"{base}/{ids[2]}", json={"status": "resolved"}, headers=headers)
    await client.patch(f"{base}/{ids[3]}", json={"status": "snoozed"}, headers=headers)
    await client.patch(f"{base}/{ids[4]}", json={"status": "pending"}, headers=headers)

    counts = (await client.get(f"{base}/counts", headers=headers)).json()
    assert counts == {
        "open": 2,
        "unassigned": 1,
        "mine": 1,
        "pending": 1,
        "snoozed": 1,
        "resolved": 1,
    }


async def test_viewer_is_read_only_and_agent_can_manage(client, workspace_ctx):
    viewer_headers = await workspace_ctx.add_member("viewer@example.com", role="viewer")
    agent_headers = await workspace_ctx.add_member("agent2@example.com", role="agent")
    contact_id = await create_contact_via_db(workspace_ctx.id)
    inbox = await create_inbox_via_api(client, workspace_ctx)
    conversation = await start_conversation(
        client, workspace_ctx, contact_id=contact_id, inbox_id=inbox["id"]
    )
    base = f"{workspace_ctx.base}/conversations"

    assert (await client.get(base, headers=viewer_headers)).status_code == 200
    assert (
        await client.get(f"{base}/{conversation['id']}", headers=viewer_headers)
    ).status_code == 200

    denied_post = await client.post(
        base,
        json={"contact_id": contact_id, "inbox_id": inbox["id"], "content": "hi"},
        headers=viewer_headers,
    )
    assert denied_post.status_code == 403
    denied_message = await client.post(
        f"{base}/{conversation['id']}/messages",
        json={"content": "sneaky"},
        headers=viewer_headers,
    )
    assert denied_message.status_code == 403
    denied_patch = await client.patch(
        f"{base}/{conversation['id']}", json={"status": "resolved"}, headers=viewer_headers
    )
    assert denied_patch.status_code == 403
    denied_read = await client.post(f"{base}/{conversation['id']}/read", headers=viewer_headers)
    assert denied_read.status_code == 403

    # Agents hold conversations:manage.
    allowed = await client.patch(
        f"{base}/{conversation['id']}", json={"status": "resolved"}, headers=agent_headers
    )
    assert allowed.status_code == 200


async def test_search_matches_public_message_content(client, workspace_ctx):
    """Widget conversations have no subject — q must match public message
    bodies, but never internal notes."""
    contact = await create_contact_via_db(workspace_ctx.id, name="Nia", email="nia@example.com")
    inbox = await create_inbox_via_api(client, workspace_ctx, name="Widget search")
    headers = workspace_ctx.owner_headers

    conversation = await start_conversation(
        client, workspace_ctx, contact_id=contact, inbox_id=inbox["id"]
    )
    other = await start_conversation(
        client, workspace_ctx, contact_id=contact, inbox_id=inbox["id"]
    )
    await _add_inbound(conversation["id"], content="my zebra parcel vanished")
    note = await client.post(
        f"{workspace_ctx.base}/conversations/{other['id']}/messages",
        json={"content": "internal xylophone context", "visibility": "note"},
        headers=headers,
    )
    assert note.status_code == 201, note.text

    async def ids(url: str) -> set[str]:
        response = await client.get(url, headers=headers)
        assert response.status_code == 200, response.text
        return {item["id"] for item in response.json()["items"]}

    base = f"{workspace_ctx.base}/conversations"
    assert await ids(f"{base}?q=zebra") == {conversation["id"]}
    assert await ids(f"{base}?q=xylophone") == set()
