from i18n import i18n
from telegram import buttons


def _texts(markup):
    return [[button.text for button in row] for row in markup.keyboard]


async def test_main_keyboard_layout_ru():
    with i18n.context(), i18n.use_locale('ru'):
        markup = await buttons.get_button_manage_money()

    assert _texts(markup) == [
        ['📊 Баланс'],
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
        markup = await buttons.categories_buttons('Еда\nКафе')

    rows = markup.inline_keyboard
    assert [row[0].callback_data for row in rows] == ['category_Еда', 'category_Кафе', 'cancel']


async def test_time_interval_buttons_in_one_row():
    with i18n.context(), i18n.use_locale('ru'):
        markup = await buttons.time_interval_buttons()

    assert [b.callback_data for b in markup.inline_keyboard[0]] == ['time_1', 'time_30', 'time_365']
