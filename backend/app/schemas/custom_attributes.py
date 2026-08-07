"""Schemas for typed custom attribute definitions."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

_MODEL_PATTERN = "^(contact|conversation)$"
_TYPE_PATTERN = "^(text|number|currency|percent|link|date|list|checkbox)$"


class CustomAttributeCreate(BaseModel):
    attribute_model: str = Field(pattern=_MODEL_PATTERN)
    key: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=200)
    attribute_type: str = Field("text", pattern=_TYPE_PATTERN)
    description: str | None = Field(None, max_length=2000)
    options: list[Any] = Field(default_factory=list, max_length=200)
    default_value: Any = None
    regex_pattern: str | None = Field(None, max_length=300)
    regex_cue: str | None = Field(None, max_length=300)
    ord: int = 0
    shown_on_front: bool = True


class CustomAttributeUpdate(BaseModel):
    """`key` and `attribute_model` are immutable — changing either would orphan
    every stored value."""

    display_name: str | None = Field(None, min_length=1, max_length=200)
    attribute_type: str | None = Field(None, pattern=_TYPE_PATTERN)
    description: str | None = Field(None, max_length=2000)
    options: list[Any] | None = Field(None, max_length=200)
    default_value: Any = None
    regex_pattern: str | None = Field(None, max_length=300)
    regex_cue: str | None = Field(None, max_length=300)
    ord: int | None = None
    shown_on_front: bool | None = None


class CustomAttributeOut(ORMModel):
    id: str
    attribute_model: str
    key: str
    display_name: str
    description: str | None = None
    attribute_type: str
    options: list[Any]
    default_value: Any = None
    regex_pattern: str | None = None
    regex_cue: str | None = None
    ord: int
    shown_on_front: bool
    created_at: datetime
    updated_at: datetime
