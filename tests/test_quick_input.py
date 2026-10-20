from decimal import Decimal
from types import SimpleNamespace

import pytest

import config
from accounting.models import OperationType
from i18n import i18n
from quick_input import handlers as qi
from telegram import main_handlers
from telegram.messages import QUICK_INPUT_HELP
from telegram.states import IncomeState

from conftest import USER_ID, make_callback, make_message, make_user


class FakeDB:
    """In-memory stand-in for the data functions the quick input router uses."""

    def __init__(self, *names):
        self.categories = {i: SimpleNamespace(id=i, name=n, user_id=USER_ID) for i, n in enumerate(names, start=1)}
        self.categories[99] = SimpleNamespace(id=99, name='чужая', user_id=2002)  # someone else's
        self.saved = []

    async def show(self, user_id):
        return [c for c in self.categories.values() if c.user_id == user_id]

    async def get(self, category_id, user_id):
        category = self.categories.get(category_id)
        return category if category and category.user_id == user_id else None

    async def find(self, name, user_id):
        return next((c for c in self.categories.values() if c.name == name and c.user_id == user_id), None)

    async def add_category(self, category_name, user_id):
        if not await self.find(category_name, user_id):
            new_id = max(self.categories) + 1
            self.categories[new_id] = SimpleNamespace(id=new_id, name=category_name, user_id=user_id)

    async def add_operation(self, **kwargs):
        self.saved.append(kwargs)


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB('кафе', 'продукты', 'зарплата', 'подарки', 'подписки')
    monkeypatch.setattr(qi, '_show_all_categories', fake.show)
    monkeypatch.setattr(qi, '_get_category', fake.get)
    monkeypatch.setattr(qi, '_find_active_category', fake.find)
    monkeypatch.setattr(qi, '_add_new_category', fake.add_category)
    monkeypatch.setattr(qi, '_add_operation', fake.add_operation)
    return fake


def last_buttons(harness):
    markup = harness.session.requests[-1].reply_markup
    return [(b.text, b.callback_data) for row in markup.inline_keyboard for b in row]


async def test_exact_category_saves_immediately(harness, db):
    await harness.feed(make_message('500 кафе обед с коллегами'))

    assert db.saved == [{'user_id': USER_ID, 'category_id': 1, 'quantity': Decimal('500.00'),
                         'operation_type': OperationType.expenditure, 'comment': 'обед с коллегами'}]
    text = harness.session.texts()[-1]
    assert 'РАСХОД <b>500.00 кафе</b>' in text and '💬 обед с коллегами' in text


async def test_plus_sign_means_income(harness, db):
    await harness.feed(make_message('+50000 зарплата'))

    assert db.saved[0]['operation_type'] == OperationType.income
    assert db.saved[0]['comment'] is None


async def test_calculation_is_shown_before_saving(harness, db):
    await harness.feed(make_message('1200+350*2 продукты'))

    assert db.saved == []
    assert '🧮 1200+350*2 = <b>1900.00</b>' in harness.session.texts()[-1]
    assert last_buttons(harness)[0] == ('✅ Сохранить', 'qi:save')

    await harness.feed(make_callback('qi:save'))
    await harness.feed(make_callback('qi:save'))  # double tap

    assert len(db.saved) == 1
    assert db.saved[0]['quantity'] == Decimal('1900.00') and db.saved[0]['category_id'] == 2


async def test_fuzzy_category_asks_for_confirmation(harness, db):
    await harness.feed(make_message('300 кафэ'))

    assert db.saved == []
    assert 'Категория «кафе»?' in harness.session.texts()[-1]
    assert last_buttons(harness)[:2] == [('✅ Да, «кафе»', 'qi:cat:1'), ('🗂 Другая категория', 'qi:pick')]

    await harness.feed(make_callback('qi:cat:1'))

    assert db.saved[0]['category_id'] == 1


async def test_ambiguous_category_offers_candidates(harness, db):
    await harness.feed(make_message('100 под'))

    assert 'Какую категорию вы имели в виду?' in harness.session.texts()[-1]
    assert {data for _, data in last_buttons(harness)} >= {'qi:cat:4', 'qi:cat:5', 'qi:pick', 'cancel'}

    await harness.feed(make_callback('qi:cat:5'))

    assert db.saved[0]['category_id'] == 5


async def test_unknown_category_can_be_created(harness, db):
    await harness.feed(make_message('150 Одежда футболка'))

    assert 'Категории «Одежда» нет.' in harness.session.texts()[-1]
    assert last_buttons(harness)[0] == ('➕ Создать «одежда»', 'qi:new')

    await harness.feed(make_callback('qi:new'))

    created = await db.find('одежда', USER_ID)
    assert db.saved == [{'user_id': USER_ID, 'category_id': created.id, 'quantity': Decimal('150.00'),
                         'operation_type': OperationType.expenditure, 'comment': 'футболка'}]


async def test_amount_only_lets_user_pick_category(harness, db):
    await harness.feed(make_message('250'))

    assert [data for _, data in last_buttons(harness)] == ['qi:cat:1', 'qi:cat:2', 'qi:cat:3', 'qi:cat:4', 'qi:cat:5', 'cancel']

    await harness.feed(make_callback('qi:cat:3'))

    assert db.saved[0]['category_id'] == 3


async def test_pick_shows_all_categories(harness, db):
    await harness.feed(make_message('300 кафэ'))
    await harness.feed(make_callback('qi:pick'))

    edit = harness.session.requests[-2]  # edit_text, then answer_callback_query
    assert [b.callback_data for row in edit.reply_markup.inline_keyboard for b in row][-1] == 'cancel'
    assert len(edit.reply_markup.inline_keyboard) == 6


@pytest.mark.parametrize('data', ['qi:cat:99', 'qi:cat:abc', 'qi:cat:', 'qi:cat:12345'])
async def test_foreign_or_bad_category_id_is_rejected(harness, db, data):
    await harness.feed(make_message('300 кафэ'))

    await harness.feed(make_callback(data))

    assert db.saved == []
    assert harness.session.requests[-1].show_alert is True


async def test_stale_button_without_draft(harness, db):
    await harness.feed(make_callback('qi:save'))

    assert db.saved == []
    assert 'устарела' in harness.session.requests[-1].text


async def test_cancel_drops_the_draft(harness, db):
    await harness.feed(make_message('1+1 кафе'))
    await harness.feed(make_callback('cancel'))
    await harness.feed(make_callback('qi:save'))

    assert db.saved == []
    assert 'устарела' in harness.session.requests[-1].text


async def test_menu_still_works_with_open_draft(harness, db):
    await harness.feed(make_message('1+1 кафе'))
    await harness.feed(make_message('➕ Доход'))

    assert await harness.state() == IncomeState.income_input.state


async def test_bad_amount_gets_a_hint(harness, db):
    await harness.feed(make_message('5/0 кафе'))

    assert db.saved == []
    assert 'Не получилось посчитать сумму' in harness.session.texts()[-1]


async def test_plain_text_is_not_quick_input(harness, db):
    await harness.feed(make_message('привет'))

    assert harness.session.requests == []


async def test_category_names_are_html_escaped(harness, db):
    await harness.feed(make_message('10 <b>x</b>'))

    assert '&lt;b&gt;x&lt;/b&gt;' in harness.session.texts()[-1]


async def test_stranger_quick_entry_is_ignored(harness, db):
    await harness.feed(make_message('500 кафе', user=make_user(user_id=2002)))
    await harness.feed(make_callback('qi:save', user=make_user(user_id=2002)))

    assert db.saved == [] and harness.session.requests == []


async def test_button_flow_accepts_calculation(harness, monkeypatch, db):
    async def show(user_id):
        return list((await db.show(user_id)))

    monkeypatch.setattr(main_handlers, '_show_all_categories', show)
    await harness.feed(make_message('➕ Доход'))

    await harness.feed(make_message('1000+500'))

    assert (await harness.data())['total'] == '1500.00'
    assert harness.session.texts()[-1].startswith('🧮 1000+500 = <b>1500.00</b>')


def test_feature_flag_parsing(monkeypatch):
    for raw, expected in [(None, True), ('1', True), ('0', False), (' 0 ', False)]:
        if raw is None:
            monkeypatch.delenv('FEATURE_QUICK_INPUT', raising=False)
        else:
            monkeypatch.setenv('FEATURE_QUICK_INPUT', raw)
        assert config._flag('FEATURE_QUICK_INPUT') is expected


def test_quick_help_is_translated():
    with i18n.context():
        assert i18n.gettext(QUICK_INPUT_HELP, locale='en').startswith('⚡ Quick entry')
        assert i18n.gettext(QUICK_INPUT_HELP, locale='be').startswith('⚡ Хуткі ўвод')
        assert i18n.gettext('➕ Создать «{name}»', locale='en').format(name='x') == '➕ Create “x”'
