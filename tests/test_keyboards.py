from types import SimpleNamespace

from i18n import i18n
from telegram import buttons


def _texts(markup):
    return [[button.text for button in row] for row in markup.keyboard]


async def test_main_keyboard_layout_ru():
    with i18n.context(), i18n.use_locale('ru'):
        markup = await buttons.get_button_manage_money()

    assert _texts(markup) == [
        ['📊 Баланс', '📜 История', '🎯 Бюджет'],
        ['➕ Доход', '➖ Расход'],
        ['🗂 Категории', '📥 Добавить категорию', '📤 Удалить категорию'],
        ['⚙️ Настройки', '🕒 Последние 10 операций'],
    ]


async def test_main_keyboard_explicit_locale_en():
    with i18n.context(), i18n.use_locale('ru'):
        markup = await buttons.get_button_manage_money(lang='en')

    assert _texts(markup)[1] == ['➕ Income', '➖ Expenditure']


async def test_categories_keyboard_has_cancel_and_callback_prefix():
    with i18n.context(), i18n.use_locale('ru'):
        markup = await buttons.categories_buttons([SimpleNamespace(id=3, name='еда'), SimpleNamespace(id=7, name='кафе и рестораны')])

    rows = markup.inline_keyboard
    assert [row[0].callback_data for row in rows] == ['category_3', 'category_7', 'cancel']
    assert [row[0].text for row in rows][:2] == ['Еда', 'Кафе и рестораны']


async def test_long_category_name_fits_callback_limit():
    with i18n.context(), i18n.use_locale('ru'):
        markup = await buttons.categories_buttons([SimpleNamespace(id=123456, name='я' * 64)])

    assert len(markup.inline_keyboard[0][0].callback_data.encode()) <= 64


async def test_time_interval_buttons_in_one_row():
    with i18n.context(), i18n.use_locale('ru'):
        markup = await buttons.time_interval_buttons()

    assert [b.callback_data for b in markup.inline_keyboard[0]] == ['time_1', 'time_30', 'time_365']
