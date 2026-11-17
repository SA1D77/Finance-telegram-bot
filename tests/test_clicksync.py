"""Click import without Telegram or a database: parsing, deduplication, reconciliation, the sync engine
(navigation, end of history, restart recovery), the adapter against a scripted bot, the worker's run and
the /click queue."""
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Dict, List, Optional

import pytest

from clicksync import clickuz, core, engine, reconcile, worker
from clicksync.clickuz import BotMessage, Button, ClickAdapter, to_transactions
from clicksync.core import ParsedTx, PeriodReport, StoredTx
from clicksync.settings import Profile

D = Decimal

REPORT = """🗓 Платежи c 17.10.2026 до 24.10.2026

➖ 37'000 UZS
🔹 LLC MADINA QANDOLAT
💳 561468******6104
🕘 21:48 23.10.2026
✅ Успешно

➖ 2'500 UZS
🔹 OOO ATTO TOLOV
💳 561468******6104
🕖 19:38 23.10.2026
✅ Успешно

➖ 2'500 UZS
🔹 OOO ATTO TOLOV
💳 561468******6104
🕖 19:38 23.10.2026
✅ Успешно

➖ 99'000 UZS
🔹 KORZINKA
💳 561468******6104
🕕 18:00 22.10.2026
❌ Отклонено

➕ 5'000'000 UZS
🔹 SALARY LLC
💳 561468******6104
🕘 09:15 20.10.2026
"""


def tx(amount='100', direction='out', status='completed', when=datetime(2026, 10, 20, 10, 0), **kw) -> ParsedTx:
    return ParsedTx(amount=D(amount), direction=direction, status=status, occurred_at=when, currency='UZS',
                    description=kw.pop('description', 'SHOP'), account=kw.pop('account', '561468******6104'), **kw)


# --- Parsing ----------------------------------------------------------------------------------

def test_report_message_to_transactions():
    rows = to_transactions(REPORT)

    assert [(r.amount, r.direction, r.status, r.description) for r in rows] == [
        (D('37000'), 'out', 'completed', 'LLC MADINA QANDOLAT'),
        (D('2500'), 'out', 'completed', 'OOO ATTO TOLOV'),
        (D('2500'), 'out', 'completed', 'OOO ATTO TOLOV'),
        (D('99000'), 'out', 'failed', 'KORZINKA'),
        (D('5000000'), 'in', 'unknown', 'SALARY LLC'),     # no ✅ line: the status is not invented
    ]
    assert rows[0].account == '561468******6104' and rows[0].currency == 'UZS'
    assert rows[0].occurred_at == datetime(2026, 10, 23, 21, 48)    # as printed, no zone applied
    assert core.parse_period(REPORT) == (date(2026, 10, 17), date(2026, 10, 24))


@pytest.mark.parametrize('raw, masked', [
    ('561468******6104', '561468******6104'), ('8600123412345678', '860012******5678'),
    ('20208000900123456789', '202080**********6789'), ('**** 6104', '**** 6104'), (None, None), ('', None),
])
def test_account_masking(raw, masked):
    assert core.mask_account(raw) == masked


@pytest.mark.parametrize('text', ['Платежи', '17.10.2026', '24.10.2026 до 17.10.2026', '31.02.2026 до 01.03.2026'])
def test_no_period_header(text):
    assert core.parse_period(text) is None


# --- Deduplication -----------------------------------------------------------------------------

def test_keys_are_stable_and_twins_are_numbered_and_flagged():
    rows = to_transactions(REPORT)
    keys = core.dedup_keys(rows)

    assert keys == core.dedup_keys(to_transactions(REPORT))     # same report again: same keys
    assert len({k for k, _ in keys}) == len(rows)
    assert [ambiguous for _, ambiguous in keys] == [False, True, True, False, False]
    assert keys[1][0].endswith(':0') and keys[2][0].endswith(':1')


def test_status_is_not_part_of_the_key_and_source_ids_win():
    pending, done = tx(status='pending'), tx(status='completed')
    assert core.dedup_keys([pending]) == core.dedup_keys([done])
    a, b = tx(source_id='T-1'), tx(source_id='T-2')
    assert core.dedup_keys([a])[0][0] != core.dedup_keys([b])[0][0]
    assert core.dedup_keys([a])[0][0].startswith('id:')


def test_description_case_and_spaces_do_not_matter():
    assert core.dedup_keys([tx(description='OOO  Atto tolov')]) == core.dedup_keys([tx(description='ooo atto TOLOV')])


def _stored(key, row, delivery='new', **kw) -> StoredTx:
    kw.setdefault('direction', row.direction)
    return StoredTx(id=kw.pop('id', 1), dedup_key=key, status=row.status, amount=row.amount, delivery=delivery,
                    description=row.description, **kw)


def test_reprocessing_the_same_report_changes_nothing():
    rows = to_transactions(REPORT)
    keys = core.dedup_keys(rows)
    existing = {key: _stored(key, row, ambiguous=amb) for (key, amb), row in zip(keys, rows)}

    plan = core.plan_ingest(existing, rows, keys)

    assert plan.inserts == [] and plan.updates == [] and plan.unchanged == len(rows)


def test_pending_to_completed_is_an_update_with_its_change():
    pending = tx(status='pending')
    key = core.dedup_keys([pending])
    stored = {key[0][0]: _stored(key[0][0], pending)}

    plan = core.plan_ingest(stored, [replace(pending, status='completed')], key)

    assert plan.inserts == []
    assert plan.updates[0].changes == {'status': ('pending', 'completed')} and not plan.updates[0].needs_review


@pytest.mark.parametrize('new_status', ['reversed', 'failed'])
def test_recorded_payment_reversed_later_needs_review(new_status):
    row = tx()
    key = core.dedup_keys([row])
    stored = {key[0][0]: _stored(key[0][0], row, delivery='delivered')}

    plan = core.plan_ingest(stored, [replace(row, status=new_status)], key)

    assert plan.updates[0].needs_review


def test_unknown_status_never_overwrites_a_known_one():
    row = tx(status='completed')
    key = core.dedup_keys([row])
    plan = core.plan_ingest({key[0][0]: _stored(key[0][0], row)}, [replace(row, status='unknown')], key)
    assert plan.updates == [] and plan.unchanged == 1


async def test_corrected_amount_updates_the_row_instead_of_adding_one():
    store = FakeStore(start=date(2026, 11, 10))
    first = tx('100', when=datetime(2026, 11, 12, 9, 0, 30))
    await engine.run_sync(FakeAdapter([week(date(2026, 11, 10), [first])]), store, TODAY, 7)
    next(iter(store.rows.values()))['delivery'] = 'delivered'

    await engine.run_sync(FakeAdapter([week(date(2026, 11, 10), [replace(first, amount=D('120'))])]), store, TODAY, 7)
    await engine.run_sync(FakeAdapter([week(date(2026, 11, 10), [replace(first, amount=D('120'))])]), store, TODAY, 7)

    assert len(store.rows) == 1 and next(iter(store.rows.values()))['tx'].amount == D('120')
    assert [(field, change[0]) for _, field, change in store.audit] == [('dedup_key', store.audit[0][2][0]), ('amount', D('100'))]
    assert [f.kind for f in store.findings.values()] == ['changed_after_delivery']    # it was already recorded


async def test_source_id_appearing_later_keeps_one_row_and_missing_again_changes_nothing():
    store = FakeStore(start=date(2026, 11, 10))
    plain = tx('6490', when=datetime(2026, 11, 12, 0, 5))
    await engine.run_sync(FakeAdapter([week(date(2026, 11, 10), [plain])]), store, TODAY, 7)
    await engine.run_sync(FakeAdapter([week(date(2026, 11, 10), [replace(plain, source_id='4512345678')])]), store, TODAY, 7)
    again = await engine.run_sync(FakeAdapter([week(date(2026, 11, 10), [plain])]), store, TODAY, 7)

    assert len(store.rows) == 1 and next(iter(store.rows))[:3] == 'id:'
    assert next(iter(store.rows.values()))['tx'].source_id == '4512345678'
    assert again.inserted == 0 and again.unchanged == 1


async def test_two_different_payments_in_one_minute_are_never_merged():
    store = FakeStore(start=date(2026, 11, 10))
    taxi = tx('60000', when=datetime(2026, 11, 12, 9, 0))
    await engine.run_sync(FakeAdapter([week(date(2026, 11, 10), [taxi])]), store, TODAY, 7)
    both = [taxi, replace(taxi, amount=D('45000'))]
    result = await engine.run_sync(FakeAdapter([week(date(2026, 11, 10), both)]), store, TODAY, 7)
    assert result.inserted == 1 and len(store.rows) == 2


def test_same_source_id_twice_in_one_report_is_inserted_once():
    rows = [tx(source_id='T-1'), tx(source_id='T-1')]
    plan = core.plan_ingest({}, rows, core.dedup_keys(rows))
    assert len(plan.inserts) == 1 and plan.unchanged == 1


@pytest.mark.parametrize('complete, unfinished, expected', [
    (False, [], date(2026, 1, 1)),                       # first import: down to the start date
    (True, [], date(2026, 11, 9)),                       # later: the overlap window
    (True, [date(2026, 10, 1)], date(2026, 10, 1)),      # an unfinished period is never skipped
    (True, [date(2025, 12, 1)], date(2026, 1, 1)),       # ... but nothing before the start date
])
def test_sync_target(complete, unfinished, expected):
    assert core.sync_target(date(2026, 11, 16), date(2026, 1, 1), 7, complete, unfinished) == expected


# --- Reconciliation ---------------------------------------------------------------------------

def test_balance_ok_with_fee_on_top():
    rows = [tx('1000', 'in'), tx('300', fee=D('3'), fee_included=False)]
    assert reconcile.balance_check(D('500'), D('1197'), rows).status == 'ok'


def test_included_fee_is_not_subtracted_twice():
    rows = [tx('303', fee=D('3'), fee_included=True)]
    result = reconcile.balance_check(D('1000'), D('697'), rows)
    assert result.status == 'ok' and result.difference == 0


def test_unknown_fee_placement_is_resolved_only_when_one_reading_fits():
    rows = [tx('300', fee=D('3'))]
    assert reconcile.balance_check(D('1000'), D('700'), rows).status == 'ok'      # fee inside the amount
    assert reconcile.balance_check(D('1000'), D('697'), rows).status == 'ok'      # fee on top
    assert reconcile.balance_check(D('1000'), D('650'), rows).status == 'indeterminate'


def test_mismatch_lists_what_may_explain_it():
    rows = [tx('100'), tx('250', description='TAXI'), tx('40', status='failed', description='CAFE')]
    result = reconcile.balance_check(D('1000'), D('610'), rows)

    assert result.status == 'mismatch'
    assert result.expected == D('650') and result.difference == D('-40')
    assert [c.description for c in result.candidates] == ['CAFE']


def test_pending_payments_make_the_result_indeterminate_not_wrong():
    rows = [tx('100'), tx('50', status='pending')]
    result = reconcile.balance_check(D('1000'), D('850'), rows)
    assert result.status == 'indeterminate' and result.waiting and result.candidates[0].status == 'pending'


def test_no_balances_means_no_reconciliation():
    result = reconcile.balance_check(None, D('1'), [tx()])
    assert result.status == 'unavailable' and result.expected is None and result.difference is None


def test_totals_per_account_and_currency_keep_own_transfers_out():
    rows = [tx('1000', 'in', account='A'), tx('200', account='A'),
            SimpleNamespace(**{**tx('300', account='A').__dict__, 'own_transfer': True}),
            SimpleNamespace(**{**tx('300', 'in', account='B').__dict__, 'own_transfer': True}),
            replace(tx('10', account='B'), currency='USD'), tx('5', status='failed', account='A')]
    per_account = reconcile.totals_by_account(rows)

    a = per_account[('A', 'UZS')]
    assert (a.income, a.expense, a.transfers_out, a.count) == (D('1000'), D('200'), D('300'), 3)
    total = reconcile.consolidated(per_account)
    assert (total['UZS'].income, total['UZS'].expense) == (D('1000'), D('200'))   # transfers are neither
    assert total['USD'].expense == D('10')                                         # never added to UZS


# --- Engine -------------------------------------------------------------------------------------

def week(start: date, rows=(), **kw) -> PeriodReport:
    return PeriodReport(start=start, end=start + timedelta(days=6), transactions=list(rows), **kw)


class FakeAdapter:
    def __init__(self, reports: List[PeriodReport], repeat_last: bool = False) -> None:
        self.reports = reports
        self.repeat_last = repeat_last
        self.index = 0
        self.opened = 0

    async def open(self):
        self.opened += 1
        self.index = 0
        return self.reports[0]

    async def older(self):
        if self.index + 1 >= len(self.reports):
            return self.reports[-1] if self.repeat_last else None
        self.index += 1
        return self.reports[self.index]


class FakeStore:
    """engine.Store in memory, with the same rules as queries.DbStore."""

    def __init__(self, start: Optional[date] = date(2026, 10, 1)) -> None:
        self.start = start
        self.complete = False
        self.periods: Dict[tuple, dict] = {}
        self.rows: Dict[str, dict] = {}
        self.audit: List[tuple] = []
        self.findings: Dict[str, engine.Finding] = {}
        self.fail_on: Optional[date] = None

    async def start_date(self):
        return self.start

    async def history_complete(self):
        return self.complete

    async def recover_interrupted(self):
        stuck = [p for p in self.periods.values() if p['status'] == 'in_progress']
        for period in stuck:
            period['status'] = 'incomplete'
        return len(stuck)

    async def unfinished_starts(self):
        return [key[0] for key, p in self.periods.items() if p['status'] != 'completed']

    async def begin_period(self, start, end):
        period = self.periods.setdefault((start, end), {'id': len(self.periods) + 1, 'attempts': 0})
        period.update(status='in_progress', attempts=period['attempts'] + 1)
        if start == self.fail_on:
            raise_in_apply[0] = True
        return period['id']

    @staticmethod
    def _stored(k, r):
        t = r['tx']
        return StoredTx(id=r['id'], dedup_key=k, status=t.status, amount=t.amount, delivery=r['delivery'],
                        description=t.description, ambiguous=r['ambiguous'], direction=t.direction, source_id=t.source_id,
                        account=t.account, occurred_at=t.occurred_at, currency=t.currency, missing_at=r.get('missing_at'))

    async def period_rows(self, first, last):
        return [self._stored(k, r) for k, r in self.rows.items()
                if r['tx'].occurred_at and first <= r['tx'].occurred_at.date() <= last]

    async def existing(self, keys):
        return {k: self._stored(k, r) for k, r in self.rows.items() if k in keys}

    async def lookalikes(self, transactions):
        minutes = {t.occurred_at.replace(second=0) for t in transactions if t.occurred_at}
        return [self._stored(k, r) for k, r in self.rows.items()
                if r['tx'].occurred_at and r['tx'].occurred_at.replace(second=0) in minutes]

    async def apply(self, period_id, report, plan, status, note, findings):
        if raise_in_apply[0]:
            raise_in_apply[0] = False
            raise RuntimeError('boom')
        for key, ambiguous, row in plan.inserts:
            self.rows[key] = {'id': len(self.rows) + 1, 'tx': row, 'ambiguous': ambiguous, 'delivery': 'new'}
        for update in plan.updates:
            old_key, record = next((k, r) for k, r in self.rows.items() if r['id'] == update.stored.id)
            if 'dedup_key' in update.changes:
                self.rows[update.changes['dedup_key'][1]] = self.rows.pop(old_key)
            values = {k: v[1] for k, v in update.changes.items() if k not in ('ambiguous', 'dedup_key', 'missing_at')}
            record['tx'] = replace(record['tx'], **values)
            if 'missing_at' in update.changes:
                record['missing_at'] = update.changes['missing_at'][1]
            self.audit += [(update.stored.id, k, v) for k, v in update.changes.items()]
        for finding in findings:
            self.findings.setdefault(finding.fingerprint, finding)
        self._period(period_id).update(status=status, note=note)

    async def fail_period(self, period_id, note, finding):
        self._period(period_id).update(status='failed', note=note)
        self.findings.setdefault(finding.fingerprint, finding)

    async def raise_finding(self, finding):
        self.findings.setdefault(finding.fingerprint, finding)

    async def set_history_complete(self):
        self.complete = True

    def _period(self, period_id):
        return next(p for p in self.periods.values() if p['id'] == period_id)


raise_in_apply = [False]


@pytest.fixture(autouse=True)
def _reset_failure():
    raise_in_apply[0] = False


TODAY = date(2026, 11, 16)


def history():
    """Four weekly reports, newest first: 10.11, 03.11, 27.10, 20.10."""
    return [week(date(2026, 11, 10), [tx('100', when=datetime(2026, 11, 12, 9, 0))]),
            week(date(2026, 11, 3), [tx('200', when=datetime(2026, 11, 4, 9, 0))]),
            week(date(2026, 10, 27), [tx('300', when=datetime(2026, 10, 28, 9, 0))]),
            week(date(2026, 10, 20), [tx('400', when=datetime(2026, 10, 21, 9, 0)),
                                      tx('500', when=datetime(2026, 10, 19, 9, 0))])]


async def test_first_run_walks_back_to_the_start_date_only():
    store = FakeStore(start=date(2026, 10, 30))
    result = await engine.run_sync(FakeAdapter(history()), store, TODAY, overlap_days=7)

    assert result.visited == [(date(2026, 11, 10), date(2026, 11, 16)), (date(2026, 11, 3), date(2026, 11, 9)),
                              (date(2026, 10, 27), date(2026, 11, 2))]
    assert result.reached_target and store.complete
    assert sorted(r['tx'].amount for r in store.rows.values()) == [D('100'), D('200')]   # 28.10 is before the start
    assert result.skipped_before_start == 1


async def test_second_run_rereads_only_the_overlap_window_and_adds_nothing():
    store = FakeStore(start=date(2026, 10, 1))
    await engine.run_sync(FakeAdapter(history()), store, TODAY, overlap_days=7)
    count = len(store.rows)

    result = await engine.run_sync(FakeAdapter(history()), store, TODAY, overlap_days=7)

    # Overlap of 7 days from 16.11 reaches 09.11, which is in the 03.11 report.
    assert result.visited == [(date(2026, 11, 10), date(2026, 11, 16)), (date(2026, 11, 3), date(2026, 11, 9))]
    assert len(store.rows) == count and result.inserted == 0 and result.unchanged == 2


async def test_end_of_history_by_missing_button_and_early_end_is_reported():
    store = FakeStore(start=date(2026, 1, 1))
    result = await engine.run_sync(FakeAdapter(history()), store, TODAY, overlap_days=7)

    assert result.end_of_history and not result.reached_target and store.complete
    assert any(f.kind == 'history_ended_early' and '20.10.2026' in f.details for f in store.findings.values())


async def test_repeated_page_ends_the_walk_instead_of_looping():
    result = await engine.run_sync(FakeAdapter(history(), repeat_last=True), FakeStore(start=date(2026, 1, 1)), TODAY, 7)
    assert result.end_of_history and len(result.visited) == 4


async def test_loop_guard_stops_endless_navigation():
    class Endless(FakeAdapter):
        async def older(self):
            self.index += 1
            return week(date(2026, 11, 10) - timedelta(days=7 * self.index))

    with pytest.raises(engine.SyncError):
        await engine.run_sync(Endless([week(date(2026, 11, 10))]), FakeStore(start=date(2000, 1, 1)), TODAY, 7, max_steps=5)


async def test_older_button_that_goes_forward_is_refused():
    class Wrong(FakeAdapter):
        async def older(self):
            return week(date(2026, 11, 17))

    with pytest.raises(engine.SyncError):
        await engine.run_sync(Wrong(history()), FakeStore(), TODAY, 7)


async def test_failed_period_is_kept_and_read_again_next_time():
    store = FakeStore(start=date(2026, 10, 1))
    store.fail_on = date(2026, 11, 3)
    first = await engine.run_sync(FakeAdapter(history()), store, TODAY, 7)

    assert first.periods_failed == 1 and not store.complete
    assert store.periods[(date(2026, 11, 3), date(2026, 11, 9))]['status'] == 'failed'
    assert any(f.kind == 'period_failed' for f in store.findings.values())
    # The walk went on past the failed period, so older ones were stored.
    assert D('300') in {r['tx'].amount for r in store.rows.values()}

    store.fail_on = None     # the problem went away
    second = await engine.run_sync(FakeAdapter(history()), store, TODAY, 7)
    assert (date(2026, 11, 3), date(2026, 11, 9)) in second.visited
    assert store.periods[(date(2026, 11, 3), date(2026, 11, 9))]['status'] == 'completed'
    assert store.complete


async def test_restart_after_a_crash_reads_the_interrupted_period_again():
    store = FakeStore(start=date(2026, 10, 1))
    await engine.run_sync(FakeAdapter(history()), store, TODAY, 7)
    store.periods[(date(2026, 10, 20), date(2026, 10, 26))]['status'] = 'in_progress'   # process killed mid-period

    result = await engine.run_sync(FakeAdapter(history()), store, TODAY, 7)

    assert (date(2026, 10, 20), date(2026, 10, 26)) in result.visited
    assert all(p['status'] == 'completed' for p in store.periods.values())
    assert result.inserted == 0     # nothing stored twice


async def test_incomplete_period_is_reported_and_not_counted_as_done():
    reports = history()
    reports[0] = replace(reports[0], complete=False, note='файл отчёта не пришёл')
    store = FakeStore(start=date(2026, 11, 1))
    result = await engine.run_sync(FakeAdapter(reports), store, TODAY, 7)

    assert result.periods_incomplete == 1 and not store.complete
    assert any(f.kind == 'period_incomplete' for f in store.findings.values())


async def test_status_change_is_stored_with_audit_and_balance_findings_repeat_once():
    pending = tx('100', status='pending', when=datetime(2026, 11, 12, 9, 0))
    store = FakeStore(start=date(2026, 11, 10))
    reports = [week(date(2026, 11, 10), [pending], opening_balance=D('1000'), closing_balance=D('850'))]
    await engine.run_sync(FakeAdapter(reports), store, TODAY, 0)
    reports = [week(date(2026, 11, 10), [replace(pending, status='completed')], opening_balance=D('1000'),
                    closing_balance=D('850'))]
    await engine.run_sync(FakeAdapter(reports), store, TODAY, 0)
    await engine.run_sync(FakeAdapter(reports), store, TODAY, 0)

    assert len(store.rows) == 1 and next(iter(store.rows.values()))['tx'].status == 'completed'
    assert store.audit == [(1, 'status', ('pending', 'completed'))]
    mismatches = [f for f in store.findings.values() if f.kind == 'balance_mismatch']
    assert [(f.expected, f.reported, f.difference) for f in mismatches] == [(D('900'), D('850'), D('-50'))]


async def test_no_start_date_no_run():
    with pytest.raises(engine.NotConfigured):
        await engine.run_sync(FakeAdapter(history()), FakeStore(start=None), TODAY, 7)


# --- Adapter against a scripted @clickuz ------------------------------------------------------

PROFILE = Profile(reports_command='/reports', payments='Платежи', download='Скачать', next_page='▶')
CARD = '561468******6104'
HEADER = ('№ платежа', 'Сумма', 'Время', 'Карта', 'Сервис', 'Комиссия', 'Параметры оплаты', 'Статус платежа')


def xlsx(path, rows, header=HEADER):
    """A file laid out like the real click-payments-<id>-<YYYYMM>.xlsx: every value is text, amounts unsigned."""
    import xlsxwriter
    book = xlsxwriter.Workbook(str(path))
    sheet = book.add_worksheet()
    sheet.write_row(0, 0, header)
    for number, row in enumerate(rows, start=1):
        for column, value in enumerate(row):
            if value not in (None, ''):
                sheet.write_string(number, column, value)
    book.close()
    return path


def file_row(amount, when, service, number='', status='Успешно проведен', card=CARD, fee=''):
    return (number, amount, when, card, service, fee, '', status)


WEEK = ("🗓 Платежи c 10.11.2026 до 17.11.2026\n\n"
        "➖ 60'000 UZS\n🔹 OOO YANDEXGO UB\n💳 561468******6104\n🕕 18:07 16.11.2026\n✅ Успешно\n\n"
        "➕ 2'100'000 UZS\n🔹 AHDIJAH KHAMKORBAHK\n💳 561468******6104\n🕕 18:06 16.11.2026\n✅ Успешно\n")
WEEK_PAGE_2 = ("🗓 Платежи c 10.11.2026 до 17.11.2026\n\n"
               "➖ 2'500 UZS\n🔹 OOO ATTO TOLOV\n💳 561468******6104\n🕕 18:12 13.11.2026\n✅ Успешно\n")
QUIET_WEEK = '🗓 Платежи c 10.11.2026 до 17.11.2026\n\nПлатежей нет'
VIEW_BUTTONS = [Button('◀️', 'callback'), Button('1️⃣', 'callback'), Button('▶️', 'callback'), Button('💳', 'callback'),
                Button('⬇️ Скачать', 'callback'), Button('⬅️ Назад', 'callback')]
MENU = [Button(label, 'text') for label in ('👑 Click Подписка', '💳 Мои карты', '💸 Оплата', '🔀 Переводы', '📊 Отчеты')]


class ScriptedClick:
    """Answers like the real @clickuz (17.11.2026): /reports -> Платежи -> weekly pages -> Скачать -> month -> file."""

    def __init__(self, tmp_path, months: Dict[str, list], pages=(WEEK, WEEK_PAGE_2)) -> None:
        self.tmp_path = tmp_path
        self.months = months        # 'YYYY.MM' -> file rows; the order is the order of the buttons
        self.pages = list(pages)
        self.page = 0
        self.messages: List[BotMessage] = []
        self.sent: List[str] = []
        self.pressed: List[str] = []
        self.files: Dict[int, object] = {}

    def _say(self, text, buttons=(), has_file=False):
        self.messages.append(BotMessage(id=len(self.messages) + 1, text=text, buttons=list(buttons), has_file=has_file))
        return self.messages[-1].id

    async def send(self, text):
        self.sent.append(text)
        if text == '/reports':
            self._say('Выберите нужный раздел 👇', MENU)
            self._say('✅ Мониторинг карт активен', [Button('💸 Платежи', 'callback'), Button('📊 Мониторинг карт', 'callback')])

    async def recent(self, limit=8):
        return list(reversed(self.messages))[:limit]

    async def press(self, message_id, index):
        label = self.messages[message_id - 1].buttons[index].text
        self.pressed.append(label)
        if label == '💸 Платежи':      # the report replaces the menu message in place
            self.messages[message_id - 1] = BotMessage(id=message_id, text=self.pages[0], buttons=VIEW_BUTTONS)
        elif label == '▶️':
            if self.page + 1 < len(self.pages):     # on the last page nothing changes
                self.page += 1
                self.messages[message_id - 1] = BotMessage(id=message_id, text=self.pages[self.page], buttons=VIEW_BUTTONS)
        elif label == '⬇️ Скачать':
            self._say('Выберите месяц 👇', [Button(m, 'callback') for m in self.months])
        elif clickuz.MONTH_LABEL.match(label):
            year, month = (int(x) for x in label.split('.'))
            self._say('⏳ Пожалуйста, подождите. Это может занять некоторое время')
            last = (date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)).day
            path = xlsx(self.tmp_path / f'click-payments-1-{year}{month:02d}.xlsx', self.months[label])
            file_id = self._say(f'🗓 История платежей с 01.{month:02d}.{year} по {last}.{month:02d}.{year}', has_file=True)
            self.files[file_id] = path
        else:
            raise AssertionError(f'pressed {label}')

    async def download(self, message_id, directory):
        return self.files[message_id]


async def no_sleep(_seconds):
    return None


def adapter(bot, profile=PROFILE):
    return ClickAdapter(bot, profile, download_dir=bot.tmp_path, step_delay=0, reply_timeout=2, file_timeout=2,
                        sleep=no_sleep)


NOVEMBER = [file_row('60000', '16.11.2026 18:07:41', 'OOO YANDEXGO UB'),
            file_row('2100000', '16.11.2026 18:06:02', 'AHDIJAH KHAMKORBAHK'),
            file_row('2500', '13.11.2026 18:12:55', 'OOO ATTO TOLOV'),
            file_row('45000', '02.11.2026 09:00:00', 'XK DREAM DENTAL'),
            file_row('6490', '01.11.2026 00:05:00', 'Istoriya po karte', number='4512345678')]
OCTOBER = [file_row('37000', '23.10.2026 21:48:10', 'LLC MADINA QANDOLAT'),
           file_row('99000', '22.10.2026 18:00:00', 'KORZINKA', status='Отклонен'),
           file_row('10000', '05.10.2026 12:00:00', 'EARLY')]


async def test_adapter_reads_months_from_files_and_signs_from_the_weekly_report(tmp_path):
    bot = ScriptedClick(tmp_path, {'2026.11': NOVEMBER, '2026.10': OCTOBER})
    click = adapter(bot)

    november = await click.open()
    october = await click.older()
    end = await click.older()

    assert bot.sent == ['/reports']
    assert bot.pressed == ['💸 Платежи', '▶️', '▶️', '⬇️ Скачать', '2026.11', '⬇️ Скачать', '2026.10', '⬇️ Скачать']
    assert (november.start, november.end, november.complete) == (date(2026, 11, 1), date(2026, 11, 30), True)
    rows = {t.description: t for t in november.transactions}
    assert rows['OOO YANDEXGO UB'].direction == 'out' and rows['AHDIJAH KHAMKORBAHK'].direction == 'in'
    assert rows['OOO ATTO TOLOV'].direction == 'out'            # from the second page of the week
    assert rows['XK DREAM DENTAL'].direction == 'unknown'       # older than the week: the owner will say
    assert rows['Istoriya po karte'].source_id == '4512345678'
    assert all(t.currency == 'UZS' and t.account == CARD for t in november.transactions)
    assert rows['OOO YANDEXGO UB'].occurred_at == datetime(2026, 11, 16, 18, 7, 41)
    assert (october.start, [t.status for t in october.transactions]) == (date(2026, 10, 1), ['completed', 'failed', 'completed'])
    assert end is None                                          # Click offers no September
    assert not list(tmp_path.glob('*.xlsx'))                    # read files are not kept on disk


async def test_adapter_never_presses_anything_else(tmp_path):
    bot = ScriptedClick(tmp_path, {'2026.11': NOVEMBER})
    click = adapter(bot)
    await click.open()
    menu = bot.messages[0]
    for message, label in ((menu, 'Оплата'), (menu, 'Переводы'), (click.view, 'Назад'), (click.view, '💳')):
        with pytest.raises(engine.SyncError):
            await click._press(message, label)
    assert not any(word in ' '.join(bot.pressed) for word in ('Оплата', 'Переводы', 'Назад'))
    with pytest.raises(engine.SyncError):     # a link is never pressed, even when it is named in the settings
        await adapter(bot, replace(PROFILE, download='Сайт'))._press(
            BotMessage(id=99, text='', buttons=[Button('Сайт', 'url')]), 'Сайт')


async def test_currency_comes_from_the_week_or_the_setting(tmp_path):
    with pytest.raises(engine.SyncError, match='CLICK_CURRENCY'):
        await adapter(ScriptedClick(tmp_path, {'2026.11': NOVEMBER}, pages=(QUIET_WEEK,))).open()
    bot = ScriptedClick(tmp_path, {'2026.11': NOVEMBER}, pages=(QUIET_WEEK,))
    report = await adapter(bot, replace(PROFILE, currency='UZS')).open()
    assert {t.currency for t in report.transactions} == {'UZS'}
    assert {t.direction for t in report.transactions} == {'unknown'}


async def test_unreadable_rows_make_the_month_incomplete(tmp_path):
    bot = ScriptedClick(tmp_path, {'2026.11': NOVEMBER + [file_row('много', '16.11.2026 10:00:00', 'X')]})
    report = await adapter(bot).open()
    assert not report.complete and 'не прочитано строк файла: 1' in report.note


async def test_missing_file_is_an_error_not_an_empty_month(tmp_path):
    class Silent(ScriptedClick):
        async def press(self, message_id, index):
            label = self.messages[message_id - 1].buttons[index].text
            if clickuz.MONTH_LABEL.match(label):
                self.pressed.append(label)
                self._say('⏳ Пожалуйста, подождите. Это может занять некоторое время')
                return
            await super().press(message_id, index)

    with pytest.raises(engine.SyncError, match='не прислал файл'):
        await adapter(Silent(tmp_path, {'2026.11': NOVEMBER})).open()


async def test_engine_walks_months_back_to_the_start_date(tmp_path):
    bot = ScriptedClick(tmp_path, {'2026.11': NOVEMBER, '2026.10': OCTOBER, '2026.09': []})
    result = await engine.run_sync(adapter(bot), FakeStore(start=date(2026, 10, 15)), date(2026, 11, 17), 7)
    assert [start for start, _ in result.visited] == [date(2026, 11, 1), date(2026, 10, 1)]
    assert '2026.09' not in bot.pressed         # October already reaches the start date
    assert result.skipped_before_start == 1     # the 05.10 row is before the chosen start


# --- Report file -----------------------------------------------------------------------------------

def test_report_file_reads_the_real_layout(tmp_path):
    from clicksync.report_file import parse_report_file
    rows = [file_row("1'200'000", '16.11.2026 10:47:00', 'XK  DREAM DENTAL', fee='1 500,50'),
            file_row('-2500.00', '16.11.2026 11:23:00', 'OOO ATTO TOLOV', status='Отменен'),
            ('', '', '', '', '', '', '', '')]
    path = xlsx(tmp_path / 'r.xlsx', rows)
    report = parse_report_file(path)
    first, second = report.transactions
    assert (first.amount, first.direction, first.description, first.fee) == (D('1200000'), 'unknown', 'XK DREAM DENTAL', D('1500.50'))
    assert (second.amount, second.direction, second.status) == (D('2500.00'), 'out', 'reversed')
    assert report.unreadable == 0


def test_report_file_finds_columns_by_name_not_position(tmp_path):
    from clicksync.report_file import parse_report_file
    path = xlsx(tmp_path / 'r.xlsx', [('Успешно проведен', '16.11.2026 10:47:00', '60000', 'OOO YANDEXGO UB')],
                header=('Статус платежа', 'Время', 'Сумма', 'Сервис'))
    (row,) = parse_report_file(path).transactions
    assert (row.amount, row.status, row.description, row.account) == (D('60000'), 'completed', 'OOO YANDEXGO UB', None)


@pytest.mark.parametrize('content', [b'not a zip', None])
def test_report_file_of_another_format_is_refused(tmp_path, content):
    from clicksync.report_file import UnknownReportFormat, parse_report_file
    path = tmp_path / 'r.xlsx'
    if content is None:
        xlsx(path, [('a', 'b')], header=('Дата операции', 'Описание'))
    else:
        path.write_bytes(content)
    with pytest.raises(UnknownReportFormat):
        parse_report_file(path)


def test_direction_is_not_part_of_the_key_and_unknown_never_overwrites():
    out = tx(direction='out')
    key = core.dedup_keys([out])
    assert key == core.dedup_keys([replace(out, direction='unknown')])
    stored = {key[0][0]: _stored(key[0][0], out, direction='unknown')}
    plan = core.plan_ingest(stored, [out], key)
    assert plan.updates[0].changes == {'direction': ('unknown', 'out')} and not plan.updates[0].needs_review
    known = {key[0][0]: _stored(key[0][0], out)}
    assert core.plan_ingest(known, [replace(out, direction='unknown')], key).unchanged == 1
    recorded = {key[0][0]: _stored(key[0][0], out, delivery='delivered', direction='in')}
    assert core.plan_ingest(recorded, [out], key).updates[0].needs_review


# --- Worker ---------------------------------------------------------------------------------------

def state(**kw):
    values = dict(start_date=date(2026, 10, 1), sync_requested=False, paused=False, last_started_at=None,
                  last_finished_at=None, last_error=None)
    values.update(kw)
    return SimpleNamespace(**values)


NOW = datetime(2026, 11, 16, 12, 0)


@pytest.mark.parametrize('values, due', [
    ({}, True),
    ({'start_date': None}, False),
    ({'last_started_at': NOW - timedelta(minutes=30)}, False),
    ({'last_started_at': NOW - timedelta(minutes=200)}, True),
    ({'paused': True, 'last_started_at': NOW - timedelta(days=1)}, False),
    ({'paused': True, 'sync_requested': True}, True),
    # a failed run is retried after 15 minutes, not after the full interval
    ({'last_started_at': NOW - timedelta(minutes=20), 'last_finished_at': NOW - timedelta(minutes=19), 'last_error': 'x'}, True),
])
def test_is_due(values, due):
    assert worker.is_due(state(**values), NOW, 180) is due


class Recorder:
    def __init__(self):
        self.calls = []
        self.pending = [0]

    async def __call__(self, text, markup):
        self.calls.append(text)
        return True


@pytest.fixture
def worker_db(monkeypatch):
    log = {'finished': [], 'notified': []}
    pending = iter([1, 4])

    async def mark_started(user_id):
        log['started'] = True

    async def get_state(user_id):
        return SimpleNamespace(last_error=log.get('previous_error'))

    async def mark_finished(user_id, error, partial=False):
        log['finished'].append(error)
        log.setdefault('partial', []).append(partial)

    async def pending_count(user_id):
        return next(pending)

    async def nothing(*args, **kwargs):
        return 0

    async def no_findings(user_id):
        return []

    async def mark_notified(user_id, ids):
        log['notified'] += list(ids)

    for name, value in dict(get_state=get_state, mark_started=mark_started, mark_finished=mark_finished, pending_count=pending_count,
                            auto_link_exact=nothing, check_ledger=nothing, unnotified_discrepancies=no_findings,
                            mark_notified=mark_notified).items():
        monkeypatch.setattr(worker.queries, name, value)
    monkeypatch.setattr(worker.settings, 'to_utc', lambda value: value)

    async def refresh(user_id):
        log['refreshed'] = log.get('refreshed', 0) + 1
        return {}

    monkeypatch.setattr(worker, 'refresh_intervals', refresh)
    return log


async def test_run_once_reports_new_payments(worker_db, monkeypatch):
    async def fake_run(adapter, store, today, overlap):
        return engine.RunResult(inserted=3)

    monkeypatch.setattr(worker, 'run_sync', fake_run)
    notify = Recorder()

    async def factory():
        return object()

    result = await worker.run_once(1001, factory, notify, TODAY, 'UZS')

    assert result.inserted == 3 and worker_db['finished'] == [None]
    assert notify.calls == ['🧾 Из Click загружено новых платежей: 3. Разобрать: /click']


@pytest.mark.parametrize('failure, expected', [
    (ConnectionError('reset'), 'нет связи с Telegram'), (engine.SyncError('Кнопка не найдена'), 'Кнопка не найдена'),
    (KeyError('secret 37000'), 'внутренняя ошибка: KeyError'),
])
async def test_run_once_survives_failures_and_records_them(worker_db, failure, expected):
    async def factory():
        raise failure

    notify = Recorder()
    assert await worker.run_once(1001, factory, notify, TODAY, 'UZS') is None
    assert expected in worker_db['finished'][0] and '37000' not in worker_db['finished'][0]
    assert any('не удалась' in call for call in notify.calls)


async def test_the_same_failure_is_not_announced_again(worker_db):
    async def factory():
        raise engine.SyncError('Кнопка не найдена')

    worker_db['previous_error'] = 'Кнопка не найдена'
    notify = Recorder()
    await worker.run_once(1001, factory, notify, TODAY, 'UZS')
    assert not any('не удалась' in call for call in notify.calls)


async def test_findings_are_sent_once(monkeypatch):
    finding = SimpleNamespace(id=7, kind='balance_mismatch', account='561468******6104', currency='UZS', period_id=None,
                              transaction_id=None, details='Период 10.11.2026–16.11.2026: баланс не сходится.',
                              expected=D('900'), reported=D('850'), difference=D('-50'))
    sent_ids = []

    async def unnotified(user_id):
        return [] if sent_ids else [finding]

    async def mark(user_id, ids):
        sent_ids.extend(ids)

    async def none(*args):
        return None

    monkeypatch.setattr(worker.queries, 'unnotified_discrepancies', unnotified)
    monkeypatch.setattr(worker.queries, 'mark_notified', mark)
    monkeypatch.setattr(worker.queries, 'get_period', none)
    notify = Recorder()

    assert await worker.notify_findings(1001, notify) == 1
    assert await worker.notify_findings(1001, notify) == 0
    text = notify.calls[0]
    for part in ('Баланс не сходится', '561468******6104', 'Ожидаемый баланс: 900 UZS', 'Баланс в отчёте: 850 UZS',
                 'Разница: <b>-50 UZS</b>', 'Что нужно от вас'):
        assert part in text


async def test_a_finding_that_was_not_delivered_is_sent_again(monkeypatch):
    finding = SimpleNamespace(id=7, kind='period_failed', account=None, currency=None, period_id=None, transaction_id=None,
                              details='Период не обработан.', expected=None, reported=None, difference=None)
    marked = []

    async def unnotified(user_id):
        return [] if marked else [finding]

    async def mark(user_id, ids):
        marked.extend(ids)

    async def none(*args):
        return None

    monkeypatch.setattr(worker.queries, 'unnotified_discrepancies', unnotified)
    monkeypatch.setattr(worker.queries, 'mark_notified', mark)
    monkeypatch.setattr(worker.queries, 'get_period', none)

    async def telegram_down(text, markup):
        return False

    assert await worker.notify_findings(1001, telegram_down) == 0 and marked == []
    assert await worker.notify_findings(1001, Recorder()) == 1 and marked == [7]


async def test_a_run_with_unread_periods_is_not_a_success(worker_db, monkeypatch):
    async def fake_run(adapter, store, today, overlap):
        return engine.RunResult(periods_ok=1, periods_failed=1, periods_incomplete=1)

    async def factory():
        return object()

    monkeypatch.setattr(worker, 'run_sync', fake_run)
    notify = Recorder()
    await worker.run_once(1001, factory, notify, TODAY, 'UZS')

    error = worker_db['finished'][0]
    assert 'с ошибкой: 1' in error and 'прочитано не полностью: 1' in error
    assert worker_db['partial'] == [True]       # stored as last_result 'partial', not 'failed'
    assert any('не удалась' in call for call in notify.calls)
    # ...so the next automatic run comes after 15 minutes, not after the full interval
    assert worker.is_due(state(last_started_at=NOW - timedelta(minutes=16), last_finished_at=NOW - timedelta(minutes=15),
                               last_error=error), NOW, 180)


def test_unsigned_payments_are_shown_apart_in_the_totals():
    rows = [SimpleNamespace(**tx('100', direction='unknown').__dict__, own_transfer=False),
            SimpleNamespace(**tx('40').__dict__, own_transfer=False)]
    totals = reconcile.totals_by_account(rows)[('561468******6104', 'UZS')]
    assert (totals.expense, totals.unsigned, totals.count) == (D('40'), D('100'), 2)
    assert reconcile.consolidated({('x', 'UZS'): totals})['UZS'].unsigned == D('100')


def test_clickuz_module_keeps_unknowns_isolated():
    assert clickuz.PRESSABLE == ('callback', 'text')
