"""operation comment

Revision ID: c41f8a2e6d17
Revises: b7e2c4d91a05
Create Date: 2026-10-21 10:00:00

Optional free-text comment on an operation ("500 кафе обед" -> "обед").
Adding a nullable column keeps existing rows untouched; downgrade drops
only the comments.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c41f8a2e6d17'
down_revision = 'b7e2c4d91a05'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('accounting', sa.Column('comment', sa.String(length=255), nullable=True))


def downgrade() -> None:
    op.drop_column('accounting', 'comment')
