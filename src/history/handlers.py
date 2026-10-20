"""/history: paged list with period/category filters, an editable card per operation, undo.

Callback data ("h:..." prefix) carries operation ids as 32 hex chars; every
handler re-reads the operation with the user's id, so a forged or stale id
only gets "not found". Filters live in FSM data, so they survive paging.
"""
import math
import re
import uuid
from html import escape
from typing import Any, Dict, Optional, Tuple

from aiogram import F, Router, types
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from accounting.models import OperationType
from budgets.notify import schedule_budget_check
from i18n import _, __
from middleware.accounting import _get_category, _show_all_categories
from telegram import buttons
from telegram.validation import normalize_comment, parse_amount, parse_category_id
from timeutils import format_local
from .periods import PERIODS, parse_user_date, period_range
from .queries import delete_operation, get_operation, last_operation, list_operations, update_operation, used_categories

router = Router(name='history')

PAGE_SIZE = 5
FILTER_KEY = 'history_filter'
EDIT_KEY = 'history_edit'
_HEX_RE = re.compile(r'^[0-9a-f]{32}$')


class HistoryEditState(StatesGroup):
    amount = State()
    comment = State()
    date = State()


def _default_filter() -> Dict[str, Any]:
    return {'period': 'all', 'category_id': None, 'page': 0}


def _parse_op_id(data: Optional[str], prefix: str) -> Optional[uuid.UUID]:
    if not data or not data.startswith(prefix):
        return None
    raw = data[len(prefix):]
    return uuid.UUID(hex=raw) if _HEX_RE.match(raw) else None


def _period_label(period: str) -> str:
    return {
        'all': _('всё время'),
        'today': _('сегодня'),
        'week': _('7 дней'),
        'month': _('этот месяц'),
        'year': _('этот год'),
    }[period]


def _signed(row) -> str:
    return ('+' if row.operation_type == OperationType.income else '−') + str(row.quantity)


def _card_text(row) -> str:
    label = _('ДОХОД') if row.operation_type == OperationType.income else _('РАСХОД')
    text = f'📌 {format_local(row.created_at)}\n{label} <b>{row.quantity} {escape(row.category)}</b>'
    if row.comment:
        text += f'\n💬 {escape(row.comment)}'
    return text


def _button(text: str, data: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text, callback_data=data)


def _card_markup(row, page: int) -> types.InlineKeyboardMarkup:
    op = row.id.hex
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [_button(_('💰 Сумма'), f'h:amt:{op}'), _button(_('🗂 Категория'), f'h:ecat:{op}')],
        [_button(_('💬 Комментарий'), f'h:cmt:{op}'), _button(_('📅 Дата'), f'h:date:{op}')],
        [_button(_('🗑 Удалить'), f'h:del:{op}')],
        [_button(_('⬅️ К списку'), f'h:list:{page}')],
    ])


async def _get_filter(state: FSMContext) -> Dict[str, Any]:
    return (await state.get_data()).get(FILTER_KEY) or _default_filter()


async def _render_list(user_id: int, flt: Dict[str, Any]) -> Tuple[str, types.InlineKeyboardMarkup, Dict[str, Any]]:
    since, until = period_range(flt['period'])
    rows, total = await list_operations(user_id, flt['page'] * PAGE_SIZE, PAGE_SIZE, flt['category_id'], since, until)
    pages = max(1, math.ceil(total / PAGE_SIZE))
    if flt['page'] >= pages:  # e.g. the last item of the last page was deleted
        flt = dict(flt, page=pages - 1)
        rows, total = await list_operations(user_id, flt['page'] * PAGE_SIZE, PAGE_SIZE, flt['category_id'], since, until)

    category_label = _('все')
    if flt['category_id'] is not None:
        names = {c.id: c.name for c in await used_categories(user_id)}
        category_label = names.get(flt['category_id'], '?')

    period_text = _('Период: {period}').format(period=_period_label(flt['period']))
    category_text = _('Категория: {category}').format(category=escape(category_label))
    text = f"{_('📜 История')}\n{period_text} · {category_text}"
    keyboard = []
    if not rows:
        text += '\n\n' + _('Операций нет.')
    else:
        text += '\n' + _('Страница {page} из {pages}, всего {total}').format(page=flt['page'] + 1, pages=pages, total=total) + '\n'
        for number, row in enumerate(rows, start=1):
            line = f'\n{number}. {format_local(row.created_at, with_year=False)}  <b>{_signed(row)}</b> {escape(row.category)}'
            if row.comment:
                line += f' 💬 {escape(row.comment)}'
            text += line
        keyboard.append([_button(str(n), f'h:op:{row.id.hex}') for n, row in enumerate(rows, start=1)])

    nav = []
    if flt['page'] > 0:
        nav.append(_button('◀️', f'h:list:{flt["page"] - 1}'))
    if flt['page'] < pages - 1:
        nav.append(_button('▶️', f'h:list:{flt["page"] + 1}'))
    if nav:
        keyboard.append(nav)
    keyboard.append([_button(_('📅 Период'), 'h:per'), _button(_('🗂 Категория'), 'h:cat')])
    if flt['period'] != 'all' or flt['category_id'] is not None:
        keyboard.append([_button(_('✖️ Сбросить фильтры'), 'h:reset')])

    return text, types.InlineKeyboardMarkup(inline_keyboard=keyboard), flt


async def _show_list(callback_query: types.CallbackQuery, state: FSMContext, flt: Dict[str, Any]) -> None:
    text, markup, flt = await _render_list(callback_query.from_user.id, flt)
    await state.update_data(**{FILTER_KEY: flt})
    await callback_query.message.edit_text(text=text, reply_markup=markup)
    await callback_query.answer()


async def _load(callback_query: types.CallbackQuery, prefix: str):
    """The operation from callback data, only if it belongs to this user."""
    op_id = _parse_op_id(callback_query.data, prefix)
    row = await get_operation(callback_query.from_user.id, op_id) if op_id else None
    if row is None:
        await callback_query.answer(text=_('Операция не найдена: возможно, она уже удалена.'), show_alert=True)
    return row


# --- List ------------------------------------------------------------------

@router.message(Command('history'), StateFilter(None))
@router.message(F.text == __('📜 История'), StateFilter(None))
async def history_command(message: types.Message, state: FSMContext):
    text, markup, flt = await _render_list(message.from_user.id, _default_filter())
    await state.update_data(**{FILTER_KEY: flt})
    await message.answer(text=text, reply_markup=markup)


@router.callback_query(F.data.startswith('h:'))
async def leave_edit_mode(callback_query: types.CallbackQuery, state: FSMContext):
    """Runs first for every history button: any half-typed edit is abandoned."""
    await state.set_state(None)
    raise SkipHandler()  # let the matching handler below process the button


@router.callback_query(F.data.startswith('h:list:'))
async def page_handler(callback_query: types.CallbackQuery, state: FSMContext):
    raw = callback_query.data[len('h:list:'):]
    flt = await _get_filter(state)
    flt['page'] = int(raw) if raw.isdigit() else 0
    await _show_list(callback_query, state, flt)


@router.callback_query(F.data == 'h:per')
async def period_menu(callback_query: types.CallbackQuery, state: FSMContext):
    flt = await _get_filter(state)
    rows = [[_button(('• ' if p == flt['period'] else '') + _period_label(p), f'h:per:{p}')] for p in PERIODS]
    rows.append([_button(_('⬅️ К списку'), f'h:list:{flt["page"]}')])
    await callback_query.message.edit_reply_markup(reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows))
    await callback_query.answer()


@router.callback_query(F.data.startswith('h:per:'))
async def period_selected(callback_query: types.CallbackQuery, state: FSMContext):
    period = callback_query.data[len('h:per:'):]
    if period not in PERIODS:
        await callback_query.answer()
        return
    flt = dict(await _get_filter(state), period=period, page=0)
    await _show_list(callback_query, state, flt)


@router.callback_query(F.data == 'h:cat')
async def category_menu(callback_query: types.CallbackQuery, state: FSMContext):
    flt = await _get_filter(state)
    categories = await used_categories(callback_query.from_user.id)
    rows = [[_button(_('Все категории'), 'h:cat:all')]]
    rows += [[_button(('• ' if c.id == flt['category_id'] else '') + c.name.capitalize(), f'h:cat:{c.id}')] for c in categories]
    rows.append([_button(_('⬅️ К списку'), f'h:list:{flt["page"]}')])
    await callback_query.message.edit_reply_markup(reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows))
    await callback_query.answer()


@router.callback_query(F.data.startswith('h:cat:'))
async def category_selected(callback_query: types.CallbackQuery, state: FSMContext):
    flt = dict(await _get_filter(state), page=0)
    if callback_query.data == 'h:cat:all':
        flt['category_id'] = None
    else:
        category_id = parse_category_id(callback_query.data, prefix='h:cat:')
        own = {c.id for c in await used_categories(callback_query.from_user.id)}
        if category_id not in own:
            await callback_query.answer()
            return
        flt['category_id'] = category_id
    await _show_list(callback_query, state, flt)


@router.callback_query(F.data == 'h:reset')
async def reset_filters(callback_query: types.CallbackQuery, state: FSMContext):
    await _show_list(callback_query, state, _default_filter())


# --- Card ------------------------------------------------------------------

@router.callback_query(F.data.startswith('h:op:'))
async def card_handler(callback_query: types.CallbackQuery, state: FSMContext):
    row = await _load(callback_query, 'h:op:')
    if row is None:
        return
    flt = await _get_filter(state)
    await callback_query.message.edit_text(text=_card_text(row), reply_markup=_card_markup(row, flt['page']))
    await callback_query.answer()


async def _start_text_edit(callback_query: types.CallbackQuery, state: FSMContext, prefix: str, new_state, prompt: str):
    row = await _load(callback_query, prefix)
    if row is None:
        return
    await state.set_state(new_state)
    await state.update_data(**{EDIT_KEY: row.id.hex})
    await callback_query.message.answer(text=prompt, reply_markup=await buttons.get_button_cancel())
    await callback_query.answer()


@router.callback_query(F.data.startswith('h:amt:'))
async def edit_amount_start(callback_query: types.CallbackQuery, state: FSMContext):
    await _start_text_edit(callback_query, state, 'h:amt:', HistoryEditState.amount,
                           _('Введите новую сумму (можно с расчётом, например 1200+350)'))


@router.callback_query(F.data.startswith('h:cmt:'))
async def edit_comment_start(callback_query: types.CallbackQuery, state: FSMContext):
    await _start_text_edit(callback_query, state, 'h:cmt:', HistoryEditState.comment,
                           _('Введите комментарий или «-», чтобы его удалить'))


@router.callback_query(F.data.startswith('h:date:'))
async def edit_date_start(callback_query: types.CallbackQuery, state: FSMContext):
    await _start_text_edit(callback_query, state, 'h:date:', HistoryEditState.date,
                           _('Введите дату: 21.10, 21.10.2026 14:30, «сегодня» или «вчера»'))


async def _edited_op(message: types.Message, state: FSMContext):
    raw = (await state.get_data()).get(EDIT_KEY) or ''
    op_id = uuid.UUID(hex=raw) if _HEX_RE.match(raw) else None
    row = await get_operation(message.from_user.id, op_id) if op_id else None
    if row is None:
        await state.set_state(None)
        await message.answer(text=_('Операция не найдена: возможно, она уже удалена.'))
    return row


async def _apply(message: types.Message, state: FSMContext, row, **values) -> None:
    await update_operation(message.from_user.id, row.id, **values)
    await schedule_budget_check(message.from_user.id)  # a bigger amount or another month may cross a limit
    await state.set_state(None)
    updated = await get_operation(message.from_user.id, row.id)
    flt = await _get_filter(state)
    await message.answer(text='✅ ' + _('Сохранено') + '\n\n' + _card_text(updated), reply_markup=_card_markup(updated, flt['page']))


@router.message(HistoryEditState.amount)
async def edit_amount_input(message: types.Message, state: FSMContext):
    row = await _edited_op(message, state)
    if row is None:
        return
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer(text=_('Не получилось посчитать сумму. Пример: 500 кафе обед или 1200+350*2 продукты'),
                             reply_markup=await buttons.get_button_cancel())
        return
    await _apply(message, state, row, quantity=amount)


@router.message(HistoryEditState.comment)
async def edit_comment_input(message: types.Message, state: FSMContext):
    row = await _edited_op(message, state)
    if row is None:
        return
    text = (message.text or '').strip()
    await _apply(message, state, row, comment=None if text == '-' else normalize_comment(text.split()))


@router.message(HistoryEditState.date)
async def edit_date_input(message: types.Message, state: FSMContext):
    row = await _edited_op(message, state)
    if row is None:
        return
    created_at = parse_user_date(message.text, row.created_at)
    if created_at is None:
        await message.answer(text=_('Не понял дату. Примеры: 21.10, 21.10.2026 14:30, вчера'),
                             reply_markup=await buttons.get_button_cancel())
        return
    await _apply(message, state, row, created_at=created_at)


@router.callback_query(F.data.startswith('h:ecat:'))
async def edit_category_start(callback_query: types.CallbackQuery, state: FSMContext):
    row = await _load(callback_query, 'h:ecat:')
    if row is None:
        return
    await state.update_data(**{EDIT_KEY: row.id.hex})
    categories = await _show_all_categories(callback_query.from_user.id)
    rows = [[_button(('• ' if c.id == row.category_id else '') + c.name.capitalize(), f'h:setcat:{c.id}')] for c in categories]
    rows.append([_button(_('⬅️ Назад'), f'h:op:{row.id.hex}')])
    await callback_query.message.edit_reply_markup(reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows))
    await callback_query.answer()


@router.callback_query(F.data.startswith('h:setcat:'))
async def edit_category_apply(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    raw = (await state.get_data()).get(EDIT_KEY) or ''
    op_id = uuid.UUID(hex=raw) if _HEX_RE.match(raw) else None
    row = await get_operation(user_id, op_id) if op_id else None
    category_id = parse_category_id(callback_query.data, prefix='h:setcat:')
    category = await _get_category(category_id=category_id, user_id=user_id) if category_id else None
    if row is None or category is None:
        await callback_query.answer(text=_('Операция не найдена: возможно, она уже удалена.'), show_alert=True)
        return
    await update_operation(user_id, row.id, category_id=category.id)
    await schedule_budget_check(user_id)
    updated = await get_operation(user_id, row.id)
    flt = await _get_filter(state)
    await callback_query.message.edit_text(text='✅ ' + _('Сохранено') + '\n\n' + _card_text(updated),
                                           reply_markup=_card_markup(updated, flt['page']))
    await callback_query.answer()


# --- Delete and undo ---------------------------------------------------------

@router.callback_query(F.data.startswith('h:del:'))
async def delete_ask(callback_query: types.CallbackQuery, state: FSMContext):
    row = await _load(callback_query, 'h:del:')
    if row is None:
        return
    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [_button(_('🗑 Да, удалить'), f'h:delok:{row.id.hex}')],
        [_button(_('⬅️ Нет'), f'h:op:{row.id.hex}')],
    ])
    await callback_query.message.edit_text(text=_card_text(row) + '\n\n' + _('Удалить эту операцию?'), reply_markup=markup)
    await callback_query.answer()


@router.callback_query(F.data.startswith('h:delok:'))
async def delete_confirmed(callback_query: types.CallbackQuery, state: FSMContext):
    row = await _load(callback_query, 'h:delok:')
    if row is None:
        return
    await delete_operation(callback_query.from_user.id, row.id)
    flt = await _get_filter(state)
    markup = types.InlineKeyboardMarkup(inline_keyboard=[[_button(_('⬅️ К списку'), f'h:list:{flt["page"]}')]])
    await callback_query.message.edit_text(text=_card_text(row) + '\n\n🗑 ' + _('Операция удалена.'), reply_markup=markup)
    await callback_query.answer()


@router.callback_query(F.data.startswith('h:undo:'))
async def undo_button(callback_query: types.CallbackQuery):
    """The ↩️ button under a just-saved operation removes exactly that operation."""
    op_id = _parse_op_id(callback_query.data, 'h:undo:')
    if op_id is None or not await delete_operation(callback_query.from_user.id, op_id):
        await callback_query.answer(text=_('Операция не найдена: возможно, она уже удалена.'), show_alert=True)
        return
    await callback_query.message.edit_text(text=(callback_query.message.html_text or '') + '\n\n↩️ ' + _('Отменено'),
                                           reply_markup=None)
    await callback_query.answer()


@router.message(Command('undo'), StateFilter(None))
async def undo_command(message: types.Message):
    row = await last_operation(message.from_user.id)
    if row is None:
        await message.answer(text=_('Операций нет.'))
        return
    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [_button(_('🗑 Да, удалить'), f'h:delok:{row.id.hex}')],
        [_button(_('⬅️ Нет'), 'cancel')],
    ])
    await message.answer(text=_('Последняя операция:') + '\n\n' + _card_text(row) + '\n\n' + _('Удалить эту операцию?'),
                         reply_markup=markup)
