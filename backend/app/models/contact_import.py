"""CSV contact import runs.

An import is uploaded, previewed (headers + a few sample rows), mapped
(csv column → contact field or `attributes.<key>`), then started. Rows are
processed in batches by a queued task so a 50k-row file doesn't block a request;
per-row failures are collected instead of aborting the run.

See docs/CHATWOOT-BACKLOG.md §1.7 — without this nobody can migrate onto Stept.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON, UTCDateTime
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class ImportStatus(enum.StrEnum):
    PENDING = "pending"  # uploaded, awaiting a column mapping + start
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ContactImport(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "contact_imports"

    id: Mapped[str] = pk()
    filename: Mapped[str] = mapped_column(String(400), nullable=False)
    # Storage key of the uploaded CSV (app.core.storage).
    file_key: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=ImportStatus.PENDING, nullable=False)
    # csv header → "name" | "email" | "phone" | "external_id" | "attributes.<key>" | "" (skip)
    mapping: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    total_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processed_rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Capped list of {"row": n, "error": "…"} so one bad file can't bloat the row.
    errors: Mapped[list[Any]] = mapped_column(PortableJSON, default=list, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_by: Mapped[str | None] = mapped_column(
        GUID, ForeignKey("users.id", ondelete="SET NULL")
    )
