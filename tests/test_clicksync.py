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

    async def existing(self, keys):
        return {k: StoredTx(id=r['id'], dedup_key=k, status=r['tx'].status, amount=r['tx'].amount, delivery=r['delivery'],
                            description=r['tx'].description, ambiguous=r['ambiguous'])
                for k, r in self.rows.items() if k in keys}

    async def apply(self, period_id, report, plan, status, note, findings):
        if raise_in_apply[0]:
            raise_in_apply[0] = False
            raise RuntimeError('boom')
        for key, ambiguous, row in plan.inserts:
            self.rows[key] = {'id': len(self.rows) + 1, 'tx': row, 'ambiguous': ambiguous, 'delivery': 'new'}
        for update in plan.updates:
            record = next(r for r in self.rows.values() if r['id'] == update.stored.id)
            values = {k: v[1] for k, v in update.changes.items() if k != 'ambiguous'}
            record['tx'] = replace(record['tx'], **values)
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

PROFILE = Profile(reports_command='/reports', payments='Платежи', older='◀', download='', next_page='')


def report_text(start: date, body: str = '') -> str:
    return f'🗓 Платежи c {start:%d.%m.%Y} до {start + timedelta(days=6):%d.%m.%Y}\n\n{body}'


class ScriptedBot:
    """Answers like the real flow: /reports -> menu, Платежи -> newest report, ◀ -> the previous one."""

    NAV = [Button('◀ Назад', 'callback'), Button('Вперёд ▶', 'callback'), Button('Скачать', 'callback'),
           Button('Оплатить', 'callback'), Button('Сайт', 'url')]

    def __init__(self, reports: List[str]) -> None:
        self.reports = reports
        self.messages: List[BotMessage] = []
        self.sent: List[str] = []
        self.pressed: List[str] = []
        self.position = 0

    def _say(self, text, buttons):
        self.messages.append(BotMessage(id=len(self.messages) + 1, text=text, buttons=buttons))

    def _nav(self):
        buttons = list(self.NAV)
        if self.position == len(self.reports) - 1:
            buttons = buttons[1:]   # the oldest report has no ◀
        return buttons

    async def send(self, text):
        self.sent.append(text)
        if text == '/reports':
            self._say('Выберите отчёт', [Button('Платежи', 'callback'), Button('Пополнения', 'callback')])

    async def recent(self, limit=8):
        return list(reversed(self.messages))[:limit]

    async def press(self, message_id, index):
        message = next(m for m in self.messages if m.id == message_id)
        label = message.buttons[index].text
        self.pressed.append(label)
        if label == 'Платежи':
            self.position = 0
            self._say(self.reports[0], self._nav())
        elif label.startswith('◀'):
            self.position += 1
            # Like many bots: the report message is edited in place (same id, new text).
            self.messages[message_id - 1] = BotMessage(id=message_id, text=self.reports[self.position], buttons=self._nav())

    async def download(self, message_id, directory):
        raise AssertionError('not configured')


async def no_sleep(_seconds):
    return None


def adapter(bot, profile=PROFILE):
    return ClickAdapter(bot, profile, step_delay=0, reply_timeout=2, sleep=no_sleep)


async def test_adapter_opens_reports_and_walks_back():
    bot = ScriptedBot([report_text(date(2026, 11, 10), "➖ 1'000 UZS\n🔹 A\n🕘 10:00 11.11.2026\n✅ Успешно"),
                       report_text(date(2026, 11, 3), "➕ 2'000 UZS\n🔹 B\n🕘 10:00 04.11.2026\n✅ Успешно")])
    click = adapter(bot)

    first = await click.open()
    second = await click.older()
    end = await click.older()

    assert bot.sent == ['/reports'] and bot.pressed == ['Платежи', '◀ Назад']
    assert (first.start, first.transactions[0].amount) == (date(2026, 11, 10), D('1000'))
    assert (second.start, second.transactions[0].direction) == (date(2026, 11, 3), 'in')
    assert end is None      # no ◀ on the oldest report


async def test_adapter_never_presses_anything_outside_the_profile():
    bot = ScriptedBot([report_text(date(2026, 11, 10))])
    click = adapter(bot, replace(PROFILE, older='Оплатить'))  # misconfigured on purpose
    await click.open()
    with pytest.raises(engine.SyncError):
        await click._press(click.view, 'Сайт')                 # not in the profile
    with pytest.raises(engine.SyncError):
        await adapter(bot, replace(PROFILE, older='Сайт'))._press(bot.messages[-1], 'Сайт')   # a link, never pressed
    assert 'Сайт' not in bot.pressed


async def test_adapter_refuses_to_start_without_the_period_button_label():
    with pytest.raises(engine.SyncError, match='CLICK_BTN_OLDER'):
        await adapter(ScriptedBot([report_text(date(2026, 11, 10))]), replace(PROFILE, older='')).open()


async def test_adapter_reads_every_page_and_stops_on_a_repeated_one():
    pages = [report_text(date(2026, 11, 10), "➖ 1'000 UZS\n🔹 A\n🕘 10:00 11.11.2026\n✅ Успешно"),
             report_text(date(2026, 11, 10), "➖ 2'000 UZS\n🔹 B\n🕘 11:00 11.11.2026\n✅ Успешно")]

    class Paged(ScriptedBot):
        page = 0

        async def press(self, message_id, index):
            label = (next(m for m in self.messages if m.id == message_id)).buttons[index].text
            self.pressed.append(label)
            if label == 'Платежи':
                self._say(pages[0], [Button('◀ Назад', 'callback'), Button('Ещё', 'callback')])
            elif label == 'Ещё':
                self.page = min(self.page + 1, len(pages) - 1)      # after the last page it shows the last again
                self._say(pages[self.page], [Button('◀ Назад', 'callback'), Button('Ещё', 'callback')])

    bot = Paged([])
    report = await adapter(bot, replace(PROFILE, next_page='Ещё')).open()

    assert [t.amount for t in report.transactions] == [D('1000'), D('2000')]
    assert report.pages == 2 and report.complete


async def test_adapter_marks_unreadable_reports_incomplete():
    bot = ScriptedBot([report_text(date(2026, 11, 10), 'Новый формат\nстрока\nещё строка\nи ещё')])
    report = await adapter(bot).open()
    assert not report.complete and 'формат' in report.note


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


@pytest.fixture
def worker_db(monkeypatch):
    log = {'finished': [], 'notified': []}
    pending = iter([1, 4])

    async def mark_started(user_id):
        log['started'] = True

    async def get_state(user_id):
        return SimpleNamespace(last_error=log.get('previous_error'))

    async def mark_finished(user_id, error):
        log['finished'].append(error)

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


def test_clickuz_module_keeps_unknowns_isolated():
    assert clickuz.PRESSABLE == ('callback', 'text')
