"""Contact merge, blocking, and CSV import/export — the migration-in door.

Merge keeps the loser row as a tombstone so old links and channel source_ids
still resolve; import matches existing contacts the same way `find_or_create`
does, so re-running an export updates instead of duplicating.
"""

from __future__ import annotations

import io

import pytest
from sqlalchemy import select

from app.core.errors import ValidationFailure
from app.models.contact import Contact
from app.models.conversation import Conversation
from app.services import contact_import
from tests.conftest import drain_tasks
from tests.slas.conftest import create_inbox_via_api


async def _contact(client, ctx, **body) -> dict:
    response = await client.post(f"{ctx.base}/contacts", json=body, headers=ctx.owner_headers)
    assert response.status_code == 201, response.text
    return response.json()


class TestMerge:
    async def test_reparents_conversations_and_fills_blanks(self, client, workspace_ctx, session):
        ctx = workspace_ctx
        inbox = await create_inbox_via_api(client, ctx)
        winner = await _contact(client, ctx, name="Nina Doe", email="nina@example.com")
        loser = await _contact(client, ctx, name="Nina", phone="+15550001")

        conversation = await client.post(
            f"{ctx.base}/conversations",
            json={"contact_id": loser["id"], "inbox_id": inbox["id"], "content": "hello"},
            headers=ctx.owner_headers,
        )
        assert conversation.status_code == 201

        merged = await client.post(
            f"{ctx.base}/contacts/{winner['id']}/merge",
            json={"loser_id": loser["id"]},
            headers=ctx.owner_headers,
        )
        assert merged.status_code == 200, merged.text
        body = merged.json()
        assert body["name"] == "Nina Doe"  # winner keeps its own scalars
        assert body["phone"] == "+15550001"  # and inherits what it lacked

        session.expire_all()
        moved = (
            await session.execute(
                select(Conversation).where(Conversation.id == conversation.json()["id"])
            )
        ).scalar_one()
        assert moved.contact_id == winner["id"]

    async def test_loser_survives_as_a_tombstone(self, client, workspace_ctx, session):
        ctx = workspace_ctx
        winner = await _contact(client, ctx, name="A", email="a@example.com")
        loser = await _contact(client, ctx, name="B", email="b@example.com")
        await client.post(
            f"{ctx.base}/contacts/{winner['id']}/merge",
            json={"loser_id": loser["id"]},
            headers=ctx.owner_headers,
        )
        session.expire_all()
        row = (await session.execute(select(Contact).where(Contact.id == loser["id"]))).scalar_one()
        assert row.merged_into_id == winner["id"]

    async def test_attributes_deep_merge_with_winner_precedence(self, client, workspace_ctx):
        ctx = workspace_ctx
        winner = await _contact(
            client, ctx, name="A", email="a2@example.com", attributes={"plan": "pro"}
        )
        loser = await _contact(
            client,
            ctx,
            name="B",
            email="b2@example.com",
            attributes={"plan": "free", "source": "import"},
        )
        merged = await client.post(
            f"{ctx.base}/contacts/{winner['id']}/merge",
            json={"loser_id": loser["id"]},
            headers=ctx.owner_headers,
        )
        assert merged.json()["attributes"] == {"plan": "pro", "source": "import"}

    async def test_tags_are_unioned(self, client, workspace_ctx):
        ctx = workspace_ctx
        tag = (
            await client.post(f"{ctx.base}/tags", json={"name": "vip"}, headers=ctx.owner_headers)
        ).json()
        winner = await _contact(client, ctx, name="A", email="a3@example.com")
        loser = await _contact(client, ctx, name="B", email="b3@example.com")
        await client.post(
            f"{ctx.base}/contacts/{loser['id']}/tags",
            json={"tag_id": tag["id"]},
            headers=ctx.owner_headers,
        )
        merged = await client.post(
            f"{ctx.base}/contacts/{winner['id']}/merge",
            json={"loser_id": loser["id"]},
            headers=ctx.owner_headers,
        )
        assert [t["id"] for t in merged.json()["tags"]] == [tag["id"]]

    async def test_cannot_merge_into_itself(self, client, workspace_ctx):
        ctx = workspace_ctx
        contact = await _contact(client, ctx, name="A", email="self@example.com")
        response = await client.post(
            f"{ctx.base}/contacts/{contact['id']}/merge",
            json={"loser_id": contact["id"]},
            headers=ctx.owner_headers,
        )
        assert response.status_code == 422

    async def test_cannot_merge_an_already_merged_contact(self, client, workspace_ctx):
        ctx = workspace_ctx
        a = await _contact(client, ctx, name="A", email="m1@example.com")
        b = await _contact(client, ctx, name="B", email="m2@example.com")
        c = await _contact(client, ctx, name="C", email="m3@example.com")
        await client.post(
            f"{ctx.base}/contacts/{a['id']}/merge",
            json={"loser_id": b["id"]},
            headers=ctx.owner_headers,
        )
        again = await client.post(
            f"{ctx.base}/contacts/{c['id']}/merge",
            json={"loser_id": b["id"]},
            headers=ctx.owner_headers,
        )
        assert again.status_code == 422

    async def test_cross_workspace_merge_is_not_found(self, client, workspace_ctx):
        ctx = workspace_ctx
        mine = await _contact(client, ctx, name="A", email="x1@example.com")
        other = await client.post(
            "/api/v1/workspaces", json={"name": "Other"}, headers=ctx.owner_headers
        )
        other_base = f"/api/v1/w/{other.json()['id']}"
        theirs = await client.post(
            f"{other_base}/contacts",
            json={"name": "B", "email": "x2@example.com"},
            headers=ctx.owner_headers,
        )
        response = await client.post(
            f"{ctx.base}/contacts/{mine['id']}/merge",
            json={"loser_id": theirs.json()["id"]},
            headers=ctx.owner_headers,
        )
        assert response.status_code == 404

    async def test_viewer_cannot_merge(self, client, workspace_ctx):
        ctx = workspace_ctx
        viewer_headers = await ctx.add_member("merge-viewer@example.com", role="viewer")
        a = await _contact(client, ctx, name="A", email="v1@example.com")
        b = await _contact(client, ctx, name="B", email="v2@example.com")
        response = await client.post(
            f"{ctx.base}/contacts/{a['id']}/merge",
            json={"loser_id": b["id"]},
            headers=viewer_headers,
        )
        assert response.status_code == 403


class TestBlock:
    async def test_block_and_unblock(self, client, workspace_ctx):
        ctx = workspace_ctx
        contact = await _contact(client, ctx, name="Spammer", email="spam@example.com")
        blocked = await client.post(
            f"{ctx.base}/contacts/{contact['id']}/block",
            json={"blocked": True},
            headers=ctx.owner_headers,
        )
        assert blocked.status_code == 200, blocked.text
        assert blocked.json()["blocked"] is True

        unblocked = await client.post(
            f"{ctx.base}/contacts/{contact['id']}/block",
            json={"blocked": False},
            headers=ctx.owner_headers,
        )
        assert unblocked.json()["blocked"] is False


class TestCsvParsing:
    def test_detects_headers_and_rows(self):
        headers, rows = contact_import.parse_csv(b"Email,Name\na@x.com,Ada\n")
        assert headers == ["Email", "Name"]
        assert rows == [{"Email": "a@x.com", "Name": "Ada"}]

    def test_handles_semicolon_delimiters(self):
        headers, _ = contact_import.parse_csv(b"Email;Name;Phone\na@x.com;Ada;+1\n")
        assert headers == ["Email", "Name", "Phone"]

    def test_strips_a_utf8_bom(self):
        headers, _ = contact_import.parse_csv("﻿Email,Name\na@x.com,Ada\n".encode())
        assert headers[0] == "Email"

    def test_rejects_a_headerless_file(self):
        with pytest.raises(ValidationFailure):
            contact_import.parse_csv(b"")

    def test_suggests_a_mapping_for_common_export_headers(self):
        mapping = contact_import.suggest_mapping(["Email", "Full Name", "User ID", "Whatever"])
        assert mapping["Email"] == "email"
        assert mapping["Full Name"] == "name"
        assert mapping["User ID"] == "external_id"
        assert mapping["Whatever"] == ""

    def test_mapping_needs_an_identifying_column(self):
        with pytest.raises(ValidationFailure, match="identifying column"):
            contact_import.validate_mapping(["Name"], {"Name": "name"})

    def test_mapping_rejects_duplicate_targets(self):
        with pytest.raises(ValidationFailure, match="Two columns"):
            contact_import.validate_mapping(["A", "B"], {"A": "email", "B": "email"})

    def test_mapping_rejects_unknown_targets(self):
        with pytest.raises(ValidationFailure, match="Invalid mapping target"):
            contact_import.validate_mapping(["A"], {"A": "nonsense"})


class TestImportFlow:
    async def _upload(self, client, ctx, csv_text: str) -> dict:
        response = await client.post(
            f"{ctx.base}/contacts/imports",
            files={"file": ("contacts.csv", io.BytesIO(csv_text.encode()), "text/csv")},
            headers=ctx.owner_headers,
        )
        assert response.status_code == 201, response.text
        return response.json()

    async def test_upload_preview_then_run(self, client, workspace_ctx):
        ctx = workspace_ctx
        preview = await self._upload(
            client,
            ctx,
            "Email,Full Name\nada@example.com,Ada\ngrace@example.com,Grace\n",
        )
        assert preview["headers"] == ["Email", "Full Name"]
        assert len(preview["sample_rows"]) == 2
        assert preview["contact_import"]["status"] == "pending"
        assert preview["contact_import"]["mapping"]["Email"] == "email"

        started = await client.post(
            f"{ctx.base}/contacts/imports/{preview['contact_import']['id']}/start",
            json={},
            headers=ctx.owner_headers,
        )
        assert started.status_code == 200, started.text
        await drain_tasks()

        done = await client.get(
            f"{ctx.base}/contacts/imports/{preview['contact_import']['id']}",
            headers=ctx.owner_headers,
        )
        body = done.json()
        assert body["status"] == "completed"
        assert body["created_count"] == 2
        assert body["failed_count"] == 0

        listed = await client.get(f"{ctx.base}/contacts", headers=ctx.owner_headers)
        assert {c["email"] for c in listed.json()["items"]} == {
            "ada@example.com",
            "grace@example.com",
        }

    async def test_rerunning_updates_instead_of_duplicating(self, client, workspace_ctx):
        ctx = workspace_ctx
        await _contact(client, ctx, name="Old name", email="ada@example.com")
        preview = await self._upload(client, ctx, "Email,Full Name\nada@example.com,Ada Lovelace\n")
        await client.post(
            f"{ctx.base}/contacts/imports/{preview['contact_import']['id']}/start",
            json={},
            headers=ctx.owner_headers,
        )
        await drain_tasks()

        done = await client.get(
            f"{ctx.base}/contacts/imports/{preview['contact_import']['id']}",
            headers=ctx.owner_headers,
        )
        assert done.json()["updated_count"] == 1
        assert done.json()["created_count"] == 0

        listed = await client.get(f"{ctx.base}/contacts", headers=ctx.owner_headers)
        items = listed.json()["items"]
        assert len(items) == 1
        assert items[0]["name"] == "Ada Lovelace"

    async def test_bad_rows_are_collected_not_fatal(self, client, workspace_ctx):
        """One malformed line in a 40k-row export must not lose the rest."""
        ctx = workspace_ctx
        preview = await self._upload(
            client, ctx, "Email,Full Name\nada@example.com,Ada\n,No identity\n"
        )
        await client.post(
            f"{ctx.base}/contacts/imports/{preview['contact_import']['id']}/start",
            json={},
            headers=ctx.owner_headers,
        )
        await drain_tasks()
        body = (
            await client.get(
                f"{ctx.base}/contacts/imports/{preview['contact_import']['id']}",
                headers=ctx.owner_headers,
            )
        ).json()
        assert body["created_count"] == 1
        assert body["failed_count"] == 1
        assert body["errors"][0]["row"] == 3  # header is row 1

    async def test_attributes_mapping(self, client, workspace_ctx):
        ctx = workspace_ctx
        preview = await self._upload(client, ctx, "Email,Plan\nada@example.com,pro\n")
        await client.post(
            f"{ctx.base}/contacts/imports/{preview['contact_import']['id']}/start",
            json={"mapping": {"Email": "email", "Plan": "attributes.plan"}},
            headers=ctx.owner_headers,
        )
        await drain_tasks()
        listed = await client.get(f"{ctx.base}/contacts", headers=ctx.owner_headers)
        assert listed.json()["items"][0]["attributes"] == {"plan": "pro"}

    async def test_empty_upload_is_rejected(self, client, workspace_ctx):
        ctx = workspace_ctx
        response = await client.post(
            f"{ctx.base}/contacts/imports",
            files={"file": ("contacts.csv", io.BytesIO(b""), "text/csv")},
            headers=ctx.owner_headers,
        )
        assert response.status_code == 422

    async def test_viewer_cannot_upload(self, client, workspace_ctx):
        ctx = workspace_ctx
        viewer_headers = await ctx.add_member("import-viewer@example.com", role="viewer")
        response = await client.post(
            f"{ctx.base}/contacts/imports",
            files={"file": ("c.csv", io.BytesIO(b"Email\na@x.com\n"), "text/csv")},
            headers=viewer_headers,
        )
        assert response.status_code == 403


class TestExport:
    async def test_round_trips_through_import(self, client, workspace_ctx):
        ctx = workspace_ctx
        await _contact(client, ctx, name="Ada", email="ada@example.com", attributes={"plan": "pro"})
        response = await client.get(f"{ctx.base}/contacts/export", headers=ctx.owner_headers)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/csv")
        headers, rows = contact_import.parse_csv(response.content)
        assert "email" in headers and "attributes.plan" in headers
        assert rows[0]["email"] == "ada@example.com"
        assert rows[0]["attributes.plan"] == "pro"

    async def test_merged_tombstones_are_excluded(self, client, workspace_ctx):
        ctx = workspace_ctx
        a = await _contact(client, ctx, name="A", email="e1@example.com")
        b = await _contact(client, ctx, name="B", email="e2@example.com")
        await client.post(
            f"{ctx.base}/contacts/{a['id']}/merge",
            json={"loser_id": b["id"]},
            headers=ctx.owner_headers,
        )
        response = await client.get(f"{ctx.base}/contacts/export", headers=ctx.owner_headers)
        _, rows = contact_import.parse_csv(response.content)
        assert [r["id"] for r in rows] == [a["id"]]


class TestBlockedIngress:
    """A block is only real if it holds at the door, not just in the UI."""

    async def test_blocked_contact_cannot_boot_the_widget(self, client, workspace_ctx):
        ctx = workspace_ctx
        inbox = await create_inbox_via_api(client, ctx)
        widget_key = (
            await client.get(f"{ctx.base}/inboxes/{inbox['id']}", headers=ctx.owner_headers)
        ).json()["widget_key"]

        first = await client.post(
            "/api/widget/boot", json={"widget_key": widget_key, "visitor_id": "v-1"}
        )
        assert first.status_code == 200, first.text
        contact_id = first.json()["contact"]["id"]

        await client.post(
            f"{ctx.base}/contacts/{contact_id}/block",
            json={"blocked": True},
            headers=ctx.owner_headers,
        )
        again = await client.post(
            "/api/widget/boot", json={"widget_key": widget_key, "visitor_id": "v-1"}
        )
        assert again.status_code == 403
        assert again.json()["error"]["code"] == "contact_blocked"

    async def test_blocked_contact_inbound_email_is_dropped(self, client, workspace_ctx, session):
        ctx = workspace_ctx
        created = await client.post(
            f"{ctx.base}/inboxes",
            json={
                "name": "Email",
                "channel_type": "email",
                "config": {"address": "support@acme.test"},
            },
            headers=ctx.owner_headers,
        )
        assert created.status_code == 201, created.text
        contact = await _contact(client, ctx, name="Spam", email="spam@example.com")
        await client.post(
            f"{ctx.base}/contacts/{contact['id']}/block",
            json={"blocked": True},
            headers=ctx.owner_headers,
        )

        response = await client.post(
            "/api/channels/email/inbound",
            json={
                "to": "support@acme.test",
                "from": "Spam <spam@example.com>",
                "subject": "buy now",
                "text": "hello",
            },
        )
        assert response.status_code == 403
        session.expire_all()
        conversations = list(
            (
                await session.execute(
                    select(Conversation).where(Conversation.workspace_id == ctx.id)
                )
            ).scalars()
        )
        assert conversations == []

    async def test_unblocking_restores_access(self, client, workspace_ctx):
        ctx = workspace_ctx
        inbox = await create_inbox_via_api(client, ctx)
        widget_key = (
            await client.get(f"{ctx.base}/inboxes/{inbox['id']}", headers=ctx.owner_headers)
        ).json()["widget_key"]
        boot = await client.post(
            "/api/widget/boot", json={"widget_key": widget_key, "visitor_id": "v-2"}
        )
        contact_id = boot.json()["contact"]["id"]
        for blocked, expected in ((True, 403), (False, 200)):
            await client.post(
                f"{ctx.base}/contacts/{contact_id}/block",
                json={"blocked": blocked},
                headers=ctx.owner_headers,
            )
            response = await client.post(
                "/api/widget/boot", json={"widget_key": widget_key, "visitor_id": "v-2"}
            )
            assert response.status_code == expected
