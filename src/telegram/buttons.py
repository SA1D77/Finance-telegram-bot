from aiogram import types

from i18n import _


async def get_button_manage_money(lang=None):
    button_income = types.KeyboardButton(text=_('➕ Доход', locale=lang))
    button_expense = types.KeyboardButton(text=_('➖ Расход', locale=lang))

    button_show_categories = types.KeyboardButton(text=_('🗂 Категории', locale=lang))
    button_add_category = types.KeyboardButton(text=_('📥 Добавить категорию', locale=lang))
    button_delete_category = types.KeyboardButton(text=_('📤 Удалить категорию', locale=lang))
    button_balance = types.KeyboardButton(text=_('📊 Баланс', locale=lang))
    button_settings = types.KeyboardButton(text=_('⚙️ Настройки', locale=lang))
    last_button = types.KeyboardButton(text=_('🕒 Последние 10 операций'))

    button_manager = types.ReplyKeyboardMarkup(
        keyboard=[[button_balance],
                  [button_income, button_expense],
                  [button_show_categories, button_add_category, button_delete_category],
                  [button_settings, last_button]],
        resize_keyboard=True,
        input_field_placeholder=_('Выберите действие', locale=lang)
    )

    return button_manager


async def get_button_cancel():
    button_cancel = types.InlineKeyboardButton(text=_('⬅️ Отмена'), callback_data='cancel')

    return types.InlineKeyboardMarkup(inline_keyboard=[[button_cancel]])


async def get_report_buttons():
    chat_button = types.KeyboardButton(text=_('📌 Чат'))
    email_button = types.KeyboardButton(text=_('📧 Почта'))

    report_button = types.ReplyKeyboardMarkup(
        keyboard=[[chat_button, email_button]],
        resize_keyboard=True
    )

    return report_button


async def get_email_remember_buttons():
    yes_button = types.KeyboardButton(text=_('⭕️ Да'))
    no_button = types.KeyboardButton(text=_('❌ Нет'))

    email_remember_button = types.ReplyKeyboardMarkup(
        keyboard=[[yes_button, no_button]],
        resize_keyboard=True
    )

    return email_remember_button


async def categories_buttons(categories):
    # The id keeps callback_data short (Telegram allows 64 bytes) whatever the name is.
    rows = [[types.InlineKeyboardButton(text=category.name.capitalize(), callback_data=f'category_{category.id}')]
            for category in categories]
    rows.append([types.InlineKeyboardButton(text=_('⬅️ Отмена'), callback_data='cancel')])

    return types.InlineKeyboardMarkup(inline_keyboard=rows)


async def settings_button():
    button_language = types.InlineKeyboardButton(text=_('🌐 Язык'), callback_data='language')
    help_button = types.InlineKeyboardButton(text=_('❓ Помощь'), callback_data='help')

    return types.InlineKeyboardMarkup(inline_keyboard=[[button_language], [help_button]])


async def time_interval_buttons():
    day_button = types.InlineKeyboardButton(text=_('День'), callback_data='time_1')
    month_button = types.InlineKeyboardButton(text=_('Месяц'), callback_data='time_30')
    year_button = types.InlineKeyboardButton(text=_('Год'), callback_data='time_365')

    return types.InlineKeyboardMarkup(inline_keyboard=[[day_button, month_button, year_button]])


RU_BUTTON = types.InlineKeyboardButton(text='🇷🇺 Русский', callback_data='ru')
BE_BUTTON = types.InlineKeyboardButton(text='🇧🇾 Беларускі', callback_data='be')
EN_BUTTON = types.InlineKeyboardButton(text='🇺🇸 English', callback_data='en')

BUTTON_LANGUAGE = types.InlineKeyboardMarkup(inline_keyboard=[[RU_BUTTON], [BE_BUTTON], [EN_BUTTON]])
