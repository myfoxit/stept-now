"""Survey schemas: authoring (app), results/analytics, and widget delivery.

Targeting (trigger / audience / schedule / frequency / priority) mirrors tours v2
so one editor vocabulary covers every DAP experience. Questions are a tagged
union (`nps` 0-10, `rating` 1-5, `text`, `select` with 2..6 options); answer
*values* are validated server-side against the question set on submit.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_core import PydanticCustomError

from app.schemas.common import ORMModel
from app.schemas.segments import SegmentFilter

SurveyStatus = Literal["draft", "live", "paused"]
SurveyPresentation = Literal["modal", "slideout"]
QuestionType = Literal["nps", "rating", "text", "select"]
FrequencyType = Literal["once", "until_completed", "until_dismissed", "every_time"]

MAX_QUESTIONS = 10
MIN_OPTIONS = 2
MAX_OPTIONS = 6
NPS_MIN, NPS_MAX = 0, 10
RATING_MIN, RATING_MAX = 1, 5


# ---------------------------------------------------------------------------
# building blocks
# ---------------------------------------------------------------------------


class SurveyTrigger(BaseModel):
    type: Literal["manual", "url_match"] = "url_match"
    url_pattern: str | None = Field(default="*", max_length=500)


class SurveyAudience(BaseModel):
    type: Literal["all", "filters"] = "all"
    filters: list[SegmentFilter] = Field(default_factory=list)


class SurveySchedule(BaseModel):
    """UTC window; both ends optional (empty schedule = always on)."""

    start_at: datetime | None = None
    end_at: datetime | None = None


class SurveyFrequency(BaseModel):
    type: FrequencyType = "once"
    cooldown_hours: int | None = Field(default=None, ge=0, le=8760)


class SurveyTheme(BaseModel):
    accent: str = Field(default="#6366f1", max_length=32)


class SurveyQuestionIn(BaseModel):
    id: str | None = None
    type: QuestionType = "text"
    question: str = Field(min_length=1, max_length=300)
    required: bool = True
    options: list[str] | None = None  # select only

    @model_validator(mode="after")
    def _check_options(self) -> SurveyQuestionIn:
        if self.type == "select":
            options = self.options or []
            if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
                raise PydanticCustomError(
                    "select_options_count",
                    "select questions need between {min} and {max} options",
                    {"min": MIN_OPTIONS, "max": MAX_OPTIONS},
                )
            if any(not option.strip() for option in options):
                raise PydanticCustomError("select_option_empty", "select options cannot be blank")
            if len({option for option in options}) != len(options):
                raise PydanticCustomError(
                    "select_option_duplicate", "select options must be unique"
                )
        elif self.options:
            raise PydanticCustomError(
                "options_not_allowed", "options are only valid on select questions"
            )
        return self


class SurveyQuestionOut(BaseModel):
    id: str
    type: QuestionType
    question: str
    required: bool
    options: list[str] | None = None


# ---------------------------------------------------------------------------
# app authoring API
# ---------------------------------------------------------------------------


class SurveyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    questions: list[SurveyQuestionIn] = Field(default_factory=list, max_length=MAX_QUESTIONS)
    presentation: SurveyPresentation = "slideout"
    trigger: SurveyTrigger = Field(default_factory=lambda: SurveyTrigger())
    audience: SurveyAudience = Field(default_factory=lambda: SurveyAudience())
    schedule: SurveySchedule = Field(default_factory=lambda: SurveySchedule())
    frequency: SurveyFrequency = Field(default_factory=lambda: SurveyFrequency())
    priority: int = Field(default=0, ge=-100, le=100)
    theme: SurveyTheme = Field(default_factory=lambda: SurveyTheme())
    thanks_message: str = Field(default="Thanks for the feedback!", max_length=2000)


class SurveyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    questions: list[SurveyQuestionIn] | None = Field(default=None, max_length=MAX_QUESTIONS)
    presentation: SurveyPresentation | None = None
    trigger: SurveyTrigger | None = None
    audience: SurveyAudience | None = None
    schedule: SurveySchedule | None = None
    frequency: SurveyFrequency | None = None
    priority: int | None = Field(default=None, ge=-100, le=100)
    theme: SurveyTheme | None = None
    thanks_message: str | None = Field(default=None, max_length=2000)


class SurveyOut(ORMModel):
    id: str
    name: str
    status: str
    questions: list[SurveyQuestionOut]
    presentation: str
    trigger: SurveyTrigger
    audience: SurveyAudience
    schedule: SurveySchedule
    frequency: SurveyFrequency
    priority: int
    theme: SurveyTheme
    thanks_message: str
    version: int
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class SurveyAnswer(BaseModel):
    question_id: str = Field(min_length=1, max_length=64)
    value: int | str


class SurveyResponseOut(ORMModel):
    id: str
    survey_id: str
    contact_id: str | None = None
    answers: list[SurveyAnswer]
    completed: bool
    meta: dict[str, str] = Field(default_factory=dict)
    created_at: datetime


# ---------------------------------------------------------------------------
# results
# ---------------------------------------------------------------------------


class SurveyDayPoint(BaseModel):
    date: str  # YYYY-MM-DD (UTC)
    responses: int


class SurveyNpsResult(BaseModel):
    score: int  # %promoters − %detractors, rounded
    promoters: int
    passives: int
    detractors: int


class SurveyRatingResult(BaseModel):
    avg: float
    distribution: dict[str, int]  # "1".."5"


class SurveySelectResult(BaseModel):
    question_id: str
    question: str
    counts: dict[str, int]


class SurveyTextAnswer(BaseModel):
    question_id: str
    value: str
    contact_id: str | None = None
    created_at: datetime


class SurveyResults(BaseModel):
    responses: int
    completed: int
    completion_rate: float
    by_day: list[SurveyDayPoint]
    nps: SurveyNpsResult | None = None
    ratings: SurveyRatingResult | None = None
    select: list[SurveySelectResult] = Field(default_factory=list)
    text_answers: list[SurveyTextAnswer] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# widget delivery + submission
# ---------------------------------------------------------------------------


class WidgetSurveyOut(BaseModel):
    """Public projection — never exposes trigger/audience/schedule internals.

    `frequency_type` ships so the widget knows when to bypass its local seen-set
    (`every_time`), exactly like tours v2.
    """

    id: str
    name: str
    questions: list[SurveyQuestionOut]
    presentation: str
    theme: SurveyTheme
    thanks_message: str
    version: int
    frequency_type: str


class WidgetSurveyResponseIn(BaseModel):
    answers: list[SurveyAnswer] = Field(default_factory=list, max_length=MAX_QUESTIONS)
    completed: bool = True


class WidgetSurveyAckOut(BaseModel):
    ok: bool = True
    thanks_message: str = ""
