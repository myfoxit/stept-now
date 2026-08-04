"""Outbound-webhook schemas (see docs/CONTRACTS.md)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

from app.core.events import EventNames
from app.core.net import UnsafeUrlError, assert_public_url

# Every declared domain event, plus the "*" wildcard.
ALL_EVENT_NAMES: frozenset[str] = frozenset(
    value
    for key, value in vars(EventNames).items()
    if not key.startswith("_") and isinstance(value, str)
)
WEBHOOK_EVENTS: frozenset[str] = ALL_EVENT_NAMES | {"*"}


def _validate_url(value: str) -> str:
    value = value.strip()
    if not value.startswith(("http://", "https://")):
        raise PydanticCustomError("invalid_url", "url must be an http(s) URL")
    # Same egress policy the delivery task enforces — rejected here so the
    # operator sees why instead of watching every delivery fail.
    try:
        # Save-time feedback only; delivery re-checks with DNS required.
        assert_public_url(value, require_resolvable=False)
    except UnsafeUrlError as exc:
        raise PydanticCustomError(
            "private_url", "url must point at a public host: {reason}", {"reason": str(exc)}
        ) from exc
    return value


def _validate_events(events: list[str]) -> list[str]:
    if not events:
        raise PydanticCustomError("empty_events", "at least one event is required")
    unknown = [e for e in events if e not in WEBHOOK_EVENTS]
    if unknown:
        raise PydanticCustomError(
            "unknown_events",
            "unknown events: {unknown}",
            {"unknown": ", ".join(sorted(set(unknown)))},
        )
    return list(dict.fromkeys(events))  # de-duplicate, preserve order


class WebhookCreate(BaseModel):
    url: str = Field(max_length=1000)
    events: list[str]
    enabled: bool = True
    description: str | None = Field(None, max_length=500)

    @field_validator("url")
    @classmethod
    def _valid_url(cls, value: str) -> str:
        return _validate_url(value)

    @field_validator("events")
    @classmethod
    def _valid_events(cls, value: list[str]) -> list[str]:
        return _validate_events(value)


class WebhookUpdate(BaseModel):
    url: str | None = Field(None, max_length=1000)
    events: list[str] | None = None
    enabled: bool | None = None
    description: str | None = Field(None, max_length=500)

    @field_validator("url")
    @classmethod
    def _valid_url(cls, value: str | None) -> str | None:
        return None if value is None else _validate_url(value)

    @field_validator("events")
    @classmethod
    def _valid_events(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _validate_events(value)


class WebhookOut(BaseModel):
    id: str
    url: str
    # The signing secret is returned so the owner can verify X-Stept-Signature.
    secret: str
    events: list[str]
    enabled: bool
    description: str | None = None
    created_at: datetime
    updated_at: datetime


class WebhookDeliveryOut(BaseModel):
    id: str
    webhook_id: str
    event_name: str
    payload: dict[str, Any]
    status: str
    response_code: int | None = None
    error: str | None = None
    attempts: int
    created_at: datetime
