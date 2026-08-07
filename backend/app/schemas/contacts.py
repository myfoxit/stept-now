"""Contact directory schemas: contacts, notes, timeline events, CSAT."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, model_validator
from pydantic_core import PydanticCustomError

from app.schemas.common import ORMModel
from app.schemas.tags import TagOut


class ContactCreate(BaseModel):
    external_id: str | None = Field(None, max_length=200)
    email: EmailStr | None = None
    name: str = Field("", max_length=200)
    phone: str | None = Field(None, max_length=50)
    avatar_url: str | None = Field(None, max_length=500)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _some_identity(self) -> ContactCreate:
        if not (self.external_id or self.email or self.name.strip()):
            # PydanticCustomError keeps error ctx JSON-serializable for the API envelope.
            raise PydanticCustomError(
                "missing_identity", "Provide at least one of external_id, email, or name"
            )
        return self


class ContactUpdate(BaseModel):
    external_id: str | None = Field(None, max_length=200)
    email: EmailStr | None = None
    name: str | None = Field(None, max_length=200)
    phone: str | None = Field(None, max_length=50)
    avatar_url: str | None = Field(None, max_length=500)
    # Replaces the whole attributes dict (admin UI edits it as one object).
    attributes: dict[str, Any] | None = None


class ContactOut(ORMModel):
    id: str
    external_id: str | None = None
    email: str | None = None
    name: str
    phone: str | None = None
    avatar_url: str | None = None
    attributes: dict[str, Any]
    verified: bool
    blocked: bool = False
    # Set when this contact was folded into another; the row is kept so old
    # links and channel source_ids still resolve.
    merged_into_id: str | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    # Hydrated by the router (the ORM model has no relationship on purpose).
    tags: list[TagOut] = Field(default_factory=list)


class ContactNoteCreate(BaseModel):
    body: str = Field(min_length=1, max_length=20_000)


class ContactNoteOut(ORMModel):
    id: str
    contact_id: str
    author_id: str | None = None
    body: str
    created_at: datetime


class ContactEventCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    meta: dict[str, Any] = Field(default_factory=dict)


class ContactEventOut(ORMModel):
    id: str
    contact_id: str
    name: str
    meta: dict[str, Any]
    created_at: datetime


class ContactTagAttach(BaseModel):
    tag_id: str


class CsatResponseOut(ORMModel):
    id: str
    conversation_id: str
    contact_id: str
    rating: int
    feedback: str | None = None
    created_at: datetime
    updated_at: datetime
