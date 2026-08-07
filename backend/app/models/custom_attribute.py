"""Typed definitions for the free-form `attributes` JSON on contacts/conversations.

Values keep living in the owning record's `attributes` dict; a definition adds a
display name, a type, validation, and (for `list`) the option set — which is what
turns untyped JSON into something the UI can render, the filter engine can offer
operators for, and reports can group by.

Modelled on Chatwoot's `custom_attribute_definitions` (docs/CHATWOOT-BACKLOG.md §1.6).
"""

from __future__ import annotations

import enum
from typing import Any

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, PortableJSON
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class AttributeModel(enum.StrEnum):
    CONTACT = "contact"
    CONVERSATION = "conversation"


class AttributeType(enum.StrEnum):
    TEXT = "text"
    NUMBER = "number"
    CURRENCY = "currency"
    PERCENT = "percent"
    LINK = "link"
    DATE = "date"
    LIST = "list"
    CHECKBOX = "checkbox"


class CustomAttributeDefinition(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "custom_attribute_definitions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "attribute_model", "key", name="uq_custom_attr_ws_model_key"
        ),
    )

    id: Mapped[str] = pk()
    # Which record the definition describes: "contact" | "conversation".
    attribute_model: Mapped[str] = mapped_column(String(20), nullable=False)
    # The JSON key inside that record's `attributes` dict.
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    attribute_type: Mapped[str] = mapped_column(
        String(20), default=AttributeType.TEXT, nullable=False
    )
    # Option set for `list`; ignored for every other type.
    options: Mapped[list[Any]] = mapped_column(PortableJSON, default=list, nullable=False)
    default_value: Mapped[Any | None] = mapped_column(PortableJSON)
    # Optional extra validation for text-ish values, with a human hint on failure.
    regex_pattern: Mapped[str | None] = mapped_column(String(300))
    regex_cue: Mapped[str | None] = mapped_column(String(300))
    ord: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Surfaced in the contact/conversation sidebar when true.
    shown_on_front: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
