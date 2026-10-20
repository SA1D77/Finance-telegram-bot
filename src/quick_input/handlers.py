"""Quick entries typed as one message: "500 кафе обед", "+50000 зарплата", "1200+350*2 продукты".

The pending entry lives in FSM data (not in an FSM state), so the menu
buttons keep working while a confirmation is open; any dialog that calls
state.clear() simply drops it.
"""
from decimal import Decimal
from html import escape
from typing import Any, Dict, Optional

from aiogram import F, Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from accounting.models import OperationType
from i18n import _
from middleware.accounting import _add_new_category, _add_operation, _find_active_category, _get_category, _show_all_categories
from telegram import buttons
from telegram.validation import normalize_category_name, normalize_comment, parse_category_id
from .matching import match_category
from .parser import AmountError, looks_like_quick_entry, parse_quick_entry

router = Router(name='quick_input')

PENDING_KEY = 'quick_entry'
CB_SAVE = 'qi:save'
CB_NEW = 'qi:new'
CB_PICK = 'qi:pick'
CB_CATEGORY = 'qi:cat:'


def _summary(pending: Dict[str, Any], category_name: Optional[str] = None) -> str:
    label = _('ДОХОД') if pending['type'] == OperationType.income.name else _('РАСХОД')
    text = f'{label} <b>{pending["amount"]}'
    if category_name:
        text += f' {escape(category_name)}'
    text += '</b>'
    if pending.get('comment'):
        text += f'\n💬 {escape(pending["comment"])}'
    if pending.get('expression'):
        text = f'🧮 {escape(pending["expression"])} = <b>{pending["amount"]}</b>\n' + text
    return text


def _keyboard(rows) -> types.InlineKeyboardMarkup:
    rows = [list(row) for row in rows]
    rows.append([types.InlineKeyboardButton(text=_('⬅️ Отмена'), callback_data='cancel')])
    return types.InlineKeyboardMarkup(inline_keyboard=rows)


def _category_rows(categories):
    return [[types.InlineKeyboardButton(text=c.name.capitalize(), callback_data=f'{CB_CATEGORY}{c.id}')] for c in categories]


async def _save(message: types.Message, user_id: int, pending: Dict[str, Any], category) -> None:
    operation_id = await _add_operation(
        user_id=user_id,
        category_id=category.id,
        quantity=Decimal(pending['amount']),
        operation_type=OperationType[pending['type']],
        comment=pending.get('comment'),
    )
    # The reply keyboard is still on screen in this flow, so the message can carry the inline undo button.
    await message.answer(text='✅ ' + _('Новая запись:') + '\n' + _summary(pending, category.name),
                         reply_markup=buttons.undo_markup(operation_id) or await buttons.get_button_manage_money())


@router.message(StateFilter(None), F.text.func(looks_like_quick_entry))
async def quick_entry_handler(message: types.Message, state: FSMContext):
    try:
        entry = parse_quick_entry(message.text)
    except AmountError:
        await message.answer(text=_('Не получилось посчитать сумму. Пример: 500 кафе обед или 1200+350*2 продукты'))
        return

    user_id = message.from_user.id
    categories = await _show_all_categories(user_id)
    match = match_category(entry.words, categories)
    pending = {
        'type': entry.operation_type.name,
        'amount': str(entry.amount),
        'expression': entry.expression,
        'comment': normalize_comment(match.rest),
    }

    if match.kind == 'exact' and entry.expression is None:
        await state.update_data(**{PENDING_KEY: None})
        await _save(message, user_id, pending, match.category)
        return

    if match.kind == 'exact':
        pending['category_id'] = match.category.id
        text = _summary(pending, match.category.name) + '\n\n' + _('Сохранить?')
        keyboard = _keyboard([[types.InlineKeyboardButton(text=_('✅ Сохранить'), callback_data=CB_SAVE)]])
    elif match.kind == 'fuzzy':
        name = match.category.name
        text = _summary(pending) + '\n\n' + _('Категория «{name}»?').format(name=escape(name))
        keyboard = _keyboard([
            [types.InlineKeyboardButton(text=_('✅ Да, «{name}»').format(name=name), callback_data=f'{CB_CATEGORY}{match.category.id}')],
            [types.InlineKeyboardButton(text=_('🗂 Другая категория'), callback_data=CB_PICK)],
        ])
    elif match.kind == 'ambiguous':
        text = _summary(pending) + '\n\n' + _('Какую категорию вы имели в виду?')
        keyboard = _keyboard(_category_rows(match.categories) + [
            [types.InlineKeyboardButton(text=_('🗂 Другая категория'), callback_data=CB_PICK)]])
    elif match.kind == 'none':
        new_name = normalize_category_name(match.typed)
        pending['new_name'] = new_name
        text = _summary(pending) + '\n\n' + _('Категории «{name}» нет.').format(name=escape(match.typed))
        rows = [[types.InlineKeyboardButton(text=_('🗂 Другая категория'), callback_data=CB_PICK)]]
        if new_name:
            rows.insert(0, [types.InlineKeyboardButton(text=_('➕ Создать «{name}»').format(name=new_name), callback_data=CB_NEW)])
        keyboard = _keyboard(rows)
    else:  # no category typed at all
        text = _summary(pending) + '\n\n' + _('Введите категорию')
        keyboard = _keyboard(_category_rows(categories))

    await state.update_data(**{PENDING_KEY: pending})
    await message.answer(text=text, reply_markup=keyboard)


async def _pending(callback_query: types.CallbackQuery, state: FSMContext) -> Optional[Dict[str, Any]]:
    pending = (await state.get_data()).get(PENDING_KEY)
    if not pending:
        await callback_query.answer(text=_('Эта кнопка устарела. Отправьте операцию ещё раз.'), show_alert=True)
        return None
    return pending


async def _finish(callback_query: types.CallbackQuery, state: FSMContext, pending: Dict[str, Any], category) -> None:
    # Drop the draft first, so a double tap cannot save it twice.
    await state.update_data(**{PENDING_KEY: None})
    await callback_query.message.edit_reply_markup(reply_markup=None)
    await _save(callback_query.message, callback_query.from_user.id, pending, category)
    await callback_query.answer()


@router.callback_query(StateFilter(None), F.data == CB_SAVE)
async def save_handler(callback_query: types.CallbackQuery, state: FSMContext):
    pending = await _pending(callback_query, state)
    if pending is None:
        return
    category = await _get_category(category_id=pending.get('category_id') or 0, user_id=callback_query.from_user.id)
    if category is None:
        await callback_query.answer(text=_('Мне кажется или такой категории нет?'), show_alert=True)
        return
    await _finish(callback_query, state, pending, category)


@router.callback_query(StateFilter(None), F.data.startswith(CB_CATEGORY))
async def category_handler(callback_query: types.CallbackQuery, state: FSMContext):
    pending = await _pending(callback_query, state)
    if pending is None:
        return
    # Callback data comes from the client: the id must be one of the user's active categories.
    category_id = parse_category_id(callback_query.data, prefix=CB_CATEGORY)
    category = await _get_category(category_id=category_id, user_id=callback_query.from_user.id) if category_id else None
    if category is None:
        await callback_query.answer(text=_('Мне кажется или такой категории нет?'), show_alert=True)
        return
    await _finish(callback_query, state, pending, category)


@router.callback_query(StateFilter(None), F.data == CB_NEW)
async def new_category_handler(callback_query: types.CallbackQuery, state: FSMContext):
    pending = await _pending(callback_query, state)
    if pending is None:
        return
    name = normalize_category_name(pending.get('new_name'))
    if name is None:
        await callback_query.answer(text=_('Название категории должно быть от 1 до 64 символов'), show_alert=True)
        return
    user_id = callback_query.from_user.id
    await _add_new_category(category_name=name, user_id=user_id)
    category = await _find_active_category(name=name, user_id=user_id)
    await _finish(callback_query, state, pending, category)


@router.callback_query(StateFilter(None), F.data == CB_PICK)
async def pick_category_handler(callback_query: types.CallbackQuery, state: FSMContext):
    pending = await _pending(callback_query, state)
    if pending is None:
        return
    categories = await _show_all_categories(callback_query.from_user.id)
    await callback_query.message.edit_text(text=_summary(pending) + '\n\n' + _('Введите категорию'),
                                           reply_markup=_keyboard(_category_rows(categories)))
    await callback_query.answer()


@router.callback_query(F.data.startswith('qi:'))
async def stale_button_handler(callback_query: types.CallbackQuery):
    """A quick-entry button pressed while another dialog is open."""
    await callback_query.answer(text=_('Эта кнопка устарела. Отправьте операцию ещё раз.'), show_alert=True)
