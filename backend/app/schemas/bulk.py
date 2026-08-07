"""Schemas for bulk conversation actions and contact merge/import."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ORMModel
from app.schemas.saved_views import FilterQuery


class BulkActionRequest(BaseModel):
    """Targets are either explicit ids or a filter document — never both."""

    action: str = Field(
        pattern="^(set_status|set_priority|assign_user|assign_team|add_tag|remove_tag)$"
    )
    params: dict[str, Any] = Field(default_factory=dict)
    conversation_ids: list[str] | None = Field(None, max_length=500)
    query: FilterQuery | None = None

    @model_validator(mode="after")
    def _one_target_form(self) -> BulkActionRequest:
        if bool(self.conversation_ids) == (self.query is not None):
            raise ValueError("Provide exactly one of conversation_ids or query")
        return self


class BulkActionResult(BaseModel):
    requested: int
    succeeded: int
    failed: int
    errors: list[dict[str, str]] = Field(default_factory=list)


class ContactMergeRequest(BaseModel):
    """`loser_id` is folded into the contact in the path."""

    loser_id: str


class ContactBlockRequest(BaseModel):
    blocked: bool = True


class ContactImportOut(ORMModel):
    id: str
    filename: str
    status: str
    mapping: dict[str, Any]
    total_rows: int
    processed_rows: int
    created_count: int
    updated_count: int
    failed_count: int
    errors: list[Any]
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime


class ContactImportPreview(BaseModel):
    """What the upload returns: the stored run plus what the mapper needs."""

    contact_import: ContactImportOut
    headers: list[str]
    sample_rows: list[dict[str, str]]


class ContactImportStart(BaseModel):
    """Column → target. Omit to accept the auto-detected mapping."""

    mapping: dict[str, str] | None = None
