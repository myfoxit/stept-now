"""social login: user identities + nullable password hash

Revision ID: c9a1f2d40aa1
Revises: b485d38185bf
Create Date: 2026-08-07 18:55:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

import app.core.db  # portable column types referenced below


revision: str = 'c9a1f2d40aa1'
down_revision: str | Sequence[str] | None = 'b485d38185bf'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'user_identities',
        sa.Column('id', app.core.db.GUID(length=36), nullable=False),
        sa.Column('user_id', app.core.db.GUID(length=36), nullable=False),
        sa.Column('provider', sa.String(length=40), nullable=False),
        sa.Column('provider_user_id', sa.String(length=255), nullable=False),
        sa.Column('email', sa.String(length=320), nullable=True),
        sa.Column('created_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ['user_id'], ['users.id'],
            name=op.f('fk_user_identities_user_id_users'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_user_identities')),
        sa.UniqueConstraint(
            'provider', 'provider_user_id', name='uq_user_identity_provider_account'
        ),
    )
    op.create_index(op.f('ix_user_identities_user_id'), 'user_identities', ['user_id'], unique=False)
    # Social-login users have no password; existing rows keep theirs.
    op.alter_column('users', 'password_hash', existing_type=sa.String(length=300), nullable=True)


def downgrade() -> None:
    op.alter_column('users', 'password_hash', existing_type=sa.String(length=300), nullable=False)
    op.drop_index(op.f('ix_user_identities_user_id'), table_name='user_identities')
    op.drop_table('user_identities')
