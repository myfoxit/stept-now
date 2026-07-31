"""DAP seed: shape, idempotency, and stats computed from the seeded funnel."""

from __future__ import annotations

from sqlalchemy import func, select

from app.dap import seed as dap_seed
from app.models.tour import Tour, TourEvent
from app.services import tours as tours_service


async def _count(session, model, workspace_id) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(model).where(model.workspace_id == workspace_id)
        )
    ).scalar_one()


async def test_seed_shape_and_idempotent(seed_ctx):
    session, ctx = seed_ctx.session, seed_ctx.ctx
    ws = ctx.workspace.id

    await dap_seed.seed(session, ctx)
    await dap_seed.seed(session, ctx)  # second run must not duplicate anything

    assert await _count(session, Tour, ws) == 2
    assert await _count(session, TourEvent, ws) == 12

    tours = {
        t.name: t
        for t in (await session.execute(select(Tour).where(Tour.workspace_id == ws)))
        .scalars()
        .all()
    }
    welcome = tours["Welcome to Stept"]
    assert welcome.status == "live"
    assert welcome.trigger == {"type": "url_match", "url_pattern": "*/inbox*"}
    assert [s["selector"] for s in welcome.steps] == [
        '[data-tour="inbox"]',
        '[data-tour="knowledge"]',
        '[data-tour="ai"]',
    ]

    draft = tours["Discover automations"]
    assert draft.status == "draft"


async def test_seed_stats_match_funnel(seed_ctx):
    session, ctx = seed_ctx.session, seed_ctx.ctx
    await dap_seed.seed(session, ctx)

    welcome = (
        await session.execute(
            select(Tour).where(
                Tour.workspace_id == ctx.workspace.id, Tour.name == "Welcome to Stept"
            )
        )
    ).scalar_one()

    stats = await tours_service.compute_stats(session, ctx.workspace.id, welcome.id)
    assert stats.starts == 3
    assert stats.completions == 1
    assert stats.dismissals == 2
    assert stats.completion_rate == 0.3333
    assert [s.viewed for s in stats.steps] == [3, 2, 1]
    assert [s.drop_off for s in stats.steps] == [1, 1, 0]
