"""single-use password reset tokens

Replaces the stateless password-reset JWT with a hashed, DB-backed token that is
cleared on use, so a reset link cannot be replayed inside its TTL.

Revision ID: b7c04e1aa93d
Revises: d41be0c77c02
Create Date: 2026-08-08

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.db import UTCDateTime

revision = "b7c04e1aa93d"
down_revision = "d41be0c77c02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_reset_hash", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("password_reset_expires_at", UTCDateTime(), nullable=True))
    op.create_index("ix_users_password_reset_hash", "users", ["password_reset_hash"])


def downgrade() -> None:
    op.drop_index("ix_users_password_reset_hash", table_name="users")
    op.drop_column("users", "password_reset_expires_at")
    op.drop_column("users", "password_reset_hash")
