"""Import of pasted / forwarded bank payment notifications: one card per payment, the user picks categories."""
from datetime import datetime
from decimal import Decimal
from html import escape
from typing import Any, Dict, List, Optional

from aiogram import F, Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from accounting.models import OperationType
from budgets.notify import schedule_budget_check
from config import currencies_active
from currency.rates import RateUnavailable, convert, get_rate
from i18n import _
from middleware.accounting import _add_operation, _get_category, _show_all_categories, kind_of
from money import current_currency, fmt
from telegram import buttons
from telegram.validation import checked_amount, parse_category_id
from timeutils import local_now, to_local, to_utc_naive
from .parser import looks_like_statement, parse_statement
from .queries import already_imported, category_for_merchant

router = Router(name='statements')

KEY = 'statement_import'


async def _prepare(user_id: int, text: str) -> Dict[str, Any]:
    """Payments ready to save (converted, deduplicated) plus counters of what was left out."""
    items: List[Dict[str, Any]] = []
    failed = duplicates = unconverted = 0
    base = current_currency()
    for payment in parse_statement(text):
        if payment.failed:
            failed += 1
            continue
        when_local = payment.when or local_now().replace(tzinfo=None, second=0, microsecond=0)
        created_at = to_utc_naive(when_local)
        amount, original = payment.amount, None
        if payment.currency and base and payment.currency != base and currencies_active():
            try:
                rate = await get_rate(payment.currency, base, when_local.date(), local_now().date())
            except RateUnavailable:
                unconverted += 1
                continue
            original = {'amount': str(payment.amount), 'currency': payment.currency, 'rate': str(rate)}
            amount = convert(payment.amount, rate)
        amount = checked_amount(amount)
        if amount is None:
            continue
        if await already_imported(user_id, amount, created_at, payment.merchant):
            duplicates += 1
            continue
        items.append({'type': 'income' if payment.income else 'expenditure', 'amount': str(amount),
                      'merchant': payment.merchant, 'created_at': created_at.isoformat(), 'original': original})
    return {'items': items, 'index': 0, 'saved': 0, 'skipped': 0,
            'failed': failed, 'duplicates': duplicates, 'unconverted': unconverted}


def _button(text: str, data: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text, callback_data=data)


async def _card(user_id: int, batch: Dict[str, Any]):
    item = batch['items'][batch['index']]
    operation_type = OperationType[item['type']]
    kind = kind_of(operation_type)
    sign = '➕' if operation_type == OperationType.income else '➖'
    when = to_local(datetime.fromisoformat(item['created_at'])).strftime('%d.%m.%Y %H:%M')
    text = _('🧾 Платёж {number} из {total}').format(number=batch['index'] + 1, total=len(batch['items']))
    text += f"\n{sign} <b>{fmt(item['amount'])}</b>"
    if item.get('original'):
        text += f" ({fmt(item['original']['amount'], item['original']['currency'])})"
    if item.get('merchant'):
        text += f"\n🔹 {escape(item['merchant'])}"
    text += f'\n🕘 {when}\n\n' + _('Введите категорию')

    categories = await _show_all_categories(user_id, kind)
    rows = []
    remembered = await category_for_merchant(user_id, item.get('merchant'), kind)
    if remembered is not None:
        name = next((c.name for c in categories if c.id == remembered), None)
        if name:
            rows.append([_button(_('✅ {name} (как в прошлый раз)').format(name=name.capitalize()), f'st:cat:{remembered}')])
    rows += buttons.category_grid(categories, 'st:cat:')
    rows.append([_button(_('⏭ Пропустить'), 'st:skip'), _button(_('📦 Все остальные сюда'), 'st:bulk')])
    rows.append([_button(_('⏹ Закончить'), 'st:stop')])
    return text, types.InlineKeyboardMarkup(inline_keyboard=rows)


def _summary(batch: Dict[str, Any]) -> str:
    text = _('✅ Готово. Записано: {saved}, пропущено: {skipped}.').format(saved=batch['saved'], skipped=batch['skipped'])
    if batch['duplicates']:
        text += '\n' + _('Уже были записаны раньше: {count}.').format(count=batch['duplicates'])
    if batch['failed']:
        text += '\n' + _('Неуспешные платежи не учитывались: {count}.').format(count=batch['failed'])
    if batch['unconverted']:
        text += '\n' + _('Не удалось получить курс для: {count}.').format(count=batch['unconverted'])
    return text


@router.message(StateFilter(None), F.text.func(looks_like_statement))
async def statement_received(message: types.Message, state: FSMContext):
    batch = await _prepare(message.from_user.id, message.text)
    if not batch['items']:
        await state.update_data(**{KEY: None})
        await message.answer(text=_summary(batch), reply_markup=await buttons.get_button_manage_money())
        return
    await state.update_data(**{KEY: batch})
    intro = _('Нашёл платежей: {count}. Выберите категорию для каждого.').format(count=len(batch['items']))
    text, markup = await _card(message.from_user.id, batch)
    await message.answer(text=intro)
    await message.answer(text=text, reply_markup=markup)


async def _batch(callback_query: types.CallbackQuery, state: FSMContext) -> Optional[Dict[str, Any]]:
    batch = (await state.get_data()).get(KEY)
    if not batch or batch['index'] >= len(batch['items']):
        await callback_query.answer(text=_('Эта кнопка устарела. Отправьте операцию ещё раз.'), show_alert=True)
        return None
    return batch


async def _save(user_id: int, item: Dict[str, Any], category_id: int) -> None:
    original = item.get('original') or {}
    await _add_operation(
        user_id=user_id, category_id=category_id, quantity=Decimal(item['amount']),
        operation_type=OperationType[item['type']], comment=item.get('merchant'),
        created_at=datetime.fromisoformat(item['created_at']),
        original_amount=Decimal(original['amount']) if original else None,
        original_currency=original.get('currency'), rate=Decimal(original['rate']) if original else None)


async def _next(callback_query: types.CallbackQuery, state: FSMContext, batch: Dict[str, Any]) -> None:
    user_id = callback_query.from_user.id
    batch['index'] += 1
    if batch['index'] >= len(batch['items']):
        await state.update_data(**{KEY: None})
        await callback_query.message.edit_text(text=_summary(batch))
        if any(i['type'] == 'expenditure' for i in batch['items']):
            await schedule_budget_check(user_id)
    else:
        await state.update_data(**{KEY: batch})
        text, markup = await _card(user_id, batch)
        await callback_query.message.edit_text(text=text, reply_markup=markup)
    await callback_query.answer()


async def _own_category(callback_query: types.CallbackQuery, prefix: str):
    category_id = parse_category_id(callback_query.data, prefix=prefix)
    category = await _get_category(category_id=category_id, user_id=callback_query.from_user.id) if category_id else None
    if category is None:
        await callback_query.answer(text=_('Мне кажется или такой категории нет?'), show_alert=True)
    return category


@router.callback_query(F.data.startswith('st:cat:'))
async def category_chosen(callback_query: types.CallbackQuery, state: FSMContext):
    batch = await _batch(callback_query, state)
    if batch is None:
        return
    category = await _own_category(callback_query, 'st:cat:')
    if category is None:
        return
    await _save(callback_query.from_user.id, batch['items'][batch['index']], category.id)
    batch['saved'] += 1
    await _next(callback_query, state, batch)


@router.callback_query(F.data == 'st:skip')
async def skip(callback_query: types.CallbackQuery, state: FSMContext):
    batch = await _batch(callback_query, state)
    if batch is None:
        return
    batch['skipped'] += 1
    await _next(callback_query, state, batch)


@router.callback_query(F.data == 'st:bulk')
async def bulk_menu(callback_query: types.CallbackQuery, state: FSMContext):
    batch = await _batch(callback_query, state)
    if batch is None:
        return
    kind = kind_of(OperationType[batch['items'][batch['index']]['type']])
    categories = await _show_all_categories(callback_query.from_user.id, kind)
    rows = buttons.category_grid(categories, 'st:all:') + [[_button(_('⬅️ Назад'), 'st:back')]]
    await callback_query.message.edit_reply_markup(reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows))
    await callback_query.answer()


@router.callback_query(F.data == 'st:back')
async def back(callback_query: types.CallbackQuery, state: FSMContext):
    batch = await _batch(callback_query, state)
    if batch is None:
        return
    text, markup = await _card(callback_query.from_user.id, batch)
    await callback_query.message.edit_text(text=text, reply_markup=markup)
    await callback_query.answer()


@router.callback_query(F.data.startswith('st:all:'))
async def bulk_apply(callback_query: types.CallbackQuery, state: FSMContext):
    """Saves the current and every remaining payment of the same kind (income/expense) into one category."""
    batch = await _batch(callback_query, state)
    if batch is None:
        return
    category = await _own_category(callback_query, 'st:all:')
    if category is None:
        return
    user_id = callback_query.from_user.id
    kind = batch['items'][batch['index']]['type']
    remaining = batch['items'][batch['index']:]
    others = []
    for item in remaining:
        if item['type'] == kind:
            await _save(user_id, item, category.id)
            batch['saved'] += 1
        else:
            others.append(item)
    batch['items'] = batch['items'][:batch['index']] + others
    batch['index'] -= 1  # _next moves to the first payment of the other kind, if any
    await _next(callback_query, state, batch)


@router.callback_query(F.data == 'st:stop')
async def stop(callback_query: types.CallbackQuery, state: FSMContext):
    batch = await _batch(callback_query, state)
    if batch is None:
        return
    batch['skipped'] += len(batch['items']) - batch['index']
    batch['index'] = len(batch['items']) - 1
    await _next(callback_query, state, batch)
