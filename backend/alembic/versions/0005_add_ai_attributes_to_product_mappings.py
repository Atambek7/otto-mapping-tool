"""add ai_attributes to product_mappings

Revision ID: 0005_ai_attributes
Revises: 0004_processing_state
Create Date: 2026-09-17 12:00:00.000000

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '0005_ai_attributes'
down_revision: Union[str, None] = '0004_processing_state'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('product_mappings', sa.Column('ai_attributes', sa.JSON(), server_default='[]', nullable=True))


def downgrade() -> None:
    op.drop_column('product_mappings', 'ai_attributes')
