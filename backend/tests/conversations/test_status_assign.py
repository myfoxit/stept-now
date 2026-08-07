"""Status transitions, snooze, assignment (incl. round-robin auto-assign)."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models.message import Message
from app.models.workspace import Membership
from app.services import conversations as convs
from app.services.inboxes import DEFAULT_WIDGET_CONFIG
from tests.conftest import get_session_factory
from tests.conversations.conftest import (
    SYSTEM,
    SvcCtx,
    committed_team_id,
    create_contact_via_db,
    create_inbox_via_api,
    make_contact,
    make_inbox,
    make_member,
    make_user,
    start_conversation,
)


async def test_status_transitions_and_snooze(client, workspace_ctx):
    contact_id = await create_contact_via_db(workspace_ctx.id, email="c@example.com")
    inbox = await create_inbox_via_api(client, workspace_ctx)
    conversation = await start_conversation(
        client, workspace_ctx, contact_id=contact_id, inbox_id=inbox["id"]
    )
    base = f"{workspace_ctx.base}/conversations/{conversation['id']}"
    until = (datetime.now(UTC) + timedelta(hours=8)).isoformat()

    snoozed = await client.patch(
        base,
        json={"status": "snoozed", "snoozed_until": until},
        headers=workspace_ctx.owner_headers,
    )
    assert snoozed.status_code == 200, snoozed.text
    assert snoozed.json()["status"] == "snoozed"
    assert snoozed.json()["snoozed_until"] is not None

    resolved = await client.patch(
        base, json={"status": "resolved"}, headers=workspace_ctx.owner_headers
    )
    body = resolved.json()
    assert body["status"] == "resolved"
    assert body["resolved_at"] is not None
    assert body["snoozed_until"] is None  # cleared when leaving snoozed
    assert body["waiting_since"] is None  # resolve empties the needs-response queue

    reopened = await client.patch(
        base, json={"status": "open"}, headers=workspace_ctx.owner_headers
    )
    assert reopened.json()["status"] == "open"

    invalid = await client.patch(
        base, json={"status": "archived"}, headers=workspace_ctx.owner_headers
    )
    assert invalid.status_code == 422


async def test_status_change_writes_activity_message(client, workspace_ctx):
    contact_id = await create_contact_via_db(workspace_ctx.id)
    inbox = await create_inbox_via_api(client, workspace_ctx)
    conversation = await start_conversation(
        client, workspace_ctx, contact_id=contact_id, inbox_id=inbox["id"]
    )
    await client.patch(
        f"{workspace_ctx.base}/conversations/{conversation['id']}",
        json={"status": "resolved"},
        headers=workspace_ctx.owner_headers,
    )
    async with get_session_factory()() as session:
        activities = (
            (
                await session.execute(
                    select(Message).where(
                        Message.conversation_id == conversation["id"],
                        Message.visibility == "activity",
                    )
                )
            )
            .scalars()
            .all()
        )
    assert any("resolved the conversation" in m.content for m in activities)


async def test_assign_unassign_and_team(client, workspace_ctx):
    agent_headers = await workspace_ctx.add_member("sam@example.com", role="agent")
    contact_id = await create_contact_via_db(workspace_ctx.id)
    inbox = await create_inbox_via_api(client, workspace_ctx)
    conversation = await start_conversation(
        client, workspace_ctx, contact_id=contact_id, inbox_id=inbox["id"]
    )
    base = f"{workspace_ctx.base}/conversations/{conversation['id']}"

    me = await client.get("/api/v1/me", headers=agent_headers)
    agent_user_id = me.json()["user"]["id"]

    assigned = await client.patch(
        base, json={"assignee_user_id": agent_user_id}, headers=workspace_ctx.owner_headers
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["assignee"] == {"id": agent_user_id, "name": "Test User"}

    # Explicit null unassigns (omitted field must NOT unassign).
    priority_only = await client.patch(
        base, json={"priority": "high"}, headers=workspace_ctx.owner_headers
    )
    assert priority_only.json()["assignee"] is not None
    assert priority_only.json()["priority"] == "high"

    unassigned = await client.patch(
        base, json={"assignee_user_id": None}, headers=workspace_ctx.owner_headers
    )
    assert unassigned.json()["assignee"] is None

    team_id = await committed_team_id(workspace_ctx.id)
    with_team = await client.patch(
        base, json={"team_id": team_id}, headers=workspace_ctx.owner_headers
    )
    assert with_team.json()["team_id"] == team_id


async def test_assigning_non_member_fails(client, workspace_ctx):
    contact_id = await create_contact_via_db(workspace_ctx.id)
    inbox = await create_inbox_via_api(client, workspace_ctx)
    conversation = await start_conversation(
        client, workspace_ctx, contact_id=contact_id, inbox_id=inbox["id"]
    )
    response = await client.patch(
        f"{workspace_ctx.base}/conversations/{conversation['id']}",
        json={"assignee_user_id": "0198344a-0000-7000-8000-00000000dead"},
        headers=workspace_ctx.owner_headers,
    )
    assert response.status_code == 422


async def test_round_robin_auto_assign_distribution(svc: SvcCtx):
    """Inbox with auto_assign + 2 available members → 3 conversations spread 2/1."""
    second = await make_user(svc.session, "Second Agent")
    await make_member(svc.session, svc.workspace, second, role="agent")
    rr_inbox = await make_inbox(
        svc.session, svc.workspace, channel_type="widget", config={"auto_assign": True}
    )
    assignees = []
    for i in range(3):
        contact = await make_contact(svc.session, svc.workspace, name=f"C{i}")
        conversation = await convs.create_conversation(
            svc.session, inbox=rr_inbox, contact=contact, actor=SYSTEM
        )
        assert conversation.assignee_user_id is not None
        assignees.append(conversation.assignee_user_id)

    distribution = Counter(assignees)
    assert set(distribution) == {svc.user.id, second.id}  # both members got work
    assert sorted(distribution.values()) == [1, 2]  # fair split of three


async def test_default_widget_inbox_leaves_conversations_unassigned(svc: SvcCtx):
    """A widget inbox created with the shipped defaults must not pre-claim work.

    Regression guard: auto_assign used to default on, so every inbound
    conversation was round-robined immediately and the Unassigned triage queue
    was always empty — in a one-member workspace it all landed on that member.
    """
    inbox = await make_inbox(
        svc.session, svc.workspace, channel_type="widget", config=DEFAULT_WIDGET_CONFIG
    )
    conversation = await convs.create_conversation(
        svc.session, inbox=inbox, contact=svc.contact, actor=SYSTEM
    )
    assert conversation.assignee_user_id is None


async def test_unavailable_members_are_skipped(svc: SvcCtx):
    membership = (
        await svc.session.execute(select(Membership).where(Membership.user_id == svc.user.id))
    ).scalar_one()
    membership.is_available = False
    rr_inbox = await make_inbox(svc.session, svc.workspace, config={"auto_assign": True})
    conversation = await convs.create_conversation(
        svc.session, inbox=rr_inbox, contact=svc.contact, actor=SYSTEM
    )
    assert conversation.assignee_user_id is None  # nobody available → unassigned
