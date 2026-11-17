"""Click ledger against a real, freshly migrated PostgreSQL (needs TEST_PG_URL): idempotent ingest with an
audit trail, delivery that can be retried safely, linking, ledger checks and discrepancies that never repeat."""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from accounting.models import Accounting, OperationType
from clicksync import engine, queries
from clicksync.core import ParsedTx, PeriodReport
from clicksync.models import Discrepancy, SourceTransaction, SourceTransactionAudit
from middleware import accounting, service, user
from test_db_layer import OWNER, OTHER, TEST_PG_URL, db  # noqa: F401  the migrated-database fixture

pytestmark = pytest.mark.skipif(not TEST_PG_URL, reason='TEST_PG_URL is not set')

D = Decimal
TODAY = date(2026, 11, 16)


def payment(amount, when, status='completed', description='LLC MADINA QANDOLAT', direction='out'):
    return ParsedTx(amount=D(amount), direction=direction, status=status, occurred_at=when, currency='UZS',
                    description=description, account='561468******6104')


class OneReport:
    def __init__(self, report):
        self.report = report

    async def open(self):
        return self.report

    async def older(self):
        return None


async def _setup():
    await user.ensure_user(OWNER, None)
    await queries.set_start_date(OWNER, date(2026, 11, 10))  # the reports below start there
    await accounting._add_new_category('кафе', OWNER, 'expense')
    return (await accounting._show_all_categories(OWNER))[0].id


async def _rows(model, *where):
    return list(await service._execute_select_command(select(model).where(*where)))


async def test_ingest_twice_stores_once_and_status_change_is_audited(db):
    await _setup()
    first = PeriodReport(date(2026, 11, 10), date(2026, 11, 16),
                         [payment('37000', datetime(2026, 11, 12, 21, 48), status='pending'),
                          payment('2500', datetime(2026, 11, 12, 19, 38), description='OOO ATTO TOLOV'),
                          payment('2500', datetime(2026, 11, 12, 19, 38), description='OOO ATTO TOLOV')])
    await engine.run_sync(OneReport(first), queries.DbStore(OWNER), TODAY, 7)
    again = PeriodReport(first.start, first.end, [payment('37000', datetime(2026, 11, 12, 21, 48))] + first.transactions[1:])
    await engine.run_sync(OneReport(again), queries.DbStore(OWNER), TODAY, 7)
    await engine.run_sync(OneReport(again), queries.DbStore(OWNER), TODAY, 7)

    rows = await _rows(SourceTransaction, SourceTransaction.user_id == OWNER)
    assert sorted((r.amount, r.status, r.ambiguous) for r in rows) == [
        (D('2500'), 'completed', True), (D('2500'), 'completed', True), (D('37000'), 'completed', False)]
    audit = await _rows(SourceTransactionAudit)
    assert [(a.field, a.old_value, a.new_value, a.reason) for a in audit] == [('status', 'pending', 'completed', 'source_report')]
    assert (await queries.period_summary(OWNER))['counts'] == {'completed': 1}
    assert (await queries.get_state(OWNER)).history_complete


async def test_interrupted_period_is_recovered_on_the_next_run(db):
    await _setup()
    store = queries.DbStore(OWNER)
    await store.begin_period(date(2026, 11, 3), date(2026, 11, 9))    # the process died here

    assert await store.recover_interrupted() == 1
    assert await store.unfinished_starts() == [date(2026, 11, 3)]


async def test_delivery_is_atomic_and_safe_to_retry(db):
    category_id = await _setup()
    await engine.run_sync(OneReport(PeriodReport(date(2026, 11, 10), date(2026, 11, 16),
                                                 [payment('37000', datetime(2026, 11, 12, 21, 48))])),
                          queries.DbStore(OWNER), TODAY, 7)
    tx = await queries.next_pending(OWNER)
    args = (OWNER, tx.id, category_id, D('37000'), OperationType.expenditure, tx.description, datetime(2026, 11, 12, 16, 48))

    assert await queries.deliver(*args) == 'ok'
    assert await queries.deliver(*args) == 'stale'      # double click / retry: nothing new
    operations = await _rows(Accounting, Accounting.user_id == OWNER)
    assert len(operations) == 1 and operations[0].id == queries.delivery_id(OWNER, tx.id)
    assert await queries.pending_count(OWNER) == 0

    # The mark is lost (as if the process died after the operation was written): the retry must not add one.
    await service._execute_insert_update_delete_command(
        SourceTransaction.__table__.update().where(SourceTransaction.id == tx.id).values(delivery='new', accounting_id=None))
    assert await queries.deliver(*args) == 'conflict'
    assert len(await _rows(Accounting, Accounting.user_id == OWNER)) == 1
    assert await queries.link(OWNER, tx.id, queries.delivery_id(OWNER, tx.id), reason='recovered_delivery')
    assert (await queries.get_transaction(OWNER, tx.id)).delivery == 'linked'


async def test_unsigned_file_row_gets_its_direction_once_without_a_second_row(db):
    await _setup()
    month = (date(2026, 11, 1), date(2026, 11, 30))
    unsigned = payment('60000', datetime(2026, 11, 16, 18, 7, 41), description='OOO YANDEXGO UB', direction='unknown')
    await engine.run_sync(OneReport(PeriodReport(*month, [unsigned])), queries.DbStore(OWNER), TODAY, 7)
    tx = await queries.next_pending(OWNER)

    assert await queries.set_direction(OWNER, tx.id, 'out')
    assert not await queries.set_direction(OWNER, tx.id, 'in')      # only while it is still unknown
    assert not await queries.set_direction(OTHER, tx.id, 'in')
    await engine.run_sync(OneReport(PeriodReport(*month, [unsigned])), queries.DbStore(OWNER), TODAY, 7)

    rows = await _rows(SourceTransaction, SourceTransaction.user_id == OWNER)
    assert [(r.direction, r.delivery) for r in rows] == [('out', 'new')]
    audit = await _rows(SourceTransactionAudit)
    assert [(a.field, a.new_value, a.reason) for a in audit] == [('direction', 'out', 'user_set')]


async def test_failed_payments_are_never_offered(db):
    await _setup()
    report = PeriodReport(date(2026, 11, 10), date(2026, 11, 16), [payment('99000', datetime(2026, 11, 12, 18, 0), status='failed')])
    await engine.run_sync(OneReport(report), queries.DbStore(OWNER), TODAY, 7)
    assert await queries.pending_count(OWNER) == 0 and await queries.next_pending(OWNER) is None


async def test_exact_matches_link_automatically_and_others_are_candidates(db):
    category_id = await _setup()
    # Forwarded earlier: saved with the merchant as the comment at the payment's minute (UTC).
    await accounting._add_operation(OWNER, category_id, D('37000.00'), OperationType.expenditure,
                                    comment='LLC MADINA QANDOLAT', created_at=datetime(2026, 11, 12, 16, 48))
    await accounting._add_operation(OWNER, category_id, D('2500.00'), OperationType.expenditure,
                                    comment='такси', created_at=datetime(2026, 11, 12, 15, 0))
    report = PeriodReport(date(2026, 11, 10), date(2026, 11, 16),
                          [payment('37000', datetime(2026, 11, 12, 21, 48)),
                           payment('2500', datetime(2026, 11, 12, 19, 38), description='OOO ATTO TOLOV')])
    await engine.run_sync(OneReport(report), queries.DbStore(OWNER), TODAY, 7)

    linked = await queries.auto_link_exact(OWNER, 'UZS', lambda printed: printed - timedelta(hours=5))

    assert linked == 1 and await queries.pending_count(OWNER) == 1
    atto = await queries.next_pending(OWNER)
    found = await queries.candidates(OWNER, atto.amount, 'UZS', 'UZS', datetime(2026, 11, 12, 14, 38))
    assert [op.comment for op in found] == ['такси']
    assert await queries.link(OWNER, atto.id, found[0].id)
    assert not await queries.link(OWNER, atto.id, found[0].id)      # already linked
    assert await queries.candidates(OWNER, atto.amount, 'UZS', 'UZS', None) == []   # each operation used once


async def test_other_users_operations_cannot_be_linked(db):
    await _setup()
    await user.ensure_user(OTHER, None)
    await accounting._add_new_category('кафе', OTHER, 'expense')
    other_category = (await accounting._show_all_categories(OTHER))[0].id
    other_op = await accounting._add_operation(OTHER, other_category, D('37000.00'), OperationType.expenditure)
    await engine.run_sync(OneReport(PeriodReport(date(2026, 11, 10), date(2026, 11, 16),
                                                 [payment('37000', datetime(2026, 11, 12, 21, 48))])),
                          queries.DbStore(OWNER), TODAY, 7)
    tx = await queries.next_pending(OWNER)

    assert not await queries.link(OWNER, tx.id, other_op)
    assert await queries.candidates(OWNER, D('37000'), 'UZS', 'UZS', None) == []


async def test_deleted_operation_raises_one_discrepancy_and_can_be_requeued(db):
    category_id = await _setup()
    await engine.run_sync(OneReport(PeriodReport(date(2026, 11, 10), date(2026, 11, 16),
                                                 [payment('37000', datetime(2026, 11, 12, 21, 48))])),
                          queries.DbStore(OWNER), TODAY, 7)
    tx = await queries.next_pending(OWNER)
    await queries.deliver(OWNER, tx.id, category_id, D('37000'), OperationType.expenditure, tx.description, datetime(2026, 11, 12, 16, 48))
    from history.queries import delete_operation
    assert await delete_operation(OWNER, queries.delivery_id(OWNER, tx.id))

    assert await queries.check_ledger(OWNER, 'UZS') == 1
    assert await queries.check_ledger(OWNER, 'UZS') == 0    # the same finding is not raised again
    [d] = await queries.unnotified_discrepancies(OWNER)
    assert d.kind == 'accounting_missing' and d.transaction_id == tx.id

    assert await queries.requeue(OWNER, tx.id)
    assert await queries.pending_count(OWNER) == 1


async def test_amount_edited_in_accounting_is_reported_not_changed(db):
    category_id = await _setup()
    await engine.run_sync(OneReport(PeriodReport(date(2026, 11, 10), date(2026, 11, 16),
                                                 [payment('37000', datetime(2026, 11, 12, 21, 48))])),
                          queries.DbStore(OWNER), TODAY, 7)
    tx = await queries.next_pending(OWNER)
    await queries.deliver(OWNER, tx.id, category_id, D('37000'), OperationType.expenditure, tx.description, datetime(2026, 11, 12, 16, 48))
    from history.queries import update_operation
    await update_operation(OWNER, queries.delivery_id(OWNER, tx.id), quantity=D('3700.00'))

    assert await queries.check_ledger(OWNER, 'UZS') == 1
    [d] = await _rows(Discrepancy, Discrepancy.user_id == OWNER)
    assert (d.kind, d.expected, d.reported, d.difference) == ('amount_differs', D('37000'), D('3700'), D('-33300'))
    operation = (await _rows(Accounting, Accounting.user_id == OWNER))[0]
    assert operation.quantity == D('3700.00')     # untouched: the user decides

    assert await queries.close_discrepancy(OWNER, d.id, 'unresolved', 'left_unresolved')
    assert await queries.check_ledger(OWNER, 'UZS') == 0
    assert [x.status for x in await queries.open_discrepancies(OWNER)] == ['unresolved']
