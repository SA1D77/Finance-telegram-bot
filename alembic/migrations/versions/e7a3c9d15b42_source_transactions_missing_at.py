"""source transactions missing from a complete report

Revision ID: e7a3c9d15b42
Revises: d4b8e2f61a90
Create Date: 2026-11-18 12:00:00

A payment that a completely read report of its period no longer shows is kept, but marked (missing_at) so that
it is neither offered for recording nor counted in the card balance reconciliation. Existing rows stay NULL
(present). Downgrade drops only this column.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e7a3c9d15b42'
down_revision = 'd4b8e2f61a90'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('source_transactions', sa.Column('missing_at', sa.TIMESTAMP(), nullable=True))


def downgrade() -> None:
    op.drop_column('source_transactions', 'missing_at')
