"""Add composite performance indexes for factory workspace and queue lookups.

Revision ID: 0008_factory_perf_indexes
Revises: 0007_factory_product_exclusions
Create Date: 2026-11-21 04:00:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0008_factory_perf_indexes"
down_revision: Union[str, None] = "0007_factory_product_exclusions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_INDEX_DDL = (
    (
        "ix_product_mappings_acc_factory",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_product_mappings_acc_factory "
        "ON product_mappings (account_name, factory_id)",
    ),
    (
        "ix_product_mappings_acc_status",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_product_mappings_acc_status "
        "ON product_mappings (account_name, status)",
    ),
    (
        "ix_product_attributes_ean_name",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_product_attributes_ean_name "
        "ON product_attributes (product_ean, attribute_name)",
    ),
    (
        "ix_publish_queue_acc_ean_status",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_publish_queue_acc_ean_status "
        "ON publish_queue_items (account_name, product_ean, status)",
    ),
    (
        "ix_publish_queue_status_date",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_publish_queue_status_date "
        "ON publish_queue_items (status, scheduled_date)",
    ),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for _name, ddl in _INDEX_DDL:
            op.execute(ddl)


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for name, _ddl in reversed(_INDEX_DDL):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
