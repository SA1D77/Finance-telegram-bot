import i18n
from telegram import main_handlers, common_handlers  # noqa: F401
from telegram.states import IncomeState, ExpenditureState, AddCategoryState

from conftest import make_callback, make_message, make_user


async def test_income_button_starts_income_flow(harness):
    await harness.feed(make_message('➕ Доход'))

    assert await harness.state() == IncomeState.income_input.state
    assert harness.session.texts() == ['ДОХОД:', 'Введите ваш доход']


async def test_full_income_flow_saves_operation(harness, monkeypatch):
    saved = {}

    async def fake_categories(username):
        return '\nЕда\nЗарплата'

    async def fake_add_income(username, category, quantity):
        saved.update(username=username, category=category, quantity=quantity)

    monkeypatch.setattr(main_handlers, '_show_all_categories', fake_categories)
    monkeypatch.setattr(main_handlers, '_add_income', fake_add_income)

    await harness.feed(make_message('➕ Доход'))
    await harness.feed(make_message('150.5'))
    assert await harness.state() == IncomeState.category_input.state
    assert (await harness.data())['total'] == 150.5

    await harness.feed(make_callback('category_Зарплата'))

    assert saved == {'username': 'owner', 'category': 'зарплата', 'quantity': 150.5}
    assert await harness.state() is None
    assert 'ДОХОД <b>150.5 зарплата</b>' in harness.session.texts()


async def test_invalid_amount_keeps_state(harness):
    await harness.set_state(ExpenditureState.expenditure_input)

    await harness.feed(make_message('abc'))

    assert await harness.state() == ExpenditureState.expenditure_input.state
    assert 'дробные числа пишутся через точку' in harness.session.texts()[-1]


async def test_menu_buttons_ignored_inside_input_state(harness):
    # Like aiogram 2: menu handlers only work without an active state,
    # so the text goes to the amount input handler instead.
    await harness.set_state(IncomeState.income_input)

    await harness.feed(make_message('📊 Баланс'))

    assert await harness.state() == IncomeState.income_input.state
    assert 'ВАШ ТЕКУЩИЙ БАЛАНС:' not in harness.session.texts()


async def test_cancel_works_in_any_state(harness):
    await harness.set_state(AddCategoryState.add_categories_input)

    await harness.feed(make_callback('cancel'))

    assert await harness.state() is None
    assert harness.session.texts()[:2] == ['Отменено', 'Гланое меню']


async def test_saved_language_translates_buttons_and_answers(harness, monkeypatch):
    async def saved_lang(username):
        return 'en'

    monkeypatch.setattr(i18n, '_get_user_lang', saved_lang)

    await harness.feed(make_message('➕ Income'))

    assert await harness.state() == IncomeState.income_input.state
    assert harness.session.texts() == ['INCOME:', 'Enter your income']


async def test_telegram_language_used_when_nothing_saved(harness):
    await harness.feed(make_message('➕ Income', user=make_user(language_code='en')))

    assert await harness.state() == IncomeState.income_input.state


async def test_user_without_username_does_not_crash_i18n(harness):
    await harness.feed(make_message('➕ Доход', user=make_user(username=None)))

    assert await harness.state() == IncomeState.income_input.state
