from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from accounting.models import OperationType
from recurring import handlers as r
from recurring.handlers import RecurringState, _parse_start
from recurring.schedule import advance, due_dates, first_date, reminder_due
from timeutils import LOCAL_TZ

from conftest import USER_ID, make_callback, make_message, make_user

D = Decimal


# --- Schedule arithmetic -----------------------------------------------------------

@pytest.mark.parametrize('current, frequency, anchor, expected', [
    (date(2026, 10, 31), 'daily', 31, date(2026, 11, 1)),
    (date(2026, 12, 28), 'weekly', 0, date(2027, 1, 4)),
    (date(2026, 1, 31), 'monthly', 31, date(2026, 2, 28)),
    (date(2026, 2, 28), 'monthly', 31, date(2026, 3, 31)),   # anchor survives a short month
    (date(2028, 1, 31), 'monthly', 31, date(2028, 2, 29)),   # leap year
    (date(2026, 12, 5), 'monthly', 5, date(2027, 1, 5)),
])
def test_advance(current, frequency, anchor, expected):
    assert advance(current, frequency, anchor) == expected


def test_advance_rejects_unknown_frequency():
    with pytest.raises(ValueError):
        advance(date(2026, 1, 1), 'hourly', 1)


def test_first_date_anchor():
    assert first_date(date(2026, 10, 22), 'weekly') == (date(2026, 10, 22), 3)  # Thursday
    assert first_date(date(2026, 10, 31), 'monthly') == (date(2026, 10, 31), 31)


def test_due_dates_catch_up_and_future():
    assert due_dates(date(2026, 10, 20), date(2026, 10, 22), 'daily', 20) == (
        [date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)], date(2026, 10, 23))
    assert due_dates(date(2026, 10, 25), date(2026, 10, 22), 'daily', 25) == ([], date(2026, 10, 25))


def test_due_dates_cap():
    dates, nxt = due_dates(date(2020, 1, 1), date(2026, 10, 22), 'daily', 1)
    assert len(dates) == 62 and nxt == date(2026, 10, 23)


@pytest.mark.parametrize('today, reminded_for, expected', [
    (date(2026, 11, 2), None, True),       # 3 days before the 5th
    (date(2026, 11, 1), None, False),      # too early
    (date(2026, 11, 4), None, True),
    (date(2026, 11, 5), None, False),      # the day itself: the operation is created instead
    (date(2026, 11, 3), date(2026, 11, 5), False),  # already reminded
])
def test_reminder_due(today, reminded_for, expected):
    assert reminder_due(date(2026, 11, 5), 3, today, reminded_for) is expected


def test_no_reminder_when_disabled():
    assert reminder_due(date(2026, 11, 5), 0, date(2026, 11, 4), None) is False


@pytest.mark.parametrize('text, expected', [
    ('сегодня', date(2026, 10, 22)), ('Завтра', date(2026, 10, 23)), ('05.11', date(2026, 11, 5)),
    ('05.01', date(2027, 1, 5)), ('22.10.2026', date(2026, 10, 22)),
])
def test_parse_start(text, expected, monkeypatch):
    monkeypatch.setattr(r, 'local_now', lambda: datetime(2026, 10, 22, 10, 0, tzinfo=LOCAL_TZ))
    assert _parse_start(text, date(2026, 10, 22)) == expected


@pytest.mark.parametrize('text', ['21.10.2026', '01.01.2030', 'вчера', 'abc', '', None, '31.02'])
def test_parse_start_rejects(text, monkeypatch):
    monkeypatch.setattr(r, 'local_now', lambda: datetime(2026, 10, 22, 10, 0, tzinfo=LOCAL_TZ))
    assert _parse_start(text, date(2026, 10, 22)) is None


# --- Bot flows -----------------------------------------------------------------------

class FakeRecurring:
    def __init__(self):
        self.rows = {}
        self.categories = {1: 'интернет', 2: 'зарплата'}

    def _row(self, template_id, **values):
        return SimpleNamespace(id=template_id, user_id=USER_ID, category=self.categories[values['category_id']], **values)

    async def create(self, user_id, **values):
        template_id = len(self.rows) + 1
        self.rows[template_id] = self._row(template_id, reminded_for=None, **values)
        return template_id

    async def list_for(self, user_id):
        return sorted(self.rows.values(), key=lambda row: row.next_date)

    async def get(self, user_id, template_id):
        return self.rows.get(template_id) if user_id == USER_ID else None

    async def update_fields(self, user_id, template_id, **values):
        row = await self.get(user_id, template_id)
        if row is None:
            return False
        for key, value in values.items():
            setattr(row, key, value)
        return True

    async def skip_next(self, user_id, template_id):
        row = await self.get(user_id, template_id)
        row.next_date = advance(row.next_date, row.frequency, row.anchor_day)
        return row.next_date

    async def remove(self, user_id, template_id):
        return self.rows.pop(template_id, None) is not None

    async def categories_list(self, user_id):
        return [SimpleNamespace(id=i, name=n) for i, n in self.categories.items()]

    async def get_category(self, category_id, user_id):
        name = self.categories.get(category_id) if user_id == USER_ID else None
        return SimpleNamespace(id=category_id, name=name) if name else None


@pytest.fixture
def fake(monkeypatch):
    f = FakeRecurring()
    for name in ('create', 'list_for', 'get', 'update_fields', 'skip_next', 'remove'):
        monkeypatch.setattr(r.service, name, getattr(f, name))
    monkeypatch.setattr(r, '_show_all_categories', f.categories_list)
    monkeypatch.setattr(r, '_get_category', f.get_category)
    monkeypatch.setattr(r, 'local_now', lambda: datetime(2026, 10, 22, 10, 0, tzinfo=LOCAL_TZ))
    return f


def last(harness):
    return harness.session.requests[-1]


def buttons_of(method):
    return [(b.text, b.callback_data) for row in method.reply_markup.inline_keyboard for b in row]


async def test_create_monthly_template(harness, fake):
    await harness.feed(make_message('🔁 Регулярные'))
    assert 'Пока пусто' in last(harness).text

    await harness.feed(make_callback('r:new'))
    assert await harness.state() == RecurringState.entry.state
    await harness.feed(make_message('5000 интернет домашний'))
    assert [d for _, d in buttons_of(last(harness))] == ['r:freq:daily', 'r:freq:weekly', 'r:freq:monthly', 'cancel']

    await harness.feed(make_callback('r:freq:monthly'))
    assert await harness.state() == RecurringState.start_date.state
    await harness.feed(make_message('вчера'))
    assert await harness.state() == RecurringState.start_date.state
    await harness.feed(make_message('31.10'))
    assert [d for _, d in buttons_of(last(harness))] == ['r:rem:0', 'r:rem:1', 'r:rem:3', 'r:rem:7']

    await harness.feed(make_callback('r:rem:3'))

    row = fake.rows[1]
    assert (row.amount, row.category_id, row.comment, row.frequency, row.anchor_day, row.next_date, row.remind_days) == (
        D('5000.00'), 1, 'домашний', 'monthly', 31, date(2026, 10, 31), 3)
    assert row.operation_type == OperationType.expenditure
    card = harness.session.requests[-2].text
    assert 'каждый месяц, 31-го' in card and 'Следующая: 31.10.2026' in card and 'Напомнить за 3 дн.' in card


async def test_unknown_category_asks_to_pick(harness, fake):
    await harness.feed(make_callback('r:new'))
    await harness.feed(make_message('+90000 зп'))
    assert [d for _, d in buttons_of(last(harness))] == ['r:cat:1', 'r:cat:2', 'cancel']

    await harness.feed(make_callback('r:cat:9'))
    assert last(harness).show_alert is True

    await harness.feed(make_callback('r:cat:2'))
    await harness.feed(make_callback('r:freq:weekly'))
    await harness.feed(make_message('сегодня'))
    await harness.feed(make_callback('r:rem:0'))

    row = fake.rows[1]
    assert row.operation_type == OperationType.income and row.category_id == 2 and row.anchor_day == 3


async def test_stale_draft_buttons(harness, fake):
    await harness.feed(make_callback('r:freq:daily'))
    await harness.feed(make_callback('r:rem:1'))

    assert fake.rows == {}


async def _one(fake):
    return await fake.create(USER_ID, category_id=1, operation_type=OperationType.expenditure, amount=D('5000.00'),
                             comment=None, frequency='monthly', anchor_day=5, next_date=date(2026, 11, 5), remind_days=0)


async def test_card_skip_amount_date_delete(harness, fake):
    template_id = await _one(fake)
    await harness.feed(make_callback(f'r:card:{template_id}'))
    assert ('⏭ Пропустить', f'r:skip:{template_id}') in buttons_of(harness.session.requests[-2])

    await harness.feed(make_callback(f'r:skip:{template_id}'))
    assert fake.rows[template_id].next_date == date(2026, 12, 5)
    assert 'Пропущено. Следующая: 05.12.2026' in harness.session.requests[-2].text

    await harness.feed(make_callback(f'r:amt:{template_id}'))
    await harness.feed(make_message('5500+500'))
    assert fake.rows[template_id].amount == D('6000.00')

    await harness.feed(make_callback(f'r:date:{template_id}'))
    await harness.feed(make_message('10.11'))
    assert fake.rows[template_id].next_date == date(2026, 11, 10)

    await harness.feed(make_callback(f'r:del:{template_id}'))
    assert template_id in fake.rows
    await harness.feed(make_callback(f'r:delok:{template_id}'))
    assert fake.rows == {}


@pytest.mark.parametrize('data', ['r:card:99', 'r:skip:99', 'r:delok:abc', 'r:amt:'])
async def test_unknown_template(harness, fake, data):
    await harness.feed(make_callback(data))

    assert last(harness).show_alert is True


async def test_stranger_ignored(harness, fake):
    template_id = await _one(fake)
    await harness.feed(make_callback(f'r:delok:{template_id}', user=make_user(user_id=2002)))

    assert template_id in fake.rows and harness.session.requests == []
