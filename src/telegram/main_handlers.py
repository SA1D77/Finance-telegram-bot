from decimal import Decimal
from html import escape
from typing import Optional

from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from accounting.models import Categories
from telegram import buttons
from i18n import _, __
from telegram.states import (
    IncomeState,
    ExpenditureState,
)
from .messages import FLOAT_NUMBER_ERROR
from .validation import normalize_expression, parse_amount, parse_category_id
from quick_input.calc import is_expression
from middleware.accounting import (
    _get_balance,
    _add_income,
    _add_expenditure,
)
from middleware.accounting import _show_all_categories
from middleware.general_handlers import category_does_not_exist
from exception import CategoryDoesNotExist

router = Router(name='main')


async def _own_category(callback_query: types.CallbackQuery, state: FSMContext) -> Optional[Categories]:
    """Callback data comes from the client: accept only the user's own active categories."""
    category_id = parse_category_id(callback_query.data)
    try:
        if category_id is None:
            raise CategoryDoesNotExist
        return await category_does_not_exist(category_id=category_id, user_id=callback_query.from_user.id)
    except CategoryDoesNotExist:
        await callback_query.message.answer(text=_('Мне кажется или такой категории нет?'), reply_markup=await buttons.get_button_manage_money())
        await state.clear()
        await callback_query.answer()
        return None


@router.message(F.text == __('➕ Доход'), StateFilter(None))
async def income_handler(message: types.Message, state: FSMContext):
    await state.set_state(IncomeState.income_input)

    await message.answer(text=_('ДОХОД:'), reply_markup=types.ReplyKeyboardRemove())
    await message.answer(text=_('Введите ваш доход'))


@router.message(F.text == __('➖ Расход'), StateFilter(None))
async def expenditure_handler(message: types.Message, state: FSMContext):
    await state.set_state(ExpenditureState.expenditure_input)

    await message.answer(text=_('РАСХОД:'), reply_markup=types.ReplyKeyboardRemove())
    await message.answer(text=_('Введите ваш расход'))


@router.message(F.text == __('📊 Баланс'), StateFilter(None))
async def balance_show_handler(message: types.Message):
    await message.answer(text=_('ВАШ ТЕКУЩИЙ БАЛАНС:'))

    user_balance = await _get_balance(message.from_user.id)

    await message.answer(text=str(user_balance), reply_markup=await buttons.get_button_manage_money())


async def _amount_input(message: types.Message, state: FSMContext, next_state) -> None:
    total = parse_amount(message.text)
    if total is None:
        await message.answer(text=_(FLOAT_NUMBER_ERROR), reply_markup=await buttons.get_button_manage_money())
        return

    await state.set_state(next_state)
    all_categories = await _show_all_categories(message.from_user.id)
    text = _('Введите категорию')
    expression = normalize_expression(message.text)
    if is_expression(expression):
        text = f'🧮 {escape(expression)} = <b>{total}</b>\n\n' + text
    await message.answer(text=text, reply_markup=await buttons.categories_buttons(categories=all_categories))
    # FSM data is stored as JSON in Redis, so the Decimal travels as a string.
    await state.update_data(total=str(total))


@router.message(IncomeState.income_input)
async def income_input_handler(message: types.Message, state: FSMContext):
    await _amount_input(message, state, IncomeState.category_input)


@router.message(ExpenditureState.expenditure_input)
async def expenditure_input_handler(message: types.Message, state: FSMContext):
    await _amount_input(message, state, ExpenditureState.category_input)


@router.callback_query(F.data.startswith('category'), IncomeState.category_input)
async def income_category_input_handler(callback_query: types.CallbackQuery, state: FSMContext):
    category = await _own_category(callback_query, state)
    if category is None:
        return

    total = Decimal((await state.get_data())['total'])
    operation_id = await _add_income(user_id=callback_query.from_user.id, category_id=category.id, quantity=total)

    await callback_query.message.edit_text(text=_('Новая запись:'), reply_markup=buttons.undo_markup(operation_id))
    await callback_query.message.answer(text=_('ДОХОД') + f' <b>{total} {escape(category.name)}</b>', reply_markup=await buttons.get_button_manage_money())

    await state.clear()
    await callback_query.answer()


@router.callback_query(F.data.startswith('category'), ExpenditureState.category_input)
async def expenditure_category_input_handler(callback_query: types.CallbackQuery, state: FSMContext):
    category = await _own_category(callback_query, state)
    if category is None:
        return

    total = Decimal((await state.get_data())['total'])
    operation_id = await _add_expenditure(user_id=callback_query.from_user.id, category_id=category.id, quantity=total)

    await callback_query.message.edit_text(text=_('Новая запись:'), reply_markup=buttons.undo_markup(operation_id))
    await callback_query.message.answer(text=_('РАСХОД') + f' <b>{total} {escape(category.name)}</b>', reply_markup=await buttons.get_button_manage_money())

    await state.clear()
    await callback_query.answer()
