"""/stats: monthly analytics with month navigation and a 12-month dynamics chart."""
import asyncio
import os
import re
import shutil
import tempfile
from decimal import Decimal
from html import escape
from typing import Optional, Tuple

from aiogram import F, Router, types
from aiogram.filters import Command, StateFilter
from aiogram.types import FSInputFile

from config import FEATURE_BUDGETS
from money import fmt
from i18n import _, __, i18n
from report.headers import EXPENDITURE, INCOME
from timeutils import days_in_month, format_local, local_now, month_range, shift_month
from . import metrics
from .charts import draw_monthly, months_between
from .service import expenses_by_category, largest_expenses, monthly_totals, totals

router = Router(name='analytics')

_MONTH_RE = re.compile(r'^(\d{4})-(\d{2})$')
DYNAMICS_MONTHS = 12


def _month_label(year: int, month: int) -> str:
    return f'{month:02d}.{year}'


def _parse_month(raw: str) -> Optional[Tuple[int, int]]:
    match = _MONTH_RE.match(raw)
    if not match:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12 or year < 2000:
        return None
    now = local_now()
    if (year, month) > (now.year, now.month):  # no future months
        return None
    return year, month


def _signed(value: Decimal) -> str:
    return f'+{value}' if value > 0 else str(value)


async def _overall_budget_left(user_id: int) -> Optional[Decimal]:
    if not FEATURE_BUDGETS:
        return None
    from budgets.service import budget_statuses
    overall = [s for s in await budget_statuses(user_id) if s.category_id is None]
    return overall[0].left if overall else None


async def render(user_id: int, year: int, month: int) -> Tuple[str, types.InlineKeyboardMarkup]:
    now = local_now()
    current = (year, month) == (now.year, now.month)
    since, until = month_range(year, month)
    prev_year, prev_month = shift_month(year, month, -1)
    prev_since, prev_until = month_range(prev_year, prev_month)

    income, expense = await totals(user_id, since, until)
    total_days = days_in_month(year, month)
    elapsed = now.day if current else total_days

    lines = [_('📈 Аналитика · {month}').format(month=_month_label(year, month)), '',
             _('💰 Доходы: {amount}').format(amount=fmt(income)),
             _('💸 Расходы: {amount}').format(amount=fmt(expense))]
    rate = metrics.savings_rate(income, expense)
    lines.append(_('🏦 Норма сбережений: {rate}%').format(rate=rate) if rate is not None
                 else _('🏦 Норма сбережений: нет доходов'))
    lines.append(_('📆 Средний расход в день: {amount}').format(amount=fmt(metrics.average_per_day(expense, elapsed))))

    if current:
        lines.append(_('🔮 Прогноз расходов на конец месяца: {amount}').format(
            amount=fmt(metrics.forecast(expense, elapsed, total_days))))
        days_left = total_days - now.day + 1
        budget_left = await _overall_budget_left(user_id)
        if budget_left is not None:
            basis, available = _('по общему бюджету'), budget_left
        else:
            basis, available = _('из остатка доходов'), income - expense
        allowance = metrics.daily_allowance(available, days_left)
        if allowance > 0:
            lines.append(_('✅ Можно тратить в день: {amount} ({basis})').format(amount=fmt(allowance), basis=basis))
        else:
            lines.append(_('⚠️ Тратить больше нечего ({basis})').format(basis=basis))

    current_by_category = dict(await expenses_by_category(user_id, since, until))
    previous_by_category = dict(await expenses_by_category(user_id, prev_since, prev_until))
    comparison = metrics.compare(current_by_category, previous_by_category)
    if comparison:
        lines += ['', _('📊 Сравнение с {month}:').format(month=_month_label(prev_year, prev_month))]
        for row in comparison:
            change = _('новая') if row.change is None else f'{_signed(row.change)}%'
            details = _('(было {previous}, {diff}, {change})').format(
                previous=fmt(row.previous), diff=fmt(row.diff, signed=True), change=change)
            lines.append(f'{escape(row.name.capitalize())}: <b>{fmt(row.current)}</b> {details}')
        previous_total = sum(previous_by_category.values(), metrics.ZERO)
        total_change = metrics.percent(expense - previous_total, previous_total)
        lines.append(_('Итого: {current} (было {previous}, {change})').format(
            current=fmt(expense), previous=fmt(previous_total),
            change=_('новая') if total_change is None else f'{_signed(total_change)}%'))

    top = metrics.shares(list(current_by_category.items()))
    if top:
        lines += ['', _('🏆 Топ категорий:')]
        for number, (name, amount, share) in enumerate(top, start=1):
            lines.append(f'{number}. {escape(name.capitalize())} — <b>{fmt(amount)}</b> ({share}%)')

    largest = await largest_expenses(user_id, since, until)
    if largest:
        lines += ['', _('💥 Самые крупные траты:')]
        for number, row in enumerate(largest, start=1):
            what = escape(row.comment) if row.comment else escape(row.category.capitalize())
            lines.append(f'{number}. {format_local(row.created_at, with_year=False)} {what} — <b>{fmt(row.quantity)}</b>')

    if not income and not expense and not comparison:
        lines += ['', _('Операций нет.')]

    nav = [types.InlineKeyboardButton(text=f'◀️ {_month_label(prev_year, prev_month)}',
                                      callback_data=f'a:m:{prev_year:04d}-{prev_month:02d}')]
    if not current:
        next_year, next_month = shift_month(year, month, 1)
        nav.append(types.InlineKeyboardButton(text=f'{_month_label(next_year, next_month)} ▶️',
                                              callback_data=f'a:m:{next_year:04d}-{next_month:02d}'))
    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        nav,
        [types.InlineKeyboardButton(text=_('📊 Динамика за 12 месяцев'), callback_data=f'a:dyn:{year:04d}-{month:02d}')],
    ])
    return '\n'.join(lines), markup


@router.message(Command('stats'), StateFilter(None))
@router.message(F.text == __('📈 Аналитика'), StateFilter(None))
async def stats_command(message: types.Message):
    now = local_now()
    text, markup = await render(message.from_user.id, now.year, now.month)
    await message.answer(text=text, reply_markup=markup)


@router.callback_query(F.data.startswith('a:m:'))
async def month_handler(callback_query: types.CallbackQuery):
    month = _parse_month(callback_query.data[len('a:m:'):])
    if month is None:
        await callback_query.answer()
        return
    text, markup = await render(callback_query.from_user.id, *month)
    await callback_query.message.edit_text(text=text, reply_markup=markup)
    await callback_query.answer()


@router.callback_query(F.data.startswith('a:dyn:'))
async def dynamics_handler(callback_query: types.CallbackQuery):
    last = _parse_month(callback_query.data[len('a:dyn:'):])
    if last is None:
        await callback_query.answer()
        return
    first = shift_month(*last, -(DYNAMICS_MONTHS - 1))
    since, _unused = month_range(*first)
    _unused, until = month_range(*last)
    data = await monthly_totals(callback_query.from_user.id, since, until)
    lang = i18n.current_locale if i18n.current_locale in INCOME else 'ru'

    directory = tempfile.mkdtemp(prefix='dynamics-')
    path = os.path.join(directory, 'dynamics.png')
    try:
        await asyncio.to_thread(draw_monthly, path, months_between(first, last), data, INCOME[lang], EXPENDITURE[lang])
        caption = _('📊 Доходы и расходы: {first} — {last}').format(first=_month_label(*first), last=_month_label(*last))
        await callback_query.message.answer_photo(photo=FSInputFile(path), caption=caption)
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    await callback_query.answer()
