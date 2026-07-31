"""Widget public survey endpoint: submission (identified + anonymous), the
thanks message, validation, and the light-auth failure modes."""

from __future__ import annotations

from app.core.security import create_widget_token
from tests.surveys.conftest import (
    NPS_AND_TEXT,
    NPS_ID,
    TEXT_ID,
    create_contact,
    create_survey,
    publish_survey,
    widget_inbox_id,
    widget_key_for,
)


async def _live(client, ctx, **overrides):
    survey = await create_survey(client, ctx, **overrides)
    await publish_survey(client, ctx, survey["id"])
    return survey


async def test_identified_submission_records_contact(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    survey = await _live(client, workspace_ctx, thanks_message="You are a legend.")
    contact = await create_contact(client, workspace_ctx)
    headers = {"X-Widget-Token": create_widget_token(workspace_ctx.id, contact["id"])}

    resp = await client.post(
        f"/api/widget/surveys/{survey['id']}/responses?widget_key={key}"
        "&url=https://app.example.com/inbox",
        json={
            "answers": [
                {"question_id": NPS_ID, "value": 9},
                {"question_id": TEXT_ID, "value": "Fast and friendly."},
            ],
            "completed": True,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "thanks_message": "You are a legend."}

    stored = (
        await client.get(
            f"{workspace_ctx.base}/surveys/{survey['id']}/responses",
            headers=workspace_ctx.owner_headers,
        )
    ).json()["items"]
    assert len(stored) == 1
    assert stored[0]["contact_id"] == contact["id"]
    assert stored[0]["completed"] is True
    assert stored[0]["meta"] == {"url": "https://app.example.com/inbox"}


async def test_anonymous_submission_allowed(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    survey = await _live(client, workspace_ctx)

    resp = await client.post(
        f"/api/widget/surveys/{survey['id']}/responses?widget_key={key}",
        json={"answers": [{"question_id": NPS_ID, "value": 3}], "completed": True},
    )
    assert resp.status_code == 200
    assert resp.json()["thanks_message"] == "Thanks for the feedback!"

    stored = (
        await client.get(
            f"{workspace_ctx.base}/surveys/{survey['id']}/responses",
            headers=workspace_ctx.owner_headers,
        )
    ).json()["items"]
    assert stored[0]["contact_id"] is None


async def test_partial_submission_then_completion(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    survey = await _live(client, workspace_ctx)

    partial = await client.post(
        f"/api/widget/surveys/{survey['id']}/responses?widget_key={key}",
        json={"answers": [], "completed": False},
    )
    assert partial.status_code == 200

    full = await client.post(
        f"/api/widget/surveys/{survey['id']}/responses?widget_key={key}",
        json={"answers": [{"question_id": NPS_ID, "value": 10}], "completed": True},
    )
    assert full.status_code == 200

    results = (
        await client.get(
            f"{workspace_ctx.base}/surveys/{survey['id']}/results",
            headers=workspace_ctx.owner_headers,
        )
    ).json()
    assert results["responses"] == 2
    assert results["completed"] == 1


async def test_invalid_answers_are_rejected(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    survey = await _live(client, workspace_ctx)

    out_of_range = await client.post(
        f"/api/widget/surveys/{survey['id']}/responses?widget_key={key}",
        json={"answers": [{"question_id": NPS_ID, "value": 42}], "completed": True},
    )
    assert out_of_range.status_code == 422

    missing_required = await client.post(
        f"/api/widget/surveys/{survey['id']}/responses?widget_key={key}",
        json={"answers": [{"question_id": TEXT_ID, "value": "no score"}], "completed": True},
    )
    assert missing_required.status_code == 422

    unknown_survey = await client.post(
        f"/api/widget/surveys/00000000-0000-7000-8000-0000000000ff/responses?widget_key={key}",
        json={"answers": [], "completed": False},
    )
    assert unknown_survey.status_code == 404


async def test_wrong_key_missing_key_and_disabled_inbox(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    survey = await _live(client, workspace_ctx)
    body = {"answers": [{"question_id": NPS_ID, "value": 8}], "completed": True}

    unknown = await client.post(
        f"/api/widget/surveys/{survey['id']}/responses?widget_key=wk_nope", json=body
    )
    assert unknown.status_code == 404

    missing_key = await client.post(f"/api/widget/surveys/{survey['id']}/responses", json=body)
    assert missing_key.status_code == 422

    inbox_id = await widget_inbox_id(client, workspace_ctx)
    await client.patch(
        f"{workspace_ctx.base}/inboxes/{inbox_id}",
        json={"enabled": False},
        headers=workspace_ctx.owner_headers,
    )
    off = await client.post(
        f"/api/widget/surveys/{survey['id']}/responses?widget_key={key}", json=body
    )
    assert off.status_code == 404


async def test_cross_workspace_survey_is_unreachable(client, workspace_ctx):
    key = await widget_key_for(client, workspace_ctx)
    other = await client.post(
        "/api/v1/workspaces", json={"name": "Other WS"}, headers=workspace_ctx.owner_headers
    )
    other_base = f"/api/v1/w/{other.json()['id']}"
    foreign = await client.post(
        f"{other_base}/surveys",
        json={"name": "Foreign", "questions": NPS_AND_TEXT},
        headers=workspace_ctx.owner_headers,
    )
    resp = await client.post(
        f"/api/widget/surveys/{foreign.json()['id']}/responses?widget_key={key}",
        json={"answers": [{"question_id": NPS_ID, "value": 8}], "completed": True},
    )
    assert resp.status_code == 404
