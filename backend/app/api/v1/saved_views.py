"""Saved views API + the filter-field catalog the builder UI renders from.

Reading needs conversations:read. Creating/editing a *personal* view needs only
that too — it's the user's own bookmark. Publishing or editing a **shared** view
needs conversations:manage, so a viewer can't rewrite the team's queues.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.deps import Db, Member, Principal, require_perm
from app.core.errors import ForbiddenError
from app.core.events import Actor
from app.core.permissions import Perm
from app.models.conversation import ConversationPriority, ConversationStatus
from app.models.saved_view import ViewVisibility
from app.schemas.common import Msg
from app.schemas.saved_views import (
    FilterCatalogOut,
    FilterFieldOut,
    SavedViewCreate,
    SavedViewOut,
    SavedViewUpdate,
)
from app.services import custom_attributes as attrs_service
from app.services import filters
from app.services import saved_views as views_service

router = APIRouter()

# Human labels + value shapes for the fields in app.services.filters.FIELD_OPS.
_FIELD_META: dict[str, tuple[str, str]] = {
    "status": ("Status", "enum"),
    "priority": ("Priority", "enum"),
    "inbox_id": ("Inbox", "id"),
    "team_id": ("Team", "id"),
    "assignee_user_id": ("Assignee", "id"),
    "ai_agent_id": ("Handled by AI", "boolean"),
    "contact_id": ("Contact", "id"),
    "subject": ("Subject", "string"),
    "tag_id": ("Tag", "id"),
    "participant_user_id": ("Participant", "id"),
    "contact_email": ("Contact email", "string"),
    "contact_name": ("Contact name", "string"),
    "created_at": ("Created", "datetime"),
    "last_activity_at": ("Last activity", "datetime"),
    "resolved_at": ("Resolved", "datetime"),
    "first_reply_at": ("First reply", "datetime"),
    "waiting_since": ("Waiting since", "datetime"),
    "snoozed_until": ("Snoozed until", "datetime"),
}

_ENUM_OPTIONS: dict[str, list[dict[str, str]]] = {
    "status": [{"value": s.value, "label": s.value.title()} for s in ConversationStatus],
    "priority": [{"value": p.value, "label": p.value.title()} for p in ConversationPriority],
}


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


def _user_id(principal: Principal) -> str | None:
    return principal.user.id if principal.user is not None else None


def _guard_shared(principal: Principal, visibility: str | None) -> None:
    if visibility == ViewVisibility.SHARED.value and not principal.has(Perm.CONVERSATIONS_MANAGE):
        raise ForbiddenError("Sharing a view requires permission conversations:manage")


@router.get(
    "/views/catalog",
    response_model=FilterCatalogOut,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def filter_catalog(principal: Member, session: Db) -> FilterCatalogOut:
    """Every filterable field with its allowed operators — including the
    workspace's own conversation attributes, so the builder needs no hardcoding."""
    fields = [
        FilterFieldOut(
            field=field,
            label=_FIELD_META.get(field, (field, "string"))[0],
            ops=list(ops),
            value_type=_FIELD_META.get(field, (field, "string"))[1],
            options=_ENUM_OPTIONS.get(field, []),
        )
        for field, ops in filters.FIELD_OPS.items()
    ]
    definitions = await attrs_service.definitions_for(
        session, principal.workspace.id, "conversation"
    )
    for definition in definitions:
        fields.append(
            FilterFieldOut(
                field=f"attributes.{definition.key}",
                label=definition.display_name,
                ops=list(
                    attrs_service.TYPE_OPERATORS.get(
                        definition.attribute_type, ("eq", "neq", "exists", "not_exists")
                    )
                ),
                value_type=definition.attribute_type,
                options=[{"value": str(o), "label": str(o)} for o in definition.options or []],
            )
        )
    return FilterCatalogOut(fields=fields)


@router.get(
    "/views",
    response_model=list[SavedViewOut],
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def list_views(
    principal: Member, session: Db, kind: str | None = Query(None)
) -> list[SavedViewOut]:
    views = await views_service.list_views(
        session, principal.workspace.id, user_id=_user_id(principal), kind=kind
    )
    return [SavedViewOut.model_validate(v) for v in views]


@router.post(
    "/views",
    response_model=SavedViewOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def create_view(body: SavedViewCreate, principal: Member, session: Db) -> SavedViewOut:
    _guard_shared(principal, body.visibility)
    view = await views_service.create_view(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        user_id=_user_id(principal),
        name=body.name,
        kind=body.kind,
        visibility=body.visibility,
        query=body.query.model_dump(),
        icon=body.icon,
        ord=body.ord,
    )
    return SavedViewOut.model_validate(view)


@router.patch(
    "/views/{view_id}",
    response_model=SavedViewOut,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def update_view(
    view_id: str, body: SavedViewUpdate, principal: Member, session: Db
) -> SavedViewOut:
    changes = body.model_dump(exclude_unset=True)
    if "query" in changes and body.query is not None:
        changes["query"] = body.query.model_dump()
    _guard_shared(principal, changes.get("visibility"))
    existing = await views_service.get_view(
        session, principal.workspace.id, view_id, user_id=_user_id(principal)
    )
    _guard_shared(principal, existing.visibility)  # editing a shared view needs it too
    view = await views_service.update_view(
        session,
        principal.workspace.id,
        view_id,
        actor=_actor(principal),
        user_id=_user_id(principal),
        changes=changes,
    )
    return SavedViewOut.model_validate(view)


@router.delete(
    "/views/{view_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.CONVERSATIONS_READ))],
)
async def delete_view(view_id: str, principal: Member, session: Db) -> Msg:
    existing = await views_service.get_view(
        session, principal.workspace.id, view_id, user_id=_user_id(principal)
    )
    _guard_shared(principal, existing.visibility)
    await views_service.delete_view(
        session,
        principal.workspace.id,
        view_id,
        actor=_actor(principal),
        user_id=_user_id(principal),
    )
    return Msg(message="View deleted")
