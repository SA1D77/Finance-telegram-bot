"""recurring operations

Revision ID: e8b2f6c4a913
Revises: d5a9e3b71c20
Create Date: 2026-10-22 10:00:00

New table only; downgrade drops the templates (operations they already
created stay).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'e8b2f6c4a913'
down_revision = 'd5a9e3b71c20'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'recurring_operations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('category_id', sa.Integer(), nullable=False),
        # The enum type already exists (accounting.operation_type).
        sa.Column('operation_type', postgresql.ENUM('income', 'expenditure', name='operationtype', create_type=False), nullable=False),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('comment', sa.String(length=255), nullable=True),
        sa.Column('frequency', sa.String(length=8), nullable=False),
        sa.Column('anchor_day', sa.SmallInteger(), nullable=False),
        sa.Column('next_date', sa.Date(), nullable=False),
        sa.Column('remind_days', sa.SmallInteger(), nullable=False),
        sa.Column('reminded_for', sa.Date(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.ForeignKeyConstraint(['category_id'], ['categories.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_recurring_operations_user_id', 'recurring_operations', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_recurring_operations_user_id', table_name='recurring_operations')
    op.drop_table('recurring_operations')
