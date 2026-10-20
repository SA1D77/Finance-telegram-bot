"""/goals: savings goals with progress, monthly amount to put aside and top-ups."""
from datetime import date
from html import escape
from typing import Optional

from aiogram import F, Router, types
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from budgets.texts import bar
from history.periods import parse_day
from money import fmt
from i18n import _, __
from telegram import buttons
from telegram.validation import parse_amount, parse_category_id
from timeutils import local_now
from . import service
from .models import GOAL_NAME_MAX_LENGTH
from .service import Goal, monthly_needed

router = Router(name='goals')

NEW_KEY = 'goal_new'
EDIT_KEY = 'goal_edit'


class GoalState(StatesGroup):
    name = State()
    target = State()
    deadline = State()
    top_up = State()
    withdraw = State()
    new_target = State()
    new_deadline = State()


def _button(text: str, data: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text, callback_data=data)


def parse_deadline(text: Optional[str], today: date) -> Optional[date]:
    """A future date ("31.12.2026", "31.12"), or None when invalid. "-" is handled by the caller."""
    day = parse_day(' '.join((text or '').split()), local_now())
    if day is not None and day <= today and (text or '').count('.') == 1:
        day = parse_day(f'{text.strip()}.{today.year + 1}', local_now())
    return day if day is not None and day > today else None


def goal_text(goal: Goal, today: date) -> str:
    text = f'⭐ <b>{escape(goal.name)}</b>\n{bar(goal.percent)} {goal.percent}%\n'
    text += _('{saved} из {target}').format(saved=fmt(goal.saved), target=fmt(goal.target))
    if goal.reached:
        text += ' 🎉 ' + _('Цель достигнута!')
    else:
        text += ', ' + _('осталось {left}').format(left=fmt(goal.left))
    if goal.deadline:
        text += '\n📅 ' + _('до {date}').format(date=goal.deadline.strftime('%d.%m.%Y'))
        needed = monthly_needed(goal, today)
        if needed is not None:
            text += ' · ' + _('откладывать {amount} в месяц').format(amount=fmt(needed))
        elif not goal.reached:
            text += ' · ' + _('срок прошёл')
    return text


def _card_markup(goal: Goal) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[
        [_button(_('💰 Пополнить'), f'g:add:{goal.id}'), _button(_('➖ Снять'), f'g:take:{goal.id}')],
        [_button(_('🎯 Сумма цели'), f'g:target:{goal.id}'), _button(_('📅 Срок'), f'g:deadline:{goal.id}')],
        [_button(_('🗑 Удалить'), f'g:del:{goal.id}')],
        [_button(_('⬅️ К списку'), 'g:list')],
    ])


async def _render_list(user_id: int):
    goals = await service.list_for(user_id)
    today = local_now().date()
    text = _('⭐ Цели накопления')
    if not goals:
        text += '\n\n' + _('Целей пока нет. Например: отпуск, ноутбук, подушка безопасности.')
    for number, goal in enumerate(goals, start=1):
        text += f'\n\n{number}. ' + goal_text(goal, today)
    keyboard = []
    if goals:
        keyboard.append([_button(str(n), f'g:card:{goal.id}') for n, goal in enumerate(goals, start=1)][:8])
    keyboard.append([_button(_('➕ Новая цель'), 'g:new')])
    return text, types.InlineKeyboardMarkup(inline_keyboard=keyboard)


async def _load(callback_query: types.CallbackQuery, prefix: str) -> Optional[Goal]:
    goal_id = parse_category_id(callback_query.data, prefix=prefix)
    goal = await service.get(callback_query.from_user.id, goal_id) if goal_id else None
    if goal is None:
        await callback_query.answer(text=_('Цель не найдена.'), show_alert=True)
    return goal


@router.message(Command('goals'), StateFilter(None))
@router.message(F.text == __('⭐ Цели'), StateFilter(None))
async def goals_command(message: types.Message):
    text, markup = await _render_list(message.from_user.id)
    await message.answer(text=text, reply_markup=markup)


@router.callback_query(F.data.startswith('g:'))
async def leave_text_input(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(None)
    raise SkipHandler()


@router.callback_query(F.data == 'g:list')
async def list_handler(callback_query: types.CallbackQuery):
    text, markup = await _render_list(callback_query.from_user.id)
    await callback_query.message.edit_text(text=text, reply_markup=markup)
    await callback_query.answer()


@router.callback_query(F.data.startswith('g:card:'))
async def card_handler(callback_query: types.CallbackQuery):
    goal = await _load(callback_query, 'g:card:')
    if goal is None:
        return
    await callback_query.message.edit_text(text=goal_text(goal, local_now().date()), reply_markup=_card_markup(goal))
    await callback_query.answer()


# --- New goal ----------------------------------------------------------------------

@router.callback_query(F.data == 'g:new')
async def new_handler(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(GoalState.name)
    await callback_query.message.answer(text=_('Как назовём цель?'), reply_markup=await buttons.get_button_cancel())
    await callback_query.answer()


@router.message(GoalState.name)
async def name_input(message: types.Message, state: FSMContext):
    name = ' '.join((message.text or '').split())
    if not name or len(name) > GOAL_NAME_MAX_LENGTH:
        await message.answer(text=_('Название должно быть от 1 до 64 символов'), reply_markup=await buttons.get_button_cancel())
        return
    await state.update_data(**{NEW_KEY: {'name': name}})
    await state.set_state(GoalState.target)
    await message.answer(text=_('Сколько нужно накопить?'), reply_markup=await buttons.get_button_cancel())


@router.message(GoalState.target)
async def target_input(message: types.Message, state: FSMContext):
    target = parse_amount(message.text)
    if target is None:
        await message.answer(text=_('Не получилось посчитать сумму. Пример: 500 кафе обед или 1200+350*2 продукты'),
                             reply_markup=await buttons.get_button_cancel())
        return
    new = (await state.get_data()).get(NEW_KEY) or {}
    new['target'] = str(target)
    await state.update_data(**{NEW_KEY: new})
    await state.set_state(GoalState.deadline)
    await message.answer(text=_('До какой даты? Например 31.12.2026, или «-», если срока нет'), reply_markup=await buttons.get_button_cancel())


async def _deadline_or_none(message: types.Message) -> Optional[object]:
    """date, None for "-", or False when the input is invalid (the user is told)."""
    if (message.text or '').strip() == '-':
        return None
    deadline = parse_deadline(message.text, local_now().date())
    if deadline is None:
        await message.answer(text=_('Нужна будущая дата, например 31.12.2026, или «-»'), reply_markup=await buttons.get_button_cancel())
        return False
    return deadline


@router.message(GoalState.deadline)
async def deadline_input(message: types.Message, state: FSMContext):
    deadline = await _deadline_or_none(message)
    if deadline is False:
        return
    new = (await state.get_data()).get(NEW_KEY) or {}
    if 'target' not in new:
        await state.set_state(None)
        return
    goal_id = await service.create(message.from_user.id, new['name'], parse_amount(new['target']), deadline)
    await state.set_state(None)
    await state.update_data(**{NEW_KEY: None})
    goal = await service.get(message.from_user.id, goal_id)
    await message.answer(text='✅ ' + _('Сохранено') + '\n\n' + goal_text(goal, local_now().date()), reply_markup=_card_markup(goal))


# --- Card actions --------------------------------------------------------------------

async def _start(callback_query: types.CallbackQuery, state: FSMContext, prefix: str, new_state, prompt: str):
    goal = await _load(callback_query, prefix)
    if goal is None:
        return
    await state.set_state(new_state)
    await state.update_data(**{EDIT_KEY: goal.id})
    await callback_query.message.answer(text=prompt, reply_markup=await buttons.get_button_cancel())
    await callback_query.answer()


@router.callback_query(F.data.startswith('g:add:'))
async def top_up_start(callback_query: types.CallbackQuery, state: FSMContext):
    await _start(callback_query, state, 'g:add:', GoalState.top_up, _('Сколько отложить?'))


@router.callback_query(F.data.startswith('g:take:'))
async def withdraw_start(callback_query: types.CallbackQuery, state: FSMContext):
    await _start(callback_query, state, 'g:take:', GoalState.withdraw, _('Сколько снять?'))


@router.callback_query(F.data.startswith('g:target:'))
async def target_start(callback_query: types.CallbackQuery, state: FSMContext):
    await _start(callback_query, state, 'g:target:', GoalState.new_target, _('Сколько нужно накопить?'))


@router.callback_query(F.data.startswith('g:deadline:'))
async def deadline_start(callback_query: types.CallbackQuery, state: FSMContext):
    await _start(callback_query, state, 'g:deadline:', GoalState.new_deadline,
                 _('До какой даты? Например 31.12.2026, или «-», если срока нет'))


async def _edited(message: types.Message, state: FSMContext) -> Optional[Goal]:
    goal_id = (await state.get_data()).get(EDIT_KEY)
    goal = await service.get(message.from_user.id, goal_id) if isinstance(goal_id, int) else None
    if goal is None:
        await state.set_state(None)
        await message.answer(text=_('Цель не найдена.'))
    return goal


async def _show(message: types.Message, state: FSMContext, goal_id: int, prefix: str = '') -> None:
    await state.set_state(None)
    goal = await service.get(message.from_user.id, goal_id)
    text = goal_text(goal, local_now().date())
    await message.answer(text=(prefix + '\n\n' if prefix else '✅ ' + _('Сохранено') + '\n\n') + text, reply_markup=_card_markup(goal))


@router.message(GoalState.top_up)
async def top_up_input(message: types.Message, state: FSMContext):
    goal = await _edited(message, state)
    if goal is None:
        return
    amount = parse_amount(message.text)
    if amount is None:
        await message.answer(text=_('Не получилось посчитать сумму. Пример: 500 кафе обед или 1200+350*2 продукты'),
                             reply_markup=await buttons.get_button_cancel())
        return
    reached_now = not goal.reached and goal.saved + amount >= goal.target  # decided before saving
    await service.contribute(message.from_user.id, goal.id, amount)
    await _show(message, state, goal.id, '🎉 ' + _('Цель достигнута!') if reached_now else '')


@router.message(GoalState.withdraw)
async def withdraw_input(message: types.Message, state: FSMContext):
    goal = await _edited(message, state)
    if goal is None:
        return
    amount = parse_amount(message.text)
    if amount is None or amount > goal.saved:
        await message.answer(text=_('Можно снять не больше {saved}').format(saved=fmt(goal.saved)), reply_markup=await buttons.get_button_cancel())
        return
    await service.contribute(message.from_user.id, goal.id, -amount)
    await _show(message, state, goal.id)


@router.message(GoalState.new_target)
async def new_target_input(message: types.Message, state: FSMContext):
    goal = await _edited(message, state)
    if goal is None:
        return
    target = parse_amount(message.text)
    if target is None:
        await message.answer(text=_('Не получилось посчитать сумму. Пример: 500 кафе обед или 1200+350*2 продукты'),
                             reply_markup=await buttons.get_button_cancel())
        return
    await service.update_fields(message.from_user.id, goal.id, target=target)
    await _show(message, state, goal.id)


@router.message(GoalState.new_deadline)
async def new_deadline_input(message: types.Message, state: FSMContext):
    goal = await _edited(message, state)
    if goal is None:
        return
    deadline = await _deadline_or_none(message)
    if deadline is False:
        return
    await service.update_fields(message.from_user.id, goal.id, deadline=deadline)
    await _show(message, state, goal.id)


@router.callback_query(F.data.startswith('g:del:'))
async def delete_ask(callback_query: types.CallbackQuery):
    goal = await _load(callback_query, 'g:del:')
    if goal is None:
        return
    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [_button(_('🗑 Да, удалить'), f'g:delok:{goal.id}')], [_button(_('⬅️ Нет'), f'g:card:{goal.id}')]])
    await callback_query.message.edit_text(text=goal_text(goal, local_now().date()) + '\n\n' + _('Удалить эту цель?'),
                                           reply_markup=markup)
    await callback_query.answer()


@router.callback_query(F.data.startswith('g:delok:'))
async def delete_confirmed(callback_query: types.CallbackQuery):
    goal = await _load(callback_query, 'g:delok:')
    if goal is None:
        return
    await service.remove(callback_query.from_user.id, goal.id)
    text, markup = await _render_list(callback_query.from_user.id)
    await callback_query.message.edit_text(text='🗑 ' + _('Удалено') + '\n\n' + text, reply_markup=markup)
    await callback_query.answer()
