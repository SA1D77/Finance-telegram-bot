"""/budget: monthly progress per limit, and setting or removing limits."""
from decimal import Decimal, ROUND_DOWN
from typing import Optional

from aiogram import F, Router, types
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from i18n import _, __
from middleware.accounting import _get_category, _show_all_categories
from telegram import buttons
from telegram.validation import parse_amount, parse_category_id
from timeutils import local_now
from .notify import schedule_budget_check
from .service import budget_statuses, days_left, delete_budget, set_budget
from .texts import bar, budget_name, status_emoji

router = Router(name='budgets')

TARGET_KEY = 'budget_target'
TOTAL = 'total'


class BudgetState(StatesGroup):
    amount = State()


def _button(text: str, data: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text, callback_data=data)


async def _render(user_id: int):
    now = local_now()
    statuses = await budget_statuses(user_id, now)
    remaining_days = days_left(now)
    title = _('🎯 Бюджет на {month}').format(month=now.strftime('%m.%Y'))
    text = title + '\n' + _('До конца месяца дней: {days}').format(days=remaining_days)

    if not statuses:
        text += '\n\n' + _('Лимитов пока нет. Задайте общий лимит на месяц или лимит на категорию.')
    for status in statuses:
        text += f'\n\n{status_emoji(status.percent)} <b>{budget_name(status, _)}</b>\n{bar(status.percent)} {status.percent}%\n'
        text += _('{spent} из {limit}').format(spent=status.spent, limit=status.limit) + ', '
        if status.left >= 0:
            text += _('осталось {left}').format(left=status.left)
        else:
            text += _('перерасход {over}').format(over=-status.left)
        if status.category_id is None and status.left > 0:
            per_day = (status.left / remaining_days).quantize(Decimal('0.01'), rounding=ROUND_DOWN)
            text += '\n' + _('Можно тратить {per_day} в день').format(per_day=per_day)

    rows = [[_button(_('🎯 Общий лимит'), f'b:set:{TOTAL}')], [_button(_('➕ Лимит на категорию'), 'b:cat')]]
    if statuses:
        rows.append([_button(_('🗑 Убрать лимит'), 'b:rm')])
    return text, types.InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(Command('budget'), StateFilter(None))
@router.message(F.text == __('🎯 Бюджет'), StateFilter(None))
async def budget_command(message: types.Message):
    text, markup = await _render(message.from_user.id)
    await message.answer(text=text, reply_markup=markup)


@router.callback_query(F.data.startswith('b:'))
async def leave_amount_input(callback_query: types.CallbackQuery, state: FSMContext):
    """Any budget button abandons a half-typed limit."""
    await state.set_state(None)
    raise SkipHandler()


@router.callback_query(F.data == 'b:view')
async def view_handler(callback_query: types.CallbackQuery):
    text, markup = await _render(callback_query.from_user.id)
    await callback_query.message.edit_text(text=text, reply_markup=markup)
    await callback_query.answer()


@router.callback_query(F.data == 'b:cat')
async def choose_category(callback_query: types.CallbackQuery):
    categories = await _show_all_categories(callback_query.from_user.id)
    rows = [[_button(c.name.capitalize(), f'b:set:{c.id}')] for c in categories]
    rows.append([_button(_('⬅️ Назад'), 'b:view')])
    await callback_query.message.edit_reply_markup(reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows))
    await callback_query.answer()


@router.callback_query(F.data.startswith('b:set:'))
async def ask_amount(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    if callback_query.data == f'b:set:{TOTAL}':
        target, name = TOTAL, _('Общий бюджет')
    else:
        category_id = parse_category_id(callback_query.data, prefix='b:set:')
        category = await _get_category(category_id=category_id, user_id=user_id) if category_id else None
        if category is None:
            await callback_query.answer(text=_('Мне кажется или такой категории нет?'), show_alert=True)
            return
        target, name = category.id, category.name.capitalize()

    await state.set_state(BudgetState.amount)
    await state.update_data(**{TARGET_KEY: target})
    await callback_query.message.answer(
        text=_('Введите лимит на месяц для «{name}» (0 — убрать лимит)').format(name=name),
        reply_markup=await buttons.get_button_cancel())
    await callback_query.answer()


@router.message(BudgetState.amount)
async def amount_input(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    target = (await state.get_data()).get(TARGET_KEY)
    category_id: Optional[int] = None if target == TOTAL else target
    if category_id is not None and await _get_category(category_id=category_id, user_id=user_id) is None:
        await state.set_state(None)
        await message.answer(text=_('Мне кажется или такой категории нет?'))
        return

    if (message.text or '').strip() == '0':
        await delete_budget(user_id, category_id)
    else:
        amount = parse_amount(message.text)
        if amount is None:
            await message.answer(text=_('Не получилось посчитать сумму. Пример: 500 кафе обед или 1200+350*2 продукты'),
                                 reply_markup=await buttons.get_button_cancel())
            return
        await set_budget(user_id, category_id, amount)
        await schedule_budget_check(user_id)  # a lower limit may already be exceeded

    await state.set_state(None)
    text, markup = await _render(user_id)
    await message.answer(text='✅ ' + _('Сохранено') + '\n\n' + text, reply_markup=markup)


@router.callback_query(F.data == 'b:rm')
async def choose_budget_to_remove(callback_query: types.CallbackQuery):
    statuses = await budget_statuses(callback_query.from_user.id)
    rows = [[_button('🗑 ' + budget_name(s, _), f'b:rm:{s.budget_id}')] for s in statuses]
    rows.append([_button(_('⬅️ Назад'), 'b:view')])
    await callback_query.message.edit_reply_markup(reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows))
    await callback_query.answer()


@router.callback_query(F.data.startswith('b:rm:'))
async def remove_budget(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    budget_id = parse_category_id(callback_query.data, prefix='b:rm:')
    # Only the user's own limits are offered and accepted.
    own = {s.budget_id: s for s in await budget_statuses(user_id)}
    if budget_id not in own:
        await callback_query.answer()
        return
    await delete_budget(user_id, own[budget_id].category_id)
    text, markup = await _render(user_id)
    await callback_query.message.edit_text(text=text, reply_markup=markup)
    await callback_query.answer()
