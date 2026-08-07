"""Pydantic schemas for slas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class SlaPolicyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    first_response_minutes: int | None = Field(None, ge=1)
    next_response_minutes: int | None = Field(None, ge=1)
    resolution_minutes: int | None = Field(None, ge=1)
    only_during_business_hours: bool = False


class SlaPolicyUpdate(BaseModel):
    """Explicit null clears a threshold; the service re-validates that at least
    one threshold remains set."""

    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = Field(None, max_length=2000)
    first_response_minutes: int | None = Field(None, ge=1)
    next_response_minutes: int | None = Field(None, ge=1)
    resolution_minutes: int | None = Field(None, ge=1)
    only_during_business_hours: bool | None = None


class SlaPolicyOut(ORMModel):
    id: str
    name: str
    description: str | None = None
    first_response_minutes: int | None = None
    next_response_minutes: int | None = None
    resolution_minutes: int | None = None
    only_during_business_hours: bool = False
    created_at: datetime
    updated_at: datetime


class SlaEventOut(ORMModel):
    id: str
    event_type: str
    meta: dict[str, Any]
    created_at: datetime


class ConversationSlaOut(BaseModel):
    """The SLA state of one conversation (policy None → no SLA applied)."""

    policy: SlaPolicyOut | None = None
    status: str | None = None
    events: list[SlaEventOut] = Field(default_factory=list)


class SlaApplyRequest(BaseModel):
    """PUT body: a policy id applies (or replaces), null removes the SLA."""

    sla_policy_id: str | None = None
