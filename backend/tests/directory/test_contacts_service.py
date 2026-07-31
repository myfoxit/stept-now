"""find_or_create matching matrix, segment filter engine, CSAT idempotency."""

from datetime import timedelta

import pytest

from app.core.db import utcnow
from app.core.errors import NotFoundError, ValidationFailure
from app.models.contact import Contact
from app.services import csat as csat_service
from app.services.contacts import find_or_create
from app.services.segments import apply_filters
from tests.directory.conftest import capture_events

# ---------------------------------------------------------------------------
# find_or_create matrix
# ---------------------------------------------------------------------------


async def test_find_or_create_creates_and_emits(dir_ctx):
    with capture_events("contact.created") as created_events:
        contact, created = await find_or_create(
            dir_ctx.session,
            dir_ctx.workspace.id,
            external_id="u-1",
            email="Jane@Example.COM",
            name="Jane",
            attributes={"plan": "pro"},
        )
    assert created is True
    assert contact.email == "jane@example.com"  # normalized
    assert contact.first_seen_at is not None and contact.last_seen_at is not None
    assert len(created_events) == 1
    assert created_events[0].payload["contact_id"] == contact.id


async def test_find_or_create_external_id_wins_over_email(dir_ctx):
    a, _ = await find_or_create(
        dir_ctx.session, dir_ctx.workspace.id, external_id="ext-a", email="a@example.com"
    )
    b, _ = await find_or_create(dir_ctx.session, dir_ctx.workspace.id, email="b@example.com")
    match, created = await find_or_create(
        dir_ctx.session, dir_ctx.workspace.id, external_id="ext-a", email="b@example.com"
    )
    assert created is False
    assert match.id == a.id  # external_id match beats the email match
    assert match.email == "b@example.com"  # provided email updates the record
    assert b.id != a.id


async def test_find_or_create_email_fallback_picks_most_recent(dir_ctx):
    first, _ = await find_or_create(dir_ctx.session, dir_ctx.workspace.id, email="dup@example.com")
    # Force distinct created_at so "most recent" is deterministic.
    first.created_at = utcnow() - timedelta(days=2)
    await dir_ctx.session.flush()
    second = Contact(workspace_id=dir_ctx.workspace.id, email="dup@example.com", name="Newer")
    dir_ctx.session.add(second)
    await dir_ctx.session.flush()

    match, created = await find_or_create(
        dir_ctx.session, dir_ctx.workspace.id, email="dup@example.com"
    )
    assert created is False
    assert match.id == second.id


async def test_find_or_create_merges_attributes(dir_ctx):
    contact, _ = await find_or_create(
        dir_ctx.session,
        dir_ctx.workspace.id,
        external_id="merge-1",
        attributes={"plan": "free", "keep": "yes"},
    )
    updated, created = await find_or_create(
        dir_ctx.session,
        dir_ctx.workspace.id,
        external_id="merge-1",
        attributes={"plan": "pro", "extra": 1},
    )
    assert created is False
    assert updated.id == contact.id
    assert updated.attributes == {"plan": "pro", "keep": "yes", "extra": 1}


async def test_find_or_create_never_downgrades_verified(dir_ctx):
    contact, _ = await find_or_create(
        dir_ctx.session, dir_ctx.workspace.id, external_id="v-1", verified=True
    )
    again, _ = await find_or_create(
        dir_ctx.session, dir_ctx.workspace.id, external_id="v-1", verified=False
    )
    assert again.id == contact.id
    assert again.verified is True  # False never downgrades
    # And an unverified contact can be upgraded.
    plain, _ = await find_or_create(dir_ctx.session, dir_ctx.workspace.id, external_id="v-2")
    assert plain.verified is False
    upgraded, _ = await find_or_create(
        dir_ctx.session, dir_ctx.workspace.id, external_id="v-2", verified=True
    )
    assert upgraded.verified is True


async def test_find_or_create_email_bound_to_other_external_id_creates_new(dir_ctx):
    existing, _ = await find_or_create(
        dir_ctx.session, dir_ctx.workspace.id, external_id="owner-1", email="shared@example.com"
    )
    other, created = await find_or_create(
        dir_ctx.session, dir_ctx.workspace.id, external_id="owner-2", email="shared@example.com"
    )
    assert created is True  # same email but a different external identity
    assert other.id != existing.id
    assert existing.external_id == "owner-1"


async def test_find_or_create_scoped_to_workspace(dir_ctx):
    from app.models.workspace import Workspace

    other_ws = Workspace(name="Other", slug="other-ws-svc")
    dir_ctx.session.add(other_ws)
    await dir_ctx.session.flush()
    a, created_a = await find_or_create(
        dir_ctx.session, dir_ctx.workspace.id, external_id="same-ext"
    )
    b, created_b = await find_or_create(dir_ctx.session, other_ws.id, external_id="same-ext")
    assert created_a and created_b
    assert a.id != b.id


# ---------------------------------------------------------------------------
# segment filter engine
# ---------------------------------------------------------------------------


async def _seed_filter_contacts(dir_ctx):
    now = utcnow()
    ws = dir_ctx.workspace.id
    await find_or_create(
        dir_ctx.session,
        ws,
        email="enterprise@corp.com",
        name="Enda",
        attributes={"plan": "enterprise", "company": "Corp"},
    )
    await find_or_create(
        dir_ctx.session, ws, email="pro@corp.com", name="Priya", attributes={"plan": "pro"}
    )
    stale, _ = await find_or_create(
        dir_ctx.session, ws, email="free@old.com", name="Fred", attributes={"plan": "free"}
    )
    stale.last_seen_at = now - timedelta(days=90)
    await dir_ctx.session.flush()
    return now


async def test_apply_filters_attributes_plan_eq(dir_ctx):
    await _seed_filter_contacts(dir_ctx)
    matches = await apply_filters(
        dir_ctx.session,
        dir_ctx.workspace.id,
        [{"field": "attributes.plan", "op": "eq", "value": "enterprise"}],
    )
    assert [c.email for c in matches] == ["enterprise@corp.com"]


async def test_apply_filters_attributes_exists(dir_ctx):
    await _seed_filter_contacts(dir_ctx)
    with_company = await apply_filters(
        dir_ctx.session,
        dir_ctx.workspace.id,
        [{"field": "attributes.company", "op": "exists"}],
    )
    without_company = await apply_filters(
        dir_ctx.session,
        dir_ctx.workspace.id,
        [{"field": "attributes.company", "op": "not_exists"}],
    )
    assert [c.email for c in with_company] == ["enterprise@corp.com"]
    assert {c.email for c in without_company} == {"pro@corp.com", "free@old.com"}


async def test_apply_filters_last_seen_gt(dir_ctx):
    now = await _seed_filter_contacts(dir_ctx)
    recent = await apply_filters(
        dir_ctx.session,
        dir_ctx.workspace.id,
        [
            {
                "field": "last_seen_at",
                "op": "gt",
                "value": (now - timedelta(days=7)).isoformat(),
            }
        ],
    )
    assert {c.email for c in recent} == {"enterprise@corp.com", "pro@corp.com"}


async def test_apply_filters_core_string_and_bool_ops(dir_ctx):
    await _seed_filter_contacts(dir_ctx)
    ws = dir_ctx.workspace.id
    contains = await apply_filters(
        dir_ctx.session, ws, [{"field": "email", "op": "contains", "value": "CORP"}]
    )
    assert {c.email for c in contains} == {"enterprise@corp.com", "pro@corp.com"}
    starts = await apply_filters(
        dir_ctx.session, ws, [{"field": "name", "op": "starts_with", "value": "pri"}]
    )
    assert [c.name for c in starts] == ["Priya"]
    unverified = await apply_filters(
        dir_ctx.session, ws, [{"field": "verified", "op": "eq", "value": False}]
    )
    assert len(unverified) == 3
    # AND semantics across multiple filters
    both = await apply_filters(
        dir_ctx.session,
        ws,
        [
            {"field": "email", "op": "contains", "value": "corp"},
            {"field": "attributes.plan", "op": "eq", "value": "pro"},
        ],
    )
    assert [c.email for c in both] == ["pro@corp.com"]


async def test_apply_filters_rejects_unknown_field_and_op(dir_ctx):
    with pytest.raises(ValidationFailure):
        await apply_filters(
            dir_ctx.session,
            dir_ctx.workspace.id,
            [{"field": "password", "op": "eq", "value": "x"}],
        )
    with pytest.raises(ValidationFailure):
        await apply_filters(
            dir_ctx.session,
            dir_ctx.workspace.id,
            [{"field": "email", "op": "matches", "value": "x"}],
        )


# ---------------------------------------------------------------------------
# CSAT
# ---------------------------------------------------------------------------


async def test_csat_record_is_idempotent_per_conversation(dir_ctx):
    contact, _ = await find_or_create(dir_ctx.session, dir_ctx.workspace.id, email="c@x.com")
    with capture_events("csat.submitted") as submitted:
        first = await csat_service.record_response(
            dir_ctx.session,
            dir_ctx.workspace.id,
            conversation_id="00000000-0000-7000-8000-000000000001",
            contact_id=contact.id,
            rating=5,
        )
        second = await csat_service.record_response(
            dir_ctx.session,
            dir_ctx.workspace.id,
            conversation_id="00000000-0000-7000-8000-000000000001",
            contact_id=contact.id,
            rating=2,
            feedback="changed my mind",
        )
    assert second.id == first.id  # updated in place
    assert second.rating == 2
    assert second.feedback == "changed my mind"
    assert len(submitted) == 2
    history = await csat_service.list_for_contact(dir_ctx.session, dir_ctx.workspace.id, contact.id)
    assert len(history) == 1


async def test_csat_validates_rating_and_contact(dir_ctx):
    contact, _ = await find_or_create(dir_ctx.session, dir_ctx.workspace.id, email="r@x.com")
    for bad in (0, 6):
        with pytest.raises(ValidationFailure):
            await csat_service.record_response(
                dir_ctx.session,
                dir_ctx.workspace.id,
                conversation_id="00000000-0000-7000-8000-000000000002",
                contact_id=contact.id,
                rating=bad,
            )
    with pytest.raises(NotFoundError):
        await csat_service.record_response(
            dir_ctx.session,
            dir_ctx.workspace.id,
            conversation_id="00000000-0000-7000-8000-000000000003",
            contact_id="00000000-0000-7000-8000-00000000dead",
            rating=4,
        )
