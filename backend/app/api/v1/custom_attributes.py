"""Custom attribute definitions API.

Reading is open to anyone who can read contacts (the sidebar renders from it);
managing definitions is a workspace-configuration action.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.deps import Db, Member, Principal, require_perm
from app.core.events import Actor
from app.core.permissions import Perm
from app.schemas.common import Msg
from app.schemas.custom_attributes import (
    CustomAttributeCreate,
    CustomAttributeOut,
    CustomAttributeUpdate,
)
from app.services import custom_attributes as attrs_service

router = APIRouter()


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


@router.get(
    "/custom-attributes",
    response_model=list[CustomAttributeOut],
    dependencies=[Depends(require_perm(Perm.CONTACTS_READ))],
)
async def list_definitions(
    principal: Member, session: Db, attribute_model: str | None = Query(None)
) -> list[CustomAttributeOut]:
    rows = await attrs_service.list_definitions(
        session, principal.workspace.id, attribute_model=attribute_model
    )
    return [CustomAttributeOut.model_validate(r) for r in rows]


@router.post(
    "/custom-attributes",
    response_model=CustomAttributeOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.WORKSPACE_MANAGE))],
)
async def create_definition(
    body: CustomAttributeCreate, principal: Member, session: Db
) -> CustomAttributeOut:
    definition = await attrs_service.create_definition(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        attribute_model=body.attribute_model,
        key=body.key,
        display_name=body.display_name,
        attribute_type=body.attribute_type,
        description=body.description,
        options=body.options,
        default_value=body.default_value,
        regex_pattern=body.regex_pattern,
        regex_cue=body.regex_cue,
        ord=body.ord,
        shown_on_front=body.shown_on_front,
    )
    return CustomAttributeOut.model_validate(definition)


@router.patch(
    "/custom-attributes/{definition_id}",
    response_model=CustomAttributeOut,
    dependencies=[Depends(require_perm(Perm.WORKSPACE_MANAGE))],
)
async def update_definition(
    definition_id: str, body: CustomAttributeUpdate, principal: Member, session: Db
) -> CustomAttributeOut:
    definition = await attrs_service.update_definition(
        session,
        principal.workspace.id,
        definition_id,
        actor=_actor(principal),
        changes=body.model_dump(exclude_unset=True),
    )
    return CustomAttributeOut.model_validate(definition)


@router.delete(
    "/custom-attributes/{definition_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.WORKSPACE_MANAGE))],
)
async def delete_definition(definition_id: str, principal: Member, session: Db) -> Msg:
    await attrs_service.delete_definition(
        session, principal.workspace.id, definition_id, actor=_actor(principal)
    )
    return Msg(message="Attribute definition deleted")
