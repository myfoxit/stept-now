"""Pydantic schemas for per-inbox working hours."""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from app.core.business_hours import MINUTES_PER_DAY
from app.schemas.common import ORMModel


class WorkingHourIn(BaseModel):
    day_of_week: int = Field(ge=0, le=6, description="0=Monday … 6=Sunday")
    closed_all_day: bool = False
    open_all_day: bool = False
    open_minute: int = Field(9 * 60, ge=0, le=MINUTES_PER_DAY)
    close_minute: int = Field(17 * 60, ge=0, le=MINUTES_PER_DAY)

    @model_validator(mode="after")
    def _check_window(self) -> WorkingHourIn:
        if not self.closed_all_day and not self.open_all_day:
            if self.close_minute <= self.open_minute:
                raise ValueError("close_minute must be after open_minute")
        return self


class WorkingHourOut(ORMModel):
    day_of_week: int
    closed_all_day: bool
    open_all_day: bool
    open_minute: int
    close_minute: int


class WorkingHoursUpdate(BaseModel):
    """Full weekly replacement plus the inbox-level switches."""

    days: list[WorkingHourIn] = Field(default_factory=list, max_length=7)
    enabled: bool | None = None
    timezone: str | None = Field(None, max_length=80)
    out_of_office_message: str | None = Field(None, max_length=2000)


class WorkingHoursOut(BaseModel):
    enabled: bool = False
    timezone: str = "UTC"
    out_of_office_message: str | None = None
    days: list[WorkingHourOut] = Field(default_factory=list)
    # Evaluated now, so the settings UI can show "open"/"closed" without
    # reimplementing the schedule maths in the browser.
    currently_open: bool = True
