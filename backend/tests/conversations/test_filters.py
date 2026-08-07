"""The conversation filter DSL and the saved views / search endpoints built on it.

Covers happy path, authz (personal views are invisible cross-user; sharing needs
conversations:manage), and the edge case that pushed the design: `attributes.*`
conditions are Python post-filters, so they must not silently drop rows from a
paginated page — and they are rejected outright under `match: "any"`.
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationFailure
from app.services import filters
from tests.slas.conftest import create_contact_via_db, create_inbox_via_api

# ---------------------------------------------------------------------------
# compile-time validation (pure)
# ---------------------------------------------------------------------------


class TestCompile:
    def test_empty_document_compiles_to_nothing(self):
        condition, post, match = filters.compile_conversation_filter(None)
        assert condition is None and post == [] and match == "all"

    def test_unknown_field_is_rejected(self):
        with pytest.raises(ValidationFailure, match="Unknown filter field"):
            filters.compile_conversation_filter(
                {"conditions": [{"field": "nope", "op": "eq", "value": 1}]}
            )

    def test_operator_must_be_allowed_for_the_field(self):
        with pytest.raises(ValidationFailure, match="not supported"):
            filters.compile_conversation_filter(
                {"conditions": [{"field": "status", "op": "contains", "value": "op"}]}
            )

    def test_bad_match_mode_is_rejected(self):
        with pytest.raises(ValidationFailure, match="match must be"):
            filters.compile_conversation_filter({"match": "some", "conditions": []})

    def test_value_required_for_value_operators(self):
        with pytest.raises(ValidationFailure, match="needs a value"):
            filters.compile_conversation_filter(
                {"conditions": [{"field": "status", "op": "eq", "value": None}]}
            )

    def test_exists_needs_no_value(self):
        condition, _, _ = filters.compile_conversation_filter(
            {"conditions": [{"field": "assignee_user_id", "op": "not_exists"}]}
        )
        assert condition is not None

    def test_attributes_are_post_filters(self):
        condition, post, _ = filters.compile_conversation_filter(
            {"conditions": [{"field": "attributes.plan", "op": "eq", "value": "pro"}]}
        )
        assert condition is None and len(post) == 1

    def test_attributes_with_match_any_is_rejected(self):
        """A Python post-filter can only narrow a SQL result set. Under "any" a
        row could match on the attribute alone, which SQL never returned — so
        allowing it would silently under-report."""
        with pytest.raises(ValidationFailure, match="require match='all'"):
            filters.compile_conversation_filter(
                {
                    "match": "any",
                    "conditions": [
                        {"field": "status", "op": "eq", "value": "open"},
                        {"field": "attributes.plan", "op": "eq", "value": "pro"},
                    ],
                }
            )

    def test_within_days_needs_a_number(self):
        with pytest.raises(ValidationFailure, match="number of days"):
            filters.compile_conversation_filter(
                {"conditions": [{"field": "created_at", "op": "within_days", "value": "soon"}]}
            )


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


async def _seed(client, ctx, *, count: int = 3) -> tuple[str, list[dict]]:
    inbox = await create_inbox_via_api(client, ctx)
    conversations = []
    for index in range(count):
        contact_id = await create_contact_via_db(ctx.id, name=f"Contact {index}")
        response = await client.post(
            f"{ctx.base}/conversations",
            json={"contact_id": contact_id, "inbox_id": inbox["id"], "content": "Hi"},
            headers=ctx.owner_headers,
        )
        assert response.status_code == 201, response.text
        conversations.append(response.json())
    return inbox["id"], conversations


class TestSearchEndpoint:
    async def test_filters_by_status(self, client, workspace_ctx):
        ctx = workspace_ctx
        _, conversations = await _seed(client, ctx, count=3)
        await client.patch(
            f"{ctx.base}/conversations/{conversations[0]['id']}",
            json={"status": "resolved"},
            headers=ctx.owner_headers,
        )
        response = await client.post(
            f"{ctx.base}/conversations/search",
            json={
                "match": "all",
                "conditions": [{"field": "status", "op": "in", "value": ["resolved"]}],
            },
            headers=ctx.owner_headers,
        )
        assert response.status_code == 200, response.text
        ids = [item["id"] for item in response.json()["items"]]
        assert ids == [conversations[0]["id"]]

    async def test_match_any_unions(self, client, workspace_ctx):
        ctx = workspace_ctx
        _, conversations = await _seed(client, ctx, count=3)
        await client.patch(
            f"{ctx.base}/conversations/{conversations[0]['id']}",
            json={"status": "resolved"},
            headers=ctx.owner_headers,
        )
        await client.patch(
            f"{ctx.base}/conversations/{conversations[1]['id']}",
            json={"priority": "urgent"},
            headers=ctx.owner_headers,
        )
        response = await client.post(
            f"{ctx.base}/conversations/search",
            json={
                "match": "any",
                "conditions": [
                    {"field": "status", "op": "eq", "value": "resolved"},
                    {"field": "priority", "op": "eq", "value": "urgent"},
                ],
            },
            headers=ctx.owner_headers,
        )
        ids = {item["id"] for item in response.json()["items"]}
        assert ids == {conversations[0]["id"], conversations[1]["id"]}

    async def test_attribute_filter_paginates_without_losing_rows(self, client, workspace_ctx):
        """Post-filters run after the DB has already counted rows toward the
        page, so the service must keep fetching batches until the page is full.
        Six conversations, every other one tagged pro, page size 2."""
        ctx = workspace_ctx
        _, conversations = await _seed(client, ctx, count=6)
        expected = []
        for index, conversation in enumerate(conversations):
            if index % 2 == 0:
                await client.patch(
                    f"{ctx.base}/conversations/{conversation['id']}",
                    json={"attributes": {"plan": "pro"}},
                    headers=ctx.owner_headers,
                )
                expected.append(conversation["id"])

        seen: list[str] = []
        cursor = None
        for _ in range(10):  # bounded so a pagination bug fails instead of hanging
            url = f"{ctx.base}/conversations/search?limit=2"
            if cursor:
                url += f"&cursor={cursor}"
            response = await client.post(
                url,
                json={
                    "match": "all",
                    "conditions": [{"field": "attributes.plan", "op": "eq", "value": "pro"}],
                },
                headers=ctx.owner_headers,
            )
            assert response.status_code == 200, response.text
            body = response.json()
            seen.extend(item["id"] for item in body["items"])
            cursor = body["next_cursor"]
            if not cursor:
                break
        assert sorted(seen) == sorted(expected)
        assert len(seen) == len(set(seen)), "pagination returned a duplicate"

    async def test_invalid_document_is_a_422(self, client, workspace_ctx):
        ctx = workspace_ctx
        response = await client.post(
            f"{ctx.base}/conversations/search",
            json={"match": "all", "conditions": [{"field": "bogus", "op": "eq", "value": 1}]},
            headers=ctx.owner_headers,
        )
        assert response.status_code == 422, response.text


class TestSavedViews:
    async def test_create_list_and_apply(self, client, workspace_ctx):
        ctx = workspace_ctx
        _, conversations = await _seed(client, ctx, count=2)
        await client.patch(
            f"{ctx.base}/conversations/{conversations[0]['id']}",
            json={"priority": "high"},
            headers=ctx.owner_headers,
        )
        created = await client.post(
            f"{ctx.base}/views",
            json={
                "name": "High priority",
                "query": {
                    "match": "all",
                    "conditions": [{"field": "priority", "op": "eq", "value": "high"}],
                },
            },
            headers=ctx.owner_headers,
        )
        assert created.status_code == 201, created.text
        view_id = created.json()["id"]

        listed = await client.get(f"{ctx.base}/views", headers=ctx.owner_headers)
        assert [v["id"] for v in listed.json()] == [view_id]

        applied = await client.get(
            f"{ctx.base}/conversations?view_id={view_id}", headers=ctx.owner_headers
        )
        assert [i["id"] for i in applied.json()["items"]] == [conversations[0]["id"]]

    async def test_invalid_query_is_rejected_on_save(self, client, workspace_ctx):
        ctx = workspace_ctx
        response = await client.post(
            f"{ctx.base}/views",
            json={
                "name": "Broken",
                "query": {
                    "match": "all",
                    "conditions": [{"field": "nope", "op": "eq", "value": 1}],
                },
            },
            headers=ctx.owner_headers,
        )
        assert response.status_code == 422, response.text

    async def test_personal_view_is_invisible_to_other_members(self, client, workspace_ctx):
        ctx = workspace_ctx
        agent_headers = await ctx.add_member("agent@example.com", role="agent")
        created = await client.post(
            f"{ctx.base}/views",
            json={
                "name": "Mine",
                "visibility": "personal",
                "query": {"match": "all", "conditions": []},
            },
            headers=ctx.owner_headers,
        )
        view_id = created.json()["id"]

        assert (await client.get(f"{ctx.base}/views", headers=agent_headers)).json() == []
        blocked = await client.patch(
            f"{ctx.base}/views/{view_id}", json={"name": "Theirs"}, headers=agent_headers
        )
        assert blocked.status_code == 404

    async def test_shared_views_are_visible_to_everyone(self, client, workspace_ctx):
        ctx = workspace_ctx
        agent_headers = await ctx.add_member("agent2@example.com", role="agent")
        await client.post(
            f"{ctx.base}/views",
            json={
                "name": "Team queue",
                "visibility": "shared",
                "query": {"match": "all", "conditions": []},
            },
            headers=ctx.owner_headers,
        )
        visible = await client.get(f"{ctx.base}/views", headers=agent_headers)
        assert [v["name"] for v in visible.json()] == ["Team queue"]

    async def test_viewer_cannot_publish_a_shared_view(self, client, workspace_ctx):
        """A viewer may bookmark their own queue but must not rewrite the team's."""
        ctx = workspace_ctx
        viewer_headers = await ctx.add_member("viewer@example.com", role="viewer")
        personal = await client.post(
            f"{ctx.base}/views",
            json={"name": "Mine", "query": {"match": "all", "conditions": []}},
            headers=viewer_headers,
        )
        assert personal.status_code == 201, personal.text
        shared = await client.post(
            f"{ctx.base}/views",
            json={
                "name": "Everyone",
                "visibility": "shared",
                "query": {"match": "all", "conditions": []},
            },
            headers=viewer_headers,
        )
        assert shared.status_code == 403

    async def test_catalog_includes_conversation_attribute_definitions(self, client, workspace_ctx):
        ctx = workspace_ctx
        await client.post(
            f"{ctx.base}/custom-attributes",
            json={
                "attribute_model": "conversation",
                "key": "refund_reason",
                "display_name": "Refund reason",
                "attribute_type": "list",
                "options": ["damaged", "late"],
            },
            headers=ctx.owner_headers,
        )
        response = await client.get(f"{ctx.base}/views/catalog", headers=ctx.owner_headers)
        assert response.status_code == 200, response.text
        fields = {f["field"]: f for f in response.json()["fields"]}
        assert "status" in fields
        custom = fields["attributes.refund_reason"]
        assert custom["label"] == "Refund reason"
        assert custom["value_type"] == "list"
        assert [o["value"] for o in custom["options"]] == ["damaged", "late"]
