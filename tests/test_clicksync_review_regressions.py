"""Regressions found while independently reviewing card balance reconciliation."""
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText

from clicksync import balance_recon, core, handlers
from test_clicksync import D, FakeAdapter, FakeStore, TODAY, engine, tx, week
from test_clicksync_balance import T0, facts, mv, pair_snapshots, run, snap


@pytest.mark.parametrize('closing', ['950000', '1000000'])
def test_unknown_status_is_uncertain_even_when_the_balance_appears_to_fit(closing):
    result = run(facts(closing, [mv(1, '50000', status='unknown')]))
    assert result.status == 'uncertain'
    assert 'статус' in result.reason


@pytest.mark.parametrize('movement,closing,expected_fees,expected_used', [
    (mv(1, '50000', fee='1500'), '950000', D('0'), [1]),
    (mv(1, '50000', status='pending'), '1000000', D('0'), []),
    (mv(1, '50000', status='reversed'), '950000', D('0'), [1]),
])
def test_a_matching_alternative_returns_its_numbers_and_audit_ids(movement, closing, expected_fees, expected_used):
    result = run(facts(closing, [movement]))
    assert result.status == 'ok'
    assert (result.expected, result.difference, result.remaining) == (D(closing), D('0'), D('0'))
    assert (result.fees, result.used) == (expected_fees, expected_used)


def test_fee_placement_can_differ_between_payments():
    result = run(facts('928500', [mv(1, '50000', fee='1500'), mv(2, '20000', fee='500')]))
    assert (result.status, result.expected, result.fees, result.remaining) == ('ok', D('928500'), D('1500'), D('0'))


def test_pending_payments_can_be_debited_independently():
    result = run(facts('950000', [mv(1, '50000', status='pending'), mv(2, '20000', status='pending')]))
    assert (result.status, result.remaining, result.used) == ('ok', D('0'), [1])


def test_unknown_fees_that_do_not_explain_the_balance_stay_uncertain():
    result = run(facts('940000', [mv(1, '50000', fee='1500')]))
    assert result.status == 'uncertain'


def test_repeated_card_renames_do_not_create_overlapping_intervals():
    snapshots = [snap(i, i, name, T0 + timedelta(hours=i)) for i, name in
                 [(1, 'OLD***6104'), (2, 'NEW***6104'), (3, 'OLD***6104'), (4, 'NEW***6104')]]
    assert pair_snapshots(snapshots) == [(1, 2), (2, 3), (3, 4)]


def test_two_new_cards_with_the_same_ending_do_not_reuse_one_previous_snapshot():
    snapshots = [snap(1, 1, 'OLD***6104', T0), snap(2, 2, 'A***6104', T0 + timedelta(hours=1)),
                 snap(3, 2, 'B***6104', T0 + timedelta(hours=1))]
    assert pair_snapshots(snapshots) == []


async def test_merchant_correction_keeps_one_source_row_and_warns_if_recorded():
    store = FakeStore(start=TODAY.replace(day=10))
    # Source timestamps include seconds. Keep the original accounting link when the merchant is corrected.
    original = tx('50000', when=datetime(2026, 11, 12, 9, 0, 31))

    def report(rows):
        return FakeAdapter([week(TODAY.replace(day=10), rows)])
    await engine.run_sync(report([original]), store, TODAY, 7)
    next(iter(store.rows.values()))['delivery'] = 'delivered'
    corrected = replace(original, description='CORRECTED SHOP')
    await engine.run_sync(report([corrected]), store, TODAY, 7)
    await engine.run_sync(report([corrected]), store, TODAY, 7)
    assert len(store.rows) == 1
    assert next(iter(store.rows.values()))['delivery'] == 'delivered'
    assert next(iter(store.rows.values()))['tx'].description == 'CORRECTED SHOP'
    assert [finding.kind for finding in store.findings.values()] == ['changed_after_delivery']


def test_different_merchants_in_the_same_minute_are_preserved():
    original = tx('50000', when=datetime(2026, 11, 12, 9, 0, 31))
    key = core.dedup_keys([original])[0][0]
    stored = core.StoredTx(1, key, original.status, original.amount, 'new', description=original.description,
                           direction=original.direction, account=original.account, occurred_at=original.occurred_at,
                           currency=original.currency)
    other = replace(original, description='OTHER SHOP', occurred_at=original.occurred_at + timedelta(seconds=1))
    plan = core.plan_ingest({}, [other], core.dedup_keys([other]), [stored])
    assert len(plan.inserts) == 1 and plan.updates == []
    # Even identical timestamps must not merge rows that are both present in the same report.
    other = replace(other, occurred_at=original.occurred_at)
    plan = core.plan_ingest({key: stored}, [original, other], core.dedup_keys([original, other]), [stored])
    assert len(plan.inserts) == 1 and plan.updates == []


def test_ambiguous_corrections_never_choose_one_new_row_arbitrarily():
    original = tx('50000', when=datetime(2026, 11, 12, 9, 0, 31))
    key = core.dedup_keys([original])[0][0]
    stored = core.StoredTx(1, key, original.status, original.amount, 'delivered', description=original.description,
                           direction='out', account=original.account, occurred_at=original.occurred_at,
                           currency=original.currency)
    rows = [replace(original, description=name) for name in ('SHOP A', 'SHOP B')]
    plan = core.plan_ingest({}, rows, core.dedup_keys(rows), [stored])
    assert plan.updates == [] and len(plan.inserts) == 2
    assert all(ambiguous for _, ambiguous, _ in plan.inserts)


def test_repeated_source_id_does_not_break_correction_uniqueness():
    original = tx('50000', when=datetime(2026, 11, 12, 9, 0, 31))
    key = core.dedup_keys([original])[0][0]
    stored = core.StoredTx(1, key, original.status, original.amount, 'new', description=original.description,
                           direction='out', account=original.account, occurred_at=original.occurred_at,
                           currency=original.currency)
    corrected = replace(original, source_id='12345')
    plan = core.plan_ingest({}, [corrected, corrected], core.dedup_keys([corrected, corrected]), [stored])
    assert not plan.inserts and len(plan.updates) == 1 and plan.unchanged == 1


def test_reading_search_is_bounded_and_stays_uncertain(monkeypatch):
    monkeypatch.setattr(balance_recon, 'MAX_READINGS', 4)
    result = run(facts('999999', [mv(i, str(2 ** i), status='pending') for i in range(4)]))
    assert result.status == 'uncertain' and 'слишком много' in result.reason


def test_failed_payment_fees_do_not_make_otherwise_certain_data_uncertain():
    result = run(facts('950000', [mv(1, '50000', fee='1500', status='failed')]))
    assert (result.status, result.remaining) == ('unexplained', D('-50000'))


def test_returning_card_alias_does_not_steal_a_current_card_snapshot():
    snapshots = [snap(1, 1, 'OLD***6104', T0), snap(2, 2, 'NEW***6104', T0 + timedelta(hours=1)),
                 snap(3, 3, 'OLD***6104', T0 + timedelta(hours=2)),
                 snap(4, 3, 'NEW***6104', T0 + timedelta(hours=2))]
    assert pair_snapshots(snapshots) == [(1, 2), (2, 4)]


def test_missing_source_id_preserves_the_key_without_discarding_other_updates():
    original = tx('50000', source_id='12345', status='pending')
    key = core.dedup_keys([original])[0][0]
    stored = core.StoredTx(1, key, original.status, original.amount, 'new', description=original.description,
                           source_id=original.source_id, direction='out', account=original.account,
                           occurred_at=original.occurred_at, currency=original.currency)
    updated = replace(original, source_id=None, status='completed')
    plan = core.plan_ingest({}, [updated], core.dedup_keys([updated]), [stored])
    assert not plan.inserts and len(plan.updates) == 1
    assert plan.updates[0].changes == {'status': ('pending', 'completed')}


# --- Payments that a complete report no longer shows ---------------------------------------------------------

def _week_rows(*amounts):
    return [tx(a, when=datetime(2026, 11, 11 + i, 9, 0)) for i, a in enumerate(amounts)]


def _rows_by_amount(store):
    return {r['tx'].amount: r for r in store.rows.values()}


async def _sync(store, report):
    return await engine.run_sync(FakeAdapter([report]), store, TODAY, 7)


async def test_a_payment_gone_from_a_complete_report_stops_counting_and_comes_back_if_shown_again():
    store = FakeStore(start=TODAY.replace(day=10))
    rows = _week_rows('100', '200', '300', '400')
    await _sync(store, week(TODAY.replace(day=10), rows))
    await _sync(store, week(TODAY.replace(day=10), rows[:3]))      # 400 cancelled in Click
    gone = _rows_by_amount(store)[D('400')]
    assert gone['missing_at'] is not None and len(store.rows) == 4
    assert all(r.get('missing_at') is None for amount, r in _rows_by_amount(store).items() if amount != D('400'))
    assert store.findings == {}         # not recorded yet: nothing to ask

    await _sync(store, week(TODAY.replace(day=10), rows))          # shown again
    assert _rows_by_amount(store)[D('400')]['missing_at'] is None


async def test_a_recorded_payment_that_vanished_is_asked_about_once():
    store = FakeStore(start=TODAY.replace(day=10))
    rows = _week_rows('100', '200', '300', '400')
    await _sync(store, week(TODAY.replace(day=10), rows))
    _rows_by_amount(store)[D('400')]['delivery'] = 'delivered'
    await _sync(store, week(TODAY.replace(day=10), rows[:3]))
    await _sync(store, week(TODAY.replace(day=10), rows[:3]))
    assert [f.kind for f in store.findings.values()] == ['missing_from_report']
    assert 'больше не учитывается' in next(iter(store.findings.values())).details


async def test_incomplete_report_marks_nothing():
    store = FakeStore(start=TODAY.replace(day=10))
    rows = _week_rows('100', '200', '300', '400')
    await _sync(store, week(TODAY.replace(day=10), rows))
    await _sync(store, week(TODAY.replace(day=10), rows[:3], complete=False, note='2 строки не прочитаны'))
    assert all(r.get('missing_at') is None for r in store.rows.values())


async def test_a_report_that_lost_most_payments_marks_nothing_and_is_read_again():
    store = FakeStore(start=TODAY.replace(day=10))
    rows = _week_rows('100', '200', '300', '400', '500', '600')
    await _sync(store, week(TODAY.replace(day=10), rows))
    result = await _sync(store, week(TODAY.replace(day=10), rows[:1]))
    assert all(r.get('missing_at') is None for r in store.rows.values())
    assert result.periods_incomplete == 1 and [f.kind for f in store.findings.values()] == ['report_shrank']
    assert store.periods[(TODAY.replace(day=10), TODAY.replace(day=16))]['status'] == 'incomplete'


async def test_rows_before_the_start_date_are_never_marked():
    store = FakeStore(start=TODAY.replace(day=10))
    early = tx('900', when=datetime(2026, 11, 9, 9, 0))
    store.rows['early'] = {'id': 99, 'tx': early, 'ambiguous': False, 'delivery': 'delivered'}
    await _sync(store, week(TODAY.replace(day=8), _week_rows('100')))
    assert store.rows['early'].get('missing_at') is None


async def test_ambiguous_merchant_correction_of_a_recorded_payment_is_not_silent():
    store = FakeStore(start=TODAY.replace(day=10))
    original = tx('50000', when=datetime(2026, 11, 12, 9, 0, 31))
    others = _week_rows('100', '200', '300')
    await _sync(store, week(TODAY.replace(day=10), [original] + others))
    _rows_by_amount(store)[D('50000')]['delivery'] = 'delivered'
    corrected = [replace(original, description=name) for name in ('SHOP A', 'SHOP B')]
    await _sync(store, week(TODAY.replace(day=10), corrected + others))
    old = next(r for r in store.rows.values() if r['id'] == 1)
    assert old['missing_at'] is not None        # no longer counted next to the two corrected rows
    assert [f.kind for f in store.findings.values()] == ['missing_from_report']
    assert sum(1 for r in store.rows.values() if r['ambiguous']) == 2


def test_vanish_limit_scales_with_the_period():
    plan = core.IngestPlan()
    rows = [core.StoredTx(i, f'k{i}', 'completed', D('1'), 'new', occurred_at=datetime(2026, 11, 12)) for i in range(10)]
    assert core.plan_vanished(plan, rows, set(range(5)), datetime(2026, 11, 18)) and len(plan.updates) == 5
    assert not core.plan_vanished(core.IngestPlan(), rows, set(range(4)), datetime(2026, 11, 18))


@pytest.mark.parametrize('description', ['message is not modified', 'chat not found'])
async def test_repeated_sync_handles_only_unchanged_message_errors(monkeypatch, description):
    state = SimpleNamespace(start_date=TODAY)
    monkeypatch.setattr(handlers.queries, 'get_state', AsyncMock(return_value=state))
    request = AsyncMock()
    monkeypatch.setattr(handlers.queries, 'request_sync', request)
    monkeypatch.setattr(handlers, '_status', AsyncMock(return_value=('unchanged', None)))
    error = TelegramBadRequest(method=EditMessageText(text='unchanged', chat_id=1001, message_id=1),
                               message=description)
    callback = SimpleNamespace(answer=AsyncMock(), message=SimpleNamespace(edit_text=AsyncMock(side_effect=error)))
    if description == 'message is not modified':
        await handlers.sync_now(callback)
        await handlers.sync_now(callback)
        assert request.await_count == 2 and callback.answer.await_count == 2
    else:
        with pytest.raises(TelegramBadRequest, match='chat not found'):
            await handlers.sync_now(callback)
