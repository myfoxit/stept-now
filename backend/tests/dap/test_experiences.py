"""The `/api/widget/experiences` bootstrap: one call, every experience kind."""

from __future__ import annotations

from app.core.security import create_widget_token
from tests.tours.conftest import (
    create_contact,
    create_tour,
    publish_tour,
    widget_inbox,
    widget_key_for,
)

INBOX_URL = "https://app.example.com/inbox?tab=open"


async def _bootstrap(client, key, url=INBOX_URL, headers=None):
    resp = await client.get(
        f"/api/widget/experiences?widget_key={key}&url={url}", headers=headers or {}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_bootstrap_returns_all_experience_kinds(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    tour = await create_tour(
        client,
        workspace_ctx,
        name="Boot",
        trigger={"type": "url_match", "url_pattern": "*/inbox*"},
    )
    await publish_tour(client, workspace_ctx, tour["id"])

    body = await _bootstrap(client, key)
    assert set(body) == {"tours", "checklists", "surveys"}
    assert [t["id"] for t in body["tours"]] == [tour["id"]]
    # Checklists/surveys are lists whether or not that module has shipped.
    assert isinstance(body["checklists"], list)
    assert isinstance(body["surveys"], list)


async def test_bootstrap_tours_match_the_tours_endpoint(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    banner = await create_tour(
        client,
        workspace_ctx,
        name="Banner",
        kind="banner",
        priority=5,
        frequency={"type": "every_time"},
        trigger={"type": "url_match", "url_pattern": "*"},
        steps=[{"type": "banner", "body": "News"}],
    )
    await publish_tour(client, workspace_ctx, banner["id"])

    body = await _bootstrap(client, key)
    payload = body["tours"][0]
    listed = (await client.get(f"/api/widget/tours?widget_key={key}&url={INBOX_URL}")).json()
    assert payload == listed[0]
    assert payload["kind"] == "banner"
    assert payload["frequency_type"] == "every_time"
    assert payload["settings"]["mode"] == "guided"
    # No targeting internals leak to the browser.
    assert "audience" not in payload
    assert "schedule" not in payload
    assert "priority" not in payload


async def test_bootstrap_honours_audience_targeting(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    ent = await create_contact(client, workspace_ctx, name="Ent", attributes={"plan": "enterprise"})
    tour = await create_tour(
        client,
        workspace_ctx,
        name="Targeted",
        trigger={"type": "url_match", "url_pattern": "*/inbox*"},
        audience={
            "type": "filters",
            "filters": [{"field": "attributes.plan", "op": "eq", "value": "enterprise"}],
        },
    )
    await publish_tour(client, workspace_ctx, tour["id"])

    headers = {"X-Widget-Token": create_widget_token(workspace_ctx.id, ent["id"])}
    assert [t["id"] for t in (await _bootstrap(client, key, headers=headers))["tours"]] == [
        tour["id"]
    ]
    assert (await _bootstrap(client, key))["tours"] == []


async def test_bootstrap_url_scoped(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    tour = await create_tour(
        client,
        workspace_ctx,
        name="Inbox only",
        trigger={"type": "url_match", "url_pattern": "*/inbox*"},
    )
    await publish_tour(client, workspace_ctx, tour["id"])
    assert (await _bootstrap(client, key, url="https://app.example.com/settings"))["tours"] == []


async def test_bootstrap_rejects_unknown_and_disabled_keys(client, workspace_ctx):
    inbox = await widget_inbox(client, workspace_ctx)
    unknown = await client.get(f"/api/widget/experiences?widget_key=wk_nope&url={INBOX_URL}")
    assert unknown.status_code == 404

    missing_params = await client.get("/api/widget/experiences")
    assert missing_params.status_code == 422

    await client.patch(
        f"{workspace_ctx.base}/inboxes/{inbox['id']}",
        json={"enabled": False},
        headers=workspace_ctx.owner_headers,
    )
    disabled = await client.get(
        f"/api/widget/experiences?widget_key={inbox['widget_key']}&url={INBOX_URL}"
    )
    assert disabled.status_code == 404


async def test_bootstrap_is_workspace_isolated(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    other = await client.post(
        "/api/v1/workspaces", json={"name": "Other WS"}, headers=workspace_ctx.owner_headers
    )
    other_base = f"/api/v1/w/{other.json()['id']}"
    foreign = await client.post(
        f"{other_base}/tours",
        json={
            "name": "Foreign",
            "steps": [{"selector": "#a"}],
            "trigger": {"type": "url_match", "url_pattern": "*"},
        },
        headers=workspace_ctx.owner_headers,
    )
    await client.post(
        f"{other_base}/tours/{foreign.json()['id']}/publish", headers=workspace_ctx.owner_headers
    )
    assert (await _bootstrap(client, key))["tours"] == []
