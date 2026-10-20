"""operation original currency

Revision ID: a9d4e2b8c751
Revises: f3c7a1d5e286
Create Date: 2026-10-22 18:00:00

Three nullable columns; existing operations stay in the base currency.
Downgrade drops only the original-currency details (amounts are already
stored converted).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a9d4e2b8c751'
down_revision = 'f3c7a1d5e286'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('accounting', sa.Column('original_amount', sa.Numeric(14, 2), nullable=True))
    op.add_column('accounting', sa.Column('original_currency', sa.String(length=3), nullable=True))
    op.add_column('accounting', sa.Column('rate', sa.Numeric(20, 10), nullable=True))


def downgrade() -> None:
    op.drop_column('accounting', 'rate')
    op.drop_column('accounting', 'original_currency')
    op.drop_column('accounting', 'original_amount')
