from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from accounting.models import OperationType
from statements import handlers as st
from statements.parser import looks_like_statement, parse_statement

from conftest import USER_ID, make_callback, make_message

D = Decimal

SAMPLE = """🗓 Платежи c 17.10.2026 до 24.10.2026

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
🕖 19:11 23.10.2026
✅ Успешно

➖ 2'500 UZS
🔹 OOO ATTO TOLOV
💳 561468******6104
🕕 18:29 23.10.2026
✅ Успешно

➖ 200'000 UZS
🔹 HAMKORBANK ATB
💳 561468******6104
🕕 18:16 23.10.2026
✅ Успешно
"""


def test_parse_the_users_sample():
    payments = parse_statement(SAMPLE)

    assert [(p.amount, p.merchant, p.when, p.income, p.currency) for p in payments] == [
        (D('37000'), 'LLC MADINA QANDOLAT', datetime(2026, 10, 23, 21, 48), False, 'UZS'),
        (D('2500'), 'OOO ATTO TOLOV', datetime(2026, 10, 23, 19, 38), False, 'UZS'),
        (D('2500'), 'OOO ATTO TOLOV', datetime(2026, 10, 23, 19, 11), False, 'UZS'),
        (D('2500'), 'OOO ATTO TOLOV', datetime(2026, 10, 23, 18, 29), False, 'UZS'),
        (D('200000'), 'HAMKORBANK ATB', datetime(2026, 10, 23, 18, 16), False, 'UZS'),
    ]
    assert not any(p.failed for p in payments)


@pytest.mark.parametrize('line, amount', [
    ("➖ 1'500'000 UZS", '1500000'), ('➖ 1 500 000,50 сум', '1500000.50'), ('- 2’500 UZS', '2500'),
    ('➖ 1,500.75 USD', '1500.75'), ('➖ 1.500.000 UZS', '1500000'), ('➖ 99 UZS', '99'),
])
def test_amount_formats(line, amount):
    payment = parse_statement(f'{line}\n🔹 SHOP\n🕘 10:00 01.10.2026')[0]
    assert payment.amount == D(amount)


def test_income_failed_and_date_first():
    payments = parse_statement("➕ 5'000'000 UZS\n🔹 SALARY LLC\n23.10.2026 09:15\n\n"
                               "➖ 99'000 UZS\n🔹 KORZINKA\n🕕 18:00 22.10.2026\n❌ Отклонено")
    assert payments[0].income and payments[0].when == datetime(2026, 10, 23, 9, 15)
    assert payments[1].failed


@pytest.mark.parametrize('text, expected', [
    (SAMPLE, True), ('500 кафе', False), ('-300 такси', False), ('➖ 37 000 UZS', False), ('', False), (None, False),
])
def test_looks_like_statement(text, expected):
    assert looks_like_statement(text) is expected


# --- Bot flow --------------------------------------------------------------------------------

class FakeDB:
    def __init__(self):
        self.categories = {1: SimpleNamespace(id=1, name='продукты', kind='expense'),
                           2: SimpleNamespace(id=2, name='транспорт', kind='expense'),
                           3: SimpleNamespace(id=3, name='переводы', kind='expense'),
                           4: SimpleNamespace(id=4, name='зарплата', kind='income')}
        self.saved = []
        self.known = set()          # (amount, created_at, merchant) already in the database
        self.memory = {'OOO ATTO TOLOV': 2}

    async def show(self, user_id, kind=None):
        return [c for c in self.categories.values() if kind is None or c.kind in (kind, None)]

    async def get(self, category_id, user_id):
        return self.categories.get(category_id) if user_id == USER_ID else None

    async def add(self, **kwargs):
        self.saved.append(kwargs)

    async def imported(self, user_id, amount, created_at, merchant):
        return (amount, created_at, merchant) in self.known

    async def remembered(self, user_id, merchant, kind):
        return self.memory.get(merchant)


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(st, '_show_all_categories', fake.show)
    monkeypatch.setattr(st, '_get_category', fake.get)
    monkeypatch.setattr(st, '_add_operation', fake.add)
    monkeypatch.setattr(st, 'already_imported', fake.imported)
    monkeypatch.setattr(st, 'category_for_merchant', fake.remembered)
    return fake


def last(harness):
    return harness.session.requests[-1]


def buttons_of(method):
    return [(b.text, b.callback_data) for row in method.reply_markup.inline_keyboard for b in row]


async def test_import_one_by_one_with_memory_skip_and_bulk(harness, db, budget_checks):
    await harness.feed(make_message(SAMPLE))

    assert harness.session.texts()[-2] == 'Нашёл платежей: 5. Выберите категорию для каждого.'
    card = last(harness)
    assert 'Платёж 1 из 5' in card.text and 'LLC MADINA QANDOLAT' in card.text and '23.10.2026 21:48' in card.text
    assert ('⏭ Пропустить', 'st:skip') in buttons_of(card)

    await harness.feed(make_callback('st:cat:1'))           # Madina -> groceries
    card = harness.session.requests[-2]
    assert 'Платёж 2 из 5' in card.text
    assert buttons_of(card)[0] == ('✅ Транспорт (как в прошлый раз)', 'st:cat:2')

    await harness.feed(make_callback('st:cat:2'))           # ATTO -> transport (remembered)
    await harness.feed(make_callback('st:skip'))            # skip the third
    await harness.feed(make_callback('st:bulk'))
    await harness.feed(make_callback('st:all:3'))           # the rest -> transfers

    assert [(s['category_id'], s['quantity'], s['comment']) for s in db.saved] == [
        (1, D('37000.00'), 'LLC MADINA QANDOLAT'), (2, D('2500.00'), 'OOO ATTO TOLOV'),
        (3, D('2500.00'), 'OOO ATTO TOLOV'), (3, D('200000.00'), 'HAMKORBANK ATB')]
    assert db.saved[0]['created_at'] == datetime(2026, 10, 23, 16, 48)  # 21:48 in Tashkent = 16:48 UTC
    assert all(s['operation_type'] == OperationType.expenditure for s in db.saved)
    assert harness.session.requests[-2].text.startswith('✅ Готово. Записано: 4, пропущено: 1.')
    assert budget_checks == [USER_ID]


async def test_duplicates_are_not_offered_again(harness, db):
    db.known = {(D('37000.00'), datetime(2026, 10, 23, 16, 48), 'LLC MADINA QANDOLAT')}

    await harness.feed(make_message(SAMPLE))

    assert harness.session.texts()[-2] == 'Нашёл платежей: 4. Выберите категорию для каждого.'


async def test_everything_already_imported(harness, db):
    text = "➖ 37'000 UZS\n🔹 LLC MADINA QANDOLAT\n🕘 21:48 23.10.2026\n✅ Успешно"
    db.known = {(D('37000.00'), datetime(2026, 10, 23, 16, 48), 'LLC MADINA QANDOLAT')}

    await harness.feed(make_message(text))

    assert 'Уже были записаны раньше: 1.' in harness.session.texts()[-1]
    assert db.saved == []


async def test_stop_and_stale_buttons(harness, db):
    await harness.feed(make_message(SAMPLE))
    await harness.feed(make_callback('st:stop'))

    assert 'Записано: 0, пропущено: 5.' in harness.session.requests[-2].text
    await harness.feed(make_callback('st:cat:1'))
    assert last(harness).show_alert is True and db.saved == []


async def test_foreign_category_rejected(harness, db):
    await harness.feed(make_message(SAMPLE))

    await harness.feed(make_callback('st:cat:99'))

    assert last(harness).show_alert is True and db.saved == []


async def test_income_payment_offers_income_categories(harness, db):
    await harness.feed(make_message("➕ 5'000'000 UZS\n🔹 SALARY LLC\n🕘 09:15 23.10.2026\n✅ Успешно"))

    data = [d for _, d in buttons_of(last(harness))]
    assert 'st:cat:4' in data and 'st:cat:1' not in data
    await harness.feed(make_callback('st:cat:4'))
    assert db.saved[0]['operation_type'] == OperationType.income


async def test_apostrophe_amounts_in_quick_entry():
    from quick_input.parser import parse_quick_entry
    from telegram.validation import parse_amount

    assert parse_quick_entry("200'000 кафе").amount == D('200000.00')
    assert parse_amount('1’500’000') == D('1500000.00')
