import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from accounting.models import OperationType
from history import handlers as h
from history.handlers import HistoryEditState
from quick_input import handlers as qi
from telegram import main_handlers
from middleware import general_handlers
from telegram.states import IncomeState

from conftest import USER_ID, make_callback, make_message, make_user

OTHER = 2002
EXP, INC = OperationType.expenditure, OperationType.income


class FakeHistory:
    def __init__(self):
        self.categories = {1: 'кафе', 2: 'продукты', 3: 'зарплата'}
        self.category_owner = {1: USER_ID, 2: USER_ID, 3: USER_ID, 9: OTHER}
        self.ops = {}
        self.calls = []
        start = datetime(2026, 10, 1, 9, 0)
        for i in range(12):
            self.add(USER_ID, start + timedelta(days=i), Decimal(100 + i), EXP if i % 3 else INC, 1 + i % 3,
                     comment='обед' if i == 11 else None)
        self.foreign = self.add(OTHER, start, Decimal('999'), EXP, 9)

    def add(self, user_id, created_at, quantity, op_type, category_id, comment=None):
        op_id = uuid.uuid4()
        self.ops[op_id] = SimpleNamespace(id=op_id, user_id=user_id, created_at=created_at, quantity=quantity,
                                          operation_type=op_type, comment=comment, category_id=category_id,
                                          category=self.categories.get(category_id, 'чужая'))
        return op_id

    def _mine(self, user_id, category_id=None, since=None):
        def keep(op):
            in_category = category_id is None or op.category_id == category_id
            in_period = since is None or op.created_at >= since
            return op.user_id == user_id and in_category and in_period

        rows = [op for op in self.ops.values() if keep(op)]
        return sorted(rows, key=lambda op: op.created_at, reverse=True)

    async def list_operations(self, user_id, offset, limit, category_id=None, since=None, until=None):
        self.calls.append(('list', offset, category_id, since))
        rows = self._mine(user_id, category_id, since)
        return rows[offset:offset + limit], len(rows)

    async def get_operation(self, user_id, op_id):
        op = self.ops.get(op_id)
        return op if op and op.user_id == user_id else None

    async def last_operation(self, user_id):
        rows = self._mine(user_id)
        return rows[0] if rows else None

    async def update_operation(self, user_id, op_id, **values):
        op = await self.get_operation(user_id, op_id)
        if op is None:
            return False
        for key, value in values.items():
            setattr(op, key, value)
        if 'category_id' in values:
            op.category = self.categories[values['category_id']]
        self.calls.append(('update', values))
        return True

    async def delete_operation(self, user_id, op_id):
        if await self.get_operation(user_id, op_id) is None:
            return False
        del self.ops[op_id]
        return True

    async def used_categories(self, user_id):
        return [SimpleNamespace(id=i, name=n) for i, n in self.categories.items() if self.category_owner[i] == user_id]

    async def get_category(self, category_id, user_id):
        if self.category_owner.get(category_id) == user_id and category_id in self.categories:
            return SimpleNamespace(id=category_id, name=self.categories[category_id])
        return None

    async def show_categories(self, user_id):
        return await self.used_categories(user_id)


@pytest.fixture
def hist(monkeypatch):
    fake = FakeHistory()
    for name in ('list_operations', 'get_operation', 'last_operation', 'update_operation', 'delete_operation', 'used_categories'):
        monkeypatch.setattr(h, name, getattr(fake, name))
    monkeypatch.setattr(h, '_get_category', fake.get_category)
    monkeypatch.setattr(h, '_show_all_categories', fake.show_categories)
    return fake


def last(harness):
    return harness.session.requests[-1]


def buttons_of(method):
    return [(b.text, b.callback_data) for row in method.reply_markup.inline_keyboard for b in row]


def newest(hist, n=0):
    return hist._mine(USER_ID)[n]


async def test_history_lists_newest_first_with_paging(harness, hist):
    await harness.feed(make_message('/history'))

    text = last(harness).text
    assert 'Страница 1 из 3, всего 12' in text
    assert text.index('12.10') < text.index('08.10')
    numbers = [d for _, d in buttons_of(last(harness)) if d.startswith('h:op:')]
    assert numbers == [f'h:op:{op.id.hex}' for op in hist._mine(USER_ID)[:5]]
    assert ('▶️', 'h:list:1') in buttons_of(last(harness))
    assert '999' not in text  # someone else's operation


async def test_menu_button_opens_history(harness, hist):
    await harness.feed(make_message('📜 История'))

    assert 'Страница 1 из 3' in last(harness).text


async def test_next_page(harness, hist):
    await harness.feed(make_message('/history'))
    await harness.feed(make_callback('h:list:1'))

    edit = harness.session.requests[-2]
    assert 'Страница 2 из 3' in edit.text
    assert ('◀️', 'h:list:0') in buttons_of(edit)


async def test_page_past_the_end_falls_back_to_last(harness, hist):
    await harness.feed(make_message('/history'))
    await harness.feed(make_callback('h:list:40'))

    assert 'Страница 3 из 3' in harness.session.requests[-2].text


async def test_period_filter(harness, hist):
    await harness.feed(make_message('/history'))
    await harness.feed(make_callback('h:per'))
    await harness.feed(make_callback('h:per:month'))

    _, offset, category_id, since = hist.calls[-1]
    assert since is not None and offset == 0
    assert 'Период: этот месяц' in harness.session.requests[-2].text
    assert ('✖️ Сбросить фильтры', 'h:reset') in buttons_of(harness.session.requests[-2])


async def test_unknown_period_is_ignored(harness, hist):
    await harness.feed(make_message('/history'))
    calls = len(hist.calls)

    await harness.feed(make_callback('h:per:forever'))

    assert len(hist.calls) == calls


async def test_category_filter_and_reset(harness, hist):
    await harness.feed(make_message('/history'))
    await harness.feed(make_callback('h:cat:2'))

    assert hist.calls[-1][2] == 2
    assert 'Категория: продукты' in harness.session.requests[-2].text

    await harness.feed(make_callback('h:reset'))
    assert hist.calls[-1][2] is None


async def test_foreign_category_filter_is_ignored(harness, hist):
    await harness.feed(make_message('/history'))
    calls = len(hist.calls)

    await harness.feed(make_callback('h:cat:9'))

    assert len(hist.calls) == calls


async def test_card_shows_operation(harness, hist):
    op = newest(hist)
    await harness.feed(make_callback(f'h:op:{op.id.hex}'))

    edit = harness.session.requests[-2]
    assert '12.10.2026 14:00' in edit.text  # 09:00 UTC shown in Tashkent time
    assert '💬 обед' in edit.text
    assert ('🗑 Удалить', f'h:del:{op.id.hex}') in buttons_of(edit)


@pytest.mark.parametrize('data', ['h:op:' + '0' * 32, 'h:op:zz', 'h:op:'])
async def test_unknown_or_bad_operation_id(harness, hist, data):
    await harness.feed(make_callback(data))

    assert last(harness).show_alert is True


async def test_foreign_operation_is_not_found(harness, hist):
    await harness.feed(make_callback(f'h:op:{hist.foreign.hex}'))
    await harness.feed(make_callback(f'h:delok:{hist.foreign.hex}'))
    await harness.feed(make_callback(f'h:undo:{hist.foreign.hex}'))

    assert hist.foreign in hist.ops
    assert all(m.show_alert for m in harness.session.requests)


async def test_edit_amount_with_calculation(harness, hist):
    op = newest(hist)
    await harness.feed(make_callback(f'h:amt:{op.id.hex}'))
    assert await harness.state() == HistoryEditState.amount.state

    await harness.feed(make_message('abc'))
    assert await harness.state() == HistoryEditState.amount.state

    await harness.feed(make_message('1200+300'))

    assert op.quantity == Decimal('1500.00')
    assert await harness.state() is None
    assert last(harness).text.startswith('✅ Сохранено')


async def test_edit_comment_and_clear_it(harness, hist):
    op = newest(hist, 1)
    await harness.feed(make_callback(f'h:cmt:{op.id.hex}'))
    await harness.feed(make_message('  с   друзьями '))
    assert op.comment == 'с друзьями'

    await harness.feed(make_callback(f'h:cmt:{op.id.hex}'))
    await harness.feed(make_message('-'))
    assert op.comment is None


async def test_edit_date(harness, hist):
    op = newest(hist)
    await harness.feed(make_callback(f'h:date:{op.id.hex}'))
    await harness.feed(make_message('31.02'))
    assert await harness.state() == HistoryEditState.date.state

    await harness.feed(make_message('05.10.2026 10:00'))

    assert op.created_at == datetime(2026, 10, 5, 5, 0)  # 10:00 Tashkent = 05:00 UTC


async def test_edit_category(harness, hist):
    op = newest(hist)
    await harness.feed(make_callback(f'h:ecat:{op.id.hex}'))
    assert [d for _, d in buttons_of(harness.session.requests[-2])][:3] == ['h:setcat:1', 'h:setcat:2', 'h:setcat:3']

    await harness.feed(make_callback('h:setcat:2'))

    assert op.category_id == 2 and op.category == 'продукты'


async def test_edit_category_rejects_foreign_category(harness, hist):
    op = newest(hist)
    before = op.category_id
    await harness.feed(make_callback(f'h:ecat:{op.id.hex}'))

    await harness.feed(make_callback('h:setcat:9'))

    assert op.category_id == before
    assert last(harness).show_alert is True


async def test_delete_needs_confirmation(harness, hist):
    op = newest(hist)
    await harness.feed(make_callback(f'h:del:{op.id.hex}'))

    assert op.id in hist.ops
    assert 'Удалить эту операцию?' in harness.session.requests[-2].text

    await harness.feed(make_callback(f'h:delok:{op.id.hex}'))

    assert op.id not in hist.ops
    assert 'Операция удалена.' in harness.session.requests[-2].text


async def test_undo_button_removes_exactly_that_operation(harness, hist):
    op = newest(hist, 3)
    count = len(hist.ops)
    await harness.feed(make_callback(f'h:undo:{op.id.hex}'))

    assert op.id not in hist.ops and len(hist.ops) == count - 1
    assert '↩️ Отменено' in harness.session.requests[-2].text

    await harness.feed(make_callback(f'h:undo:{op.id.hex}'))  # second tap
    assert last(harness).show_alert is True


async def test_undo_command_asks_about_last_operation(harness, hist):
    op = newest(hist)
    await harness.feed(make_message('/undo'))

    assert 'Последняя операция:' in last(harness).text
    assert ('🗑 Да, удалить', f'h:delok:{op.id.hex}') in buttons_of(last(harness))
    assert op.id in hist.ops


async def test_any_history_button_leaves_edit_mode(harness, hist, monkeypatch):
    async def no_categories(user_id):
        return []

    monkeypatch.setattr(qi, '_show_all_categories', no_categories)  # "777" becomes a quick entry
    op = newest(hist)
    await harness.feed(make_callback(f'h:amt:{op.id.hex}'))
    await harness.feed(make_callback('h:list:0'))

    assert await harness.state() is None
    await harness.feed(make_message('777'))  # no longer edits the amount
    assert op.quantity != Decimal('777')


async def test_stranger_cannot_use_history(harness, hist):
    stranger = make_user(user_id=OTHER)
    await harness.feed(make_message('/history', user=stranger))
    await harness.feed(make_callback(f'h:delok:{hist.foreign.hex}', user=stranger))

    assert harness.session.requests == []
    assert hist.foreign in hist.ops


# --- Undo button under saved operations ---------------------------------------

async def test_button_flow_save_offers_undo(harness, monkeypatch):
    new_id = uuid.uuid4()

    async def add_income(user_id, category_id, quantity):
        return new_id

    async def category(category_id, user_id):
        return SimpleNamespace(id=1, name='зарплата')

    monkeypatch.setattr(main_handlers, '_add_income', add_income)
    monkeypatch.setattr(general_handlers, '_get_category', category)
    await harness.set_state(IncomeState.category_input)
    await harness.storage.set_data(harness.key, {'total': '10.00'})

    await harness.feed(make_callback('category_1'))

    edit = next(m for m in harness.session.requests if type(m).__name__ == 'EditMessageText')
    assert buttons_of(edit) == [('↩️ Отменить', f'h:undo:{new_id.hex}')]


async def test_quick_entry_save_offers_undo(harness, monkeypatch):
    new_id = uuid.uuid4()

    async def categories(user_id):
        return [SimpleNamespace(id=1, name='кафе')]

    async def add_operation(**kwargs):
        return new_id

    monkeypatch.setattr(qi, '_show_all_categories', categories)
    monkeypatch.setattr(qi, '_add_operation', add_operation)

    await harness.feed(make_message('500 кафе'))

    assert buttons_of(last(harness)) == [('↩️ Отменить', f'h:undo:{new_id.hex}')]
