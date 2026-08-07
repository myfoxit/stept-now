"""Typed custom attribute definitions: CRUD, coercion, and the pass-through rule.

The design decision under test: definitions are additive metadata, not a schema
lock. Keys with no definition keep working exactly as before, so existing
integrations writing free-form attributes are unaffected.
"""

from __future__ import annotations

import pytest

from app.core.errors import ValidationFailure
from app.models.custom_attribute import AttributeType, CustomAttributeDefinition
from app.services import custom_attributes as attrs


def _definition(**kwargs) -> CustomAttributeDefinition:
    defaults = {
        "workspace_id": "ws",
        "attribute_model": "contact",
        "key": "plan",
        "display_name": "Plan",
        "attribute_type": AttributeType.TEXT.value,
        "options": [],
    }
    return CustomAttributeDefinition(**{**defaults, **kwargs})


class TestCoerce:
    def test_null_always_clears(self):
        assert attrs.coerce(_definition(attribute_type="number"), None) is None

    def test_number_accepts_numeric_strings(self):
        assert attrs.coerce(_definition(attribute_type="number"), "42") == 42
        assert attrs.coerce(_definition(attribute_type="number"), "4.5") == 4.5

    def test_number_rejects_text(self):
        with pytest.raises(ValidationFailure, match="must be a number"):
            attrs.coerce(_definition(attribute_type="number"), "many")

    def test_percent_is_bounded(self):
        assert attrs.coerce(_definition(attribute_type="percent"), 50) == 50
        with pytest.raises(ValidationFailure, match="between 0 and 100"):
            attrs.coerce(_definition(attribute_type="percent"), 101)

    def test_checkbox_accepts_common_truthy_strings(self):
        definition = _definition(attribute_type="checkbox")
        assert attrs.coerce(definition, "yes") is True
        assert attrs.coerce(definition, "0") is False
        with pytest.raises(ValidationFailure, match="true or false"):
            attrs.coerce(definition, "maybe")

    def test_date_normalises_to_iso(self):
        definition = _definition(attribute_type="date")
        assert attrs.coerce(definition, "2026-08-07T13:00:00") == "2026-08-07"
        with pytest.raises(ValidationFailure, match="ISO date"):
            attrs.coerce(definition, "last tuesday")

    def test_list_enforces_options(self):
        definition = _definition(attribute_type="list", options=["free", "pro"])
        assert attrs.coerce(definition, "pro") == "pro"
        with pytest.raises(ValidationFailure, match="must be one of"):
            attrs.coerce(definition, "enterprise")

    def test_link_requires_http(self):
        with pytest.raises(ValidationFailure, match="http"):
            attrs.coerce(_definition(attribute_type="link"), "ftp://x")

    def test_regex_uses_the_admins_own_cue(self):
        definition = _definition(regex_pattern=r"^ACME-\d+$", regex_cue="Use ACME-123")
        assert attrs.coerce(definition, "ACME-9") == "ACME-9"
        with pytest.raises(ValidationFailure, match="Use ACME-123"):
            attrs.coerce(definition, "nope")

    def test_broken_admin_regex_does_not_block_writes(self):
        """A malformed pattern is an admin mistake; it must not wedge the API."""
        definition = _definition(regex_pattern="[unclosed")
        assert attrs.coerce(definition, "anything") == "anything"


class TestApi:
    async def test_create_and_list(self, client, workspace_ctx):
        ctx = workspace_ctx
        created = await client.post(
            f"{ctx.base}/custom-attributes",
            json={
                "attribute_model": "contact",
                "key": "plan",
                "display_name": "Plan",
                "attribute_type": "list",
                "options": ["free", "pro"],
            },
            headers=ctx.owner_headers,
        )
        assert created.status_code == 201, created.text
        listed = await client.get(
            f"{ctx.base}/custom-attributes?attribute_model=contact", headers=ctx.owner_headers
        )
        assert [d["key"] for d in listed.json()] == ["plan"]

    async def test_key_is_normalised_and_validated(self, client, workspace_ctx):
        ctx = workspace_ctx
        ok = await client.post(
            f"{ctx.base}/custom-attributes",
            json={
                "attribute_model": "contact",
                "key": "  MRR  ",
                "display_name": "MRR",
                "attribute_type": "currency",
            },
            headers=ctx.owner_headers,
        )
        assert ok.json()["key"] == "mrr"
        bad = await client.post(
            f"{ctx.base}/custom-attributes",
            json={"attribute_model": "contact", "key": "has spaces", "display_name": "X"},
            headers=ctx.owner_headers,
        )
        assert bad.status_code == 422

    async def test_duplicate_key_per_model_conflicts(self, client, workspace_ctx):
        ctx = workspace_ctx
        body = {"attribute_model": "contact", "key": "plan", "display_name": "Plan"}
        assert (
            await client.post(f"{ctx.base}/custom-attributes", json=body, headers=ctx.owner_headers)
        ).status_code == 201
        clash = await client.post(
            f"{ctx.base}/custom-attributes", json=body, headers=ctx.owner_headers
        )
        assert clash.status_code == 409

        # The same key on the other model is fine — they're separate namespaces.
        other = await client.post(
            f"{ctx.base}/custom-attributes",
            json={**body, "attribute_model": "conversation"},
            headers=ctx.owner_headers,
        )
        assert other.status_code == 201

    async def test_list_type_needs_options(self, client, workspace_ctx):
        ctx = workspace_ctx
        response = await client.post(
            f"{ctx.base}/custom-attributes",
            json={
                "attribute_model": "contact",
                "key": "tier",
                "display_name": "Tier",
                "attribute_type": "list",
                "options": [],
            },
            headers=ctx.owner_headers,
        )
        assert response.status_code == 422

    async def test_values_are_coerced_on_contact_write(self, client, workspace_ctx):
        ctx = workspace_ctx
        await client.post(
            f"{ctx.base}/custom-attributes",
            json={
                "attribute_model": "contact",
                "key": "mrr",
                "display_name": "MRR",
                "attribute_type": "number",
            },
            headers=ctx.owner_headers,
        )
        created = await client.post(
            f"{ctx.base}/contacts",
            json={"name": "Nina", "email": "nina@example.com", "attributes": {"mrr": "250"}},
            headers=ctx.owner_headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["attributes"]["mrr"] == 250

    async def test_bad_value_is_rejected_on_contact_write(self, client, workspace_ctx):
        ctx = workspace_ctx
        await client.post(
            f"{ctx.base}/custom-attributes",
            json={
                "attribute_model": "contact",
                "key": "mrr",
                "display_name": "MRR",
                "attribute_type": "number",
            },
            headers=ctx.owner_headers,
        )
        response = await client.post(
            f"{ctx.base}/contacts",
            json={"name": "Nina", "email": "n@example.com", "attributes": {"mrr": "lots"}},
            headers=ctx.owner_headers,
        )
        assert response.status_code == 422

    async def test_undefined_keys_pass_through_untouched(self, client, workspace_ctx):
        """Definitions are additive metadata, not a schema lock."""
        ctx = workspace_ctx
        created = await client.post(
            f"{ctx.base}/contacts",
            json={
                "name": "Nina",
                "email": "n2@example.com",
                "attributes": {"anything": {"nested": True}},
            },
            headers=ctx.owner_headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["attributes"]["anything"] == {"nested": True}

    async def test_deleting_a_definition_keeps_stored_values(self, client, workspace_ctx):
        ctx = workspace_ctx
        definition = await client.post(
            f"{ctx.base}/custom-attributes",
            json={
                "attribute_model": "contact",
                "key": "mrr",
                "display_name": "MRR",
                "attribute_type": "number",
            },
            headers=ctx.owner_headers,
        )
        contact = await client.post(
            f"{ctx.base}/contacts",
            json={"name": "Nina", "email": "n3@example.com", "attributes": {"mrr": 10}},
            headers=ctx.owner_headers,
        )
        await client.delete(
            f"{ctx.base}/custom-attributes/{definition.json()['id']}", headers=ctx.owner_headers
        )
        after = await client.get(
            f"{ctx.base}/contacts/{contact.json()['id']}", headers=ctx.owner_headers
        )
        assert after.json()["attributes"]["mrr"] == 10

    async def test_agent_can_read_but_not_manage(self, client, workspace_ctx):
        ctx = workspace_ctx
        agent_headers = await ctx.add_member("attr-agent@example.com", role="agent")
        assert (
            await client.get(f"{ctx.base}/custom-attributes", headers=agent_headers)
        ).status_code == 200
        blocked = await client.post(
            f"{ctx.base}/custom-attributes",
            json={"attribute_model": "contact", "key": "plan", "display_name": "Plan"},
            headers=agent_headers,
        )
        assert blocked.status_code == 403

    async def test_definitions_do_not_leak_across_workspaces(self, client, workspace_ctx):
        ctx = workspace_ctx
        await client.post(
            f"{ctx.base}/custom-attributes",
            json={"attribute_model": "contact", "key": "plan", "display_name": "Plan"},
            headers=ctx.owner_headers,
        )
        other = await client.post(
            "/api/v1/workspaces", json={"name": "Other"}, headers=ctx.owner_headers
        )
        listed = await client.get(
            f"/api/v1/w/{other.json()['id']}/custom-attributes", headers=ctx.owner_headers
        )
        assert listed.json() == []
