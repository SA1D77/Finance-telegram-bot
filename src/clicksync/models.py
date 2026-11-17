"""Ledger of transactions read from an external source (the @clickuz reports) and the state of that import.

Kept apart from `accounting`: a source transaction is what the source reported, an accounting operation is
what the user decided to record. `delivery` links the two.
"""
from sqlalchemy import (
    BigInteger, Boolean, Column, Date, ForeignKey, Integer, Numeric, String, Text, TIMESTAMP, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID

from accounting.models import Accounting
from timeutils import utc_now
from user.models import Base, User

# Exact, wider than accounting.MONEY: the source's units and precision are kept as printed.
SOURCE_MONEY = Numeric(20, 4)

DIRECTIONS = ('in', 'out', 'unknown')
STATUSES = ('completed', 'pending', 'failed', 'reversed', 'unknown')
# new: waits for the user's decision; delivered: an accounting operation was created from it;
# linked: tied to an operation that already existed; ignored: the user chose not to record it.
DELIVERIES = ('new', 'delivered', 'linked', 'ignored')
PERIOD_STATUSES = ('in_progress', 'completed', 'incomplete', 'failed')
DISCREPANCY_STATUSES = ('open', 'resolved', 'unresolved')


class SyncPeriod(Base):
    """One report period as the source shows it. A period that is not `completed` is read again."""
    __tablename__ = 'sync_periods'
    __table_args__ = (UniqueConstraint('user_id', 'source', 'period_start', 'period_end', name='uq_sync_periods_range'),)

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey(User.id), nullable=False, index=True)
    source = Column(String(16), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    status = Column(String(12), nullable=False)
    pages = Column(Integer, nullable=False, default=0)
    tx_count = Column(Integer, nullable=False, default=0)
    # Only when the source prints them; NULL means "not provided", never zero.
    opening_balance = Column(SOURCE_MONEY, nullable=True)
    closing_balance = Column(SOURCE_MONEY, nullable=True)
    balance_account = Column(String(32), nullable=True)
    balance_currency = Column(String(8), nullable=True)
    note = Column(String(255), nullable=True)   # why it is incomplete / failed (no amounts)
    attempts = Column(Integer, nullable=False, default=0)
    started_at = Column(TIMESTAMP, nullable=True)
    finished_at = Column(TIMESTAMP, nullable=True)


class SourceTransaction(Base):
    __tablename__ = 'source_transactions'
    __table_args__ = (UniqueConstraint('user_id', 'source', 'dedup_key', name='uq_source_transactions_key'),)

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey(User.id), nullable=False, index=True)
    source = Column(String(16), nullable=False)
    source_id = Column(String(64), nullable=True)      # the source's own transaction id, when it has one
    dedup_key = Column(String(80), nullable=False)     # see clicksync.core.dedup_keys
    occurred_at = Column(TIMESTAMP, nullable=True)     # wall-clock time exactly as printed, no zone applied
    amount = Column(SOURCE_MONEY, nullable=False)      # positive, in the source's units
    currency = Column(String(8), nullable=True)
    direction = Column(String(8), nullable=False)
    status = Column(String(12), nullable=False)
    description = Column(String(255), nullable=True)
    fee = Column(SOURCE_MONEY, nullable=True)
    fee_included = Column(Boolean, nullable=True)      # NULL: the source does not say
    account = Column(String(32), nullable=True)        # masked card / account
    ambiguous = Column(Boolean, nullable=False, default=False)     # had an identical twin in the same report
    own_transfer = Column(Boolean, nullable=False, default=False)  # between the user's own accounts
    delivery = Column(String(12), nullable=False, default='new')
    accounting_id = Column(UUID(as_uuid=True), ForeignKey(Accounting.id, ondelete='SET NULL'), nullable=True)
    period_id = Column(Integer, ForeignKey(SyncPeriod.id, ondelete='SET NULL'), nullable=True)
    first_seen_at = Column(TIMESTAMP, nullable=False, default=utc_now)
    updated_at = Column(TIMESTAMP, nullable=False, default=utc_now)


class SourceTransactionAudit(Base):
    """Every change of a stored source transaction: what, from, to and why."""
    __tablename__ = 'source_transaction_audit'

    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey(SourceTransaction.id, ondelete='CASCADE'), nullable=False, index=True)
    changed_at = Column(TIMESTAMP, nullable=False, default=utc_now)
    field = Column(String(24), nullable=False)
    old_value = Column(String(255), nullable=True)
    new_value = Column(String(255), nullable=True)
    reason = Column(String(32), nullable=False)


class SyncState(Base):
    """One row per user and source: settings changed from the bot and the userbot's progress."""
    __tablename__ = 'sync_state'

    user_id = Column(BigInteger, ForeignKey(User.id), primary_key=True, autoincrement=False)
    source = Column(String(16), primary_key=True)
    start_date = Column(Date, nullable=True)            # import nothing earlier than this
    history_complete = Column(Boolean, nullable=False, default=False)  # walked back to start_date at least once
    paused = Column(Boolean, nullable=False, default=False)
    sync_requested = Column(Boolean, nullable=False, default=False)   # "sync now" pressed in the bot
    heartbeat_at = Column(TIMESTAMP, nullable=True)     # the userbot process is alive
    last_started_at = Column(TIMESTAMP, nullable=True)
    last_finished_at = Column(TIMESTAMP, nullable=True)
    last_ok_at = Column(TIMESTAMP, nullable=True)
    last_error = Column(String(255), nullable=True)


class Discrepancy(Base):
    """A question for the user. `fingerprint` keeps the same finding from being raised twice."""
    __tablename__ = 'sync_discrepancies'
    __table_args__ = (UniqueConstraint('user_id', 'source', 'fingerprint', name='uq_sync_discrepancies_fingerprint'),)

    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, ForeignKey(User.id), nullable=False, index=True)
    source = Column(String(16), nullable=False)
    kind = Column(String(24), nullable=False)
    fingerprint = Column(String(64), nullable=False)
    account = Column(String(32), nullable=True)
    currency = Column(String(8), nullable=True)
    period_id = Column(Integer, ForeignKey(SyncPeriod.id, ondelete='SET NULL'), nullable=True)
    transaction_id = Column(Integer, ForeignKey(SourceTransaction.id, ondelete='CASCADE'), nullable=True)
    expected = Column(SOURCE_MONEY, nullable=True)
    reported = Column(SOURCE_MONEY, nullable=True)
    difference = Column(SOURCE_MONEY, nullable=True)
    details = Column(Text, nullable=True)
    status = Column(String(12), nullable=False, default='open')
    resolution = Column(String(24), nullable=True)
    created_at = Column(TIMESTAMP, nullable=False, default=utc_now)
    notified_at = Column(TIMESTAMP, nullable=True)
    resolved_at = Column(TIMESTAMP, nullable=True)
