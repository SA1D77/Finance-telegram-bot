"""category kind and user currency

Revision ID: b2e6d9f4a318
Revises: a9d4e2b8c751
Create Date: 2026-10-21 12:00:00

- categories.kind: 'income' / 'expense' / NULL (both). Existing categories
  get a kind from their operations: only incomes -> income, only expenses
  -> expense, mixed or unused -> NULL. Nothing is lost on downgrade except
  this hint.
- user.currency: per-user main currency (NULL = BASE_CURRENCY).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2e6d9f4a318'
down_revision = 'a9d4e2b8c751'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('categories', sa.Column('kind', sa.String(length=8), nullable=True))
    op.execute('''
        UPDATE categories c SET kind = CASE
            WHEN t.has_income AND NOT t.has_expense THEN 'income'
            WHEN t.has_expense AND NOT t.has_income THEN 'expense'
        END
        FROM (
            SELECT category_id,
                   bool_or(operation_type = 'income') AS has_income,
                   bool_or(operation_type = 'expenditure') AS has_expense
            FROM accounting GROUP BY category_id
        ) t
        WHERE t.category_id = c.id
    ''')
    op.add_column('user', sa.Column('currency', sa.String(length=3), nullable=True))


def downgrade() -> None:
    op.drop_column('user', 'currency')
    op.drop_column('categories', 'kind')
