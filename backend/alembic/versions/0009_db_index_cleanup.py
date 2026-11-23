"""Add missing composite indexes and drop redundant duplicate indexes concurrently.

Revision ID: 0009_db_index_cleanup
Revises: 0008_factory_perf_indexes
Create Date: 2026-11-21 04:38:00.000000
"""
from typing import Sequence, Union
from alembic import op

revision: str = "0009_db_index_cleanup"
down_revision: Union[str, None] = "0008_factory_perf_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_INDEXES = (
    (
        "ix_initial_availability_acc_factory",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_initial_availability_acc_factory "
        "ON initial_availability_states (account_name, factory_id)",
    ),
    (
        "ix_publish_plans_acc_factory",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_publish_plans_acc_factory "
        "ON publish_plans (account_name, factory_id)",
    ),
)

# These 6 indexes are 100% redundant duplicates of PRIMARY KEY(id) or left-prefix
# of uq_product_attr_account(product_ean, attribute_name, account_name).
_REDUNDANT_INDEXES = (
    (
        "ix_product_attributes_ean_name",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_product_attributes_ean_name "
        "ON product_attributes (product_ean, attribute_name)",
    ),
    (
        "ix_product_attributes_id",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_product_attributes_id "
        "ON product_attributes (id)",
    ),
    (
        "ix_availability_sync_log_id",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_availability_sync_log_id "
        "ON availability_sync_log (id)",
    ),
    (
        "ix_product_mappings_id",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_product_mappings_id "
        "ON product_mappings (id)",
    ),
    (
        "ix_publish_queue_items_id",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_publish_queue_items_id "
        "ON publish_queue_items (id)",
    ),
    (
        "ix_otto_order_items_id",
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS ix_otto_order_items_id "
        "ON otto_order_items (id)",
    ),
)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for _name, create_ddl in _NEW_INDEXES:
            op.execute(create_ddl)
        for name, _recreate_ddl in _REDUNDANT_INDEXES:
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        for _name, recreate_ddl in _REDUNDANT_INDEXES:
            op.execute(recreate_ddl)
        for name, _create_ddl in reversed(_NEW_INDEXES):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
