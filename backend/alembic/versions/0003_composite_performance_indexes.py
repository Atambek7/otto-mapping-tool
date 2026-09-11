"""composite performance indexes

Revision ID: 0003_composite_perf_indexes
Revises: 0002_order_stats
Create Date: 2026-09-10 12:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = '0003_composite_perf_indexes'
down_revision: Union[str, None] = '0002_order_stats'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Product mapping composite indexes
    op.create_index(
        'ix_product_mappings_acc_factory',
        'product_mappings',
        ['account_name', 'factory_id'],
        if_not_exists=True
    )
    op.create_index(
        'ix_product_mappings_acc_status',
        'product_mappings',
        ['account_name', 'status'],
        if_not_exists=True
    )

    # 2. Product attributes composite index
    op.create_index(
        'ix_product_attributes_ean_name',
        'product_attributes',
        ['product_ean', 'attribute_name'],
        if_not_exists=True
    )

    # 3. Publish queue items composite index for Celery Beat tick
    op.create_index(
        'ix_publish_queue_status_date',
        'publish_queue_items',
        ['status', 'scheduled_date'],
        if_not_exists=True
    )


def downgrade() -> None:
    op.drop_index('ix_publish_queue_status_date', table_name='publish_queue_items', if_exists=True)
    op.drop_index('ix_product_attributes_ean_name', table_name='product_attributes', if_exists=True)
    op.drop_index('ix_product_mappings_acc_status', table_name='product_mappings', if_exists=True)
    op.drop_index('ix_product_mappings_acc_factory', table_name='product_mappings', if_exists=True)
