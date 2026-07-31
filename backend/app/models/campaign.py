"""Campaigns: proactive outbound — ongoing widget campaigns + one-off scheduled sends.

Chatwoot semantics (docs/research/chatwoot-gaps.md §2): the inbox's channel
forces the type — widget inboxes run "ongoing" campaigns (widget evaluates
trigger_rules client-side, server creates the conversation once per fresh
visitor); email/sms/whatsapp inboxes run "one_off" scheduled blasts picked up
by the campaign_dispatch scheduler job.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base, PortableJSON, UTCDateTime
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk


class CampaignType(enum.StrEnum):
    ONGOING = "ongoing"  # widget-triggered proactive messages
    ONE_OFF = "one_off"  # scheduled blast over email/sms/whatsapp


class CampaignStatus(enum.StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PROCESSING = "processing"  # one_off being dispatched (concurrency guard)
    COMPLETED = "completed"


class Campaign(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "campaigns"
    __table_args__ = (
        Index("ix_campaigns_ws_type_status", "workspace_id", "campaign_type", "status"),
    )

    id: Mapped[str] = pk()
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)  # supports {{contact.name}}
    campaign_type: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=CampaignStatus.DRAFT, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    inbox_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("inboxes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # Member the outbound message is authored as; None → "Campaign"/system author.
    sender_user_id: Mapped[str | None] = mapped_column(GUID)
    # {"type":"all"} | {"type":"segment","segment_id":...} | {"type":"tag","tag_id":...}
    audience: Mapped[dict[str, Any]] = mapped_column(PortableJSON, default=dict, nullable=False)
    # Ongoing only: {"url_pattern": glob, "time_on_page_seconds": int} — evaluated
    # client-side by the widget; the server just validates and creates.
    trigger_rules: Mapped[dict[str, Any]] = mapped_column(
        PortableJSON, default=dict, nullable=False
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime)  # one_off only
    sent_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
