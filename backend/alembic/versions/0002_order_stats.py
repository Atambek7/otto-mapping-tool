"""order stats table

Revision ID: 0002_order_stats
Revises: 0001_composite_ean
Create Date: 2026-09-08 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0002_order_stats'
down_revision: Union[str, None] = '0001_composite_ean'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

account_type_enum = postgresql.ENUM('JV', 'XL', name='accounttype', create_type=False)


def upgrade() -> None:
    op.create_table(
        'order_stats',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_name', account_type_enum, nullable=False),
        sa.Column('order_date', sa.Date(), nullable=False),
        sa.Column('weekday', sa.SmallInteger(), nullable=False),
        sa.Column('orders_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('items_count', sa.Integer(), server_default='0', nullable=False),
        sa.Column('revenue', sa.Numeric(12, 2), server_default='0', nullable=False),
        sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('account_name', 'order_date', name='uq_order_stats_account_date'),
    )
    op.create_index(
        'ix_order_stats_account_weekday',
        'order_stats',
        ['account_name', 'weekday']
    )


def downgrade() -> None:
    op.drop_index('ix_order_stats_account_weekday', table_name='order_stats')
    op.drop_table('order_stats')
