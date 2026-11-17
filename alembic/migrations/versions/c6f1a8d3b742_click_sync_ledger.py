"""click sync ledger

Revision ID: c6f1a8d3b742
Revises: b2e6d9f4a318
Create Date: 2026-11-16 12:00:00

Tables for the import of payment reports from an external source (@clickuz):
sync_periods, source_transactions (+ audit), sync_state, sync_discrepancies.
Nothing existing is changed; downgrade drops the new tables.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'c6f1a8d3b742'
down_revision = 'b2e6d9f4a318'
branch_labels = None
depends_on = None

MONEY = sa.Numeric(20, 4)


def upgrade() -> None:
    op.create_table(
        'sync_periods',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('period_start', sa.Date(), nullable=False),
        sa.Column('period_end', sa.Date(), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('pages', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('tx_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('opening_balance', MONEY, nullable=True),
        sa.Column('closing_balance', MONEY, nullable=True),
        sa.Column('balance_account', sa.String(length=32), nullable=True),
        sa.Column('balance_currency', sa.String(length=8), nullable=True),
        sa.Column('note', sa.String(length=255), nullable=True),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('started_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('finished_at', sa.TIMESTAMP(), nullable=True),
        sa.UniqueConstraint('user_id', 'source', 'period_start', 'period_end', name='uq_sync_periods_range'),
    )
    op.create_index('ix_sync_periods_user_id', 'sync_periods', ['user_id'])

    op.create_table(
        'source_transactions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('source_id', sa.String(length=64), nullable=True),
        sa.Column('dedup_key', sa.String(length=80), nullable=False),
        sa.Column('occurred_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('amount', MONEY, nullable=False),
        sa.Column('currency', sa.String(length=8), nullable=True),
        sa.Column('direction', sa.String(length=8), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('fee', MONEY, nullable=True),
        sa.Column('fee_included', sa.Boolean(), nullable=True),
        sa.Column('account', sa.String(length=32), nullable=True),
        sa.Column('ambiguous', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('own_transfer', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('delivery', sa.String(length=12), nullable=False, server_default='new'),
        sa.Column('accounting_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('accounting.id', name='fk_source_transactions_accounting', ondelete='SET NULL'), nullable=True),
        sa.Column('period_id', sa.Integer(),
                  sa.ForeignKey('sync_periods.id', name='fk_source_transactions_period', ondelete='SET NULL'), nullable=True),
        sa.Column('first_seen_at', sa.TIMESTAMP(), nullable=False, server_default=sa.text("timezone('utc', now())")),
        sa.Column('updated_at', sa.TIMESTAMP(), nullable=False, server_default=sa.text("timezone('utc', now())")),
        sa.UniqueConstraint('user_id', 'source', 'dedup_key', name='uq_source_transactions_key'),
    )
    op.create_index('ix_source_transactions_user_id', 'source_transactions', ['user_id'])

    op.create_table(
        'source_transaction_audit',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('transaction_id', sa.Integer(),
                  sa.ForeignKey('source_transactions.id', name='fk_source_transaction_audit_tx', ondelete='CASCADE'), nullable=False),
        sa.Column('changed_at', sa.TIMESTAMP(), nullable=False, server_default=sa.text("timezone('utc', now())")),
        sa.Column('field', sa.String(length=24), nullable=False),
        sa.Column('old_value', sa.String(length=255), nullable=True),
        sa.Column('new_value', sa.String(length=255), nullable=True),
        sa.Column('reason', sa.String(length=32), nullable=False),
    )
    op.create_index('ix_source_transaction_audit_transaction_id', 'source_transaction_audit', ['transaction_id'])

    op.create_table(
        'sync_state',
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('user.id'), primary_key=True, autoincrement=False),
        sa.Column('source', sa.String(length=16), primary_key=True),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('history_complete', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('paused', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('sync_requested', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('heartbeat_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('last_started_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('last_finished_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('last_ok_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('last_error', sa.String(length=255), nullable=True),
    )

    op.create_table(
        'sync_discrepancies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('kind', sa.String(length=24), nullable=False),
        sa.Column('fingerprint', sa.String(length=64), nullable=False),
        sa.Column('account', sa.String(length=32), nullable=True),
        sa.Column('currency', sa.String(length=8), nullable=True),
        sa.Column('period_id', sa.Integer(),
                  sa.ForeignKey('sync_periods.id', name='fk_sync_discrepancies_period', ondelete='SET NULL'), nullable=True),
        sa.Column('transaction_id', sa.Integer(),
                  sa.ForeignKey('source_transactions.id', name='fk_sync_discrepancies_tx', ondelete='CASCADE'), nullable=True),
        sa.Column('expected', MONEY, nullable=True),
        sa.Column('reported', MONEY, nullable=True),
        sa.Column('difference', MONEY, nullable=True),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='open'),
        sa.Column('resolution', sa.String(length=24), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=False, server_default=sa.text("timezone('utc', now())")),
        sa.Column('notified_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('resolved_at', sa.TIMESTAMP(), nullable=True),
        sa.UniqueConstraint('user_id', 'source', 'fingerprint', name='uq_sync_discrepancies_fingerprint'),
    )
    op.create_index('ix_sync_discrepancies_user_id', 'sync_discrepancies', ['user_id'])


def downgrade() -> None:
    op.drop_table('sync_discrepancies')
    op.drop_table('sync_state')
    op.drop_table('source_transaction_audit')
    op.drop_table('source_transactions')
    op.drop_table('sync_periods')
