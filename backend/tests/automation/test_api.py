"""Automations API tests: CRUD, toggle, reorder, authz, and live rule firing."""

from __future__ import annotations

from app.core.events import EventNames
from tests.automation.conftest import create_contact_via_db, create_inbox_via_api


def _rule_body(**overrides) -> dict:
    body = {
        "name": "Tag VIPs",
        "event": EventNames.CONVERSATION_CREATED,
        "conditions": [{"field": "contact.attributes.plan", "op": "eq", "value": "enterprise"}],
        "actions": [{"type": "set_priority", "params": {"priority": "high"}}],
        "enabled": True,
        "ord": 0,
    }
    body.update(overrides)
    return body


async def test_crud_lifecycle(client, workspace_ctx):
    base = workspace_ctx.base
    headers = workspace_ctx.owner_headers

    created = await client.post(f"{base}/automations", json=_rule_body(), headers=headers)
    assert created.status_code == 201, created.text
    rule = created.json()
    assert rule["event"] == EventNames.CONVERSATION_CREATED
    assert rule["conditions"][0]["field"] == "contact.attributes.plan"
    rule_id = rule["id"]

    listing = await client.get(f"{base}/automations", headers=headers)
    assert listing.status_code == 200
    assert [r["id"] for r in listing.json()] == [rule_id]

    fetched = await client.get(f"{base}/automations/{rule_id}", headers=headers)
    assert fetched.status_code == 200

    patched = await client.patch(
        f"{base}/automations/{rule_id}",
        json={"name": "Renamed", "actions": [{"type": "add_tag", "params": {"tag": "vip"}}]},
        headers=headers,
    )
    assert patched.status_code == 200
    assert patched.json()["name"] == "Renamed"
    assert patched.json()["actions"][0]["type"] == "add_tag"

    deleted = await client.delete(f"{base}/automations/{rule_id}", headers=headers)
    assert deleted.status_code == 200
    assert (await client.get(f"{base}/automations/{rule_id}", headers=headers)).status_code == 404


async def test_toggle(client, workspace_ctx):
    base, headers = workspace_ctx.base, workspace_ctx.owner_headers
    rule = (await client.post(f"{base}/automations", json=_rule_body(), headers=headers)).json()
    assert rule["enabled"] is True

    toggled = await client.post(f"{base}/automations/{rule['id']}/toggle", headers=headers)
    assert toggled.status_code == 200
    assert toggled.json()["enabled"] is False

    again = await client.post(f"{base}/automations/{rule['id']}/toggle", headers=headers)
    assert again.json()["enabled"] is True


async def test_reorder(client, workspace_ctx):
    base, headers = workspace_ctx.base, workspace_ctx.owner_headers
    a = (
        await client.post(f"{base}/automations", json=_rule_body(name="A"), headers=headers)
    ).json()
    b = (
        await client.post(f"{base}/automations", json=_rule_body(name="B"), headers=headers)
    ).json()
    c = (
        await client.post(f"{base}/automations", json=_rule_body(name="C"), headers=headers)
    ).json()

    reordered = await client.post(
        f"{base}/automations/reorder",
        json={"ordered_ids": [c["id"], a["id"], b["id"]]},
        headers=headers,
    )
    assert reordered.status_code == 200
    assert [r["name"] for r in reordered.json()] == ["C", "A", "B"]
    assert [r["ord"] for r in reordered.json()] == [0, 1, 2]


async def test_reorder_rejects_unknown_id(client, workspace_ctx):
    base, headers = workspace_ctx.base, workspace_ctx.owner_headers
    rule = (await client.post(f"{base}/automations", json=_rule_body(), headers=headers)).json()
    resp = await client.post(
        f"{base}/automations/reorder",
        json={"ordered_ids": [rule["id"], "not-a-real-id"]},
        headers=headers,
    )
    assert resp.status_code == 422


async def test_validation_rejects_bad_event_and_op(client, workspace_ctx):
    base, headers = workspace_ctx.base, workspace_ctx.owner_headers
    bad_event = await client.post(
        f"{base}/automations", json=_rule_body(event="not.an.event"), headers=headers
    )
    assert bad_event.status_code == 422

    bad_op = await client.post(
        f"{base}/automations",
        json=_rule_body(conditions=[{"field": "status", "op": "wat", "value": "open"}]),
        headers=headers,
    )
    assert bad_op.status_code == 422

    no_actions = await client.post(
        f"{base}/automations", json=_rule_body(actions=[]), headers=headers
    )
    assert no_actions.status_code == 422


async def test_authz_viewer_cannot_manage(client, workspace_ctx):
    base = workspace_ctx.base
    viewer = await workspace_ctx.add_member("viewer@example.com", role="viewer")

    # viewer may read
    assert (await client.get(f"{base}/automations", headers=viewer)).status_code == 200
    # but not create
    denied = await client.post(f"{base}/automations", json=_rule_body(), headers=viewer)
    assert denied.status_code == 403


async def test_authz_cross_workspace(client, workspace_ctx):
    from tests.conftest import bearer, signup

    other = await signup(client, "intruder@example.com")
    other_ws = await client.post(
        "/api/v1/workspaces", json={"name": "Other"}, headers=bearer(other)
    )
    resp = await client.get(f"{workspace_ctx.base}/automations", headers=bearer(other))
    assert resp.status_code == 403
    assert other_ws.status_code == 201  # the intruder has their own workspace, just not this one


async def test_rule_fires_end_to_end_on_conversation_created(client, workspace_ctx):
    """Proves the engine's @on(...) handlers register via the shipped router import."""
    base, headers = workspace_ctx.base, workspace_ctx.owner_headers
    inbox = await create_inbox_via_api(client, workspace_ctx, channel_type="widget")
    contact_id = await create_contact_via_db(
        workspace_ctx.id, name="Enterprise Ed", attributes={"plan": "enterprise"}
    )
    await client.post(
        f"{base}/automations",
        json=_rule_body(
            actions=[
                {"type": "add_tag", "params": {"tag": "vip"}},
                {"type": "set_priority", "params": {"priority": "high"}},
            ]
        ),
        headers=headers,
    )

    created = await client.post(
        f"{base}/conversations",
        json={"contact_id": contact_id, "inbox_id": inbox["id"], "content": "Hi there"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    conversation = created.json()
    assert conversation["priority"] == "high"
    assert len(conversation["tag_ids"]) == 1
