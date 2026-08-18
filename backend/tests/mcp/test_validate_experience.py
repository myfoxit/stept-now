"""`validate_experience` — the publish-readiness check.

The schemas already reject malformed input, so everything asserted here is
*well-formed content that cannot work*: a flow with nothing in it, a checklist
item wired to a tour that was deleted, a window that already closed. Errors block
a sensible publish; warnings do not, because content shipped ahead of its
audience is legitimate.
"""

from __future__ import annotations

from datetime import timedelta

from app.core.db import session_scope, utcnow
from app.models.contact import Contact
from app.models.tour import Tour
from tests.mcp.conftest import call_tool, make_api_key


async def _key(client, ctx) -> str:
    return (await make_api_key(client, ctx, scopes=["write"], name="validate"))["key"]


def _codes(report: dict, bucket: str) -> set[str]:
    return {entry["code"] for entry in report[bucket]}


async def test_empty_flow_is_an_error(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    tour = await call_tool(client, "create_tour", {"name": "Empty"}, key=key)
    report = await call_tool(
        client, "validate_experience", {"type": "tour", "experience_id": tour["id"]}, key=key
    )
    assert report["ok"] is False
    assert "no_steps" in _codes(report, "errors")


async def test_healthy_tour_validates_clean(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    tour = await call_tool(
        client,
        "create_tour",
        {
            "name": "Good",
            "trigger": {"type": "url_match", "url_pattern": "*/app*"},
            "steps": [
                {
                    "type": "tooltip",
                    "selector": "#one",
                    "fallback_selectors": ["[data-testid=one]"],
                    "title": "Step one",
                    "body": "Do the thing.",
                }
            ],
        },
        key=key,
    )
    report = await call_tool(
        client, "validate_experience", {"type": "tour", "experience_id": tour["id"]}, key=key
    )
    assert report["ok"] is True
    assert report["errors"] == []
    assert report["warnings"] == []


async def test_banner_with_two_steps_is_an_error(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    tour = await call_tool(
        client,
        "create_tour",
        {
            "name": "Bar",
            "kind": "banner",
            "steps": [
                {"type": "banner", "title": "One", "body": "a"},
                {"type": "banner", "title": "Two", "body": "b"},
            ],
        },
        key=key,
    )
    report = await call_tool(
        client, "validate_experience", {"type": "tour", "experience_id": tour["id"]}, key=key
    )
    assert "step_count" in _codes(report, "errors")


async def test_anchored_step_without_healing_is_a_warning_not_an_error(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    tour = await call_tool(
        client,
        "create_tour",
        {
            "name": "Brittle",
            "steps": [{"type": "tooltip", "selector": ".css-1a2b3c", "title": "Here", "body": "x"}],
        },
        key=key,
    )
    report = await call_tool(
        client, "validate_experience", {"type": "tour", "experience_id": tour["id"]}, key=key
    )
    assert report["ok"] is True, "brittle targets must not block a publish"
    assert "brittle_target" in _codes(report, "warnings")


async def test_partial_step_urls_warn(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    tour = await call_tool(
        client,
        "create_tour",
        {
            "name": "Multi page",
            "steps": [
                {
                    "type": "tooltip",
                    "selector": "#a",
                    "title": "A",
                    "body": "x",
                    "url": "https://acme.test/app",
                    "fallback_selectors": ["[data-testid=a]"],
                },
                {
                    "type": "tooltip",
                    "selector": "#b",
                    "title": "B",
                    "body": "x",
                    "fallback_selectors": ["[data-testid=b]"],
                },
            ],
        },
        key=key,
    )
    report = await call_tool(
        client, "validate_experience", {"type": "tour", "experience_id": tour["id"]}, key=key
    )
    assert "partial_step_urls" in _codes(report, "warnings")


async def test_expired_schedule_is_an_error(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    past = (utcnow() - timedelta(days=2)).isoformat()
    tour = await call_tool(
        client,
        "create_tour",
        {
            "name": "Expired",
            "schedule": {"end_at": past},
            "steps": [{"type": "modal", "title": "Hi", "body": "x"}],
        },
        key=key,
    )
    report = await call_tool(
        client, "validate_experience", {"type": "tour", "experience_id": tour["id"]}, key=key
    )
    assert "schedule_ended" in _codes(report, "errors")


async def test_audience_matching_nobody_warns(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    tour = await call_tool(
        client,
        "create_tour",
        {
            "name": "Targeted",
            "audience": {
                "type": "filters",
                "filters": [{"field": "attributes.plan", "op": "eq", "value": "enterprise"}],
            },
            "steps": [{"type": "modal", "title": "Hi", "body": "x"}],
        },
        key=key,
    )
    report = await call_tool(
        client, "validate_experience", {"type": "tour", "experience_id": tour["id"]}, key=key
    )
    assert report["ok"] is True
    assert "audience_matches_none" in _codes(report, "warnings")

    async with session_scope() as session:
        session.add(
            Contact(
                workspace_id=workspace_ctx.id,
                email="ent@example.com",
                attributes={"plan": "enterprise"},
            )
        )

    again = await call_tool(
        client, "validate_experience", {"type": "tour", "experience_id": tour["id"]}, key=key
    )
    assert "audience_matches_none" not in _codes(again, "warnings")


async def test_manual_tour_nobody_starts_warns_until_a_checklist_links_it(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    tour = await call_tool(
        client,
        "create_tour",
        {
            "name": "Orphan",
            "trigger": {"type": "manual"},
            "steps": [{"type": "modal", "title": "Hi", "body": "x"}],
        },
        key=key,
    )
    report = await call_tool(
        client, "validate_experience", {"type": "tour", "experience_id": tour["id"]}, key=key
    )
    assert "manual_unreferenced" in _codes(report, "warnings")

    await call_tool(
        client,
        "create_checklist",
        {
            "name": "Setup",
            "items": [
                {"title": "Take it", "action": {"type": "start_tour", "tour_id": tour["id"]}}
            ],
        },
        key=key,
    )
    again = await call_tool(
        client, "validate_experience", {"type": "tour", "experience_id": tour["id"]}, key=key
    )
    assert "manual_unreferenced" not in _codes(again, "warnings")


async def test_checklist_pointing_at_a_deleted_tour_is_an_error(client, workspace_ctx):
    """The exact failure this tool exists for: valid when authored, broken by
    the time it publishes."""
    key = await _key(client, workspace_ctx)
    tour = await call_tool(
        client,
        "create_tour",
        {"name": "Doomed", "steps": [{"type": "modal", "title": "Hi", "body": "x"}]},
        key=key,
    )
    checklist = await call_tool(
        client,
        "create_checklist",
        {
            "name": "Setup",
            "items": [
                {
                    "title": "Take the tour",
                    "action": {"type": "start_tour", "tour_id": tour["id"]},
                    "completion": {"type": "tour_completed", "tour_id": tour["id"]},
                }
            ],
        },
        key=key,
    )
    clean = await call_tool(
        client,
        "validate_experience",
        {"type": "checklist", "experience_id": checklist["id"]},
        key=key,
    )
    assert clean["ok"] is True

    async with session_scope() as session:
        row = await session.get(Tour, tour["id"])
        await session.delete(row)

    broken = await call_tool(
        client,
        "validate_experience",
        {"type": "checklist", "experience_id": checklist["id"]},
        key=key,
    )
    assert broken["ok"] is False
    assert "missing_tour" in _codes(broken, "errors")


async def test_checklist_item_completing_on_a_different_tour_warns(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    first = await call_tool(
        client,
        "create_tour",
        {"name": "A", "steps": [{"type": "modal", "title": "Hi", "body": "x"}]},
        key=key,
    )
    second = await call_tool(
        client,
        "create_tour",
        {"name": "B", "steps": [{"type": "modal", "title": "Hi", "body": "x"}]},
        key=key,
    )
    checklist = await call_tool(
        client,
        "create_checklist",
        {
            "name": "Mismatched",
            "items": [
                {
                    "title": "Do it",
                    "action": {"type": "start_tour", "tour_id": first["id"]},
                    "completion": {"type": "tour_completed", "tour_id": second["id"]},
                }
            ],
        },
        key=key,
    )
    report = await call_tool(
        client,
        "validate_experience",
        {"type": "checklist", "experience_id": checklist["id"]},
        key=key,
    )
    assert "completion_tour_mismatch" in _codes(report, "warnings")


async def test_survey_without_questions_is_an_error(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    survey = await call_tool(client, "create_survey", {"name": "Empty"}, key=key)
    report = await call_tool(
        client, "validate_experience", {"type": "survey", "experience_id": survey["id"]}, key=key
    )
    assert "no_questions" in _codes(report, "errors")


async def test_validate_rejects_unknown_type_and_missing_id(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    unknown = await call_tool(
        client, "validate_experience", {"type": "widget", "experience_id": "x"}, key=key
    )
    assert "Unknown type" in unknown["error"]

    missing = await call_tool(
        client,
        "validate_experience",
        {"type": "tour", "experience_id": "01890000000000000000000000"},
        key=key,
    )
    assert "not found" in missing["error"].lower()
