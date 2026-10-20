from decimal import Decimal
from types import SimpleNamespace

import pytest

import middleware.ensure_user
from middleware import general_handlers
from telegram import main_handlers
from telegram.states import IncomeState, ExpenditureState, AddCategoryState

from conftest import USER_ID, make_callback, make_message, make_user

FOOD = SimpleNamespace(id=1, name='еда')
SALARY = SimpleNamespace(id=2, name='зарплата')


def use_categories(monkeypatch, *categories):
    by_id = {c.id: c for c in categories}

    async def show(user_id, kind=None):
        assert user_id == USER_ID
        return list(categories)

    async def get(category_id, user_id):
        assert user_id == USER_ID
        return by_id.get(category_id)

    monkeypatch.setattr(main_handlers, '_show_all_categories', show)
    monkeypatch.setattr(general_handlers, '_get_category', get)


async def test_income_button_starts_income_flow(harness):
    await harness.feed(make_message('➕ Доход'))

    assert await harness.state() == IncomeState.income_input.state
    assert harness.session.texts() == ['ДОХОД:', 'Введите ваш доход']


async def test_full_income_flow_saves_operation(harness, monkeypatch):
    saved = {}

    async def fake_add_income(user_id, category_id, quantity):
        saved.update(user_id=user_id, category_id=category_id, quantity=quantity)

    use_categories(monkeypatch, FOOD, SALARY)
    monkeypatch.setattr(main_handlers, '_add_income', fake_add_income)

    await harness.feed(make_message('➕ Доход'))
    await harness.feed(make_message('150,5'))
    assert await harness.state() == IncomeState.category_input.state
    assert (await harness.data())['total'] == '150.50'
    keyboard = harness.session.requests[-1].reply_markup.inline_keyboard
    assert [[b.callback_data for b in row] for row in keyboard] == [['category_1', 'category_2'], ['newcat'], ['cancel']]
    assert [b.text for b in keyboard[0]] == ['Еда', 'Зарплата']

    await harness.feed(make_callback('category_2'))

    assert saved == {'user_id': USER_ID, 'category_id': 2, 'quantity': Decimal('150.50')}
    assert await harness.state() is None
    assert 'ДОХОД <b>150.50 зарплата</b>' in harness.session.texts()


async def test_invalid_amount_keeps_state(harness):
    await harness.set_state(ExpenditureState.expenditure_input)

    await harness.feed(make_message('abc'))

    assert await harness.state() == ExpenditureState.expenditure_input.state
    assert 'дробные числа пишутся через точку' in harness.session.texts()[-1]


@pytest.mark.parametrize('stuck_in', [IncomeState.income_input, IncomeState.category_input, AddCategoryState.add_categories_input])
async def test_menu_button_leaves_unfinished_dialog(harness, monkeypatch, stuck_in):
    async def balance(user_id):
        return Decimal('10.00')

    monkeypatch.setattr(main_handlers, '_get_balance', balance)
    await harness.set_state(stuck_in)

    await harness.feed(make_message('📊 Баланс'))

    assert await harness.state() is None
    assert harness.session.texts() == ['ВАШ ТЕКУЩИЙ БАЛАНС:', '<b>10</b>']


async def test_command_leaves_unfinished_dialog(harness):
    await harness.set_state(IncomeState.category_input)

    await harness.feed(make_message('/start'))

    assert await harness.state() is None
    assert 'Привет!' in harness.session.texts()[-1]


async def test_text_while_waiting_for_a_button_gets_a_hint(harness):
    await harness.set_state(IncomeState.category_input)

    await harness.feed(make_message('хм'))

    assert await harness.state() == IncomeState.category_input.state
    assert harness.session.texts()[-1].startswith('Сейчас я жду выбор кнопкой выше')


async def test_unknown_button_is_answered(harness):
    await harness.feed(make_callback('something_old'))

    assert [type(m).__name__ for m in harness.session.requests] == ['AnswerCallbackQuery']


async def test_menu_text_in_english(harness, monkeypatch):
    harness.settings['locale'] = 'en'
    await harness.set_state(IncomeState.category_input)

    await harness.feed(make_message('➖ Expenditure'))

    assert await harness.state() == ExpenditureState.expenditure_input.state


async def test_balance_is_shown_exactly(harness, monkeypatch):
    async def balance(user_id):
        return Decimal('1749.60')

    monkeypatch.setattr(main_handlers, '_get_balance', balance)

    await harness.feed(make_message('📊 Баланс'))

    assert harness.session.texts() == ['ВАШ ТЕКУЩИЙ БАЛАНС:', '<b>1 749.60</b>']


async def test_cancel_works_in_any_state(harness):
    await harness.set_state(AddCategoryState.add_categories_input)

    await harness.feed(make_callback('cancel'))

    assert await harness.state() is None
    assert harness.session.texts()[:2] == ['Отменено', 'Гланое меню']


async def test_saved_language_translates_buttons_and_answers(harness, monkeypatch):
    harness.settings['locale'] = 'en'

    await harness.feed(make_message('➕ Income'))

    assert await harness.state() == IncomeState.income_input.state
    assert harness.session.texts() == ['INCOME:', 'Enter your income']


async def test_telegram_language_used_when_nothing_saved(harness):
    await harness.feed(make_message('➕ Income', user=make_user(language_code='en')))

    assert await harness.state() == IncomeState.income_input.state


async def test_user_without_username_works(harness):
    await harness.feed(make_message('➕ Доход', user=make_user(username=None)))

    assert await harness.state() == IncomeState.income_input.state


async def test_start_greets_new_user_with_sticker(harness, monkeypatch):
    calls = []

    async def created(user_id, username):
        calls.append((user_id, username))
        return True

    monkeypatch.setattr(middleware.ensure_user, 'ensure_user', created)

    await harness.feed(make_message('/start'))
    await harness.feed(make_message('/start'))

    assert calls == [(USER_ID, 'owner')]  # checked once per process
    names = [type(m).__name__ for m in harness.session.requests]
    assert names == ['SendSticker', 'SendMessage', 'SendMessage']
