"""/recurring: templates that add an operation every day / week / month, with reminders."""
from datetime import date, timedelta
from typing import Any, Dict, Optional

from aiogram import F, Router, types
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from accounting.models import OperationType
from history.periods import parse_day
from i18n import _, __
from middleware.accounting import _get_category, _show_all_categories
from quick_input.matching import match_category
from quick_input.parser import AmountError, parse_quick_entry
from telegram import buttons
from telegram.validation import normalize_comment, parse_amount, parse_category_id
from timeutils import local_now
from . import service
from .models import FREQUENCIES
from .schedule import first_date
from .texts import card, frequency_label, summary

router = Router(name='recurring')

NEW_KEY = 'recurring_new'
EDIT_KEY = 'recurring_edit'
REMIND_CHOICES = (0, 1, 3, 7)
MAX_AHEAD_DAYS = 366
_TODAY = {'сегодня', 'today', 'сёння'}
_TOMORROW = {'завтра', 'tomorrow', 'заўтра'}


class RecurringState(StatesGroup):
    entry = State()
    start_date = State()
    amount = State()
    next_date = State()


def _button(text: str, data: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text, callback_data=data)


def _parse_start(text: Optional[str], today: date) -> Optional[date]:
    """Today or a future date within a year ("05.11", "05.11.2026", "сегодня", "завтра")."""
    value = ' '.join((text or '').split()).lower()
    if value in _TODAY:
        return today
    if value in _TOMORROW:
        return today + timedelta(days=1)
    day = parse_day(value, local_now())
    if day is None:
        return None
    if day < today and len(value.split('.')) == 2:  # "05.01" typed in December means next January
        day = parse_day(f'{value}.{today.year + 1}', local_now())
    if day is None or day < today or day > today + timedelta(days=MAX_AHEAD_DAYS):
        return None
    return day


async def _render_list(user_id: int):
    rows = await service.list_for(user_id)
    text = _('🔁 Регулярные операции')
    keyboard = []
    if not rows:
        text += '\n\n' + _('Пока пусто. Добавьте, например, зарплату или оплату интернета.')
    for number, row in enumerate(rows, start=1):
        schedule = frequency_label(row.frequency, row.anchor_day, _)
        text += f"\n\n{number}. {summary(row, _)}\n🔁 {schedule} · 📅 {row.next_date.strftime('%d.%m.%Y')}"
    if rows:
        keyboard.append([_button(str(n), f'r:card:{row.id}') for n, row in enumerate(rows, start=1)][:8])
    keyboard.append([_button(_('➕ Добавить'), 'r:new')])
    return text, types.InlineKeyboardMarkup(inline_keyboard=keyboard)


def _card_markup(row: Any) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [_button(_('⏭ Пропустить'), f'r:skip:{row.id}'), _button(_('💰 Сумма'), f'r:amt:{row.id}')],
        [_button(_('📅 Дата'), f'r:date:{row.id}'), _button(_('🗑 Удалить'), f'r:del:{row.id}')],
        [_button(_('⬅️ К списку'), 'r:list')],
    ])


async def _load(callback_query: types.CallbackQuery, prefix: str):
    template_id = parse_category_id(callback_query.data, prefix=prefix)
    row = await service.get(callback_query.from_user.id, template_id) if template_id else None
    if row is None:
        await callback_query.answer(text=_('Регулярная операция не найдена.'), show_alert=True)
    return row


# --- List and card ---------------------------------------------------------------

@router.message(Command('recurring'), StateFilter(None))
@router.message(F.text == __('🔁 Регулярные'), StateFilter(None))
async def recurring_command(message: types.Message):
    text, markup = await _render_list(message.from_user.id)
    await message.answer(text=text, reply_markup=markup)


@router.callback_query(F.data.startswith('r:'))
async def leave_text_input(callback_query: types.CallbackQuery, state: FSMContext):
    """Any recurring button abandons a half-typed value."""
    await state.set_state(None)
    raise SkipHandler()


@router.callback_query(F.data == 'r:list')
async def list_handler(callback_query: types.CallbackQuery):
    text, markup = await _render_list(callback_query.from_user.id)
    await callback_query.message.edit_text(text=text, reply_markup=markup)
    await callback_query.answer()


@router.callback_query(F.data.startswith('r:card:'))
async def card_handler(callback_query: types.CallbackQuery):
    row = await _load(callback_query, 'r:card:')
    if row is None:
        return
    await callback_query.message.edit_text(text=card(row, _), reply_markup=_card_markup(row))
    await callback_query.answer()


# --- Create --------------------------------------------------------------------------

@router.callback_query(F.data == 'r:new')
async def new_handler(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(RecurringState.entry)
    await callback_query.message.answer(text=_('Введите операцию, как обычно: 5000 интернет или +50000 зарплата'),
                                        reply_markup=await buttons.get_button_cancel())
    await callback_query.answer()


async def _ask_frequency(message: types.Message, state: FSMContext, new: Dict[str, Any]) -> None:
    await state.set_state(None)
    await state.update_data(**{NEW_KEY: new})
    labels = {'daily': _('Каждый день'), 'weekly': _('Каждую неделю'), 'monthly': _('Каждый месяц')}
    rows = [[_button(labels[f], f'r:freq:{f}')] for f in FREQUENCIES]
    rows.append([_button(_('⬅️ Отмена'), 'cancel')])
    await message.answer(text=_('Как часто?'), reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows))


@router.message(RecurringState.entry)
async def entry_input(message: types.Message, state: FSMContext):
    try:
        entry = parse_quick_entry(message.text or '')
    except AmountError:
        await message.answer(text=_('Не получилось посчитать сумму. Пример: 500 кафе обед или 1200+350*2 продукты'),
                             reply_markup=await buttons.get_button_cancel())
        return
    categories = await _show_all_categories(message.from_user.id)
    match = match_category(entry.words, categories)
    new = {'type': entry.operation_type.name, 'amount': str(entry.amount), 'comment': normalize_comment(match.rest)}
    if match.kind == 'exact':
        new['category_id'] = match.category.id
        await _ask_frequency(message, state, new)
        return
    await state.set_state(None)
    await state.update_data(**{NEW_KEY: new})
    rows = [[_button(c.name.capitalize(), f'r:cat:{c.id}')] for c in categories]
    rows.append([_button(_('⬅️ Отмена'), 'cancel')])
    await message.answer(text=_('Введите категорию'), reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows))


async def _new_draft(callback_query: types.CallbackQuery, state: FSMContext) -> Optional[Dict[str, Any]]:
    new = (await state.get_data()).get(NEW_KEY)
    if not new:
        await callback_query.answer(text=_('Эта кнопка устарела. Отправьте операцию ещё раз.'), show_alert=True)
    return new


@router.callback_query(F.data.startswith('r:cat:'))
async def category_chosen(callback_query: types.CallbackQuery, state: FSMContext):
    new = await _new_draft(callback_query, state)
    if new is None:
        return
    category_id = parse_category_id(callback_query.data, prefix='r:cat:')
    category = await _get_category(category_id=category_id, user_id=callback_query.from_user.id) if category_id else None
    if category is None:
        await callback_query.answer(text=_('Мне кажется или такой категории нет?'), show_alert=True)
        return
    new['category_id'] = category.id
    await callback_query.message.edit_reply_markup(reply_markup=None)
    await _ask_frequency(callback_query.message, state, new)
    await callback_query.answer()


@router.callback_query(F.data.startswith('r:freq:'))
async def frequency_chosen(callback_query: types.CallbackQuery, state: FSMContext):
    new = await _new_draft(callback_query, state)
    frequency = callback_query.data[len('r:freq:'):]
    if new is None or frequency not in FREQUENCIES:
        return
    new['frequency'] = frequency
    await state.update_data(**{NEW_KEY: new})
    await state.set_state(RecurringState.start_date)
    await callback_query.message.edit_text(text=_('С какой даты? Например: сегодня, завтра, 05.11'),
                                           reply_markup=await buttons.get_button_cancel())
    await callback_query.answer()


@router.message(RecurringState.start_date)
async def start_date_input(message: types.Message, state: FSMContext):
    new = (await state.get_data()).get(NEW_KEY) or {}
    start = _parse_start(message.text, local_now().date())
    if start is None or 'frequency' not in new:
        await message.answer(text=_('Нужна сегодняшняя или будущая дата, например 05.11'), reply_markup=await buttons.get_button_cancel())
        return
    next_date, anchor = first_date(start, new['frequency'])
    new.update(next_date=next_date.isoformat(), anchor_day=anchor)
    await state.update_data(**{NEW_KEY: new})
    await state.set_state(None)
    labels = {0: _('Не напоминать'), 1: _('За 1 день'), 3: _('За 3 дня'), 7: _('За неделю')}
    rows = [[_button(labels[days], f'r:rem:{days}')] for days in REMIND_CHOICES]
    await message.answer(text=_('Напоминать заранее?'), reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith('r:rem:'))
async def remind_chosen(callback_query: types.CallbackQuery, state: FSMContext):
    new = await _new_draft(callback_query, state)
    raw = callback_query.data[len('r:rem:'):]
    if new is None or not raw.isdigit() or int(raw) not in REMIND_CHOICES or 'next_date' not in new:
        return
    user_id = callback_query.from_user.id
    category = await _get_category(category_id=new['category_id'], user_id=user_id)
    if category is None:
        await callback_query.answer(text=_('Мне кажется или такой категории нет?'), show_alert=True)
        return
    template_id = await service.create(
        user_id=user_id, category_id=category.id, operation_type=OperationType[new['type']], amount=parse_amount(new['amount']),
        comment=new.get('comment'), frequency=new['frequency'], anchor_day=new['anchor_day'],
        next_date=date.fromisoformat(new['next_date']), remind_days=int(raw))
    await state.update_data(**{NEW_KEY: None})
    row = await service.get(user_id, template_id)
    await callback_query.message.edit_text(text='✅ ' + _('Сохранено') + '\n\n' + card(row, _), reply_markup=_card_markup(row))
    await callback_query.answer()


# --- Edit, skip, delete ------------------------------------------------------------

@router.callback_query(F.data.startswith('r:skip:'))
async def skip_handler(callback_query: types.CallbackQuery):
    row = await _load(callback_query, 'r:skip:')
    if row is None:
        return
    new_date = await service.skip_next(callback_query.from_user.id, row.id)
    if new_date is None:
        await callback_query.answer(text=_('Регулярная операция не найдена.'), show_alert=True)
        return
    note = '⏭ ' + _('Пропущено. Следующая: {date}').format(date=new_date.strftime('%d.%m.%Y'))
    await callback_query.message.edit_text(text=(callback_query.message.html_text or '') + '\n\n' + note, reply_markup=None)
    await callback_query.answer()


async def _start_edit(callback_query: types.CallbackQuery, state: FSMContext, prefix: str, new_state, prompt: str):
    row = await _load(callback_query, prefix)
    if row is None:
        return
    await state.set_state(new_state)
    await state.update_data(**{EDIT_KEY: row.id})
    await callback_query.message.answer(text=prompt, reply_markup=await buttons.get_button_cancel())
    await callback_query.answer()


@router.callback_query(F.data.startswith('r:amt:'))
async def amount_start(callback_query: types.CallbackQuery, state: FSMContext):
    await _start_edit(callback_query, state, 'r:amt:', RecurringState.amount,
                      _('Введите новую сумму (можно с расчётом, например 1200+350)'))


@router.callback_query(F.data.startswith('r:date:'))
async def date_start(callback_query: types.CallbackQuery, state: FSMContext):
    await _start_edit(callback_query, state, 'r:date:', RecurringState.next_date, _('С какой даты? Например: сегодня, завтра, 05.11'))


async def _apply(message: types.Message, state: FSMContext, **values) -> None:
    user_id = message.from_user.id
    template_id = (await state.get_data()).get(EDIT_KEY)
    if not isinstance(template_id, int) or not await service.update_fields(user_id, template_id, **values):
        await state.set_state(None)
        await message.answer(text=_('Регулярная операция не найдена.'))
        return
    await state.set_state(None)
    row = await service.get(user_id, template_id)
    await message.answer(text='✅ ' + _('Сохранено') + '\n\n' + card(row, _), reply_markup=_card_markup(row))


@router.message(RecurringState.amount)
async def amount_input(message: types.Message, state: FSMContext):
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer(text=_('Не получилось посчитать сумму. Пример: 500 кафе обед или 1200+350*2 продукты'),
                             reply_markup=await buttons.get_button_cancel())
        return
    await _apply(message, state, amount=amount)


@router.message(RecurringState.next_date)
async def next_date_input(message: types.Message, state: FSMContext):
    day = _parse_start(message.text, local_now().date())
    if day is None:
        await message.answer(text=_('Нужна сегодняшняя или будущая дата, например 05.11'), reply_markup=await buttons.get_button_cancel())
        return
    await _apply(message, state, next_date=day)


@router.callback_query(F.data.startswith('r:del:'))
async def delete_ask(callback_query: types.CallbackQuery):
    row = await _load(callback_query, 'r:del:')
    if row is None:
        return
    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [_button(_('🗑 Да, удалить'), f'r:delok:{row.id}')], [_button(_('⬅️ Нет'), f'r:card:{row.id}')]])
    await callback_query.message.edit_text(text=card(row, _) + '\n\n' + _('Удалить эту регулярную операцию?'), reply_markup=markup)
    await callback_query.answer()


@router.callback_query(F.data.startswith('r:delok:'))
async def delete_confirmed(callback_query: types.CallbackQuery):
    row = await _load(callback_query, 'r:delok:')
    if row is None:
        return
    await service.remove(callback_query.from_user.id, row.id)
    text, markup = await _render_list(callback_query.from_user.id)
    await callback_query.message.edit_text(text='🗑 ' + _('Удалено') + '\n\n' + text, reply_markup=markup)
    await callback_query.answer()
