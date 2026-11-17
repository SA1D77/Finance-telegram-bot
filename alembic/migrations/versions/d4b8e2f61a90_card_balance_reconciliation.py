"""card balance reconciliation

Revision ID: d4b8e2f61a90
Revises: c6f1a8d3b742
Create Date: 2026-11-17 12:00:00

Card balances read with /balance from @clickuz, the intervals between them reconciled against the imported
payments, the owner's explanations of unexplained changes and their later matches to source payments.
sync_state gets the run result and the balance schedule columns (all nullable or with a default), so
existing rows and deployments without the feature keep working. Downgrade drops only what is added here.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = 'd4b8e2f61a90'
down_revision = 'c6f1a8d3b742'
branch_labels = None
depends_on = None

MONEY = sa.Numeric(20, 4)


def upgrade() -> None:
    op.add_column('sync_state', sa.Column('last_result', sa.String(length=8), nullable=True))
    op.add_column('sync_state', sa.Column('balance_requested', sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column('sync_state', sa.Column('last_balance_at', sa.TIMESTAMP(), nullable=True))
    op.add_column('sync_state', sa.Column('last_balance_ok_at', sa.TIMESTAMP(), nullable=True))
    op.add_column('sync_state', sa.Column('last_balance_error', sa.String(length=255), nullable=True))
    op.add_column('sync_state', sa.Column('last_review_on', sa.Date(), nullable=True))
    # Runs finished before this migration: an error means failed, otherwise ok.
    op.execute("UPDATE sync_state SET last_result = CASE WHEN last_error IS NULL THEN 'ok' ELSE 'failed' END "
               "WHERE last_finished_at IS NOT NULL")

    op.create_table(
        'balance_observations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('requested_at', sa.TIMESTAMP(), nullable=False),
        sa.Column('observed_at', sa.TIMESTAMP(), nullable=False),
        sa.Column('message_id', sa.BigInteger(), nullable=True),
        sa.Column('status', sa.String(length=10), nullable=False),
        sa.Column('error', sa.String(length=255), nullable=True),
    )
    op.create_index('ix_balance_observations_user_id', 'balance_observations', ['user_id'])

    op.create_table(
        'balance_snapshots',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('observation_id', sa.Integer(), sa.ForeignKey('balance_observations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('kind', sa.String(length=8), nullable=False),
        sa.Column('card', sa.String(length=32), nullable=False, server_default=''),
        sa.Column('last4', sa.String(length=4), nullable=True),
        sa.Column('ambiguous', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('currency', sa.String(length=8), nullable=False),
        sa.Column('amount', MONEY, nullable=False),
        sa.Column('observed_at', sa.TIMESTAMP(), nullable=False),
        sa.UniqueConstraint('observation_id', 'kind', 'card', 'currency', name='uq_balance_snapshots_row'),
    )
    op.create_index('ix_balance_snapshots_observation_id', 'balance_snapshots', ['observation_id'])
    op.create_index('ix_balance_snapshots_user_id', 'balance_snapshots', ['user_id'])

    op.create_table(
        'balance_intervals',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('source', sa.String(length=16), nullable=False),
        sa.Column('card', sa.String(length=32), nullable=False),
        sa.Column('last4', sa.String(length=4), nullable=True),
        sa.Column('currency', sa.String(length=8), nullable=False),
        sa.Column('start_snapshot_id', sa.Integer(), sa.ForeignKey('balance_snapshots.id', ondelete='CASCADE'), nullable=False),
        sa.Column('end_snapshot_id', sa.Integer(), sa.ForeignKey('balance_snapshots.id', ondelete='CASCADE'), nullable=False),
        sa.Column('start_at', sa.TIMESTAMP(), nullable=False),
        sa.Column('end_at', sa.TIMESTAMP(), nullable=False),
        sa.Column('opening', MONEY, nullable=False),
        sa.Column('closing', MONEY, nullable=False),
        sa.Column('incoming', MONEY, nullable=True),
        sa.Column('outgoing', MONEY, nullable=True),
        sa.Column('fees', MONEY, nullable=True),
        sa.Column('expected', MONEY, nullable=True),
        sa.Column('difference', MONEY, nullable=True),
        sa.Column('explained', MONEY, nullable=True),
        sa.Column('remaining', MONEY, nullable=True),
        sa.Column('status', sa.String(length=12), nullable=False, server_default='waiting'),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column('computed_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('unresolved', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('snoozed_until', sa.TIMESTAMP(), nullable=True),
        sa.Column('notified_at', sa.TIMESTAMP(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=False),
        sa.UniqueConstraint('start_snapshot_id', 'end_snapshot_id', name='uq_balance_intervals_pair'),
    )
    op.create_index('ix_balance_intervals_user_id', 'balance_intervals', ['user_id'])

    op.create_table(
        'balance_interval_audit',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('interval_id', sa.Integer(), sa.ForeignKey('balance_intervals.id', ondelete='CASCADE'), nullable=False),
        sa.Column('at', sa.TIMESTAMP(), nullable=False),
        sa.Column('event', sa.String(length=24), nullable=False),
        sa.Column('status', sa.String(length=12), nullable=True),
        sa.Column('remaining', MONEY, nullable=True),
        sa.Column('details', sa.Text(), nullable=True),
    )
    op.create_index('ix_balance_interval_audit_interval_id', 'balance_interval_audit', ['interval_id'])

    op.create_table(
        'balance_explanations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.BigInteger(), sa.ForeignKey('user.id'), nullable=False),
        sa.Column('interval_id', sa.Integer(), sa.ForeignKey('balance_intervals.id', ondelete='CASCADE'), nullable=False),
        sa.Column('token', sa.String(length=32), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('direction', sa.String(length=4), nullable=False),
        sa.Column('amount', MONEY, nullable=False),
        sa.Column('currency', sa.String(length=8), nullable=False),
        sa.Column('category_id', sa.Integer(), nullable=True),
        sa.Column('accounting_id', postgresql.UUID(as_uuid=True),
                  sa.ForeignKey('accounting.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=False),
        sa.UniqueConstraint('user_id', 'token', name='uq_balance_explanations_token'),
    )
    op.create_index('ix_balance_explanations_user_id', 'balance_explanations', ['user_id'])
    op.create_index('ix_balance_explanations_interval_id', 'balance_explanations', ['interval_id'])

    op.create_table(
        'balance_explanation_matches',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('explanation_id', sa.Integer(), sa.ForeignKey('balance_explanations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('transaction_id', sa.Integer(), sa.ForeignKey('source_transactions.id', ondelete='CASCADE'), nullable=False),
        sa.Column('amount', MONEY, nullable=False),
        sa.Column('reason', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(), nullable=False),
        sa.UniqueConstraint('transaction_id', name='uq_balance_explanation_matches_tx'),
    )
    op.create_index('ix_balance_explanation_matches_explanation_id', 'balance_explanation_matches', ['explanation_id'])


def downgrade() -> None:
    op.drop_table('balance_explanation_matches')
    op.drop_table('balance_explanations')
    op.drop_table('balance_interval_audit')
    op.drop_table('balance_intervals')
    op.drop_table('balance_snapshots')
    op.drop_table('balance_observations')
    for column in ('last_review_on', 'last_balance_error', 'last_balance_ok_at', 'last_balance_at', 'balance_requested',
                   'last_result'):
        op.drop_column('sync_state', column)
