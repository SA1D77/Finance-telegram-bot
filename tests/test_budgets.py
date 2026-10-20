import uuid
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from accounting.models import OperationType
from budgets import handlers as b
from budgets.handlers import BudgetState
from budgets.service import BudgetStatus
from history import handlers as h
from quick_input import handlers as qi

from conftest import USER_ID, make_callback, make_message, make_user


class FakeBudgets:
    def __init__(self):
        self.categories = {1: 'кафе', 2: 'продукты'}
        self.budgets = {}  # category_id (None = overall) -> (budget_id, amount)
        self.spent = {None: Decimal('0'), 1: Decimal('0'), 2: Decimal('0')}
        self.next_id = 10

    async def statuses(self, user_id, now=None):
        assert user_id == USER_ID
        result = [BudgetStatus(budget_id=bid, category_id=cid, name=self.categories.get(cid), limit=amount,
                               spent=self.spent.get(cid, Decimal('0')))
                  for cid, (bid, amount) in self.budgets.items()]
        return sorted(result, key=lambda s: (s.category_id is not None, s.name or ''))

    async def set_budget(self, user_id, category_id, amount):
        bid = self.budgets.get(category_id, (self.next_id, None))[0]
        self.next_id += 1
        self.budgets[category_id] = (bid, amount)

    async def delete_budget(self, user_id, category_id):
        return self.budgets.pop(category_id, None) is not None

    async def get_category(self, category_id, user_id):
        name = self.categories.get(category_id) if user_id == USER_ID else None
        return SimpleNamespace(id=category_id, name=name) if name else None

    async def show_categories(self, user_id, kind=None):
        return [SimpleNamespace(id=i, name=n) for i, n in self.categories.items()]


@pytest.fixture
def fake(monkeypatch):
    f = FakeBudgets()
    monkeypatch.setattr(b, 'budget_statuses', f.statuses)
    monkeypatch.setattr(b, 'set_budget', f.set_budget)
    monkeypatch.setattr(b, 'delete_budget', f.delete_budget)
    monkeypatch.setattr(b, '_get_category', f.get_category)
    monkeypatch.setattr(b, '_show_all_categories', f.show_categories)
    monkeypatch.setattr(b, 'days_left', lambda now=None: 10)
    return f


def last(harness):
    return harness.session.requests[-1]


def buttons_of(method):
    return [(btn.text, btn.callback_data) for row in method.reply_markup.inline_keyboard for btn in row]


async def test_empty_budget_screen(harness, fake):
    await harness.feed(make_message('/budget'))

    assert 'Лимитов пока нет' in last(harness).text
    assert 'До конца месяца дней: 10' in last(harness).text
    assert [d for _, d in buttons_of(last(harness))] == ['b:set:total', 'b:cat']


async def test_menu_button_opens_budget(harness, fake):
    await harness.feed(make_message('🎯 Бюджет'))

    assert last(harness).text.startswith('🎯 Бюджет на ')


async def test_progress_for_overall_and_categories(harness, fake):
    fake.budgets = {None: (1, Decimal('50000.00')), 1: (2, Decimal('8000.00')), 2: (3, Decimal('10000.00'))}
    fake.spent = {None: Decimal('32000.00'), 1: Decimal('9000.00'), 2: Decimal('8500.00')}

    await harness.feed(make_message('/budget'))

    text = last(harness).text
    assert text.index('Общий бюджет') < text.index('Кафе') < text.index('Продукты')
    assert '🟢 <b>Общий бюджет</b>\n▓▓▓▓▓▓░░░░ 64%\n32\xa0000 из 50\xa0000, осталось 18\xa0000' in text
    assert 'Можно тратить 1\xa0800 в день' in text
    assert '🔴 <b>Кафе</b>\n▓▓▓▓▓▓▓▓▓▓ 112%\n9\xa0000 из 8\xa0000, перерасход 1\xa0000' in text
    assert '🟡 <b>Продукты</b>' in text
    assert ('🗑 Убрать лимит', 'b:rm') in buttons_of(last(harness))


async def test_set_overall_limit(harness, fake, budget_checks):
    await harness.feed(make_message('/budget'))
    await harness.feed(make_callback('b:set:total'))
    assert await harness.state() == BudgetState.amount.state
    assert '«Общий бюджет»' in harness.session.requests[-2].text

    await harness.feed(make_message('нет'))
    assert await harness.state() == BudgetState.amount.state

    await harness.feed(make_message('40000+10000'))

    assert fake.budgets[None][1] == Decimal('50000.00')
    assert await harness.state() is None
    assert budget_checks == [USER_ID]
    assert last(harness).text.startswith('✅ Сохранено')


async def test_set_category_limit(harness, fake):
    await harness.feed(make_callback('b:cat'))
    assert [d for _, d in buttons_of(harness.session.requests[-2])] == ['b:set:1', 'b:set:2', 'b:view']

    await harness.feed(make_callback('b:set:2'))
    await harness.feed(make_message('7 000'))

    assert fake.budgets[2][1] == Decimal('7000.00')


@pytest.mark.parametrize('data', ['b:set:99', 'b:set:abc', 'b:set:'])
async def test_foreign_or_bad_category_rejected(harness, fake, data):
    await harness.feed(make_callback(data))

    assert last(harness).show_alert is True
    assert await harness.state() is None


async def test_zero_removes_limit(harness, fake, budget_checks):
    fake.budgets = {1: (2, Decimal('8000.00'))}
    await harness.feed(make_callback('b:set:1'))

    await harness.feed(make_message('0'))

    assert fake.budgets == {}
    assert budget_checks == []


async def test_remove_limit_button(harness, fake):
    fake.budgets = {None: (1, Decimal('100')), 1: (2, Decimal('50'))}
    await harness.feed(make_callback('b:rm'))
    assert [d for _, d in buttons_of(harness.session.requests[-2])] == ['b:rm:1', 'b:rm:2', 'b:view']

    await harness.feed(make_callback('b:rm:2'))

    assert list(fake.budgets) == [None]


async def test_remove_unknown_budget_id_is_ignored(harness, fake):
    fake.budgets = {1: (2, Decimal('50'))}

    await harness.feed(make_callback('b:rm:777'))

    assert 1 in fake.budgets


async def test_budget_button_leaves_amount_input(harness, fake):
    await harness.feed(make_callback('b:set:total'))
    await harness.feed(make_callback('b:view'))

    assert await harness.state() is None


async def test_stranger_ignored(harness, fake):
    await harness.feed(make_message('/budget', user=make_user(user_id=2002)))
    await harness.feed(make_callback('b:set:total', user=make_user(user_id=2002)))

    assert harness.session.requests == []


# --- When a check is queued ---------------------------------------------------

@pytest.fixture
def quick(monkeypatch):
    async def categories(user_id, kind=None):
        return [SimpleNamespace(id=1, name='кафе'), SimpleNamespace(id=2, name='зарплата')]

    async def add_operation(**kwargs):
        return uuid.uuid4()

    monkeypatch.setattr(qi, '_show_all_categories', categories)
    monkeypatch.setattr(qi, '_add_operation', add_operation)


async def test_expense_queues_a_check(harness, quick, budget_checks):
    await harness.feed(make_message('500 кафе'))

    assert budget_checks == [USER_ID]


async def test_income_does_not_queue_a_check(harness, quick, budget_checks):
    await harness.feed(make_message('+500 зарплата'))

    assert budget_checks == []


async def test_history_edit_queues_a_check(harness, monkeypatch, budget_checks):
    row = SimpleNamespace(id=uuid.uuid4(), created_at=datetime(2026, 10, 1), quantity=Decimal('1'),
                          operation_type=OperationType.expenditure, comment=None, category_id=1, category='кафе')

    async def get(user_id, op_id):
        return row

    async def update(user_id, op_id, **values):
        row.quantity = values.get('quantity', row.quantity)
        return True

    monkeypatch.setattr(h, 'get_operation', get)
    monkeypatch.setattr(h, 'update_operation', update)
    await harness.feed(make_callback(f'h:amt:{row.id.hex}'))
    await harness.feed(make_message('900'))

    assert budget_checks == [USER_ID]


async def test_broker_failure_does_not_break_saving(harness, quick, monkeypatch):
    from budgets.tasks import check_budgets

    def broken(user_id):
        raise ConnectionError('redis is down')

    monkeypatch.setattr(check_budgets, 'delay', broken)

    await harness.feed(make_message('500 кафе'))

    assert 'Новая запись' in last(harness).text
