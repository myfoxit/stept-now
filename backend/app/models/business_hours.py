"""Per-inbox weekly opening schedule.

One row per (inbox, day_of_week) with 0=Monday, matching `datetime.weekday()` —
Chatwoot uses 0=Sunday, but aligning with the stdlib removes a conversion from
every calculation in `app.core.business_hours`.

Business hours drive three things: out-of-office replies, business-hours SLA
math (`sla_policies.only_during_business_hours`), and business-hours-gated
campaigns. See docs/CHATWOOT-BACKLOG.md §1.1.
"""

from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import GUID, Base
from app.models.base import TimestampMixin, WorkspaceScopedMixin, pk

# Minutes from midnight; a day is "open" for [open_minute, close_minute).
MINUTES_PER_DAY = 24 * 60


class WorkingHour(TimestampMixin, WorkspaceScopedMixin, Base):
    __tablename__ = "working_hours"
    __table_args__ = (
        UniqueConstraint("inbox_id", "day_of_week", name="uq_working_hours_inbox_day"),
    )

    id: Mapped[str] = pk()
    inbox_id: Mapped[str] = mapped_column(
        GUID, ForeignKey("inboxes.id", ondelete="CASCADE"), index=True, nullable=False
    )
    day_of_week: Mapped[int] = mapped_column(Integer, nullable=False)  # 0=Mon … 6=Sun
    # Closed wins over open_all_day; both false means the [open, close) window applies.
    closed_all_day: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    open_all_day: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    open_minute: Mapped[int] = mapped_column(Integer, default=9 * 60, nullable=False)
    close_minute: Mapped[int] = mapped_column(Integer, default=17 * 60, nullable=False)
