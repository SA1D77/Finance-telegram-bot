"""First start: language -> main currency -> ready-made categories. Included before every other router."""
from typing import Any, Dict, Optional

from aiogram import F, Router, types

import money
from i18n import i18n
from middleware.accounting import seed_default_categories
from middleware.user import _language_locale, get_settings, set_currency
from telegram import buttons
from telegram.messages import QUICK_INPUT_HELP

router = Router(name='onboarding')

LANGUAGES = {'ru': '🇷🇺 Русский', 'uz': "🇺🇿 O'zbekcha", 'en': '🇬🇧 English', 'be': '🇧🇾 Беларуская'}
LANGUAGE_PROMPT = '🌍 Выберите язык · Tilni tanlang · Choose language · Абярыце мову'


def needs_onboarding(event: Any, user_settings: Optional[Dict[str, Any]] = None) -> bool:
    """A user who has not picked a main currency yet (None; '' or a code means done)."""
    return user_settings is not None and user_settings.get('currency') is None


def language_markup(prefix: str = 'onb:lang:') -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=label, callback_data=f'{prefix}{code}')] for code, label in LANGUAGES.items()])


def currency_markup(lang: Optional[str], prefix: str = 'onb:cur:') -> types.InlineKeyboardMarkup:
    recommended = money.DEFAULT_BY_LANGUAGE.get(lang or '', 'USD')
    codes = [recommended] + [code for code in money.CURRENCIES if code != recommended]
    rows = [[types.InlineKeyboardButton(text=f'{money.FLAGS.get(code, "")} {code} · {money.symbol(code, lang)}',
                                        callback_data=f'{prefix}{code}')] for code in codes]
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def currency_prompt(lang: Optional[str]) -> str:
    return i18n.gettext('💱 Выберите основную валюту. Её можно поменять в настройках.', locale=lang)


@router.callback_query(F.data.startswith('onb:lang:'))
async def language_chosen(callback_query: types.CallbackQuery):
    lang = callback_query.data[len('onb:lang:'):]
    if lang not in LANGUAGES:
        await callback_query.answer()
        return
    await _language_locale(user_id=callback_query.from_user.id, lang=lang)
    await callback_query.message.edit_text(text=currency_prompt(lang), reply_markup=currency_markup(lang))
    await callback_query.answer()


@router.callback_query(F.data.startswith('onb:cur:'))
async def currency_chosen(callback_query: types.CallbackQuery):
    code = callback_query.data[len('onb:cur:'):]
    if code not in money.CURRENCIES:
        await callback_query.answer()
        return
    user_id = callback_query.from_user.id
    await set_currency(user_id, code)
    money.set_currency(code)
    lang, _currency = await get_settings(user_id)
    lang = lang or callback_query.from_user.language_code
    await seed_default_categories(user_id, lang if lang in LANGUAGES else 'ru')
    with i18n.use_locale(lang if lang in LANGUAGES else 'ru'):
        await callback_query.message.edit_text(text=i18n.gettext('✅ Готово! Валюта: {code}. Категории уже созданы.', locale=lang).format(code=code))
        await callback_query.message.answer(text=i18n.gettext(QUICK_INPUT_HELP, locale=lang),
                                            reply_markup=await buttons.get_button_manage_money(lang=lang))
    await callback_query.answer()


@router.message(needs_onboarding)
async def start_onboarding(message: types.Message):
    await message.answer(text=LANGUAGE_PROMPT, reply_markup=language_markup())


@router.callback_query(needs_onboarding)
async def start_onboarding_from_button(callback_query: types.CallbackQuery):
    await callback_query.message.answer(text=LANGUAGE_PROMPT, reply_markup=language_markup())
    await callback_query.answer()
