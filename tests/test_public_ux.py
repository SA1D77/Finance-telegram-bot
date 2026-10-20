import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

import money
from export import handlers as export_handlers
from middleware.owner import AccessMiddleware, ThrottleMiddleware
from onboarding import handlers as onboarding
from quick_input.parser import parse_quick_entry
from telegram import category_handlers, main_handlers, settings_handlers
from telegram.states import AddCategoryState, ExpenditureState, IncomeState
from telegram.validation import parse_amount

from conftest import USER_ID, make_callback, make_message, make_user

D = Decimal
N = '\xa0'


# --- Money formatting ---------------------------------------------------------------

@pytest.mark.parametrize('amount, currency, text', [
    ('1500000', 'UZS', f'1{N}500{N}000{N}сум'),
    ('1250.5', 'USD', f'$1{N}250.50'),
    ('-300', 'RUB', f'-300{N}₽'),
    ('-42', 'EUR', '-€42'),
    ('99.999', None, '100'),
    ('0', 'BYN', f'0{N}Br'),
])
def test_fmt(amount, currency, text):
    assert money.fmt(D(amount), currency) == text


def test_fmt_signed_and_uzbek_symbol():
    from i18n import i18n
    assert money.fmt(D('500'), None, signed=True) == '+500'
    with i18n.context(), i18n.use_locale('uz'):
        assert money.fmt(D('1000'), 'UZS') == f"1{N}000{N}so'm"


# --- Short amounts ----------------------------------------------------------------------

@pytest.mark.parametrize('text, amount', [
    ('500к', '500000'), ('500 k', '500000'), ('200 тыс', '200000'), ('1.5 млн', '1500000'), ('1,5 млн', '1500000'),
    ('300 ming', '300000'), ('2 mln', '2000000'), ('1 млрд', '1000000000'), ('2 000 000', '2000000'),
])
def test_parse_amount_with_multiplier(text, amount):
    assert parse_amount(text) == D(amount)


@pytest.mark.parametrize('text', ['к', 'abc млн', '500 кафе'])
def test_parse_amount_multiplier_rejects(text):
    assert parse_amount(text) is None


def test_quick_entry_with_multiplier():
    entry = parse_quick_entry('1.5 млн машина ремонт')
    assert entry.amount == D('1500000.00') and entry.words == ['машина', 'ремонт']
    assert parse_quick_entry('+12 млн зарплата').amount == D('12000000.00')


# --- Onboarding ---------------------------------------------------------------------------

@pytest.fixture
def new_user(harness, monkeypatch):
    harness.settings.update(locale=None, currency=None)
    calls = {'seeded': [], 'currency': [], 'lang': []}

    async def set_lang(user_id, lang):
        calls['lang'].append(lang)
        harness.settings['locale'] = lang

    async def set_cur(user_id, code):
        calls['currency'].append(code)
        harness.settings['currency'] = code

    async def get_settings(user_id):
        return harness.settings['locale'], harness.settings['currency']

    async def seed(user_id, locale):
        calls['seeded'].append(locale)
        return 15

    monkeypatch.setattr(onboarding, '_language_locale', set_lang)
    monkeypatch.setattr(onboarding, 'set_currency', set_cur)
    monkeypatch.setattr(onboarding, 'get_settings', get_settings)
    monkeypatch.setattr(onboarding, 'seed_default_categories', seed)
    return calls


async def test_new_user_picks_language_then_currency(harness, new_user):
    await harness.feed(make_message('/start'))
    first = harness.session.requests[-1]
    assert first.text.startswith('🌍')
    assert [b.callback_data for row in first.reply_markup.inline_keyboard for b in row] == [
        'onb:lang:ru', 'onb:lang:uz', 'onb:lang:en', 'onb:lang:be']

    await harness.feed(make_callback('onb:lang:uz'))
    edit = harness.session.requests[-2]
    codes = [b.callback_data for row in edit.reply_markup.inline_keyboard for b in row]
    assert codes[0] == 'onb:cur:UZS'  # the language's currency comes first
    assert set(codes) == {f'onb:cur:{c}' for c in money.CURRENCIES}

    await harness.feed(make_callback('onb:cur:UZS'))
    assert new_user == {'seeded': ['uz'], 'currency': ['UZS'], 'lang': ['uz']}
    keyboard = harness.session.requests[-2].reply_markup.keyboard
    assert keyboard[1][0].text == '➕ Daromad'  # the menu comes in the chosen language


@pytest.mark.parametrize('lang, currency', [('ru', 'RUB'), ('be', 'BYN'), ('en', 'USD'), ('uz', 'UZS')])
async def test_recommended_currency_follows_language(harness, new_user, lang, currency):
    await harness.feed(make_callback(f'onb:lang:{lang}'))

    edit = harness.session.requests[-2]
    assert edit.reply_markup.inline_keyboard[0][0].callback_data == f'onb:cur:{currency}'


async def test_any_message_before_onboarding_shows_language_picker(harness, new_user):
    await harness.feed(make_message('➕ Доход'))
    await harness.feed(make_callback('h:list:0'))

    assert harness.session.requests[0].text.startswith('🌍')
    assert new_user['seeded'] == []


async def test_bad_onboarding_values_ignored(harness, new_user):
    await harness.feed(make_callback('onb:lang:xx'))
    await harness.feed(make_callback('onb:cur:XXX'))

    assert new_user == {'seeded': [], 'currency': [], 'lang': []}


# --- Settings: currency and rates --------------------------------------------------------

async def test_change_currency_in_settings(harness, monkeypatch):
    chosen = []

    async def set_cur(user_id, code):
        chosen.append(code)

    monkeypatch.setattr(settings_handlers, 'set_currency', set_cur)
    await harness.feed(make_message('⚙️ Настройки'))
    assert ('💱 Валюта', 'currency') in [(b.text, b.callback_data) for row in harness.session.requests[-1].reply_markup.inline_keyboard for b in row]

    await harness.feed(make_callback('currency'))
    await harness.feed(make_callback('cur:KZT'))
    await harness.feed(make_callback('cur:XXX'))

    assert chosen == ['KZT']


async def test_rates_and_balance_in_dollars_and_euros(harness, monkeypatch):
    harness.settings['currency'] = 'UZS'
    rates = {('USD', 'UZS'): D('12650'), ('EUR', 'UZS'): D('13700'), ('RUB', 'UZS'): D('150'),
             ('UZS', 'USD'): D('0.0000790514'), ('UZS', 'EUR'): D('0.0000729927')}

    async def rate(code, base, day, today):
        return rates[(code, base)]

    async def balance(user_id):
        return D('12650000')

    monkeypatch.setattr(settings_handlers, 'get_rate', rate)
    monkeypatch.setattr(main_handlers, '_get_balance', balance)

    await harness.feed(make_callback('rates'))
    text = harness.session.requests[-2].text
    assert f'1 USD = 12{N}650{N}сум' in text and f'1 EUR = 13{N}700{N}сум' in text and f'1 RUB = 150{N}сум' in text

    await harness.feed(make_message('📊 Баланс'))
    assert harness.session.texts()[-1] == f'<b>12{N}650{N}000{N}сум</b>\n≈ $1{N}000 · €923.36'


async def test_balance_without_rates_still_shows_amount(harness, monkeypatch):
    harness.settings['currency'] = 'UZS'

    async def down(*args):
        from currency.rates import RateUnavailable
        raise RateUnavailable('x')

    async def balance(user_id):
        return D('500')

    monkeypatch.setattr(settings_handlers, 'get_rate', down)
    monkeypatch.setattr(main_handlers, '_get_balance', balance)

    await harness.feed(make_message('📊 Баланс'))

    assert harness.session.texts()[-1] == f'<b>500{N}сум</b>'


# --- Categories by kind ---------------------------------------------------------------------

@pytest.fixture
def cats(monkeypatch):
    store = {1: SimpleNamespace(id=1, name='такси', kind='expense'), 2: SimpleNamespace(id=2, name='зарплата', kind='income')}
    kinds = []
    saved = []

    async def show(user_id, kind=None):
        kinds.append(kind)
        return [c for c in store.values() if kind is None or c.kind in (kind, None)]

    async def add(category_name, user_id, kind=None):
        store[len(store) + 1] = SimpleNamespace(id=len(store) + 1, name=category_name, kind=kind)

    async def find(name, user_id):
        return next(c for c in store.values() if c.name == name)

    async def add_income(user_id, category_id, quantity):
        saved.append(('income', category_id, quantity))
        return uuid.uuid4()

    async def add_expense(user_id, category_id, quantity):
        saved.append(('expense', category_id, quantity))
        return uuid.uuid4()

    monkeypatch.setattr(main_handlers, '_show_all_categories', show)
    monkeypatch.setattr(main_handlers, '_add_new_category', add)
    monkeypatch.setattr(main_handlers, '_find_active_category', find)
    monkeypatch.setattr(main_handlers, '_add_income', add_income)
    monkeypatch.setattr(main_handlers, '_add_expenditure', add_expense)
    return SimpleNamespace(store=store, kinds=kinds, saved=saved)


async def test_income_shows_only_income_categories(harness, cats):
    await harness.feed(make_message('➕ Доход'))
    await harness.feed(make_message('5 млн'))

    assert cats.kinds == ['income']
    rows = harness.session.requests[-1].reply_markup.inline_keyboard
    assert [[b.callback_data for b in row] for row in rows] == [['category_2'], ['newcat'], ['cancel']]
    assert (await harness.data())['total'] == '5000000.00'


async def test_new_category_from_picker_saves_operation(harness, cats):
    await harness.feed(make_message('➖ Расход'))
    await harness.feed(make_message('45к'))
    await harness.feed(make_callback('newcat'))
    assert await harness.state() == ExpenditureState.new_category.state

    await harness.feed(make_message('  Бензин '))

    created = next(c for c in cats.store.values() if c.name == 'бензин')
    assert created.kind == 'expense'
    assert cats.saved == [('expense', created.id, D('45000.00'))]
    assert await harness.state() is None


async def test_new_income_category_gets_income_kind(harness, cats):
    await harness.set_state(IncomeState.category_input)
    await harness.storage.set_data(harness.key, {'total': '100.00'})
    await harness.feed(make_callback('newcat'))
    await harness.feed(make_message('фриланс'))

    assert next(c for c in cats.store.values() if c.name == 'фриланс').kind == 'income'


async def test_add_category_asks_kind(harness, monkeypatch):
    added = []

    async def no_duplicate(category, user_id):
        return None

    async def add(category_name, user_id, kind=None):
        added.append((category_name, kind))

    monkeypatch.setattr(category_handlers, 'category_already_exist', no_duplicate)
    monkeypatch.setattr(category_handlers, '_add_new_category', add)
    await harness.feed(make_message('📥 Добавить категорию'))
    await harness.feed(make_message('Спорт'))
    assert await harness.state() == AddCategoryState.kind.state

    await harness.feed(make_callback('addcat:expense'))

    assert added == [('спорт', 'expense')] and await harness.state() is None


async def test_category_list_is_grouped(harness, monkeypatch):
    async def show(user_id, kind=None):
        return [SimpleNamespace(id=1, name='такси', kind='expense'), SimpleNamespace(id=2, name='зарплата', kind='income'),
                SimpleNamespace(id=3, name='разное', kind=None)]

    monkeypatch.setattr(category_handlers, '_show_all_categories', show)
    await harness.feed(make_message('🗂 Категории'))

    text = harness.session.texts()[-1]
    assert text.index('Для расходов') < text.index('Такси') < text.index('Для доходов') < text.index('Зарплата') < text.index('Разное')


# --- Public access, flood protection, owner-only commands ----------------------------------

async def _passes(middleware, user_id, is_bot=False):
    hit = []

    async def handler(event, data):
        hit.append(data.get('is_owner'))

    await middleware(handler, None, {'event_from_user': make_user(user_id=user_id).model_copy(update={'is_bot': is_bot})})
    return hit


async def test_access_modes():
    personal = AccessMiddleware(owner_id=USER_ID, public=False)
    public = AccessMiddleware(owner_id=USER_ID, public=True)

    assert await _passes(personal, USER_ID) == [True]
    assert await _passes(personal, 2002) == []
    assert await _passes(public, 2002) == [False]
    assert await _passes(public, 3003, is_bot=True) == []


async def test_throttle_drops_floods_but_not_owner():
    throttle = ThrottleMiddleware(owner_id=USER_ID, limit=3, window=60)
    stranger = [await _passes(throttle, 2002) for _ in range(5)]
    owner = [await _passes(throttle, USER_ID) for _ in range(5)]

    assert [bool(x) for x in stranger] == [True, True, True, False, False]
    assert all(owner)


async def test_backup_is_owner_only(harness, monkeypatch):
    queued = []

    async def queue(chat_id):
        queued.append(chat_id)
        return True

    import backup.tasks
    monkeypatch.setattr(backup.tasks, 'queue_backup', queue)
    await harness.feed(make_message('/backup'))
    assert queued == [USER_ID]

    from i18n import i18n
    with i18n.context(), i18n.use_locale('ru'):
        await export_handlers.backup_command(SimpleNamespace(answer=_answer_collector(harness), chat=SimpleNamespace(id=2002)),
                                             is_owner=False)
    assert queued == [USER_ID]
    assert harness.session.requests[-1].text == 'Эта команда доступна только владельцу бота.'


def _answer_collector(harness):
    async def answer(text, **kwargs):
        harness.session.requests.append(SimpleNamespace(text=text))
    return answer
