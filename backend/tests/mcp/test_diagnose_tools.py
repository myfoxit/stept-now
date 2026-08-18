"""Diagnosis MCP tools.

The property that matters most here is agreement with delivery: `diagnose_contact`
must not claim something is showing that `deliverable_tours` would withhold, and
vice versa. The tests below assert on both sides of that — a tour blocked by
frequency, and one blocked by audience, each has to appear in `blocked` with the
right gate named, while the one that survives has to appear in `showing`.
"""

from __future__ import annotations

from app.core.db import session_scope
from app.models.contact import Contact
from app.services import tours as tours_service
from tests.mcp.conftest import call_tool, list_tool_names, make_api_key


async def _key(client, ctx, scopes=("write",)) -> str:
    return (await make_api_key(client, ctx, scopes=list(scopes), name="diag"))["key"]


async def _make_contact(workspace_id: str, **fields) -> str:
    async with session_scope() as session:
        contact = Contact(workspace_id=workspace_id, **fields)
        session.add(contact)
        await session.flush()
        return contact.id


async def _record(workspace_id: str, tour_id: str, event: str, contact_id: str) -> None:
    async with session_scope() as session:
        await tours_service.record_event(
            session, workspace_id, tour_id, event=event, contact_id=contact_id
        )


async def _live_tour(client, key, **overrides) -> dict:
    body = {
        "name": "Welcome",
        "trigger": {"type": "url_match", "url_pattern": "*/app*"},
        "steps": [{"type": "modal", "title": "Hi", "body": "Welcome aboard"}],
        **overrides,
    }
    tour = await call_tool(client, "create_tour", body, key=key)
    assert "error" not in tour, tour
    await call_tool(client, "publish_tour", {"tour_id": tour["id"]}, key=key)
    return tour


async def test_diagnose_tools_are_registered(client, workspace_ctx):
    assert {"diagnose_experience", "diagnose_contact"} <= await list_tool_names(client)


# --- diagnose_experience -----------------------------------------------------


async def test_draft_tour_is_blocked_by_status(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    tour = await call_tool(
        client,
        "create_tour",
        {"name": "Draft", "steps": [{"type": "modal", "title": "Hi", "body": "x"}]},
        key=key,
    )
    payload = await call_tool(
        client, "diagnose_experience", {"type": "tour", "experience_id": tour["id"]}, key=key
    )
    assert payload["verdict"] == "blocked"
    status_gate = next(g for g in payload["gates"] if g["gate"] == "status")
    assert status_gate["status"] == "fail"
    assert "status" in payload["summary"]


async def test_manual_trigger_is_reported_as_never_auto_delivered(client, workspace_ctx):
    """The most common authoring mistake — the gate must say so in words."""
    key = await _key(client, workspace_ctx)
    tour = await _live_tour(client, key, trigger={"type": "manual"})
    payload = await call_tool(
        client, "diagnose_experience", {"type": "tour", "experience_id": tour["id"]}, key=key
    )
    trigger_gate = next(g for g in payload["gates"] if g["gate"] == "trigger")
    assert trigger_gate["status"] == "fail"
    assert "NEVER auto-delivered" in trigger_gate["detail"]


async def test_gates_are_unknown_until_url_and_contact_are_supplied(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    tour = await _live_tour(
        client,
        key,
        audience={"type": "filters", "filters": [{"field": "email", "op": "exists"}]},
    )
    bare = await call_tool(
        client, "diagnose_experience", {"type": "tour", "experience_id": tour["id"]}, key=key
    )
    assert bare["verdict"] == "indeterminate"
    unknown_gates = {g["gate"] for g in bare["gates"] if g["status"] == "unknown"}
    assert {"trigger", "audience", "frequency"} <= unknown_gates

    contact_id = await _make_contact(workspace_ctx.id, email="nina@example.com")
    resolved = await call_tool(
        client,
        "diagnose_experience",
        {
            "type": "tour",
            "experience_id": tour["id"],
            "url": "https://acme.test/app/home",
            "contact_id": contact_id,
        },
        key=key,
    )
    assert resolved["verdict"] == "showing"
    assert all(gate["status"] == "pass" for gate in resolved["gates"]), resolved["gates"]


async def test_url_that_does_not_match_the_pattern_fails_the_trigger_gate(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    tour = await _live_tour(client, key)
    payload = await call_tool(
        client,
        "diagnose_experience",
        {"type": "tour", "experience_id": tour["id"], "url": "https://acme.test/marketing"},
        key=key,
    )
    trigger_gate = next(g for g in payload["gates"] if g["gate"] == "trigger")
    assert trigger_gate["status"] == "fail"
    assert "does not match" in trigger_gate["detail"]


async def test_unmatched_audience_filter_reports_the_contacts_actual_value(client, workspace_ctx):
    """An unmatched condition has to explain itself without a second lookup."""
    key = await _key(client, workspace_ctx)
    tour = await _live_tour(
        client,
        key,
        audience={
            "type": "filters",
            "filters": [{"field": "attributes.plan", "op": "eq", "value": "enterprise"}],
        },
    )
    contact_id = await _make_contact(
        workspace_ctx.id, email="free@example.com", attributes={"plan": "free"}
    )
    payload = await call_tool(
        client,
        "diagnose_experience",
        {
            "type": "tour",
            "experience_id": tour["id"],
            "url": "https://acme.test/app",
            "contact_id": contact_id,
        },
        key=key,
    )
    assert payload["verdict"] == "blocked"
    condition = payload["conditions"][0]
    assert condition["status"] == "unmatched"
    assert condition["actual"] == "free", "the contact's real value must be in the report"


async def test_frequency_gate_fails_after_the_contact_started_a_once_tour(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    tour = await _live_tour(client, key, frequency={"type": "once"})
    contact_id = await _make_contact(workspace_ctx.id, email="seen@example.com")
    await _record(workspace_ctx.id, tour["id"], "started", contact_id)

    payload = await call_tool(
        client,
        "diagnose_experience",
        {
            "type": "tour",
            "experience_id": tour["id"],
            "url": "https://acme.test/app",
            "contact_id": contact_id,
        },
        key=key,
    )
    frequency_gate = next(g for g in payload["gates"] if g["gate"] == "frequency")
    assert frequency_gate["status"] == "fail"
    assert "started" in frequency_gate["detail"]


async def test_diagnose_rejects_unknown_type_and_missing_contact(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    unknown_type = await call_tool(
        client, "diagnose_experience", {"type": "banner", "experience_id": "x"}, key=key
    )
    assert "Unknown type" in unknown_type["error"]

    tour = await _live_tour(client, key)
    missing_contact = await call_tool(
        client,
        "diagnose_experience",
        {"type": "tour", "experience_id": tour["id"], "contact_id": "01890000000000000000000000"},
        key=key,
    )
    assert "not found" in missing_contact["error"]


# --- diagnose_contact --------------------------------------------------------


async def test_diagnose_contact_agrees_with_delivery(client, workspace_ctx):
    """One deliverable tour, one blocked by frequency, one blocked by audience —
    the split has to match what the widget would actually receive."""
    key = await _key(client, workspace_ctx)
    contact_id = await _make_contact(
        workspace_ctx.id, email="nina@example.com", attributes={"plan": "free"}
    )

    visible = await _live_tour(client, key, name="Visible")
    seen = await _live_tour(client, key, name="Already seen", frequency={"type": "once"})
    await _record(workspace_ctx.id, seen["id"], "started", contact_id)
    targeted = await _live_tour(
        client,
        key,
        name="Enterprise only",
        audience={
            "type": "filters",
            "filters": [{"field": "attributes.plan", "op": "eq", "value": "enterprise"}],
        },
    )

    payload = await call_tool(
        client,
        "diagnose_contact",
        {"contact_id": contact_id, "url": "https://acme.test/app/home"},
        key=key,
    )

    showing_ids = {row["id"] for row in payload["showing"]}
    blocked = {row["id"]: row for row in payload["blocked"]}

    assert visible["id"] in showing_ids
    assert seen["id"] in blocked
    assert blocked[seen["id"]]["blocked_by"] == "frequency"
    assert targeted["id"] in blocked
    assert blocked[targeted["id"]]["blocked_by"] == "audience"

    # And the delivery code itself must agree with the "showing" list.
    async with session_scope() as session:
        contact = await session.get(Contact, contact_id)
        delivered = await tours_service.deliverable_tours(
            session, workspace_ctx.id, url="https://acme.test/app/home", contact=contact
        )
    assert {tour.id for tour in delivered} == showing_ids


async def test_diagnose_contact_reports_identification_state(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    anonymous = await _make_contact(workspace_ctx.id, name="Anon")
    payload = await call_tool(
        client,
        "diagnose_contact",
        {"contact_id": anonymous, "url": "https://acme.test/app"},
        key=key,
    )
    assert payload["contact"]["identified"] is False


async def test_diagnose_contact_requires_a_known_contact(client, workspace_ctx):
    key = await _key(client, workspace_ctx)
    payload = await call_tool(
        client,
        "diagnose_contact",
        {"contact_id": "01890000000000000000000000", "url": "https://acme.test/app"},
        key=key,
    )
    assert "not found" in payload["error"]


# --- authz -------------------------------------------------------------------


async def test_diagnosis_needs_a_key_but_only_read_scope(client, workspace_ctx):
    """Diagnosis is read-only: a `read` key must be enough, no key must not."""
    unauthenticated = await call_tool(
        client, "diagnose_experience", {"type": "tour", "experience_id": "x"}
    )
    assert unauthenticated == {"error": "Authentication required. Provide a valid API key."}

    write_key = await _key(client, workspace_ctx)
    tour = await _live_tour(client, write_key)
    read_key = (await make_api_key(client, workspace_ctx, scopes=["read"], name="ro"))["key"]
    payload = await call_tool(
        client,
        "diagnose_experience",
        {"type": "tour", "experience_id": tour["id"]},
        key=read_key,
    )
    assert "error" not in payload
    assert payload["id"] == tour["id"]
