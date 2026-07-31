"""Shared schema bases."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    """Base for schemas hydrated from SQLAlchemy models."""

    model_config = ConfigDict(from_attributes=True)


class Msg(BaseModel):
    message: str


class IdOut(BaseModel):
    id: str
