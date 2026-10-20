from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from telegram import buttons
from i18n import _, __
from telegram.states import (
    IncomeState,
    ExpenditureState,
)
from .messages import FLOAT_NUMBER_ERROR
from middleware.accounting import (
    _get_balance,
    _add_income,
    _add_expenditure,
)
from middleware.accounting import _show_all_categories

router = Router(name='main')


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

    user_balance = await _get_balance(message.from_user.username)

    await message.answer(text=str(round(user_balance, 2)), reply_markup=await buttons.get_button_manage_money())


@router.message(IncomeState.income_input)
async def income_input_handler(message: types.Message, state: FSMContext):
    try:
        total = float(message.text)
    except (TypeError, ValueError):
        await message.answer(text=_(FLOAT_NUMBER_ERROR), reply_markup=await buttons.get_button_manage_money())
    else:
        await state.set_state(IncomeState.category_input)
        all_categories = await _show_all_categories(message.from_user.username)
        await message.answer(text=_('Введите категорию'), reply_markup=await buttons.categories_buttons(categories=all_categories))
        await state.update_data(total=total)
        await state.update_data(username=message.from_user.username)


@router.callback_query(F.data.startswith('category'), IncomeState.category_input)
async def income_category_input_handler(callback_query: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    total = data.get('total')
    username = data.get('username')
    text = callback_query.data.replace('category_', '').lower()
    await _add_income(
        username=username,
        category=text,
        quantity=total
    )

    await callback_query.message.edit_text(text=_('Новая запись:'))
    await callback_query.message.answer(text=_('ДОХОД') + f' <b>{total} {text}</b>', reply_markup=await buttons.get_button_manage_money())

    await state.clear()
    await callback_query.answer()


@router.message(ExpenditureState.expenditure_input)
async def expenditure_input_handler(message: types.Message, state: FSMContext):
    try:
        total = float(message.text)
    except (TypeError, ValueError):
        await message.answer(text=_(FLOAT_NUMBER_ERROR), reply_markup=await buttons.get_button_manage_money())
    else:
        await state.set_state(ExpenditureState.category_input)
        all_categories = await _show_all_categories(message.from_user.username)
        await message.answer(text=_('Введите категорию'), reply_markup=await buttons.categories_buttons(categories=all_categories))
        await state.update_data(total=total)
        await state.update_data(username=message.from_user.username)


@router.callback_query(F.data.startswith('category'), ExpenditureState.category_input)
async def expenditure_category_input_handler(callback_query: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    total = data.get('total')
    username = data.get('username')
    text = callback_query.data.replace('category_', '').lower()
    await _add_expenditure(
        username=username,
        category=text,
        quantity=total
    )

    await callback_query.message.edit_text(text=_('Новая запись:'))
    await callback_query.message.answer(text=_('РАСХОД') + f' <b>{total} {text}</b>', reply_markup=await buttons.get_button_manage_money())

    await state.clear()
    await callback_query.answer()
