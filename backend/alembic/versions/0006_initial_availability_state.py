"""Persist initial availability progress for new Drip Feed publications."""
from alembic import op
import sqlalchemy as sa

revision = '0006_initial_availability'
down_revision = '0005_ai_attributes'
branch_labels = None
depends_on = None


def upgrade():
    from sqlalchemy import inspect
    bind = op.get_bind()
    insp = inspect(bind)
    cols = [c['name'] for c in insp.get_columns('publish_queue_items')]
    if 'otto_submission_attempted_at' not in cols:
        op.add_column('publish_queue_items', sa.Column('otto_submission_attempted_at', sa.DateTime(timezone=True), nullable=True))
    if not insp.has_table('initial_availability_states'):
        op.create_table(
            'initial_availability_states',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('account_name', sa.String(2), nullable=False),
            sa.Column('product_ean', sa.String(), nullable=False),
            sa.Column('queue_item_id', sa.Integer(), nullable=False),
            sa.Column('plan_id', sa.Integer(), nullable=True),
            sa.Column('factory_id', sa.String(), nullable=True),
            sa.Column('factory_name', sa.String(), nullable=True),
            sa.Column('category_id', sa.String(), nullable=True),
            sa.Column('stock_state', sa.String(24), nullable=False),
            sa.Column('delivery_state', sa.String(24), nullable=False),
            sa.Column('stock_attempts', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('delivery_attempts', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('stock_next_attempt_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('delivery_next_attempt_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('stock_last_error', sa.Text(), nullable=True),
            sa.Column('delivery_last_error', sa.Text(), nullable=True),
            sa.Column('delivery_payload_json', sa.JSON(), nullable=True),
            sa.Column('dispatch_lease_until', sa.DateTime(timezone=True), nullable=True),
            sa.Column('next_dispatch_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('last_dispatch_error', sa.Text(), nullable=True),
            sa.Column('manual_retry_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('next_attempt_trigger_kind', sa.String(16), nullable=False, server_default='automatic'),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint('account_name', 'product_ean', name='uq_initial_availability_account_ean'),
            sa.CheckConstraint("account_name IN ('JV', 'XL')", name='ck_initial_availability_account'),
            sa.CheckConstraint("stock_state IN ('pending', 'sending', 'confirmed', 'retryable_failed', 'failed', 'unknown', 'accepted')", name='ck_initial_availability_stock_state'),
            sa.CheckConstraint("delivery_state IN ('pending', 'sending', 'confirmed', 'retryable_failed', 'failed', 'unknown', 'accepted')", name='ck_initial_availability_delivery_state'),
            sa.CheckConstraint("next_attempt_trigger_kind IN ('automatic', 'manual')", name='ck_initial_availability_trigger_kind'),
            sa.CheckConstraint('stock_attempts >= 0 AND delivery_attempts >= 0 AND manual_retry_count >= 0', name='ck_initial_availability_attempt_counts'),
        )
        op.create_index('ix_initial_availability_dispatch', 'initial_availability_states', ['next_dispatch_at', 'dispatch_lease_until'])
    if not insp.has_table('initial_availability_attempts'):
        op.create_table(
            'initial_availability_attempts',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('state_id', sa.Integer(), sa.ForeignKey('initial_availability_states.id'), nullable=False),
            sa.Column('operation', sa.String(16), nullable=False),
            sa.Column('attempt_number', sa.Integer(), nullable=False),
            sa.Column('trigger_kind', sa.String(16), nullable=False),
            sa.Column('payload_hash', sa.String(64), nullable=False),
            sa.Column('outcome', sa.String(24), nullable=False, server_default='sending'),
            sa.Column('status_code', sa.Integer(), nullable=True),
            sa.Column('error_summary', sa.Text(), nullable=True),
            sa.Column('started_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint('state_id', 'operation', 'attempt_number', name='uq_initial_availability_attempt'),
            sa.CheckConstraint("operation IN ('stock', 'delivery')", name='ck_initial_availability_attempt_operation'),
            sa.CheckConstraint("trigger_kind IN ('automatic', 'manual')", name='ck_initial_availability_attempt_trigger'),
            sa.CheckConstraint("outcome IN ('sending', 'confirmed', 'accepted', 'retryable_failed', 'failed', 'unknown')", name='ck_initial_availability_attempt_outcome'),
            sa.CheckConstraint('attempt_number > 0', name='ck_initial_availability_attempt_number'),
        )
        op.create_index('ix_initial_availability_attempts_state_id', 'initial_availability_attempts', ['state_id'])
    if not insp.has_table('initial_availability_review_events'):
        op.create_table(
            'initial_availability_review_events',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('state_id', sa.Integer(), sa.ForeignKey('initial_availability_states.id'), nullable=False),
            sa.Column('account_name', sa.String(2), nullable=False),
            sa.Column('product_ean', sa.String(), nullable=False),
            sa.Column('operation', sa.String(16), nullable=False),
            sa.Column('outcome', sa.String(16), nullable=False),
            sa.Column('actor_id', sa.String(128), nullable=False),
            sa.Column('reason', sa.Text(), nullable=False),
            sa.Column('evidence_ref', sa.String(512), nullable=True),
            sa.Column('payload_hash', sa.String(64), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.CheckConstraint("operation IN ('stock', 'delivery')", name='ck_initial_availability_review_operation'),
            sa.CheckConstraint("outcome IN ('APPLIED', 'NOT_APPLIED', 'UNRESOLVED', 'RETRY_AUTHORIZED', 'PAYLOAD_CORRECTED')", name='ck_initial_availability_review_outcome'),
            sa.CheckConstraint("account_name IN ('JV', 'XL')", name='ck_initial_availability_review_account'),
        )
        op.create_index('ix_initial_availability_review_events_state_id', 'initial_availability_review_events', ['state_id'])


def downgrade():
    count = op.get_bind().execute(sa.text('SELECT count(*) FROM initial_availability_states')).scalar()
    attempt_rows = op.get_bind().execute(sa.text('SELECT count(*) FROM initial_availability_attempts')).scalar()
    reviews = op.get_bind().execute(sa.text('SELECT count(*) FROM initial_availability_review_events')).scalar()
    submission_evidence = op.get_bind().execute(sa.text('SELECT count(*) FROM publish_queue_items WHERE otto_submission_attempted_at IS NOT NULL')).scalar()
    if count or attempt_rows or reviews or submission_evidence:
        raise RuntimeError('Cannot downgrade: availability or submission evidence exists')
    op.drop_index('ix_initial_availability_review_events_state_id', table_name='initial_availability_review_events')
    op.drop_table('initial_availability_review_events')
    op.drop_index('ix_initial_availability_attempts_state_id', table_name='initial_availability_attempts')
    op.drop_table('initial_availability_attempts')
    op.drop_index('ix_initial_availability_dispatch', table_name='initial_availability_states')
    op.drop_table('initial_availability_states')
    op.drop_column('publish_queue_items', 'otto_submission_attempted_at')
