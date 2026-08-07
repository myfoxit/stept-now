"""Schemas for saved views and the filter DSL they carry."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class FilterCondition(BaseModel):
    field: str = Field(max_length=120)
    op: str = Field(max_length=20)
    value: Any = None


class FilterQuery(BaseModel):
    match: str = Field("all", pattern="^(all|any)$")
    conditions: list[FilterCondition] = Field(default_factory=list, max_length=25)


class SavedViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field("conversation", pattern="^(conversation|contact)$")
    visibility: str = Field("personal", pattern="^(personal|shared)$")
    query: FilterQuery = Field(default_factory=lambda: FilterQuery(match="all", conditions=[]))
    icon: str | None = Field(None, max_length=20)
    ord: int = 0


class SavedViewUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    visibility: str | None = Field(None, pattern="^(personal|shared)$")
    query: FilterQuery | None = None
    icon: str | None = Field(None, max_length=20)
    ord: int | None = None


class SavedViewOut(ORMModel):
    id: str
    name: str
    kind: str
    visibility: str
    query: dict[str, Any]
    icon: str | None = None
    ord: int
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class FilterFieldOut(BaseModel):
    """One entry of the filter catalog the frontend builds its UI from."""

    field: str
    label: str
    ops: list[str]
    value_type: str  # "string" | "number" | "boolean" | "datetime" | "days" | "id" | "enum"
    options: list[dict[str, Any]] = Field(default_factory=list)


class FilterCatalogOut(BaseModel):
    fields: list[FilterFieldOut]
