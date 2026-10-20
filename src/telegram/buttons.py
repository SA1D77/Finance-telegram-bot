import uuid
from typing import Optional

from aiogram import types

from config import FEATURE_ANALYTICS, FEATURE_BUDGETS, FEATURE_HISTORY, FEATURE_RECURRING
from i18n import _


async def get_button_manage_money(lang=None):
    button_income = types.KeyboardButton(text=_('➕ Доход', locale=lang))
    button_expense = types.KeyboardButton(text=_('➖ Расход', locale=lang))

    button_show_categories = types.KeyboardButton(text=_('🗂 Категории', locale=lang))
    button_add_category = types.KeyboardButton(text=_('📥 Добавить категорию', locale=lang))
    button_delete_category = types.KeyboardButton(text=_('📤 Удалить категорию', locale=lang))
    button_balance = types.KeyboardButton(text=_('📊 Баланс', locale=lang))
    first_row = [button_balance]
    if FEATURE_HISTORY:
        first_row.append(types.KeyboardButton(text=_('📜 История', locale=lang)))
    if FEATURE_BUDGETS:
        first_row.append(types.KeyboardButton(text=_('🎯 Бюджет', locale=lang)))
    button_settings = types.KeyboardButton(text=_('⚙️ Настройки', locale=lang))
    last_button = types.KeyboardButton(text=_('🕒 Последние 10 операций'))
    last_row = [button_settings, last_button]
    if FEATURE_ANALYTICS:
        last_row.append(types.KeyboardButton(text=_('📈 Аналитика', locale=lang)))

    extra_row = []
    if FEATURE_RECURRING:
        extra_row.append(types.KeyboardButton(text=_('🔁 Регулярные', locale=lang)))

    rows = [first_row,
            [button_income, button_expense],
            [button_show_categories, button_add_category, button_delete_category],
            last_row]
    if extra_row:
        rows.append(extra_row)
    button_manager = types.ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder=_('Выберите действие', locale=lang)
    )

    return button_manager


def undo_markup(operation_id: Optional[uuid.UUID]) -> Optional[types.InlineKeyboardMarkup]:
    """↩️ under a just-saved operation; handled by the history router."""
    if not FEATURE_HISTORY or operation_id is None:
        return None
    button = types.InlineKeyboardButton(text=_('↩️ Отменить'), callback_data=f'h:undo:{operation_id.hex}')
    return types.InlineKeyboardMarkup(inline_keyboard=[[button]])


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
    # Calendar periods in the local time zone (the old "last 1/30/365 days" buttons still work).
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=_('Сегодня'), callback_data='time_today'),
         types.InlineKeyboardButton(text=_('Неделя'), callback_data='time_week')],
        [types.InlineKeyboardButton(text=_('Месяц'), callback_data='time_month'),
         types.InlineKeyboardButton(text=_('Год'), callback_data='time_year')],
        [types.InlineKeyboardButton(text=_('📅 Свой период'), callback_data='time_custom')],
    ])


RU_BUTTON = types.InlineKeyboardButton(text='🇷🇺 Русский', callback_data='ru')
BE_BUTTON = types.InlineKeyboardButton(text='🇧🇾 Беларускі', callback_data='be')
EN_BUTTON = types.InlineKeyboardButton(text='🇺🇸 English', callback_data='en')

BUTTON_LANGUAGE = types.InlineKeyboardMarkup(inline_keyboard=[[RU_BUTTON], [BE_BUTTON], [EN_BUTTON]])
