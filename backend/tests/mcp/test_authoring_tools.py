"""Authoring MCP tools: create / update / publish tours, checklists, surveys,
plus the guide and schema introspection tools.

The load-bearing property under test is that an MCP-authored experience obeys
exactly the rules a dashboard-authored one does — the tools route through the
same Pydantic schemas, so a step that the REST API would reject must be rejected
here too, with a `details` payload the caller can act on.
"""

from __future__ import annotations

from tests.mcp.conftest import call_tool, list_tool_names, make_api_key

PERMISSION_ERROR_FRAGMENT = "tours:manage"


async def _write_key(client, ctx) -> str:
    return (await make_api_key(client, ctx, scopes=["write"], name="authoring"))["key"]


async def _read_key(client, ctx) -> str:
    return (await make_api_key(client, ctx, scopes=["read"], name="readonly"))["key"]


# --- registration ------------------------------------------------------------


async def test_authoring_tools_are_registered(client, workspace_ctx):
    names = await list_tool_names(client)
    assert {
        "get_authoring_guide",
        "get_experience_schema",
        "create_tour",
        "update_tour",
        "publish_tour",
        "pause_tour",
        "create_checklist",
        "update_checklist",
        "publish_checklist",
        "pause_checklist",
        "create_survey",
        "update_survey",
        "publish_survey",
        "pause_survey",
        "validate_experience",
    } <= names


async def test_write_tools_carry_destructive_and_idempotent_hints(client, workspace_ctx):
    """Clients decide what to confirm from the annotations, so a publish must
    not look like a plain read."""
    from tests.mcp.conftest import rpc

    listed = await rpc(client, "tools/list")
    by_name = {tool["name"]: tool for tool in listed["result"]["tools"]}

    assert by_name["get_authoring_guide"]["annotations"]["readOnlyHint"] is True
    assert by_name["create_tour"]["annotations"]["readOnlyHint"] is False
    assert by_name["create_tour"]["annotations"]["idempotentHint"] is False
    # publish_ is idempotent: publishing twice converges on the same state.
    assert by_name["publish_tour"]["annotations"]["idempotentHint"] is True
    assert by_name["publish_tour"]["annotations"]["destructiveHint"] is False


# --- guide + schema ----------------------------------------------------------


async def test_guide_without_args_returns_core_sections_and_contents(client, workspace_ctx):
    payload = await call_tool(client, "get_authoring_guide", {})
    assert {entry["section"] for entry in payload["contents"]} >= {
        "lifecycle",
        "tour-steps",
        "targets",
        "publish-requirements",
    }
    returned = {section["section"] for section in payload["sections"]}
    assert returned == {"lifecycle", "publish-requirements"}


async def test_guide_fetches_multiple_sections_in_one_call(client, workspace_ctx):
    payload = await call_tool(client, "get_authoring_guide", {"section": ["tour-steps", "targets"]})
    assert [section["section"] for section in payload["sections"]] == ["tour-steps", "targets"]
    assert "selector" in payload["sections"][0]["body"]


async def test_guide_reports_unknown_section_without_losing_the_others(client, workspace_ctx):
    """One typo in an array of sections must not cost the valid ones."""
    payload = await call_tool(client, "get_authoring_guide", {"section": ["targets", "nonsense"]})
    assert [section["section"] for section in payload["sections"]] == ["targets"]
    assert payload["unknown_sections"] == ["nonsense"]
    assert "targets" in payload["available_sections"]


async def test_experience_schema_returns_create_and_update_shapes(client, workspace_ctx):
    payload = await call_tool(client, "get_experience_schema", {"type": "tour"})
    assert payload["type"] == "tour"
    assert "steps" in payload["create"]["properties"]
    assert "name" in payload["update"]["properties"]


async def test_experience_schema_rejects_unknown_type(client, workspace_ctx):
    payload = await call_tool(client, "get_experience_schema", {"type": "banner"})
    assert "error" in payload
    assert "tour, checklist, survey" in payload["error"]


# --- tours -------------------------------------------------------------------


async def test_create_tour_lands_as_draft(client, workspace_ctx):
    key = await _write_key(client, workspace_ctx)
    payload = await call_tool(
        client,
        "create_tour",
        {
            "name": "Welcome flow",
            "trigger": {"type": "url_match", "url_pattern": "*/app*"},
            "steps": [
                {
                    "type": "tooltip",
                    "selector": "#new-project",
                    "title": "Start here",
                    "body": "Create your first project.",
                }
            ],
        },
        key=key,
    )
    assert payload["status"] == "draft", "created content must never be live"
    assert payload["kind"] == "flow"
    assert payload["steps"] == 1
    assert payload["version"] == 1


async def test_publish_then_pause_round_trip(client, workspace_ctx):
    key = await _write_key(client, workspace_ctx)
    tour = await call_tool(
        client,
        "create_tour",
        {"name": "Flow", "steps": [{"type": "modal", "title": "Hi", "body": "Welcome"}]},
        key=key,
    )
    published = await call_tool(client, "publish_tour", {"tour_id": tour["id"]}, key=key)
    assert published["status"] == "live"
    paused = await call_tool(client, "pause_tour", {"tour_id": tour["id"]}, key=key)
    assert paused["status"] == "paused"


async def test_update_tour_patches_only_supplied_fields(client, workspace_ctx):
    key = await _write_key(client, workspace_ctx)
    tour = await call_tool(
        client,
        "create_tour",
        {
            "name": "Original",
            "priority": 7,
            "steps": [{"type": "modal", "title": "Hi", "body": "Welcome"}],
        },
        key=key,
    )
    updated = await call_tool(
        client, "update_tour", {"tour_id": tour["id"], "name": "Renamed"}, key=key
    )
    assert updated["name"] == "Renamed"
    assert updated["priority"] == 7, "an omitted field must be left alone"
    assert updated["steps"] == 1, "omitting steps must not clear them"


async def test_update_tour_steps_is_a_full_replacement(client, workspace_ctx):
    key = await _write_key(client, workspace_ctx)
    tour = await call_tool(
        client,
        "create_tour",
        {
            "name": "Two steps",
            "steps": [
                {"type": "modal", "title": "One", "body": "a"},
                {"type": "modal", "title": "Two", "body": "b"},
            ],
        },
        key=key,
    )
    updated = await call_tool(
        client,
        "update_tour",
        {"tour_id": tour["id"], "steps": [{"type": "modal", "title": "Only", "body": "a"}]},
        key=key,
    )
    assert updated["steps"] == 1
    assert updated["version"] == 2, "a content change bumps the version"


async def test_invalid_step_is_rejected_with_actionable_details(client, workspace_ctx):
    """A tooltip without a selector cannot render; the schema rejects it and the
    caller is told exactly which field."""
    key = await _write_key(client, workspace_ctx)
    payload = await call_tool(
        client,
        "create_tour",
        {"name": "Broken", "steps": [{"type": "tooltip", "title": "No anchor"}]},
        key=key,
    )
    assert payload["error"].startswith("Invalid input")
    locations = {detail["loc"] for detail in payload["details"]}
    assert any("steps.0" in loc for loc in locations)


async def test_action_step_without_action_config_is_rejected(client, workspace_ctx):
    key = await _write_key(client, workspace_ctx)
    payload = await call_tool(
        client,
        "create_tour",
        {
            "name": "Broken",
            "steps": [{"type": "action", "selector": "#go", "title": "Go", "body": "x"}],
        },
        key=key,
    )
    assert "details" in payload


async def test_javascript_cta_url_is_rejected(client, workspace_ctx):
    """The player puts a CTA url in an href on the customer's own page — a
    javascript: URL there is stored XSS."""
    key = await _write_key(client, workspace_ctx)
    payload = await call_tool(
        client,
        "create_tour",
        {
            "name": "XSS",
            "steps": [
                {
                    "type": "modal",
                    "title": "Hi",
                    "body": "x",
                    "cta": {"label": "Go", "url": "javascript:alert(1)"},
                }
            ],
        },
        key=key,
    )
    assert "details" in payload


# --- checklists + surveys ----------------------------------------------------


async def test_create_checklist_with_tour_linked_item(client, workspace_ctx):
    key = await _write_key(client, workspace_ctx)
    tour = await call_tool(
        client,
        "create_tour",
        {"name": "Linked", "steps": [{"type": "modal", "title": "Hi", "body": "x"}]},
        key=key,
    )
    payload = await call_tool(
        client,
        "create_checklist",
        {
            "name": "Getting started",
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
    assert payload["status"] == "draft"
    assert payload["items"] == 1


async def test_checklist_start_tour_without_tour_id_is_rejected(client, workspace_ctx):
    key = await _write_key(client, workspace_ctx)
    payload = await call_tool(
        client,
        "create_checklist",
        {
            "name": "Broken",
            "items": [{"title": "Go", "action": {"type": "start_tour"}}],
        },
        key=key,
    )
    assert "details" in payload


async def test_create_survey_and_publish(client, workspace_ctx):
    key = await _write_key(client, workspace_ctx)
    survey = await call_tool(
        client,
        "create_survey",
        {
            "name": "Onboarding NPS",
            "questions": [
                {"type": "nps", "question": "How likely are you to recommend us?"},
                {
                    "type": "select",
                    "question": "What did you come here to do?",
                    "options": ["Evaluate", "Set up", "Something else"],
                },
            ],
        },
        key=key,
    )
    assert survey["questions"] == 2
    published = await call_tool(client, "publish_survey", {"survey_id": survey["id"]}, key=key)
    assert published["status"] == "live"


async def test_select_question_with_one_option_is_rejected(client, workspace_ctx):
    key = await _write_key(client, workspace_ctx)
    payload = await call_tool(
        client,
        "create_survey",
        {
            "name": "Bad",
            "questions": [{"type": "select", "question": "Pick", "options": ["only"]}],
        },
        key=key,
    )
    assert "details" in payload


# --- authz -------------------------------------------------------------------


async def test_read_scoped_key_cannot_author(client, workspace_ctx):
    key = await _read_key(client, workspace_ctx)
    for tool, args in (
        ("create_tour", {"name": "Nope"}),
        ("create_checklist", {"name": "Nope"}),
        ("create_survey", {"name": "Nope"}),
        ("publish_tour", {"tour_id": "whatever"}),
    ):
        payload = await call_tool(client, tool, args, key=key)
        assert PERMISSION_ERROR_FRAGMENT in payload["error"], (tool, payload)


async def test_authoring_without_a_key_is_refused(client, workspace_ctx):
    payload = await call_tool(client, "create_tour", {"name": "Nope"})
    assert payload == {"error": "Authentication required. Provide a valid API key."}


async def test_cannot_publish_another_workspaces_tour(client, workspace_ctx):
    """Keys are workspace-scoped; an id from elsewhere must read as not found,
    not as success."""
    from tests.conftest import bearer, signup

    key = await _write_key(client, workspace_ctx)
    tour = await call_tool(
        client,
        "create_tour",
        {"name": "Mine", "steps": [{"type": "modal", "title": "Hi", "body": "x"}]},
        key=key,
    )

    other_auth = await signup(client, "intruder@example.com", name="Intruder")
    other_ws = await client.post(
        "/api/v1/workspaces", json={"name": "Other"}, headers=bearer(other_auth)
    )
    other_id = other_ws.json()["id"]
    other_key_response = await client.post(
        f"/api/v1/w/{other_id}/api-keys",
        json={"name": "other", "scopes": ["write"]},
        headers=bearer(other_auth),
    )
    other_key = other_key_response.json()["key"]

    payload = await call_tool(client, "publish_tour", {"tour_id": tour["id"]}, key=other_key)
    assert "error" in payload
    assert "not found" in payload["error"].lower()
