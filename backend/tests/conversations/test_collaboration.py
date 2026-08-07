"""Participants and @mentions.

The rules under test: notes create mentions, public replies never do, an
explicit "leave" survives implicit re-adds, and mentions only ever resolve to
members of the same workspace.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.collaboration import ConversationParticipant, Mention
from app.models.notification import Notification
from app.services import collaboration
from tests.slas.conftest import create_contact_via_db, create_inbox_via_api


async def _conversation(client, ctx) -> dict:
    inbox = await create_inbox_via_api(client, ctx)
    contact_id = await create_contact_via_db(ctx.id, name="Nina")
    response = await client.post(
        f"{ctx.base}/conversations",
        json={"contact_id": contact_id, "inbox_id": inbox["id"], "content": "Hi"},
        headers=ctx.owner_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _note(client, ctx, conversation_id: str, content: str, headers=None):
    return await client.post(
        f"{ctx.base}/conversations/{conversation_id}/messages",
        json={"content": content, "visibility": "note"},
        headers=headers or ctx.owner_headers,
    )


class TestMentionParsing:
    def test_extracts_names_longest_first(self):
        names = collaboration.extract_mention_names("cc @Ada Lovelace and @bob please")
        assert names[0].lower().startswith("ada lovelace")
        assert any(n.lower() == "bob" for n in names)

    def test_ignores_text_without_handles(self):
        assert collaboration.extract_mention_names("no handles here") == []

    def test_email_like_text_is_not_a_mention_of_the_domain(self):
        # "@example.com" parses as a candidate but resolves to nobody.
        assert "example.com" in [
            n.lower() for n in collaboration.extract_mention_names("mail me@example.com")
        ]


class TestMentions:
    async def test_note_mention_notifies_and_subscribes(self, client, workspace_ctx, session):
        ctx = workspace_ctx
        await ctx.add_member("ada@example.com", role="agent")
        conversation = await _conversation(client, ctx)

        response = await _note(client, ctx, conversation["id"], "@Test User can you look?")
        assert response.status_code == 201, response.text

        mentions = list(
            (
                await session.execute(
                    select(Mention).where(Mention.conversation_id == conversation["id"])
                )
            ).scalars()
        )
        assert len(mentions) == 1

        notifications = list(
            (
                await session.execute(select(Notification).where(Notification.type == "mention"))
            ).scalars()
        )
        assert len(notifications) == 1
        assert notifications[0].link == f"/inbox/{conversation['id']}"

        participants = list(
            (
                await session.execute(
                    select(ConversationParticipant).where(
                        ConversationParticipant.conversation_id == conversation["id"]
                    )
                )
            ).scalars()
        )
        # the note author (owner) + the mentioned member
        assert {p.user_id for p in participants} == {mentions[0].user_id, mentions[0].author_id}

    async def test_public_reply_never_creates_a_mention(self, client, workspace_ctx, session):
        """An @handle in a customer-visible reply would leak the handle to the
        contact, so mentions are note-only."""
        ctx = workspace_ctx
        await ctx.add_member("ada2@example.com", role="agent")
        conversation = await _conversation(client, ctx)
        response = await client.post(
            f"{ctx.base}/conversations/{conversation['id']}/messages",
            json={"content": "@Test User take a look", "visibility": "public"},
            headers=ctx.owner_headers,
        )
        assert response.status_code == 201, response.text
        mentions = list((await session.execute(select(Mention))).scalars())
        assert mentions == []

    async def test_self_mention_is_a_noop(self, client, workspace_ctx, session):
        ctx = workspace_ctx
        conversation = await _conversation(client, ctx)
        await _note(client, ctx, conversation["id"], "note to self @Owner")
        assert list((await session.execute(select(Mention))).scalars()) == []

    async def test_unknown_handle_is_ignored(self, client, workspace_ctx, session):
        ctx = workspace_ctx
        conversation = await _conversation(client, ctx)
        await _note(client, ctx, conversation["id"], "@nobody-here please help")
        assert list((await session.execute(select(Mention))).scalars()) == []

    async def test_mentions_feed_and_mark_read(self, client, workspace_ctx):
        ctx = workspace_ctx
        agent_headers = await ctx.add_member("ada3@example.com", role="agent")
        conversation = await _conversation(client, ctx)
        await _note(client, ctx, conversation["id"], "@Test User please review")

        feed = await client.get(f"{ctx.base}/mentions", headers=agent_headers)
        assert feed.status_code == 200, feed.text
        items = feed.json()
        assert len(items) == 1
        assert items[0]["conversation_id"] == conversation["id"]
        assert items[0]["read_at"] is None
        assert "please review" in items[0]["excerpt"]

        marked = await client.post(
            f"{ctx.base}/mentions/read", json={"conversation_id": None}, headers=agent_headers
        )
        assert marked.json()["marked"] == 1
        unread = await client.get(f"{ctx.base}/mentions?unread_only=true", headers=agent_headers)
        assert unread.json() == []

    async def test_owner_does_not_see_another_members_mentions(self, client, workspace_ctx):
        ctx = workspace_ctx
        await ctx.add_member("ada4@example.com", role="agent")
        conversation = await _conversation(client, ctx)
        await _note(client, ctx, conversation["id"], "@Test User please review")
        mine = await client.get(f"{ctx.base}/mentions", headers=ctx.owner_headers)
        assert mine.json() == []


class TestParticipants:
    async def test_add_and_list(self, client, workspace_ctx):
        ctx = workspace_ctx
        agent_headers = await ctx.add_member("watcher@example.com", role="agent")
        me = (await client.get("/api/v1/me", headers=agent_headers)).json()["user"]
        conversation = await _conversation(client, ctx)

        added = await client.post(
            f"{ctx.base}/conversations/{conversation['id']}/participants",
            json={"user_id": me["id"]},
            headers=ctx.owner_headers,
        )
        assert added.status_code == 201, added.text
        assert me["id"] in {p["user_id"] for p in added.json()}

    async def test_assignment_subscribes_the_assignee(self, client, workspace_ctx):
        ctx = workspace_ctx
        agent_headers = await ctx.add_member("assignee@example.com", role="agent")
        me = (await client.get("/api/v1/me", headers=agent_headers)).json()["user"]
        conversation = await _conversation(client, ctx)

        await client.patch(
            f"{ctx.base}/conversations/{conversation['id']}",
            json={"assignee_user_id": me["id"]},
            headers=ctx.owner_headers,
        )
        listed = await client.get(
            f"{ctx.base}/conversations/{conversation['id']}/participants",
            headers=ctx.owner_headers,
        )
        assert me["id"] in {p["user_id"] for p in listed.json()}

    async def test_leaving_survives_an_implicit_re_add(self, client, workspace_ctx):
        """Someone who explicitly left must stay muted when a later note or
        assignment would otherwise re-subscribe them."""
        ctx = workspace_ctx
        agent_headers = await ctx.add_member("quiet@example.com", role="agent")
        me = (await client.get("/api/v1/me", headers=agent_headers)).json()["user"]
        conversation = await _conversation(client, ctx)

        await client.patch(
            f"{ctx.base}/conversations/{conversation['id']}",
            json={"assignee_user_id": me["id"]},
            headers=ctx.owner_headers,
        )
        left = await client.delete(
            f"{ctx.base}/conversations/{conversation['id']}/participants/{me['id']}",
            headers=agent_headers,
        )
        assert left.status_code == 200, left.text

        # An implicit re-add (re-assignment) must not un-mute.
        await client.patch(
            f"{ctx.base}/conversations/{conversation['id']}",
            json={"assignee_user_id": None},
            headers=ctx.owner_headers,
        )
        await client.patch(
            f"{ctx.base}/conversations/{conversation['id']}",
            json={"assignee_user_id": me["id"]},
            headers=ctx.owner_headers,
        )
        listed = await client.get(
            f"{ctx.base}/conversations/{conversation['id']}/participants",
            headers=ctx.owner_headers,
        )
        row = next(p for p in listed.json() if p["user_id"] == me["id"])
        assert row["muted"] is True

        # An explicit add does un-mute.
        await client.post(
            f"{ctx.base}/conversations/{conversation['id']}/participants",
            json={"user_id": me["id"]},
            headers=agent_headers,
        )
        listed = await client.get(
            f"{ctx.base}/conversations/{conversation['id']}/participants",
            headers=ctx.owner_headers,
        )
        row = next(p for p in listed.json() if p["user_id"] == me["id"])
        assert row["muted"] is False

    async def test_non_member_cannot_be_added(self, client, workspace_ctx):
        ctx = workspace_ctx
        conversation = await _conversation(client, ctx)
        response = await client.post(
            f"{ctx.base}/conversations/{conversation['id']}/participants",
            json={"user_id": "01890000-0000-7000-8000-000000000000"},
            headers=ctx.owner_headers,
        )
        assert response.status_code == 422, response.text

    async def test_cross_workspace_conversation_is_not_found(self, client, workspace_ctx):
        ctx = workspace_ctx
        conversation = await _conversation(client, ctx)
        other = await client.post(
            "/api/v1/workspaces", json={"name": "Other"}, headers=ctx.owner_headers
        )
        other_base = f"/api/v1/w/{other.json()['id']}"
        response = await client.get(
            f"{other_base}/conversations/{conversation['id']}/participants",
            headers=ctx.owner_headers,
        )
        assert response.status_code == 404


class TestActivityEntriesAreExcluded:
    """Activity entries are generated prose, not authored participation."""

    async def test_status_change_does_not_add_the_actor_as_a_watcher(
        self, client, workspace_ctx, session
    ):
        ctx = workspace_ctx
        conversation = await _conversation(client, ctx)
        # The owner already watches (they sent the opening message), so use an
        # admin who has touched nothing else.
        admin_headers = await ctx.add_member("activity-admin@example.com", role="admin")
        me = (await client.get("/api/v1/me", headers=admin_headers)).json()["user"]

        await client.patch(
            f"{ctx.base}/conversations/{conversation['id']}",
            json={"status": "resolved"},
            headers=admin_headers,
        )
        session.expire_all()
        rows = list(
            (
                await session.execute(
                    select(ConversationParticipant).where(
                        ConversationParticipant.conversation_id == conversation["id"]
                    )
                )
            ).scalars()
        )
        assert me["id"] not in {row.user_id for row in rows}

    async def test_an_at_sign_in_activity_text_creates_no_mention(
        self, client, workspace_ctx, session
    ):
        ctx = workspace_ctx
        await ctx.add_member("ada5@example.com", role="agent")
        conversation = await _conversation(client, ctx)
        # Assignment writes "<label> assigned the conversation to <name>" as an
        # activity entry; even if a display name contained a handle it must not
        # be parsed as a mention.
        agent = (await client.get(f"{ctx.base}/members", headers=ctx.owner_headers)).json()
        target = next(m for m in agent if m["user"]["email"] == "ada5@example.com")
        await client.patch(
            f"{ctx.base}/conversations/{conversation['id']}",
            json={"assignee_user_id": target["user"]["id"]},
            headers=ctx.owner_headers,
        )
        session.expire_all()
        assert list((await session.execute(select(Mention))).scalars()) == []
