"""Audit + notification schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.schemas.common import ORMModel


class AuditLogOut(ORMModel):
    id: str
    actor_type: str
    actor_id: str | None = None
    actor_label: str | None = None
    action: str
    target_type: str | None = None
    target_id: str | None = None
    meta: dict[str, Any]
    created_at: datetime


class NotificationOut(ORMModel):
    id: str
    type: str
    title: str
    body: str | None = None
    link: str | None = None
    meta: dict[str, Any]
    read_at: datetime | None = None
    created_at: datetime
