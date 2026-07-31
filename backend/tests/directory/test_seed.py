"""Directory seed: shape + idempotency."""

from sqlalchemy import func, select

from app.models.canned_response import CannedResponse
from app.models.contact import Contact
from app.models.segment import Segment
from app.models.tag import Tag
from app.models.team import Team, TeamMember
from app.seed import SeedContext
from app.services import contacts_seed
from app.services.segments import apply_filters


async def _count(session, model, workspace_id) -> int:
    return (
        await session.execute(
            select(func.count()).select_from(model).where(model.workspace_id == workspace_id)
        )
    ).scalar_one()


async def test_seed_is_idempotent_and_complete(dir_ctx):
    ctx = SeedContext(workspace=dir_ctx.workspace, owner=dir_ctx.owner, agent=dir_ctx.agent)
    await contacts_seed.seed(dir_ctx.session, ctx)
    await contacts_seed.seed(dir_ctx.session, ctx)  # second run must not duplicate

    ws = dir_ctx.workspace.id
    assert await _count(dir_ctx.session, Contact, ws) == 9
    assert await _count(dir_ctx.session, Tag, ws) == 3
    assert await _count(dir_ctx.session, CannedResponse, ws) == 4
    assert await _count(dir_ctx.session, Segment, ws) == 1
    assert await _count(dir_ctx.session, Team, ws) == 1
    assert await _count(dir_ctx.session, TeamMember, ws) == 2

    tag_names = set(
        (await dir_ctx.session.execute(select(Tag.name).where(Tag.workspace_id == ws))).scalars()
    )
    assert tag_names == {"vip", "bug", "billing"}


async def test_seed_segment_matches_enterprise_contacts(dir_ctx):
    ctx = SeedContext(workspace=dir_ctx.workspace, owner=dir_ctx.owner, agent=dir_ctx.agent)
    await contacts_seed.seed(dir_ctx.session, ctx)
    segment = (
        await dir_ctx.session.execute(
            select(Segment).where(
                Segment.workspace_id == dir_ctx.workspace.id,
                Segment.name == "Enterprise customers",
            )
        )
    ).scalar_one()
    matches = await apply_filters(dir_ctx.session, dir_ctx.workspace.id, segment.filters)
    assert len(matches) == 3
    assert all(c.attributes["plan"] == "enterprise" for c in matches)
