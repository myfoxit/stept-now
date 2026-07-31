"""Pydantic schemas for macros."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel

# Automation action vocabulary (conversation-scoped subset) plus remove_tag.
MacroActionType = Literal[
    "assign_user",
    "assign_team",
    "set_priority",
    "set_status",
    "add_tag",
    "remove_tag",
    "send_reply",
    "send_note",
]

MacroVisibilityType = Literal["personal", "global"]


class MacroAction(BaseModel):
    type: MacroActionType
    params: dict[str, Any] = Field(default_factory=dict)


class MacroCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    actions: list[MacroAction] = Field(min_length=1)
    visibility: MacroVisibilityType = "personal"


class MacroUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    actions: list[MacroAction] | None = Field(None, min_length=1)
    visibility: MacroVisibilityType | None = None


class MacroOut(ORMModel):
    id: str
    name: str
    actions: list[dict[str, Any]]
    visibility: str
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime


class MacroRunRequest(BaseModel):
    conversation_id: str


class MacroActionResult(BaseModel):
    action: str
    ok: bool
    error: str | None = None


class MacroRunOut(BaseModel):
    results: list[MacroActionResult]
