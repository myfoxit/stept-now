"""The approval-gate matrix: pause/persist → approve / reject / expire / resume,
restart survival, notifications, and authz."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.agents import engine
from app.core.db import session_scope, utcnow
from app.models.agent_run import AgentRun, ApprovalRequest
from tests.agents.conftest import (
    approval_notified_user_ids,
    conversation_with_message,
    get_conversation,
    get_pending_approval,
    get_run,
    get_user_id,
    make_agent,
    run_now,
    step_kinds,
)
from tests.conftest import drain_tasks

CLOSE = '[[tool:close_conversation {"closing_message": "All set — closing this out!"}]]'


async def _paused_run(actx) -> tuple[str, str]:
    """A run parked at the close_conversation approval gate."""
    agent_id = await make_agent(actx)  # defaults: close_conversation → require_approval
    conversation_id, _ = await conversation_with_message(actx, f"Please close this. {CLOSE}")
    run_id = await run_now(actx, agent_id, conversation_id)
    return run_id, conversation_id


async def test_require_approval_pauses_and_persists_state(actx):
    await actx.wc.add_member("approver@x.io", "agent")
    await actx.wc.add_member("looker@x.io", "viewer")
    run_id, conversation_id = await _paused_run(actx)

    run = await get_run(run_id)
    assert run.status == "awaiting_approval"
    # The entire resumable state lives in the DB — no in-memory loop is held.
    assert run.pending_tool_call is not None
    assert run.pending_tool_call["name"] == "close_conversation"
    assert run.pending_tool_call["approval_request_id"]
    assert run.messages_snapshot, "messages must be snapshotted for resume"
    assert run.lease_expires_at is None

    approval = await get_pending_approval(run_id)
    assert approval.status == "pending"
    assert approval.tool_key == "close_conversation"
    assert "approval_request" in await step_kinds(run_id)
    # Conversation is still AI-owned while awaiting the human.
    assert (await get_conversation(conversation_id)).status == "pending"


async def test_approval_notifies_only_members_who_can_approve(actx):
    await actx.wc.add_member("approver@x.io", "agent")
    await actx.wc.add_member("looker@x.io", "viewer")
    run_id, _ = await _paused_run(actx)

    notified = await approval_notified_user_ids(actx.workspace_id)
    assert await get_user_id("owner@example.com") in notified  # owner: ai:approve
    assert await get_user_id("approver@x.io") in notified  # agent role: ai:approve
    assert await get_user_id("looker@x.io") not in notified  # viewer: no ai:approve


async def test_approve_resumes_and_resolves(actx, client):
    run_id, conversation_id = await _paused_run(actx)
    approval = await get_pending_approval(run_id)

    resp = await client.post(
        f"{actx.base}/ai/approvals/{approval.id}/decide",
        json={"approved": True},
        headers=actx.owner_headers,
    )
    assert resp.status_code == 200, resp.text
    await drain_tasks()

    run = await get_run(run_id)
    assert run.status == "completed"
    assert run.pending_tool_call is None
    assert (await get_conversation(conversation_id)).status == "resolved"
    kinds = await step_kinds(run_id)
    assert "approval_decision" in kinds
    assert kinds.count("tool_call") >= 1  # the approved close actually executed


async def test_reject_resumes_with_denial_and_keeps_open(actx, client):
    run_id, conversation_id = await _paused_run(actx)
    approval = await get_pending_approval(run_id)

    resp = await client.post(
        f"{actx.base}/ai/approvals/{approval.id}/decide",
        json={"approved": False, "note": "not right now"},
        headers=actx.owner_headers,
    )
    assert resp.status_code == 200, resp.text
    await drain_tasks()

    run = await get_run(run_id)
    assert run.status == "completed"  # the model saw the denial and produced a reply
    conversation = await get_conversation(conversation_id)
    assert conversation.status != "resolved"  # close was NOT executed

    from tests.agents.conftest import get_steps

    steps = await get_steps(run_id)
    denials = [
        s for s in steps if s.kind == "tool_result" and "Rejected" in str(s.output.get("error", ""))
    ]
    assert denials, "the rejection must be fed back as a tool_result the model sees"
    assert "final_reply" in [s.kind for s in steps]


async def test_expired_approval_resumes_as_rejected(actx, client):
    run_id, conversation_id = await _paused_run(actx)
    approval = await get_pending_approval(run_id)
    async with session_scope() as session:
        row = await session.get(ApprovalRequest, approval.id)
        assert row is not None
        row.expires_at = utcnow() - timedelta(hours=1)
        await session.commit()

    # Listing lazily expires overdue approvals and resumes their runs as rejected.
    resp = await client.get(f"{actx.base}/ai/approvals?status=pending", headers=actx.owner_headers)
    assert resp.status_code == 200
    await drain_tasks()

    async with session_scope() as session:
        row = await session.get(ApprovalRequest, approval.id)
        assert row is not None and row.status == "expired"
    run = await get_run(run_id)
    assert run.status == "completed"
    assert (await get_conversation(conversation_id)).status != "resolved"


async def test_gate_survives_process_restart(actx):
    """Prove the gate is DB-backed: a brand-new session resumes purely from the snapshot."""
    run_id, conversation_id = await _paused_run(actx)
    run = await get_run(run_id)
    snapshot = run.messages_snapshot
    assert snapshot and run.pending_tool_call

    # Simulate a fresh worker process: decide directly in the DB, then execute in a
    # new session with nothing carried over in memory.
    async with session_scope() as session:
        approval = (
            await session.execute(select(ApprovalRequest).where(ApprovalRequest.run_id == run_id))
        ).scalar_one()
        approval.status = "approved"
        approval.decided_at = utcnow()
        await session.commit()
    async with session_scope() as session:
        fresh_run = await session.get(AgentRun, run_id)
        assert fresh_run is not None
        await engine.execute_run(session, fresh_run)
        await session.commit()

    run = await get_run(run_id)
    assert run.status == "completed"
    assert run.pending_tool_call is None
    assert run.messages_snapshot is None
    assert (await get_conversation(conversation_id)).status == "resolved"


async def test_decide_authz_agent_yes_viewer_no(actx, client):
    approver_headers = await actx.wc.add_member("approver@x.io", "agent")
    viewer_headers = await actx.wc.add_member("looker@x.io", "viewer")
    run_id, _ = await _paused_run(actx)
    approval = await get_pending_approval(run_id)

    # Viewer lacks ai:approve — cannot list or decide.
    assert (
        await client.get(f"{actx.base}/ai/approvals", headers=viewer_headers)
    ).status_code == 403
    assert (
        await client.post(
            f"{actx.base}/ai/approvals/{approval.id}/decide",
            json={"approved": True},
            headers=viewer_headers,
        )
    ).status_code == 403

    # Agent role has ai:approve.
    assert (
        await client.get(f"{actx.base}/ai/approvals", headers=approver_headers)
    ).status_code == 200
    decide = await client.post(
        f"{actx.base}/ai/approvals/{approval.id}/decide",
        json={"approved": True},
        headers=approver_headers,
    )
    assert decide.status_code == 200, decide.text


async def test_double_decide_conflicts(actx, client):
    run_id, _ = await _paused_run(actx)
    approval = await get_pending_approval(run_id)
    first = await client.post(
        f"{actx.base}/ai/approvals/{approval.id}/decide",
        json={"approved": True},
        headers=actx.owner_headers,
    )
    assert first.status_code == 200
    await drain_tasks()
    second = await client.post(
        f"{actx.base}/ai/approvals/{approval.id}/decide",
        json={"approved": True},
        headers=actx.owner_headers,
    )
    assert second.status_code == 409  # already decided
