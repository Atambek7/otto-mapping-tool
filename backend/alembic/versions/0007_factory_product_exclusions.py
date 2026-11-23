"""Persist scoped factory/EAN exclusions and audited factory ownership repair."""
from alembic import op
import sqlalchemy as sa

revision = "0007_factory_product_exclusions"
down_revision = "0006_initial_availability"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "factory_product_exclusions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("account_name", sa.String(2), nullable=False),
        sa.Column("excluded_factory_id", sa.String(), nullable=False),
        sa.Column("source_factory_id", sa.String(), nullable=False),
        sa.Column("product_ean", sa.String(), nullable=False),
        sa.Column("batch_id", sa.String(64), nullable=False),
        sa.Column("source_manifest_hash", sa.String(64), nullable=False),
        sa.Column("source_verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_by", sa.String(128), nullable=True),
        sa.CheckConstraint("account_name IN ('JV', 'XL')", name="ck_factory_exclusion_account"),
        sa.UniqueConstraint("account_name", "excluded_factory_id", "product_ean", name="uq_factory_exclusion_scope_ean"),
    )
    op.create_index("ix_factory_product_exclusions_account_name", "factory_product_exclusions", ["account_name"])
    op.create_index("ix_factory_product_exclusions_excluded_factory_id", "factory_product_exclusions", ["excluded_factory_id"])
    op.create_index("ix_factory_product_exclusions_batch_id", "factory_product_exclusions", ["batch_id"])
    op.create_index("ix_factory_product_exclusions_is_active", "factory_product_exclusions", ["is_active"])

    op.create_table(
        "factory_product_exclusion_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.String(64), nullable=False),
        sa.Column("account_name", sa.String(2), nullable=False),
        sa.Column("excluded_factory_id", sa.String(), nullable=False),
        sa.Column("source_factory_id", sa.String(), nullable=False),
        sa.Column("product_ean", sa.String(), nullable=False),
        sa.Column("source_manifest_hash", sa.String(64), nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.CheckConstraint("account_name IN ('JV', 'XL')", name="ck_factory_exclusion_event_account"),
        sa.CheckConstraint("action IN ('APPLIED', 'ROLLED_BACK')", name="ck_factory_exclusion_event_action"),
        sa.UniqueConstraint("batch_id", "account_name", "excluded_factory_id", "product_ean", "action", name="uq_factory_exclusion_event_action"),
    )
    op.create_index("ix_factory_product_exclusion_events_batch_id", "factory_product_exclusion_events", ["batch_id"])
    op.create_index("ix_factory_product_exclusion_events_account_name", "factory_product_exclusion_events", ["account_name"])
    op.create_index("ix_factory_product_exclusion_events_product_ean", "factory_product_exclusion_events", ["product_ean"])

    op.create_table(
        "factory_product_reassignments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.String(64), nullable=False),
        sa.Column("account_name", sa.String(2), nullable=False),
        sa.Column("product_ean", sa.String(), nullable=False),
        sa.Column("mapping_id", sa.Integer(), nullable=False),
        sa.Column("previous_factory_id", sa.String(), nullable=False),
        sa.Column("target_factory_id", sa.String(), nullable=False),
        sa.Column("previous_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="APPLIED"),
        sa.Column("actor", sa.String(128), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("rolled_back_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("account_name IN ('JV', 'XL')", name="ck_factory_reassignment_account"),
        sa.CheckConstraint("status IN ('APPLIED', 'ROLLED_BACK')", name="ck_factory_reassignment_status"),
        sa.UniqueConstraint("batch_id", "account_name", "product_ean", name="uq_factory_reassignment_batch_ean"),
    )
    op.create_index("ix_factory_product_reassignments_batch_id", "factory_product_reassignments", ["batch_id"])
    op.create_index("ix_factory_product_reassignments_account_name", "factory_product_reassignments", ["account_name"])
    op.create_index("ix_factory_product_reassignments_product_ean", "factory_product_reassignments", ["product_ean"])


def downgrade():
    bind = op.get_bind()
    reassignments = bind.execute(sa.text("SELECT count(*) FROM factory_product_reassignments")).scalar_one()
    events = bind.execute(sa.text("SELECT count(*) FROM factory_product_exclusion_events")).scalar_one()
    exclusions = bind.execute(sa.text("SELECT count(*) FROM factory_product_exclusions")).scalar_one()
    if reassignments or events or exclusions:
        raise RuntimeError("Refusing to drop non-empty factory exclusion audit tables; use explicit forward recovery.")
    op.drop_table("factory_product_reassignments")
    op.drop_table("factory_product_exclusion_events")
    op.drop_table("factory_product_exclusions")
