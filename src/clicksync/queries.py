"""Database side of the import: the userbot's store, delivery into accounting and what the bot shows.

Every query is scoped to the user. Delivery is one transaction with a deterministic operation id, so a
retry after any failure either does nothing or completes; it can never create a second operation.
"""
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from accounting.models import Accounting, OperationType
from middleware import service
from timeutils import utc_now
from user.models import User
from .core import IngestPlan, PeriodReport, StoredTx, fingerprint
from .engine import Finding
from .models import Discrepancy, SourceTransaction, SourceTransactionAudit, SyncPeriod, SyncState

SOURCE = 'clickuz'
# Statuses a transaction may be offered for recording with ('unknown' is shown with a warning).
OFFERABLE = ('completed', 'unknown')
_DELIVERY_NAMESPACE = uuid.UUID('6c1f0e52-9a41-4f0b-8f1e-5d2f3a7b9c10')


def delivery_id(user_id: int, transaction_id: int, source: str = SOURCE) -> uuid.UUID:
    """Id of the accounting operation created from a source transaction: always the same for the same one."""
    return uuid.uuid5(_DELIVERY_NAMESPACE, f'{source}:{user_id}:{transaction_id}')


def _session():
    return service.async_sessioin_maker()


def _text(value: Any) -> Optional[str]:
    return None if value is None else str(value)[:255]


def _audit(transaction_id: int, field: str, old: Any, new: Any, reason: str):
    return insert(SourceTransactionAudit).values(transaction_id=transaction_id, field=field, old_value=_text(old),
                                                 new_value=_text(new), reason=reason, changed_at=utc_now())


def _finding_insert(user_id: int, source: str, finding: Finding, period_id: Optional[int] = None):
    return (insert(Discrepancy)
            .values(user_id=user_id, source=source, kind=finding.kind, fingerprint=finding.fingerprint,
                    account=finding.account, currency=finding.currency, period_id=period_id,
                    transaction_id=finding.transaction_id, expected=finding.expected, reported=finding.reported,
                    difference=finding.difference, details=finding.details, status='open', created_at=utc_now())
            .on_conflict_do_nothing(constraint='uq_sync_discrepancies_fingerprint'))


# --- State (shared by the bot and the userbot) ------------------------------------------------

async def ensure_state(user_id: int, source: str = SOURCE) -> None:
    async with _session() as session, session.begin():
        await session.execute(insert(User).values(id=user_id).on_conflict_do_nothing(index_elements=['id']))
        await session.execute(insert(SyncState).values(user_id=user_id, source=source)
                              .on_conflict_do_nothing(index_elements=['user_id', 'source']))


async def get_state(user_id: int, source: str = SOURCE) -> Optional[SyncState]:
    statement = select(SyncState).where(SyncState.user_id == user_id, SyncState.source == source)
    async with _session() as session, session.begin():
        return (await session.execute(statement)).scalar()


async def _set_state(user_id: int, source: str = SOURCE, **values) -> None:
    await ensure_state(user_id, source)
    statement = update(SyncState).where(SyncState.user_id == user_id, SyncState.source == source).values(**values)
    await service._execute_insert_update_delete_command(statement)


async def set_start_date(user_id: int, start: date) -> None:
    # A new boundary means the history has to be walked again down to it.
    await _set_state(user_id, start_date=start, history_complete=False)


async def set_paused(user_id: int, paused: bool) -> None:
    await _set_state(user_id, paused=paused)


async def request_sync(user_id: int) -> None:
    await _set_state(user_id, sync_requested=True)


async def heartbeat(user_id: int) -> None:
    await _set_state(user_id, heartbeat_at=utc_now())


async def mark_started(user_id: int) -> None:
    await _set_state(user_id, sync_requested=False, last_started_at=utc_now())


async def mark_finished(user_id: int, error: Optional[str]) -> None:
    now = utc_now()
    values: Dict[str, Any] = {'last_finished_at': now, 'last_error': error[:255] if error else None}
    if not error:
        values['last_ok_at'] = now
    await _set_state(user_id, **values)


# --- The engine's store -------------------------------------------------------------------------

class DbStore:
    """clicksync.engine.Store on PostgreSQL."""

    def __init__(self, user_id: int, source: str = SOURCE) -> None:
        self.user_id = user_id
        self.source = source

    def _periods(self):
        return and_(SyncPeriod.user_id == self.user_id, SyncPeriod.source == self.source)

    async def start_date(self) -> Optional[date]:
        state = await get_state(self.user_id, self.source)
        return state.start_date if state else None

    async def history_complete(self) -> bool:
        state = await get_state(self.user_id, self.source)
        return bool(state and state.history_complete)

    async def recover_interrupted(self) -> int:
        statement = (update(SyncPeriod).where(self._periods(), SyncPeriod.status == 'in_progress')
                     .values(status='incomplete', note='синхронизация была прервана'))
        return (await service._execute_insert_update_delete_command(statement)).rowcount

    async def unfinished_starts(self) -> List[date]:
        statement = select(SyncPeriod.period_start).where(self._periods(), SyncPeriod.status != 'completed')
        return list(await service._execute_select_command(statement))

    async def begin_period(self, start: date, end: date) -> int:
        now = utc_now()
        statement = (insert(SyncPeriod)
                     .values(user_id=self.user_id, source=self.source, period_start=start, period_end=end,
                             status='in_progress', attempts=1, started_at=now)
                     .on_conflict_do_update(constraint='uq_sync_periods_range',
                                            set_={'status': 'in_progress', 'attempts': SyncPeriod.attempts + 1,
                                                  'started_at': now, 'note': None})
                     .returning(SyncPeriod.id))
        return await service._execute_scalar(statement)

    async def existing(self, keys: Sequence[str]) -> Dict[str, StoredTx]:
        if not keys:
            return {}
        statement = select(SourceTransaction).where(
            SourceTransaction.user_id == self.user_id, SourceTransaction.source == self.source,
            SourceTransaction.dedup_key.in_(list(keys)))
        rows = await service._execute_select_command(statement)
        return {row.dedup_key: StoredTx(id=row.id, dedup_key=row.dedup_key, status=row.status, amount=row.amount,
                                        delivery=row.delivery, fee=row.fee, fee_included=row.fee_included,
                                        description=row.description, source_id=row.source_id, ambiguous=row.ambiguous,
                                        direction=row.direction)
                for row in rows}

    async def apply(self, period_id: int, report: PeriodReport, plan: IngestPlan, status: str,
                    note: Optional[str], findings: List[Finding]) -> None:
        """New rows, corrections with their audit trail, findings and the period's result: all or nothing."""
        now = utc_now()
        async with _session() as session, session.begin():
            for key, ambiguous, tx in plan.inserts:
                await session.execute(
                    insert(SourceTransaction)
                    .values(user_id=self.user_id, source=self.source, source_id=tx.source_id, dedup_key=key,
                            occurred_at=tx.occurred_at, amount=tx.amount, currency=tx.currency, direction=tx.direction,
                            status=tx.status, description=(tx.description or None) and tx.description[:255],
                            fee=tx.fee, fee_included=tx.fee_included, account=tx.account, ambiguous=ambiguous,
                            own_transfer=False, delivery='new', period_id=period_id, first_seen_at=now, updated_at=now)
                    .on_conflict_do_nothing(constraint='uq_source_transactions_key'))
            for change in plan.updates:
                values = {name: new for name, (_old, new) in change.changes.items()}
                await session.execute(update(SourceTransaction)
                                      .where(SourceTransaction.id == change.stored.id, SourceTransaction.user_id == self.user_id)
                                      .values(updated_at=now, **values))
                for name, (old, new) in change.changes.items():
                    await session.execute(_audit(change.stored.id, name, old, new, 'source_report'))
            for finding in findings:
                await session.execute(_finding_insert(self.user_id, self.source, finding, period_id))
            await session.execute(
                update(SyncPeriod).where(SyncPeriod.id == period_id, self._periods())
                .values(status=status, note=note[:255] if note else None, pages=report.pages,
                        tx_count=len(report.transactions), opening_balance=report.opening_balance,
                        closing_balance=report.closing_balance, balance_account=report.balance_account,
                        balance_currency=report.balance_currency, finished_at=now))

    async def fail_period(self, period_id: int, note: str, finding: Finding) -> None:
        async with _session() as session, session.begin():
            await session.execute(update(SyncPeriod).where(SyncPeriod.id == period_id, self._periods())
                                  .values(status='failed', note=note[:255], finished_at=utc_now()))
            await session.execute(_finding_insert(self.user_id, self.source, finding, period_id))

    async def raise_finding(self, finding: Finding) -> None:
        await service._execute_insert_update_delete_command(_finding_insert(self.user_id, self.source, finding))

    async def set_history_complete(self) -> None:
        await _set_state(self.user_id, self.source, history_complete=True)


# --- Classification queue and delivery ----------------------------------------------------------

def _pending(user_id: int):
    return and_(SourceTransaction.user_id == user_id, SourceTransaction.source == SOURCE,
                SourceTransaction.delivery == 'new', SourceTransaction.status.in_(OFFERABLE))


async def pending_count(user_id: int) -> int:
    return await service._execute_scalar(select(func.count(SourceTransaction.id)).where(_pending(user_id))) or 0


async def next_pending(user_id: int) -> Optional[SourceTransaction]:
    statement = (select(SourceTransaction).where(_pending(user_id))
                 .order_by(SourceTransaction.occurred_at.asc().nulls_last(), SourceTransaction.id).limit(1))
    rows = await service._execute_select_command(statement)
    return rows[0] if rows else None


async def get_transaction(user_id: int, transaction_id: int) -> Optional[SourceTransaction]:
    statement = select(SourceTransaction).where(SourceTransaction.id == transaction_id, SourceTransaction.user_id == user_id)
    rows = await service._execute_select_command(statement)
    return rows[0] if rows else None


class _NotClaimed(Exception):
    pass


async def deliver(user_id: int, transaction_id: int, category_id: int, quantity: Decimal, operation_type: OperationType,
                  comment: Optional[str], created_at: datetime, original_amount: Optional[Decimal] = None,
                  original_currency: Optional[str] = None, rate: Optional[Decimal] = None) -> str:
    """Creates the accounting operation and marks the transaction delivered, atomically.

    'ok': recorded. 'stale': the transaction is no longer waiting (already handled). 'conflict': an operation
    with this transaction's id already exists although the transaction says it was not delivered; nothing
    is written; the caller links the existing operation instead of writing it again.
    """
    operation_id = delivery_id(user_id, transaction_id)
    try:
        async with _session() as session, session.begin():
            # Locked first: a second click waits here and then sees the transaction already delivered.
            waiting = (await session.execute(
                select(SourceTransaction.id).where(SourceTransaction.id == transaction_id, _pending(user_id))
                .with_for_update())).scalar()
            if waiting is None:
                raise _NotClaimed()
            await session.execute(insert(Accounting).values(
                id=operation_id, user_id=user_id, quantity=quantity, category_id=category_id,
                operation_type=operation_type, comment=comment, created_at=created_at,
                original_amount=original_amount, original_currency=original_currency, rate=rate))
            await session.execute(
                update(SourceTransaction).where(SourceTransaction.id == transaction_id)
                .values(delivery='delivered', accounting_id=operation_id, updated_at=utc_now()))
            await session.execute(_audit(transaction_id, 'delivery', 'new', 'delivered', 'user_classified'))
    except _NotClaimed:
        return 'stale'
    except IntegrityError:
        return 'conflict'
    return 'ok'


def _unlinked(user_id: int):
    """Accounting operations of the user that no source transaction points at yet."""
    taken = select(SourceTransaction.accounting_id).where(SourceTransaction.accounting_id.isnot(None))
    return and_(Accounting.user_id == user_id, Accounting.id.notin_(taken))


def _same_amount(amount: Decimal, currency: Optional[str], user_currency: Optional[str]):
    """The operation holds this source amount: directly, or as the original amount of a converted one."""
    converted = and_(Accounting.original_amount == amount, Accounting.original_currency == currency)
    if currency and user_currency and currency != user_currency:
        return converted
    return or_(and_(Accounting.quantity == amount, Accounting.original_amount.is_(None)), converted)


async def link(user_id: int, transaction_id: int, operation_id: uuid.UUID, reason: str = 'user_linked') -> bool:
    """Ties a waiting transaction to an existing operation of the same user that is not linked yet."""
    async with _session() as session, session.begin():
        free = (await session.execute(select(Accounting.id).where(Accounting.id == operation_id, _unlinked(user_id)))).scalar()
        if free is None:
            return False
        claimed = (await session.execute(
            update(SourceTransaction).where(SourceTransaction.id == transaction_id, _pending(user_id))
            .values(delivery='linked', accounting_id=operation_id, updated_at=utc_now())
            .returning(SourceTransaction.id))).scalar()
        if claimed is None:
            return False
        await session.execute(_audit(transaction_id, 'delivery', 'new', 'linked', reason))
    return True


async def ignore(user_id: int, transaction_id: int, own_transfer: bool = False) -> bool:
    async with _session() as session, session.begin():
        claimed = (await session.execute(
            update(SourceTransaction).where(SourceTransaction.id == transaction_id, _pending(user_id))
            .values(delivery='ignored', own_transfer=own_transfer, updated_at=utc_now())
            .returning(SourceTransaction.id))).scalar()
        if claimed is None:
            return False
        await session.execute(_audit(transaction_id, 'delivery', 'new', 'ignored',
                                     'own_transfer' if own_transfer else 'user_skipped'))
    return True


async def set_direction(user_id: int, transaction_id: int, direction: str) -> bool:
    """The owner says which way a payment without a sign went (the monthly file has none). Only while it waits."""
    if direction not in ('in', 'out'):
        return False
    async with _session() as session, session.begin():
        claimed = (await session.execute(
            update(SourceTransaction)
            .where(SourceTransaction.id == transaction_id, _pending(user_id), SourceTransaction.direction == 'unknown')
            .values(direction=direction, updated_at=utc_now())
            .returning(SourceTransaction.id))).scalar()
        if claimed is None:
            return False
        await session.execute(_audit(transaction_id, 'direction', 'unknown', direction, 'user_set'))
    return True


async def requeue(user_id: int, transaction_id: int) -> bool:
    """Back to the queue: only for a transaction that has no operation any more (ignored, or its operation was deleted)."""
    async with _session() as session, session.begin():
        claimed = (await session.execute(
            update(SourceTransaction)
            .where(SourceTransaction.id == transaction_id, SourceTransaction.user_id == user_id,
                   SourceTransaction.delivery != 'new', SourceTransaction.accounting_id.is_(None))
            .values(delivery='new', own_transfer=False, updated_at=utc_now())
            .returning(SourceTransaction.id))).scalar()
        if claimed is None:
            return False
        await session.execute(_audit(transaction_id, 'delivery', None, 'new', 'requeued'))
    return True


async def candidates(user_id: int, amount: Decimal, currency: Optional[str], user_currency: Optional[str],
                     around: Optional[datetime], days: int = 3, limit: int = 5) -> List[Accounting]:
    """Unlinked operations with the same amount near that time (UTC), closest first."""
    conditions = [_unlinked(user_id), _same_amount(amount, currency, user_currency)]
    statement = select(Accounting).where(*conditions)
    if around is not None:
        statement = statement.where(Accounting.created_at.between(around - timedelta(days=days), around + timedelta(days=days)))
        statement = statement.order_by(func.abs(func.extract('epoch', Accounting.created_at - around)))
    else:
        statement = statement.order_by(Accounting.created_at.desc())
    return list(await service._execute_select_command(statement.limit(limit)))


async def auto_link_exact(user_id: int, user_currency: Optional[str], to_utc) -> int:
    """Links waiting transactions to operations already recorded from the same payment.

    Exact match only: same amount, same minute and the merchant as the comment, which is how forwarded bank
    messages are saved. Each operation is used once, so two equal payments need two operations.
    """
    statement = select(SourceTransaction).where(_pending(user_id), SourceTransaction.occurred_at.isnot(None),
                                                SourceTransaction.description.isnot(None))
    linked = 0
    for tx in await service._execute_select_command(statement):
        # The file prints seconds, a forwarded message only minutes: compare by the minute.
        created_at = to_utc(tx.occurred_at.replace(second=0, microsecond=0))
        conditions = [_unlinked(user_id), _same_amount(tx.amount, tx.currency, user_currency),
                      Accounting.created_at >= created_at, Accounting.created_at < created_at + timedelta(minutes=1),
                      Accounting.comment == tx.description]
        if tx.direction in ('in', 'out'):   # unknown: the recorded operation's own type is taken as it is
            conditions.append(Accounting.operation_type == (OperationType.income if tx.direction == 'in'
                                                            else OperationType.expenditure))
        match = select(Accounting.id).where(*conditions).order_by(Accounting.created_at).limit(1)
        operation_id = await service._execute_scalar(match)
        if operation_id is not None and await link(user_id, tx.id, operation_id, reason='auto_link_exact'):
            linked += 1
    return linked


async def remembered_category(user_id: int, description: Optional[str], kind: str) -> Optional[int]:
    from statements.queries import category_for_merchant
    return await category_for_merchant(user_id, description, kind)


# --- Status, reconciliation, discrepancies ------------------------------------------------------

async def period_summary(user_id: int) -> Dict[str, Any]:
    where = and_(SyncPeriod.user_id == user_id, SyncPeriod.source == SOURCE)
    counts = await service._execute_select_rows(select(SyncPeriod.status, func.count(SyncPeriod.id)).where(where).group_by(SyncPeriod.status))
    last_ok = await service._execute_select_command(
        select(SyncPeriod).where(where, SyncPeriod.status == 'completed').order_by(SyncPeriod.period_end.desc()).limit(1))
    problems = await service._execute_select_command(
        select(SyncPeriod).where(where, SyncPeriod.status.in_(('failed', 'incomplete'))).order_by(SyncPeriod.period_start).limit(10))
    earliest = await service._execute_scalar(select(func.min(SyncPeriod.period_start)).where(where, SyncPeriod.status == 'completed'))
    return {'counts': dict(counts), 'last_ok': last_ok[0] if last_ok else None, 'problems': list(problems), 'earliest': earliest}


async def delivery_counts(user_id: int) -> Dict[str, int]:
    statement = (select(SourceTransaction.delivery, func.count(SourceTransaction.id))
                 .where(SourceTransaction.user_id == user_id, SourceTransaction.source == SOURCE)
                 .group_by(SourceTransaction.delivery))
    return dict(await service._execute_select_rows(statement))


async def all_transactions(user_id: int) -> List[SourceTransaction]:
    statement = (select(SourceTransaction).where(SourceTransaction.user_id == user_id, SourceTransaction.source == SOURCE)
                 .order_by(SourceTransaction.occurred_at.asc().nulls_last(), SourceTransaction.id))
    return list(await service._execute_select_command(statement))


async def periods_with_balances(user_id: int) -> int:
    statement = select(func.count(SyncPeriod.id)).where(
        SyncPeriod.user_id == user_id, SyncPeriod.source == SOURCE,
        SyncPeriod.opening_balance.isnot(None), SyncPeriod.closing_balance.isnot(None))
    return await service._execute_scalar(statement) or 0


async def last_accounting_date(user_id: int) -> Optional[datetime]:
    return await service._execute_scalar(select(func.max(Accounting.created_at)).where(Accounting.user_id == user_id))


async def check_ledger(user_id: int, user_currency: Optional[str]) -> int:
    """Compares the source ledger with accounting and raises a discrepancy for every recorded transaction
    whose operation was deleted or now holds another amount. Returns how many new ones were raised."""
    recorded = and_(SourceTransaction.user_id == user_id, SourceTransaction.source == SOURCE,
                    SourceTransaction.delivery.in_(('delivered', 'linked')))
    findings: List[Finding] = []
    for tx in await service._execute_select_command(select(SourceTransaction).where(recorded, SourceTransaction.accounting_id.is_(None))):
        findings.append(Finding(kind='accounting_missing', transaction_id=tx.id, account=tx.account, currency=tx.currency,
                                fingerprint=fingerprint('missing', tx.id, tx.updated_at),
                                details='Операция была записана в учёт, но этой записи больше нет (её удалили).'))
    rows = await service._execute_select_rows(
        select(SourceTransaction, Accounting).join(Accounting, Accounting.id == SourceTransaction.accounting_id).where(recorded))
    for tx, operation in rows:
        same_currency = not (tx.currency and user_currency and tx.currency != user_currency)
        held = operation.original_amount if operation.original_amount is not None else (operation.quantity if same_currency else None)
        if held is not None and held != tx.amount:
            findings.append(Finding(kind='amount_differs', transaction_id=tx.id, account=tx.account, currency=tx.currency,
                                    expected=tx.amount, reported=held, difference=held - tx.amount,
                                    fingerprint=fingerprint('amount', tx.id, tx.amount, held),
                                    details='Сумма в учёте отличается от суммы в отчёте Click.'))
    raised = 0
    for finding in findings:
        result = await service._execute_insert_update_delete_command(_finding_insert(user_id, SOURCE, finding))
        raised += result.rowcount or 0
    return raised


def _discrepancies(user_id: int):
    return and_(Discrepancy.user_id == user_id, Discrepancy.source == SOURCE)


async def open_discrepancies(user_id: int, limit: int = 20) -> List[Discrepancy]:
    statement = (select(Discrepancy).where(_discrepancies(user_id), Discrepancy.status.in_(('open', 'unresolved')))
                 .order_by(Discrepancy.status, Discrepancy.id).limit(limit))
    return list(await service._execute_select_command(statement))


async def unnotified_discrepancies(user_id: int) -> List[Discrepancy]:
    statement = (select(Discrepancy).where(_discrepancies(user_id), Discrepancy.status == 'open', Discrepancy.notified_at.is_(None))
                 .order_by(Discrepancy.id))
    return list(await service._execute_select_command(statement))


async def mark_notified(user_id: int, ids: Sequence[int]) -> None:
    if ids:
        await service._execute_insert_update_delete_command(
            update(Discrepancy).where(_discrepancies(user_id), Discrepancy.id.in_(list(ids))).values(notified_at=utc_now()))


async def get_discrepancy(user_id: int, discrepancy_id: int) -> Optional[Discrepancy]:
    rows = await service._execute_select_command(select(Discrepancy).where(_discrepancies(user_id), Discrepancy.id == discrepancy_id))
    return rows[0] if rows else None


async def get_period(user_id: int, period_id: Optional[int]) -> Optional[SyncPeriod]:
    if period_id is None:
        return None
    rows = await service._execute_select_command(select(SyncPeriod).where(SyncPeriod.id == period_id, SyncPeriod.user_id == user_id))
    return rows[0] if rows else None


async def period_transactions(user_id: int, period_id: int) -> List[SourceTransaction]:
    statement = select(SourceTransaction).where(SourceTransaction.user_id == user_id, SourceTransaction.period_id == period_id)
    return list(await service._execute_select_command(statement))


async def close_discrepancy(user_id: int, discrepancy_id: int, status: str, resolution: str) -> bool:
    """status: 'resolved' or 'unresolved' (the user chose to leave it; it is kept but not raised again)."""
    statement = (update(Discrepancy).where(_discrepancies(user_id), Discrepancy.id == discrepancy_id)
                 .values(status=status, resolution=resolution, resolved_at=utc_now() if status == 'resolved' else None))
    return (await service._execute_insert_update_delete_command(statement)).rowcount == 1


async def relink_recorded(user_id: int, transaction_id: int, operation_id: uuid.UUID) -> bool:
    """For a recorded transaction whose operation is gone: point it at another existing, unlinked operation."""
    async with _session() as session, session.begin():
        free = (await session.execute(select(Accounting.id).where(Accounting.id == operation_id, _unlinked(user_id)))).scalar()
        if free is None:
            return False
        claimed = (await session.execute(
            update(SourceTransaction)
            .where(SourceTransaction.id == transaction_id, SourceTransaction.user_id == user_id,
                   SourceTransaction.accounting_id.is_(None))
            .values(delivery='linked', accounting_id=operation_id, updated_at=utc_now())
            .returning(SourceTransaction.id))).scalar()
        if claimed is None:
            return False
        await session.execute(_audit(transaction_id, 'delivery', None, 'linked', 'user_linked'))
    return True
