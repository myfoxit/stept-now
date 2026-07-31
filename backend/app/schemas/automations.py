"""Automation-rule schemas (see docs/CONTRACTS.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

# Events the engine subscribes to; rules may only target these.
AutomationEvent = Literal[
    "conversation.created",
    "message.created",
    "conversation.status_changed",
    "csat.submitted",
    "contact.created",
]

ConditionOp = Literal["eq", "neq", "contains", "in", "exists"]

ActionType = Literal[
    "assign_user",
    "assign_team",
    "set_priority",
    "add_tag",
    "set_status",
    "send_reply",
    "send_note",
    "notify_member",
    "send_webhook",
]


class Condition(BaseModel):
    field: str = Field(min_length=1, max_length=100)
    op: ConditionOp
    value: Any = None


class Action(BaseModel):
    type: ActionType
    params: dict[str, Any] = Field(default_factory=dict)


class AutomationRuleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    event: AutomationEvent
    conditions: list[Condition] = Field(default_factory=list)
    actions: list[Action] = Field(min_length=1)
    enabled: bool = True
    ord: int = 0


class AutomationRuleUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    event: AutomationEvent | None = None
    conditions: list[Condition] | None = None
    actions: list[Action] | None = Field(None, min_length=1)
    enabled: bool | None = None
    ord: int | None = None


class AutomationRuleOut(ORMModel):
    id: str
    name: str
    event: str
    conditions: list[dict[str, Any]]
    actions: list[dict[str, Any]]
    enabled: bool
    ord: int
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class ReorderRequest(BaseModel):
    ordered_ids: list[str] = Field(min_length=1)
