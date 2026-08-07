"""billing: subscriptions + webhook idempotency ledger

Revision ID: d41be0c77c02
Revises: c9a1f2d40aa1
Create Date: 2026-08-07 19:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

import app.core.db  # portable column types referenced below


revision: str = 'd41be0c77c02'
down_revision: str | Sequence[str] | None = 'c9a1f2d40aa1'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'billing_subscriptions',
        sa.Column('id', app.core.db.GUID(length=36), nullable=False),
        sa.Column('stripe_customer_id', sa.String(length=120), nullable=True),
        sa.Column('stripe_subscription_id', sa.String(length=120), nullable=True),
        sa.Column('plan', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('seats', sa.Integer(), nullable=False),
        sa.Column('current_period_end', app.core.db.UTCDateTime(timezone=True), nullable=True),
        sa.Column('cancel_at_period_end', sa.Boolean(), nullable=False),
        sa.Column('created_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column('updated_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
        sa.Column('workspace_id', app.core.db.GUID(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ['workspace_id'], ['workspaces.id'],
            name=op.f('fk_billing_subscriptions_workspace_id_workspaces'), ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_billing_subscriptions')),
        sa.UniqueConstraint('workspace_id', name='uq_billing_subscriptions_workspace'),
    )
    op.create_index(
        op.f('ix_billing_subscriptions_workspace_id'),
        'billing_subscriptions', ['workspace_id'], unique=False,
    )
    op.create_index(
        op.f('ix_billing_subscriptions_stripe_customer_id'),
        'billing_subscriptions', ['stripe_customer_id'], unique=False,
    )
    op.create_index(
        op.f('ix_billing_subscriptions_stripe_subscription_id'),
        'billing_subscriptions', ['stripe_subscription_id'], unique=False,
    )
    op.create_table(
        'billing_webhook_events',
        sa.Column('id', app.core.db.GUID(length=36), nullable=False),
        sa.Column('stripe_event_id', sa.String(length=255), nullable=False),
        sa.Column('type', sa.String(length=120), nullable=False),
        sa.Column('received_at', app.core.db.UTCDateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_billing_webhook_events')),
    )
    op.create_index(
        op.f('ix_billing_webhook_events_stripe_event_id'),
        'billing_webhook_events', ['stripe_event_id'], unique=True,
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_billing_webhook_events_stripe_event_id'), table_name='billing_webhook_events')
    op.drop_table('billing_webhook_events')
    op.drop_index(op.f('ix_billing_subscriptions_stripe_subscription_id'), table_name='billing_subscriptions')
    op.drop_index(op.f('ix_billing_subscriptions_stripe_customer_id'), table_name='billing_subscriptions')
    op.drop_index(op.f('ix_billing_subscriptions_workspace_id'), table_name='billing_subscriptions')
    op.drop_table('billing_subscriptions')
