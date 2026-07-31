"""Automations API: CRUD + toggle + reorder for event-triggered rules.

The empty router is pre-registered; add routes here, never touch the registry.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

# Side-effect import: registers the engine's @on(...) rule handlers and the
# webhook fan-out subscriber when the app (or a test app) is built. MUST stay.
from app.automation import engine as _automation_engine  # noqa: F401
from app.core.deps import Db, Member, Principal, require_perm
from app.core.errors import NotFoundError, ValidationFailure
from app.core.events import Actor
from app.core.permissions import Perm
from app.models.automation import AutomationRule
from app.schemas.automations import (
    AutomationRuleCreate,
    AutomationRuleOut,
    AutomationRuleUpdate,
    ReorderRequest,
)
from app.schemas.common import Msg
from app.services import audit

router = APIRouter()


def _actor(principal: Principal) -> Actor:
    return Actor(type=principal.kind, id=principal.actor_id, label=principal.label)


def _out(rule: AutomationRule) -> AutomationRuleOut:
    return AutomationRuleOut.model_validate(rule)


async def _get_rule(session: Db, workspace_id: str, rule_id: str) -> AutomationRule:
    rule = await session.get(AutomationRule, rule_id)
    if rule is None or rule.workspace_id != workspace_id:
        raise NotFoundError("Automation rule not found")
    return rule


@router.get(
    "/automations",
    response_model=list[AutomationRuleOut],
    dependencies=[Depends(require_perm(Perm.AUTOMATIONS_READ))],
)
async def list_automations(principal: Member, session: Db) -> list[AutomationRuleOut]:
    rows = (
        await session.execute(
            select(AutomationRule)
            .where(AutomationRule.workspace_id == principal.workspace.id)
            .order_by(AutomationRule.ord, AutomationRule.created_at, AutomationRule.id)
        )
    ).scalars()
    return [_out(rule) for rule in rows]


@router.post(
    "/automations",
    response_model=AutomationRuleOut,
    status_code=201,
    dependencies=[Depends(require_perm(Perm.AUTOMATIONS_MANAGE))],
)
async def create_automation(
    body: AutomationRuleCreate, principal: Member, session: Db
) -> AutomationRuleOut:
    rule = AutomationRule(
        workspace_id=principal.workspace.id,
        name=body.name,
        event=body.event,
        conditions=[c.model_dump() for c in body.conditions],
        actions=[a.model_dump() for a in body.actions],
        enabled=body.enabled,
        ord=body.ord,
        created_by=principal.user.id if principal.user is not None else None,
    )
    session.add(rule)
    await session.flush()
    await audit.record(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        action="automation.create",
        target_type="automation_rule",
        target_id=rule.id,
        meta={"name": rule.name, "event": rule.event},
    )
    return _out(rule)


@router.get(
    "/automations/{rule_id}",
    response_model=AutomationRuleOut,
    dependencies=[Depends(require_perm(Perm.AUTOMATIONS_READ))],
)
async def get_automation(rule_id: str, principal: Member, session: Db) -> AutomationRuleOut:
    return _out(await _get_rule(session, principal.workspace.id, rule_id))


@router.patch(
    "/automations/{rule_id}",
    response_model=AutomationRuleOut,
    dependencies=[Depends(require_perm(Perm.AUTOMATIONS_MANAGE))],
)
async def update_automation(
    rule_id: str, body: AutomationRuleUpdate, principal: Member, session: Db
) -> AutomationRuleOut:
    rule = await _get_rule(session, principal.workspace.id, rule_id)
    if body.name is not None:
        rule.name = body.name
    if body.event is not None:
        rule.event = body.event
    if body.conditions is not None:
        rule.conditions = [c.model_dump() for c in body.conditions]
    if body.actions is not None:
        rule.actions = [a.model_dump() for a in body.actions]
    if body.enabled is not None:
        rule.enabled = body.enabled
    if body.ord is not None:
        rule.ord = body.ord
    await session.flush()
    await audit.record(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        action="automation.update",
        target_type="automation_rule",
        target_id=rule.id,
        meta={"name": rule.name, "event": rule.event, "enabled": rule.enabled},
    )
    return _out(rule)


@router.delete(
    "/automations/{rule_id}",
    response_model=Msg,
    dependencies=[Depends(require_perm(Perm.AUTOMATIONS_MANAGE))],
)
async def delete_automation(rule_id: str, principal: Member, session: Db) -> Msg:
    rule = await _get_rule(session, principal.workspace.id, rule_id)
    await session.delete(rule)
    await session.flush()
    await audit.record(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        action="automation.delete",
        target_type="automation_rule",
        target_id=rule_id,
    )
    return Msg(message="Automation rule deleted")


@router.post(
    "/automations/{rule_id}/toggle",
    response_model=AutomationRuleOut,
    dependencies=[Depends(require_perm(Perm.AUTOMATIONS_MANAGE))],
)
async def toggle_automation(rule_id: str, principal: Member, session: Db) -> AutomationRuleOut:
    rule = await _get_rule(session, principal.workspace.id, rule_id)
    rule.enabled = not rule.enabled
    await session.flush()
    await audit.record(
        session,
        principal.workspace.id,
        actor=_actor(principal),
        action="automation.toggle",
        target_type="automation_rule",
        target_id=rule.id,
        meta={"enabled": rule.enabled},
    )
    return _out(rule)


@router.post(
    "/automations/reorder",
    response_model=list[AutomationRuleOut],
    dependencies=[Depends(require_perm(Perm.AUTOMATIONS_MANAGE))],
)
async def reorder_automations(
    body: ReorderRequest, principal: Member, session: Db
) -> list[AutomationRuleOut]:
    rows = (
        await session.execute(
            select(AutomationRule).where(AutomationRule.workspace_id == principal.workspace.id)
        )
    ).scalars()
    rules = {rule.id: rule for rule in rows}
    unknown = [rid for rid in body.ordered_ids if rid not in rules]
    if unknown:
        raise ValidationFailure(f"Unknown rule ids: {', '.join(unknown)}")
    for index, rule_id in enumerate(body.ordered_ids):
        rules[rule_id].ord = index
    await session.flush()
    ordered = sorted(rules.values(), key=lambda r: (r.ord, r.created_at, r.id))
    return [_out(rule) for rule in ordered]
