from decimal import Decimal

from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

import money
from telegram import buttons
from i18n import _, __
from config import FEATURE_QUICK_INPUT
from currency.rates import RateUnavailable, get_rate
from middleware.user import set_currency
from timeutils import local_now
from .messages import HELP_MESSAGE, QUICK_INPUT_HELP

router = Router(name='settings')

RATE_CURRENCIES = ('USD', 'EUR', 'RUB')


@router.message(F.text == __('⚙️ Настройки'), StateFilter(None))
async def settings(message: types.Message):
    await message.answer(text=_('⚙️ Настройки'), reply_markup=await buttons.settings_button())


@router.callback_query(F.data == 'help', StateFilter(None))
async def help_handler(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text(text=_(HELP_MESSAGE))
    if FEATURE_QUICK_INPUT:
        await callback_query.message.answer(text=_(QUICK_INPUT_HELP))
    await callback_query.answer()
    await state.clear()


@router.callback_query(F.data == 'currency')
async def currency_menu(callback_query: types.CallbackQuery):
    from onboarding.handlers import currency_markup
    current = money.current_currency()
    text = _('💱 Основная валюта: {code}').format(code=current or '—') + '\n' + _(
        'Новые суммы будут в выбранной валюте. Старые записи не пересчитываются.')
    from i18n import i18n
    await callback_query.message.edit_text(text=text, reply_markup=currency_markup(i18n.current_locale, prefix='cur:'))
    await callback_query.answer()


@router.callback_query(F.data.startswith('cur:'))
async def currency_chosen(callback_query: types.CallbackQuery):
    code = callback_query.data[len('cur:'):]
    if code not in money.CURRENCIES:
        await callback_query.answer()
        return
    await set_currency(callback_query.from_user.id, code)
    money.set_currency(code)
    await callback_query.message.edit_text(text='✅ ' + _('💱 Основная валюта: {code}').format(code=code))
    await callback_query.answer()


async def approx_in(amount: Decimal, codes=('USD', 'EUR')) -> str:
    """"≈ $123 · €110" for an amount in the user's currency; empty when rates are unavailable."""
    base = money.current_currency()
    if not base:
        return ''
    today = local_now().date()
    parts = []
    for code in codes:
        if code == base:
            continue
        try:
            rate = await get_rate(base, code, today, today)
        except RateUnavailable:
            return ''
        parts.append(money.fmt(amount * rate, code))
    return ('≈ ' + ' · '.join(parts)) if parts else ''


@router.callback_query(F.data == 'rates')
async def rates_handler(callback_query: types.CallbackQuery):
    base = money.current_currency()
    if not base:
        await callback_query.answer(text=_('Сначала выберите валюту в настройках.'), show_alert=True)
        return
    today = local_now().date()
    lines = [_('💹 Курсы на сегодня:')]
    for code in RATE_CURRENCIES:
        if code == base:
            continue
        try:
            rate = await get_rate(code, base, today, today)
        except RateUnavailable:
            await callback_query.answer(text=_('Не удалось получить курсы. Попробуйте позже.'), show_alert=True)
            return
        lines.append(f'{money.FLAGS.get(code, "")} 1 {code} = {money.fmt(rate, base)}')
    await callback_query.message.edit_text(text='\n'.join(lines))
    await callback_query.answer()
