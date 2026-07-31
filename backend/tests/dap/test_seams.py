"""Cross-module seams between tours and checklists/surveys.

Both integrations are soft imports in the tours code (a missing sibling module
must degrade, never break), so these tests skip rather than fail when that
module is absent.
"""

from __future__ import annotations

import pytest

from app.core.security import create_widget_token
from tests.tours.conftest import create_contact, create_tour, publish_tour, widget_key_for

INBOX_URL = "https://app.example.com/inbox?tab=open"


def _require(module_name: str, attr: str) -> None:
    module = pytest.importorskip(f"app.services.{module_name}")
    if not hasattr(module, attr):
        pytest.skip(f"{module_name}.{attr} has not shipped yet")


async def _live_tour(client, ctx, name="Welcome"):
    tour = await create_tour(
        client,
        ctx,
        name=name,
        trigger={"type": "url_match", "url_pattern": "*/inbox*"},
    )
    await publish_tour(client, ctx, tour["id"])
    return tour


async def test_completing_a_tour_checks_off_the_checklist_item(client, workspace_ctx):
    _require("checklists", "mark_tour_completed")
    key = await widget_key_for(client, workspace_ctx)
    tour = await _live_tour(client, workspace_ctx)

    created = await client.post(
        f"{workspace_ctx.base}/checklists",
        json={
            "name": "Getting started",
            "items": [
                {
                    "title": "Take the welcome tour",
                    "completion": {"type": "tour_completed", "tour_id": tour["id"]},
                },
                {"title": "Invite a teammate"},
            ],
        },
        headers=workspace_ctx.owner_headers,
    )
    assert created.status_code == 201, created.text
    checklist = created.json()
    published = await client.post(
        f"{workspace_ctx.base}/checklists/{checklist['id']}/publish",
        headers=workspace_ctx.owner_headers,
    )
    assert published.status_code == 200, published.text

    contact = await create_contact(client, workspace_ctx, name="Seam")
    headers = {"X-Widget-Token": create_widget_token(workspace_ctx.id, contact["id"])}
    done = await client.post(
        f"/api/widget/tours/{tour['id']}/events?widget_key={key}",
        json={"event": "completed"},
        headers=headers,
    )
    assert done.status_code == 200, done.text

    stats = await client.get(
        f"{workspace_ctx.base}/checklists/{checklist['id']}/stats",
        headers=workspace_ctx.owner_headers,
    )
    assert stats.status_code == 200, stats.text
    by_id = {item["id"]: item for item in stats.json()["items"]}
    tour_item = by_id[checklist["items"][0]["id"]]
    other_item = by_id[checklist["items"][1]["id"]]
    assert tour_item["completed_count"] == 1
    assert other_item["completed_count"] == 0


async def test_anonymous_completion_does_not_break_telemetry(client, workspace_ctx):
    """No contact → the checklist seam is a no-op, the event still records."""
    key = await widget_key_for(client, workspace_ctx)
    tour = await _live_tour(client, workspace_ctx)
    resp = await client.post(
        f"/api/widget/tours/{tour['id']}/events?widget_key={key}", json={"event": "completed"}
    )
    assert resp.status_code == 200
    stats = await client.get(
        f"{workspace_ctx.base}/tours/{tour['id']}/stats", headers=workspace_ctx.owner_headers
    )
    assert stats.json()["completions"] == 1


async def test_experiences_bootstrap_includes_checklists_and_surveys(client, workspace_ctx):
    _require("checklists", "deliverable_checklists")
    _require("surveys", "deliverable_surveys")
    key = await widget_key_for(client, workspace_ctx)
    tour = await _live_tour(client, workspace_ctx)

    checklist = await client.post(
        f"{workspace_ctx.base}/checklists",
        json={"name": "Onboarding", "items": [{"title": "Connect an inbox"}]},
        headers=workspace_ctx.owner_headers,
    )
    assert checklist.status_code == 201, checklist.text
    await client.post(
        f"{workspace_ctx.base}/checklists/{checklist.json()['id']}/publish",
        headers=workspace_ctx.owner_headers,
    )

    survey = await client.post(
        f"{workspace_ctx.base}/surveys",
        json={
            "name": "How are we doing?",
            "questions": [{"type": "nps", "question": "How likely are you to recommend us?"}],
            "trigger": {"type": "url_match", "url_pattern": "*/inbox*"},
        },
        headers=workspace_ctx.owner_headers,
    )
    assert survey.status_code == 201, survey.text
    await client.post(
        f"{workspace_ctx.base}/surveys/{survey.json()['id']}/publish",
        headers=workspace_ctx.owner_headers,
    )

    resp = await client.get(f"/api/widget/experiences?widget_key={key}&url={INBOX_URL}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [t["id"] for t in body["tours"]] == [tour["id"]]
    assert [c["id"] for c in body["checklists"]] == [checklist.json()["id"]]
    assert [s["id"] for s in body["surveys"]] == [survey.json()["id"]]
