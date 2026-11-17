"""Card balance checks against a real, freshly migrated PostgreSQL (needs TEST_PG_URL): snapshots, intervals,
recalculation with an audit trail, idempotent and concurrent confirmations, matching of later payments, the
daily review claim, isolation between users and the migration on existing data."""
import asyncio
import os
import subprocess
import sys
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlparse

import pytest
from sqlalchemy import insert, select

from accounting.models import Accounting, OperationType
from clicksync import balance_queries as bq
from clicksync import queries, settings
from clicksync.balance import parse_balance_reply
from clicksync.models import (BalanceInterval, BalanceIntervalAudit, BalanceObservation, BalanceSnapshot, SourceTransaction,
                              SyncPeriod)
from middleware import accounting, service, user
from test_db_layer import ALEMBIC_DIR, OWNER, OTHER, TEST_PG_URL, db, psycopg2  # noqa: F401  the migrated-database fixture

pytestmark = pytest.mark.skipif(not TEST_PG_URL, reason='TEST_PG_URL is not set')

D = Decimal
T0 = datetime(2026, 11, 15, 16, 0)      # UTC: 21:00 in Tashkent
T1 = datetime(2026, 11, 17, 4, 0)       # UTC: 09:00 in Tashkent
LATER = T1 + timedelta(days=3)
TIMING = dict(grace=timedelta(hours=24), boundary=timedelta(minutes=10), recheck_days=45, to_utc=settings.to_utc,
              from_utc=settings.from_utc)


def reply(*cards):
    total = sum(D(amount) for _, amount in cards)
    lines = ['Общий баланс:', f'{total} сум', 'Ваши карты:']
    for label, amount in cards:
        lines += [f'💳 {label}', f'{amount} сум']
    return parse_balance_reply('\n'.join(lines))


async def _setup(owner=OWNER):
    await user.ensure_user(owner, None)
    await queries.set_start_date(owner, date(2026, 11, 1))
    await accounting._add_new_category('кафе', owner, 'expense')
    category_id = (await accounting._show_all_categories(owner))[0].id
    # The November report was read completely two days after the second check.
    await service._execute_insert_update_delete_command(insert(SyncPeriod).values(
        user_id=owner, source='clickuz', period_start=date(2026, 11, 1), period_end=date(2026, 11, 30), status='completed',
        pages=1, tx_count=0, attempts=1, started_at=T1 + timedelta(days=2), finished_at=T1 + timedelta(days=2)))
    return category_id


async def _payment(amount, local_time, owner=OWNER, account='561468******6104', direction='out', status='completed'):
    return (await service._execute_insert_update_delete_command(insert(SourceTransaction).values(
        user_id=owner, source='clickuz', dedup_key=uuid.uuid4().hex, occurred_at=local_time, amount=D(amount),
        currency='UZS', direction=direction, status=status, description='OOO SHOP', account=account,
        ambiguous=False, own_transfer=False, delivery='new').returning(SourceTransaction.id))).scalar()


async def _rows(model, *where):
    return list(await service._execute_select_command(select(model).where(*where)))


async def _two_checks(owner=OWNER, closing='950000', label='HAMK***6104'):
    await bq.save_observation(owner, T0, T0, 10, reply((label, '1000000')))
    await bq.save_observation(owner, T1, T1, 11, reply((label, closing)))
    return await bq.build_intervals(owner)


async def _interval(owner=OWNER):
    [interval] = await _rows(BalanceInterval, BalanceInterval.user_id == owner)
    return interval


async def test_first_snapshot_is_a_baseline_and_intervals_are_built_once(db):
    await _setup()
    await bq.save_observation(OWNER, T0, T0, 10, reply(('HAMK***6104', '1000000')))
    assert await bq.build_intervals(OWNER) == 0
    await bq.save_observation(OWNER, T1, T1, 11, reply(('HAMK***6104', '950000')))
    assert await bq.build_intervals(OWNER) == 1
    assert await bq.build_intervals(OWNER) == 0
    interval = await _interval()
    assert (interval.last4, interval.currency, interval.opening, interval.closing) == ('6104', 'UZS', D('1000000'), D('950000'))
    totals = await _rows(BalanceSnapshot, BalanceSnapshot.kind == 'total')
    assert len(totals) == 2     # the aggregate is stored, but never becomes an interval


async def test_rejected_reply_is_kept_without_amounts(db):
    await _setup()
    await bq.save_observation(OWNER, T0, T0, 10, reply(('HAMK***6104', '1000000')))
    await bq.save_rejection(OWNER, T1, T1, 'ответ Click на /balance не распознан: сумма скрыта')
    observations = await _rows(BalanceObservation)
    assert sorted(o.status for o in observations) == ['ok', 'rejected']
    observed_at, snapshots = await bq.latest_balances(OWNER)
    assert observed_at == T0 and [s.amount for s in snapshots if s.kind == 'card'] == [D('1000000')]


async def test_full_cycle_explain_then_the_payment_appears_and_is_not_counted_twice(db):
    category_id = await _setup()
    await _payment('1000', datetime(2026, 11, 16, 12, 0))
    await _two_checks()

    counts = await bq.recompute(OWNER, LATER, **TIMING)
    interval = await _interval()
    assert counts == {'unexplained': 1} and (interval.status, interval.remaining) == ('unexplained', D('-49000'))
    assert len(await _rows(BalanceIntervalAudit)) == 1
    await bq.recompute(OWNER, LATER, **TIMING)
    assert len(await _rows(BalanceIntervalAudit)) == 1      # unchanged: no new audit row

    operation = bq.operation_values(D('20000'), OperationType.expenditure, 'Сверка баланса ••••6104', T1, None, None, None)
    assert await bq.explain(OWNER, interval.id, 'tok1', 'expense', 'out', D('20000'), category_id, operation) == 'ok'
    assert await bq.explain(OWNER, interval.id, 'tok1', 'expense', 'out', D('20000'), category_id, operation) == 'duplicate'
    assert await bq.explain(OWNER, interval.id, 'tok2', 'expense', 'out', D('40000'), category_id, operation) == 'too_much'
    assert await bq.explain(OWNER, interval.id, 'tok3', 'income', 'in', D('1000'), category_id, operation) == 'too_much'
    operations = await _rows(Accounting, Accounting.user_id == OWNER)
    assert [op.id for op in operations] == [bq.explanation_operation_id(OWNER, 'tok1')]
    assert (await _interval()).remaining == D('-29000')

    assert await bq.explain(OWNER, interval.id, 'tok4', 'own_transfer', 'out', D('29000')) == 'ok'
    assert (await _interval()).status == 'explained'
    await bq.recompute(OWNER, LATER, **TIMING)
    assert ((await _interval()).status, (await _interval()).remaining) == ('explained', D('0'))
    assert len(await _rows(Accounting, Accounting.user_id == OWNER)) == 1

    # The explained 20 000 shows up in the next report: not new money, and not offered to be recorded again.
    tx_id = await _payment('20000', datetime(2026, 11, 16, 15, 0))
    await bq.recompute(OWNER, LATER, **TIMING)
    assert (await _interval()).status == 'uncertain'
    assert await bq.auto_match(OWNER, settings.to_utc, settings.from_utc) == 1
    await bq.recompute(OWNER, LATER, **TIMING)
    interval = await _interval()
    assert (interval.status, interval.remaining) == ('explained', D('0'))
    tx = await queries.get_transaction(OWNER, tx_id)
    assert (tx.delivery, tx.accounting_id) == ('linked', bq.explanation_operation_id(OWNER, 'tok1'))
    assert await queries.pending_count(OWNER) == 1      # only the 1 000 payment waits
    assert await queries.check_ledger(OWNER, 'UZS') == 0
    assert len(await _rows(Accounting, Accounting.user_id == OWNER)) == 1
    events = [a.event for a in await _rows(BalanceIntervalAudit)]
    assert {'computed', 'explained', 'matched', 'auto_explained'} <= set(events)


async def test_ambiguous_match_is_left_to_the_owner(db):
    await _setup()
    await _two_checks(closing='950000')
    await bq.recompute(OWNER, LATER, **TIMING)
    interval = await _interval()
    assert await bq.explain(OWNER, interval.id, 'a', 'expense', 'out', D('25000')) == 'ok'
    assert await bq.explain(OWNER, interval.id, 'b', 'expense', 'out', D('25000')) == 'ok'
    first = await _payment('25000', datetime(2026, 11, 16, 15, 0))
    assert await bq.auto_match(OWNER, settings.to_utc, settings.from_utc) == 0      # which of the two? not guessed
    candidates = await bq.explanation_candidates(OWNER, first, TIMING['boundary'], settings.to_utc)
    assert len(candidates) == 2
    assert await bq.apply_match(OWNER, candidates[0][0].id, first, 'owner', TIMING['boundary'], settings.to_utc)
    assert not await bq.apply_match(OWNER, candidates[1][0].id, first, 'owner', TIMING['boundary'], settings.to_utc)


async def test_partial_match_of_one_explanation_by_two_payments(db):
    await _setup()
    await _two_checks(closing='950000')
    await bq.recompute(OWNER, LATER, **TIMING)
    interval = await _interval()
    assert await bq.explain(OWNER, interval.id, 'a', 'expense', 'out', D('50000')) == 'ok'
    one = await _payment('30000', datetime(2026, 11, 16, 15, 0))
    [(explanation, _, left)] = await bq.explanation_candidates(OWNER, one, TIMING['boundary'], settings.to_utc)
    assert left == D('50000')
    assert await bq.apply_match(OWNER, explanation.id, one, 'owner')
    await bq.recompute(OWNER, LATER, **TIMING)
    # 30 000 of the explanation is now a source payment, 20 000 still the owner's word: still fits
    assert (await _interval()).status == 'explained'

    too_big = await _payment('30000', datetime(2026, 11, 18, 10, 0))     # after the interval, and only 20 000 is left
    assert await bq.explanation_candidates(OWNER, too_big, TIMING['boundary'], settings.to_utc) == []
    assert not await bq.apply_match(OWNER, explanation.id, too_big, 'owner')
    two = await _payment('20000', datetime(2026, 11, 16, 16, 0))
    assert await bq.apply_match(OWNER, explanation.id, two, 'owner', TIMING['boundary'], settings.to_utc)
    await bq.recompute(OWNER, LATER, **TIMING)
    # every part of the answer is in the report now: it fits by the payments alone
    assert ((await _interval()).status, (await _interval()).explained) == ('ok', D('0'))


async def test_concurrent_confirmations_record_once(db):
    category_id = await _setup()
    await _two_checks()
    await bq.recompute(OWNER, LATER, **TIMING)
    interval = await _interval()
    operation = bq.operation_values(D('50000'), OperationType.expenditure, 'x', T1, None, None, None)
    same = await asyncio.gather(*[bq.explain(OWNER, interval.id, 'same', 'expense', 'out', D('50000'), category_id, operation)
                                  for _ in range(2)])
    assert sorted(same) == ['duplicate', 'ok']
    assert len(await _rows(Accounting, Accounting.user_id == OWNER)) == 1


async def test_concurrent_different_answers_never_over_explain(db):
    await _setup()
    await _two_checks()
    await bq.recompute(OWNER, LATER, **TIMING)
    interval = await _interval()
    results = await asyncio.gather(bq.explain(OWNER, interval.id, 'x', 'adjustment', 'out', D('50000')),
                                   bq.explain(OWNER, interval.id, 'y', 'adjustment', 'out', D('50000')))
    assert sorted(results) == ['ok', 'stale']


async def test_other_users_see_and_change_nothing(db):
    await _setup()
    await user.ensure_user(OTHER, None)
    await _two_checks()
    await bq.recompute(OWNER, LATER, **TIMING)
    interval = await _interval()
    assert await bq.get_interval(OTHER, interval.id) is None
    assert await bq.explain(OTHER, interval.id, 't', 'adjustment', 'out', D('1000')) == 'stale'
    assert not await bq.mark_unresolved(OTHER, interval.id) and not await bq.snooze(OTHER, interval.id, LATER)
    assert await bq.open_intervals(OTHER, LATER) == [] and len(await bq.open_intervals(OWNER, LATER)) == 1


async def test_unresolved_and_snoozed_are_not_asked(db):
    await _setup()
    await _two_checks()
    await bq.recompute(OWNER, LATER, **TIMING)
    interval = await _interval()
    assert await bq.snooze(OWNER, interval.id, LATER + timedelta(days=1))
    assert await bq.open_intervals(OWNER, LATER) == []
    assert len(await bq.open_intervals(OWNER, LATER + timedelta(days=2))) == 1
    assert await bq.mark_unresolved(OWNER, interval.id)
    assert await bq.open_intervals(OWNER, LATER + timedelta(days=2)) == []
    assert [i.id for i in await bq.other_intervals(OWNER, LATER)] == [interval.id]
    await bq.recompute(OWNER, LATER, **TIMING)
    assert (await _interval()).unresolved     # a recalculation keeps the owner's mark


async def test_waiting_until_the_report_after_the_grace_period(db):
    await _setup()
    await _two_checks()
    await bq.recompute(OWNER, T1 + timedelta(hours=3), **TIMING)
    assert (await _interval()).status == 'waiting'
    assert await bq.unnotified_intervals(OWNER, T1 + timedelta(hours=3)) == []


async def test_two_cards_with_the_same_ending_are_uncertain(db):
    await _setup()
    await bq.save_observation(OWNER, T0, T0, 10, reply(('A***6104', '100'), ('B***6104', '200')))
    await bq.save_observation(OWNER, T1, T1, 11, reply(('A***6104', '50'), ('B***6104', '200')))
    await bq.build_intervals(OWNER)
    await bq.recompute(OWNER, LATER, **TIMING)
    intervals = await _rows(BalanceInterval)
    assert len(intervals) == 2 and {i.status for i in intervals} == {'uncertain'}


async def test_payments_of_another_card_or_user_are_not_counted(db):
    await _setup()
    await user.ensure_user(OTHER, None)
    await _payment('50000', datetime(2026, 11, 16, 12, 0), account='860012******1234')
    await _payment('50000', datetime(2026, 11, 16, 12, 0), owner=OTHER)
    await _two_checks()
    await bq.recompute(OWNER, LATER, **TIMING)
    assert ((await _interval()).status, (await _interval()).remaining) == ('unexplained', D('-50000'))


async def test_daily_review_is_claimed_once(db):
    await _setup()
    day = date(2026, 11, 17)
    results = await asyncio.gather(bq.claim_review(OWNER, day), bq.claim_review(OWNER, day))
    assert sorted(r is None for r in results) == [False, True]
    previous = next(r for r in results if r is not None)
    await bq.release_review(OWNER, day, previous)
    assert await bq.claim_review(OWNER, day) is not None


async def test_explanation_operations_are_not_offered_for_linking(db):
    category_id = await _setup()
    await _two_checks()
    await bq.recompute(OWNER, LATER, **TIMING)
    interval = await _interval()
    operation = bq.operation_values(D('50000'), OperationType.expenditure, 'x', T1, None, None, None)
    await bq.explain(OWNER, interval.id, 't', 'expense', 'out', D('50000'), category_id, operation)
    assert await queries.candidates(OWNER, D('50000'), 'UZS', 'UZS', T1) == []


async def test_balance_state_and_partial_result(db):
    await _setup()
    await bq.request_balance(OWNER)
    assert (await queries.get_state(OWNER)).balance_requested
    await bq.mark_balance(OWNER, 'Click не ответил')
    state = await queries.get_state(OWNER)
    assert (state.balance_requested, state.last_balance_error, state.last_balance_ok_at) == (False, 'Click не ответил', None)
    await queries.mark_finished(OWNER, 'часть периодов не загружена', partial=True)
    assert (await queries.get_state(OWNER)).last_result == 'partial'


def test_migration_keeps_existing_data_and_downgrades():
    url = urlparse(TEST_PG_URL)
    name = f'balance_migration_{uuid.uuid4().hex[:8]}'
    admin = psycopg2.connect(TEST_PG_URL)
    admin.autocommit = True
    admin.cursor().execute(f'CREATE DATABASE {name}')
    env = dict(os.environ, HOST=url.hostname, PORT=str(url.port or 5432), POSTGRES_USER=url.username,
               POSTGRES_PASSWORD=url.password or 'unused', POSTGRES_DB=name, OWNER_ID=str(OWNER))

    def alembic(*args):
        subprocess.run([sys.executable, '-m', 'alembic', *args], cwd=ALEMBIC_DIR, env=env, check=True, capture_output=True)

    try:
        alembic('upgrade', 'c6f1a8d3b742')
        connection = psycopg2.connect(TEST_PG_URL.rsplit('/', 1)[0] + '/' + name)
        connection.autocommit = True
        cursor = connection.cursor()
        cursor.execute('INSERT INTO "user" (id) VALUES (1001), (2002)')
        cursor.execute("INSERT INTO sync_state (user_id, source, history_complete, paused, sync_requested, last_finished_at, "
                       "last_error) VALUES (1001, 'clickuz', true, false, false, now(), 'нет связи'), "
                       "(2002, 'clickuz', true, false, false, now(), NULL)")
        alembic('upgrade', 'head')
        cursor.execute('SELECT user_id, last_result, balance_requested, paused FROM sync_state ORDER BY user_id')
        assert cursor.fetchall() == [(1001, 'failed', False, False), (2002, 'ok', False, False)]
        alembic('downgrade', '-1')
        cursor.execute('SELECT count(*) FROM sync_state')
        assert cursor.fetchone() == (2,)
        cursor.execute("SELECT to_regclass('balance_intervals')")
        assert cursor.fetchone() == (None,)
        alembic('upgrade', 'head')
        connection.close()
    finally:
        admin.cursor().execute(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)')
        admin.close()
