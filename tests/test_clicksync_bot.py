"""/click in the bot: status, start date, the queue of imported payments and the ledger export."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from accounting.models import OperationType
from clicksync import balance_handlers as bh
from clicksync import handlers as ch
from clicksync.balance_handlers import BalanceState
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
                                     heartbeat_at=utc_now(), last_ok_at=None, last_error=None, history_complete=True,
                                     last_result=None, balance_requested=False, last_balance_ok_at=None,
                                     last_balance_error=None)
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


def balance_interval(**kw):
    # 15.11 21:00 – 17.11 09:00 in Tashkent: a span of days, never "today"
    values = dict(id=9, card='HAMK***6104', last4='6104', currency='UZS', start_at=datetime(2026, 11, 15, 16, 0),
                  end_at=datetime(2026, 11, 17, 4, 0), opening=D('1000000'), closing=D('950000'), incoming=D('0'),
                  outgoing=D('0'), fees=D('0'), expected=D('1000000'), explained=D('0'), remaining=D('-50000'),
                  status='unexplained', reason=None, unresolved=False)
    values.update(kw)
    return SimpleNamespace(**values)


class FakeBalance:
    def __init__(self):
        self.interval = balance_interval()
        self.explained = []
        self.tokens = set()
        self.unresolved = []
        self.snoozed = []
        self.matched = []
        self.candidates = []
        self.requested = 0

    async def open_intervals(self, user_id, now):
        return [self.interval] if self.interval.status == 'unexplained' and not self.interval.unresolved else []

    async def other_intervals(self, user_id, now):
        return []

    async def latest_balances(self, user_id):
        return None, []

    async def get_interval(self, user_id, interval_id):
        return self.interval if interval_id == self.interval.id else None

    async def interval_explanations(self, user_id, interval_id):
        return []

    async def request_balance(self, user_id):
        self.requested += 1

    async def explain(self, user_id, interval_id, token, kind, direction, amount, category_id=None, operation=None):
        if token in self.tokens:
            return 'duplicate'
        self.tokens.add(token)
        self.explained.append((interval_id, kind, direction, amount, category_id, operation))
        self.interval.remaining -= amount if direction == 'in' else -amount
        if self.interval.remaining == 0:
            self.interval.status = 'explained'
        return 'ok'

    async def mark_unresolved(self, user_id, interval_id):
        self.unresolved.append(interval_id)
        self.interval.unresolved = True
        return True

    async def snooze(self, user_id, interval_id, until):
        self.snoozed.append(interval_id)
        return True

    async def explanation_candidates(self, user_id, transaction_id, boundary, to_utc):
        return self.candidates

    async def apply_match(self, user_id, explanation_id, transaction_id, reason, boundary=None, to_utc=None):
        self.matched.append((explanation_id, transaction_id, reason))
        return True


@pytest.fixture
def fq(monkeypatch):
    fake = FakeQueries()
    for name in ('ensure_state', 'get_state', 'period_summary', 'pending_count', 'open_discrepancies', 'next_pending',
                 'get_transaction', 'remembered_category', 'deliver', 'ignore', 'request_sync', 'set_start_date',
                 'last_accounting_date', 'all_transactions'):
        monkeypatch.setattr(ch.queries, name, getattr(fake, name))
    fake.balance = FakeBalance()
    for name in ('open_intervals', 'other_intervals', 'latest_balances', 'get_interval', 'interval_explanations',
                 'request_balance', 'explain', 'mark_unresolved', 'snooze', 'explanation_candidates', 'apply_match'):
        monkeypatch.setattr(ch.balance_queries, name, getattr(fake.balance, name))

    async def categories(user_id, kind=None):
        return [SimpleNamespace(id=1, name='кафе', kind='expense'), SimpleNamespace(id=2, name='такси', kind='expense')]

    async def category(category_id, user_id):
        return SimpleNamespace(id=category_id, name='кафе') if category_id in (1, 2) else None

    async def no_budget_check(user_id):
        return None

    for module in (ch, bh):
        monkeypatch.setattr(module, '_show_all_categories', categories)
        monkeypatch.setattr(module, '_get_category', category)
    monkeypatch.setattr(bh, 'schedule_budget_check', no_budget_check)
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


async def test_payment_without_a_sign_asks_the_direction_first(harness, fq, monkeypatch):
    fq.queue[0].direction = 'unknown'
    chosen = []

    async def set_direction(user_id, transaction_id, direction):
        chosen.append((transaction_id, direction))
        fq.queue[0].direction = direction
        return True

    monkeypatch.setattr(ch.queries, 'set_direction', set_direction)
    await harness.feed(make_callback('cs:q'))
    card = harness.session.requests[-2]
    assert 'нет знака' in card.text
    assert ('➖ Расход', 'cs:dir:5:out') in buttons_of(card) and not any(d.startswith('cs:c:') for _, d in buttons_of(card))
    assert 'cs:o:5' not in [d for _, d in buttons_of(card)]     # an own transfer needs a direction first

    await harness.feed(make_callback('cs:dir:5:out'))

    assert chosen == [(5, 'out')]
    assert ('✅ Кафе (как в прошлый раз)', 'cs:c:5:1') in buttons_of(harness.session.requests[-2])


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


def plain(text):
    return text.replace(' ', ' ').replace(' ', ' ')


async def test_status_shows_balance_controls_and_partial_result(harness, fq):
    fq.state.last_result, fq.state.last_error = 'partial', 'часть периодов не загружена'
    await harness.feed(make_message('/click'))
    answer = harness.session.requests[-1]
    assert 'прошла частично' in answer.text and 'Неясных изменений баланса: 1' in answer.text
    data = [d for _, d in buttons_of(answer)]
    assert 'cb:now' in data and 'cb:list' in data


async def test_manual_balance_check_is_requested(harness, fq):
    await harness.feed(make_callback('cb:now'))
    assert fq.balance.requested == 1


async def test_interval_question_names_the_real_span_and_masks_the_card(harness, fq):
    await harness.feed(make_callback('cb:v:9'))
    view = harness.session.requests[-2]
    text = plain(view.text)
    assert ('По карте ••••6104 с 15.11.2026 21:00 до 17.11.2026 09:00 осталось на 50 000 сум меньше, '
            'чем получается по операциям. На что ушли деньги?') in text
    assert 'сегодня' not in text and 'HAMK' not in text
    for part in ('Баланс в начале: 1 000 000 сум', 'Должно быть по операциям: 1 000 000 сум', 'Баланс в конце: 950 000 сум',
                 'Не объяснено: <b>−50 000 сум</b>'):
        assert part in text
    data = [d for _, d in buttons_of(view)]
    assert {'cb:k:9:ex', 'cb:k:9:fe', 'cb:k:9:ow', 'cb:k:9:ad', 'cb:u:9', 'cb:s:9'} <= set(data)
    assert 'cb:k:9:in' not in data      # money went out: income is not offered


async def test_explaining_a_part_records_once_after_confirmation(harness, fq):
    await harness.feed(make_callback('cb:k:9:ex'))
    assert ('Кафе', 'cb:c:9:ex:1') in [(t.capitalize(), d) for t, d in buttons_of(harness.session.requests[-2])]
    await harness.feed(make_callback('cb:c:9:ex:1'))
    assert await harness.state() == BalanceState.amount.state

    await harness.feed(make_message('60 000'))      # more than is unexplained
    assert 'не больше' in harness.session.requests[-1].text and fq.balance.explained == []
    await harness.feed(make_message("20'000"))
    confirm = harness.session.requests[-1]
    assert 'Подтвердите' in confirm.text and fq.balance.explained == []     # nothing before the confirmation
    [ok] = [d for _, d in buttons_of(confirm) if d.startswith('cb:ok:')]

    await harness.feed(make_callback(ok))
    [(interval_id, kind, direction, value, category_id, operation)] = fq.balance.explained
    assert (interval_id, kind, direction, value, category_id) == (9, 'expense', 'out', D('20000'), 1)
    assert operation['quantity'] == D('20000.00') and operation['operation_type'] == OperationType.expenditure
    assert operation['created_at'] == datetime(2026, 11, 17, 4, 0) and '••••6104' in operation['comment']
    assert 'Не объяснено: <b>−30 000 сум</b>' in plain(harness.session.requests[-2].text)

    await harness.feed(make_callback(ok))           # a second click on the same button
    assert len(fq.balance.explained) == 1


async def test_own_transfer_explains_without_accounting(harness, fq):
    await harness.feed(make_callback('cb:k:9:ow'))
    await harness.feed(make_callback('cb:a:9'))
    [ok] = [d for _, d in buttons_of(harness.session.requests[-2]) if d.startswith('cb:ok:')]
    await harness.feed(make_callback(ok))
    [(_, kind, direction, value, category_id, operation)] = fq.balance.explained
    assert (kind, direction, value, category_id, operation) == ('own_transfer', 'out', D('50000'), None, None)


async def test_dont_remember_and_later(harness, fq):
    await harness.feed(make_callback('cb:s:9'))
    await harness.feed(make_callback('cb:u:9'))
    assert fq.balance.snoozed == [9] and fq.balance.unresolved == [9] and fq.balance.explained == []
    assert 'Не помню' in harness.session.requests[-2].text


async def test_a_payment_that_may_be_an_explanation_is_offered_for_linking(harness, fq):
    explanation = SimpleNamespace(id=3, amount=D('37000'), currency='UZS')
    fq.balance.candidates = [(explanation, fq.balance.interval, D('37000'))]
    await harness.feed(make_callback('cs:q'))
    card = harness.session.requests[-2]
    assert 'уже объяснили при сверке баланса' in card.text and buttons_of(card)[0][1] == 'cb:m:5:3'

    await harness.feed(make_callback('cb:m:5:3'))
    assert fq.balance.matched == [(3, 5, 'owner')]


def test_custom_amount_parsing():
    assert bh.parse_custom_amount('50 000') == D('50000')
    assert bh.parse_custom_amount("1'250,5") == D('1250.5')
    for bad in ('', 'много', '-5', '0', 'NaN'):
        assert bh.parse_custom_amount(bad) is None


def test_reconciliation_without_balances_says_so():
    text = ch.reconciliation_text([source_tx(), source_tx(id=6, direction='in', amount=D('5000000'), own_transfer=True)], [],
                                  {'delivered': 1, 'new': 1})
    assert 'Сверку баланса выполнить нельзя' in text
    assert 'приход 0 UZS, расход 37 000 UZS, переводы между своими 5 000 000 UZS' in text.replace(' ', ' ')
