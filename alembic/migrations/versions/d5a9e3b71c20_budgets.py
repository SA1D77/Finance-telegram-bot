"""budgets and budget alerts

Revision ID: d5a9e3b71c20
Revises: c41f8a2e6d17
Create Date: 2026-10-21 12:00:00

New tables only; existing data is not touched. Downgrade drops the limits
and the log of sent alerts.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5a9e3b71c20'
down_revision = 'c41f8a2e6d17'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'budgets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('category_id', sa.Integer(), nullable=True),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.ForeignKeyConstraint(['category_id'], ['categories.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'category_id', name='uq_budgets_user_category', postgresql_nulls_not_distinct=True),
    )
    op.create_table(
        'budget_alerts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('budget_id', sa.Integer(), nullable=False),
        sa.Column('period', sa.String(length=7), nullable=False),
        sa.Column('threshold', sa.SmallInteger(), nullable=False),
        sa.Column('sent_at', sa.TIMESTAMP(), nullable=False),
        sa.ForeignKeyConstraint(['budget_id'], ['budgets.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('budget_id', 'period', 'threshold', name='uq_budget_alerts_once'),
    )


def downgrade() -> None:
    op.drop_table('budget_alerts')
    op.drop_table('budgets')
