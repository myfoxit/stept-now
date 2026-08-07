"""SLA thresholds measured in business minutes.

The regression this closes: with wall-clock-only SLAs, a ticket that arrives
Friday evening breaches its FRT by Monday morning even for a Mon–Fri team, so
every breach notification becomes noise and the attainment report is worthless.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.core.db import get_session_factory, utcnow
from app.models.business_hours import WorkingHour
from app.models.conversation import Conversation
from app.models.sla import AppliedSla, SlaEvent, SlaStatus
from app.services import conversations as conversations_service
from app.services import slas as slas_service
from tests.slas.conftest import (
    SYSTEM,
    SlaCtx,
    create_inbox_via_api,
    make_policy,
)


async def _weekday_schedule(session, workspace_id: str, inbox_id: str) -> None:
    """Mon–Fri 09:00–17:00 UTC, weekend closed."""
    inbox_config = {"working_hours_enabled": True, "timezone": "UTC"}
    from app.models.inbox import Inbox

    inbox = await session.get(Inbox, inbox_id)
    inbox.config = {**(inbox.config or {}), **inbox_config}
    for day in range(7):
        session.add(
            WorkingHour(
                workspace_id=workspace_id,
                inbox_id=inbox_id,
                day_of_week=day,
                closed_all_day=day >= 5,
                open_minute=9 * 60,
                close_minute=17 * 60,
            )
        )
    await session.flush()


async def _conversation(sla: SlaCtx, *, created_at) -> Conversation:
    conversation = await conversations_service.create_conversation(
        sla.session, inbox=sla.inbox, contact=sla.contact, subject="Help", actor=SYSTEM
    )
    conversation.created_at = created_at
    await sla.session.flush()
    return conversation


class TestBusinessHoursSla:
    async def test_weekend_does_not_breach_a_business_hours_policy(self, sla: SlaCtx):
        """Friday 18:00 + a 60-minute FRT target is due Monday 10:00, so a scan
        on Saturday must find nothing."""
        await _weekday_schedule(sla.session, sla.workspace.id, sla.inbox.id)
        policy = await make_policy(sla.session, sla.workspace, frt=60)
        policy.only_during_business_hours = True
        friday_evening = utcnow().replace(
            year=2026, month=8, day=7, hour=18, minute=0, second=0, microsecond=0
        )
        conversation = await _conversation(sla, created_at=friday_evening)
        await slas_service.apply_sla(
            sla.session, sla.workspace.id, conversation, policy.id, actor=SYSTEM
        )
        await sla.session.commit()

        saturday = friday_evening + timedelta(days=1)
        assert await slas_service.scan_sla_breaches(now=saturday) == 0

        # Monday 10:01 — one business hour after the 09:00 opening — does breach.
        monday = friday_evening + timedelta(days=3, hours=16, minutes=1)
        assert await slas_service.scan_sla_breaches(now=monday) == 1

    async def test_wall_clock_policy_still_breaches_over_the_weekend(self, sla: SlaCtx):
        """The default stays wall-clock, so existing policies are unchanged."""
        await _weekday_schedule(sla.session, sla.workspace.id, sla.inbox.id)
        policy = await make_policy(sla.session, sla.workspace, name="Wall", frt=60)
        assert policy.only_during_business_hours is False
        friday_evening = utcnow().replace(
            year=2026, month=8, day=7, hour=18, minute=0, second=0, microsecond=0
        )
        conversation = await _conversation(sla, created_at=friday_evening)
        await slas_service.apply_sla(
            sla.session, sla.workspace.id, conversation, policy.id, actor=SYSTEM
        )
        await sla.session.commit()

        saturday = friday_evening + timedelta(days=1)
        assert await slas_service.scan_sla_breaches(now=saturday) == 1

    async def test_business_hours_policy_without_a_schedule_is_wall_clock(self, sla: SlaCtx):
        """An inbox that never configured hours must not become un-breachable."""
        policy = await make_policy(sla.session, sla.workspace, frt=60)
        policy.only_during_business_hours = True
        created = utcnow() - timedelta(hours=5)
        conversation = await _conversation(sla, created_at=created)
        await slas_service.apply_sla(
            sla.session, sla.workspace.id, conversation, policy.id, actor=SYSTEM
        )
        await sla.session.commit()
        assert await slas_service.scan_sla_breaches(now=utcnow()) == 1

    async def test_breach_still_finalises_normally(self, sla: SlaCtx):
        await _weekday_schedule(sla.session, sla.workspace.id, sla.inbox.id)
        policy = await make_policy(sla.session, sla.workspace, frt=30)
        policy.only_during_business_hours = True
        wednesday = utcnow().replace(
            year=2026, month=8, day=5, hour=10, minute=0, second=0, microsecond=0
        )
        conversation = await _conversation(sla, created_at=wednesday)
        conversation_id = conversation.id
        await slas_service.apply_sla(
            sla.session, sla.workspace.id, conversation, policy.id, actor=SYSTEM
        )
        await sla.session.commit()

        assert await slas_service.scan_sla_breaches(now=wednesday + timedelta(hours=2)) == 1
        async with get_session_factory()() as check:
            applied = (
                await check.execute(
                    select(AppliedSla).where(AppliedSla.conversation_id == conversation_id)
                )
            ).scalar_one()
            assert applied.status == SlaStatus.ACTIVE_WITH_MISSES
            events = list(
                (
                    await check.execute(
                        select(SlaEvent).where(SlaEvent.conversation_id == conversation_id)
                    )
                ).scalars()
            )
            assert [e.event_type for e in events] == ["frt"]


class TestWorkingHoursApi:
    async def test_set_and_read_back(self, client, workspace_ctx):
        ctx = workspace_ctx
        inbox = await create_inbox_via_api(client, ctx)
        response = await client.put(
            f"{ctx.base}/inboxes/{inbox['id']}/working-hours",
            json={
                "enabled": True,
                "timezone": "Europe/Berlin",
                "out_of_office_message": "Back at 9",
                "days": [
                    {"day_of_week": d, "open_minute": 540, "close_minute": 1020} for d in range(5)
                ],
            },
            headers=ctx.owner_headers,
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["enabled"] is True
        assert body["timezone"] == "Europe/Berlin"
        assert len(body["days"]) == 5
        assert "currently_open" in body

        again = await client.get(
            f"{ctx.base}/inboxes/{inbox['id']}/working-hours", headers=ctx.owner_headers
        )
        assert again.json()["out_of_office_message"] == "Back at 9"

    async def test_replacing_the_week_removes_old_days(self, client, workspace_ctx):
        ctx = workspace_ctx
        inbox = await create_inbox_via_api(client, ctx)
        base = f"{ctx.base}/inboxes/{inbox['id']}/working-hours"
        await client.put(
            base,
            json={"enabled": True, "days": [{"day_of_week": d} for d in range(7)]},
            headers=ctx.owner_headers,
        )
        replaced = await client.put(
            base,
            json={"enabled": True, "days": [{"day_of_week": 0}]},
            headers=ctx.owner_headers,
        )
        assert len(replaced.json()["days"]) == 1

    async def test_duplicate_day_is_rejected(self, client, workspace_ctx):
        ctx = workspace_ctx
        inbox = await create_inbox_via_api(client, ctx)
        response = await client.put(
            f"{ctx.base}/inboxes/{inbox['id']}/working-hours",
            json={"days": [{"day_of_week": 1}, {"day_of_week": 1}]},
            headers=ctx.owner_headers,
        )
        assert response.status_code == 422

    async def test_close_before_open_is_rejected(self, client, workspace_ctx):
        ctx = workspace_ctx
        inbox = await create_inbox_via_api(client, ctx)
        response = await client.put(
            f"{ctx.base}/inboxes/{inbox['id']}/working-hours",
            json={"days": [{"day_of_week": 1, "open_minute": 1000, "close_minute": 600}]},
            headers=ctx.owner_headers,
        )
        assert response.status_code == 422

    async def test_agent_cannot_change_hours(self, client, workspace_ctx):
        ctx = workspace_ctx
        inbox = await create_inbox_via_api(client, ctx)
        agent_headers = await ctx.add_member("hours-agent@example.com", role="agent")
        assert (
            await client.get(
                f"{ctx.base}/inboxes/{inbox['id']}/working-hours", headers=agent_headers
            )
        ).status_code == 200
        blocked = await client.put(
            f"{ctx.base}/inboxes/{inbox['id']}/working-hours",
            json={"enabled": True, "days": []},
            headers=agent_headers,
        )
        assert blocked.status_code == 403

    async def test_unknown_inbox_is_404(self, client, workspace_ctx):
        ctx = workspace_ctx
        response = await client.get(
            f"{ctx.base}/inboxes/01890000-0000-7000-8000-000000000000/working-hours",
            headers=ctx.owner_headers,
        )
        assert response.status_code == 404

    async def test_policy_flag_round_trips(self, client, workspace_ctx):
        ctx = workspace_ctx
        created = await client.post(
            f"{ctx.base}/slas",
            json={
                "name": "Business hours gold",
                "first_response_minutes": 60,
                "only_during_business_hours": True,
            },
            headers=ctx.owner_headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["only_during_business_hours"] is True

        patched = await client.patch(
            f"{ctx.base}/slas/{created.json()['id']}",
            json={"only_during_business_hours": False},
            headers=ctx.owner_headers,
        )
        assert patched.json()["only_during_business_hours"] is False
