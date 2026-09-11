"""composite ean and publish tables

Revision ID: 0001_composite_ean
Revises: 
Create Date: 2026-09-08 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '0001_composite_ean'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

account_type_enum = postgresql.ENUM('JV', 'XL', name='accounttype', create_type=False)
plan_status_enum = postgresql.ENUM('draft', 'active', 'paused', 'completed', 'cancelled', name='planstatus', create_type=False)
queue_item_status_enum = postgresql.ENUM('pending', 'published', 'failed', name='queueitemstatus', create_type=False)
executor_type_enum = postgresql.ENUM('otto_api', 'batch_file', name='executortype', create_type=False)


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Explicitly create Postgres ENUM types once
    raw_account_type = postgresql.ENUM('JV', 'XL', name='accounttype')
    raw_plan_status = postgresql.ENUM('draft', 'active', 'paused', 'completed', 'cancelled', name='planstatus')
    raw_queue_status = postgresql.ENUM('pending', 'published', 'failed', name='queueitemstatus')
    raw_executor_type = postgresql.ENUM('otto_api', 'batch_file', name='executortype')

    for raw_enum in (raw_account_type, raw_plan_status, raw_queue_status, raw_executor_type):
        raw_enum.create(conn, checkfirst=True)

    # 2. Swap index concurrently with protection against invalid state
    with op.get_context().autocommit_block():
        is_invalid = conn.execute(sa.text("""
            SELECT NOT i.indisvalid
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            WHERE c.relname = 'uq_product_ean_account'
        """)).scalar()

        if is_invalid:
            op.execute("DROP INDEX CONCURRENTLY uq_product_ean_account")

        op.execute("DROP INDEX CONCURRENTLY IF EXISTS ix_product_mappings_product_ean")
        op.execute(
            "CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS "
            "uq_product_ean_account ON product_mappings (product_ean, account_name)"
        )

    # 3. Alter product_mappings.account_name to ENUM with NOT NULL
    op.execute("ALTER TABLE product_mappings ALTER COLUMN account_name TYPE accounttype USING account_name::accounttype")
    op.execute("ALTER TABLE product_mappings ALTER COLUMN account_name SET NOT NULL")

    # 4. Create publish_plans table
    op.create_table(
        'publish_plans',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('factory_id', sa.String(), nullable=False),
        sa.Column('factory_name', sa.String(), nullable=True),
        sa.Column('account_name', account_type_enum, nullable=False),
        sa.Column('total_products', sa.Integer(), nullable=False),
        sa.Column('total_days', sa.Integer(), nullable=False),
        sa.Column('current_day', sa.Integer(), server_default='1', nullable=False),
        sa.Column('daily_time', sa.String(), server_default='08:00', nullable=False),
        sa.Column('status', plan_status_enum, nullable=False),
        sa.Column('executor_type', executor_type_enum, nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now(), nullable=True)
    )

    # 5. Create publish_queue_items table
    op.create_table(
        'publish_queue_items',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('plan_id', sa.Integer(), sa.ForeignKey('publish_plans.id', ondelete='CASCADE'), nullable=False),
        sa.Column('product_ean', sa.String(), nullable=False),
        sa.Column('account_name', account_type_enum, nullable=False),
        sa.Column('category_id', sa.String(), nullable=False),
        sa.Column('scheduled_day', sa.Integer(), nullable=False),
        sa.Column('scheduled_date', sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column('status', queue_item_status_enum, server_default='pending', nullable=False, index=True),
        sa.Column('attempts', sa.Integer(), server_default='0', nullable=False),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('plan_id', 'product_ean', name='uq_plan_product_ean')
    )


def downgrade() -> None:
    op.drop_table('publish_queue_items')
    op.drop_table('publish_plans')

    op.execute("ALTER TABLE product_mappings ALTER COLUMN account_name TYPE VARCHAR")

    conn = op.get_bind()
    raw_account_type = postgresql.ENUM(name='accounttype')
    raw_plan_status = postgresql.ENUM(name='planstatus')
    raw_queue_status = postgresql.ENUM(name='queueitemstatus')
    raw_executor_type = postgresql.ENUM(name='executortype')

    for raw_enum in (raw_executor_type, raw_queue_status, raw_plan_status, raw_account_type):
        raw_enum.drop(conn, checkfirst=True)

    # NOTE: Composite index 'uq_product_ean_account' is intentionally kept on product_mappings.
    # Restoring global 'ix_product_mappings_product_ean' is unsafe because duplicate EANs 
    # between JV and XL may already exist by the time this migration is rolled back.
