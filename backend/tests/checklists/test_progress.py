"""Service layer: progress lifecycle, dismissal, the tour-completed seam, and
widget delivery (targeting, exclusion, ordering)."""

from __future__ import annotations

import pytest

from app.core.errors import ValidationFailure
from app.core.events import Actor
from app.services import checklists as service
from tests.checklists.conftest import make_contact

INBOX_URL = "https://app.example.com/inbox?tab=open"

ITEMS = [
    {"id": "one", "title": "One"},
    {"id": "two", "title": "Two", "completion": {"type": "tour_completed", "tour_id": "tour-1"}},
    {"id": "three", "title": "Three"},
]


async def _live_checklist(env, *, name="Checklist", items=None, **overrides):
    actor = Actor(type="user", id=env.ctx.owner.id, label=env.ctx.owner.name)
    checklist = await service.create_checklist(
        env.session,
        env.workspace_id,
        actor=actor,
        name=name,
        items=items if items is not None else ITEMS,
        **overrides,
    )
    return await service.publish_checklist(env.session, env.workspace_id, checklist.id, actor=actor)


async def test_progress_lifecycle_sets_and_clears_completed_at(dap_env):
    checklist = await _live_checklist(dap_env)
    contact = await make_contact(dap_env.session, dap_env.workspace_id)

    progress = await service.record_checklist_progress(
        dap_env.session, dap_env.workspace_id, checklist, contact.id, item_id="one", done=True
    )
    assert set(progress.item_state) == {"one"}
    assert progress.completed_at is None

    await service.record_checklist_progress(
        dap_env.session, dap_env.workspace_id, checklist, contact.id, item_id="two", done=True
    )
    progress = await service.record_checklist_progress(
        dap_env.session, dap_env.workspace_id, checklist, contact.id, item_id="three", done=True
    )
    assert set(progress.item_state) == {"one", "two", "three"}
    assert progress.completed_at is not None

    # Unchecking an item re-opens the checklist.
    progress = await service.record_checklist_progress(
        dap_env.session, dap_env.workspace_id, checklist, contact.id, item_id="two", done=False
    )
    assert set(progress.item_state) == {"one", "three"}
    assert progress.completed_at is None

    # One row per (checklist, contact).
    assert (
        await service.get_progress(dap_env.session, dap_env.workspace_id, checklist.id, contact.id)
    ).id == progress.id


async def test_progress_rejects_unknown_item(dap_env):
    checklist = await _live_checklist(dap_env)
    contact = await make_contact(dap_env.session, dap_env.workspace_id)
    with pytest.raises(ValidationFailure):
        await service.record_checklist_progress(
            dap_env.session,
            dap_env.workspace_id,
            checklist,
            contact.id,
            item_id="nope",
            done=True,
        )


async def test_mark_tour_completed_auto_completes_matching_items(dap_env):
    checklist = await _live_checklist(dap_env)
    other = await _live_checklist(
        dap_env,
        name="Other",
        items=[
            {
                "id": "x",
                "title": "X",
                "completion": {"type": "tour_completed", "tour_id": "tour-2"},
            }
        ],
    )
    contact = await make_contact(dap_env.session, dap_env.workspace_id)

    await service.mark_tour_completed(dap_env.session, dap_env.workspace_id, contact.id, "tour-1")

    progress = await service.get_progress(
        dap_env.session, dap_env.workspace_id, checklist.id, contact.id
    )
    assert progress is not None
    assert set(progress.item_state) == {"two"}
    assert progress.completed_at is None
    # A checklist keyed to a different tour is untouched.
    assert (
        await service.get_progress(dap_env.session, dap_env.workspace_id, other.id, contact.id)
    ) is None

    # Anonymous visitors are a no-op (handled client-side).
    await service.mark_tour_completed(dap_env.session, dap_env.workspace_id, None, "tour-1")


async def test_mark_tour_completed_skips_draft_checklists(dap_env):
    actor = Actor(type="user", id=dap_env.ctx.owner.id)
    draft = await service.create_checklist(
        dap_env.session, dap_env.workspace_id, actor=actor, name="Draft", items=ITEMS
    )
    contact = await make_contact(dap_env.session, dap_env.workspace_id)
    await service.mark_tour_completed(dap_env.session, dap_env.workspace_id, contact.id, "tour-1")
    assert (
        await service.get_progress(dap_env.session, dap_env.workspace_id, draft.id, contact.id)
    ) is None


async def test_delivery_targets_url_and_excludes_finished(dap_env):
    checklist = await _live_checklist(
        dap_env, trigger={"type": "url_match", "url_pattern": "*/inbox*"}
    )
    contact = await make_contact(dap_env.session, dap_env.workspace_id)

    delivered = await service.deliverable_checklists(
        dap_env.session, dap_env.workspace_id, url=INBOX_URL, contact=contact
    )
    assert [c["id"] for c in delivered] == [checklist.id]
    assert delivered[0]["progress"] == {"item_state": {}, "dismissed": False, "completed": False}
    # Public projection stays lean.
    assert set(delivered[0]) == {
        "id",
        "name",
        "description",
        "items",
        "theme",
        "launcher",
        "version",
        "progress",
    }

    other_url = await service.deliverable_checklists(
        dap_env.session,
        dap_env.workspace_id,
        url="https://app.example.com/settings",
        contact=contact,
    )
    assert other_url == []

    # Partially done → still delivered, with progress attached.
    await service.record_checklist_progress(
        dap_env.session, dap_env.workspace_id, checklist, contact.id, item_id="one", done=True
    )
    partial = await service.deliverable_checklists(
        dap_env.session, dap_env.workspace_id, url=INBOX_URL, contact=contact
    )
    assert list(partial[0]["progress"]["item_state"]) == ["one"]

    # Completing it removes it from delivery.
    for item_id in ("two", "three"):
        await service.record_checklist_progress(
            dap_env.session,
            dap_env.workspace_id,
            checklist,
            contact.id,
            item_id=item_id,
            done=True,
        )
    assert (
        await service.deliverable_checklists(
            dap_env.session, dap_env.workspace_id, url=INBOX_URL, contact=contact
        )
        == []
    )
    # …but a different visitor still sees it.
    stranger = await make_contact(dap_env.session, dap_env.workspace_id, name="Stranger")
    assert (
        len(
            await service.deliverable_checklists(
                dap_env.session, dap_env.workspace_id, url=INBOX_URL, contact=stranger
            )
        )
        == 1
    )


async def test_dismiss_excludes_from_delivery(dap_env):
    checklist = await _live_checklist(dap_env)
    contact = await make_contact(dap_env.session, dap_env.workspace_id)

    progress = await service.dismiss_checklist(
        dap_env.session, dap_env.workspace_id, checklist, contact.id
    )
    assert progress.dismissed_at is not None
    assert (
        await service.deliverable_checklists(
            dap_env.session, dap_env.workspace_id, url=INBOX_URL, contact=contact
        )
        == []
    )
    # Anonymous visitors are unaffected by another contact's dismissal.
    anon = await service.deliverable_checklists(
        dap_env.session, dap_env.workspace_id, url=INBOX_URL, contact=None
    )
    assert [c["id"] for c in anon] == [checklist.id]


async def test_delivery_audience_priority_and_status(dap_env):
    actor = Actor(type="user", id=dap_env.ctx.owner.id)
    low = await _live_checklist(dap_env, name="Low", priority=0)
    high = await _live_checklist(dap_env, name="High", priority=5)
    await _live_checklist(
        dap_env,
        name="Enterprise only",
        audience={
            "type": "filters",
            "filters": [{"field": "attributes.plan", "op": "eq", "value": "enterprise"}],
        },
    )
    await service.create_checklist(
        dap_env.session, dap_env.workspace_id, actor=actor, name="Draft", items=ITEMS
    )
    manual = await _live_checklist(dap_env, name="Manual", trigger={"type": "manual"})

    free = await make_contact(dap_env.session, dap_env.workspace_id, attributes={"plan": "free"})
    delivered = await service.deliverable_checklists(
        dap_env.session, dap_env.workspace_id, url=INBOX_URL, contact=free
    )
    # priority desc, drafts + manual triggers + non-matching audiences excluded.
    assert [c["id"] for c in delivered] == [high.id, low.id]
    assert manual.id not in {c["id"] for c in delivered}

    ent = await make_contact(
        dap_env.session, dap_env.workspace_id, name="Ent", attributes={"plan": "enterprise"}
    )
    ent_names = {
        c["name"]
        for c in await service.deliverable_checklists(
            dap_env.session, dap_env.workspace_id, url=INBOX_URL, contact=ent
        )
    }
    assert "Enterprise only" in ent_names

    # Anonymous visitors never match a filters audience.
    anon_names = {
        c["name"]
        for c in await service.deliverable_checklists(
            dap_env.session, dap_env.workspace_id, url=INBOX_URL, contact=None
        )
    }
    assert "Enterprise only" not in anon_names


async def test_delivery_is_workspace_scoped(dap_env):
    from app.models.workspace import Workspace

    checklist = await _live_checklist(dap_env)
    other = Workspace(name="Other", slug="other-cl-ws")
    dap_env.session.add(other)
    await dap_env.session.flush()

    assert (
        await service.deliverable_checklists(dap_env.session, other.id, url=INBOX_URL, contact=None)
        == []
    )
    assert checklist.workspace_id == dap_env.workspace_id
