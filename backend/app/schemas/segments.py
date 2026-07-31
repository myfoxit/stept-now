"""Segment schemas, including the filter DSL."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

from app.schemas.common import ORMModel

CORE_FILTER_FIELDS = {"email", "name", "external_id", "last_seen_at", "created_at", "verified"}

FilterOp = Literal["eq", "neq", "contains", "starts_with", "exists", "not_exists", "gt", "lt"]


class SegmentFilter(BaseModel):
    """One condition; a segment's filters are ANDed together."""

    field: str = Field(min_length=1, max_length=200)
    op: FilterOp
    value: Any = None

    @field_validator("field")
    @classmethod
    def _known_field(cls, v: str) -> str:
        if v in CORE_FILTER_FIELDS:
            return v
        if v.startswith("attributes.") and len(v) > len("attributes."):
            return v
        # PydanticCustomError keeps error ctx JSON-serializable for the API envelope.
        raise PydanticCustomError(
            "unknown_filter_field",
            "field must be one of {allowed} or 'attributes.<key>'",
            {"allowed": ", ".join(sorted(CORE_FILTER_FIELDS))},
        )


class SegmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    filters: list[SegmentFilter] = Field(default_factory=list)


class SegmentUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    filters: list[SegmentFilter] | None = None


class SegmentOut(ORMModel):
    id: str
    name: str
    filters: list[SegmentFilter]
    created_by: str | None = None
    created_at: datetime
