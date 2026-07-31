"""Pydantic schemas for campaigns."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

CampaignTypeLiteral = Literal["ongoing", "one_off"]


def _default_audience() -> dict[str, Any]:
    return {"type": "all"}


class CampaignCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1, max_length=50_000)
    campaign_type: CampaignTypeLiteral
    inbox_id: str
    sender_user_id: str | None = None
    audience: dict[str, Any] = Field(default_factory=_default_audience)
    trigger_rules: dict[str, Any] = Field(default_factory=dict)
    scheduled_at: datetime | None = None
    enabled: bool = True


class CampaignUpdate(BaseModel):
    """campaign_type and inbox_id are immutable after creation."""

    title: str | None = Field(None, min_length=1, max_length=200)
    message: str | None = Field(None, min_length=1, max_length=50_000)
    sender_user_id: str | None = None
    audience: dict[str, Any] | None = None
    trigger_rules: dict[str, Any] | None = None
    scheduled_at: datetime | None = None
    enabled: bool | None = None


class CampaignOut(ORMModel):
    id: str
    title: str
    message: str
    campaign_type: str
    status: str
    enabled: bool
    inbox_id: str
    sender_user_id: str | None = None
    audience: dict[str, Any]
    trigger_rules: dict[str, Any]
    scheduled_at: datetime | None = None
    sent_count: int
    created_at: datetime
    updated_at: datetime


class WidgetCampaignOut(BaseModel):
    """Public shape the embedded widget consumes (trigger rules are evaluated
    client-side)."""

    id: str
    message: str
    trigger_rules: dict[str, Any]
    sender_name: str


class WidgetCampaignTriggerOut(BaseModel):
    skipped: bool = False
    conversation_id: str | None = None
