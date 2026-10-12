"""processing state and task tracking

Revision ID: 0004_processing_and_task_tracking
Revises: 0003_composite_perf_indexes
Create Date: 2026-09-15 12:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0004_processing_state'
down_revision: Union[str, None] = '0003_composite_perf_indexes'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add 'processing' value to queueitemstatus enum
    # In PostgreSQL, ALTER TYPE ... ADD VALUE cannot run inside an active transaction block.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE queueitemstatus ADD VALUE IF NOT EXISTS 'processing'")

    # 2. Add columns to publish_queue_items
    op.add_column('publish_queue_items', sa.Column('otto_task_href', sa.String(), nullable=True))
    op.add_column('publish_queue_items', sa.Column('otto_task_failed_href', sa.String(), nullable=True))
    op.add_column('publish_queue_items', sa.Column('poll_attempts', sa.Integer(), server_default='0', nullable=False))

    # 3. Create index for fast polling of items in processing state
    op.create_index(
        'ix_publish_queue_status_poll',
        'publish_queue_items',
        ['status', 'poll_attempts'],
        if_not_exists=True
    )


def downgrade() -> None:
    op.drop_index('ix_publish_queue_status_poll', table_name='publish_queue_items', if_exists=True)
    op.drop_column('publish_queue_items', 'poll_attempts')
    op.drop_column('publish_queue_items', 'otto_task_failed_href')
    op.drop_column('publish_queue_items', 'otto_task_href')
    # Note: Postgres ENUM values cannot be easily removed without recreating the type
