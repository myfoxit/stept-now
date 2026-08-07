"""Bulk conversation actions.

The invariants: bulk goes through the same service functions as single edits
(so trackers/activity/events still fire), one bad target doesn't roll back the
batch, targets come from ids *or* a filter but never both, and the whole thing
needs conversations:manage.
"""

from __future__ import annotations

from sqlalchemy import select

from app.models.conversation import Conversation
from app.models.message import Message, MessageVisibility
from app.services.bulk import MAX_TARGETS
from tests.slas.conftest import create_contact_via_db, create_inbox_via_api


async def _seed(client, ctx, count: int = 3) -> list[dict]:
    inbox = await create_inbox_via_api(client, ctx)
    out = []
    for index in range(count):
        contact_id = await create_contact_via_db(ctx.id, name=f"C{index}")
        response = await client.post(
            f"{ctx.base}/conversations",
            json={"contact_id": contact_id, "inbox_id": inbox["id"], "content": "Hi"},
            headers=ctx.owner_headers,
        )
        out.append(response.json())
    return out


class TestBulkActions:
    async def test_set_status_by_ids(self, client, workspace_ctx, session):
        ctx = workspace_ctx
        conversations = await _seed(client, ctx, 3)
        ids = [c["id"] for c in conversations[:2]]
        response = await client.post(
            f"{ctx.base}/conversations/bulk",
            json={
                "action": "set_status",
                "params": {"status": "resolved"},
                "conversation_ids": ids,
            },
            headers=ctx.owner_headers,
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"requested": 2, "succeeded": 2, "failed": 0, "errors": []}

        rows = {
            c.id: c.status
            for c in (
                await session.execute(
                    select(Conversation).where(Conversation.workspace_id == ctx.id)
                )
            ).scalars()
        }
        assert rows[ids[0]] == "resolved" and rows[ids[1]] == "resolved"
        assert rows[conversations[2]["id"]] == "open"

    async def test_bulk_writes_activity_like_a_single_edit(self, client, workspace_ctx, session):
        """Bulk must not be a shortcut around the normal write path — otherwise
        the audit trail silently rots."""
        ctx = workspace_ctx
        conversations = await _seed(client, ctx, 1)
        agent_headers = await ctx.add_member("bulk-agent@example.com", role="agent")
        me = (await client.get("/api/v1/me", headers=agent_headers)).json()["user"]

        await client.post(
            f"{ctx.base}/conversations/bulk",
            json={
                "action": "assign_user",
                "params": {"assignee_user_id": me["id"]},
                "conversation_ids": [conversations[0]["id"]],
            },
            headers=ctx.owner_headers,
        )
        notes = list(
            (
                await session.execute(
                    select(Message).where(
                        Message.conversation_id == conversations[0]["id"],
                        Message.visibility != MessageVisibility.PUBLIC,
                    )
                )
            ).scalars()
        )
        assert any("assigned" in n.content for n in notes)

    async def test_targets_by_filter(self, client, workspace_ctx):
        ctx = workspace_ctx
        conversations = await _seed(client, ctx, 3)
        await client.patch(
            f"{ctx.base}/conversations/{conversations[0]['id']}",
            json={"priority": "urgent"},
            headers=ctx.owner_headers,
        )
        response = await client.post(
            f"{ctx.base}/conversations/bulk",
            json={
                "action": "set_status",
                "params": {"status": "resolved"},
                "query": {
                    "match": "all",
                    "conditions": [{"field": "priority", "op": "eq", "value": "urgent"}],
                },
            },
            headers=ctx.owner_headers,
        )
        assert response.json()["succeeded"] == 1

    async def test_assign_self(self, client, workspace_ctx):
        ctx = workspace_ctx
        conversations = await _seed(client, ctx, 1)
        response = await client.post(
            f"{ctx.base}/conversations/bulk",
            json={
                "action": "assign_user",
                "params": {"assignee_user_id": "self"},
                "conversation_ids": [conversations[0]["id"]],
            },
            headers=ctx.owner_headers,
        )
        assert response.json()["succeeded"] == 1
        detail = await client.get(
            f"{ctx.base}/conversations/{conversations[0]['id']}", headers=ctx.owner_headers
        )
        assert detail.json()["assignee"]["name"] == "Owner"

    async def test_one_bad_target_does_not_abort_the_batch(self, client, workspace_ctx):
        ctx = workspace_ctx
        conversations = await _seed(client, ctx, 2)
        response = await client.post(
            f"{ctx.base}/conversations/bulk",
            json={
                "action": "add_tag",
                "params": {"tag_id": "01890000-0000-7000-8000-00000000dead"},
                "conversation_ids": [c["id"] for c in conversations],
            },
            headers=ctx.owner_headers,
        )
        body = response.json()
        assert body["requested"] == 2
        assert body["succeeded"] == 0
        assert body["failed"] == 2
        assert len(body["errors"]) == 2

    async def test_add_and_remove_tag(self, client, workspace_ctx):
        ctx = workspace_ctx
        conversations = await _seed(client, ctx, 2)
        tag = await client.post(
            f"{ctx.base}/tags", json={"name": "billing"}, headers=ctx.owner_headers
        )
        tag_id = tag.json()["id"]
        ids = [c["id"] for c in conversations]

        added = await client.post(
            f"{ctx.base}/conversations/bulk",
            json={"action": "add_tag", "params": {"tag_id": tag_id}, "conversation_ids": ids},
            headers=ctx.owner_headers,
        )
        assert added.json()["succeeded"] == 2
        detail = await client.get(f"{ctx.base}/conversations/{ids[0]}", headers=ctx.owner_headers)
        assert tag_id in detail.json()["tag_ids"]

        removed = await client.post(
            f"{ctx.base}/conversations/bulk",
            json={"action": "remove_tag", "params": {"tag_id": tag_id}, "conversation_ids": ids},
            headers=ctx.owner_headers,
        )
        assert removed.json()["succeeded"] == 2

    async def test_snooze_requires_a_time(self, client, workspace_ctx):
        ctx = workspace_ctx
        conversations = await _seed(client, ctx, 1)
        response = await client.post(
            f"{ctx.base}/conversations/bulk",
            json={
                "action": "set_status",
                "params": {"status": "snoozed"},
                "conversation_ids": [conversations[0]["id"]],
            },
            headers=ctx.owner_headers,
        )
        assert response.status_code == 422, response.text

    async def test_ids_and_query_are_mutually_exclusive(self, client, workspace_ctx):
        ctx = workspace_ctx
        response = await client.post(
            f"{ctx.base}/conversations/bulk",
            json={
                "action": "set_status",
                "params": {"status": "resolved"},
                "conversation_ids": ["x"],
                "query": {"match": "all", "conditions": []},
            },
            headers=ctx.owner_headers,
        )
        assert response.status_code == 422

    async def test_neither_ids_nor_query_is_rejected(self, client, workspace_ctx):
        ctx = workspace_ctx
        response = await client.post(
            f"{ctx.base}/conversations/bulk",
            json={"action": "set_status", "params": {"status": "resolved"}},
            headers=ctx.owner_headers,
        )
        assert response.status_code == 422

    async def test_cross_workspace_ids_are_silently_skipped(self, client, workspace_ctx):
        """Ids from another workspace must never resolve — they're filtered by
        the workspace scope, so they simply aren't targets."""
        ctx = workspace_ctx
        conversations = await _seed(client, ctx, 1)
        other = await client.post(
            "/api/v1/workspaces", json={"name": "Other"}, headers=ctx.owner_headers
        )
        other_base = f"/api/v1/w/{other.json()['id']}"
        response = await client.post(
            f"{other_base}/conversations/bulk",
            json={
                "action": "set_status",
                "params": {"status": "resolved"},
                "conversation_ids": [conversations[0]["id"]],
            },
            headers=ctx.owner_headers,
        )
        assert response.json()["requested"] == 0

    async def test_viewer_cannot_run_bulk_actions(self, client, workspace_ctx):
        ctx = workspace_ctx
        conversations = await _seed(client, ctx, 1)
        viewer_headers = await ctx.add_member("bulk-viewer@example.com", role="viewer")
        response = await client.post(
            f"{ctx.base}/conversations/bulk",
            json={
                "action": "set_status",
                "params": {"status": "resolved"},
                "conversation_ids": [conversations[0]["id"]],
            },
            headers=viewer_headers,
        )
        assert response.status_code == 403

    async def test_too_many_ids_is_rejected(self, client, workspace_ctx):
        ctx = workspace_ctx
        response = await client.post(
            f"{ctx.base}/conversations/bulk",
            json={
                "action": "set_status",
                "params": {"status": "resolved"},
                "conversation_ids": [f"id-{i}" for i in range(MAX_TARGETS + 1)],
            },
            headers=ctx.owner_headers,
        )
        assert response.status_code == 422
