"""Tour schemas: authoring (app), delivery/telemetry (widget) and the recorder.

Trigger, audience, step, and theme shapes are validated on the way in and echoed
back typed on the way out. Audience filters reuse the segment filter DSL from
Agent A so targeting stays consistent with contact segments.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel
from app.schemas.segments import SegmentFilter

TourStatus = Literal["draft", "live", "paused"]
StepPlacement = Literal["auto", "top", "bottom", "left", "right"]
TourEventName = Literal["started", "step_viewed", "completed", "dismissed"]


# ---------------------------------------------------------------------------
# building blocks
# ---------------------------------------------------------------------------


class TourTrigger(BaseModel):
    type: Literal["manual", "url_match"] = "manual"
    url_pattern: str | None = Field(default=None, max_length=500)


class TourAudience(BaseModel):
    type: Literal["all", "filters"] = "all"
    filters: list[SegmentFilter] = Field(default_factory=list)


class TourTheme(BaseModel):
    accent: str = Field(default="#6366f1", max_length=32)


class TourStepIn(BaseModel):
    id: str | None = None
    selector: str = Field(min_length=1, max_length=500)
    title: str = Field("", max_length=200)
    body: str = ""
    placement: StepPlacement = "auto"


class TourStepOut(BaseModel):
    id: str
    selector: str
    title: str
    body: str
    placement: str


# ---------------------------------------------------------------------------
# app authoring API
# ---------------------------------------------------------------------------


class TourCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    trigger: TourTrigger = Field(default_factory=lambda: TourTrigger())
    audience: TourAudience = Field(default_factory=lambda: TourAudience())
    steps: list[TourStepIn] = Field(default_factory=list)
    theme: TourTheme = Field(default_factory=lambda: TourTheme())


class TourUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    trigger: TourTrigger | None = None
    audience: TourAudience | None = None
    steps: list[TourStepIn] | None = None
    theme: TourTheme | None = None


class TourOut(ORMModel):
    id: str
    name: str
    description: str
    status: str
    trigger: TourTrigger
    audience: TourAudience
    steps: list[TourStepOut]
    theme: TourTheme
    version: int
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


class TourStepStat(BaseModel):
    index: int
    title: str
    viewed: int
    drop_off: int


class TourStats(BaseModel):
    starts: int
    completions: int
    dismissals: int
    completion_rate: float
    steps: list[TourStepStat]


# ---------------------------------------------------------------------------
# recorder flow
# ---------------------------------------------------------------------------


class RecorderTokenOut(BaseModel):
    token: str
    expires_days: int = 7


class RecorderStepIn(BaseModel):
    selector: str = Field(min_length=1, max_length=500)
    title: str | None = Field(None, max_length=200)
    body: str | None = None


class RecorderTourIn(BaseModel):
    token: str
    name: str = Field(min_length=1, max_length=200)
    url_pattern: str | None = Field(None, max_length=500)
    steps: list[RecorderStepIn] = Field(default_factory=list)


class RecorderTourOut(BaseModel):
    id: str
    name: str
    app_url: str


# ---------------------------------------------------------------------------
# widget delivery + telemetry
# ---------------------------------------------------------------------------


class WidgetTourOut(ORMModel):
    id: str
    name: str
    steps: list[TourStepOut]
    theme: TourTheme
    version: int


class WidgetTourEventIn(BaseModel):
    event: TourEventName
    step_index: int | None = Field(None, ge=0)
