"""Report breakdowns, SLA attainment and CSV export.

The property that makes drill-down work: every breakdown row carries a filter
document, and running that document through `POST /conversations/search` must
return exactly the conversations the row counted.
"""

from __future__ import annotations

from tests.slas.conftest import create_contact_via_db, create_inbox_via_api


async def _seed(client, ctx, count: int = 3) -> tuple[dict, list[dict]]:
    inbox = await create_inbox_via_api(client, ctx)
    conversations = []
    for index in range(count):
        contact_id = await create_contact_via_db(ctx.id, name=f"C{index}")
        response = await client.post(
            f"{ctx.base}/conversations",
            json={"contact_id": contact_id, "inbox_id": inbox["id"], "content": "Hi"},
            headers=ctx.owner_headers,
        )
        conversations.append(response.json())
    return inbox, conversations


class TestBreakdown:
    async def test_by_agent_counts_and_labels(self, client, workspace_ctx):
        ctx = workspace_ctx
        _, conversations = await _seed(client, ctx, 3)
        agent_headers = await ctx.add_member("rep-agent@example.com", role="agent")
        me = (await client.get("/api/v1/me", headers=agent_headers)).json()["user"]
        await client.patch(
            f"{ctx.base}/conversations/{conversations[0]['id']}",
            json={"assignee_user_id": me["id"]},
            headers=ctx.owner_headers,
        )

        response = await client.get(
            f"{ctx.base}/reports/breakdown?dimension=agent&days=7", headers=ctx.owner_headers
        )
        assert response.status_code == 200, response.text
        rows = {r["label"]: r for r in response.json()["rows"]}
        assert rows["Unassigned"]["new"] == 2
        assert rows["Test User"]["new"] == 1

    async def test_by_inbox_and_channel(self, client, workspace_ctx):
        ctx = workspace_ctx
        inbox, conversations = await _seed(client, ctx, 2)
        by_inbox = await client.get(
            f"{ctx.base}/reports/breakdown?dimension=inbox", headers=ctx.owner_headers
        )
        assert [r["key"] for r in by_inbox.json()["rows"]] == [inbox["id"]]

        by_channel = await client.get(
            f"{ctx.base}/reports/breakdown?dimension=channel", headers=ctx.owner_headers
        )
        assert by_channel.json()["rows"][0]["key"] == "widget"

    async def test_tag_dimension_counts_a_conversation_once_per_tag(self, client, workspace_ctx):
        ctx = workspace_ctx
        _, conversations = await _seed(client, ctx, 2)
        for name in ("billing", "urgent"):
            tag = await client.post(
                f"{ctx.base}/tags", json={"name": name}, headers=ctx.owner_headers
            )
            await client.post(
                f"{ctx.base}/conversations/{conversations[0]['id']}/tags",
                json={"tag_id": tag.json()["id"]},
                headers=ctx.owner_headers,
            )
        response = await client.get(
            f"{ctx.base}/reports/breakdown?dimension=tag", headers=ctx.owner_headers
        )
        rows = {r["label"]: r["new"] for r in response.json()["rows"]}
        assert rows["billing"] == 1
        assert rows["urgent"] == 1
        assert rows["Untagged"] == 1

    async def test_row_filter_reproduces_the_row(self, client, workspace_ctx):
        """This is the drill-down contract — the number and the list must agree."""
        ctx = workspace_ctx
        _, conversations = await _seed(client, ctx, 3)
        agent_headers = await ctx.add_member("drill@example.com", role="agent")
        me = (await client.get("/api/v1/me", headers=agent_headers)).json()["user"]
        await client.patch(
            f"{ctx.base}/conversations/{conversations[0]['id']}",
            json={"assignee_user_id": me["id"]},
            headers=ctx.owner_headers,
        )
        breakdown = await client.get(
            f"{ctx.base}/reports/breakdown?dimension=agent", headers=ctx.owner_headers
        )
        for row in breakdown.json()["rows"]:
            drilled = await client.post(
                f"{ctx.base}/conversations/search?limit=100",
                json=row["filter"],
                headers=ctx.owner_headers,
            )
            assert drilled.status_code == 200, drilled.text
            assert len(drilled.json()["items"]) == row["new"], row["label"]

    async def test_unknown_dimension_is_rejected(self, client, workspace_ctx):
        ctx = workspace_ctx
        response = await client.get(
            f"{ctx.base}/reports/breakdown?dimension=phase-of-moon", headers=ctx.owner_headers
        )
        assert response.status_code == 422

    async def test_days_is_bounded(self, client, workspace_ctx):
        ctx = workspace_ctx
        response = await client.get(
            f"{ctx.base}/reports/breakdown?days=9999", headers=ctx.owner_headers
        )
        assert response.status_code == 422

    async def test_viewer_can_read_reports(self, client, workspace_ctx):
        ctx = workspace_ctx
        viewer_headers = await ctx.add_member("rep-viewer@example.com", role="viewer")
        response = await client.get(f"{ctx.base}/reports/breakdown", headers=viewer_headers)
        assert response.status_code == 200

    async def test_breakdown_does_not_leak_across_workspaces(self, client, workspace_ctx):
        ctx = workspace_ctx
        await _seed(client, ctx, 2)
        other = await client.post(
            "/api/v1/workspaces", json={"name": "Other"}, headers=ctx.owner_headers
        )
        response = await client.get(
            f"/api/v1/w/{other.json()['id']}/reports/breakdown", headers=ctx.owner_headers
        )
        assert response.json()["rows"] == []


class TestSlaReport:
    async def test_attainment_after_a_breach_and_a_resolve(self, client, workspace_ctx, session):
        ctx = workspace_ctx
        inbox, conversations = await _seed(client, ctx, 2)
        policy = await client.post(
            f"{ctx.base}/slas",
            json={"name": "Gold", "first_response_minutes": 60},
            headers=ctx.owner_headers,
        )
        policy_id = policy.json()["id"]
        for conversation in conversations:
            applied = await client.put(
                f"{ctx.base}/conversations/{conversation['id']}/sla",
                json={"sla_policy_id": policy_id},
                headers=ctx.owner_headers,
            )
            assert applied.status_code == 200, applied.text

        await client.patch(
            f"{ctx.base}/conversations/{conversations[0]['id']}",
            json={"status": "resolved"},
            headers=ctx.owner_headers,
        )

        report = await client.get(f"{ctx.base}/reports/sla?days=30", headers=ctx.owner_headers)
        assert report.status_code == 200, report.text
        body = report.json()
        assert body["totals"]["applied"] == 2
        assert body["totals"]["hit"] == 1
        assert body["totals"]["active"] == 1
        assert body["totals"]["attainment_rate"] == 1.0
        assert [p["name"] for p in body["by_policy"]] == ["Gold"]

    async def test_empty_workspace_reports_zeroes(self, client, workspace_ctx):
        ctx = workspace_ctx
        body = (await client.get(f"{ctx.base}/reports/sla", headers=ctx.owner_headers)).json()
        assert body["totals"]["applied"] == 0
        assert body["totals"]["attainment_rate"] == 0.0
        assert body["by_policy"] == []


class TestCsv:
    async def test_overview_csv(self, client, workspace_ctx):
        ctx = workspace_ctx
        await _seed(client, ctx, 1)
        response = await client.get(
            f"{ctx.base}/reports/overview.csv?days=7", headers=ctx.owner_headers
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment" in response.headers["content-disposition"]
        assert response.text.splitlines()[0] == "date,new,resolved"

    async def test_breakdown_csv(self, client, workspace_ctx):
        ctx = workspace_ctx
        await _seed(client, ctx, 1)
        response = await client.get(
            f"{ctx.base}/reports/breakdown.csv?dimension=inbox", headers=ctx.owner_headers
        )
        assert response.status_code == 200
        assert response.text.splitlines()[0].startswith("inbox,new,resolved")

    async def test_sla_csv(self, client, workspace_ctx):
        ctx = workspace_ctx
        response = await client.get(f"{ctx.base}/reports/sla.csv", headers=ctx.owner_headers)
        assert response.status_code == 200
        assert response.text.splitlines()[0].startswith("policy,applied,hit,missed")
