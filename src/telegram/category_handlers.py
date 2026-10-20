from html import escape

from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from telegram import buttons
from i18n import _, __
from telegram.states import (
    AddCategoryState,
    DeleteCategoryState
)
from .validation import normalize_category_name, parse_category_id
from middleware.accounting import (
    _add_new_category,
    _show_all_categories,
    _delete_category
)
from middleware.general_handlers import (
    category_does_not_exist,
    category_already_exist
)
from exception import (
    CategoryDoesNotExist,
    CategoryAlreadyExist,)

router = Router(name='category')


@router.message(F.text == __('🗂 Категории'), StateFilter(None))
async def categories_show_handler(message: types.Message):
    all_categories = await _show_all_categories(message.from_user.id)
    groups = [(_('💸 Для расходов:'), 'expense'), (_('💰 Для доходов:'), 'income'), (_('↔️ Для всего:'), None)]
    msg = str()
    for title, kind in groups:
        names = [escape(c.name.capitalize()) for c in all_categories if c.kind == kind]
        if names:
            msg += f'\n<b>{title}</b>\n' + '\n'.join(f'📌 {name}' for name in names) + '\n'

    await message.answer(text=_('Вот список текущих категорий:\n') + msg, reply_markup=await buttons.get_button_manage_money())


@router.message(F.text == __('📥 Добавить категорию'), StateFilter(None))
async def categories_add_handler(message: types.Message, state: FSMContext):
    await state.set_state(AddCategoryState.add_categories_input)

    await message.answer(text=_('НОВАЯ КАТЕГОРИЯ:'), reply_markup=types.ReplyKeyboardRemove())
    await message.answer(text=_('Введите название новой категории'), reply_markup=await buttons.get_button_cancel())


@router.message(F.text == __('📤 Удалить категорию'), StateFilter(None))
async def categories_delete_handler(message: types.Message, state: FSMContext):

    await state.set_state(DeleteCategoryState.delete_categories_input)

    all_categories = await _show_all_categories(message.from_user.id)
    await message.answer(text=_('Введите категорию'), reply_markup=await buttons.categories_buttons(categories=all_categories))


@router.message(AddCategoryState.add_categories_input)
async def add_categories_input_handler(message: types.Message, state: FSMContext):
    name = normalize_category_name(message.text)
    if name is None:
        # Stay in the same state so the user can simply type another name.
        await message.answer(text=_('Название категории должно быть от 1 до 64 символов'), reply_markup=await buttons.get_button_cancel())
        return

    try:
        await category_already_exist(category=name, user_id=message.from_user.id)
    except CategoryAlreadyExist:
        await message.answer(text=_('Посмотрите внимательно, кажется такая категория уже существует!'), reply_markup=await buttons.get_button_manage_money())
        await state.clear()
        return

    await state.update_data(new_category_name=name)
    await state.set_state(AddCategoryState.kind)
    markup = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=_('💸 Для расходов'), callback_data='addcat:expense'),
         types.InlineKeyboardButton(text=_('💰 Для доходов'), callback_data='addcat:income')],
        [types.InlineKeyboardButton(text=_('↔️ Для всего'), callback_data='addcat:both')],
        [types.InlineKeyboardButton(text=_('⬅️ Отмена'), callback_data='cancel')],
    ])
    await message.answer(text=_('Для чего эта категория?'), reply_markup=markup)


@router.callback_query(F.data.startswith('addcat:'), AddCategoryState.kind)
async def add_category_kind(callback_query: types.CallbackQuery, state: FSMContext):
    kind = {'addcat:expense': 'expense', 'addcat:income': 'income', 'addcat:both': None}.get(callback_query.data, 'invalid')
    name = (await state.get_data()).get('new_category_name')
    if kind == 'invalid' or not name:
        await callback_query.answer()
        return
    await _add_new_category(category_name=name, user_id=callback_query.from_user.id, kind=kind)
    await state.clear()
    await callback_query.message.edit_text(text=_('Категория успешно добавлена!'))
    await callback_query.message.answer(text=_('⚙️ Главное меню'), reply_markup=await buttons.get_button_manage_money())
    await callback_query.answer()


@router.callback_query(F.data.startswith('category'), DeleteCategoryState.delete_categories_input)
async def delete_categories_input_handler(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = callback_query.from_user.id
    category_id = parse_category_id(callback_query.data)

    try:
        if category_id is None:
            raise CategoryDoesNotExist
        await category_does_not_exist(category_id=category_id, user_id=user_id)
    except CategoryDoesNotExist:
        await callback_query.message.answer(text=_('Мне кажется или такой категории нет?'), reply_markup=await buttons.get_button_manage_money())
    else:
        await _delete_category(user_id=user_id, category_id=category_id)
        await callback_query.message.edit_text(text=_('Категория успешно удалена!'))
    finally:
        await state.clear()
        await callback_query.answer()
