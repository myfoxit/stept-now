"""Widget public checklist endpoints: progress, dismissal, anonymous no-op,
and the light-auth failure modes (bad key, disabled inbox, cross-workspace)."""

from __future__ import annotations

from app.core.security import create_widget_token
from tests.checklists.conftest import (
    THREE_ITEMS,
    create_checklist,
    create_contact,
    publish_checklist,
    widget_inbox_id,
    widget_key_for,
)


async def _live(client, ctx):
    checklist = await create_checklist(client, ctx)
    await publish_checklist(client, ctx, checklist["id"])
    return checklist


async def test_progress_and_dismiss_for_identified_contact(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    checklist = await _live(client, workspace_ctx)
    contact = await create_contact(client, workspace_ctx)
    headers = {"X-Widget-Token": create_widget_token(workspace_ctx.id, contact["id"])}
    first_item = checklist["items"][0]["id"]

    resp = await client.post(
        f"/api/widget/checklists/{checklist['id']}/progress?widget_key={key}",
        json={"item_id": first_item, "done": True},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stored"] is True
    assert list(body["item_state"]) == [first_item]
    assert body["completed"] is False

    # Completing every item flips `completed`.
    for item in checklist["items"][1:]:
        body = (
            await client.post(
                f"/api/widget/checklists/{checklist['id']}/progress?widget_key={key}",
                json={"item_id": item["id"], "done": True},
                headers=headers,
            )
        ).json()
    assert body["completed"] is True

    dismissed = await client.post(
        f"/api/widget/checklists/{checklist['id']}/dismiss?widget_key={key}", headers=headers
    )
    assert dismissed.status_code == 200
    assert dismissed.json() == {"ok": True, "stored": True}


async def test_anonymous_progress_is_a_no_op(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    checklist = await _live(client, workspace_ctx)

    resp = await client.post(
        f"/api/widget/checklists/{checklist['id']}/progress?widget_key={key}",
        json={"item_id": checklist["items"][0]["id"], "done": True},
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "stored": False,
        "item_state": {},
        "dismissed": False,
        "completed": False,
    }

    dismissed = await client.post(
        f"/api/widget/checklists/{checklist['id']}/dismiss?widget_key={key}"
    )
    assert dismissed.json() == {"ok": True, "stored": False}

    stats = await client.get(
        f"{workspace_ctx.base}/checklists/{checklist['id']}/stats",
        headers=workspace_ctx.owner_headers,
    )
    assert stats.json()["starts"] == 0


async def test_unknown_item_and_unknown_checklist(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    checklist = await _live(client, workspace_ctx)
    contact = await create_contact(client, workspace_ctx)
    headers = {"X-Widget-Token": create_widget_token(workspace_ctx.id, contact["id"])}

    bad_item = await client.post(
        f"/api/widget/checklists/{checklist['id']}/progress?widget_key={key}",
        json={"item_id": "nope", "done": True},
        headers=headers,
    )
    assert bad_item.status_code == 422

    missing = await client.post(
        f"/api/widget/checklists/00000000-0000-7000-8000-0000000000ff/progress?widget_key={key}",
        json={"item_id": "nope", "done": True},
        headers=headers,
    )
    assert missing.status_code == 404


async def test_wrong_key_missing_key_and_disabled_inbox(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    checklist = await _live(client, workspace_ctx)
    body = {"item_id": checklist["items"][0]["id"], "done": True}

    unknown = await client.post(
        f"/api/widget/checklists/{checklist['id']}/progress?widget_key=wk_nope", json=body
    )
    assert unknown.status_code == 404

    missing_key = await client.post(f"/api/widget/checklists/{checklist['id']}/progress", json=body)
    assert missing_key.status_code == 422

    inbox_id = await widget_inbox_id(client, workspace_ctx)
    disabled = await client.patch(
        f"{workspace_ctx.base}/inboxes/{inbox_id}",
        json={"enabled": False},
        headers=workspace_ctx.owner_headers,
    )
    assert disabled.status_code == 200
    off = await client.post(
        f"/api/widget/checklists/{checklist['id']}/progress?widget_key={key}", json=body
    )
    assert off.status_code == 404


async def test_cross_workspace_checklist_is_unreachable(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    other = await client.post(
        "/api/v1/workspaces", json={"name": "Other WS"}, headers=workspace_ctx.owner_headers
    )
    other_base = f"/api/v1/w/{other.json()['id']}"
    foreign = await client.post(
        f"{other_base}/checklists",
        json={"name": "Foreign", "items": THREE_ITEMS},
        headers=workspace_ctx.owner_headers,
    )
    resp = await client.post(
        f"/api/widget/checklists/{foreign.json()['id']}/progress?widget_key={key}",
        json={"item_id": foreign.json()["items"][0]["id"], "done": True},
    )
    assert resp.status_code == 404


async def test_cross_workspace_widget_token_is_anonymous(client, workspace_ctx):
    """A token minted for another workspace degrades to an anonymous visitor."""
    key = await widget_key_for(client, workspace_ctx)
    checklist = await _live(client, workspace_ctx)
    other = await client.post(
        "/api/v1/workspaces", json={"name": "Other WS"}, headers=workspace_ctx.owner_headers
    )
    stray = create_widget_token(other.json()["id"], "00000000-0000-7000-8000-0000000000aa")

    resp = await client.post(
        f"/api/widget/checklists/{checklist['id']}/progress?widget_key={key}",
        json={"item_id": checklist["items"][0]["id"], "done": True},
        headers={"X-Widget-Token": stray},
    )
    assert resp.status_code == 200
    assert resp.json()["stored"] is False
