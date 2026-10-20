"""savings goals

Revision ID: f3c7a1d5e286
Revises: e8b2f6c4a913
Create Date: 2026-10-22 14:00:00

New tables only; downgrade drops goals and their contributions.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f3c7a1d5e286'
down_revision = 'e8b2f6c4a913'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'savings_goals',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.BigInteger(), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('target', sa.Numeric(14, 2), nullable=False),
        sa.Column('deadline', sa.Date(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_savings_goals_user_id', 'savings_goals', ['user_id'])
    op.create_table(
        'goal_contributions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('goal_id', sa.Integer(), nullable=False),
        sa.Column('amount', sa.Numeric(14, 2), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=True),
        sa.ForeignKeyConstraint(['goal_id'], ['savings_goals.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_goal_contributions_goal_id', 'goal_contributions', ['goal_id'])


def downgrade() -> None:
    op.drop_index('ix_goal_contributions_goal_id', table_name='goal_contributions')
    op.drop_table('goal_contributions')
    op.drop_index('ix_savings_goals_user_id', table_name='savings_goals')
    op.drop_table('savings_goals')
