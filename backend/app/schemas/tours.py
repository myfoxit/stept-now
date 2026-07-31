"""Tour schemas: authoring (app), delivery/telemetry (widget), extension + recorder.

Trigger, audience, step, schedule, frequency, settings and theme shapes are
validated on the way in and echoed back typed on the way out. Audience filters
reuse the segment filter DSL so targeting stays consistent with contact
segments. The step schema is THE shared contract between the dashboard editor,
the Chrome extension recorder, and the widget player (see docs/DAP2-CONTRACTS.md).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticCustomError

from app.schemas.common import ORMModel
from app.schemas.segments import SegmentFilter

TourStatus = Literal["draft", "live", "paused"]
TourKind = Literal["flow", "banner", "announcement"]
TourStepType = Literal["tooltip", "modal", "banner", "hotspot", "action", "wait"]
StepPlacement = Literal["auto", "top", "bottom", "left", "right", "center"]
TourEventName = Literal["started", "step_viewed", "completed", "dismissed", "step_error"]
FrequencyType = Literal["once", "until_completed", "until_dismissed", "every_time"]

MAX_TARGET_BYTES = 8 * 1024  # opaque @stept/dom-capture Target descriptor cap


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
    # Banner kind only: where the bar docks.
    position: Literal["top", "bottom"] | None = None


class TourSchedule(BaseModel):
    """Delivery window (UTC). Empty = always on."""

    start_at: datetime | None = None
    end_at: datetime | None = None


class TourFrequency(BaseModel):
    type: FrequencyType = "until_dismissed"
    cooldown_hours: int | None = Field(default=None, ge=1, le=24 * 365)


class TourSettings(BaseModel):
    mode: Literal["guided", "driven"] = "guided"
    backdrop: bool = True
    show_progress: bool = True
    dismissable: bool = True


class StepMedia(BaseModel):
    type: Literal["image", "video"]
    url: str = Field(min_length=1, max_length=2000)


class StepAdvance(BaseModel):
    on: Literal["button", "element_click", "input", "delay"] = "button"
    delay_ms: int | None = Field(default=None, ge=100, le=600_000)

    @model_validator(mode="after")
    def _delay_needs_ms(self) -> StepAdvance:
        # PydanticCustomError keeps error ctx JSON-serializable for the API envelope.
        if self.on == "delay" and self.delay_ms is None:
            raise PydanticCustomError(
                "missing_delay", "advance.delay_ms is required when advance.on is 'delay'"
            )
        return self


class StepAction(BaseModel):
    kind: Literal["click", "fill", "navigate"]
    value: str | None = Field(default=None, max_length=2000)
    url: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _kind_fields(self) -> StepAction:
        if self.kind == "fill" and not self.value:
            raise PydanticCustomError(
                "missing_value", "action.value is required for a 'fill' action"
            )
        if self.kind == "navigate" and not self.url:
            raise PydanticCustomError(
                "missing_url", "action.url is required for a 'navigate' action"
            )
        return self


class StepWait(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # "for" is a Python keyword; the wire/persisted key stays "for".
    for_: Literal["element", "url"] = Field(default="element", alias="for")
    selector: str | None = Field(default=None, max_length=500)
    url_pattern: str | None = Field(default=None, max_length=500)
    timeout_ms: int = Field(default=10_000, ge=100, le=120_000)

    @model_validator(mode="after")
    def _target_fields(self) -> StepWait:
        if self.for_ == "url" and not self.url_pattern:
            raise PydanticCustomError(
                "missing_url_pattern", "wait.url_pattern is required when waiting for a url"
            )
        return self


_SELECTOR_REQUIRED_TYPES = ("tooltip", "hotspot", "action")


class TourStepIn(BaseModel):
    id: str | None = None
    type: TourStepType = "tooltip"
    selector: str = Field(default="", max_length=500)
    fallback_selectors: list[str] = Field(default_factory=list, max_length=5)
    text_hint: str = ""
    # Full @stept/dom-capture Target descriptor — opaque passthrough, size-capped.
    target: dict[str, Any] | None = None
    title: str = Field(default="", max_length=200)
    body: str = ""  # markdown
    media: StepMedia | None = None
    screenshot_key: str | None = Field(default=None, max_length=500)
    placement: StepPlacement = "auto"
    advance: StepAdvance = Field(default_factory=StepAdvance)
    action: StepAction | None = None
    wait: StepWait | None = None

    @field_validator("text_hint")
    @classmethod
    def _clip_hint(cls, value: str) -> str:
        return value.strip()[:80]

    @field_validator("fallback_selectors")
    @classmethod
    def _clip_fallbacks(cls, value: list[str]) -> list[str]:
        for sel in value:
            if not sel or len(sel) > 500:
                raise PydanticCustomError("bad_selector", "fallback selectors must be 1..500 chars")
        return value

    @field_validator("target")
    @classmethod
    def _cap_target(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and len(json.dumps(value, default=str)) > MAX_TARGET_BYTES:
            raise PydanticCustomError(
                "target_too_large",
                "target descriptor exceeds {limit} bytes",
                {"limit": MAX_TARGET_BYTES},
            )
        return value

    @model_validator(mode="after")
    def _per_type_rules(self) -> TourStepIn:
        if self.type in _SELECTOR_REQUIRED_TYPES and not self.selector.strip():
            raise PydanticCustomError(
                "missing_selector",
                "selector is required for {step_type} steps",
                {"step_type": self.type},
            )
        if self.type == "action":
            if self.action is None:
                raise PydanticCustomError(
                    "missing_action", "action config is required for action steps"
                )
        elif self.action is not None:
            self.action = None  # ignored on other types — drop for signature stability
        if self.type == "wait":
            if self.wait is None:
                raise PydanticCustomError("missing_wait", "wait config is required for wait steps")
            if self.wait.for_ == "element" and not (
                (self.wait.selector or "").strip() or self.selector.strip()
            ):
                raise PydanticCustomError(
                    "missing_selector", "wait-for-element steps need a selector"
                )
        elif self.wait is not None:
            self.wait = None
        return self


class TourStepOut(BaseModel):
    """Echo of a stored step. Defaults keep pre-v2 rows readable."""

    id: str
    type: TourStepType = "tooltip"
    selector: str = ""
    fallback_selectors: list[str] = Field(default_factory=list)
    text_hint: str = ""
    target: dict[str, Any] | None = None
    title: str = ""
    body: str = ""
    media: StepMedia | None = None
    screenshot_key: str | None = None
    placement: str = "auto"
    advance: StepAdvance = Field(default_factory=StepAdvance)
    action: StepAction | None = None
    wait: StepWait | None = None


# ---------------------------------------------------------------------------
# app authoring API
# ---------------------------------------------------------------------------


class TourCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    kind: TourKind = "flow"
    trigger: TourTrigger = Field(default_factory=TourTrigger)
    audience: TourAudience = Field(default_factory=TourAudience)
    schedule: TourSchedule = Field(default_factory=TourSchedule)
    frequency: TourFrequency = Field(default_factory=TourFrequency)
    priority: int = Field(default=0, ge=-1000, le=1000)
    settings: TourSettings = Field(default_factory=TourSettings)
    steps: list[TourStepIn] = Field(default_factory=list)
    theme: TourTheme = Field(default_factory=TourTheme)


class TourUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    kind: TourKind | None = None
    trigger: TourTrigger | None = None
    audience: TourAudience | None = None
    schedule: TourSchedule | None = None
    frequency: TourFrequency | None = None
    priority: int | None = Field(None, ge=-1000, le=1000)
    settings: TourSettings | None = None
    steps: list[TourStepIn] | None = None
    theme: TourTheme | None = None


class TourOut(ORMModel):
    id: str
    name: str
    description: str
    kind: str
    status: str
    trigger: TourTrigger
    audience: TourAudience
    schedule: TourSchedule
    frequency: TourFrequency
    priority: int
    settings: TourSettings
    steps: list[TourStepOut]
    theme: TourTheme
    version: int
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# stats + events
# ---------------------------------------------------------------------------


class TourStepStat(BaseModel):
    index: int
    title: str
    viewed: int
    drop_off: int
    healed: int = 0


class TourDayStat(BaseModel):
    date: str  # ISO date
    starts: int
    completions: int


class TourStats(BaseModel):
    starts: int
    completions: int
    dismissals: int
    completion_rate: float
    unique_starts: int = 0
    step_errors: int = 0
    by_day: list[TourDayStat] = Field(default_factory=list)
    steps: list[TourStepStat]


class TourEventOut(ORMModel):
    id: str
    event: str
    step_index: int | None = None
    contact_id: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# ---------------------------------------------------------------------------
# recorder / extension / preview tokens
# ---------------------------------------------------------------------------


class RecorderTokenOut(BaseModel):
    token: str
    expires_days: int = 7


class ExtensionTokenOut(BaseModel):
    token: str
    expires_days: int = 30


class PreviewTokenOut(BaseModel):
    token: str
    expires_minutes: int = 60


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
# extension (dap) API
# ---------------------------------------------------------------------------


class DapTourSummary(BaseModel):
    id: str
    name: str
    kind: str
    status: str
    steps_count: int
    version: int
    updated_at: datetime


class DapTourCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url_pattern: str | None = Field(None, max_length=500)
    steps: list[TourStepIn] = Field(default_factory=list)


class DapStepsPut(BaseModel):
    steps: list[TourStepIn]
    base_version: int = Field(ge=1)


class DapTourPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    url_pattern: str | None = Field(None, max_length=500)


class DapAuthCheckOut(BaseModel):
    workspace_id: str
    workspace_name: str
    user_name: str
    perms_ok: bool = True


class ScreenshotOut(BaseModel):
    key: str


# ---------------------------------------------------------------------------
# widget delivery + telemetry
# ---------------------------------------------------------------------------


class WidgetTourOut(BaseModel):
    """Public tour payload. Never exposes audience/schedule internals; the
    player needs kind/settings plus the frequency *type* (so `every_time`
    bypasses the widget's local seen-set)."""

    id: str
    name: str
    kind: str
    steps: list[TourStepOut]
    theme: TourTheme
    version: int
    settings: TourSettings
    frequency_type: str


class WidgetTourEventIn(BaseModel):
    event: TourEventName
    step_index: int | None = Field(None, ge=0)
    meta: dict[str, Any] | None = None

    @field_validator("meta")
    @classmethod
    def _cap_meta(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None and len(json.dumps(value, default=str)) > 4096:
            raise PydanticCustomError("meta_too_large", "event meta exceeds 4KB")
        return value


class ExperiencesOut(BaseModel):
    """One-call widget bootstrap for all DAP experience kinds."""

    tours: list[WidgetTourOut] = Field(default_factory=list)
    checklists: list[dict[str, Any]] = Field(default_factory=list)
    surveys: list[dict[str, Any]] = Field(default_factory=list)
