"""DAP seed: shape, idempotency, and stats computed from the seeded funnel."""

from __future__ import annotations

import pytest
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

    assert await _count(session, Tour, ws) == 4
    assert await _count(session, TourEvent, ws) == 16

    tours = {
        t.name: t
        for t in (await session.execute(select(Tour).where(Tour.workspace_id == ws)))
        .scalars()
        .all()
    }
    welcome = tours["Welcome to Stept"]
    assert welcome.status == "live"
    assert welcome.kind == "flow"
    assert welcome.trigger == {"type": "url_match", "url_pattern": "*/inbox*"}
    assert [s["selector"] for s in welcome.steps] == [
        '[data-tour="inbox"]',
        '[data-tour="knowledge"]',
        '[data-tour="ai"]',
    ]

    draft = tours["Discover automations"]
    assert draft.status == "draft"

    banner = tours["What's new in Stept"]
    assert banner.kind == "banner"
    assert banner.status == "live"
    assert banner.frequency == {"type": "once"}
    assert banner.theme["position"] == "top"
    assert [s["type"] for s in banner.steps] == ["banner"]

    driven = tours["Create your first automation"]
    assert driven.status == "draft"
    assert driven.settings["mode"] == "driven"
    assert [s["type"] for s in driven.steps] == ["action", "wait", "tooltip"]
    assert driven.steps[0]["action"] == {"kind": "click", "value": None, "url": None}
    assert driven.steps[1]["wait"]["for"] == "element"


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
    assert stats.starts == 4
    assert stats.completions == 1
    assert stats.dismissals == 3
    assert stats.completion_rate == 0.25
    assert [s.viewed for s in stats.steps] == [4, 2, 1]
    assert [s.drop_off for s in stats.steps] == [2, 1, 0]
    # The seeded funnel exercises every analytics tile.
    assert stats.unique_starts == 4
    assert stats.step_errors == 1
    assert [s.healed for s in stats.steps] == [1, 0, 0]
    assert sum(d.starts for d in stats.by_day) == 4


async def test_seed_chains_the_sibling_experience_seeder(seed_ctx):
    """app/dap/seed.py calls dap_seed_extra through its soft import when present."""
    pytest.importorskip("app.services.dap_seed_extra")
    from app.models.checklist import Checklist
    from app.models.survey import Survey

    session, ctx = seed_ctx.session, seed_ctx.ctx
    await dap_seed.seed(session, ctx)
    assert await _count(session, Checklist, ctx.workspace.id) >= 1
    assert await _count(session, Survey, ctx.workspace.id) >= 1
