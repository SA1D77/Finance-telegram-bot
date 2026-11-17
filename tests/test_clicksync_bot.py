"""/click in the bot: status, start date, the queue of imported payments and the ledger export."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from accounting.models import OperationType
from clicksync import handlers as ch
from clicksync.handlers import ClickState
from timeutils import utc_now

from conftest import USER_ID, make_callback, make_message, make_user

D = Decimal


def source_tx(**kw):
    values = dict(id=5, amount=D('37000'), currency='UZS', direction='out', status='completed',
                  occurred_at=datetime(2026, 11, 12, 21, 48), description='LLC MADINA QANDOLAT', account='561468******6104',
                  fee=None, fee_included=None, ambiguous=False, delivery='new', accounting_id=None, own_transfer=False,
                  source_id=None, period_id=1, first_seen_at=datetime(2026, 11, 13, 0, 0))
    values.update(kw)
    return SimpleNamespace(**values)


class FakeQueries:
    def __init__(self):
        self.state = SimpleNamespace(start_date=date(2026, 11, 1), paused=False, sync_requested=False,
                                     heartbeat_at=utc_now(), last_ok_at=None, last_error=None, history_complete=True)
        self.queue = [source_tx()]
        self.delivered = []
        self.ignored = []
        self.requested = 0
        self.start = None

    async def ensure_state(self, user_id):
        pass

    async def get_state(self, user_id):
        return self.state

    async def period_summary(self, user_id):
        return {'counts': {'completed': 3}, 'last_ok': SimpleNamespace(period_start=date(2026, 11, 10), period_end=date(2026, 11, 16)),
                'problems': [], 'earliest': date(2026, 11, 1)}

    async def pending_count(self, user_id):
        return len(self.queue)

    async def open_discrepancies(self, user_id):
        return []

    async def next_pending(self, user_id):
        return self.queue[0] if self.queue else None

    async def get_transaction(self, user_id, transaction_id):
        return next((t for t in self.queue if t.id == transaction_id), None)

    async def remembered_category(self, user_id, description, kind):
        return 1

    async def deliver(self, *args):
        self.delivered.append(args)
        self.queue.pop(0)
        return 'ok'

    async def ignore(self, user_id, transaction_id, own_transfer=False):
        self.ignored.append((transaction_id, own_transfer))
        self.queue.pop(0)
        return True

    async def request_sync(self, user_id):
        self.requested += 1

    async def set_start_date(self, user_id, value):
        self.start = value

    async def last_accounting_date(self, user_id):
        return datetime(2026, 11, 14, 10, 0)

    async def all_transactions(self, user_id):
        return [source_tx(delivery='delivered', amount=D('1500.5'))]


@pytest.fixture
def fq(monkeypatch):
    fake = FakeQueries()
    for name in ('ensure_state', 'get_state', 'period_summary', 'pending_count', 'open_discrepancies', 'next_pending',
                 'get_transaction', 'remembered_category', 'deliver', 'ignore', 'request_sync', 'set_start_date',
                 'last_accounting_date', 'all_transactions'):
        monkeypatch.setattr(ch.queries, name, getattr(fake, name))

    async def categories(user_id, kind=None):
        return [SimpleNamespace(id=1, name='кафе', kind='expense'), SimpleNamespace(id=2, name='такси', kind='expense')]

    async def category(category_id, user_id):
        return SimpleNamespace(id=category_id, name='кафе') if category_id in (1, 2) else None

    monkeypatch.setattr(ch, '_show_all_categories', categories)
    monkeypatch.setattr(ch, '_get_category', category)
    return fake


def buttons_of(method):
    return [(b.text, b.callback_data) for row in method.reply_markup.inline_keyboard for b in row]


async def test_status_shows_progress_and_controls(harness, fq):
    await harness.feed(make_message('/click'))

    answer = harness.session.requests[-1]
    for part in ('Импорт с: 01.11.2026', 'Помощник: работает', 'Последний полный период: 10.11.2026–16.11.2026',
                 'Периодов: готово 3'):
        assert part in answer.text
    data = [d for _, d in buttons_of(answer)]
    assert data[:3] == ['cs:q', 'cs:now', 'cs:start'] and 'cs:pause' in data and 'cs:x' in data


async def test_other_users_get_nothing(harness, fq):
    await harness.feed(make_message('/click', user=make_user(user_id=2002)))
    assert harness.session.requests == []


async def test_classify_records_with_utc_time_and_moves_on(harness, fq):
    await harness.feed(make_callback('cs:q'))
    card = harness.session.requests[-2]
    assert 'Платёж из Click' in card.text and '37 000 UZS' in card.text  # money.number: no-break spaces
    assert buttons_of(card)[0] == ('✅ Кафе (как в прошлый раз)', 'cs:c:5:1')

    await harness.feed(make_callback('cs:c:5:1'))

    [args] = fq.delivered
    assert args[:6] == (USER_ID, 5, 1, D('37000.00'), OperationType.expenditure, 'LLC MADINA QANDOLAT')
    assert args[6] == datetime(2026, 11, 12, 16, 48)    # 21:48 in Tashkent
    assert 'Все платежи разобраны' in harness.session.requests[-2].text


async def test_stale_button_does_not_record_again(harness, fq):
    fq.queue[0].delivery = 'delivered'
    await harness.feed(make_callback('cs:c:5:1'))
    assert fq.delivered == []


async def test_own_transfer_is_not_income_or_expense(harness, fq):
    await harness.feed(make_callback('cs:o:5'))
    assert fq.ignored == [(5, True)] and fq.delivered == []
    assert 'перевод между своими' in harness.session.requests[-2].text


async def test_sync_now_and_start_date(harness, fq):
    await harness.feed(make_callback('cs:now'))
    assert fq.requested == 1

    await harness.feed(make_callback('cs:start'))
    assert await harness.state() == ClickState.start_date.state
    await harness.feed(make_message('31.02.2026'))
    assert fq.start is None
    await harness.feed(make_message('01.10.2026'))
    assert fq.start == date(2026, 10, 1) and await harness.state() is None
    assert 'В учёте уже есть операции после этой даты' in harness.session.requests[-1].text


def test_start_date_parsing():
    today = date(2026, 11, 16)
    assert ch.parse_start_date('01.10.2026', today) == date(2026, 10, 1)
    assert ch.parse_start_date('1.10.26', today) == date(2026, 10, 1)
    assert ch.parse_start_date((today + timedelta(days=1)).strftime('%d.%m.%Y'), today) is None
    assert ch.parse_start_date('завтра', today) is None


def test_ledger_export_keeps_exact_amounts():
    data = ch.ledger_csv([source_tx(amount=D('1500.5000'), fee=D('3'), fee_included=False, delivery='linked')]).decode('utf-8-sig')
    header, row = data.strip().split('\r\n')
    assert header.startswith('Дата и время (Click);Направление;Сумма')
    assert row.split(';')[:6] == ['2026-11-12 21:48', 'out', '1500,5', 'UZS', '3', 'нет'] and 'связан' in row


def test_reconciliation_without_balances_says_so():
    text = ch.reconciliation_text([source_tx(), source_tx(id=6, direction='in', amount=D('5000000'), own_transfer=True)], [],
                                  {'delivered': 1, 'new': 1})
    assert 'Сверку баланса выполнить нельзя' in text
    assert 'приход 0 UZS, расход 37 000 UZS, переводы между своими 5 000 000 UZS' in text.replace(' ', ' ')
