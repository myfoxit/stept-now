"""Checklist schemas: authoring (app), stats, and widget delivery/progress.

Trigger/audience mirror the tour shapes (audience filters reuse the segment
filter DSL) so DAP targeting stays one vocabulary. Item `action` and
`completion` are tagged unions validated on the way in and echoed back typed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticCustomError

from app.schemas.common import ORMModel
from app.schemas.segments import SegmentFilter

ChecklistStatus = Literal["draft", "live", "paused"]
ChecklistPosition = Literal["bottom-right", "bottom-left"]

MAX_ITEMS = 20


# ---------------------------------------------------------------------------
# building blocks
# ---------------------------------------------------------------------------


class ChecklistTrigger(BaseModel):
    """Where the launcher shows. Defaults to every page (`*`)."""

    type: Literal["manual", "url_match"] = "url_match"
    url_pattern: str | None = Field(default="*", max_length=500)


class ChecklistAudience(BaseModel):
    type: Literal["all", "filters"] = "all"
    filters: list[SegmentFilter] = Field(default_factory=list)


class ChecklistTheme(BaseModel):
    accent: str = Field(default="#6366f1", max_length=32)
    position: ChecklistPosition = "bottom-right"


class ChecklistLauncher(BaseModel):
    label: str = Field(default="Getting started", max_length=60)
    auto_open_once: bool = True


class ChecklistItemAction(BaseModel):
    """What the item's CTA does when clicked."""

    type: Literal["start_tour", "open_url", "open_messenger", "none"] = "none"
    tour_id: str | None = None
    url: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _required_fields(self) -> ChecklistItemAction:
        if self.type == "start_tour" and not self.tour_id:
            raise PydanticCustomError(
                "action_tour_id_required", "action.tour_id is required for start_tour"
            )
        if self.type == "open_url" and not self.url:
            raise PydanticCustomError("action_url_required", "action.url is required for open_url")
        return self


class ChecklistItemCompletion(BaseModel):
    """How the item gets checked off."""

    type: Literal["manual", "tour_completed", "url_visited"] = "manual"
    tour_id: str | None = None
    url_pattern: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _required_fields(self) -> ChecklistItemCompletion:
        if self.type == "tour_completed" and not self.tour_id:
            raise PydanticCustomError(
                "completion_tour_id_required", "completion.tour_id is required for tour_completed"
            )
        if self.type == "url_visited" and not self.url_pattern:
            raise PydanticCustomError(
                "completion_url_pattern_required",
                "completion.url_pattern is required for url_visited",
            )
        return self


class ChecklistItemIn(BaseModel):
    id: str | None = None
    title: str = Field(min_length=1, max_length=200)
    body: str = ""  # markdown
    action: ChecklistItemAction = Field(default_factory=lambda: ChecklistItemAction())
    completion: ChecklistItemCompletion = Field(default_factory=lambda: ChecklistItemCompletion())


class ChecklistItemOut(BaseModel):
    id: str
    title: str
    body: str
    action: ChecklistItemAction
    completion: ChecklistItemCompletion


# ---------------------------------------------------------------------------
# app authoring API
# ---------------------------------------------------------------------------


class ChecklistCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    items: list[ChecklistItemIn] = Field(default_factory=list, max_length=MAX_ITEMS)
    trigger: ChecklistTrigger = Field(default_factory=lambda: ChecklistTrigger())
    audience: ChecklistAudience = Field(default_factory=lambda: ChecklistAudience())
    theme: ChecklistTheme = Field(default_factory=lambda: ChecklistTheme())
    launcher: ChecklistLauncher = Field(default_factory=lambda: ChecklistLauncher())
    priority: int = Field(default=0, ge=-100, le=100)


class ChecklistUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    items: list[ChecklistItemIn] | None = Field(default=None, max_length=MAX_ITEMS)
    trigger: ChecklistTrigger | None = None
    audience: ChecklistAudience | None = None
    theme: ChecklistTheme | None = None
    launcher: ChecklistLauncher | None = None
    priority: int | None = Field(default=None, ge=-100, le=100)


class ChecklistOut(ORMModel):
    id: str
    name: str
    description: str
    status: str
    items: list[ChecklistItemOut]
    trigger: ChecklistTrigger
    audience: ChecklistAudience
    theme: ChecklistTheme
    launcher: ChecklistLauncher
    priority: int
    version: int
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


class ChecklistItemStat(BaseModel):
    id: str
    title: str
    completed_count: int


class ChecklistStats(BaseModel):
    # Reserved: the widget does not report impressions yet (always null).
    views: int | None = None
    starts: int  # progress rows
    completions: int
    completion_rate: float
    items: list[ChecklistItemStat]


# ---------------------------------------------------------------------------
# widget delivery + progress
# ---------------------------------------------------------------------------


class WidgetChecklistProgress(BaseModel):
    item_state: dict[str, str] = Field(default_factory=dict)
    dismissed: bool = False
    completed: bool = False


class WidgetChecklistOut(BaseModel):
    """Public projection — never exposes trigger/audience/priority internals."""

    id: str
    name: str
    description: str
    items: list[ChecklistItemOut]
    theme: ChecklistTheme
    launcher: ChecklistLauncher
    version: int
    progress: WidgetChecklistProgress = Field(default_factory=lambda: WidgetChecklistProgress())


class WidgetChecklistProgressIn(BaseModel):
    item_id: str = Field(min_length=1, max_length=64)
    done: bool = True


class WidgetChecklistProgressOut(BaseModel):
    """`stored=False` for anonymous visitors — the widget keeps local state."""

    stored: bool
    item_state: dict[str, str] = Field(default_factory=dict)
    dismissed: bool = False
    completed: bool = False


class WidgetAckOut(BaseModel):
    ok: bool = True
    stored: bool = True
